import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from services.memory_consolidator import (
    MemoryCandidate,
    MemoryConsolidationDispatcher,
    MemoryConsolidator,
    RuleBasedMemoryCandidateExtractor,
    TurnCompletion,
    VerifiedMemoryFact,
)
from services.memory_manager import MemoryManager


def completion_fixture(**overrides) -> TurnCompletion:
    values = {
        "tenant_id": "demo",
        "user_id": "user-a",
        "session_id": "session-a",
        "turn_id": "turn-1",
        "status": "completed",
        "route": "knowledge_consultation",
        "user_message": "这个商品怎么保修？",
        "public_result": "整机保修一年。",
    }
    values.update(overrides)
    return TurnCompletion(**values)


def test_rule_extractor_emits_minimal_return_event_without_raw_chat():
    completion = completion_fixture(
        route="order_after_sales",
        user_message="订单 JP20260920001 退货已确认",
        public_result="退货申请已提交，申请编号为 AS-001。",
    )

    candidates = RuleBasedMemoryCandidateExtractor().extract(completion)

    assert candidates[0].event_type == "return_requested"
    assert candidates[0].entity_refs == ("JP20260920001",)
    assert candidates[0].summary == (
        "订单 JP20260920001 已提交退货申请，申请编号 AS-001"
    )
    serialized = candidates[0].model_dump_json()
    assert completion.user_message not in serialized
    assert completion.public_result not in serialized


def test_verified_fact_supports_real_confirmation_turn_without_repeated_id():
    completion = completion_fixture(
        route="order_after_sales",
        user_message="确认提交",
        public_result="退货申请已提交。",
        verified_facts=(
            VerifiedMemoryFact(
                event_type="return_requested",
                candidate_key="return:JP20260920001:AS-001",
                summary="订单 JP20260920001 已提交退货申请，申请编号 AS-001",
                outcome="completed",
                entity_refs=("JP20260920001",),
            ),
        ),
    )

    candidates = RuleBasedMemoryCandidateExtractor().extract(completion)

    assert len(candidates) == 1
    assert candidates[0].candidate_key == "return:JP20260920001:AS-001"


@pytest.mark.parametrize("status", ["failed", "needs_input"])
def test_non_terminal_success_status_has_no_candidates(status):
    completion = completion_fixture(status=status)

    assert RuleBasedMemoryCandidateExtractor().extract(completion) == ()


def test_explicit_preference_requires_stable_language():
    completion = completion_fixture(
        user_message="以后上门维修尽量安排在上午",
        route="service_appointment",
    )

    candidate = RuleBasedMemoryCandidateExtractor().extract(completion)[0]

    assert candidate.kind == "profile"
    assert candidate.memory_key == "service_time_preference"
    assert candidate.memory_value == "morning"
    assert candidate.source_type == "explicit"
    assert candidate.confidence == 1.0


def test_one_time_time_expression_is_not_a_stable_preference():
    completion = completion_fixture(
        user_message="这次上门维修安排在上午",
        route="service_appointment",
    )

    assert RuleBasedMemoryCandidateExtractor().extract(completion) == ()


def test_stable_language_preference_ignores_negated_alternative():
    completion = completion_fixture(
        user_message="以后不要用英文，请用中文",
    )

    candidates = RuleBasedMemoryCandidateExtractor().extract(completion)

    assert len(candidates) == 1
    assert candidates[0].memory_key == "communication_language"
    assert candidates[0].memory_value == "zh"


def test_stable_time_preference_ignores_negated_alternative():
    completion = completion_fixture(
        user_message="以后不要安排上午，请安排下午",
        route="service_appointment",
    )

    candidates = RuleBasedMemoryCandidateExtractor().extract(completion)

    assert len(candidates) == 1
    assert candidates[0].memory_key == "service_time_preference"
    assert candidates[0].memory_value == "afternoon"


def test_time_sequence_word_is_not_a_stable_preference_marker():
    completion = completion_fixture(
        user_message="这次下午三点以后安排上门维修",
        route="service_appointment",
    )

    assert RuleBasedMemoryCandidateExtractor().extract(completion) == ()


def sqlite_url(tmp_path, name="memory-consolidator.db") -> str:
    return f"sqlite:///{tmp_path / name}"


def return_completion(**overrides) -> TurnCompletion:
    values = {
        "route": "order_after_sales",
        "user_message": "订单 JP20260920001 退货已确认",
        "public_result": "退货申请已提交，申请编号为 AS-001。",
    }
    values.update(overrides)
    return completion_fixture(**values)


class FixedExtractor:
    def __init__(self, *candidates: MemoryCandidate) -> None:
        self.candidates = candidates

    def extract(self, completion: TurnCompletion):
        return self.candidates


def profile_candidate(**overrides) -> MemoryCandidate:
    values = {
        "kind": "profile",
        "candidate_key": "profile:service_time_preference",
        "memory_key": "service_time_preference",
        "memory_value": "morning",
        "source_type": "explicit",
        "confidence": 1.0,
    }
    values.update(overrides)
    return MemoryCandidate(**values)


def episodic_candidate(**overrides) -> MemoryCandidate:
    values = {
        "kind": "episodic",
        "candidate_key": "return:JP20260920001:AS-001",
        "event_type": "return_requested",
        "summary": "订单 JP20260920001 已提交退货申请",
        "outcome": "completed",
        "entity_refs": ("JP20260920001",),
    }
    values.update(overrides)
    return MemoryCandidate(**values)


def test_consolidation_is_idempotent_per_scoped_candidate(tmp_path):
    manager = MemoryManager(sqlite_url(tmp_path))
    consolidator = MemoryConsolidator(manager)

    first = consolidator.consolidate(return_completion())
    second = consolidator.consolidate(return_completion())

    assert first.written == 1
    assert second.written == 0
    assert tuple(manager.recall_events("demo", "user-a", limit=3)) == (
        first.memories
    )


def test_consolidation_same_turn_is_scoped_by_tenant(tmp_path):
    manager = MemoryManager(sqlite_url(tmp_path))
    consolidator = MemoryConsolidator(manager)

    tenant_a = consolidator.consolidate(
        return_completion(tenant_id="tenant-a")
    )
    tenant_b = consolidator.consolidate(
        return_completion(tenant_id="tenant-b")
    )

    assert tenant_a.written == tenant_b.written == 1
    assert len(manager.recall_events("tenant-a", "user-a")) == 1
    assert len(manager.recall_events("tenant-b", "user-a")) == 1
    assert manager.recall_events("tenant-c", "user-a") == []


@pytest.mark.parametrize(
    "unsafe",
    [
        "手机号 13800138000",
        "银行卡 6222020202020202",
        "地址 北京市朝阳区某街道 88 号 2 单元 301",
        "邮箱 user@example.com",
        "api_key=secret-token",
    ],
)
def test_sensitive_candidate_is_rejected(tmp_path, unsafe):
    extractor = FixedExtractor(profile_candidate(memory_value=unsafe))
    result = MemoryConsolidator(
        MemoryManager(sqlite_url(tmp_path)), extractor
    ).consolidate(completion_fixture())

    assert result.written == 0
    assert result.rejected == 1
    assert result.memories == ()


def test_sensitive_or_unrecognized_outcome_is_rejected(tmp_path):
    candidate = episodic_candidate(
        outcome="api_key=SYNTHETIC_PRIVATE_TOKEN"
    )

    result = MemoryConsolidator(
        MemoryManager(sqlite_url(tmp_path)), FixedExtractor(candidate)
    ).consolidate(completion_fixture())

    assert result.written == 0
    assert result.rejected == 1


def test_sensitive_source_rejects_even_normalized_candidate(tmp_path):
    result = MemoryConsolidator(
        MemoryManager(sqlite_url(tmp_path)),
        FixedExtractor(profile_candidate()),
    ).consolidate(
        completion_fixture(
            user_message="以后请安排上午，联系电话 13800138000"
        )
    )

    assert result.written == 0
    assert result.rejected == 1


def test_profile_candidate_must_be_explicit_and_fully_confident(tmp_path):
    candidate = profile_candidate(confidence=0.8)
    result = MemoryConsolidator(
        MemoryManager(sqlite_url(tmp_path)), FixedExtractor(candidate)
    ).consolidate(completion_fixture())

    assert result.written == 0
    assert result.rejected == 1


def test_same_turn_profile_candidate_does_not_create_a_new_version(tmp_path):
    manager = MemoryManager(sqlite_url(tmp_path))
    consolidator = MemoryConsolidator(
        manager, FixedExtractor(profile_candidate())
    )

    first = consolidator.consolidate(completion_fixture())
    second = consolidator.consolidate(completion_fixture())

    assert first.written == 1
    assert second.written == 0
    assert second.skipped == 1
    assert len(manager.recall_profiles("demo", "user-a")) == 1


def test_replaying_old_profile_turn_does_not_rollback_newer_preference(tmp_path):
    manager = MemoryManager(sqlite_url(tmp_path))
    english = MemoryConsolidator(
        manager,
        FixedExtractor(
            profile_candidate(
                candidate_key="profile:communication_language",
                memory_key="communication_language",
                memory_value="en",
            )
        ),
    )
    chinese = MemoryConsolidator(
        manager,
        FixedExtractor(
            profile_candidate(
                candidate_key="profile:communication_language",
                memory_key="communication_language",
                memory_value="zh",
            )
        ),
    )

    english.consolidate(completion_fixture(turn_id="turn-old"))
    chinese.consolidate(completion_fixture(turn_id="turn-new"))
    replay = english.consolidate(completion_fixture(turn_id="turn-old"))

    assert replay.written == 0
    assert replay.skipped == 1
    assert manager.recall_profiles(
        "demo", "user-a", keys=["communication_language"]
    )[0]["memory_value"] == "zh"


def test_concurrent_profile_replay_keeps_one_active_version(tmp_path):
    manager = MemoryManager(sqlite_url(tmp_path, "profile-race.db"))
    barrier = threading.Barrier(2)

    class BarrierExtractor(FixedExtractor):
        def extract(self, completion):
            barrier.wait(timeout=2)
            return super().extract(completion)

    consolidator = MemoryConsolidator(
        manager, BarrierExtractor(profile_candidate())
    )
    completion = completion_fixture(turn_id="turn-race")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(consolidator.consolidate, [completion, completion]))

    assert sorted(result.written for result in results) == [0, 1]
    assert len(manager.recall_profiles("demo", "user-a")) == 1


def test_dispatcher_runs_consolidation_and_can_be_drained():
    class RecordingConsolidator:
        def __init__(self):
            self.items = []

        def consolidate(self, completion):
            self.items.append(completion)

    consolidator = RecordingConsolidator()
    dispatcher = MemoryConsolidationDispatcher(consolidator)
    completion = completion_fixture()

    async def run():
        assert dispatcher.submit(completion) is True
        await dispatcher.drain()

    asyncio.run(run())
    assert consolidator.items == [completion]


def test_dispatcher_is_bounded_and_does_not_raise_worker_failure():
    class BlockingConsolidator:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def consolidate(self, completion):
            self.started.set()
            self.release.wait(timeout=2)
            raise RuntimeError("private worker failure")

    consolidator = BlockingConsolidator()
    dispatcher = MemoryConsolidationDispatcher(consolidator, max_pending=1)

    async def run():
        assert dispatcher.submit(completion_fixture(turn_id="turn-a")) is True
        started = await asyncio.to_thread(consolidator.started.wait, 1)
        assert started is True
        assert dispatcher.submit(completion_fixture(turn_id="turn-b")) is False
        consolidator.release.set()
        await dispatcher.drain()
        assert dispatcher.pending_count == 0

    asyncio.run(run())


def test_real_confirmed_return_is_consolidated_with_server_turn_id(tmp_path):
    from agents.order_after_sales_agent import OrderAfterSalesAgent
    from services.order_after_sales_service import OrderAfterSalesService
    from web.routes import build_agent_event_stream

    database_url = sqlite_url(tmp_path, "real-return.db")
    service = OrderAfterSalesService(database_url)
    service.seed_demo_data()
    manager = MemoryManager(database_url)
    agent = OrderAfterSalesAgent(
        session_id="session-a",
        service=service,
        tenant_id="demo",
        user_id="user-a",
        memory_manager=manager,
    )
    dispatcher = MemoryConsolidationDispatcher(MemoryConsolidator(manager))

    async def run():
        async for _ in agent.run_stream(
            "申请退货 JP20260919002，原因是商品破损",
            turn_id="turn-request",
        ):
            pass

        async def confirmed_processor(message):
            yield "[THOUGHT][归类机器人] 已识别为订单售后任务，转交订单售后 Agent 处理。"
            async for token in agent.run_stream(
                message, turn_id="turn-confirm"
            ):
                yield token

        async for _ in build_agent_event_stream(
            "确认提交",
            turn_id="turn-confirm",
            session_id="session-a",
            processor=confirmed_processor,
            memory_dispatcher=dispatcher,
        ):
            pass
        await dispatcher.drain()

    asyncio.run(run())

    events = manager.recall_events(
        "demo", "user-a", event_type="return_requested"
    )
    assert len(events) == 1
    assert events[0]["source_trace_id"] == "turn-confirm"
    assert events[0]["entity_refs"] == ["JP20260919002"]


def test_successful_appointment_is_consolidated_with_verified_reference(
    tmp_path,
):
    from datetime import datetime, timedelta

    from agents.appointment.appointment_processor import AppointmentProcessor
    from agents.appointment.message_builder import MessageBuilder
    from web.routes import build_agent_event_stream

    class Finder:
        def find_technician_with_thought(self, history, yield_func):
            return {"id": 7, "name": "张伟", "gender": "男"}

        def parse_time_and_duration(self, start_time, duration):
            start = datetime(2026, 9, 20, 10, 0)
            return start, start + timedelta(hours=1), 60

    class Database:
        def save_appointment(self, *args, **kwargs):
            return "APT-001"

        def update_memory_schedule(self, *args, **kwargs):
            return None

    processor = AppointmentProcessor(
        input_parser=None,
        technician_finder=Finder(),
        message_builder=MessageBuilder(),
        appointment_database=Database(),
        llm=None,
    )
    manager = MemoryManager(sqlite_url(tmp_path, "real-appointment.db"))
    dispatcher = MemoryConsolidationDispatcher(MemoryConsolidator(manager))

    async def appointment_processor(message):
        yield "[THOUGHT][归类机器人] 已识别为上门服务预约，转交服务预约 Agent 处理。"
        async for token in processor.handle_complete_appointment(
            {
                "start_time": "2026-09-20 10:00",
                "duration": "60分钟",
                "project": "空调安装",
            },
            "session-a",
        ):
            yield token

    async def run():
        async for _ in build_agent_event_stream(
            "确认预约",
            turn_id="turn-appointment",
            session_id="session-a",
            processor=appointment_processor,
            memory_dispatcher=dispatcher,
        ):
            pass
        await dispatcher.drain()

    asyncio.run(run())

    events = manager.recall_events(
        "demo", "user-a", event_type="service_booked"
    )
    assert len(events) == 1
    assert events[0]["source_trace_id"] == "turn-appointment"
    assert events[0]["entity_refs"] == ["APT-001"]
