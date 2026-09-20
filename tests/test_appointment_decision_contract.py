from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from appointment_decision.contracts import (
    AppointmentDecision,
    AppointmentSlots,
    DecisionRequest,
)
from appointment_decision.validator import (
    DecisionValidationError,
    validate_decision,
)


def test_contract_rejects_unknown_fields_and_actions():
    payload = {
        "action": "call_any_tool",
        "slots": {},
        "missing_slots": [],
        "tool": "refund",
    }

    with pytest.raises(ValidationError):
        AppointmentDecision.model_validate(payload)


def test_decision_request_rejects_actions_outside_the_allowlist():
    with pytest.raises(ValidationError):
        DecisionRequest(
            message="预约维修",
            current_time=datetime.now(timezone.utc),
            allowed_actions=("refund.create",),
        )


def test_guard_rejects_finish_without_confirmation():
    decision = AppointmentDecision(
        action="finish",
        slots=AppointmentSlots(slot_id="slot-1", confirmation=False),
        missing_slots=(),
    )

    with pytest.raises(DecisionValidationError, match="confirmation"):
        validate_decision(decision, AppointmentSlots())


def test_guard_rejects_conflict_with_confirmed_slot():
    decision = AppointmentDecision(
        action="ask_user",
        slots=AppointmentSlots(order_id="JP-OTHER"),
        missing_slots=("region",),
    )

    with pytest.raises(DecisionValidationError, match="order_id"):
        validate_decision(
            decision,
            AppointmentSlots(order_id="JP20260919001"),
        )


def test_guard_rejects_missing_slot_that_already_has_a_value():
    decision = AppointmentDecision(
        action="ask_user",
        slots=AppointmentSlots(region="杭州市西湖区"),
        missing_slots=("region",),
    )

    with pytest.raises(DecisionValidationError, match="missing_slots"):
        validate_decision(decision, AppointmentSlots())


def test_guard_requires_query_slot_inputs():
    decision = AppointmentDecision(
        action="query_slots",
        slots=AppointmentSlots(
            product_ref="洗衣机",
            service_type="repair",
            region="杭州市西湖区",
        ),
        missing_slots=("date_range",),
    )

    with pytest.raises(DecisionValidationError, match="date_range"):
        validate_decision(decision, AppointmentSlots())


def test_guard_accepts_a_complete_slot_query_decision():
    decision = AppointmentDecision(
        action="query_slots",
        slots=AppointmentSlots(
            product_ref="洗衣机",
            service_type="repair",
            region="杭州市西湖区",
            date_range="2026-09-21/2026-09-22",
        ),
        missing_slots=(),
    )

    validate_decision(decision, AppointmentSlots())
