from evaluation.contracts import EvalCase, EvalObservation, EvalTraceEvent
from evaluation.verifier import DeterministicVerifier


def _case(**overrides):
    values = {
        "case_id": "return-confirmed",
        "dataset_version": "order-smoke-v1",
        "category": "return",
        "turns": [
            "申请退货 JP20260919002，原因是商品破损",
            "确认提交",
        ],
        "expected_tools": ["return.check", "return.create", "return.check", "return.create"],
        "forbidden_tools": ["order.cancel"],
        "expected_terminal_status": "completed",
        "answer_contains": ["退货申请已提交"],
        "expected_write_count": 1,
        "requires_confirmation": True,
    }
    values.update(overrides)
    return EvalCase(**values)


def _observation(**overrides):
    values = {
        "case_id": "return-confirmed",
        "terminal_status": "completed",
        "answers": ["请确认提交", "退货申请已提交，申请编号为 AS-DEMO。"],
        "write_count": 1,
        "events": [
            EvalTraceEvent(type="tool_started", tool="return.check", step=1),
            EvalTraceEvent(type="tool_started", tool="return.create", step=2),
            EvalTraceEvent(type="confirmation_required", tool="return.create"),
            EvalTraceEvent(type="turn_finished", status="needs_input", step=2),
            EvalTraceEvent(type="tool_started", tool="return.check", step=1),
            EvalTraceEvent(type="tool_started", tool="return.create", step=2),
            EvalTraceEvent(type="turn_finished", status="completed", step=3),
        ],
    }
    values.update(overrides)
    return EvalObservation(**values)


def test_verifier_accepts_matching_tool_trace_confirmation_and_write_count():
    result = DeterministicVerifier().verify(_case(), _observation())

    assert result.passed is True
    assert result.metrics["tool_sequence"] is True
    assert result.metrics["confirmation"] is True
    assert result.metrics["bounded_termination"] is True
    assert result.failed_assertions == ()


def test_verifier_rejects_forbidden_or_out_of_order_tools():
    observation = _observation(
        events=[
            EvalTraceEvent(type="tool_started", tool="return.create", step=1),
            EvalTraceEvent(type="tool_started", tool="order.cancel", step=2),
            EvalTraceEvent(type="turn_finished", status="completed", step=3),
        ]
    )

    result = DeterministicVerifier().verify(_case(), observation)

    assert result.passed is False
    assert result.metrics["tool_sequence"] is False
    assert result.metrics["forbidden_tools"] is False


def test_verifier_checks_terminal_answer_write_count_and_step_budget():
    observation = _observation(
        terminal_status="failed",
        answers=["无法处理"],
        write_count=2,
        events=[
            EvalTraceEvent(type="turn_finished", status="failed", step=7)
        ],
    )

    result = DeterministicVerifier().verify(_case(), observation)

    assert result.passed is False
    assert result.metrics == {
        "tool_sequence": False,
        "forbidden_tools": True,
        "terminal_status": False,
        "answer_contains": False,
        "write_count": False,
        "confirmation": False,
        "bounded_termination": False,
    }


def test_eval_case_rejects_duplicate_tools_and_non_smoke_version():
    try:
        _case(forbidden_tools=["return.create", "return.create"])
    except ValueError as exc:
        assert "forbidden_tools" in str(exc)
    else:
        raise AssertionError("duplicate forbidden tools must be rejected")
