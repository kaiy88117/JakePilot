import asyncio
import json

import pytest

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


def _collect_with_terminal(source, turn_id: str, on_terminal):
    async def collect():
        return [
            _decode(frame)
            async for frame in iter_sse_events(
                source, turn_id, on_terminal=on_terminal
            )
        ]

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


def test_completed_stream_calls_terminal_hook_with_bounded_public_answer():
    terminal = []
    long_tail = "a" * 2100

    events = _collect_with_terminal(
        _tokens("[REPLY][咨询机器人]已完成", long_tail),
        "turn-terminal",
        lambda status, result: terminal.append((status, result)),
    )

    assert events[-1] == (
        "turn_ended",
        {"turn_id": "turn-terminal", "status": "completed"},
    )
    assert terminal == [("completed", ("已完成" + long_tail)[:2000])]


def test_failed_and_needs_input_streams_report_truthful_terminal_status():
    failed = []
    needs_input = []

    failed_events = _collect_with_terminal(
        _tokens("[ERROR]private"),
        "turn-failed-hook",
        lambda status, result: failed.append((status, result)),
    )
    needs_input_events = _collect_with_terminal(
        _tokens(
            '[EVENT]{"type":"input_required","data":{"field":"reason","summary":"请补充退货原因"}}',
            "[REPLY][订单售后 Agent]请补充退货原因。",
        ),
        "turn-needs-input-hook",
        lambda status, result: needs_input.append((status, result)),
    )

    assert failed_events[-1][0] == "turn_failed"
    assert failed == [("failed", "")]
    assert needs_input_events[-1][1]["status"] == "needs_input"
    assert needs_input == [("needs_input", "请补充退货原因。")]


def test_terminal_hook_failure_never_changes_public_stream():
    def failing_hook(status, result):
        raise RuntimeError("private callback failure")

    events = _collect_with_terminal(
        _tokens("[REPLY][咨询机器人]公开回答"),
        "turn-hook-failure",
        failing_hook,
    )

    assert events[-1] == (
        "turn_ended",
        {"turn_id": "turn-hook-failure", "status": "completed"},
    )


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

    async def fake_processor(
        message: str,
        session_id: str | None = None,
        turn_id: str | None = None,
    ):
        captured["message"] = message
        captured["session_id"] = session_id
        captured["turn_id"] = turn_id
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
    assert captured == {
        "message": "查询订单",
        "session_id": "session-a",
        "turn_id": "turn-session",
    }
    assert frames[-1][0] == "turn_ended"


def test_build_agent_event_stream_submits_server_scoped_completion():
    from web.routes import build_agent_event_stream

    class RecordingDispatcher:
        def __init__(self):
            self.items = []

        def submit(self, completion):
            self.items.append(completion)
            return True

    dispatcher = RecordingDispatcher()

    async def fake_processor(message: str):
        yield "[THOUGHT][归类机器人] 已识别为订单售后任务，转交订单售后 Agent 处理。"
        yield "[REPLY][订单售后 Agent]退货申请已提交，申请编号为 AS-001。"

    async def collect():
        return [
            _decode(frame)
            async for frame in build_agent_event_stream(
                "订单 JP20260920001 退货已确认",
                turn_id="turn-completion",
                session_id="session-a",
                processor=fake_processor,
                memory_dispatcher=dispatcher,
            )
        ]

    frames = asyncio.run(collect())

    assert frames[-1][1]["status"] == "completed"
    assert len(dispatcher.items) == 1
    completion = dispatcher.items[0]
    assert completion.turn_id == "turn-completion"
    assert completion.session_id == "session-a"
    assert completion.tenant_id == "demo"
    assert completion.user_id == "user-a"
    assert completion.route == "order_after_sales"
    assert completion.public_result == "退货申请已提交，申请编号为 AS-001。"


def test_hidden_verified_memory_fact_is_attached_to_terminal_completion():
    from web.routes import build_agent_event_stream

    class RecordingDispatcher:
        def __init__(self):
            self.items = []

        def submit(self, completion):
            self.items.append(completion)
            return True

    dispatcher = RecordingDispatcher()

    async def fake_processor(message: str):
        yield "[THOUGHT][归类机器人] 已识别为订单售后任务，转交订单售后 Agent 处理。"
        yield (
            '[EVENT]{"type":"memory_fact","data":'
            '{"event_type":"return_requested",'
            '"candidate_key":"return:JP20260920001:AS-001",'
            '"summary":"订单 JP20260920001 已提交退货申请，申请编号 AS-001",'
            '"outcome":"completed",'
            '"entity_refs":["JP20260920001"]}}'
        )
        yield "[REPLY][订单售后 Agent]退货申请已提交。"

    async def collect():
        return [
            _decode(frame)
            async for frame in build_agent_event_stream(
                "确认提交",
                turn_id="turn-verified-fact",
                session_id="session-a",
                processor=fake_processor,
                memory_dispatcher=dispatcher,
            )
        ]

    frames = asyncio.run(collect())

    assert all(name != "memory_fact" for name, _ in frames)
    assert "JP20260920001" not in json.dumps(frames, ensure_ascii=False)
    completion = dispatcher.items[0]
    assert len(completion.verified_facts) == 1
    assert completion.verified_facts[0].candidate_key == (
        "return:JP20260920001:AS-001"
    )


def test_active_order_flow_is_inferred_from_safe_tool_event():
    from web.routes import build_agent_event_stream

    class RecordingDispatcher:
        def __init__(self):
            self.items = []

        def submit(self, completion):
            self.items.append(completion)
            return True

    dispatcher = RecordingDispatcher()

    async def fake_processor(message: str):
        yield '[EVENT]{"type":"tool_finished","data":{"tool":"return.create","step":2,"status":"succeeded"}}'
        yield "[REPLY][订单售后 Agent]退货申请已提交，申请编号为 AS-002。"

    async def collect():
        return [
            _decode(frame)
            async for frame in build_agent_event_stream(
                "确认提交",
                turn_id="turn-active-order",
                session_id="session-a",
                processor=fake_processor,
                memory_dispatcher=dispatcher,
            )
        ]

    asyncio.run(collect())

    assert dispatcher.items[0].route == "order_after_sales"


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


def test_build_agent_event_stream_persists_delivered_checkpoint(tmp_path):
    from services.turn_journal import TurnJournal
    from web.routes import build_agent_event_stream

    journal = TurnJournal(
        f"sqlite:///{(tmp_path / 'delivered-turn.db').as_posix()}"
    )

    async def fake_processor(message: str):
        yield "[REPLY][咨询机器人]已完成"

    async def collect():
        return [
            _decode(frame)
            async for frame in build_agent_event_stream(
                "测试",
                turn_id="turn-delivered",
                session_id="session-a",
                processor=fake_processor,
                turn_journal=journal,
            )
        ]

    frames = asyncio.run(collect())
    checkpoint = journal.get("turn-delivered", "session-a")

    assert frames[-1][0] == "turn_ended"
    assert checkpoint["status"] == "completed"
    assert checkpoint["delivery_status"] == "delivered"
    assert checkpoint["last_event_type"] == "turn_ended"


def test_build_agent_event_stream_marks_cancelled_after_successful_write(
    tmp_path,
):
    from services.turn_journal import TurnJournal
    from web.routes import build_agent_event_stream

    journal = TurnJournal(
        f"sqlite:///{(tmp_path / 'cancelled-turn.db').as_posix()}"
    )

    async def cancelled_processor(message: str):
        yield '[EVENT]{"type":"tool_finished","data":{"tool":"return.create","status":"succeeded","step":2}}'
        raise asyncio.CancelledError()

    async def consume():
        frames = []
        async for frame in build_agent_event_stream(
            "确认提交",
            turn_id="turn-cancelled",
            session_id="session-a",
            processor=cancelled_processor,
            turn_journal=journal,
        ):
            frames.append(_decode(frame))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(consume())

    checkpoint = journal.get("turn-cancelled", "session-a")
    assert checkpoint["status"] == "cancelled"
    assert checkpoint["delivery_status"] == "disconnected"
    assert checkpoint["business_write_succeeded"] is True


def test_turn_checkpoint_endpoint_requires_matching_session(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from services.turn_journal import TurnJournal
    import web.routes as routes

    journal = TurnJournal(
        f"sqlite:///{(tmp_path / 'checkpoint-api.db').as_posix()}"
    )
    journal.begin(
        turn_id="turn-api",
        tenant_id="demo",
        user_id="user-a",
        session_id="session-a",
    )
    monkeypatch.setattr(routes, "_turn_journal", journal)

    checkpoint = asyncio.run(
        routes.turn_checkpoint_endpoint("turn-api", "session-a")
    )
    assert checkpoint["turn_id"] == "turn-api"

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(routes.turn_checkpoint_endpoint("turn-api", "session-b"))
    assert exc_info.value.status_code == 404


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
    assert events[-1][0] == "turn_ended"
    assert events[-1][1]["status"] == "needs_input"


def test_missing_slot_event_marks_the_turn_as_needing_input():
    events = _collect(
        _tokens(
            '[EVENT]{"type":"input_required","data":{"field":"reason","summary":"请补充退货原因","private":"hidden"}}',
            "[REPLY][订单售后 Agent]请补充退货原因。",
        ),
        "turn-input",
    )

    required = next(
        payload for name, payload in events if name == "input_required"
    )
    assert required == {
        "turn_id": "turn-input",
        "field": "reason",
        "summary": "请补充退货原因",
    }
    assert events[-1][1]["status"] == "needs_input"


def test_knowledge_event_exposes_only_safe_evidence_summary():
    events = _collect(
        _tokens(
            '[EVENT]{"type":"knowledge_retrieval","data":{"mode":"agentic","pipeline_status":"ready","evidence_sufficiency":"sufficient","citation_count":2,"terminal_reason":"answer_ready","fallback":false,"bearer_token":"PRIVATE_TOKEN","raw_trace":{"prompt":"PRIVATE_PROMPT"}}}',
            "[REPLY][咨询机器人]有证据的回答",
        ),
        "turn-knowledge",
    )

    knowledge = next(
        payload for name, payload in events if name == "knowledge_retrieval"
    )
    assert knowledge == {
        "turn_id": "turn-knowledge",
        "mode": "agentic",
        "pipeline_status": "ready",
        "evidence_sufficiency": "sufficient",
        "citation_count": 2,
        "terminal_reason": "answer_ready",
        "fallback": False,
    }
    serialized = json.dumps(events, ensure_ascii=False)
    assert "PRIVATE_TOKEN" not in serialized
    assert "PRIVATE_PROMPT" not in serialized


def test_memory_context_event_only_exposes_aggregate_fields():
    events = _collect(
        _tokens(
            '[EVENT]{"type":"memory_context","data":{"working_loaded":true,"episodic_count":2,"profile_count":1,"dropped_count":3,"segments":[{"content":"private"}]}}',
            "[REPLY][订单售后 Agent]已恢复任务",
        ),
        "turn-memory",
    )

    memory = next(payload for name, payload in events if name == "memory_context")
    assert memory == {
        "turn_id": "turn-memory",
        "working_loaded": True,
        "episodic_count": 2,
        "profile_count": 1,
        "dropped_count": 3,
    }
    assert "private" not in json.dumps(events, ensure_ascii=False)


def test_handoff_event_projects_only_safe_fields_and_terminal_status():
    events = _collect(
        _tokens(
            '[EVENT]{"type":"handoff_created","data":{"ticket_no":"HO-0001","reason_code":"user_requested","status":"open","raw_message":"secret","summary":"private"}}',
            "[REPLY][人工接管 Agent]已为您转接人工客服，工单号 HO-0001。",
        ),
        "turn-handoff",
    )

    handoff = next(
        payload for name, payload in events if name == "handoff_created"
    )
    assert handoff == {
        "turn_id": "turn-handoff",
        "ticket_no": "HO-0001",
        "reason_code": "user_requested",
        "status": "open",
    }
    assert events[-1] == (
        "turn_ended",
        {"turn_id": "turn-handoff", "status": "handed_off"},
    )
    serialized = json.dumps(events, ensure_ascii=False)
    assert "raw_message" not in serialized
    assert "private" not in serialized
