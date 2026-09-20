import asyncio
import json
from datetime import datetime, timedelta, timezone

from agents.order_after_sales_agent import OrderAfterSalesAgent
from runtime.context_engine import ContextEngine
from services.memory_manager import MemoryManager
from services.order_after_sales_service import OrderAfterSalesService


def _collect(agent, message):
    async def collect():
        return [token async for token in agent.run_stream(message)]

    return asyncio.run(collect())


def _events(tokens):
    return [
        json.loads(token.removeprefix("[EVENT]"))
        for token in tokens
        if token.startswith("[EVENT]")
    ]


def _build(tmp_path, session_id="session-a", user_id="user-a"):
    database_url = f"sqlite:///{(tmp_path / 'memory-orders.db').as_posix()}"
    service = OrderAfterSalesService(database_url)
    service.seed_demo_data()
    memory = MemoryManager(database_url)
    return (
        OrderAfterSalesAgent(
            session_id,
            service,
            "demo",
            user_id,
            memory_manager=memory,
            context_engine=ContextEngine(memory),
        ),
        service,
        memory,
    )


def test_return_flow_survives_agent_recreation_and_records_completion(tmp_path):
    first, service, memory = _build(tmp_path)

    initial = _collect(first, "申请退货 JP20260919002")
    working = memory.get_working("demo", "user-a", "session-a")

    assert working["plan_state"] == "collecting_reason"
    assert working["slots"] == {"order_id": "JP20260919002", "reason": ""}
    assert any(event["type"] == "memory_context" for event in _events(initial))

    second = OrderAfterSalesAgent(
        "session-a",
        service,
        "demo",
        "user-a",
        memory_manager=memory,
        context_engine=ContextEngine(memory),
    )
    pending = _collect(second, "原因是商品破损")

    assert second.has_pending_action is True
    assert any(
        event["type"] == "memory_context"
        and event["data"]["working_loaded"] is True
        for event in _events(pending)
    )
    assert memory.get_working("demo", "user-a", "session-a")["plan_state"] == "awaiting_confirmation"

    third = OrderAfterSalesAgent(
        "session-a",
        service,
        "demo",
        "user-a",
        memory_manager=memory,
        context_engine=ContextEngine(memory),
    )
    confirmed = _collect(third, "确认提交")
    tools = [
        event["data"].get("tool")
        for event in _events(confirmed)
        if event["type"] == "tool_started"
    ]

    assert tools == ["return.check", "return.create"]
    assert service.count_return_requests() == 1
    assert memory.get_working("demo", "user-a", "session-a") is None
    recalled = memory.recall_events(
        "demo", "user-a", entity_refs=["JP20260919002"]
    )
    assert len(recalled) == 1
    assert recalled[0]["event_type"] == "return_requested"
    assert recalled[0]["outcome"] == "completed"


def test_working_memory_is_not_shared_across_users_or_sessions(tmp_path):
    first, service, memory = _build(tmp_path)
    _collect(first, "申请退货 JP20260919002，原因是商品破损")

    other_session = OrderAfterSalesAgent(
        "session-b",
        service,
        "demo",
        "user-a",
        memory_manager=memory,
        context_engine=ContextEngine(memory),
    )
    other_user = OrderAfterSalesAgent(
        "session-a",
        service,
        "demo",
        "user-b",
        memory_manager=memory,
        context_engine=ContextEngine(memory),
    )

    assert other_session.has_pending_action is False
    assert other_user.has_pending_action is False


def test_expired_working_memory_is_not_restored(tmp_path):
    _, service, memory = _build(tmp_path)
    expired_at = datetime.now(timezone.utc) - timedelta(hours=1)
    memory.save_working(
        tenant_id="demo",
        user_id="user-a",
        session_id="expired-session",
        active_intent="return_request",
        plan_state="collecting_reason",
        slots={"order_id": "JP20260919002", "reason": ""},
        ttl_minutes=1,
        now=expired_at,
    )

    restored = OrderAfterSalesAgent(
        "expired-session",
        service,
        "demo",
        "user-a",
        memory_manager=memory,
        context_engine=ContextEngine(memory),
    )

    assert restored.has_active_flow is False
    assert memory.get_working("demo", "user-a", "expired-session") is None


def test_cancel_clears_persisted_working_memory(tmp_path):
    agent, _, memory = _build(tmp_path)
    _collect(agent, "申请退货 JP20260919002")

    _collect(agent, "取消")

    assert memory.get_working("demo", "user-a", "session-a") is None


def test_confirmation_does_not_record_completion_when_eligibility_changed(
    tmp_path, monkeypatch
):
    agent, service, memory = _build(tmp_path)
    _collect(agent, "申请退货 JP20260919002，原因是商品破损")
    monkeypatch.setattr(
        service,
        "check_return_eligibility",
        lambda *args, **kwargs: {
            "eligible": False,
            "reason": "return_window_expired",
        },
    )

    confirmed = _collect(agent, "确认提交")

    assert service.count_return_requests() == 0
    assert not memory.recall_events(
        "demo", "user-a", entity_refs=["JP20260919002"]
    )
    assert "超过七日退货期限" in "".join(confirmed)
