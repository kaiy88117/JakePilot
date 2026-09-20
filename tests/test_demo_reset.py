from datetime import datetime, timedelta, timezone

from db.models import ActionExecution, AfterSalesRequest, Order
from scripts.reset_demo import load_configured_database_url, reset_demo_state
from services.order_after_sales_service import OrderAfterSalesService
from services.memory_manager import MemoryManager


def test_reset_removes_only_demo_users_after_sales_records(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'demo-reset.db').as_posix()}"
    service = OrderAfterSalesService(database_url)
    service.seed_demo_data()
    service.create_return_request(
        "demo",
        "user-a",
        "JP20260919002",
        "商品破损",
        "demo-idem",
        "demo-hash",
    )
    memory = MemoryManager(database_url)
    memory.save_working(
        tenant_id="demo",
        user_id="user-a",
        session_id="demo-session",
        active_intent="return_request",
        plan_state="collecting_reason",
        slots={"order_id": "JP20260919002"},
    )
    memory.record_event(
        tenant_id="demo",
        user_id="user-a",
        event_type="return_requested",
        summary="demo",
        outcome="completed",
        entity_refs=["JP20260919002"],
        source_trace_id="trace-demo",
    )
    memory.set_profile(
        tenant_id="demo",
        user_id="user-a",
        memory_key="communication_language",
        memory_value="zh-CN",
        source_type="explicit",
        confidence=1.0,
        source_trace_id="trace-profile",
    )
    with service.session_manager.session_scope() as session:
        demo_order = (
            session.query(Order)
            .filter(Order.order_no == "JP20260919002")
            .one()
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        demo_order.delivered_at = now - timedelta(days=30)
        session.add(
            AfterSalesRequest(
                request_no="AS-FOREIGN",
                tenant_id="other",
                user_id="user-z",
                order_no="FOREIGN-ORDER",
                reason="other",
                status="submitted",
                idempotency_key="foreign-idem",
            )
        )
        session.add(
            ActionExecution(
                action_id="ACT-FOREIGN",
                tenant_id="other",
                user_id="user-z",
                tool_name="return.create",
                payload_hash="foreign-hash",
                idempotency_key="foreign-idem",
                status="succeeded",
                external_ref="AS-FOREIGN",
                confirmed_by="user-z",
            )
        )

    result = reset_demo_state(database_url)

    assert result == {
        "after_sales_requests": 1,
        "action_executions": 1,
        "working_states": 1,
        "memory_events": 1,
        "profile_memories": 1,
        "return_window_refreshed": 1,
    }
    assert memory.get_working("demo", "user-a", "demo-session") is None
    assert memory.recall_events("demo", "user-a") == []
    assert memory.recall_profiles("demo", "user-a") == []
    assert service.count_return_requests() == 1
    assert service.get_order("demo", "user-a", "JP20260919002") is not None
    with service.session_manager.session_scope() as session:
        remaining = session.query(AfterSalesRequest).one()
        assert (remaining.tenant_id, remaining.user_id) == ("other", "user-z")
        refreshed = (
            session.query(Order)
            .filter(Order.order_no == "JP20260919002")
            .one()
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        assert now - refreshed.delivered_at < timedelta(minutes=1)


def test_reset_refuses_non_sqlite_databases():
    try:
        reset_demo_state("postgresql://example.invalid/jakepilot")
    except ValueError as exc:
        assert str(exc) == "demo reset only supports SQLite"
    else:
        raise AssertionError("non-SQLite reset must be rejected")


def test_cli_configuration_loads_the_requested_dotenv_file(tmp_path, monkeypatch):
    database_file = tmp_path / "configured.db"
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text(
        f"JAKEPILOT_DATABASE_URL=sqlite:///{database_file.as_posix()}\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("JAKEPILOT_DATABASE_URL", raising=False)

    assert load_configured_database_url(dotenv_file) == (
        f"sqlite:///{database_file.as_posix()}"
    )
