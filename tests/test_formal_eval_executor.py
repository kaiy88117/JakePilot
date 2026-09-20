import json

import pytest

from evaluation.contracts import EvalCase, EvalObservation, EvalResult
from evaluation.formal_eval_executor import FormalEvaluationExecutor
from evaluation.formal_evidence_store import FormalEvidenceStore
from evaluation.formal_gate import FormalEvaluationManifest, dataset_digest
from evaluation.order_runner import CaseRun, SuiteRun, summarize_case_runs


CATEGORY_TARGETS = {
    "knowledge": 40,
    "order_logistics": 40,
    "return_exchange": 40,
    "appointment": 30,
    "combined_intent": 20,
    "memory_dependency": 15,
    "safety_exception": 15,
}


def _cases(*, approved: bool = True) -> tuple[EvalCase, ...]:
    return tuple(
        EvalCase(
            case_id=f"{category}-{index:03d}",
            dataset_version="ecommerce-agent-golden-v1",
            category=category,
            turns=(f"question-{category}-{index}",),
            review_status="approved" if approved else "draft",
            source_ref=f"fixture:{category}",
        )
        for category, count in CATEGORY_TARGETS.items()
        for index in range(count)
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


class FakeMultiDomainRunner:
    def __init__(self, tool_fixture_version="ecommerce-mock-v2") -> None:
        self.calls = 0
        self.tool_fixture_version = tool_fixture_version

    def run(self, cases: tuple[EvalCase, ...]) -> SuiteRun:
        self.calls += 1
        case_runs = tuple(
            CaseRun(
                case_id=case.case_id,
                category=case.category,
                observation=EvalObservation(
                    case_id=case.case_id,
                    terminal_status="completed",
                    answers=("private answer",),
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


def test_formal_executor_runs_exactly_three_times_and_writes_report(tmp_path):
    runner = FakeMultiDomainRunner()
    executor = FormalEvaluationExecutor(
        runner=runner,
        evidence_store=FormalEvidenceStore(tmp_path),
    )

    output = executor.run(
        cases=_cases(),
        manifest=_manifest(),
        variant="jakepilot",
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert runner.calls == 3
    assert payload["repeat_count"] == 3
    assert len(payload["run_summaries"]) == 3


def test_formal_executor_rejects_ineligible_dataset_before_running(tmp_path):
    runner = FakeMultiDomainRunner()
    executor = FormalEvaluationExecutor(
        runner=runner,
        evidence_store=FormalEvidenceStore(tmp_path),
    )

    with pytest.raises(ValueError, match="formal evaluation gate failed"):
        executor.run(
            cases=_cases(approved=False),
            manifest=_manifest(),
            variant="baseline",
        )

    assert runner.calls == 0
    assert list(tmp_path.iterdir()) == []


def test_formal_executor_rejects_tool_fixture_version_mismatch_before_running(
    tmp_path,
):
    runner = FakeMultiDomainRunner(tool_fixture_version="ecommerce-mock-v1")
    executor = FormalEvaluationExecutor(
        runner=runner,
        evidence_store=FormalEvidenceStore(tmp_path),
    )

    with pytest.raises(ValueError, match="tool fixture version mismatch"):
        executor.run(
            cases=_cases(),
            manifest=_manifest(),
            variant="jakepilot",
        )

    assert runner.calls == 0
    assert list(tmp_path.iterdir()) == []
