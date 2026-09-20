"""Validated application service for privacy-minimized human handoffs."""

from __future__ import annotations

import re
from typing import Iterable

from db.base.session_manager import SessionManager
from db.repositories.handoff_repository import HandoffRepository


_ALLOWED_REASON_CODES = frozenset(
    {
        "user_requested",
        "evidence_insufficient",
        "risk_threshold",
        "tool_failure",
        "intent_unstable",
    }
)
_PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
)


class HandoffService:
    def __init__(self, database_url: str) -> None:
        self.session_manager = SessionManager(database_url)
        self.repository = HandoffRepository(self.session_manager)

    def create_or_get(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        turn_id: str,
        reason_code: str,
        summary: str,
        verified_facts: Iterable[str],
        evidence_refs: Iterable[str],
        failed_steps: Iterable[str],
    ) -> dict:
        identity = {
            "tenant_id": self._bounded_text("tenant_id", tenant_id, 128),
            "user_id": self._bounded_text("user_id", user_id, 128),
            "session_id": self._bounded_text("session_id", session_id, 128),
            "turn_id": self._bounded_text("turn_id", turn_id, 128),
        }
        if reason_code not in _ALLOWED_REASON_CODES:
            raise ValueError("reason_code is not allowed")
        clean_summary = self._bounded_text("summary", summary, 240)
        if _PHONE_PATTERN.search(clean_summary) or _EMAIL_PATTERN.search(clean_summary):
            raise ValueError("summary contains sensitive contact data")

        return self.repository.create_or_get(
            **identity,
            reason_code=reason_code,
            summary=clean_summary,
            verified_facts=self._bounded_list("verified_facts", verified_facts),
            evidence_refs=self._bounded_list("evidence_refs", evidence_refs),
            failed_steps=self._bounded_list("failed_steps", failed_steps),
        )

    def list_recent(self, tenant_id: str, limit: int = 20) -> list[dict]:
        clean_tenant = self._bounded_text("tenant_id", tenant_id, 128)
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return self.repository.list_recent(clean_tenant, limit)

    def count(self, tenant_id: str) -> int:
        return self.repository.count(
            self._bounded_text("tenant_id", tenant_id, 128)
        )

    def close(self) -> None:
        self.session_manager.close()

    @staticmethod
    def _bounded_text(name: str, value: str, maximum: int) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be text")
        cleaned = value.strip()
        if not cleaned or len(cleaned) > maximum:
            raise ValueError(f"{name} length is invalid")
        return cleaned

    @classmethod
    def _bounded_list(cls, name: str, values: Iterable[str]) -> list[str]:
        if isinstance(values, (str, bytes)):
            raise ValueError(f"{name} must be a list of strings")
        items = list(values)
        if len(items) > 20:
            raise ValueError(f"{name} has too many items")
        return [cls._bounded_text(name, item, 160) for item in items]
