import asyncio

import pytest

from services.handoff_service import HandoffService


def _handoff_payload(**overrides):
    payload = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "turn_id": "turn-1",
        "reason_code": "user_requested",
        "summary": "用户明确请求人工客服",
        "verified_facts": [],
        "evidence_refs": [],
        "failed_steps": [],
    }
    payload.update(overrides)
    return payload


def test_create_or_get_handoff_is_idempotent_and_tenant_scoped(tmp_path):
    service = HandoffService(f"sqlite:///{tmp_path / 'handoff.db'}")

    first = service.create_or_get(**_handoff_payload())
    second = service.create_or_get(
        **_handoff_payload(summary="重复投递不应改写")
    )

    assert first["ticket_no"] == second["ticket_no"]
    assert second["summary"] == "用户明确请求人工客服"
    assert service.count("tenant-a") == 1
    assert service.list_recent("tenant-b") == []


def test_handoff_replay_rejects_a_different_user_or_session(tmp_path):
    service = HandoffService(f"sqlite:///{tmp_path / 'handoff-owner.db'}")
    service.create_or_get(**_handoff_payload())

    with pytest.raises(ValueError, match="ownership"):
        service.create_or_get(
            **_handoff_payload(user_id="user-b", session_id="session-b")
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"summary": "13800138000"},
        {"summary": "contact@example.com"},
        {"summary": "过长" * 121},
        {"verified_facts": ["fact"] * 21},
        {"reason_code": "unknown_reason"},
    ],
)
def test_handoff_rejects_unbounded_or_sensitive_content(tmp_path, overrides):
    service = HandoffService(f"sqlite:///{tmp_path / 'handoff.db'}")

    with pytest.raises(ValueError):
        service.create_or_get(**_handoff_payload(**overrides))


def _tool_context(**overrides):
    from runtime.tools import ToolContext

    values = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "turn_id": "turn-1",
        "idempotency_key": "handoff-turn-1",
    }
    values.update(overrides)
    return ToolContext(**values)


def _handoff_arguments():
    return {
        "reason_code": "user_requested",
        "summary": "用户明确请求人工客服",
        "verified_facts": [],
        "evidence_refs": [],
        "failed_steps": [],
    }


def test_handoff_control_tool_needs_idempotency_but_no_second_confirmation(
    tmp_path,
):
    from runtime.handoff import register_handoff_tool
    from runtime.tools import ToolRegistry

    service = HandoffService(f"sqlite:///{tmp_path / 'handoff.db'}")
    registry = ToolRegistry()
    register_handoff_tool(registry, service)

    missing_key = asyncio.run(
        registry.execute(
            "handoff.create",
            _handoff_arguments(),
            _tool_context(idempotency_key=None),
        )
    )
    first = asyncio.run(
        registry.execute(
            "handoff.create", _handoff_arguments(), _tool_context()
        )
    )
    second = asyncio.run(
        registry.execute(
            "handoff.create", _handoff_arguments(), _tool_context()
        )
    )

    assert missing_key.status == "failed"
    assert first.status == "handed_off"
    assert first.data["ticket_no"] == second.data["ticket_no"]
    assert service.count("tenant-a") == 1


def test_bounded_runtime_stops_with_handed_off_terminal_status(tmp_path):
    from runtime.contracts import TurnRequest, TurnStatus
    from runtime.handoff import register_handoff_tool
    from runtime.loop import BoundedAgentRuntime, PlanAction
    from runtime.tools import ToolRegistry

    class HandoffPlanner:
        async def next_action(self, turn, history):
            return PlanAction.tool("handoff.create", _handoff_arguments())

    service = HandoffService(f"sqlite:///{tmp_path / 'handoff.db'}")
    registry = ToolRegistry()
    register_handoff_tool(registry, service)
    turn = TurnRequest(
        turn_id="turn-1",
        session_id="session-a",
        user_id="user-a",
        tenant_id="tenant-a",
        message="请转人工客服",
    )

    run = asyncio.run(
        BoundedAgentRuntime(registry).run(turn, HandoffPlanner(), _tool_context())
    )

    assert run.outcome.status == TurnStatus.HANDED_OFF
    assert run.outcome.tool_calls == 1
    assert run.events[-1].type == "turn_finished"
    assert run.events[-1].data["status"] == "handed_off"


@pytest.mark.parametrize(
    "message",
    ["转人工", "请转人工客服", "我要真人客服", "请人工处理", "需要客服介入"],
)
def test_explicit_handoff_matcher_accepts_action_requests(message):
    from agents.human_handoff_agent import is_explicit_handoff_request

    assert is_explicit_handoff_request(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "人工客服上班时间",
        "人工客服电话是多少",
        "怎么联系人工客服",
        "不要转人工，继续查询订单",
    ],
)
def test_explicit_handoff_matcher_rejects_informational_questions(message):
    from agents.human_handoff_agent import is_explicit_handoff_request

    assert is_explicit_handoff_request(message) is False


def test_explicit_handoff_matcher_uses_the_action_clause_not_global_keywords():
    from agents.human_handoff_agent import is_explicit_handoff_request

    assert (
        is_explicit_handoff_request(
            "不用告诉我人工客服上班时间，现在请转人工"
        )
        is True
    )


def test_explicit_human_request_bypasses_llm_and_active_return_flow(tmp_path):
    from agents.human_handoff_agent import HumanHandoffAgent
    from agents.task_classification.agent_router import AgentRouter
    from agents.task_classification.classification_processor import (
        ClassificationProcessor,
    )
    from agents.task_classification.state_manager import StateManager
    from agents.task_classification.unrelated_handler import UnrelatedHandler
    from config.constants import SharedState

    class FailingClassifier:
        async def classify_task(self, task):
            raise AssertionError("explicit handoff must bypass the classifier")

    class ActiveOrderAgent:
        has_active_flow = True
        calls = 0

        async def run_stream(self, message):
            self.calls += 1
            yield "[REPLY][订单售后 Agent]不应执行"

    service = HandoffService(f"sqlite:///{tmp_path / 'route.db'}")
    handoff_agent = HumanHandoffAgent("session-a", service)
    state_manager = StateManager(SharedState())
    state_manager.transition_to_order_after_sales()
    order_agent = ActiveOrderAgent()
    router = AgentRouter(
        appointment_agent=None,
        consultant_agent=None,
        state_manager=state_manager,
        order_after_sales_agent=order_agent,
        human_handoff_agent=handoff_agent,
    )
    processor = ClassificationProcessor(
        FailingClassifier(),
        state_manager,
        router,
        UnrelatedHandler(state_manager),
    )

    async def collect():
        return [
            token
            async for token in processor.process_task_stream(
                "请转人工客服", turn_id="turn-route"
            )
        ]

    tokens = asyncio.run(collect())
    serialized = "".join(tokens)
    assert '"type":"handoff_created"' in serialized
    assert "HO-" in serialized
    assert service.count("demo") == 1
    assert order_agent.calls == 0
    assert state_manager.should_classify() is True


def test_handoff_failure_does_not_fabricate_ticket_number():
    from agents.human_handoff_agent import HumanHandoffAgent

    class FailingService:
        def create_or_get(self, **kwargs):
            raise RuntimeError("PRIVATE_DATABASE_DIAGNOSTIC")

    agent = HumanHandoffAgent("session-a", FailingService())

    async def collect():
        return [
            token
            async for token in agent.run_stream("转人工", turn_id="turn-fail")
        ]

    tokens = asyncio.run(collect())
    serialized = "".join(tokens)
    assert "HO-" not in serialized
    assert "PRIVATE_DATABASE_DIAGNOSTIC" not in serialized
    assert "暂时无法创建人工接管工单" in serialized


def test_successful_handoff_clears_pending_return_and_working_memory(tmp_path):
    from agents.human_handoff_agent import HumanHandoffAgent
    from agents.order_after_sales_agent import OrderAfterSalesAgent
    from agents.task_classification.agent_router import AgentRouter
    from agents.task_classification.state_manager import StateManager
    from config.constants import SharedState
    from services.memory_manager import MemoryManager
    from services.order_after_sales_service import OrderAfterSalesService

    database_url = f"sqlite:///{tmp_path / 'handoff-flow.db'}"
    order_service = OrderAfterSalesService(database_url)
    order_service.seed_demo_data()
    memory = MemoryManager(database_url)
    order_agent = OrderAfterSalesAgent(
        "session-a", order_service, memory_manager=memory
    )

    async def collect(source):
        return [token async for token in source]

    asyncio.run(
        collect(
            order_agent.run_stream(
                "申请退货 JP20260919002，原因是商品破损"
            )
        )
    )
    assert order_agent.has_pending_action is True
    assert memory.get_working("demo", "user-a", "session-a") is not None

    state_manager = StateManager(SharedState())
    state_manager.transition_to_order_after_sales()
    router = AgentRouter(
        appointment_agent=None,
        consultant_agent=None,
        state_manager=state_manager,
        order_after_sales_agent=order_agent,
        human_handoff_agent=HumanHandoffAgent(
            "session-a", HandoffService(database_url)
        ),
    )
    asyncio.run(
        collect(router.route_to_handoff("请转人工客服", "turn-clear-flow"))
    )

    assert order_agent.has_active_flow is False
    assert memory.get_working("demo", "user-a", "session-a") is None
    confirmation = asyncio.run(collect(order_agent.run_stream("确认提交")))
    assert "当前没有待确认操作" in "".join(confirmation)
    assert order_service.count_return_requests() == 0
