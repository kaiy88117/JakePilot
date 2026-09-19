import asyncio

import pytest


class _FakeAgent:
    def __init__(self, session_id: str):
        self.session_id = session_id

    async def classify_task_stream(self, user_input: str):
        yield f"{self.session_id}:{user_input}"


def test_registry_reuses_an_agent_only_within_the_same_session():
    from api.chat_handler import AgentSessionRegistry

    registry = AgentSessionRegistry(factory=_FakeAgent, max_sessions=4)

    assert registry.get("session-a") is registry.get("session-a")
    assert registry.get("session-a") is not registry.get("session-b")


def test_registry_evicts_the_least_recently_used_session():
    from api.chat_handler import AgentSessionRegistry

    registry = AgentSessionRegistry(factory=_FakeAgent, max_sessions=2)
    first_a = registry.get("session-a")
    first_b = registry.get("session-b")
    registry.get("session-a")
    registry.get("session-c")

    assert registry.get("session-a") is first_a
    assert registry.get("session-b") is not first_b


def test_stream_uses_the_agent_bound_to_the_requested_session(monkeypatch):
    import api.chat_handler as chat_handler

    registry = chat_handler.AgentSessionRegistry(factory=_FakeAgent, max_sessions=4)
    monkeypatch.setattr(chat_handler, "session_registry", registry)

    async def collect(session_id: str):
        return [
            token
            async for token in chat_handler.ProcessUserInput_stream(
                "hello", session_id=session_id
            )
        ]

    assert asyncio.run(collect("session-a")) == ["session-a:hello"]
    assert asyncio.run(collect("session-b")) == ["session-b:hello"]


def test_same_session_requests_are_serialized(monkeypatch):
    import api.chat_handler as chat_handler

    class StatefulAgent:
        def __init__(self, session_id: str):
            self.active = 0
            self.max_active = 0

        async def classify_task_stream(self, user_input: str):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            yield user_input
            self.active -= 1

    registry = chat_handler.AgentSessionRegistry(factory=StatefulAgent, max_sessions=2)
    monkeypatch.setattr(chat_handler, "session_registry", registry)

    async def collect(message: str):
        return [
            token
            async for token in chat_handler.ProcessUserInput_stream(
                message, session_id="session-a"
            )
        ]

    async def run_both():
        await asyncio.gather(collect("first"), collect("second"))

    asyncio.run(run_both())
    assert registry.get("session-a").max_active == 1


def test_registry_never_evicts_a_busy_session():
    from api.chat_handler import AgentSessionRegistry, SessionRegistryFull

    registry = AgentSessionRegistry(factory=_FakeAgent, max_sessions=1)

    async def verify():
        async with registry.acquire("session-a") as first_a:
            with pytest.raises(SessionRegistryFull):
                registry.get("session-b")
            assert registry.get("session-a") is first_a

        assert registry.get("session-b").session_id == "session-b"

    asyncio.run(verify())
