"""Mode-aware local decision gateway with deterministic fallback."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Protocol

import requests
from pydantic import BaseModel, ConfigDict, ValidationError

from .contracts import AppointmentDecision, DecisionRequest
from .trace import DecisionMode, DecisionSource, DecisionTrace
from .validator import DecisionValidationError, validate_decision


class DecisionClient(Protocol):
    def complete(self, request: DecisionRequest) -> str: ...


class DecisionOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: AppointmentDecision
    source: DecisionSource
    trace: DecisionTrace
    shadow_decision: AppointmentDecision | None = None


class _ActionNotAllowed(ValueError):
    pass


class AppointmentDecisionGateway:
    def __init__(self, mode: DecisionMode, client: DecisionClient) -> None:
        if mode not in {"disabled", "shadow", "local_first"}:
            raise ValueError(f"unsupported appointment decision mode: {mode}")
        self.mode = mode
        self.client = client

    def decide(
        self,
        request: DecisionRequest,
        fallback: Callable[[DecisionRequest], AppointmentDecision],
    ) -> DecisionOutcome:
        if self.mode == "disabled":
            decision = fallback(request)
            return self._outcome(
                decision=decision,
                source="strong_model",
                latency_ms=0.0,
                validation_status="not_run",
            )

        if self.mode == "shadow":
            decision = fallback(request)
            local, latency_ms, reason = self._try_local(request)
            return self._outcome(
                decision=decision,
                source="strong_model",
                latency_ms=latency_ms,
                validation_status="passed" if local is not None else "failed",
                fallback_reason=reason,
                shadow_decision=local,
            )

        local, latency_ms, reason = self._try_local(request)
        if local is not None:
            return self._outcome(
                decision=local,
                source="local_model",
                latency_ms=latency_ms,
                validation_status="passed",
            )

        decision = fallback(request)
        return self._outcome(
            decision=decision,
            source="strong_model_fallback",
            latency_ms=latency_ms,
            validation_status="failed",
            fallback_reason=reason,
        )

    def _try_local(
        self, request: DecisionRequest
    ) -> tuple[AppointmentDecision | None, float, str | None]:
        started = time.perf_counter()
        try:
            raw = self.client.complete(request)
            repaired = _strip_one_complete_code_fence(raw)
            decision = AppointmentDecision.model_validate_json(repaired)
            if decision.action not in request.allowed_actions:
                raise _ActionNotAllowed(decision.action)
            validate_decision(decision, request.confirmed_slots)
        except (TimeoutError, requests.Timeout):
            return None, _elapsed_ms(started), "timeout"
        except _ActionNotAllowed:
            return None, _elapsed_ms(started), "action_not_allowed"
        except DecisionValidationError as exc:
            reason = (
                "confirmed_slot_conflict"
                if "confirmed slot conflict" in str(exc)
                else "business_precondition"
            )
            return None, _elapsed_ms(started), reason
        except ValidationError as exc:
            reason = (
                "invalid_json"
                if any(error["type"] == "json_invalid" for error in exc.errors())
                else "schema_validation"
            )
            return None, _elapsed_ms(started), reason
        except (KeyError, TypeError, ValueError, requests.RequestException):
            return None, _elapsed_ms(started), "local_error"
        return decision, _elapsed_ms(started), None

    def _outcome(
        self,
        *,
        decision: AppointmentDecision,
        source: DecisionSource,
        latency_ms: float,
        validation_status: str,
        fallback_reason: str | None = None,
        shadow_decision: AppointmentDecision | None = None,
    ) -> DecisionOutcome:
        trace = DecisionTrace(
            mode=self.mode,
            source=source,
            latency_ms=latency_ms,
            validation_status=validation_status,
            fallback_reason=fallback_reason,
        )
        return DecisionOutcome(
            decision=decision,
            source=source,
            trace=trace,
            shadow_decision=shadow_decision,
        )


def _strip_one_complete_code_fence(raw: str) -> str:
    stripped = raw.strip()
    match = re.fullmatch(
        r"```(?:json)?\s*\n?(.*?)\n?```",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1).strip() if match else raw


def _elapsed_ms(started: float) -> float:
    return max(0.0, (time.perf_counter() - started) * 1000)
