"""Deterministic component metrics for appointment decision models."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from appointment_decision import (
    AppointmentDecision,
    AppointmentSlots,
    DecisionRequest,
    DecisionValidationError,
    validate_decision,
)

from .dataset import AppointmentTrainingSample, validate_dataset


_ACTIONS = ("ask_user", "query_slots", "request_confirmation", "finish")
_SLOT_FIELDS = (
    "order_id",
    "product_ref",
    "service_type",
    "issue_type",
    "region",
    "date_range",
    "slot_id",
)


class EvaluationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code_revision: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=128)
    model_version: str = Field(min_length=1, max_length=256)
    hardware_label: str = Field(min_length=1, max_length=256)


class EvaluationThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_formal_samples: int = 150
    minimum_structure_validity: float = 0.98
    minimum_slot_exact_match: float = 0.90
    minimum_action_accuracy: float = 0.90
    maximum_hallucinated_slot_rate: float = 0.02


class AppointmentModelReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_sha256: str
    code_revision: str
    prompt_version: str
    model_version: str
    hardware_label: str
    sample_count: int
    formal_benchmark: bool
    promotion_eligible: bool
    counts: dict[str, int]
    structure_validity: float
    slot_exact_match: float
    field_f1: float
    action_accuracy: float
    hallucinated_slot_rate: float
    refusal_boundary_rate: float
    p95_latency_ms: float
    fallback_rate: float


def evaluate_model(
    path: Path,
    predict: Callable[[DecisionRequest], str],
    *,
    metadata: EvaluationMetadata,
    formal: bool = False,
    thresholds: EvaluationThresholds | None = None,
) -> AppointmentModelReport:
    thresholds = thresholds or EvaluationThresholds()
    manifest = validate_dataset(path, formal=formal)
    samples = _load_eval_samples(path)
    if not samples:
        raise ValueError("dataset contains no evaluation samples")

    counts = {
        "structure_valid": 0,
        "structure_invalid": 0,
        "action_correct": 0,
        "action_incorrect": 0,
        "slot_exact": 0,
        "slot_inexact": 0,
        "field_true_positive": 0,
        "field_false_positive": 0,
        "field_false_negative": 0,
        "hallucinated_slot_cases": 0,
        "refusal_boundary_pass": 0,
        "refusal_boundary_fail": 0,
        "fallback_required": 0,
    }
    latencies: list[float] = []

    for sample in samples:
        request = _to_request(sample)
        started = time.perf_counter()
        predicted: AppointmentDecision | None = None
        business_valid = False
        try:
            raw = predict(request)
            predicted = AppointmentDecision.model_validate_json(raw)
            validate_decision(predicted, AppointmentSlots())
            business_valid = True
        except (ValidationError, DecisionValidationError, TypeError, ValueError):
            pass
        except Exception:
            pass
        finally:
            latencies.append(max(0.0, (time.perf_counter() - started) * 1000))

        reference = sample.decision
        if predicted is None:
            counts["structure_invalid"] += 1
        else:
            counts["structure_valid"] += 1

        if predicted is not None and predicted.action == reference.action:
            counts["action_correct"] += 1
        else:
            counts["action_incorrect"] += 1

        if predicted is not None and predicted.slots == reference.slots:
            counts["slot_exact"] += 1
        else:
            counts["slot_inexact"] += 1

        tp, fp, fn, hallucinated = _score_fields(reference, predicted)
        counts["field_true_positive"] += tp
        counts["field_false_positive"] += fp
        counts["field_false_negative"] += fn
        counts["hallucinated_slot_cases"] += int(hallucinated)

        if reference.action == "ask_user":
            if predicted is not None and predicted.action == "ask_user":
                counts["refusal_boundary_pass"] += 1
            else:
                counts["refusal_boundary_fail"] += 1

        if predicted is None or not business_valid:
            counts["fallback_required"] += 1

    total = len(samples)
    boundary_total = (
        counts["refusal_boundary_pass"] + counts["refusal_boundary_fail"]
    )
    field_denominator = (
        2 * counts["field_true_positive"]
        + counts["field_false_positive"]
        + counts["field_false_negative"]
    )
    structure_validity = counts["structure_valid"] / total
    slot_exact_match = counts["slot_exact"] / total
    action_accuracy = counts["action_correct"] / total
    hallucinated_slot_rate = counts["hallucinated_slot_cases"] / total
    formal_benchmark = formal and total >= thresholds.minimum_formal_samples
    promotion_eligible = formal_benchmark and all(
        (
            structure_validity >= thresholds.minimum_structure_validity,
            slot_exact_match >= thresholds.minimum_slot_exact_match,
            action_accuracy >= thresholds.minimum_action_accuracy,
            hallucinated_slot_rate
            <= thresholds.maximum_hallucinated_slot_rate,
        )
    )

    return AppointmentModelReport(
        dataset_sha256=manifest.sha256,
        code_revision=metadata.code_revision,
        prompt_version=metadata.prompt_version,
        model_version=metadata.model_version,
        hardware_label=metadata.hardware_label,
        sample_count=total,
        formal_benchmark=formal_benchmark,
        promotion_eligible=promotion_eligible,
        counts=counts,
        structure_validity=structure_validity,
        slot_exact_match=slot_exact_match,
        field_f1=(
            2 * counts["field_true_positive"] / field_denominator
            if field_denominator
            else 1.0
        ),
        action_accuracy=action_accuracy,
        hallucinated_slot_rate=hallucinated_slot_rate,
        refusal_boundary_rate=(
            counts["refusal_boundary_pass"] / boundary_total
            if boundary_total
            else 1.0
        ),
        p95_latency_ms=_nearest_rank_percentile(latencies, 0.95),
        fallback_rate=counts["fallback_required"] / total,
    )


def _load_eval_samples(path: Path) -> list[AppointmentTrainingSample]:
    samples: list[AppointmentTrainingSample] = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            sample = AppointmentTrainingSample.model_validate(json.loads(line))
            if sample.split == "eval":
                samples.append(sample)
    return samples


def _to_request(sample: AppointmentTrainingSample) -> DecisionRequest:
    user_messages = [item.content for item in sample.messages if item.role == "user"]
    if not user_messages:
        raise ValueError(f"evaluation sample {sample.sample_id} has no user message")
    return DecisionRequest(
        message=user_messages[-1],
        recent_appointment_history=tuple(user_messages[:-1]),
        current_time=datetime.now(timezone.utc),
        allowed_actions=_ACTIONS,
    )


def _score_fields(
    reference: AppointmentDecision,
    predicted: AppointmentDecision | None,
) -> tuple[int, int, int, bool]:
    reference_values = reference.slots.model_dump()
    predicted_values = predicted.slots.model_dump() if predicted else {}
    true_positive = false_positive = false_negative = 0
    hallucinated = False
    for name in _SLOT_FIELDS:
        expected = reference_values[name]
        actual = predicted_values.get(name)
        if actual is not None and actual == expected:
            true_positive += 1
        elif actual is not None:
            false_positive += 1
            if expected is None:
                hallucinated = True
            if expected is not None:
                false_negative += 1
        elif expected is not None:
            false_negative += 1
    return true_positive, false_positive, false_negative, hallucinated


def _nearest_rank_percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]
