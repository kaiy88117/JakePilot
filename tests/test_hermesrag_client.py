import asyncio

import pytest

from services.hermesrag_client import HermesRagClient, HermesRagError


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def run(coro):
    return asyncio.run(coro)


def test_maps_agentic_answer_citations_and_evidence_status():
    def requester(method, url, **kwargs):
        assert method == "POST"
        assert url == "http://hermes.test/chat"
        assert kwargs["json"] == {
            "message": "耳机保修期多久？",
            "session_id": "session-a",
            "rag_mode": "auto",
        }
        assert kwargs["headers"]["Authorization"] == "Bearer demo-token"
        return FakeResponse(
            200,
            {
                "response": "耳机保修期为一年。[E1]",
                "answer": "耳机保修期为一年。[E1]",
                "citations": [
                    {
                        "citation_id": "E1",
                        "filename": "warranty.md",
                        "source_label": "耳机保修政策",
                    }
                ],
                "rag_mode": "agentic",
                "pipeline_status": "ready",
                "terminal_reason": "answer_ready",
                "rag_trace": {"private_prompt": "must-not-leak"},
            },
        )

    client = HermesRagClient(
        base_url="http://hermes.test",
        bearer_token="demo-token",
        requester=requester,
    )

    result = run(client.query("耳机保修期多久？", "session-a"))

    assert result.answer == "耳机保修期为一年。[E1]"
    assert result.mode == "agentic"
    assert result.pipeline_status == "ready"
    assert result.terminal_reason == "answer_ready"
    assert result.evidence_sufficiency == "sufficient"
    assert result.citation_count == 1
    assert result.citations[0].filename == "warranty.md"
    assert "private_prompt" not in result.model_dump_json()
    assert "demo-token" not in result.model_dump_json()


def test_maps_standard_response_without_claiming_evidence_was_graded():
    def requester(method, url, **kwargs):
        return FakeResponse(
            200,
            {
                "response": "七天无理由退货需保持商品完好。",
                "rag_trace": {
                    "retrieved_chunks": [
                        {"filename": "return-policy.md", "page_number": 2}
                    ]
                },
            },
        )

    client = HermesRagClient(
        base_url="http://hermes.test",
        bearer_token="demo-token",
        requester=requester,
    )

    result = run(client.query("退货政策是什么？", "session-b", mode="standard"))

    assert result.mode == "standard"
    assert result.pipeline_status == "ready"
    assert result.evidence_sufficiency == "not_evaluated"
    assert result.citation_count == 1
    assert result.citations[0].filename == "return-policy.md"


def test_refreshes_stale_token_once_after_401():
    calls = []

    def requester(method, url, **kwargs):
        calls.append((url, kwargs.get("headers", {})))
        if url.endswith("/auth/login"):
            assert kwargs["json"] == {"username": "demo", "password": "secret"}
            return FakeResponse(200, {"access_token": "fresh-token"})
        if len([item for item in calls if item[0].endswith("/chat")]) == 1:
            return FakeResponse(401, {"detail": "expired"})
        return FakeResponse(200, {"response": "已刷新", "rag_trace": None})

    client = HermesRagClient(
        base_url="http://hermes.test",
        bearer_token="stale-token",
        username="demo",
        password="secret",
        requester=requester,
    )

    result = run(client.query("问题", "session-c"))

    assert result.answer == "已刷新"
    assert [url for url, _ in calls] == [
        "http://hermes.test/chat",
        "http://hermes.test/auth/login",
        "http://hermes.test/chat",
    ]
    assert calls[-1][1]["Authorization"] == "Bearer fresh-token"


@pytest.mark.parametrize(
    "payload",
    [
        {"response": "", "pipeline_status": "failed"},
        {"response": "", "pipeline_status": "degraded"},
        {"pipeline_status": "ready"},
        ValueError("PRIVATE_INVALID_JSON"),
    ],
)
def test_rejects_unusable_responses_without_exposing_private_details(payload):
    def requester(method, url, **kwargs):
        return FakeResponse(200, payload)

    client = HermesRagClient(
        base_url="http://hermes.test",
        bearer_token="demo-token",
        requester=requester,
    )

    with pytest.raises(HermesRagError) as error:
        run(client.query("问题", "session-d"))

    assert "PRIVATE_INVALID_JSON" not in str(error.value)
    assert "demo-token" not in str(error.value)


def test_preserves_insufficient_evidence_as_a_non_answerable_result():
    def requester(method, url, **kwargs):
        return FakeResponse(
            200,
            {
                "response": "当前知识库证据不足，无法确认。",
                "answer": "当前知识库证据不足，无法确认。",
                "citations": [],
                "rag_mode": "agentic",
                "pipeline_status": "insufficient_evidence",
                "terminal_reason": "insufficient_evidence",
            },
        )

    client = HermesRagClient(
        base_url="http://hermes.test",
        bearer_token="demo-token",
        requester=requester,
    )

    result = run(client.query("不存在的政策", "session-e", mode="agentic"))

    assert result.evidence_sufficiency == "insufficient"
    assert result.pipeline_status == "insufficient_evidence"
    assert result.citation_count == 0
