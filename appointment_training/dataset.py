"""Validation and leakage gates for appointment training JSONL files."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from appointment_decision import (
    AppointmentDecision,
    AppointmentSlots,
    DecisionValidationError,
    validate_decision,
)


_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_ADDRESS_FIELDS = {
    "address",
    "address_detail",
    "customer_address",
    "delivery_address",
    "full_address",
    "street_address",
}


class DatasetValidationError(ValueError):
    """Raised when a dataset is unsafe, malformed, or leaks across splits."""


class TrainingMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AppointmentTrainingSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_id: str = Field(min_length=1, max_length=128)
    cluster_id: str = Field(min_length=1, max_length=128)
    split: Literal["train", "eval"]
    messages: tuple[TrainingMessage, ...] = Field(min_length=1)
    decision: AppointmentDecision
    source_type: Literal[
        "licensed_public",
        "anonymous_template",
        "human_authored",
    ]
    license_ref: str = Field(min_length=1, max_length=256)


class DatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_count: int = Field(ge=0)
    counts_by_split: dict[str, int]
    counts_by_source: dict[str, int]
    counts_by_action: dict[str, int]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    formal: bool


def validate_dataset(path: Path, *, formal: bool = False) -> DatasetManifest:
    path = Path(path)
    samples: list[AppointmentTrainingSample] = []
    seen_ids: set[str] = set()
    cluster_splits: dict[str, set[str]] = defaultdict(set)

    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise DatasetValidationError(f"cannot read dataset: {path}") from exc

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetValidationError(
                f"line {line_number}: invalid JSON"
            ) from None
        if not isinstance(raw, dict):
            raise DatasetValidationError(
                f"line {line_number}: row must be a JSON object"
            )
        _assert_no_personal_data(raw, line_number)
        try:
            item = AppointmentTrainingSample.model_validate(raw)
        except ValidationError as exc:
            raise DatasetValidationError(
                f"line {line_number}: invalid row: {exc.errors()[0]['msg']}"
            ) from None
        try:
            validate_decision(item.decision, AppointmentSlots())
        except DecisionValidationError as exc:
            raise DatasetValidationError(
                f"line {line_number}: business-invalid decision label: {exc}"
            ) from None
        if item.sample_id in seen_ids:
            raise DatasetValidationError(
                f"line {line_number}: duplicate sample_id {item.sample_id}"
            )
        seen_ids.add(item.sample_id)
        cluster_splits[item.cluster_id].add(item.split)
        samples.append(item)

    if not samples:
        raise DatasetValidationError("dataset contains no samples")

    leaked = sorted(
        cluster_id
        for cluster_id, splits in cluster_splits.items()
        if len(splits) > 1
    )
    if leaked:
        raise DatasetValidationError(
            "cluster leakage across splits: " + ", ".join(leaked[:5])
        )

    counts_by_split = Counter(item.split for item in samples)
    if formal and counts_by_split.get("eval", 0) < 150:
        raise DatasetValidationError(
            "formal dataset requires at least 150 eval samples"
        )

    normalized = sorted(
        json.dumps(
            item.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for item in samples
    )
    digest = hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest()

    return DatasetManifest(
        sample_count=len(samples),
        counts_by_split=dict(sorted(counts_by_split.items())),
        counts_by_source=dict(
            sorted(Counter(item.source_type for item in samples).items())
        ),
        counts_by_action=dict(
            sorted(Counter(item.decision.action for item in samples).items())
        ),
        sha256=digest,
        formal=formal,
    )


def _assert_no_personal_data(raw: dict, line_number: int) -> None:
    keys = _collect_keys(raw)
    blocked_fields = sorted(keys & _ADDRESS_FIELDS)
    serialized = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    if blocked_fields or _PHONE.search(serialized) or _EMAIL.search(serialized):
        detail = blocked_fields[0] if blocked_fields else "phone_or_email"
        raise DatasetValidationError(
            f"line {line_number}: personal data is not allowed ({detail})"
        )


def _collect_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = {str(key).lower() for key in value}
        for nested in value.values():
            keys.update(_collect_keys(nested))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for nested in value:
            keys.update(_collect_keys(nested))
        return keys
    return set()
