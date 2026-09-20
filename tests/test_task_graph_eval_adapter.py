from evaluation.contracts import EvalCase
from evaluation.task_graph_eval_adapter import (
    FORMAL_CATEGORIES,
    build_task_graph_category_runners,
)


class FakeTaskGraph:
    def __init__(self) -> None:
        self.turns = []

    async def classify_task_stream(self, message, turn_id=None):
        self.turns.append((message, turn_id))
        yield '[EVENT]{"type":"turn_finished","data":{"status":"completed","step":1}}'
        yield "[REPLY][归类机器人]已完成"


def test_task_graph_adapter_registers_every_formal_category():
    runners = build_task_graph_category_runners(
        agent_factory=lambda case: FakeTaskGraph()
    )

    assert set(runners) == set(FORMAL_CATEGORIES)
    assert len({id(runner) for runner in runners.values()}) == 1


def test_task_graph_adapter_uses_real_central_router_entrypoint():
    created = []

    def factory(case):
        agent = FakeTaskGraph()
        created.append(agent)
        return agent

    runners = build_task_graph_category_runners(agent_factory=factory)
    case = EvalCase(
        case_id="knowledge-001",
        dataset_version="ecommerce-agent-golden-v1",
        category="knowledge",
        turns=("耳机保修多久？",),
        answer_contains=("已完成",),
    )

    suite = runners["knowledge"].run((case,))

    assert suite.case_runs[0].result.passed is True
    assert created[0].turns == [("耳机保修多久？", "knowledge-001-1")]
