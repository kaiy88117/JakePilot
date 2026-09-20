"""Deterministic safety gates for appointment model decisions."""

from __future__ import annotations

from .contracts import AppointmentDecision, AppointmentSlots


class DecisionValidationError(ValueError):
    """Raised when a structurally valid decision violates business gates."""


def validate_decision(
    decision: AppointmentDecision,
    confirmed_slots: AppointmentSlots,
) -> None:
    proposed = decision.slots.model_dump()
    confirmed = confirmed_slots.model_dump()

    for name, confirmed_value in confirmed.items():
        is_confirmed = (
            confirmed_value is not None
            if name != "confirmation"
            else confirmed_value is True
        )
        if not is_confirmed:
            continue
        proposed_value = proposed[name]
        if proposed_value != confirmed_value:
            raise DecisionValidationError(
                f"confirmed slot conflict: {name}"
            )

    for name in decision.missing_slots:
        if proposed[name] is not None:
            raise DecisionValidationError(
                f"missing_slots contains populated slot: {name}"
            )

    required_by_action = {
        "query_slots": ("product_ref", "service_type", "region", "date_range"),
        "request_confirmation": ("slot_id",),
        "finish": ("slot_id",),
    }
    missing_required = [
        name
        for name in required_by_action.get(decision.action, ())
        if proposed[name] is None
    ]
    if missing_required:
        raise DecisionValidationError(
            "action requires slots: " + ", ".join(missing_required)
        )

    if decision.action == "finish" and not decision.slots.confirmation:
        raise DecisionValidationError("finish requires confirmation")
