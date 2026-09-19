import asyncio
import json

from agents.order_after_sales_agent import OrderAfterSalesAgent
from api.stream_protocol import iter_sse_events
from services.order_after_sales_service import OrderAfterSalesService


def _decode(frame):
    lines = frame.strip().splitlines()
    return (
        lines[0].removeprefix("event: "),
        json.loads(lines[1].removeprefix("data: ")),
    )


def _request(agent, message):
    async def source():
        yield "[THOUGHT][归类机器人] 已识别为订单售后任务，转交订单售后 Agent 处理。"
        async for token in agent.run_stream(message):
            yield token

    async def collect():
        return [
            _decode(frame)
            async for frame in iter_sse_events(source(), "e2e-turn")
        ]

    return asyncio.run(collect())


def _answer(events):
    return "".join(
        payload.get("delta", "")
        for name, payload in events
        if name == "answer_delta"
    )


def test_order_and_return_flow_is_idempotent_and_session_scoped(tmp_path):
    service = OrderAfterSalesService(
        f"sqlite:///{(tmp_path / 'e2e-orders.db').as_posix()}"
    )
    service.seed_demo_data()
    first = OrderAfterSalesAgent("session-a", service, "demo", "user-a")
    second = OrderAfterSalesAgent("session-b", service, "demo", "user-a")

    logistics = _request(first, "查询订单 JP20260919001 的物流")
    pending = _request(first, "申请退货 JP20260919002，原因是商品破损")
    foreign_confirmation = _request(second, "确认提交")
    confirmed = _request(first, "确认提交")
    repeated = _request(first, "确认提交")

    assert next(payload for name, payload in logistics if name == "route_selected")["route"] == "order_after_sales"
    assert any(name == "tool_started" for name, _ in logistics)
    assert any(name == "confirmation_required" for name, _ in pending)
    assert "当前没有待确认操作" in _answer(foreign_confirmation)
    assert "退货申请" in _answer(confirmed)
    assert "当前没有待确认操作" in _answer(repeated)
    assert service.count_return_requests() == 1


def test_failed_write_emits_failed_terminal_and_remains_retryable(
    tmp_path, monkeypatch
):
    service = OrderAfterSalesService(
        f"sqlite:///{(tmp_path / 'e2e-failure.db').as_posix()}"
    )
    service.seed_demo_data()
    agent = OrderAfterSalesAgent("session-a", service, "demo", "user-a")
    _request(agent, "申请退货 JP20260919002，原因是商品破损")
    monkeypatch.setattr(
        service,
        "create_return_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("private")),
    )

    failed = _request(agent, "确认提交")

    assert any(name == "turn_failed" for name, _ in failed)
    assert not any(name == "turn_ended" for name, _ in failed)
    assert agent.has_pending_action is True
