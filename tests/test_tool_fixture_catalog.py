import asyncio
import json

import pytest

from evaluation.tool_fixtures import (
    FrozenKnowledgeClient,
    ToolFixtureCatalog,
    load_tool_fixture_catalog,
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
