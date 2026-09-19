import asyncio
import json

from api.stream_protocol import iter_sse_events


async def _tokens(*values: str):
    for value in values:
        yield value


def _decode(frame: str) -> tuple[str, dict]:
    lines = frame.strip().splitlines()
    return (
        lines[0].removeprefix("event: "),
        json.loads(lines[1].removeprefix("data: ")),
    )


def _collect(source, turn_id: str) -> list[tuple[str, dict]]:
    async def collect():
        return [_decode(frame) async for frame in iter_sse_events(source, turn_id)]

    return asyncio.run(collect())


def test_converts_route_and_reply_without_exposing_thought():
    events = _collect(
        _tokens(
            "[THOUGHT][归类机器人] 归类机器人：我发现这是一个预约任务，我将转给预约机器人处理。",
            "[REPLY][预约机器人]请提供上门时间",
            "。",
        ),
        "turn-1",
    )

    assert [name for name, _ in events] == [
        "turn_started",
        "route_selected",
        "answer_delta",
        "answer_delta",
        "turn_ended",
    ]
    assert events[1][1]["route"] == "service_appointment"
    serialized = json.dumps(events, ensure_ascii=False)
    assert "THOUGHT" not in serialized
    assert "我发现这是" not in serialized


def test_converts_ecommerce_route_wording_to_public_route_event():
    events = _collect(
        _tokens(
            "[THOUGHT][归类机器人] 已识别为电商售后咨询，转交知识咨询 Agent 处理。",
            "[REPLY][咨询机器人]保修期为12个月。",
        ),
        "turn-ecommerce",
    )

    assert [name for name, _ in events] == [
        "turn_started",
        "route_selected",
        "answer_delta",
        "turn_ended",
    ]
    assert events[1][1]["route"] == "knowledge_consultation"


def test_internal_signal_is_not_sent_as_answer():
    events = _collect(
        _tokens(
            "[SIGNAL]recommendation_pending",
            "[REPLY][预约机器人]请确认该时段",
        ),
        "turn-2",
    )

    answer = "".join(payload.get("delta", "") for _, payload in events)
    assert "SIGNAL" not in answer
    assert answer == "请确认该时段"


def test_error_ends_turn_as_failed():
    events = _collect(_tokens("[ERROR]SYNTHETIC_PRIVATE_DIAGNOSTIC"), "turn-3")

    assert [name for name, _ in events][-1] == "turn_failed"
    assert events[-1][1]["message"] == "服务处理失败，请稍后重试"
    assert "SYNTHETIC_PRIVATE_DIAGNOSTIC" not in json.dumps(events)
    assert all(name != "turn_ended" for name, _ in events)


def test_combined_thought_and_reply_preserves_public_answer():
    events = _collect(
        _tokens(
            "[THOUGHT][归类机器人] 这是咨询任务，转给咨询机器人处理。"
            "[REPLY][咨询机器人]保修期为一年"
        ),
        "turn-combined",
    )

    assert events[1][0] == "route_selected"
    answer = "".join(payload.get("delta", "") for _, payload in events)
    assert answer == "保修期为一年"
    assert "THOUGHT" not in json.dumps(events, ensure_ascii=False)


def test_consultation_model_exception_uses_error_channel_without_diagnostic():
    from agents.consultant.response_generator import ResponseGenerator

    class FailingModel:
        async def ainvoke(self, messages):
            raise RuntimeError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    async def collect():
        generator = ResponseGenerator(FailingModel())
        return [
            token
            async for token in generator.generate_response_stream("问题", [])
        ]

    tokens = asyncio.run(collect())
    assert tokens == ["[ERROR]咨询服务暂时不可用，请稍后重试"]


def test_consultation_retrieval_exception_uses_generic_error_channel():
    from agents.consultant.consultation_processor import ConsultationProcessor

    class FailingRetriever:
        async def search_knowledge(self, user_input, top_k):
            raise RuntimeError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    async def collect():
        processor = ConsultationProcessor(FailingRetriever(), None, None)
        return [
            token
            async for token in processor.process_consultation_stream(
                "问题", "session-test"
            )
        ]

    tokens = asyncio.run(collect())
    assert tokens == ["[ERROR]咨询服务暂时不可用，请稍后重试"]


def test_build_agent_event_stream_uses_supplied_processor():
    from web.routes import build_agent_event_stream

    async def fake_processor(message: str):
        assert message == "查询耳机保修政策"
        yield "[REPLY][咨询机器人]保修期为一年"

    async def collect():
        return [
            _decode(frame)
            async for frame in build_agent_event_stream(
                "查询耳机保修政策",
                turn_id="turn-test",
                processor=fake_processor,
            )
        ]

    frames = asyncio.run(collect())
    assert frames[0] == ("turn_started", {"turn_id": "turn-test"})
    assert frames[1][1]["delta"] == "保修期为一年"
    assert frames[-1][0] == "turn_ended"


def test_build_agent_event_stream_forwards_session_id(monkeypatch):
    import api.chat_handler as chat_handler
    from web.routes import build_agent_event_stream

    captured = {}

    async def fake_processor(message: str, session_id: str | None = None):
        captured["message"] = message
        captured["session_id"] = session_id
        yield "[REPLY][咨询机器人]已隔离"

    monkeypatch.setattr(chat_handler, "ProcessUserInput_stream", fake_processor)

    async def collect():
        return [
            _decode(frame)
            async for frame in build_agent_event_stream(
                "查询订单",
                turn_id="turn-session",
                session_id="session-a",
            )
        ]

    frames = asyncio.run(collect())
    assert captured == {"message": "查询订单", "session_id": "session-a"}
    assert frames[-1][0] == "turn_ended"


def test_build_agent_event_stream_converts_processor_startup_failure():
    from web.routes import build_agent_event_stream

    def failing_processor(message: str):
        raise RuntimeError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    async def collect():
        return [
            _decode(frame)
            async for frame in build_agent_event_stream(
                "测试问题",
                turn_id="turn-startup-failure",
                processor=failing_processor,
            )
        ]

    frames = asyncio.run(collect())
    assert [name for name, _ in frames] == ["turn_started", "turn_failed"]
    assert frames[-1][1]["message"] == "服务处理失败，请稍后重试"
    assert "SYNTHETIC_PRIVATE_DIAGNOSTIC" not in json.dumps(frames)


def test_runtime_tool_events_are_allowlisted_and_arguments_are_hidden():
    events = _collect(
        _tokens(
            "[THOUGHT][归类机器人] 已识别为订单售后任务，转交订单售后 Agent 处理。",
            '[EVENT]{"type":"tool_started","data":{"tool":"logistics.get","step":1,"arguments":{"order_id":"JP20260919001"},"phone":"13800138000"}}',
            '[EVENT]{"type":"tool_finished","data":{"tool":"logistics.get","step":1,"status":"succeeded","private_result":"secret"}}',
            "[REPLY][订单售后 Agent]物流查询完成",
        ),
        "turn-tools",
    )

    assert [name for name, _ in events] == [
        "turn_started",
        "route_selected",
        "tool_started",
        "tool_finished",
        "answer_delta",
        "turn_ended",
    ]
    assert events[1][1]["route"] == "order_after_sales"
    assert events[2][1] == {
        "turn_id": "turn-tools",
        "tool": "logistics.get",
        "step": 1,
    }
    serialized = json.dumps(events, ensure_ascii=False)
    assert "arguments" not in serialized
    assert "13800138000" not in serialized
    assert "private_result" not in serialized


def test_confirmation_event_exposes_summary_but_not_frozen_arguments():
    events = _collect(
        _tokens(
            '[EVENT]{"type":"confirmation_required","data":{"tool":"return.create","summary":"为订单提交退货申请","order_id":"JP20260919002","payload_hash":"private"}}',
            "[REPLY][订单售后 Agent]请确认提交",
        ),
        "turn-confirm",
    )

    confirmation = next(payload for name, payload in events if name == "confirmation_required")
    assert confirmation == {
        "turn_id": "turn-confirm",
        "tool": "return.create",
        "summary": "为订单提交退货申请",
    }
