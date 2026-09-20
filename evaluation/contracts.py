"""Strict data contracts for deterministic Agent evaluation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EvalCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1, max_length=128)
    dataset_version: str = Field(pattern=r"^[a-z0-9._-]+-smoke-v\d+$")
    category: str = Field(min_length=1, max_length=64)
    turns: tuple[str, ...] = Field(min_length=1)
    expected_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    expected_terminal_status: Literal[
        "completed", "needs_input", "failed", "cancelled"
    ] = "completed"
    answer_contains: tuple[str, ...] = ()
    expected_write_count: int = Field(default=0, ge=0)
    requires_confirmation: bool = False
    recreate_between_turns: bool = False
    max_steps: int = Field(default=6, ge=1, le=6)

    @field_validator("turns", "expected_tools", "forbidden_tools", "answer_contains")
    @classmethod
    def reject_duplicates_or_blanks(cls, value: tuple[str, ...], info):
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError(f"{info.field_name} contains a blank value")
        if (
            info.field_name in {"forbidden_tools", "answer_contains"}
            and len(set(normalized)) != len(normalized)
        ):
            raise ValueError(f"{info.field_name} contains duplicate values")
        return normalized


class EvalTraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str = Field(min_length=1, max_length=64)
    tool: str | None = None
    status: str | None = None
    step: int | None = Field(default=None, ge=0)


class EvalObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    terminal_status: str
    answers: tuple[str, ...] = ()
    write_count: int = Field(ge=0)
    events: tuple[EvalTraceEvent, ...] = ()


class EvalResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    passed: bool
    metrics: dict[str, bool]
    failed_assertions: tuple[str, ...] = ()
