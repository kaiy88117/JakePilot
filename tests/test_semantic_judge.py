import asyncio
import json

from evaluation.semantic_judge import SemanticJudge


class FakeResponse:
    def __init__(self, content):
        self.content = content


class FakeModel:
    def __init__(self, content):
        self.content = content
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return FakeResponse(self.content)


def test_semantic_judge_returns_pass_from_strict_scores_without_hidden_reasoning():
    model = FakeModel(
        json.dumps(
            {
                "relevance_score": 0.92,
                "completeness_score": 0.86,
                "reason_codes": ["answers_user_intent", "covers_required_facts"],
            }
        )
    )
    judge = SemanticJudge(model=model, timeout_seconds=1.0)

    result = asyncio.run(
        judge.evaluate(
            question="订单什么时候到？",
            answer="预计明天送达。",
            required_facts=("预计明天送达",),
        )
    )

    assert result.status == "pass"
    assert result.relevance_score == 0.92
    assert result.completeness_score == 0.86
    assert result.reason_codes == (
        "answers_user_intent",
        "covers_required_facts",
    )
    assert result.error_code is None
    assert "不要输出分析过程" in model.messages[0]["content"]


def test_semantic_judge_returns_fail_when_one_dimension_is_below_threshold():
    model = FakeModel(
        json.dumps(
            {
                "relevance_score": 0.91,
                "completeness_score": 0.62,
                "reason_codes": ["missing_required_fact"],
            }
        )
    )

    result = asyncio.run(
        SemanticJudge(model=model).evaluate(
            question="如何退货？",
            answer="可以申请。",
            required_facts=("需要二次确认",),
        )
    )

    assert result.status == "fail"
    assert result.reason_codes == ("missing_required_fact",)


def test_semantic_judge_maps_invalid_or_extra_output_to_unknown():
    model = FakeModel(
        json.dumps(
            {
                "relevance_score": 0.95,
                "completeness_score": 0.95,
                "reason_codes": ["answers_user_intent"],
                "hidden_reasoning": "must never be stored",
            }
        )
    )

    result = asyncio.run(
        SemanticJudge(model=model).evaluate(
            question="问题",
            answer="回答",
            required_facts=(),
        )
    )

    assert result.status == "unknown"
    assert result.error_code == "invalid_judge_output"
    assert "hidden_reasoning" not in result.model_dump()


def test_semantic_judge_maps_timeout_to_unknown_without_blocking_eval():
    class SlowModel:
        async def ainvoke(self, messages):
            await asyncio.sleep(0.05)
            return FakeResponse("{}")

    result = asyncio.run(
        SemanticJudge(model=SlowModel(), timeout_seconds=0.001).evaluate(
            question="问题",
            answer="回答",
            required_facts=(),
        )
    )

    assert result.status == "unknown"
    assert result.error_code == "judge_timeout"
