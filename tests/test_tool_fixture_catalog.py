import asyncio
import json

import pytest

from evaluation.tool_fixtures import (
    FrozenKnowledgeClient,
    ToolFixtureCatalog,
    load_tool_fixture_catalog,
    seed_appointment_tool_fixture,
)
from services.hermesrag_client import HermesRagError


def _payload():
    return {
        "fixture_version": "ecommerce-mock-v2",
        "cases": [
            {
                "case_id": "knowledge-001",
                "knowledge": {
                    "answer": "耳机整机保修一年。",
                    "citations": [
                        {
                            "citation_id": "E1",
                            "filename": "warranty.md",
                            "source_label": "耳机保修政策",
                            "page_number": 1,
                        }
                    ],
                    "mode": "agentic",
                    "pipeline_status": "ready",
                    "terminal_reason": "answer_ready",
                    "evidence_sufficiency": "sufficient",
                },
            },
            {
                "case_id": "order-001",
                "knowledge": None,
            },
            {
                "case_id": "appointment-001",
                "knowledge": None,
                "appointment": {
                    "technicians": [
                        {
                            "name": "张伟",
                            "gender": "男",
                            "strength": "空调安装与调试",
                        }
                    ],
                    "schedules": [
                        {
                            "technician_name": "张伟",
                            "start_time": "2026-09-21T10:00:00",
                            "end_time": "2026-09-21T11:00:00",
                            "status": "busy",
                        }
                    ],
                },
            },
        ],
    }


def test_fixture_catalog_loads_strict_versioned_knowledge_snapshot(tmp_path):
    path = tmp_path / "fixtures.json"
    path.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")

    catalog = load_tool_fixture_catalog(path)
    result = asyncio.run(
        FrozenKnowledgeClient(catalog, "knowledge-001").query(
            "耳机保修多久？",
            "eval-session",
            mode="auto",
        )
    )

    assert catalog.fixture_version == "ecommerce-mock-v2"
    assert result.answer == "耳机整机保修一年。"
    assert result.citations[0].source_label == "耳机保修政策"
    assert result.mode == "agentic"
    assert result.evidence_sufficiency == "sufficient"


def test_fixture_catalog_rejects_duplicates_and_unknown_fields():
    payload = _payload()
    payload["cases"].append(payload["cases"][0])
    with pytest.raises(ValueError, match="duplicate fixture case_id"):
        ToolFixtureCatalog.model_validate(payload)

    payload = _payload()
    payload["secret_token"] = "must-not-be-accepted"
    with pytest.raises(ValueError):
        ToolFixtureCatalog.model_validate(payload)


def test_frozen_knowledge_client_fails_closed_for_missing_case_or_snapshot():
    catalog = ToolFixtureCatalog.model_validate(_payload())

    for case_id in ("missing-001", "order-001"):
        with pytest.raises(HermesRagError, match="fixture is unavailable"):
            asyncio.run(
                FrozenKnowledgeClient(catalog, case_id).query(
                    "问题",
                    "eval-session",
                )
            )


def test_appointment_fixture_seeds_isolated_staff_and_schedule(tmp_path):
    catalog = ToolFixtureCatalog.model_validate(_payload())
    fixture = catalog.for_case("appointment-001")
    database_url = f"sqlite:///{(tmp_path / 'appointment.db').as_posix()}"

    resources = seed_appointment_tool_fixture(database_url, fixture)
    try:
        technician = resources.appointment_service.get_technician_by_name("张伟")
        assert technician["strength"] == "空调安装与调试"
        assert resources.appointment_service.is_technician_available(
            technician["id"],
            fixture.appointment.schedules[0].start_time,
            fixture.appointment.schedules[0].end_time,
        ) is False
        assert resources.write_count() == 0

        resources.appointment_service.save_appointment(
            str(technician["id"]),
            fixture.appointment.schedules[0].end_time,
            fixture.appointment.schedules[0].end_time.replace(hour=12),
            {"project": "空调安装"},
            "eval-session",
        )
        assert resources.write_count() == 1
    finally:
        resources.close()


def test_appointment_fixture_rejects_schedule_for_unknown_technician(tmp_path):
    payload = _payload()
    payload["cases"][2]["appointment"]["schedules"][0][
        "technician_name"
    ] = "不存在"
    catalog = ToolFixtureCatalog.model_validate(payload)
    fixture = catalog.for_case("appointment-001")

    with pytest.raises(ValueError, match="unknown technician"):
        seed_appointment_tool_fixture(
            f"sqlite:///{(tmp_path / 'invalid.db').as_posix()}",
            fixture,
        )
