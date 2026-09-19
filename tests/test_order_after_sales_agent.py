import asyncio
import json

from agents.order_after_sales_agent import OrderAfterSalesAgent
from services.order_after_sales_service import OrderAfterSalesService


def _agent(tmp_path, session_id="session-a"):
    service = OrderAfterSalesService(
        f"sqlite:///{(tmp_path / 'agent-orders.db').as_posix()}"
    )
    service.seed_demo_data()
    return OrderAfterSalesAgent(
        session_id=session_id,
        service=service,
        tenant_id="demo",
        user_id="user-a",
    )


def _collect(agent, message):
    async def collect():
        return [token async for token in agent.run_stream(message)]

    return asyncio.run(collect())


def _answer(tokens):
    answer_started = False
    parts = []
    for token in tokens:
        if token.startswith("[REPLY]"):
            answer_started = True
            parts.append(token.split("]", 2)[-1])
        elif answer_started and not token.startswith("[EVENT]"):
            parts.append(token)
    return "".join(parts)


def _events(tokens):
    return [
        json.loads(token.removeprefix("[EVENT]"))
        for token in tokens
        if token.startswith("[EVENT]")
    ]


def test_logistics_query_uses_the_bounded_tool_runtime(tmp_path):
    agent = _agent(tmp_path)

    tokens = _collect(agent, "查询订单 JP20260919001 的物流")

    assert "正在运输途中" in _answer(tokens)
    assert [
        event["data"].get("tool")
        for event in _events(tokens)
        if event["type"] == "tool_started"
    ] == ["logistics.get"]
    assert agent.has_pending_action is False


def test_return_request_requires_confirmation_before_write(tmp_path):
    agent = _agent(tmp_path)

    tokens = _collect(
        agent,
        "申请退货 JP20260919002，原因是商品破损",
    )

    confirmation = next(
        event for event in _events(tokens) if event["type"] == "confirmation_required"
    )
    assert confirmation["data"]["order_id"] == "JP20260919002"
    assert "确认提交" in _answer(tokens)
    assert agent.service.count_return_requests() == 0
    assert agent.has_pending_action is True


def test_confirmation_executes_the_frozen_action_once(tmp_path):
    agent = _agent(tmp_path)
    _collect(agent, "申请退货 JP20260919002，原因是商品破损")

    confirmed = _collect(agent, "确认提交")
    repeated = _collect(agent, "确认提交")

    assert "退货申请" in _answer(confirmed)
    assert "当前没有待确认操作" in _answer(repeated)
    assert agent.service.count_return_requests() == 1
    assert agent.has_pending_action is False


def test_pending_action_is_isolated_between_agent_sessions(tmp_path):
    service = OrderAfterSalesService(
        f"sqlite:///{(tmp_path / 'shared-orders.db').as_posix()}"
    )
    service.seed_demo_data()
    first = OrderAfterSalesAgent("session-a", service, "demo", "user-a")
    second = OrderAfterSalesAgent("session-b", service, "demo", "user-a")
    _collect(first, "申请退货 JP20260919002，原因是商品破损")

    second_answer = _answer(_collect(second, "确认提交"))

    assert "当前没有待确认操作" in second_answer
    assert service.count_return_requests() == 0


def test_task_agent_keeps_the_new_domain_agent_optional():
    from agents.task_classification_agent import TaskClassificationAgent

    agent = TaskClassificationAgent(None, None)

    assert agent.order_after_sales_agent is None


def test_classification_processor_routes_order_after_sales_category():
    from agents.task_classification.agent_router import AgentRouter
    from agents.task_classification.classification_processor import (
        ClassificationProcessor,
    )
    from agents.task_classification.state_manager import StateManager
    from agents.task_classification.unrelated_handler import UnrelatedHandler
    from config.constants import SharedState, StateEnum

    class FakeClassifier:
        async def classify_task(self, task):
            return "order_after_sales"

    class FakeOrderAgent:
        has_pending_action = False

        async def run_stream(self, message):
            yield "[REPLY][订单售后 Agent]订单已查询"

    state_manager = StateManager(SharedState())
    order_agent = FakeOrderAgent()
    router = AgentRouter(
        appointment_agent=None,
        consultant_agent=None,
        state_manager=state_manager,
        order_after_sales_agent=order_agent,
    )
    processor = ClassificationProcessor(
        FakeClassifier(),
        state_manager,
        router,
        UnrelatedHandler(state_manager),
    )

    async def collect():
        return [
            token
            async for token in processor.process_task_stream(
                "查询订单 JP20260919001"
            )
        ]

    tokens = asyncio.run(collect())
    assert any("订单售后 Agent" in token for token in tokens)
    assert state_manager.get_current_state() == StateEnum.CLASSIFY
