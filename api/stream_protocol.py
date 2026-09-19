"""Safe server-sent event adapter for the legacy Agent token stream."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterable, AsyncIterator


_REPLY_PREFIX = re.compile(r"^\[REPLY\](?:\[[^\]]+\])?")
_ERROR_PREFIX = re.compile(r"^\[ERROR\]")


def encode_sse(event: str, payload: dict) -> str:
    """Encode one JSON payload as an SSE frame."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


def _route_event(token: str, turn_id: str) -> str | None:
    if "预约任务" in token and "预约机器人" in token:
        return encode_sse(
            "route_selected",
            {
                "turn_id": turn_id,
                "route": "service_appointment",
                "label": "上门服务预约 Agent",
            },
        )
    if "咨询任务" in token and "咨询机器人" in token:
        return encode_sse(
            "route_selected",
            {
                "turn_id": turn_id,
                "route": "knowledge_consultation",
                "label": "知识咨询 Agent",
            },
        )
    return None


async def iter_sse_events(
    tokens: AsyncIterable[str], turn_id: str
) -> AsyncIterator[str]:
    """Convert tagged legacy output into public, structured turn events.

    Thought and signal tokens are internal protocol details. They are never
    forwarded as answer text. Only verified router hand-offs become public
    execution events.
    """
    yield encode_sse("turn_started", {"turn_id": turn_id})
    reply_started = False

    try:
        async for raw_token in tokens:
            token = str(raw_token or "")
            if not token:
                continue

            if token.startswith("[THOUGHT]"):
                route_frame = _route_event(token, turn_id)
                if route_frame:
                    yield route_frame
                continue

            if token.startswith("[SIGNAL]"):
                continue

            if token.startswith("[ERROR]"):
                message = _ERROR_PREFIX.sub("", token, count=1).strip()
                yield encode_sse(
                    "turn_failed",
                    {
                        "turn_id": turn_id,
                        "status": "failed",
                        "message": message or "服务处理失败，请稍后重试",
                    },
                )
                return

            if token.startswith("[REPLY]"):
                reply_started = True
                token = _REPLY_PREFIX.sub("", token, count=1)
            elif token.startswith("[") and not reply_started:
                continue

            if reply_started and token:
                yield encode_sse(
                    "answer_delta", {"turn_id": turn_id, "delta": token}
                )
    except Exception:
        yield encode_sse(
            "turn_failed",
            {
                "turn_id": turn_id,
                "status": "failed",
                "message": "服务连接中断，请稍后重试",
            },
        )
        return

    yield encode_sse("turn_ended", {"turn_id": turn_id, "status": "completed"})
