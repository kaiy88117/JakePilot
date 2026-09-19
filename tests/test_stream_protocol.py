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
    events = _collect(_tokens("[ERROR]预约服务暂时不可用"), "turn-3")

    assert [name for name, _ in events][-1] == "turn_failed"
    assert events[-1][1]["message"] == "预约服务暂时不可用"
    assert all(name != "turn_ended" for name, _ in events)


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
