import pytest

from services.handoff_service import HandoffService


def _handoff_payload(**overrides):
    payload = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "turn_id": "turn-1",
        "reason_code": "user_requested",
        "summary": "用户明确请求人工客服",
        "verified_facts": [],
        "evidence_refs": [],
        "failed_steps": [],
    }
    payload.update(overrides)
    return payload


def test_create_or_get_handoff_is_idempotent_and_tenant_scoped(tmp_path):
    service = HandoffService(f"sqlite:///{tmp_path / 'handoff.db'}")

    first = service.create_or_get(**_handoff_payload())
    second = service.create_or_get(
        **_handoff_payload(summary="重复投递不应改写")
    )

    assert first["ticket_no"] == second["ticket_no"]
    assert second["summary"] == "用户明确请求人工客服"
    assert service.count("tenant-a") == 1
    assert service.list_recent("tenant-b") == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"summary": "13800138000"},
        {"summary": "contact@example.com"},
        {"summary": "过长" * 121},
        {"verified_facts": ["fact"] * 21},
        {"reason_code": "unknown_reason"},
    ],
)
def test_handoff_rejects_unbounded_or_sensitive_content(tmp_path, overrides):
    service = HandoffService(f"sqlite:///{tmp_path / 'handoff.db'}")

    with pytest.raises(ValueError):
        service.create_or_get(**_handoff_payload(**overrides))

