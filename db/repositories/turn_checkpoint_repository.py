"""Persistence for privacy-minimized Turn lifecycle checkpoints."""

from __future__ import annotations

from db.base.session_manager import SessionManager
from db.models import TurnCheckpoint


class TurnCheckpointRepository:
    def __init__(self, session_manager: SessionManager) -> None:
        self.session_manager = session_manager

    def begin(
        self,
        *,
        turn_id: str,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> dict:
        with self.session_manager.session_scope() as session:
            row = TurnCheckpoint(
                turn_id=turn_id,
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                status="started",
                last_event_type="turn_started",
                delivery_status="pending",
                business_write_succeeded=0,
                event_count=0,
            )
            session.add(row)
            session.flush()
            return self._public_dict(row)

    def observe(
        self,
        turn_id: str,
        *,
        event_type: str,
        status: str | None,
        business_write_succeeded: bool,
    ) -> dict | None:
        with self.session_manager.session_scope() as session:
            row = self._find(session, turn_id)
            if row is None:
                return None
            row.last_event_type = event_type
            row.event_count += 1
            if status is not None:
                row.status = status
            if business_write_succeeded:
                row.business_write_succeeded = 1
            session.flush()
            return self._public_dict(row)

    def mark_delivery(
        self, turn_id: str, *, delivery_status: str, status: str | None = None
    ) -> dict | None:
        with self.session_manager.session_scope() as session:
            row = self._find(session, turn_id)
            if row is None:
                return None
            row.delivery_status = delivery_status
            if status is not None:
                row.status = status
            session.flush()
            return self._public_dict(row)

    def get(self, turn_id: str, session_id: str) -> dict | None:
        with self.session_manager.session_scope() as session:
            row = (
                session.query(TurnCheckpoint)
                .filter(
                    TurnCheckpoint.turn_id == turn_id,
                    TurnCheckpoint.session_id == session_id,
                )
                .first()
            )
            return self._public_dict(row) if row is not None else None

    def list_recent(self, *, limit: int = 20) -> list[dict]:
        safe_limit = max(1, min(limit, 100))
        with self.session_manager.session_scope() as session:
            rows = (
                session.query(TurnCheckpoint)
                .order_by(TurnCheckpoint.id.desc())
                .limit(safe_limit)
                .all()
            )
            return [self._recent_dict(row) for row in rows]

    @staticmethod
    def _find(session, turn_id: str) -> TurnCheckpoint | None:
        return (
            session.query(TurnCheckpoint)
            .filter(TurnCheckpoint.turn_id == turn_id)
            .first()
        )

    @staticmethod
    def _public_dict(row: TurnCheckpoint) -> dict:
        return {
            "turn_id": row.turn_id,
            "status": row.status,
            "last_event_type": row.last_event_type,
            "delivery_status": row.delivery_status,
            "business_write_succeeded": bool(row.business_write_succeeded),
            "event_count": row.event_count,
        }

    @classmethod
    def _recent_dict(cls, row: TurnCheckpoint) -> dict:
        payload = cls._public_dict(row)
        payload["updated_at"] = row.updated_at.isoformat(timespec="seconds") + "Z"
        return payload
