from __future__ import annotations

from datetime import datetime, timezone

import pytest

from appointment_decision import (
    AppointmentDecision,
    AppointmentSlots,
    DecisionRequest,
)
from appointment_decision.gateway import AppointmentDecisionGateway


class FakeClient:
    def __init__(
        self,
        response: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls = 0

    def complete(self, request: DecisionRequest) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


@pytest.fixture
def decision_request() -> DecisionRequest:
    return DecisionRequest(
        message="周末想约洗衣机维修",
        current_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
        allowed_actions=(
            "ask_user",
            "query_slots",
            "request_confirmation",
            "finish",
        ),
    )


def strong_decision(_request: DecisionRequest) -> AppointmentDecision:
    return AppointmentDecision(
        action="ask_user",
        slots=AppointmentSlots(product_ref="洗衣机"),
        missing_slots=("region", "date_range"),
    )


def valid_local_json() -> str:
    return AppointmentDecision(
        action="query_slots",
        slots=AppointmentSlots(
            product_ref="洗衣机",
            service_type="repair",
            region="杭州市西湖区",
            date_range="2026-09-21/2026-09-22",
        ),
        missing_slots=(),
    ).model_dump_json()


def assert_safe_trace(outcome) -> None:
    trace = outcome.trace.model_dump()
    assert set(trace) == {
        "mode",
        "source",
        "latency_ms",
        "validation_status",
        "fallback_reason",
    }
    serialized = outcome.trace.model_dump_json()
    for sensitive in (
        "周末想约洗衣机维修",
        "杭州市西湖区",
        "洗衣机",
        "prompt",
        "authorization",
        "credential",
    ):
        assert sensitive not in serialized.lower()


def test_disabled_never_calls_local_client(
    decision_request: DecisionRequest,
) -> None:
    client = FakeClient(valid_local_json())
    outcome = AppointmentDecisionGateway("disabled", client).decide(
        decision_request, strong_decision
    )

    assert client.calls == 0
    assert outcome.decision == strong_decision(decision_request)
    assert outcome.source == "strong_model"
    assert outcome.trace.validation_status == "not_run"
    assert_safe_trace(outcome)


def test_shadow_returns_fallback_even_when_local_is_valid(
    decision_request: DecisionRequest,
) -> None:
    client = FakeClient(valid_local_json())
    outcome = AppointmentDecisionGateway("shadow", client).decide(
        decision_request, strong_decision
    )

    assert client.calls == 1
    assert outcome.decision == strong_decision(decision_request)
    assert outcome.source == "strong_model"
    assert outcome.shadow_decision is not None
    assert outcome.shadow_decision.action == "query_slots"
    assert outcome.trace.validation_status == "passed"
    assert_safe_trace(outcome)


def test_local_first_returns_valid_local_decision(
    decision_request: DecisionRequest,
) -> None:
    outcome = AppointmentDecisionGateway(
        "local_first", FakeClient(valid_local_json())
    ).decide(decision_request, strong_decision)

    assert outcome.source == "local_model"
    assert outcome.decision.action == "query_slots"
    assert outcome.trace.fallback_reason is None
    assert outcome.trace.validation_status == "passed"
    assert_safe_trace(outcome)


@pytest.mark.parametrize(
    ("client", "reason"),
    [
        (FakeClient(error=TimeoutError()), "timeout"),
        (FakeClient("not-json"), "invalid_json"),
        (
            FakeClient(
                AppointmentDecision(
                    action="ask_user",
                    slots=AppointmentSlots(order_id="JP-OTHER"),
                    missing_slots=("region",),
                ).model_dump_json()
            ),
            "confirmed_slot_conflict",
        ),
    ],
)
def test_local_first_falls_back_on_invalid_local_result(
    decision_request: DecisionRequest,
    client: FakeClient,
    reason: str,
) -> None:
    fallback_calls = 0

    def fallback(value: DecisionRequest) -> AppointmentDecision:
        nonlocal fallback_calls
        fallback_calls += 1
        return strong_decision(value)

    outcome = AppointmentDecisionGateway("local_first", client).decide(
        decision_request.model_copy(
            update={
                "confirmed_slots": AppointmentSlots(
                    order_id="JP20260919001"
                )
            }
        ),
        fallback,
    )

    assert fallback_calls == 1
    assert outcome.source == "strong_model_fallback"
    assert outcome.trace.fallback_reason == reason
    assert outcome.trace.validation_status == "failed"
    assert_safe_trace(outcome)


def test_only_one_complete_code_fence_repair_is_allowed(
    decision_request: DecisionRequest,
) -> None:
    fenced = "```json\n" + valid_local_json() + "\n```"
    repaired = AppointmentDecisionGateway(
        "local_first", FakeClient(fenced)
    ).decide(decision_request, strong_decision)
    prose = AppointmentDecisionGateway(
        "local_first", FakeClient("result: " + valid_local_json())
    ).decide(decision_request, strong_decision)

    assert repaired.source == "local_model"
    assert prose.source == "strong_model_fallback"
    assert prose.trace.fallback_reason == "invalid_json"
    assert_safe_trace(repaired)
    assert_safe_trace(prose)


def test_local_action_must_be_allowed_by_request(
    decision_request: DecisionRequest,
) -> None:
    restricted = decision_request.model_copy(
        update={"allowed_actions": ("ask_user",)}
    )
    outcome = AppointmentDecisionGateway(
        "local_first", FakeClient(valid_local_json())
    ).decide(restricted, strong_decision)

    assert outcome.source == "strong_model_fallback"
    assert outcome.trace.fallback_reason == "action_not_allowed"
    assert_safe_trace(outcome)


def test_shadow_timeout_preserves_strong_result(
    decision_request: DecisionRequest,
) -> None:
    outcome = AppointmentDecisionGateway(
        "shadow", FakeClient(error=TimeoutError())
    ).decide(decision_request, strong_decision)

    assert outcome.decision == strong_decision(decision_request)
    assert outcome.source == "strong_model"
    assert outcome.shadow_decision is None
    assert outcome.trace.fallback_reason == "timeout"
    assert_safe_trace(outcome)
