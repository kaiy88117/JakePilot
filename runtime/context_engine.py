"""Deterministic, budgeted context projection for domain agents."""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from services.memory_manager import MemoryManager


class ContextSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal[
        "current_request", "task_state", "episodic_memory", "profile_memory"
    ]
    source: str
    trust_level: str
    content: dict[str, Any]
    token_estimate: int
    inclusion_reason: str


class ContextProjection(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_agent: str
    segments: tuple[ContextSegment, ...]
    working_loaded: bool
    episodic_count: int
    profile_count: int
    estimated_tokens: int
    dropped_count: int


_PROFILE_KEYS = {
    "service_appointment": [
        "service_time_preference",
        "communication_language",
        "address_region_ref",
    ],
    "order_after_sales": ["communication_language"],
    "knowledge_consultation": [
        "product_category_preference",
        "communication_language",
    ],
}


def _estimate_tokens(content: dict[str, Any]) -> int:
    serialized = json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return max(1, math.ceil(len(serialized) / 4))


class ContextEngine:
    def __init__(self, memory_manager: MemoryManager) -> None:
        self.memory_manager = memory_manager

    def build(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        current_request: str,
        target_agent: str,
        entity_refs: list[str] | None = None,
        token_budget: int = 1200,
        now: datetime | None = None,
    ) -> ContextProjection:
        required = [
            self._segment(
                kind="current_request",
                source="current_turn",
                trust_level="user_input",
                content={"message": current_request},
                reason="当前用户请求不可裁剪",
            )
        ]

        working = self.memory_manager.get_working(
            tenant_id, user_id, session_id, now=now
        )
        if working is not None:
            required.append(
                self._segment(
                    kind="task_state",
                    source=f"working:{session_id}",
                    trust_level="session_state",
                    content={
                        "active_intent": working["active_intent"],
                        "plan_state": working["plan_state"],
                        "slots": working["slots"],
                        "pending_action": working["pending_action"],
                        "version": working["version"],
                    },
                    reason="恢复当前会话任务状态",
                )
            )

        events = self.memory_manager.recall_events(
            tenant_id,
            user_id,
            entity_refs=entity_refs or [],
            limit=3,
            now=now,
        )
        profile_keys = _PROFILE_KEYS.get(target_agent)
        profiles = (
            self.memory_manager.recall_profiles(
                tenant_id,
                user_id,
                keys=profile_keys,
                limit=3,
                now=now,
            )
            if profile_keys is not None
            else []
        )

        optional = [
            self._segment(
                kind="episodic_memory",
                source=f"event:{item['event_id']}",
                trust_level="historical_event",
                content={
                    "event_type": item["event_type"],
                    "entity_refs": item["entity_refs"],
                    "summary": item["summary"],
                    "outcome": item["outcome"],
                    "source_trace_id": item["source_trace_id"],
                },
                reason=(
                    "与当前业务实体相关的历史事件"
                    if set(entity_refs or []).intersection(item["entity_refs"])
                    else "近期同用户业务事件"
                ),
            )
            for item in events
        ]
        optional.extend(
            self._segment(
                kind="profile_memory",
                source=f"profile:{item['memory_id']}",
                trust_level=(
                    "user_confirmed"
                    if item["source_type"] == "explicit"
                    else "inferred_preference"
                ),
                content={
                    "memory_key": item["memory_key"],
                    "memory_value": item["memory_value"],
                    "source_type": item["source_type"],
                    "confidence": item["confidence"],
                    "source_trace_id": item["source_trace_id"],
                },
                reason="与目标领域 Agent 相关的当前偏好",
            )
            for item in profiles
        )

        segments = list(required)
        used = sum(segment.token_estimate for segment in segments)
        budget = max(0, token_budget)
        dropped = 0
        for segment in optional:
            if used + segment.token_estimate <= budget:
                segments.append(segment)
                used += segment.token_estimate
            else:
                dropped += 1

        episodic_count = sum(
            segment.kind == "episodic_memory" for segment in segments
        )
        profile_count = sum(
            segment.kind == "profile_memory" for segment in segments
        )
        return ContextProjection(
            target_agent=target_agent,
            segments=tuple(segments),
            working_loaded=working is not None,
            episodic_count=episodic_count,
            profile_count=profile_count,
            estimated_tokens=used,
            dropped_count=dropped,
        )

    @staticmethod
    def _segment(
        *,
        kind: str,
        source: str,
        trust_level: str,
        content: dict[str, Any],
        reason: str,
    ) -> ContextSegment:
        return ContextSegment(
            kind=kind,
            source=source,
            trust_level=trust_level,
            content=content,
            token_estimate=_estimate_tokens(content),
            inclusion_reason=reason,
        )
