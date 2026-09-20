"""Bounded control tool for creating a human-handoff ticket."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from runtime.tools import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolRisk,
    ToolSpec,
)
from services.handoff_service import HandoffService


HandoffReason = Literal[
    "user_requested",
    "evidence_insufficient",
    "risk_threshold",
    "tool_failure",
    "intent_unstable",
]


class HandoffArgs(BaseModel):
    reason_code: HandoffReason
    summary: str = Field(min_length=1, max_length=240)
    verified_facts: list[str] = Field(default_factory=list, max_length=20)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    failed_steps: list[str] = Field(default_factory=list, max_length=20)


def register_handoff_tool(
    registry: ToolRegistry,
    service: HandoffService,
) -> None:
    async def create_handoff(
        arguments: BaseModel,
        context: ToolContext,
    ) -> ToolResult:
        args = HandoffArgs.model_validate(arguments)
        ticket = service.create_or_get(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            session_id=context.session_id,
            turn_id=context.turn_id,
            reason_code=args.reason_code,
            summary=args.summary,
            verified_facts=args.verified_facts,
            evidence_refs=args.evidence_refs,
            failed_steps=args.failed_steps,
        )
        return ToolResult(
            status="handed_off",
            data={
                "ticket_no": ticket["ticket_no"],
                "reason_code": ticket["reason_code"],
                "status": ticket["status"],
            },
            public_message="已创建人工接管工单",
        )

    registry.register(
        ToolSpec(
            name="handoff.create",
            args_model=HandoffArgs,
            risk=ToolRisk.CONTROL,
            handler=create_handoff,
        )
    )
