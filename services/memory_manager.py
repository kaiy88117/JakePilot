"""Deterministic three-layer business memory manager."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from db.base.session_manager import SessionManager
from db.repositories.memory_repository import MemoryRepository


_SENSITIVE_PROFILE_FIELDS = {
    "phone_number",
    "real_name",
    "bank_card",
    "id_card",
    "full_address",
    "payment_proof",
}


def _utc_naive(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


class MemoryManager:
    def __init__(self, database_url: str) -> None:
        self.session_manager = SessionManager(database_url)
        self.repository = MemoryRepository(self.session_manager)

    def save_working(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        active_intent: str,
        plan_state: str,
        slots: dict,
        pending_action: dict | None = None,
        ttl_minutes: int = 30,
        now: datetime | None = None,
    ) -> dict:
        if ttl_minutes < 1:
            raise ValueError("ttl_minutes must be positive")
        current = _utc_naive(now)
        return self.repository.save_working(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            active_intent=active_intent,
            plan_state=plan_state,
            slots=slots,
            pending_action=pending_action,
            expires_at=current + timedelta(minutes=ttl_minutes),
            now=current,
        )

    def get_working(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        *,
        now: datetime | None = None,
    ) -> dict | None:
        return self.repository.get_working(
            tenant_id,
            user_id,
            session_id,
            now=_utc_naive(now),
        )

    def clear_working(
        self, tenant_id: str, user_id: str, session_id: str
    ) -> bool:
        return self.repository.clear_working(tenant_id, user_id, session_id)

    def record_event(
        self,
        *,
        tenant_id: str,
        user_id: str,
        event_type: str,
        summary: str,
        outcome: str,
        entity_refs: list[str] | None = None,
        source_trace_id: str,
        occurred_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> dict:
        return self.repository.record_event(
            tenant_id=tenant_id,
            user_id=user_id,
            event_type=event_type,
            summary=summary,
            outcome=outcome,
            entity_refs=entity_refs or [],
            source_trace_id=source_trace_id,
            occurred_at=_utc_naive(occurred_at),
            expires_at=_utc_naive(expires_at) if expires_at else None,
        )

    def record_event_once(
        self,
        *,
        tenant_id: str,
        user_id: str,
        event_type: str,
        candidate_key: str,
        summary: str,
        outcome: str,
        entity_refs: list[str] | None = None,
        source_trace_id: str,
        occurred_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> dict:
        material = "\x1f".join(
            [tenant_id, user_id, source_trace_id, event_type, candidate_key]
        )
        event_id = (
            "mem_evt_"
            + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
        )
        return self.repository.record_event_once(
            event_id=event_id,
            tenant_id=tenant_id,
            user_id=user_id,
            event_type=event_type,
            summary=summary,
            outcome=outcome,
            entity_refs=entity_refs or [],
            source_trace_id=source_trace_id,
            occurred_at=_utc_naive(occurred_at),
            expires_at=_utc_naive(expires_at) if expires_at else None,
        )

    def recall_events(
        self,
        tenant_id: str,
        user_id: str,
        *,
        entity_refs: list[str] | None = None,
        event_type: str | None = None,
        limit: int = 3,
        now: datetime | None = None,
    ) -> list[dict]:
        return self.repository.recall_events(
            tenant_id,
            user_id,
            entity_refs=entity_refs or [],
            event_type=event_type,
            limit=max(0, min(limit, 3)),
            now=_utc_naive(now),
        )

    def set_profile(
        self,
        *,
        tenant_id: str,
        user_id: str,
        memory_key: str,
        memory_value,
        source_type: str,
        confidence: float,
        source_trace_id: str,
        now: datetime | None = None,
        valid_until: datetime | None = None,
    ) -> dict:
        if memory_key in _SENSITIVE_PROFILE_FIELDS:
            raise ValueError("sensitive profile field is not allowed")
        if source_type not in {"explicit", "inferred"}:
            raise ValueError("source_type must be explicit or inferred")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return self.repository.set_profile(
            tenant_id=tenant_id,
            user_id=user_id,
            memory_key=memory_key,
            memory_value=memory_value,
            source_type=source_type,
            confidence=confidence,
            source_trace_id=source_trace_id,
            valid_from=_utc_naive(now),
            valid_until=_utc_naive(valid_until) if valid_until else None,
        )

    def set_profile_once(
        self,
        *,
        tenant_id: str,
        user_id: str,
        memory_key: str,
        memory_value,
        candidate_key: str,
        source_type: str,
        confidence: float,
        source_trace_id: str,
        now: datetime | None = None,
        valid_until: datetime | None = None,
    ) -> dict:
        if memory_key in _SENSITIVE_PROFILE_FIELDS:
            raise ValueError("sensitive profile field is not allowed")
        if source_type not in {"explicit", "inferred"}:
            raise ValueError("source_type must be explicit or inferred")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        material = "\x1f".join(
            [tenant_id, user_id, source_trace_id, memory_key, candidate_key]
        )
        memory_id = (
            "profile_"
            + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
        )
        return self.repository.set_profile_once(
            memory_id=memory_id,
            tenant_id=tenant_id,
            user_id=user_id,
            memory_key=memory_key,
            memory_value=memory_value,
            source_type=source_type,
            confidence=confidence,
            source_trace_id=source_trace_id,
            valid_from=_utc_naive(now),
            valid_until=_utc_naive(valid_until) if valid_until else None,
        )

    def recall_profiles(
        self,
        tenant_id: str,
        user_id: str,
        *,
        keys: list[str] | None = None,
        limit: int = 3,
        now: datetime | None = None,
    ) -> list[dict]:
        return self.repository.recall_profiles(
            tenant_id,
            user_id,
            keys=keys,
            limit=max(0, min(limit, 3)),
            now=_utc_naive(now),
        )

    def close(self) -> None:
        self.session_manager.close()
