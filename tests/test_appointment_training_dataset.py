from __future__ import annotations

import json
import subprocess
import sys
import traceback
from pathlib import Path

import pytest

from appointment_training.dataset import DatasetValidationError, validate_dataset


def sample(
    sample_id: str,
    *,
    cluster_id: str | None = None,
    split: str = "train",
    content: str = "周六想约洗衣机维修",
    decision: dict | None = None,
    **extra,
) -> dict:
    value = {
        "sample_id": sample_id,
        "cluster_id": cluster_id or f"cluster-{sample_id}",
        "split": split,
        "messages": [{"role": "user", "content": content}],
        "decision": decision
        or {
            "action": "ask_user",
            "slots": {"product_ref": "洗衣机"},
            "missing_slots": ["region", "date_range"],
        },
        "source_type": "anonymous_template",
        "license_ref": "internal-template-policy-v1",
    }
    value.update(extra)
    return value


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in rows
        ),
        encoding="utf-8",
    )
    return path


def test_valid_dataset_returns_counts_and_stable_digest(tmp_path: Path) -> None:
    rows = [
        sample("apt-2", split="eval"),
        sample(
            "apt-1",
            decision={
                "action": "query_slots",
                "slots": {
                    "product_ref": "冰箱",
                    "service_type": "repair",
                    "region": "杭州市西湖区",
                    "date_range": "2026-09-21/2026-09-22",
                },
                "missing_slots": [],
            },
            source_type="human_authored",
            license_ref="authoring-policy-v1",
        ),
    ]
    first = validate_dataset(write_jsonl(tmp_path / "first.jsonl", rows))
    second = validate_dataset(
        write_jsonl(tmp_path / "second.jsonl", list(reversed(rows)))
    )

    assert first.sample_count == 2
    assert first.counts_by_split == {"eval": 1, "train": 1}
    assert first.counts_by_source == {
        "anonymous_template": 1,
        "human_authored": 1,
    }
    assert first.counts_by_action == {"ask_user": 1, "query_slots": 1}
    assert first.sha256 == second.sha256
    assert len(first.sha256) == 64


def test_duplicate_sample_id_is_rejected(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "data.jsonl",
        [sample("apt-1"), sample("apt-1", cluster_id="cluster-other")],
    )

    with pytest.raises(DatasetValidationError, match="duplicate sample_id"):
        validate_dataset(path)


def test_cluster_leakage_across_train_and_eval_is_rejected(
    tmp_path: Path,
) -> None:
    path = write_jsonl(
        tmp_path / "data.jsonl",
        [
            sample("apt-1", cluster_id="relative-date-01", split="train"),
            sample("apt-2", cluster_id="relative-date-01", split="eval"),
        ],
    )

    with pytest.raises(DatasetValidationError, match="cluster leakage"):
        validate_dataset(path)


def test_invalid_decision_contract_is_rejected(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "data.jsonl",
        [
            sample(
                "apt-1",
                decision={
                    "action": "call_arbitrary_tool",
                    "slots": {},
                    "missing_slots": [],
                },
            )
        ],
    )

    with pytest.raises(DatasetValidationError, match="invalid row"):
        validate_dataset(path)


def test_business_invalid_decision_label_is_rejected(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "data.jsonl",
        [
            sample(
                "apt-1",
                decision={
                    "action": "finish",
                    "slots": {"confirmation": False},
                    "missing_slots": [],
                },
            )
        ],
    )

    with pytest.raises(DatasetValidationError, match="business-invalid"):
        validate_dataset(path)


@pytest.mark.parametrize(
    "row",
    [
        sample("apt-phone", content="联系电话是13800138000"),
        sample("apt-email", content="请发到 buyer@example.com"),
        sample(
            "apt-address",
            customer_address="杭州市西湖区某街道1号101室",
        ),
    ],
)
def test_personal_data_is_rejected(tmp_path: Path, row: dict) -> None:
    path = write_jsonl(tmp_path / "data.jsonl", [row])

    with pytest.raises(DatasetValidationError, match="personal data"):
        validate_dataset(path)


def test_formal_dataset_requires_at_least_150_eval_samples(
    tmp_path: Path,
) -> None:
    path = write_jsonl(
        tmp_path / "data.jsonl",
        [sample(f"apt-{index}", split="eval") for index in range(149)],
    )

    with pytest.raises(DatasetValidationError, match="at least 150"):
        validate_dataset(path, formal=True)


def test_validation_exception_chain_does_not_contain_raw_customer_text(
    tmp_path: Path,
) -> None:
    marker = "SYNTHETIC_RAW_CUSTOMER_CONTENT"
    path = write_jsonl(
        tmp_path / "data.jsonl",
        [sample("apt-1", content=marker + "x" * 4100)],
    )

    with pytest.raises(DatasetValidationError) as exc_info:
        validate_dataset(path)

    rendered = "".join(
        traceback.format_exception(exc_info.value)
    )
    assert marker not in rendered


def test_model_eval_cli_reports_validation_error_without_raw_text(
    tmp_path: Path,
) -> None:
    marker = "SYNTHETIC_CLI_PRIVATE_CONTENT"
    path = write_jsonl(
        tmp_path / "data.jsonl",
        [sample("apt-1", content=marker + "x" * 4100)],
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.run_appointment_model_eval",
            "--input",
            str(path),
            "--output-dir",
            str(tmp_path / "reports"),
            "--code-revision",
            "test",
            "--prompt-version",
            "test",
            "--model-version",
            "test",
            "--hardware-label",
            "test",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 1
    assert marker not in result.stdout
    assert marker not in result.stderr
    assert json.loads(result.stdout)["valid"] is False
