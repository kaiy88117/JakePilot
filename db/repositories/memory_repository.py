"""Tenant-scoped persistence for layered Agent memory."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from db.base.session_manager import SessionManager
from db.models import MemoryEvent, UserProfileMemory, WorkingState


class MemoryRepository:
    def __init__(self, session_manager: SessionManager) -> None:
        self.session_manager = session_manager

    def save_working(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        active_intent: str,
        plan_state: str,
        slots: dict,
        pending_action: dict | None,
        expires_at: datetime,
        now: datetime,
    ) -> dict:
        with self.session_manager.session_scope() as session:
            row = (
                session.query(WorkingState)
                .filter(
                    WorkingState.tenant_id == tenant_id,
                    WorkingState.user_id == user_id,
                    WorkingState.session_id == session_id,
                )
                .first()
            )
            if row is None:
                row = WorkingState(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=session_id,
                    version=1,
                )
                session.add(row)
            else:
                row.version += 1
            row.active_intent = active_intent
            row.plan_state = plan_state
            row.slots_json = dict(slots)
            row.pending_action_json = (
                dict(pending_action) if pending_action is not None else None
            )
            row.expires_at = expires_at
            row.updated_at = now
            session.flush()
            return self._working_dict(row)

    def get_working(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        *,
        now: datetime,
    ) -> dict | None:
        with self.session_manager.session_scope() as session:
            row = (
                session.query(WorkingState)
                .filter(
                    WorkingState.tenant_id == tenant_id,
                    WorkingState.user_id == user_id,
                    WorkingState.session_id == session_id,
                    WorkingState.expires_at > now,
                )
                .first()
            )
            return self._working_dict(row) if row is not None else None

    def clear_working(
        self, tenant_id: str, user_id: str, session_id: str
    ) -> bool:
        with self.session_manager.session_scope() as session:
            count = (
                session.query(WorkingState)
                .filter(
                    WorkingState.tenant_id == tenant_id,
                    WorkingState.user_id == user_id,
                    WorkingState.session_id == session_id,
                )
                .delete(synchronize_session=False)
            )
            return bool(count)

    def record_event(
        self,
        *,
        tenant_id: str,
        user_id: str,
        event_type: str,
        summary: str,
        outcome: str,
        entity_refs: list[str],
        source_trace_id: str,
        occurred_at: datetime,
        expires_at: datetime | None,
    ) -> dict:
        with self.session_manager.session_scope() as session:
            row = MemoryEvent(
                event_id=f"mem_evt_{uuid4().hex}",
                tenant_id=tenant_id,
                user_id=user_id,
                event_type=event_type,
                entity_refs_json=list(dict.fromkeys(entity_refs)),
                summary=summary,
                outcome=outcome,
                source_trace_id=source_trace_id,
                occurred_at=occurred_at,
                expires_at=expires_at,
            )
            session.add(row)
            session.flush()
            return self._event_dict(row)

    def recall_events(
        self,
        tenant_id: str,
        user_id: str,
        *,
        entity_refs: list[str],
        event_type: str | None,
        limit: int,
        now: datetime,
    ) -> list[dict]:
        with self.session_manager.session_scope() as session:
            query = session.query(MemoryEvent).filter(
                MemoryEvent.tenant_id == tenant_id,
                MemoryEvent.user_id == user_id,
            )
            if event_type:
                query = query.filter(MemoryEvent.event_type == event_type)
            rows = [
                row
                for row in query.all()
                if row.expires_at is None or row.expires_at > now
            ]
            requested = set(entity_refs)
            rows.sort(
                key=lambda row: (
                    len(requested.intersection(row.entity_refs_json or [])),
                    row.occurred_at,
                ),
                reverse=True,
            )
            return [self._event_dict(row) for row in rows[:limit]]

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
        valid_from: datetime,
        valid_until: datetime | None,
    ) -> dict:
        with self.session_manager.session_scope() as session:
            memory_id = f"profile_{uuid4().hex}"
            current = (
                session.query(UserProfileMemory)
                .filter(
                    UserProfileMemory.tenant_id == tenant_id,
                    UserProfileMemory.user_id == user_id,
                    UserProfileMemory.memory_key == memory_key,
                    UserProfileMemory.superseded_by.is_(None),
                )
                .all()
            )
            for old in current:
                old.valid_until = valid_from
                old.superseded_by = memory_id
            row = UserProfileMemory(
                memory_id=memory_id,
                tenant_id=tenant_id,
                user_id=user_id,
                memory_key=memory_key,
                memory_value=memory_value,
                source_type=source_type,
                confidence=confidence,
                source_trace_id=source_trace_id,
                valid_from=valid_from,
                valid_until=valid_until,
            )
            session.add(row)
            session.flush()
            return self._profile_dict(row)

    def recall_profiles(
        self,
        tenant_id: str,
        user_id: str,
        *,
        keys: list[str] | None,
        limit: int,
        now: datetime,
    ) -> list[dict]:
        with self.session_manager.session_scope() as session:
            query = session.query(UserProfileMemory).filter(
                UserProfileMemory.tenant_id == tenant_id,
                UserProfileMemory.user_id == user_id,
                UserProfileMemory.superseded_by.is_(None),
            )
            if keys:
                query = query.filter(UserProfileMemory.memory_key.in_(keys))
            rows = [
                row
                for row in query.order_by(
                    UserProfileMemory.valid_from.desc()
                ).all()
                if row.valid_until is None or row.valid_until > now
            ]
            return [self._profile_dict(row) for row in rows[:limit]]

    @staticmethod
    def _working_dict(row: WorkingState) -> dict:
        return {
            "tenant_id": row.tenant_id,
            "user_id": row.user_id,
            "session_id": row.session_id,
            "active_intent": row.active_intent,
            "plan_state": row.plan_state,
            "slots": dict(row.slots_json or {}),
            "pending_action": (
                dict(row.pending_action_json)
                if row.pending_action_json is not None
                else None
            ),
            "version": row.version,
            "expires_at": row.expires_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _event_dict(row: MemoryEvent) -> dict:
        return {
            "event_id": row.event_id,
            "event_type": row.event_type,
            "entity_refs": list(row.entity_refs_json or []),
            "summary": row.summary,
            "outcome": row.outcome,
            "source_trace_id": row.source_trace_id,
            "occurred_at": row.occurred_at,
            "expires_at": row.expires_at,
        }

    @staticmethod
    def _profile_dict(row: UserProfileMemory) -> dict:
        return {
            "memory_id": row.memory_id,
            "memory_key": row.memory_key,
            "memory_value": row.memory_value,
            "source_type": row.source_type,
            "confidence": float(row.confidence),
            "source_trace_id": row.source_trace_id,
            "valid_from": row.valid_from,
            "valid_until": row.valid_until,
            "superseded_by": row.superseded_by,
        }
