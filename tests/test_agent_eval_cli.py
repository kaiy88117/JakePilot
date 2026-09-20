import json
import subprocess
import sys


def test_agent_eval_cli_writes_a_smoke_evidence_report(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.run_agent_eval",
            "--work-dir",
            str(tmp_path / "work"),
            "--output-dir",
            str(tmp_path / "reports"),
            "--code-revision",
            "test-sha",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)
    report_path = tmp_path / "reports" / payload["report_file"]
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert payload["suite_kind"] == "smoke"
    assert payload["case_count"] == 6
    assert payload["passed_cases"] == 6
    assert report["code_revision"] == "test-sha"
    assert report["summary"]["end_to_end_task_success"]["total"] == 6


def test_agent_eval_cli_formal_mode_rejects_smoke_without_writing_report(
    tmp_path,
):
    reports = tmp_path / "reports"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.run_agent_eval",
            "--formal",
            "--work-dir",
            str(tmp_path / "work"),
            "--output-dir",
            str(reports),
            "--model-version",
            "deepseek-flash-2026-09",
            "--prompt-version",
            "planner-v3",
            "--tool-fixture-version",
            "ecommerce-mock-v2",
            "--code-revision",
            "test-sha",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(result.stdout)

    assert result.returncode == 2
    assert payload["status"] == "formal_gate_rejected"
    assert payload["formal_benchmark"] is False
    assert "dataset_version_not_golden" in payload["errors"]
    assert "case_count_below_200" in payload["errors"]
    assert not reports.exists()
