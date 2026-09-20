import asyncio
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from evaluation.contracts import EvalCase
from evaluation.task_graph_eval_adapter import (
    build_isolated_task_graph_category_runners,
)
from evaluation.task_graph_fixture import IsolatedTaskGraphFixtureFactory


class FakeAppointmentAgent:
    def set_shared_state(self, state):
        self.state = state

    def reset(self):
        return None

    async def run_stream(self, user_input=None):
        yield "[REPLY][预约机器人]fixture"


class FakeConsultantAgent:
    def set_shared_state(self, state):
        self.state = state

    def set_unrelated_callback(self, callback):
        self.callback = callback

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def consult_stream(self, message):
        yield "[REPLY][咨询机器人]fixture"


def _case(case_id: str) -> EvalCase:
    return EvalCase(
        case_id=case_id,
        dataset_version="ecommerce-agent-golden-v1",
        category="order_logistics",
        turns=("查询订单 JP20260919001",),
    )


def _collect(agent, message):
    async def collect():
        return [
            token
            async for token in agent.classify_task_stream(
                message,
                turn_id="fixture-turn",
            )
        ]

    return asyncio.run(collect())


def test_fixture_factory_isolates_each_case_database_and_runs_real_order_agent(
    tmp_path,
):
    factory = IsolatedTaskGraphFixtureFactory(
        work_dir=tmp_path,
        router_model_factory=lambda case: FakeListChatModel(
            responses=["order_after_sales"]
        ),
        appointment_agent_factory=lambda session_id, database_url: FakeAppointmentAgent(),
        consultant_agent_factory=lambda session_id, database_url: FakeConsultantAgent(),
    )

    first = factory(_case("order-001"))
    second = factory(_case("order-002"))
    try:
        assert first.database_url != second.database_url
        assert Path(first.database_path).parent == tmp_path
        assert Path(second.database_path).parent == tmp_path

        tokens = _collect(first, "查询订单 JP20260919001")

        assert any("订单 JP20260919001" in token for token in tokens)
        assert first.evaluation_write_count() == 0
        assert second.evaluation_write_count() == 0
    finally:
        first.close()
        second.close()


def test_fixture_recreates_same_case_from_clean_database(tmp_path):
    factory = IsolatedTaskGraphFixtureFactory(
        work_dir=tmp_path,
        router_model_factory=lambda case: FakeListChatModel(
            responses=["order_after_sales"]
        ),
        appointment_agent_factory=lambda session_id, database_url: FakeAppointmentAgent(),
        consultant_agent_factory=lambda session_id, database_url: FakeConsultantAgent(),
    )
    case = _case("return-001")
    first = factory(case)
    database_path = first.database_path
    first.close()

    Path(database_path).write_bytes(b"stale")
    second = factory(case)
    try:
        assert Path(database_path).stat().st_size > len(b"stale")
        assert second.evaluation_write_count() == 0
    finally:
        second.close()


def test_isolated_fixture_plugs_into_all_formal_category_runners(tmp_path):
    factory = IsolatedTaskGraphFixtureFactory(
        work_dir=tmp_path,
        router_model_factory=lambda case: FakeListChatModel(
            responses=["order_after_sales"]
        ),
        appointment_agent_factory=lambda session_id, database_url: FakeAppointmentAgent(),
        consultant_agent_factory=lambda session_id, database_url: FakeConsultantAgent(),
    )
    runners = build_isolated_task_graph_category_runners(factory)

    suite = runners["order_logistics"].run((_case("order-003"),))

    assert suite.case_runs[0].observation.write_count == 0
    assert suite.case_runs[0].observation.terminal_status == "completed"
