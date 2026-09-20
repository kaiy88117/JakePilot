from copy import deepcopy
import json
import subprocess
import sys

from evaluation.release_gate import FormalReleaseGate


CATEGORY_COUNTS = {
    "knowledge": 40,
    "order_logistics": 40,
    "return_exchange": 40,
    "appointment": 30,
    "combined_intent": 20,
    "memory_dependency": 15,
    "safety_exception": 15,
}


def _metric(rate: float) -> dict[str, int | float]:
    return {
        "passed": round(rate * 200),
        "total": 200,
        "rate": rate,
    }


def _report(variant: str, *, digest: str = "a" * 64) -> dict:
    summary = {
        "end_to_end_task_success": _metric(0.88 if variant == "jakepilot" else 0.82),
        "tool_selection_accuracy": _metric(0.92 if variant == "jakepilot" else 0.86),
        "argument_exact_match": _metric(0.87 if variant == "jakepilot" else 0.82),
        "bounded_termination_rate": _metric(1.0),
        "unauthorized_write_rate": _metric(0.0),
        "duplicate_write_rate": _metric(0.0),
        "trace_completeness": _metric(0.99),
        "final_delivery_success": _metric(0.995),
    }
    return {
        "schema_version": "2.0",
        "run_id": f"golden_{variant}",
        "suite_kind": "golden",
        "formal_benchmark": True,
        "repeat_count": 3,
        "dataset_version": "ecommerce-agent-golden-v1",
        "dataset_digest": digest,
        "code_revision": "abc123456789",
        "created_at": "2026-09-20T01:00:00+00:00",
        "case_count": 200,
        "formal_gate": {
            "eligible": True,
            "errors": [],
            "case_count": 200,
            "dataset_digest": digest,
            "category_counts": CATEGORY_COUNTS,
        },
        "run_config": {
            "variant": variant,
            "model_version": "deepseek-flash-2026-09",
            "prompt_version": f"{variant}-prompt-v1",
            "tool_fixture_version": "ecommerce-mock-v2",
        },
        "summary": summary,
    }


def test_release_gate_accepts_same_dataset_baseline_comparison_at_thresholds():
    assessment = FormalReleaseGate().assess(
        baseline=_report("baseline"),
        candidate=_report("jakepilot"),
    )

    assert assessment.eligible is True
    assert assessment.errors == ()
    assert assessment.metric_deltas["end_to_end_task_success"] == 0.06
    assert assessment.metric_deltas["tool_selection_accuracy"] == 0.06


def test_release_gate_rejects_mismatched_dataset_and_missing_metric():
    baseline = _report("baseline")
    candidate = _report("jakepilot", digest="b" * 64)
    del candidate["summary"]["argument_exact_match"]

    assessment = FormalReleaseGate().assess(
        baseline=baseline,
        candidate=candidate,
    )

    assert assessment.eligible is False
    assert "dataset_digest_mismatch" in assessment.errors
    assert "candidate_missing_metric:argument_exact_match" in assessment.errors


def test_release_gate_rejects_threshold_failure_and_material_regression():
    baseline = _report("baseline")
    candidate = deepcopy(_report("jakepilot"))
    baseline["summary"]["tool_selection_accuracy"] = _metric(0.97)
    candidate["summary"]["tool_selection_accuracy"] = _metric(0.91)
    candidate["summary"]["unauthorized_write_rate"] = _metric(0.01)

    assessment = FormalReleaseGate().assess(
        baseline=baseline,
        candidate=candidate,
    )

    assert assessment.eligible is False
    assert "threshold_failed:unauthorized_write_rate:0.01<=0.0" in assessment.errors
    assert "regression:tool_selection_accuracy:-0.06" in assessment.errors


def test_release_gate_cli_returns_machine_readable_pass_result(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    baseline_path.write_text(json.dumps(_report("baseline")), encoding="utf-8")
    candidate_path.write_text(json.dumps(_report("jakepilot")), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.check_agent_eval_release",
            "--baseline",
            str(baseline_path),
            "--candidate",
            str(candidate_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["status"] == "release_gate_passed"
    assert payload["eligible"] is True
    assert payload["metric_deltas"]["end_to_end_task_success"] == 0.06


def test_release_gate_cli_fails_closed_on_invalid_json(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    baseline_path.write_text("{broken", encoding="utf-8")
    candidate_path.write_text(json.dumps(_report("jakepilot")), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.check_agent_eval_release",
            "--baseline",
            str(baseline_path),
            "--candidate",
            str(candidate_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert payload == {
        "status": "release_gate_error",
        "eligible": False,
        "errors": ["invalid_report_json"],
    }
