from datetime import datetime, timedelta, timezone

from runtime.context_engine import ContextEngine
from services.memory_manager import MemoryManager


NOW = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


def setup_memory(tmp_path):
    memory = MemoryManager(f"sqlite:///{tmp_path / 'context.db'}")
    memory.save_working(
        tenant_id="demo",
        user_id="user-a",
        session_id="session-a",
        active_intent="service_booking",
        plan_state="collecting_slots",
        slots={"order_id": "JP20260919002", "service_type": "安装"},
        now=NOW,
    )
    for index in range(5):
        memory.record_event(
            tenant_id="demo",
            user_id="user-a",
            event_type="service_event",
            summary=f"第 {index} 条服务事件",
            outcome="completed",
            entity_refs=["JP20260919002"] if index == 0 else [f"OTHER-{index}"],
            source_trace_id=f"trace-{index}",
            occurred_at=NOW - timedelta(hours=index),
        )
    memory.set_profile(
        tenant_id="demo",
        user_id="user-a",
        memory_key="service_time_preference",
        memory_value="周六上午",
        source_type="explicit",
        confidence=1.0,
        source_trace_id="trace-profile-time",
        now=NOW,
    )
    memory.set_profile(
        tenant_id="demo",
        user_id="user-a",
        memory_key="communication_language",
        memory_value="中文",
        source_type="explicit",
        confidence=1.0,
        source_trace_id="trace-profile-language",
        now=NOW,
    )
    memory.set_profile(
        tenant_id="demo",
        user_id="user-a",
        memory_key="product_category_preference",
        memory_value="智能家电",
        source_type="inferred",
        confidence=0.7,
        source_trace_id="trace-profile-product",
        now=NOW,
    )
    return memory


def test_builds_domain_projection_with_bounded_relevant_memories(tmp_path):
    memory = setup_memory(tmp_path)
    engine = ContextEngine(memory)

    projection = engine.build(
        tenant_id="demo",
        user_id="user-a",
        session_id="session-a",
        current_request="帮我预约这个订单周六上午安装",
        target_agent="service_appointment",
        entity_refs=["JP20260919002"],
        token_budget=1000,
        now=NOW,
    )

    assert projection.working_loaded is True
    assert projection.episodic_count == 3
    assert projection.profile_count == 2
    assert [segment.kind for segment in projection.segments[:2]] == [
        "current_request",
        "task_state",
    ]
    profile_keys = {
        segment.content["memory_key"]
        for segment in projection.segments
        if segment.kind == "profile_memory"
    }
    assert profile_keys == {"service_time_preference", "communication_language"}
    first_episode = next(
        segment
        for segment in projection.segments
        if segment.kind == "episodic_memory"
    )
    assert first_episode.content["source_trace_id"] == "trace-0"
    assert all(segment.source for segment in projection.segments)
    assert all(segment.inclusion_reason for segment in projection.segments)


def test_budget_keeps_request_and_task_state_before_optional_memory(tmp_path):
    memory = setup_memory(tmp_path)
    engine = ContextEngine(memory)

    projection = engine.build(
        tenant_id="demo",
        user_id="user-a",
        session_id="session-a",
        current_request="预约安装",
        target_agent="service_appointment",
        entity_refs=["JP20260919002"],
        token_budget=1,
        now=NOW,
    )

    assert [segment.kind for segment in projection.segments] == [
        "current_request",
        "task_state",
    ]
    assert projection.episodic_count == 0
    assert projection.profile_count == 0
    assert projection.dropped_count == 5


def test_projection_does_not_cross_tenant_or_user_boundaries(tmp_path):
    memory = setup_memory(tmp_path)
    engine = ContextEngine(memory)

    projection = engine.build(
        tenant_id="other",
        user_id="user-a",
        session_id="session-a",
        current_request="预约安装",
        target_agent="service_appointment",
        entity_refs=["JP20260919002"],
        now=NOW,
    )

    assert projection.working_loaded is False
    assert projection.episodic_count == 0
    assert projection.profile_count == 0
    assert [segment.kind for segment in projection.segments] == [
        "current_request"
    ]


def test_expired_working_memory_is_not_projected(tmp_path):
    memory = setup_memory(tmp_path)
    engine = ContextEngine(memory)

    projection = engine.build(
        tenant_id="demo",
        user_id="user-a",
        session_id="session-a",
        current_request="继续处理",
        target_agent="order_after_sales",
        now=NOW + timedelta(minutes=31),
    )

    assert projection.working_loaded is False
    assert all(segment.kind != "task_state" for segment in projection.segments)


def test_unknown_target_agent_does_not_receive_unscoped_profiles(tmp_path):
    memory = setup_memory(tmp_path)
    engine = ContextEngine(memory)

    projection = engine.build(
        tenant_id="demo",
        user_id="user-a",
        session_id="session-a",
        current_request="继续处理",
        target_agent="unknown_agent",
        now=NOW,
    )

    assert projection.profile_count == 0
    assert all(segment.kind != "profile_memory" for segment in projection.segments)
