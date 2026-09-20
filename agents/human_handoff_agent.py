"""Deterministic human-handoff Agent backed by the bounded runtime."""

from __future__ import annotations

import re
from uuid import uuid4

from runtime.contracts import TurnRequest, TurnStatus
from runtime.handoff import register_handoff_tool
from runtime.loop import BoundedAgentRuntime, PlanAction
from runtime.tools import ToolContext, ToolRegistry
from services.handoff_service import HandoffService


_INFORMATIONAL_MARKERS = (
    "上班时间",
    "工作时间",
    "客服电话",
    "电话是多少",
    "怎么联系",
    "如何联系",
    "几点",
    "是否在线",
)
_ACTION_PATTERNS = (
    re.compile(r"转(?:接)?(?:到)?人工(?:客服)?"),
    re.compile(r"(?:找|要|需要|请求|联系|接入|呼叫)(?:一下)?(?:人工|真人)(?:客服)?"),
    re.compile(r"(?:请|需要|要求)?人工处理"),
    re.compile(r"(?:请|需要|要求)?客服介入"),
    re.compile(r"真人客服"),
)


def is_explicit_handoff_request(message: str) -> bool:
    """Match an action request, not a question about customer-service info."""
    normalized = re.sub(r"\s+", "", str(message or "")).strip("，。！？?!")
    if not normalized:
        return False
    if any(marker in normalized for marker in _INFORMATIONAL_MARKERS):
        return False
    if normalized in {"人工", "人工客服", "真人客服"}:
        return True
    return any(pattern.search(normalized) for pattern in _ACTION_PATTERNS)


class _HandoffPlanner:
    async def next_action(self, turn, history):
        return PlanAction.tool(
            "handoff.create",
            {
                "reason_code": "user_requested",
                "summary": "用户明确请求人工客服",
                "verified_facts": [],
                "evidence_refs": [],
                "failed_steps": [],
            },
        )


class HumanHandoffAgent:
    def __init__(
        self,
        session_id: str,
        service: HandoffService,
        tenant_id: str = "demo",
        user_id: str = "user-a",
    ) -> None:
        self.session_id = session_id
        self.service = service
        self.tenant_id = tenant_id
        self.user_id = user_id
        registry = ToolRegistry()
        register_handoff_tool(registry, service)
        self.runtime = BoundedAgentRuntime(registry)

    async def run_stream(self, message: str, turn_id: str | None = None):
        runtime_turn_id = turn_id or f"turn_{uuid4().hex}"
        turn = TurnRequest(
            turn_id=runtime_turn_id,
            session_id=self.session_id,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            message=message,
        )
        context = ToolContext(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            session_id=self.session_id,
            turn_id=runtime_turn_id,
            idempotency_key=f"handoff:{runtime_turn_id}",
        )
        run = await self.runtime.run(turn, _HandoffPlanner(), context)
        ticket_no = ""
        for event in run.events:
            if event.type == "handoff_created":
                ticket_no = str(event.data.get("ticket_no", ""))
            yield f"[EVENT]{event.model_dump_json()}"

        if run.outcome.status == TurnStatus.HANDED_OFF and ticket_no:
            yield (
                "[REPLY][人工接管 Agent]已为您转接人工客服，"
                f"工单号 {ticket_no}。"
            )
            return
        yield "[ERROR]暂时无法创建人工接管工单，请稍后重试"
