from pathlib import Path


def test_ci_workflow_runs_tests_and_smoke_evidence_without_claiming_formal():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in workflow
    assert "python -m pytest -q" in workflow
    assert "python -m scripts.run_agent_eval" in workflow
    assert "artifacts/eval/reports" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "--formal" not in workflow
