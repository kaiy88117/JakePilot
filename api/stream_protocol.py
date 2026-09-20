"""Safe server-sent event adapter for the legacy Agent token stream."""

from __future__ import annotations

import json
import inspect
import re
from collections.abc import AsyncIterable, AsyncIterator, Callable


_TAG_PATTERN = re.compile(
    r"\[(THOUGHT|REPLY|ERROR|SIGNAL)\](?:\[[^\]]+\])?"
)

_PUBLIC_RUNTIME_FIELDS = {
    "tool_started": frozenset({"tool", "step"}),
    "tool_finished": frozenset(
        {"tool", "step", "status", "elapsed_ms", "external_ref"}
    ),
    "confirmation_required": frozenset({"tool", "summary"}),
    "input_required": frozenset({"field", "summary"}),
    "knowledge_retrieval": frozenset(
        {
            "mode",
            "pipeline_status",
            "evidence_sufficiency",
            "citation_count",
            "terminal_reason",
            "fallback",
        }
    ),
    "memory_context": frozenset(
        {
            "working_loaded",
            "episodic_count",
            "profile_count",
            "dropped_count",
        }
    ),
    "decision_model_trace": frozenset(
        {
            "mode",
            "source",
            "latency_ms",
            "validation_status",
            "fallback_reason",
        }
    ),
    "handoff_created": frozenset(
        {"ticket_no", "reason_code", "status"}
    ),
}


def encode_sse(event: str, payload: dict) -> str:
    """Encode one JSON payload as an SSE frame."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


def _route_event(token: str, turn_id: str) -> str | None:
    if "订单售后任务" in token and "订单售后 Agent" in token:
        return encode_sse(
            "route_selected",
            {
                "turn_id": turn_id,
                "route": "order_after_sales",
                "label": "订单售后 Agent",
            },
        )
    if (
        "预约任务" in token and "预约机器人" in token
    ) or (
        "上门服务预约" in token and "服务预约 Agent" in token
    ):
        return encode_sse(
            "route_selected",
            {
                "turn_id": turn_id,
                "route": "service_appointment",
                "label": "上门服务预约 Agent",
            },
        )
    if (
        "咨询任务" in token and "咨询机器人" in token
    ) or (
        "电商售后咨询" in token and "知识咨询 Agent" in token
    ):
        return encode_sse(
            "route_selected",
            {
                "turn_id": turn_id,
                "route": "knowledge_consultation",
                "label": "知识咨询 Agent",
            },
        )
    return None


def _runtime_event(
    token: str, turn_id: str
) -> tuple[str | None, str | None]:
    """Project one internal runtime event onto the public SSE contract."""
    try:
        event = json.loads(token.removeprefix("[EVENT]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, None

    if not isinstance(event, dict):
        return None, None
    event_type = event.get("type")
    allowed_fields = _PUBLIC_RUNTIME_FIELDS.get(event_type)
    data = event.get("data")
    if allowed_fields is None or not isinstance(data, dict):
        return None, None

    public_data = {
        key: value for key, value in data.items() if key in allowed_fields
    }
    public_data["turn_id"] = turn_id
    requested_status = (
        "needs_input"
        if event_type in {"confirmation_required", "input_required"}
        else "handed_off" if event_type == "handoff_created" else None
    )
    return encode_sse(event_type, public_data), requested_status


async def iter_sse_events(
    tokens: AsyncIterable[str],
    turn_id: str,
    on_terminal: Callable[[str, str], object] | None = None,
) -> AsyncIterator[str]:
    """Convert tagged legacy output into public, structured turn events.

    Thought and signal tokens are internal protocol details. They are never
    forwarded as answer text. Only verified router hand-offs become public
    execution events.
    """
    yield encode_sse("turn_started", {"turn_id": turn_id})
    reply_started = False
    terminal_status = "completed"
    public_result = ""
    terminal_notified = False

    def remember_public_answer(fragment: str) -> None:
        nonlocal public_result
        if fragment and len(public_result) < 2000:
            public_result += fragment[: 2000 - len(public_result)]

    async def notify_terminal(status: str) -> None:
        nonlocal terminal_notified
        if terminal_notified or on_terminal is None:
            terminal_notified = True
            return
        terminal_notified = True
        try:
            result = on_terminal(status, public_result)
            if inspect.isawaitable(result):
                await result
        except Exception:
            return

    try:
        async for raw_token in tokens:
            token = str(raw_token or "")
            if not token:
                continue

            if token.startswith("[EVENT]"):
                event_frame, requested_status = _runtime_event(token, turn_id)
                if event_frame:
                    yield event_frame
                if requested_status:
                    terminal_status = requested_status
                continue

            markers = list(_TAG_PATTERN.finditer(token))
            if markers:
                for index, marker in enumerate(markers):
                    section = token[marker.end() : markers[index + 1].start() if index + 1 < len(markers) else len(token)]
                    kind = marker.group(1)
                    if kind == "THOUGHT":
                        route_frame = _route_event(marker.group(0) + section, turn_id)
                        if route_frame:
                            yield route_frame
                    elif kind == "REPLY":
                        reply_started = True
                        if section:
                            remember_public_answer(section)
                            yield encode_sse(
                                "answer_delta",
                                {"turn_id": turn_id, "delta": section},
                            )
                    elif kind == "ERROR":
                        await notify_terminal("failed")
                        yield encode_sse(
                            "turn_failed",
                            {
                                "turn_id": turn_id,
                                "status": "failed",
                                "message": "服务处理失败，请稍后重试",
                            },
                        )
                        return
                continue

            if token.startswith("[ERROR]"):
                await notify_terminal("failed")
                yield encode_sse(
                    "turn_failed",
                    {
                        "turn_id": turn_id,
                        "status": "failed",
                        "message": "服务处理失败，请稍后重试",
                    },
                )
                return

            if token.startswith("[") and not reply_started:
                continue

            if reply_started and token:
                remember_public_answer(token)
                yield encode_sse(
                    "answer_delta", {"turn_id": turn_id, "delta": token}
                )
    except Exception:
        await notify_terminal("failed")
        yield encode_sse(
            "turn_failed",
            {
                "turn_id": turn_id,
                "status": "failed",
                "message": "服务连接中断，请稍后重试",
            },
        )
        return

    await notify_terminal(terminal_status)
    yield encode_sse(
        "turn_ended", {"turn_id": turn_id, "status": terminal_status}
    )
