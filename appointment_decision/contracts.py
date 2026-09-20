"""Immutable contracts for appointment slot extraction and action selection."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


AppointmentAction = Literal[
    "ask_user",
    "query_slots",
    "request_confirmation",
    "finish",
]
MissingSlot = Literal[
    "order_id",
    "product_ref",
    "service_type",
    "issue_type",
    "region",
    "date_range",
    "slot_id",
]


class AppointmentSlots(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: str | None = None
    product_ref: str | None = None
    service_type: Literal["install", "inspect", "repair"] | None = None
    issue_type: str | None = None
    region: str | None = None
    date_range: str | None = None
    slot_id: str | None = None
    confirmation: bool = False


class AppointmentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: AppointmentAction
    slots: AppointmentSlots
    missing_slots: tuple[MissingSlot, ...] = ()

    @field_validator("missing_slots")
    @classmethod
    def reject_duplicate_missing_slots(
        cls, value: tuple[MissingSlot, ...]
    ) -> tuple[MissingSlot, ...]:
        if len(set(value)) != len(value):
            raise ValueError("missing_slots contains duplicates")
        return value


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str = Field(min_length=1, max_length=2000)
    recent_appointment_history: tuple[str, ...] = ()
    confirmed_slots: AppointmentSlots = AppointmentSlots()
    current_time: datetime
    allowed_actions: tuple[AppointmentAction, ...] = Field(min_length=1)

    @field_validator("allowed_actions")
    @classmethod
    def reject_duplicate_actions(
        cls, value: tuple[AppointmentAction, ...]
    ) -> tuple[AppointmentAction, ...]:
        if len(set(value)) != len(value):
            raise ValueError("allowed_actions contains duplicates")
        return value
