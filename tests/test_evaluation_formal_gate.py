from evaluation.contracts import EvalCase
from evaluation.formal_gate import (
    FormalEvaluationGate,
    FormalEvaluationManifest,
)


CATEGORY_TARGETS = {
    "knowledge": 40,
    "order_logistics": 40,
    "return_exchange": 40,
    "appointment": 30,
    "combined_intent": 20,
    "memory_dependency": 15,
    "safety_exception": 15,
}


def _case(case_id: str, category: str, *, turn: str | None = None) -> EvalCase:
    return EvalCase(
        case_id=case_id,
        dataset_version="ecommerce-agent-golden-v1",
        category=category,
        turns=(turn or f"fixture-{case_id}",),
    )


def _formal_cases() -> tuple[EvalCase, ...]:
    return tuple(
        _case(f"{category}-{index:03d}", category)
        for category, count in CATEGORY_TARGETS.items()
        for index in range(count)
    )


def _manifest(**overrides) -> FormalEvaluationManifest:
    payload = {
        "dataset_version": "ecommerce-agent-golden-v1",
        "model_version": "deepseek-flash-2026-09",
        "prompt_version": "planner-v3",
        "tool_fixture_version": "ecommerce-mock-v2",
        "code_revision": "abc123456789",
        "repeats": 3,
    }
    payload.update(overrides)
    return FormalEvaluationManifest(**payload)


def test_formal_gate_accepts_only_complete_versioned_200_case_dataset():
    assessment = FormalEvaluationGate().assess(_formal_cases(), _manifest())

    assert assessment.eligible is True
    assert assessment.errors == ()
    assert assessment.case_count == 200
    assert assessment.category_counts == CATEGORY_TARGETS
    assert len(assessment.dataset_digest) == 64


def test_formal_gate_rejects_missing_quota_wrong_repeats_and_version_mismatch():
    cases = list(_formal_cases())
    cases.pop()
    cases[0] = cases[0].model_copy(
        update={"dataset_version": "ecommerce-agent-golden-v2"}
    )

    assessment = FormalEvaluationGate().assess(
        tuple(cases),
        _manifest(repeats=1),
    )

    assert assessment.eligible is False
    assert assessment.case_count == 199
    assert set(assessment.errors) == {
        "case_count_below_200",
        "category_quota:safety_exception:14/15",
        "dataset_version_mismatch:ecommerce-agent-golden-v2",
        "repeat_count_must_equal_3",
    }


def test_formal_dataset_digest_is_order_independent_and_content_sensitive():
    cases = _formal_cases()
    gate = FormalEvaluationGate()

    first = gate.assess(cases, _manifest()).dataset_digest
    reordered = gate.assess(tuple(reversed(cases)), _manifest()).dataset_digest
    changed_cases = list(cases)
    changed_cases[0] = _case(
        changed_cases[0].case_id,
        changed_cases[0].category,
        turn="changed fixture",
    )
    changed = gate.assess(tuple(changed_cases), _manifest()).dataset_digest

    assert first == reordered
    assert changed != first


def test_formal_gate_rejects_duplicate_ids_and_unpinned_versions():
    cases = list(_formal_cases())
    cases[-1] = cases[0]

    assessment = FormalEvaluationGate().assess(
        tuple(cases),
        _manifest(
            model_version="not_pinned",
            prompt_version="unknown",
            tool_fixture_version="not_applicable",
            code_revision="unknown",
        ),
    )

    assert assessment.eligible is False
    assert {
        "duplicate_case_id:knowledge-000",
        "unpinned_model_version",
        "unpinned_prompt_version",
        "unpinned_tool_fixture_version",
        "unpinned_code_revision",
    }.issubset(assessment.errors)
