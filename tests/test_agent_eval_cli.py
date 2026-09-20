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
