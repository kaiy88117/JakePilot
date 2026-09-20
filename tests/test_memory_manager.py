from datetime import datetime, timedelta, timezone

import pytest

from services.memory_manager import MemoryManager


def utc(hour=10):
    return datetime(2026, 9, 20, hour, 0, tzinfo=timezone.utc)


def manager(tmp_path):
    return MemoryManager(f"sqlite:///{tmp_path / 'memory.db'}")


def test_working_memory_is_tenant_user_and_session_scoped(tmp_path):
    memory = manager(tmp_path)
    memory.save_working(
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="same-session",
        active_intent="return_request",
        plan_state="collecting_reason",
        slots={"order_id": "JP20260919002"},
        now=utc(),
    )

    restored = memory.get_working(
        "tenant-a", "user-a", "same-session", now=utc()
    )

    assert restored["active_intent"] == "return_request"
    assert restored["slots"] == {"order_id": "JP20260919002"}
    assert restored["version"] == 1
    assert memory.get_working(
        "tenant-b", "user-a", "same-session", now=utc()
    ) is None
    assert memory.get_working(
        "tenant-a", "user-b", "same-session", now=utc()
    ) is None


def test_working_memory_versions_updates_and_expires(tmp_path):
    memory = manager(tmp_path)
    first = memory.save_working(
        tenant_id="demo",
        user_id="user-a",
        session_id="session-a",
        active_intent="return_request",
        plan_state="collecting_reason",
        slots={"order_id": "JP20260919002"},
        now=utc(),
        ttl_minutes=30,
    )
    second = memory.save_working(
        tenant_id="demo",
        user_id="user-a",
        session_id="session-a",
        active_intent="return_request",
        plan_state="awaiting_confirmation",
        slots={"order_id": "JP20260919002", "reason": "商品破损"},
        pending_action={"tool": "return.create"},
        now=utc(10) + timedelta(minutes=5),
        ttl_minutes=30,
    )

    assert first["version"] == 1
    assert second["version"] == 2
    assert memory.get_working(
        "demo", "user-a", "session-a", now=utc(10) + timedelta(minutes=34)
    )["plan_state"] == "awaiting_confirmation"
    assert memory.get_working(
        "demo", "user-a", "session-a", now=utc(10) + timedelta(minutes=36)
    ) is None


def test_events_are_ranked_by_entity_match_then_recency(tmp_path):
    memory = manager(tmp_path)
    memory.record_event(
        tenant_id="demo",
        user_id="user-a",
        event_type="return_completed",
        summary="旧订单已完成退货",
        outcome="completed",
        entity_refs=["JP-OLD"],
        source_trace_id="trace-old",
        occurred_at=utc(9),
    )
    memory.record_event(
        tenant_id="demo",
        user_id="user-a",
        event_type="return_completed",
        summary="当前订单曾提交退货",
        outcome="completed",
        entity_refs=["JP20260919002"],
        source_trace_id="trace-match",
        occurred_at=utc(8),
    )

    recalled = memory.recall_events(
        "demo",
        "user-a",
        entity_refs=["JP20260919002"],
        limit=3,
        now=utc(10),
    )

    assert [item["source_trace_id"] for item in recalled] == [
        "trace-match",
        "trace-old",
    ]
    assert memory.recall_events(
        "demo", "user-b", entity_refs=["JP20260919002"], now=utc(10)
    ) == []


def test_profile_update_supersedes_previous_current_value(tmp_path):
    memory = manager(tmp_path)
    first = memory.set_profile(
        tenant_id="demo",
        user_id="user-a",
        memory_key="service_time_preference",
        memory_value="周六上午",
        source_type="explicit",
        confidence=1.0,
        source_trace_id="trace-1",
        now=utc(9),
    )
    second = memory.set_profile(
        tenant_id="demo",
        user_id="user-a",
        memory_key="service_time_preference",
        memory_value="周日下午",
        source_type="explicit",
        confidence=1.0,
        source_trace_id="trace-2",
        now=utc(10),
    )

    current = memory.recall_profiles("demo", "user-a", now=utc(10))

    assert first["memory_id"] != second["memory_id"]
    assert [(item["memory_key"], item["memory_value"]) for item in current] == [
        ("service_time_preference", "周日下午")
    ]


@pytest.mark.parametrize(
    "memory_key",
    ["phone_number", "real_name", "bank_card", "id_card", "full_address"],
)
def test_profile_rejects_high_sensitive_raw_fields(tmp_path, memory_key):
    memory = manager(tmp_path)

    with pytest.raises(ValueError, match="sensitive profile field"):
        memory.set_profile(
            tenant_id="demo",
            user_id="user-a",
            memory_key=memory_key,
            memory_value="private-value",
            source_type="explicit",
            confidence=1.0,
            source_trace_id="trace-private",
            now=utc(),
        )
