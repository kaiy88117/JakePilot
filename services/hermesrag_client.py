"""HTTP adapter for the independently deployed HermesRAG service."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any, Literal

import requests
from pydantic import BaseModel, ConfigDict, Field


class HermesRagError(RuntimeError):
    """A public-safe HermesRAG adapter failure."""


class KnowledgeCitation(BaseModel):
    citation_id: str
    filename: str
    source_label: str
    page_number: str | int | None = None


class KnowledgeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    answer: str
    citations: tuple[KnowledgeCitation, ...] = ()
    mode: str
    pipeline_status: str
    terminal_reason: str
    evidence_sufficiency: Literal[
        "sufficient", "partial", "insufficient", "not_evaluated"
    ]

    @property
    def citation_count(self) -> int:
        return len(self.citations)


Requester = Callable[..., Any]


class HermesRagClient:
    """Translate HermesRAG HTTP responses into JakePilot's stable contract."""

    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout_seconds: float = 45.0,
        requester: Requester = requests.request,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        self._username = username
        self._password = password
        self._timeout_seconds = timeout_seconds
        self._requester = requester

    @classmethod
    def from_env(cls) -> "HermesRagClient | None":
        enabled = os.getenv("HERMESRAG_ENABLED", "false").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return None
        base_url = os.getenv("HERMESRAG_BASE_URL", "").strip()
        if not base_url:
            return None
        try:
            timeout = float(os.getenv("HERMESRAG_TIMEOUT_SECONDS", "45"))
        except ValueError:
            timeout = 45.0
        return cls(
            base_url=base_url,
            bearer_token=os.getenv("HERMESRAG_BEARER_TOKEN") or None,
            username=os.getenv("HERMESRAG_USERNAME") or None,
            password=os.getenv("HERMESRAG_PASSWORD") or None,
            timeout_seconds=max(1.0, timeout),
        )

    async def query(
        self,
        message: str,
        session_id: str,
        mode: str = "auto",
    ) -> KnowledgeResult:
        if not message.strip() or not session_id.strip():
            raise HermesRagError("HermesRAG request is invalid")
        if mode not in {"standard", "self_corrective", "agentic", "auto"}:
            raise HermesRagError("HermesRAG mode is invalid")

        if not self._bearer_token and self._username and self._password:
            await self._login()

        response = await self._send_chat(message, session_id, mode)
        if response.status_code == 401 and self._username and self._password:
            await self._login()
            response = await self._send_chat(message, session_id, mode)
        if response.status_code < 200 or response.status_code >= 300:
            raise HermesRagError("HermesRAG request failed")

        payload = self._safe_json(response)
        return self._map_result(payload, requested_mode=mode)

    async def _login(self) -> None:
        response = await self._request(
            "POST",
            f"{self.base_url}/auth/login",
            json={"username": self._username, "password": self._password},
            headers={"Content-Type": "application/json"},
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise HermesRagError("HermesRAG authentication failed")
        payload = self._safe_json(response)
        token = payload.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise HermesRagError("HermesRAG authentication failed")
        self._bearer_token = token.strip()

    async def _send_chat(self, message: str, session_id: str, mode: str):
        headers = {"Content-Type": "application/json"}
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        return await self._request(
            "POST",
            f"{self.base_url}/chat",
            json={
                "message": message,
                "session_id": session_id,
                "rag_mode": mode,
            },
            headers=headers,
        )

    async def _request(self, method: str, url: str, **kwargs):
        try:
            return await asyncio.to_thread(
                self._requester,
                method,
                url,
                timeout=self._timeout_seconds,
                **kwargs,
            )
        except Exception as exc:
            raise HermesRagError("HermesRAG service is unavailable") from exc

    @staticmethod
    def _safe_json(response) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception as exc:
            raise HermesRagError("HermesRAG returned an invalid response") from exc
        if not isinstance(payload, dict):
            raise HermesRagError("HermesRAG returned an invalid response")
        return payload

    @classmethod
    def _map_result(
        cls, payload: dict[str, Any], *, requested_mode: str
    ) -> KnowledgeResult:
        answer = payload.get("answer") or payload.get("response")
        if not isinstance(answer, str) or not answer.strip():
            raise HermesRagError("HermesRAG returned an unusable response")

        pipeline_status = payload.get("pipeline_status") or "ready"
        if not isinstance(pipeline_status, str):
            raise HermesRagError("HermesRAG returned an invalid response")
        if pipeline_status in {"failed", "degraded", "budget_exhausted"}:
            raise HermesRagError("HermesRAG could not complete the request")

        mode = payload.get("rag_mode") or requested_mode
        if hasattr(mode, "value"):
            mode = mode.value
        mode = str(mode)
        terminal_reason = payload.get("terminal_reason") or "answer_ready"
        if not isinstance(terminal_reason, str):
            terminal_reason = "answer_ready"

        if pipeline_status == "ready" and "pipeline_status" in payload:
            sufficiency = "sufficient"
        elif pipeline_status == "partial_ready":
            sufficiency = "partial"
        elif pipeline_status == "insufficient_evidence":
            sufficiency = "insufficient"
        else:
            sufficiency = "not_evaluated"

        citations = cls._map_citations(payload)
        return KnowledgeResult(
            answer=answer.strip(),
            citations=tuple(citations),
            mode=mode,
            pipeline_status=pipeline_status,
            terminal_reason=terminal_reason,
            evidence_sufficiency=sufficiency,
        )

    @staticmethod
    def _map_citations(payload: dict[str, Any]) -> list[KnowledgeCitation]:
        raw_citations = payload.get("citations")
        if isinstance(raw_citations, list):
            mapped = []
            for index, item in enumerate(raw_citations, 1):
                if not isinstance(item, dict):
                    continue
                filename = item.get("filename") or item.get("source_label")
                if not isinstance(filename, str) or not filename.strip():
                    continue
                mapped.append(
                    KnowledgeCitation(
                        citation_id=str(item.get("citation_id") or f"E{index}"),
                        filename=filename.strip(),
                        source_label=str(item.get("source_label") or filename).strip(),
                        page_number=item.get("page_number"),
                    )
                )
            return mapped

        trace = payload.get("rag_trace")
        chunks = trace.get("retrieved_chunks") if isinstance(trace, dict) else None
        if not isinstance(chunks, list):
            return []
        mapped = []
        seen = set()
        for item in chunks:
            if not isinstance(item, dict):
                continue
            filename = item.get("filename")
            if not isinstance(filename, str) or not filename.strip():
                continue
            page = item.get("page_number")
            identity = (filename.strip(), str(page) if page is not None else "")
            if identity in seen:
                continue
            seen.add(identity)
            mapped.append(
                KnowledgeCitation(
                    citation_id=f"E{len(mapped) + 1}",
                    filename=filename.strip(),
                    source_label=filename.strip(),
                    page_number=page,
                )
            )
        return mapped
