"""Validated, domain-neutral contracts for one Agent turn."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class TurnStatus(StrEnum):
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    HANDED_OFF = "handed_off"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TurnRequest(BaseModel):
    turn_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("turn_id", "session_id", "user_id", "tenant_id", "message")
    @classmethod
    def reject_blank_values(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped


class ExecutionBudget(BaseModel):
    max_steps: int = Field(default=6, ge=1, le=6)
    max_tool_calls: int = Field(default=8, ge=1, le=8)
    max_replans: int = Field(default=2, ge=0, le=2)


class RuntimeEvent(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    data: dict[str, Any] = Field(default_factory=dict)


class TurnOutcome(BaseModel):
    status: TurnStatus
    answer: str
    trace_id: str = Field(min_length=1, max_length=128)
    tool_calls: int = Field(default=0, ge=0)
    steps: int = Field(default=0, ge=0)
