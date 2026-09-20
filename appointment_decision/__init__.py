"""Structured decision boundary for the service appointment domain."""

from .contracts import AppointmentDecision, AppointmentSlots, DecisionRequest
from .gateway import AppointmentDecisionGateway, DecisionOutcome
from .trace import DecisionTrace
from .validator import DecisionValidationError, validate_decision

__all__ = [
    "AppointmentDecision",
    "AppointmentDecisionGateway",
    "AppointmentSlots",
    "DecisionRequest",
    "DecisionOutcome",
    "DecisionTrace",
    "DecisionValidationError",
    "validate_decision",
]
