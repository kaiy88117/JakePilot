import asyncio

from evaluation.contracts import EvalCase
from evaluation.stream_runner import StreamCategoryEvalRunner, observe_stream_tokens


def test_observer_collects_chunked_reply_and_safe_runtime_events():
    observation = observe_stream_tokens(
        case_id="knowledge-1",
        tokens=[
            "[THOUGHT][路由]正在处理",
            '[EVENT]{"type":"tool_started","data":{"tool":"knowledge.search","step":1,"arguments":{"secret":"value"}}}',
            "[REPLY][咨询机器人]保修期",
            "为",
            "一年。",
            '[EVENT]{"type":"turn_finished","data":{"status":"completed","step":2}}',
        ],
        write_count=0,
    )

    assert observation.answers == ("保修期为一年。",)
    assert observation.terminal_status == "completed"
    assert observation.events[0].model_dump(exclude_none=True) == {
        "type": "tool_started",
        "tool": "knowledge.search",
        "step": 1,
    }
    assert "secret" not in str(observation.events)


def test_observer_fails_closed_on_malformed_event_without_leaking_payload():
    observation = observe_stream_tokens(
        case_id="safety-1",
        tokens=['[EVENT]{"type":"tool_started","private":"secret"'],
        write_count=0,
    )

    assert observation.terminal_status == "failed"
    assert observation.answers == ()
    assert observation.events[0].model_dump(exclude_none=True) == {
        "type": "stream_protocol_error",
        "status": "failed",
    }


class FakeAgent:
    def __init__(self, instance: int) -> None:
        self.instance = instance

    async def run_stream(self, message: str):
        yield '[EVENT]{"type":"turn_finished","data":{"status":"completed","step":1}}'
        yield f"[REPLY][Fake]{self.instance}:{message}"


def test_stream_category_runner_preserves_or_recreates_session_per_case():
    created = []

    def factory(case):
        agent = FakeAgent(len(created) + 1)
        created.append(agent)
        return agent

    async def stream(agent, message, turn_id):
        async for token in agent.run_stream(message):
            yield token

    runner = StreamCategoryEvalRunner(
        agent_factory=factory,
        stream_turn=stream,
    )
    cases = (
        EvalCase(
            case_id="memory-1",
            dataset_version="ecommerce-agent-golden-v1",
            category="memory_dependency",
            turns=("第一轮", "第二轮"),
            answer_contains=("1:第一轮", "1:第二轮"),
        ),
        EvalCase(
            case_id="memory-2",
            dataset_version="ecommerce-agent-golden-v1",
            category="memory_dependency",
            turns=("第一轮", "第二轮"),
            recreate_between_turns=True,
            answer_contains=("2:第一轮", "3:第二轮"),
        ),
    )

    suite = runner.run(cases)

    assert len(created) == 3
    assert all(case_run.result.passed for case_run in suite.case_runs)
    assert suite.case_runs[0].observation.answers == (
        "1:第一轮",
        "1:第二轮",
    )
    assert suite.case_runs[1].observation.answers == (
        "2:第一轮",
        "3:第二轮",
    )
