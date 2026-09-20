"""Privacy-minimized traces for the appointment decision boundary."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


DecisionMode = Literal["disabled", "shadow", "local_first"]
DecisionSource = Literal[
    "strong_model",
    "local_model",
    "strong_model_fallback",
]
ValidationStatus = Literal["not_run", "passed", "failed"]


class DecisionTrace(BaseModel):
    """Safe operational metadata; deliberately excludes request contents."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: DecisionMode
    source: DecisionSource
    latency_ms: float = Field(ge=0)
    validation_status: ValidationStatus
    fallback_reason: str | None = None
