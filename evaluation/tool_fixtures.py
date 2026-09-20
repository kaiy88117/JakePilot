"""Strict, versioned tool snapshots for reproducible formal evaluation."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.appointment.appointment_database import AppointmentDatabase
from agents.appointment.technician_finder import TechnicianFinder
from db.models import TechnicianSchedule
from services.appointment_service import AppointmentService
from services.hermesrag_client import (
    HermesRagError,
    KnowledgeCitation,
    KnowledgeResult,
)
from services.user_behavior_service import UserBehaviorService


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


class TechnicianToolFixture(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    gender: str | None = Field(default=None, max_length=16)
    strength: str | None = Field(default=None, max_length=256)


class AppointmentScheduleFixture(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    technician_name: str = Field(min_length=1, max_length=64)
    start_time: datetime
    end_time: datetime
    status: Literal["busy", "free"]

    @model_validator(mode="after")
    def validate_interval(self):
        if self.end_time <= self.start_time:
            raise ValueError("appointment schedule end must be after start")
        return self


class AppointmentToolFixture(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    technicians: tuple[TechnicianToolFixture, ...] = ()
    schedules: tuple[AppointmentScheduleFixture, ...] = ()

    @model_validator(mode="after")
    def reject_duplicate_technicians(self):
        names = [item.name for item in self.technicians]
        if len(set(names)) != len(names):
            raise ValueError("duplicate fixture technician")
        return self


class CaseToolFixture(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1, max_length=128)
    knowledge: KnowledgeToolFixture | None = None
    appointment: AppointmentToolFixture | None = None


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


class AppointmentFixtureResources:
    """Injected appointment services plus an evaluation write counter."""

    def __init__(
        self,
        *,
        appointment_service: AppointmentService,
        user_behavior_service: UserBehaviorService,
        initial_appointment_count: int,
    ) -> None:
        self.appointment_service = appointment_service
        self.user_behavior_service = user_behavior_service
        self.appointment_database = AppointmentDatabase(
            appointment_service=appointment_service,
            user_behavior_service=user_behavior_service,
        )
        self.technician_finder = TechnicianFinder(
            appointment_service=appointment_service
        )
        self.initial_appointment_count = initial_appointment_count
        self._closed = False

    def write_count(self) -> int:
        current = _appointment_count(self.appointment_service)
        return max(0, current - self.initial_appointment_count)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.appointment_service.close()
        self.user_behavior_service.close()


def seed_appointment_tool_fixture(
    database_url: str,
    case_fixture: CaseToolFixture | None,
) -> AppointmentFixtureResources:
    if case_fixture is None or case_fixture.appointment is None:
        raise ValueError("appointment fixture is unavailable")
    appointment_service = AppointmentService(database_url)
    user_behavior_service = UserBehaviorService(database_url)
    snapshot = case_fixture.appointment
    try:
        for technician in snapshot.technicians:
            technician_id = appointment_service.add_technician(
                technician.name,
                technician.gender,
                technician.strength,
            )
            if technician_id is None:
                raise ValueError("appointment technician fixture could not be seeded")

        for schedule in snapshot.schedules:
            technician = appointment_service.get_technician_by_name(
                schedule.technician_name
            )
            if technician is None:
                raise ValueError(
                    "appointment schedule references unknown technician"
                )
            appointment_service.technician_repo.add_schedule(
                technician_id=technician["id"],
                start_time=schedule.start_time,
                end_time=schedule.end_time,
                status=schedule.status,
            )

        return AppointmentFixtureResources(
            appointment_service=appointment_service,
            user_behavior_service=user_behavior_service,
            initial_appointment_count=_appointment_count(appointment_service),
        )
    except Exception:
        appointment_service.close()
        user_behavior_service.close()
        raise


def _appointment_count(service: AppointmentService) -> int:
    manager = service.technician_repo.session_manager
    with manager.session_scope() as session:
        return int(
            session.query(TechnicianSchedule)
            .filter(TechnicianSchedule.appointment_id.isnot(None))
            .count()
        )
