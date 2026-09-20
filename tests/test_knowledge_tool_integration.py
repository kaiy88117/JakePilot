import asyncio
import json
from types import SimpleNamespace

from agents.consultant_agent import ConsultantAgent
from agents.consultant.consultation_processor import ConsultationProcessor
from services.hermesrag_client import (
    HermesRagError,
    KnowledgeCitation,
    KnowledgeResult,
)


class FakeKnowledgeClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def query(self, message, session_id, mode="auto"):
        self.calls.append((message, session_id, mode))
        if self.error:
            raise self.error
        return self.result


class FakeRetriever:
    def __init__(self):
        self.calls = []

    async def search_knowledge(self, user_input, top_k):
        self.calls.append((user_input, top_k))
        return [{"content": "本地知识", "category": "policy"}]


class FakeResponseGenerator:
    async def generate_response(self, user_input, knowledge_docs):
        return "本地降级回答"

    async def generate_response_stream(self, user_input, knowledge_docs):
        yield "[REPLY][咨询机器人]"
        yield "本地降级回答"


def run(coro):
    return asyncio.run(coro)


def ready_result():
    return KnowledgeResult(
        answer="耳机保修期为一年。[E1]",
        citations=(
            KnowledgeCitation(
                citation_id="E1",
                filename="warranty.md",
                source_label="耳机保修政策",
            ),
        ),
        mode="agentic",
        pipeline_status="ready",
        terminal_reason="answer_ready",
        evidence_sufficiency="sufficient",
    )


def make_processor(client):
    return ConsultationProcessor(
        FakeRetriever(),
        None,
        FakeResponseGenerator(),
        knowledge_client=client,
    )


def test_non_stream_consultation_uses_hermesrag_answer_and_source():
    client = FakeKnowledgeClient(result=ready_result())
    processor = make_processor(client)

    answer = run(processor.process_consultation("耳机保修期多久？", "session-a"))

    assert answer == "耳机保修期为一年。[E1]\n\n来源：耳机保修政策"
    assert client.calls == [("耳机保修期多久？", "session-a", "auto")]
    assert processor.knowledge_retriever.calls == []


def test_stream_emits_safe_knowledge_event_before_answer():
    processor = make_processor(FakeKnowledgeClient(result=ready_result()))

    async def no_record(*args, **kwargs):
        return None

    processor._record_consultation_behavior = no_record

    async def collect():
        return [
            token
            async for token in processor.process_consultation_stream(
                "耳机保修期多久？", "session-b"
            )
        ]

    tokens = run(collect())
    event = json.loads(tokens[0].removeprefix("[EVENT]"))

    assert event == {
        "type": "knowledge_retrieval",
        "data": {
            "mode": "agentic",
            "pipeline_status": "ready",
            "evidence_sufficiency": "sufficient",
            "citation_count": 1,
            "terminal_reason": "answer_ready",
            "fallback": False,
        },
    }
    assert "".join(tokens[1:]) == (
        "[REPLY][咨询机器人]耳机保修期为一年。[E1]\n\n来源：耳机保修政策"
    )


def test_insufficient_evidence_is_returned_without_local_fallback():
    result = KnowledgeResult(
        answer="当前知识库证据不足，无法确认该政策。",
        mode="agentic",
        pipeline_status="insufficient_evidence",
        terminal_reason="insufficient_evidence",
        evidence_sufficiency="insufficient",
    )
    client = FakeKnowledgeClient(result=result)
    processor = make_processor(client)

    answer = run(processor.process_consultation("未知政策", "session-c"))

    assert answer == "当前知识库证据不足，无法确认该政策。"
    assert processor.knowledge_retriever.calls == []


def test_service_failure_falls_back_to_existing_local_consultation():
    client = FakeKnowledgeClient(error=HermesRagError("private upstream detail"))
    processor = make_processor(client)

    answer = run(processor.process_consultation("退货政策", "session-d"))

    assert answer == "本地降级回答"
    assert processor.knowledge_retriever.calls == [("退货政策", 3)]


def test_stream_failure_announces_generic_fallback_without_exception_text():
    processor = make_processor(
        FakeKnowledgeClient(error=HermesRagError("PRIVATE_UPSTREAM_DETAIL"))
    )

    async def no_record(*args, **kwargs):
        return None

    processor._record_consultation_behavior = no_record

    async def collect():
        return [
            token
            async for token in processor.process_consultation_stream(
                "退货政策", "session-e"
            )
        ]

    tokens = run(collect())
    serialized = "".join(tokens)
    event = json.loads(tokens[0].removeprefix("[EVENT]"))

    assert event["data"] == {
        "mode": "local",
        "pipeline_status": "degraded",
        "evidence_sufficiency": "not_evaluated",
        "citation_count": 0,
        "terminal_reason": "hermesrag_unavailable",
        "fallback": True,
    }
    assert "本地降级回答" in serialized
    assert "PRIVATE_UPSTREAM_DETAIL" not in serialized


def test_entering_hermesrag_consultation_does_not_require_local_index():
    class LocalRetrieverMustRemainLazy:
        async def initialize(self):
            raise AssertionError("local index should remain lazy")

    agent = ConsultantAgent.__new__(ConsultantAgent)
    agent.knowledge_retriever = LocalRetrieverMustRemainLazy()
    agent.consultation_processor = SimpleNamespace(knowledge_client=object())

    entered = run(agent.__aenter__())

    assert entered is agent
