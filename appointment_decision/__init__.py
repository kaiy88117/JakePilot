"""Structured decision boundary for the service appointment domain."""

from .contracts import AppointmentDecision, AppointmentSlots, DecisionRequest
from .validator import DecisionValidationError, validate_decision

__all__ = [
    "AppointmentDecision",
    "AppointmentSlots",
    "DecisionRequest",
    "DecisionValidationError",
    "validate_decision",
]
