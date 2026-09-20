import pytest

from services.memory_consolidator import (
    RuleBasedMemoryCandidateExtractor,
    TurnCompletion,
)


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
