"""Deterministic human-handoff Agent backed by the bounded runtime."""

from __future__ import annotations

import re
from uuid import uuid4

from runtime.contracts import TurnRequest, TurnStatus
from runtime.handoff import register_handoff_tool
from runtime.loop import BoundedAgentRuntime, PlanAction
from runtime.tools import ToolContext, ToolRegistry
from services.handoff_service import HandoffService


_ACTION_PATTERNS = (
    re.compile(r"转(?:接)?(?:到)?人工(?:客服)?"),
    re.compile(r"(?:找|要|需要|请求|联系|接入|呼叫)(?:一下)?(?:人工|真人)(?:客服)?"),
    re.compile(r"(?:请|需要|要求)?人工处理"),
    re.compile(r"(?:请|需要|要求)?客服介入"),
)
_NEGATED_ACTION_PREFIX = re.compile(
    r"(?:不要|不用|不需要|不想|无需|暂不|别(?:给我)?)(?:再|立即|马上)?$"
)
_INFORMATIONAL_PREFIX = re.compile(r"(?:怎么|如何|什么是|为什么)$")


def is_explicit_handoff_request(message: str) -> bool:
    """Match an action request, not a question about customer-service info."""
    normalized = re.sub(r"\s+", "", str(message or "")).strip("，。！？?!")
    if not normalized:
        return False
    if normalized in {"人工", "人工客服", "真人客服"}:
        return True
    for pattern in _ACTION_PATTERNS:
        for match in pattern.finditer(normalized):
            prefix = normalized[max(0, match.start() - 8) : match.start()]
            if _NEGATED_ACTION_PREFIX.search(prefix):
                continue
            if _INFORMATIONAL_PREFIX.search(prefix):
                continue
            return True
    return False


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
