"""Fail-closed eligibility checks for a formal Agent evaluation dataset."""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evaluation.contracts import EvalCase


FORMAL_CATEGORY_TARGETS = {
    "knowledge": 40,
    "order_logistics": 40,
    "return_exchange": 40,
    "appointment": 30,
    "combined_intent": 20,
    "memory_dependency": 15,
    "safety_exception": 15,
}
_UNPINNED_VALUES = {"unknown", "not_pinned", "not_applicable"}


class FormalEvaluationManifest(BaseModel):
    """Pinned configuration required before a run can be called formal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_version: str = Field(
        pattern=r"^[a-z0-9._-]+-(?:smoke|golden)-v\d+$"
    )
    model_version: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=128)
    tool_fixture_version: str = Field(min_length=1, max_length=128)
    code_revision: str = Field(min_length=1, max_length=128)
    repeats: int = Field(default=3, ge=1, le=10)

    @field_validator(
        "model_version",
        "prompt_version",
        "tool_fixture_version",
        "code_revision",
    )
    @classmethod
    def reject_blank_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("version must not be blank")
        return normalized


class FormalGateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    eligible: bool
    errors: tuple[str, ...]
    case_count: int = Field(ge=0)
    category_counts: dict[str, int]
    dataset_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class FormalEvaluationGate:
    """Assess evidence completeness without running models or tools."""

    def assess(
        self,
        cases: tuple[EvalCase, ...],
        manifest: FormalEvaluationManifest,
    ) -> FormalGateResult:
        counts = Counter(case.category for case in cases)
        id_counts = Counter(case.case_id for case in cases)
        errors: list[str] = []
        if "-golden-v" not in manifest.dataset_version:
            errors.append("dataset_version_not_golden")
        if len(cases) < 200:
            errors.append("case_count_below_200")
        errors.extend(
            f"duplicate_case_id:{case_id}"
            for case_id, count in sorted(id_counts.items())
            if count > 1
        )
        for category, target in FORMAL_CATEGORY_TARGETS.items():
            actual = counts.get(category, 0)
            if actual < target:
                errors.append(f"category_quota:{category}:{actual}/{target}")
        mismatched_versions = sorted(
            {
                case.dataset_version
                for case in cases
                if case.dataset_version != manifest.dataset_version
            }
        )
        errors.extend(
            f"dataset_version_mismatch:{version}"
            for version in mismatched_versions
        )
        if manifest.repeats != 3:
            errors.append("repeat_count_must_equal_3")
        for field_name in (
            "model_version",
            "prompt_version",
            "tool_fixture_version",
            "code_revision",
        ):
            if getattr(manifest, field_name).lower() in _UNPINNED_VALUES:
                errors.append(f"unpinned_{field_name}")

        return FormalGateResult(
            eligible=not errors,
            errors=tuple(errors),
            case_count=len(cases),
            category_counts={
                category: counts.get(category, 0)
                for category in FORMAL_CATEGORY_TARGETS
            },
            dataset_digest=dataset_digest(cases),
        )


def dataset_digest(cases: tuple[EvalCase, ...]) -> str:
    """Return a stable digest independent of case file ordering."""

    digest_payload = [
        case.model_dump(mode="json")
        for case in sorted(cases, key=lambda item: item.case_id)
    ]
    return hashlib.sha256(
        json.dumps(
            digest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
