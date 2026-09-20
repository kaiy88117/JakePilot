from __future__ import annotations

import json
from pathlib import Path

import pytest

from appointment_decision import AppointmentDecision, AppointmentSlots
from appointment_training.evaluator import EvaluationMetadata, evaluate_model


def decision(
    action: str,
    *,
    slots: AppointmentSlots,
    missing_slots: tuple[str, ...] = (),
) -> dict:
    return AppointmentDecision(
        action=action,
        slots=slots,
        missing_slots=missing_slots,
    ).model_dump(mode="json")


def row(sample_id: str, message: str, expected: dict) -> dict:
    return {
        "sample_id": sample_id,
        "cluster_id": f"cluster-{sample_id}",
        "split": "eval",
        "messages": [{"role": "user", "content": message}],
        "decision": expected,
        "source_type": "human_authored",
        "license_ref": "authoring-policy-v1",
    }


def fixture_rows_and_predictions() -> tuple[list[dict], dict[str, str]]:
    ask_washer = decision(
        "ask_user",
        slots=AppointmentSlots(product_ref="洗衣机"),
        missing_slots=("region", "date_range"),
    )
    query_fridge = decision(
        "query_slots",
        slots=AppointmentSlots(
            product_ref="冰箱",
            service_type="repair",
            region="杭州市西湖区",
            date_range="2026-09-21/2026-09-22",
        ),
    )
    query_aircon = decision(
        "query_slots",
        slots=AppointmentSlots(
            product_ref="空调",
            service_type="inspect",
            region="杭州市拱墅区",
            date_range="2026-09-23",
        ),
    )
    finish_tv = decision(
        "finish",
        slots=AppointmentSlots(
            product_ref="电视",
            slot_id="slot-001",
            confirmation=True,
        ),
    )
    ask_dishwasher = decision(
        "ask_user",
        slots=AppointmentSlots(product_ref="洗碗机"),
        missing_slots=("region", "date_range"),
    )

    rows = [
        row("apt-1", "case-one", ask_washer),
        row("apt-2", "case-two", query_fridge),
        row("apt-3", "case-three", query_aircon),
        row("apt-4", "case-four", finish_tv),
        row("apt-5", "case-five", ask_dishwasher),
    ]
    predictions = {
        "case-one": json.dumps(ask_washer, ensure_ascii=False),
        "case-two": json.dumps(query_fridge, ensure_ascii=False),
        "case-three": "not-json",
        "case-four": AppointmentDecision(
            action="request_confirmation",
            slots=AppointmentSlots(
                product_ref="电视",
                slot_id="slot-001",
                confirmation=True,
            ),
            missing_slots=(),
        ).model_dump_json(),
        "case-five": AppointmentDecision(
            action="ask_user",
            slots=AppointmentSlots(
                product_ref="洗碗机",
                issue_type="漏水",
            ),
            missing_slots=("region", "date_range"),
        ).model_dump_json(),
    }
    return rows, predictions


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows),
        encoding="utf-8",
    )
    return path


def test_component_metrics_keep_failed_outputs_in_denominator(
    tmp_path: Path,
) -> None:
    rows, predictions = fixture_rows_and_predictions()
    path = write_jsonl(tmp_path / "eval.jsonl", rows)

    report = evaluate_model(
        path,
        lambda request: predictions[request.message],
        metadata=EvaluationMetadata(
            code_revision="abc1234",
            prompt_version="appointment-json-v1",
            model_version="qwen3-1.7b-sft-dev",
            hardware_label="test-cpu",
        ),
    )

    assert report.sample_count == 5
    assert report.counts == {
        "structure_valid": 4,
        "structure_invalid": 1,
        "action_correct": 3,
        "action_incorrect": 2,
        "slot_exact": 3,
        "slot_inexact": 2,
        "field_true_positive": 8,
        "field_false_positive": 1,
        "field_false_negative": 4,
        "hallucinated_slot_cases": 1,
        "refusal_boundary_pass": 2,
        "refusal_boundary_fail": 0,
        "fallback_required": 1,
    }
    assert report.structure_validity == pytest.approx(0.8)
    assert report.slot_exact_match == pytest.approx(0.6)
    assert report.field_f1 == pytest.approx(16 / 21)
    assert report.action_accuracy == pytest.approx(0.6)
    assert report.hallucinated_slot_rate == pytest.approx(0.2)
    assert report.refusal_boundary_rate == pytest.approx(1.0)
    assert report.fallback_rate == pytest.approx(0.2)
    assert report.p95_local_latency_ms >= 0
    assert report.formal_benchmark is False
    assert report.promotion_eligible is False


def test_report_evidence_contains_provenance_but_no_raw_content(
    tmp_path: Path,
) -> None:
    rows, predictions = fixture_rows_and_predictions()
    report = evaluate_model(
        write_jsonl(tmp_path / "eval.jsonl", rows),
        lambda request: predictions[request.message],
        metadata=EvaluationMetadata(
            code_revision="abc1234",
            prompt_version="appointment-json-v1",
            model_version="qwen3-1.7b-sft-dev",
            hardware_label="test-cpu",
        ),
    )

    serialized = report.model_dump_json()
    assert report.dataset_sha256
    assert report.code_revision == "abc1234"
    assert report.prompt_version == "appointment-json-v1"
    assert report.model_version == "qwen3-1.7b-sft-dev"
    assert report.hardware_label == "test-cpu"
    for raw_content in ("case-one", "case-five", "messages", "洗衣机"):
        assert raw_content not in serialized
