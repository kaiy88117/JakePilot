"""Reusable stream-to-observation adapter for multi-domain Agent evaluation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, Callable, Iterable
from typing import Any

from evaluation.contracts import EvalCase, EvalObservation, EvalTraceEvent
from evaluation.formal_gate import dataset_digest
from evaluation.order_runner import CaseRun, SuiteRun, summarize_case_runs
from evaluation.verifier import DeterministicVerifier


AgentFactory = Callable[[EvalCase], Any]
StreamTurn = Callable[[Any, str, str], AsyncIterable[str]]
WriteCount = Callable[[Any], int]
CloseAgent = Callable[[Any], None]


def observe_stream_tokens(
    *,
    case_id: str,
    tokens: Iterable[str],
    write_count: int,
) -> EvalObservation:
    """Convert the shared stream protocol into a privacy-minimized observation."""

    events: list[EvalTraceEvent] = []
    answer_parts: list[str] = []
    terminal_status = "completed"
    collecting_reply = False

    for token in tokens:
        if token.startswith("[EVENT]"):
            collecting_reply = False
            try:
                payload = json.loads(token.removeprefix("[EVENT]"))
                data = payload.get("data") or {}
                event = EvalTraceEvent(
                    type=payload["type"],
                    tool=data.get("tool"),
                    status=data.get("status"),
                    step=data.get("step"),
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                events.append(
                    EvalTraceEvent(
                        type="stream_protocol_error",
                        status="failed",
                    )
                )
                terminal_status = "failed"
                continue

            events.append(event)
            if event.type == "turn_finished" and event.status:
                terminal_status = event.status
            elif event.type in {"confirmation_required", "input_required"}:
                terminal_status = "needs_input"
            elif event.type == "handoff_created":
                terminal_status = "handed_off"
            continue

        if token.startswith("[ERROR]"):
            collecting_reply = False
            terminal_status = "failed"
            continue

        if token.startswith("[REPLY]"):
            collecting_reply = True
            answer_parts.append(token.split("]", 2)[-1])
            continue

        if token.startswith("["):
            collecting_reply = False
            continue

        if collecting_reply:
            answer_parts.append(token)

    answer = "".join(answer_parts)
    return EvalObservation(
        case_id=case_id,
        terminal_status=terminal_status,
        answers=(answer,) if answer else (),
        write_count=write_count,
        events=tuple(events),
    )


class StreamCategoryEvalRunner:
    """Run any streaming domain Agent through the common evaluation contract."""

    def __init__(
        self,
        *,
        agent_factory: AgentFactory,
        stream_turn: StreamTurn,
        write_count: WriteCount | None = None,
        close_agent: CloseAgent | None = None,
    ) -> None:
        self.agent_factory = agent_factory
        self.stream_turn = stream_turn
        self.write_count = write_count or (lambda agent: 0)
        self.close_agent = close_agent or (lambda agent: None)
        self.verifier = DeterministicVerifier()

    def run(self, cases: tuple[EvalCase, ...]) -> SuiteRun:
        if not cases:
            raise ValueError("evaluation cases must not be empty")
        case_runs = tuple(self._run_case(case) for case in cases)
        return SuiteRun(
            dataset_version=cases[0].dataset_version,
            dataset_digest=dataset_digest(cases),
            case_runs=case_runs,
            summary=summarize_case_runs(case_runs),
        )

    def _run_case(self, case: EvalCase) -> CaseRun:
        agent = self.agent_factory(case)
        answers: list[str] = []
        events: list[EvalTraceEvent] = []
        terminal_status = "completed"
        try:
            for index, message in enumerate(case.turns):
                tokens = asyncio.run(
                    self._collect(
                        self.stream_turn(agent, message, f"{case.case_id}-{index + 1}")
                    )
                )
                turn = observe_stream_tokens(
                    case_id=case.case_id,
                    tokens=tokens,
                    write_count=0,
                )
                answers.extend(turn.answers)
                events.extend(turn.events)
                terminal_status = turn.terminal_status
                if case.recreate_between_turns and index < len(case.turns) - 1:
                    self.close_agent(agent)
                    agent = self.agent_factory(case)

            observation = EvalObservation(
                case_id=case.case_id,
                terminal_status=terminal_status,
                answers=tuple(answers),
                write_count=self.write_count(agent),
                events=tuple(events),
            )
            return CaseRun(
                case_id=case.case_id,
                category=case.category,
                observation=observation,
                result=self.verifier.verify(case, observation),
            )
        finally:
            self.close_agent(agent)

    @staticmethod
    async def _collect(tokens: AsyncIterable[str]) -> list[str]:
        return [token async for token in tokens]
