import json
from dataclasses import replace

import pytest

from evaluation.contracts import EvalCase, EvalObservation, EvalResult
from evaluation.formal_evidence_store import FormalEvidenceStore
from evaluation.formal_gate import FormalEvaluationManifest, dataset_digest
from evaluation.order_runner import CaseRun, SuiteRun, summarize_case_runs
from evaluation.release_gate import formal_report_errors


CATEGORY_TARGETS = {
    "knowledge": 40,
    "order_logistics": 40,
    "return_exchange": 40,
    "appointment": 30,
    "combined_intent": 20,
    "memory_dependency": 15,
    "safety_exception": 15,
}


def _cases() -> tuple[EvalCase, ...]:
    return tuple(
        EvalCase(
            case_id=f"{category}-{index:03d}",
            dataset_version="ecommerce-agent-golden-v1",
            category=category,
            turns=(f"private-question-{category}-{index}",),
            review_status="approved",
            source_ref=f"mock-fixture:{category}",
        )
        for category, count in CATEGORY_TARGETS.items()
        for index in range(count)
    )


def _suite(cases: tuple[EvalCase, ...]) -> SuiteRun:
    case_runs = tuple(
        CaseRun(
            case_id=case.case_id,
            category=case.category,
            observation=EvalObservation(
                case_id=case.case_id,
                terminal_status="completed",
                answers=("private-answer",),
                write_count=0,
            ),
            result=EvalResult(
                case_id=case.case_id,
                passed=True,
                metrics={"bounded_termination_rate": True},
            ),
        )
        for case in cases
    )
    return SuiteRun(
        dataset_version=cases[0].dataset_version,
        dataset_digest=dataset_digest(cases),
        case_runs=case_runs,
        summary=summarize_case_runs(case_runs),
    )


def _manifest() -> FormalEvaluationManifest:
    return FormalEvaluationManifest(
        dataset_version="ecommerce-agent-golden-v1",
        model_version="deepseek-flash-2026-09",
        prompt_version="planner-v3",
        tool_fixture_version="ecommerce-mock-v2",
        code_revision="abc123456789",
        repeats=3,
    )


def test_formal_evidence_store_writes_three_run_privacy_minimized_report(tmp_path):
    cases = _cases()
    runs = tuple(_suite(cases) for _ in range(3))

    output = FormalEvidenceStore(tmp_path).write(
        cases=cases,
        runs=runs,
        manifest=_manifest(),
        variant="jakepilot",
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["schema_version"] == "2.0"
    assert payload["suite_kind"] == "golden"
    assert payload["formal_benchmark"] is True
    assert payload["repeat_count"] == 3
    assert payload["case_count"] == 200
    assert payload["formal_gate"]["eligible"] is True
    assert payload["run_config"]["variant"] == "jakepilot"
    assert len(payload["run_summaries"]) == 3
    assert payload["summary"]["end_to_end_task_success"] == {
        "passed": 600,
        "total": 600,
        "rate": 1.0,
    }
    assert len(payload["runs"]) == 3
    assert len(payload["runs"][0]["cases"]) == 200
    assert "answer_digest" in payload["runs"][0]["cases"][0]
    assert "private-question" not in serialized
    assert "private-answer" not in serialized
    assert formal_report_errors(payload, expected_variant="jakepilot") == ()


def test_formal_evidence_store_rejects_missing_repeat_without_writing(tmp_path):
    cases = _cases()

    with pytest.raises(ValueError, match="exactly three runs"):
        FormalEvidenceStore(tmp_path).write(
            cases=cases,
            runs=(_suite(cases), _suite(cases)),
            manifest=_manifest(),
            variant="baseline",
        )

    assert list(tmp_path.iterdir()) == []


def test_formal_evidence_store_rejects_summary_that_disagrees_with_case_runs(
    tmp_path,
):
    cases = _cases()
    valid_run = _suite(cases)
    invalid_run = replace(
        valid_run,
        summary={
            "end_to_end_task_success": {
                "passed": 199,
                "total": 200,
                "rate": 0.995,
            },
            "bounded_termination_rate": {
                "passed": 200,
                "total": 200,
                "rate": 1.0,
            },
        },
    )

    with pytest.raises(ValueError, match="summary does not match case results"):
        FormalEvidenceStore(tmp_path).write(
            cases=cases,
            runs=(valid_run, invalid_run, valid_run),
            manifest=_manifest(),
            variant="jakepilot",
        )

    assert list(tmp_path.iterdir()) == []
