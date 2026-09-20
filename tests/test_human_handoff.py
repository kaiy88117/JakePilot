import asyncio

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


def _tool_context(**overrides):
    from runtime.tools import ToolContext

    values = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "turn_id": "turn-1",
        "idempotency_key": "handoff-turn-1",
    }
    values.update(overrides)
    return ToolContext(**values)


def _handoff_arguments():
    return {
        "reason_code": "user_requested",
        "summary": "用户明确请求人工客服",
        "verified_facts": [],
        "evidence_refs": [],
        "failed_steps": [],
    }


def test_handoff_control_tool_needs_idempotency_but_no_second_confirmation(
    tmp_path,
):
    from runtime.handoff import register_handoff_tool
    from runtime.tools import ToolRegistry

    service = HandoffService(f"sqlite:///{tmp_path / 'handoff.db'}")
    registry = ToolRegistry()
    register_handoff_tool(registry, service)

    missing_key = asyncio.run(
        registry.execute(
            "handoff.create",
            _handoff_arguments(),
            _tool_context(idempotency_key=None),
        )
    )
    first = asyncio.run(
        registry.execute(
            "handoff.create", _handoff_arguments(), _tool_context()
        )
    )
    second = asyncio.run(
        registry.execute(
            "handoff.create", _handoff_arguments(), _tool_context()
        )
    )

    assert missing_key.status == "failed"
    assert first.status == "handed_off"
    assert first.data["ticket_no"] == second.data["ticket_no"]
    assert service.count("tenant-a") == 1


def test_bounded_runtime_stops_with_handed_off_terminal_status(tmp_path):
    from runtime.contracts import TurnRequest, TurnStatus
    from runtime.handoff import register_handoff_tool
    from runtime.loop import BoundedAgentRuntime, PlanAction
    from runtime.tools import ToolRegistry

    class HandoffPlanner:
        async def next_action(self, turn, history):
            return PlanAction.tool("handoff.create", _handoff_arguments())

    service = HandoffService(f"sqlite:///{tmp_path / 'handoff.db'}")
    registry = ToolRegistry()
    register_handoff_tool(registry, service)
    turn = TurnRequest(
        turn_id="turn-1",
        session_id="session-a",
        user_id="user-a",
        tenant_id="tenant-a",
        message="请转人工客服",
    )

    run = asyncio.run(
        BoundedAgentRuntime(registry).run(turn, HandoffPlanner(), _tool_context())
    )

    assert run.outcome.status == TurnStatus.HANDED_OFF
    assert run.outcome.tool_calls == 1
    assert run.events[-1].type == "turn_finished"
    assert run.events[-1].data["status"] == "handed_off"
