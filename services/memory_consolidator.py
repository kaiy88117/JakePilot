"""Safe contracts and deterministic candidate extraction for long-term memory."""

from __future__ import annotations

import re
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


TurnCompletionStatus = Literal[
    "completed", "cancelled", "handed_off", "failed", "needs_input"
]
TurnRoute = Literal[
    "knowledge_consultation",
    "order_after_sales",
    "service_appointment",
    "human_handoff",
    "unsupported",
]


class TurnCompletion(BaseModel):
    """Privacy-bounded terminal projection consumed by consolidation."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    status: TurnCompletionStatus
    route: TurnRoute
    user_message: str = Field(max_length=2000)
    public_result: str = Field(max_length=2000)


class MemoryCandidate(BaseModel):
    """A minimal, validated proposal; it is not persisted by extraction."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["episodic", "profile"]
    candidate_key: str = Field(min_length=1, max_length=128)
    event_type: str | None = None
    summary: str | None = Field(default=None, min_length=1, max_length=240)
    outcome: str | None = None
    entity_refs: tuple[str, ...] = Field(default=(), max_length=8)
    memory_key: str | None = None
    memory_value: str | None = Field(default=None, min_length=1, max_length=80)
    source_type: Literal["explicit"] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_kind_fields(self):
        if self.kind == "episodic":
            required = (self.event_type, self.summary, self.outcome)
            if any(value is None for value in required):
                raise ValueError("episodic candidate is incomplete")
            if any(
                value is not None
                for value in (
                    self.memory_key,
                    self.memory_value,
                    self.source_type,
                    self.confidence,
                )
            ):
                raise ValueError("episodic candidate contains profile fields")
        else:
            required = (
                self.memory_key,
                self.memory_value,
                self.source_type,
                self.confidence,
            )
            if any(value is None for value in required):
                raise ValueError("profile candidate is incomplete")
            if any(
                value is not None
                for value in (self.event_type, self.summary, self.outcome)
            ) or self.entity_refs:
                raise ValueError("profile candidate contains episodic fields")
        return self


class MemoryCandidateExtractor(Protocol):
    def extract(self, completion: TurnCompletion) -> tuple[MemoryCandidate, ...]:
        """Return safe candidates without performing persistence."""


_ORDER_PATTERN = re.compile(r"\bJP\d{11}\b", re.IGNORECASE)
_RETURN_REFERENCE_PATTERN = re.compile(
    r"申请编号(?:为)?\s*([A-Za-z0-9_-]{2,64})"
)
_APPOINTMENT_REFERENCE_PATTERN = re.compile(
    r"(?:预约编号|预约ID)(?:为)?[:：\s]*([A-Za-z0-9_-]{2,64})",
    re.IGNORECASE,
)
_HANDOFF_REFERENCE_PATTERN = re.compile(r"工单号\s*(HO-[A-Za-z0-9-]{2,64})")
_STABLE_PREFERENCE_PATTERN = re.compile(r"(?:以后都?|今后|之后都)")
_TIME_PREFERENCE_VALUES = {
    "上午": "morning",
    "下午": "afternoon",
    "晚上": "evening",
    "周末": "weekend",
}


class RuleBasedMemoryCandidateExtractor:
    """Conservative offline extractor for a small set of auditable facts."""

    def extract(self, completion: TurnCompletion) -> tuple[MemoryCandidate, ...]:
        if completion.status in {"failed", "needs_input"}:
            return ()

        candidates: list[MemoryCandidate] = []
        episodic = self._episodic_candidate(completion)
        if episodic is not None:
            candidates.append(episodic)
        profile = self._profile_candidate(completion)
        if profile is not None:
            candidates.append(profile)
        return tuple(candidates)

    @staticmethod
    def _episodic_candidate(
        completion: TurnCompletion,
    ) -> MemoryCandidate | None:
        if completion.route == "order_after_sales":
            order_match = _ORDER_PATTERN.search(completion.user_message)
            request_match = _RETURN_REFERENCE_PATTERN.search(
                completion.public_result
            )
            if (
                completion.status == "completed"
                and order_match is not None
                and request_match is not None
                and "退货申请已提交" in completion.public_result
            ):
                order_id = order_match.group(0).upper()
                request_id = request_match.group(1)
                return MemoryCandidate(
                    kind="episodic",
                    candidate_key=f"return:{order_id}:{request_id}",
                    event_type="return_requested",
                    summary=(
                        f"订单 {order_id} 已提交退货申请，申请编号 {request_id}"
                    ),
                    outcome="completed",
                    entity_refs=(order_id,),
                )

        if completion.route == "service_appointment":
            appointment_match = _APPOINTMENT_REFERENCE_PATTERN.search(
                completion.public_result
            )
            if completion.status == "completed" and appointment_match:
                appointment_id = appointment_match.group(1)
                return MemoryCandidate(
                    kind="episodic",
                    candidate_key=f"appointment:{appointment_id}",
                    event_type="service_booked",
                    summary=f"已创建上门服务预约，预约编号 {appointment_id}",
                    outcome="completed",
                    entity_refs=(appointment_id,),
                )

        if completion.route == "human_handoff":
            handoff_match = _HANDOFF_REFERENCE_PATTERN.search(
                completion.public_result
            )
            if completion.status == "handed_off" and handoff_match:
                ticket_no = handoff_match.group(1)
                return MemoryCandidate(
                    kind="episodic",
                    candidate_key=f"handoff:{ticket_no}",
                    event_type="handoff_created",
                    summary=f"已创建人工接管工单 {ticket_no}",
                    outcome="handed_off",
                    entity_refs=(ticket_no,),
                )
        return None

    @staticmethod
    def _profile_candidate(
        completion: TurnCompletion,
    ) -> MemoryCandidate | None:
        if (
            completion.status != "completed"
            or not _STABLE_PREFERENCE_PATTERN.search(completion.user_message)
        ):
            return None
        if completion.route == "service_appointment":
            for expression, normalized in _TIME_PREFERENCE_VALUES.items():
                if expression in completion.user_message:
                    return MemoryCandidate(
                        kind="profile",
                        candidate_key="profile:service_time_preference",
                        memory_key="service_time_preference",
                        memory_value=normalized,
                        source_type="explicit",
                        confidence=1.0,
                    )
        if "用英文" in completion.user_message:
            return MemoryCandidate(
                kind="profile",
                candidate_key="profile:communication_language",
                memory_key="communication_language",
                memory_value="en",
                source_type="explicit",
                confidence=1.0,
            )
        if "用中文" in completion.user_message:
            return MemoryCandidate(
                kind="profile",
                candidate_key="profile:communication_language",
                memory_key="communication_language",
                memory_value="zh",
                source_type="explicit",
                confidence=1.0,
            )
        return None
