"""Bounded planner-tool loop used by domain agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from runtime.contracts import (
    ExecutionBudget,
    RuntimeEvent,
    TurnOutcome,
    TurnRequest,
    TurnStatus,
)
from runtime.tools import ToolContext, ToolRegistry, ToolResult, canonical_payload_hash
from runtime.trace import TraceRecorder


@dataclass(frozen=True)
class PlanAction:
    kind: Literal["tool", "answer", "replan"]
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    answer: str = ""
    reason: str = ""

    @classmethod
    def tool(cls, name: str, arguments: dict[str, Any]) -> "PlanAction":
        return cls(kind="tool", tool_name=name, arguments=arguments)

    @classmethod
    def answer_now(cls, answer: str) -> "PlanAction":
        return cls(kind="answer", answer=answer)

    @classmethod
    def replan(cls, reason: str) -> "PlanAction":
        return cls(kind="replan", reason=reason)


class Planner(Protocol):
    async def next_action(
        self,
        turn: TurnRequest,
        history: list[tuple[PlanAction, ToolResult | None]],
    ) -> PlanAction: ...


@dataclass(frozen=True)
class RuntimeRun:
    outcome: TurnOutcome
    events: list[RuntimeEvent]


class BoundedAgentRuntime:
    def __init__(
        self,
        registry: ToolRegistry,
        budget: ExecutionBudget | None = None,
    ) -> None:
        self.registry = registry
        self.budget = budget or ExecutionBudget()

    async def run(
        self,
        turn: TurnRequest,
        planner: Planner,
        tool_context: ToolContext,
    ) -> RuntimeRun:
        trace = TraceRecorder(turn.turn_id)
        history: list[tuple[PlanAction, ToolResult | None]] = []
        fingerprints: set[str] = set()
        tool_calls = 0
        replans = 0

        def finish(
            status: TurnStatus,
            answer: str,
            steps: int,
            reason: str | None = None,
        ) -> RuntimeRun:
            event_data: dict[str, Any] = {
                "status": status.value,
                "step": steps,
            }
            if reason:
                event_data["reason"] = reason
            trace.record(RuntimeEvent(type="turn_finished", data=event_data))
            return RuntimeRun(
                outcome=TurnOutcome(
                    status=status,
                    answer=answer,
                    trace_id=trace.trace_id,
                    tool_calls=tool_calls,
                    steps=steps,
                ),
                events=trace.snapshot(),
            )

        for step in range(1, self.budget.max_steps + 1):
            action = await planner.next_action(turn, history)
            trace.record(
                RuntimeEvent(
                    type="plan_selected",
                    data={
                        "step": step,
                        "reason": action.reason,
                    },
                )
            )

            if action.kind == "answer":
                return finish(TurnStatus.COMPLETED, action.answer, step)

            if action.kind == "replan":
                replans += 1
                history.append((action, None))
                if replans > self.budget.max_replans:
                    return finish(
                        TurnStatus.FAILED,
                        "任务无法在当前限制内完成",
                        step,
                        "replan_budget_exhausted",
                    )
                continue

            if not action.tool_name:
                return finish(
                    TurnStatus.FAILED,
                    "执行计划无效",
                    step,
                    "invalid_plan",
                )

            fingerprint = canonical_payload_hash(
                {"tool": action.tool_name, "arguments": action.arguments}
            )
            if fingerprint in fingerprints:
                return finish(
                    TurnStatus.FAILED,
                    "检测到重复工具调用，任务已停止",
                    step,
                    "repeated_tool_call",
                )
            fingerprints.add(fingerprint)

            if tool_calls >= self.budget.max_tool_calls:
                return finish(
                    TurnStatus.FAILED,
                    "工具调用次数已达到上限",
                    step,
                    "tool_budget_exhausted",
                )

            tool_calls += 1
            trace.record(
                RuntimeEvent(
                    type="tool_started",
                    data={"tool": action.tool_name, "step": step},
                )
            )
            result = await self.registry.execute(
                action.tool_name,
                action.arguments,
                tool_context,
            )
            trace.record(
                RuntimeEvent(
                    type="tool_finished",
                    data={
                        "tool": action.tool_name,
                        "status": result.status,
                        "step": step,
                    },
                )
            )
            history.append((action, result))

            if result.status == "confirmation_required":
                return finish(
                    TurnStatus.NEEDS_INPUT,
                    result.public_message,
                    step,
                    "confirmation_required",
                )

        return finish(
            TurnStatus.FAILED,
            "任务步骤已达到上限",
            self.budget.max_steps,
            "step_budget_exhausted",
        )
