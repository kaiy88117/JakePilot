"""Tenant-scoped persistence for human handoff tickets."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from db.base.session_manager import SessionManager
from db.models import HumanHandoffTicket


class HandoffRepository:
    def __init__(self, session_manager: SessionManager) -> None:
        self.session_manager = session_manager

    def create_or_get(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        turn_id: str,
        reason_code: str,
        summary: str,
        verified_facts: list[str],
        evidence_refs: list[str],
        failed_steps: list[str],
    ) -> dict:
        try:
            with self.session_manager.session_scope() as session:
                existing = self._find(session, tenant_id, turn_id)
                if existing is not None:
                    return self._to_dict(existing)

                ticket = HumanHandoffTicket(
                    ticket_no=f"HO-{uuid4().hex[:12].upper()}",
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    reason_code=reason_code,
                    summary=summary,
                    verified_facts_json=list(verified_facts),
                    evidence_refs_json=list(evidence_refs),
                    failed_steps_json=list(failed_steps),
                    status="open",
                )
                session.add(ticket)
                session.flush()
                return self._to_dict(ticket)
        except IntegrityError:
            with self.session_manager.session_scope() as session:
                existing = self._find(session, tenant_id, turn_id)
                if existing is None:
                    raise
                return self._to_dict(existing)

    def list_recent(self, tenant_id: str, limit: int = 20) -> list[dict]:
        with self.session_manager.session_scope() as session:
            tickets = (
                session.query(HumanHandoffTicket)
                .filter(HumanHandoffTicket.tenant_id == tenant_id)
                .order_by(HumanHandoffTicket.created_at.desc())
                .limit(limit)
                .all()
            )
            return [self._to_dict(ticket) for ticket in tickets]

    def count(self, tenant_id: str) -> int:
        with self.session_manager.session_scope() as session:
            return (
                session.query(HumanHandoffTicket)
                .filter(HumanHandoffTicket.tenant_id == tenant_id)
                .count()
            )

    @staticmethod
    def _find(session, tenant_id: str, turn_id: str):
        return (
            session.query(HumanHandoffTicket)
            .filter(
                HumanHandoffTicket.tenant_id == tenant_id,
                HumanHandoffTicket.turn_id == turn_id,
            )
            .first()
        )

    @staticmethod
    def _to_dict(ticket: HumanHandoffTicket) -> dict:
        return {
            "ticket_no": ticket.ticket_no,
            "tenant_id": ticket.tenant_id,
            "user_id": ticket.user_id,
            "session_id": ticket.session_id,
            "turn_id": ticket.turn_id,
            "reason_code": ticket.reason_code,
            "summary": ticket.summary,
            "verified_facts": list(ticket.verified_facts_json or []),
            "evidence_refs": list(ticket.evidence_refs_json or []),
            "failed_steps": list(ticket.failed_steps_json or []),
            "status": ticket.status,
            "created_at": ticket.created_at,
            "updated_at": ticket.updated_at,
        }
