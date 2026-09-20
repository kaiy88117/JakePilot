"""Safe lifecycle journal for streamed Agent turns."""

from __future__ import annotations

from db.base.session_manager import SessionManager
from db.repositories.turn_checkpoint_repository import TurnCheckpointRepository


_TERMINAL_EVENT_STATUS = {
    "confirmation_required": "needs_input",
    "input_required": "needs_input",
    "turn_failed": "failed",
}


class TurnJournal:
    def __init__(self, database_url: str) -> None:
        self.session_manager = SessionManager(database_url)
        self.repository = TurnCheckpointRepository(self.session_manager)

    def begin(
        self,
        *,
        turn_id: str,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> dict:
        return self.repository.begin(
            turn_id=turn_id,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
        )

    def observe(self, turn_id: str, event_type: str, payload: dict) -> dict | None:
        status = _TERMINAL_EVENT_STATUS.get(event_type)
        if event_type == "turn_started":
            status = "running"
        elif event_type == "turn_ended":
            candidate = payload.get("status")
            status = candidate if isinstance(candidate, str) else "completed"
        write_succeeded = (
            event_type == "tool_finished"
            and payload.get("tool") == "return.create"
            and payload.get("status") == "succeeded"
        )
        return self.repository.observe(
            turn_id,
            event_type=event_type,
            status=status,
            business_write_succeeded=write_succeeded,
        )

    def mark_delivered(self, turn_id: str) -> dict | None:
        return self.repository.mark_delivery(
            turn_id, delivery_status="delivered"
        )

    def mark_disconnected(self, turn_id: str) -> dict | None:
        return self.repository.mark_delivery(
            turn_id,
            delivery_status="disconnected",
            status="cancelled",
        )

    def get(self, turn_id: str, session_id: str) -> dict | None:
        return self.repository.get(turn_id, session_id)

    def close(self) -> None:
        self.session_manager.close()
