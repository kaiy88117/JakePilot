"""Strict, versioned tool snapshots for reproducible formal evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.hermesrag_client import (
    HermesRagError,
    KnowledgeCitation,
    KnowledgeResult,
)


class KnowledgeToolFixture(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    answer: str = Field(min_length=1, max_length=20000)
    citations: tuple[KnowledgeCitation, ...] = ()
    mode: str = Field(min_length=1, max_length=32)
    pipeline_status: str = Field(min_length=1, max_length=64)
    terminal_reason: str = Field(min_length=1, max_length=128)
    evidence_sufficiency: Literal[
        "sufficient", "partial", "insufficient", "not_evaluated"
    ]


class CaseToolFixture(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1, max_length=128)
    knowledge: KnowledgeToolFixture | None = None


class ToolFixtureCatalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fixture_version: str = Field(
        pattern=r"^[a-z0-9._-]+-v\d+$",
        min_length=1,
        max_length=128,
    )
    cases: tuple[CaseToolFixture, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_case_ids(self):
        case_ids = [item.case_id for item in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("duplicate fixture case_id")
        return self

    def for_case(self, case_id: str) -> CaseToolFixture | None:
        return next(
            (item for item in self.cases if item.case_id == case_id),
            None,
        )


def load_tool_fixture_catalog(path: str | Path) -> ToolFixtureCatalog:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ToolFixtureCatalog.model_validate(payload)


class FrozenKnowledgeClient:
    """Serve one Case's pinned HermesRAG result without network access."""

    def __init__(self, catalog: ToolFixtureCatalog, case_id: str) -> None:
        self.catalog = catalog
        self.case_id = case_id

    async def query(
        self,
        message: str,
        session_id: str,
        mode: str = "auto",
    ) -> KnowledgeResult:
        if not message.strip() or not session_id.strip():
            raise HermesRagError("knowledge fixture request is invalid")
        fixture = self.catalog.for_case(self.case_id)
        if fixture is None or fixture.knowledge is None:
            raise HermesRagError("knowledge fixture is unavailable")
        snapshot = fixture.knowledge
        return KnowledgeResult(
            answer=snapshot.answer,
            citations=snapshot.citations,
            mode=snapshot.mode,
            pipeline_status=snapshot.pipeline_status,
            terminal_reason=snapshot.terminal_reason,
            evidence_sufficiency=snapshot.evidence_sufficiency,
        )
