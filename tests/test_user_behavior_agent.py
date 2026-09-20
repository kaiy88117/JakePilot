"""Boundary tests for the retired online user-behavior agent path.

Long-term behavior is now handled by MemoryConsolidator after a terminal turn;
it is not an online routing agent and it never infers profiles from raw events.
"""

from api.chat_handler import _create_task_agent
from services.memory_consolidator import MemoryConsolidator, TurnCompletion
from services.memory_manager import MemoryManager


def test_default_task_graph_has_no_online_user_behavior_agent(monkeypatch, tmp_path):
    import api.chat_handler as chat_handler

    manager = MemoryManager(f"sqlite:///{tmp_path / 'memory.db'}")
    monkeypatch.setattr(chat_handler, "_get_memory_manager", lambda: manager)

    graph = _create_task_agent("session-a")

    assert not hasattr(graph, "user_behavior_agent")
    assert graph.appointment_agent is not None
    assert graph.consultant_agent is not None


def test_one_time_behavior_does_not_become_a_profile(tmp_path):
    manager = MemoryManager(f"sqlite:///{tmp_path / 'memory.db'}")
    completion = TurnCompletion(
        tenant_id="demo",
        user_id="user-a",
        session_id="session-a",
        turn_id="turn-a",
        status="completed",
        route="service_appointment",
        user_message="这次安排在下午",
        public_result="请补充具体上门日期。",
    )

    result = MemoryConsolidator(manager).consolidate(completion)

    assert result.written == 0
    assert manager.recall_profiles("demo", "user-a") == []


def test_explicit_stable_preference_uses_profile_version_chain(tmp_path):
    manager = MemoryManager(f"sqlite:///{tmp_path / 'memory.db'}")
    consolidator = MemoryConsolidator(manager)

    first = TurnCompletion(
        tenant_id="demo",
        user_id="user-a",
        session_id="session-a",
        turn_id="turn-a",
        status="completed",
        route="service_appointment",
        user_message="以后上门服务尽量安排在上午",
        public_result="已记录您的时间偏好。",
    )
    second = first.model_copy(
        update={
            "turn_id": "turn-b",
            "user_message": "以后上门服务尽量安排在下午",
        }
    )

    assert consolidator.consolidate(first).written == 1
    assert consolidator.consolidate(second).written == 1
    current = manager.recall_profiles(
        "demo", "user-a", keys=["service_time_preference"]
    )

    assert len(current) == 1
    assert current[0]["memory_value"] == "afternoon"
