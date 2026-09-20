import json

from evaluation.evidence_store import EvidenceStore
from evaluation.order_runner import OrderAfterSalesEvalRunner, load_cases


def test_repository_smoke_cases_run_offline_and_pass_deterministically(tmp_path):
    cases = load_cases()
    runner = OrderAfterSalesEvalRunner(tmp_path / "repeatable-run")
    first = runner.run(cases)
    second = runner.run(cases)

    assert len(cases) == 6
    assert all(item.result.passed for item in first.case_runs)
    assert [item.result.metrics for item in first.case_runs] == [
        item.result.metrics for item in second.case_runs
    ]
    assert first.summary["end_to_end_task_success"] == {
        "passed": 6,
        "total": 6,
        "rate": 1.0,
    }
    restored = next(
        item for item in first.case_runs if item.case_id == "return-restart-confirm"
    )
    assert [
        event.tool
        for event in restored.observation.events
        if event.type == "tool_started"
    ] == ["return.check", "return.create", "return.check", "return.create"]


def test_evidence_store_omits_raw_turns_answers_and_tool_arguments(tmp_path):
    report = OrderAfterSalesEvalRunner(tmp_path / "run").run(load_cases())
    output = EvidenceStore(tmp_path / "evidence").write(
        report,
        code_revision="abc123",
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["suite_kind"] == "smoke"
    assert payload["dataset_version"] == "order-after-sales-smoke-v1"
    assert payload["code_revision"] == "abc123"
    assert payload["case_count"] == 6
    assert "商品破损" not in serialized
    assert "申请退货" not in serialized
    assert "arguments" not in serialized
    assert all("answer_digest" in case for case in payload["cases"])


def test_case_file_declares_smoke_version_and_no_private_fixtures():
    cases = load_cases()

    assert {case.dataset_version for case in cases} == {
        "order-after-sales-smoke-v1"
    }
    assert all(case.case_id and case.category for case in cases)
