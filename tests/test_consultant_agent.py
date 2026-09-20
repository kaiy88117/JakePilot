"""Offline orchestration tests for the knowledge consultation agent."""

import asyncio

from agents.consultant_agent import ConsultantAgent


class FakeClassifier:
    def __init__(self, related=True):
        self.related = related

    async def is_consultation_related(self, user_input):
        return self.related


class FakeProcessor:
    async def process_consultation(self, user_input, session_id):
        return "根据知识库证据，耳机整机保修期为一年。"

    async def process_consultation_stream(self, user_input, session_id):
        yield "[REPLY][咨询机器人]根据知识库证据，耳机整机保修期为一年。"

    async def handle_unrelated_request(self, user_input, callback, state):
        yield "[REPLY][咨询机器人]该请求不属于商品售后知识咨询。"


def make_agent(*, related=True):
    agent = ConsultantAgent.__new__(ConsultantAgent)
    agent.session_id = "session-test"
    agent.shared_state = None
    agent.unrelated_callback = None
    agent.consultation_classifier = FakeClassifier(related)
    agent.consultation_processor = FakeProcessor()
    return agent


def test_consult_returns_grounded_ecommerce_answer():
    response = asyncio.run(make_agent().consult("耳机保修多久？"))

    assert "知识库证据" in response
    assert "保修期" in response


def test_consult_stream_routes_related_query_without_network():
    async def collect():
        return "".join(
            [
                token
                async for token in make_agent().consult_stream("耳机保修多久？")
            ]
        )

    response = asyncio.run(collect())

    assert response.startswith("[REPLY][咨询机器人]")
    assert "保修期" in response


def test_consult_stream_uses_bounded_unrelated_reply():
    async def collect():
        return "".join(
            [
                token
                async for token in make_agent(related=False).consult_stream(
                    "讲个笑话"
                )
            ]
        )

    response = asyncio.run(collect())

    assert "不属于商品售后知识咨询" in response
