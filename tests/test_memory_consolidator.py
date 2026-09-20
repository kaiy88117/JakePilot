import pytest

from services.memory_consolidator import (
    MemoryCandidate,
    MemoryConsolidator,
    RuleBasedMemoryCandidateExtractor,
    TurnCompletion,
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
