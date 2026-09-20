"""Offline deterministic runner for the order-after-sales smoke suite."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from agents.order_after_sales_agent import OrderAfterSalesAgent
from evaluation.contracts import (
    EvalCase,
    EvalObservation,
    EvalResult,
    EvalTraceEvent,
)
from evaluation.verifier import DeterministicVerifier
from runtime.context_engine import ContextEngine
from services.memory_manager import MemoryManager
from services.order_after_sales_service import OrderAfterSalesService


DEFAULT_CASES_PATH = (
    Path(__file__).resolve().parent / "cases" / "order_after_sales_smoke.json"
)


@dataclass(frozen=True)
class CaseRun:
    case_id: str
    category: str
    observation: EvalObservation
    result: EvalResult


@dataclass(frozen=True)
class SuiteRun:
    dataset_version: str
    case_runs: tuple[CaseRun, ...]
    summary: dict[str, dict[str, int | float]]


def load_cases(path: str | Path = DEFAULT_CASES_PATH) -> tuple[EvalCase, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("evaluation case file must contain a JSON array")
    cases = tuple(EvalCase.model_validate(item) for item in payload)
    if not cases:
        raise ValueError("evaluation case file must not be empty")
    versions = {case.dataset_version for case in cases}
    if len(versions) != 1:
        raise ValueError("all cases must use one dataset version")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("case_id values must be unique")
    return cases


class OrderAfterSalesEvalRunner:
    def __init__(self, work_dir: str | Path) -> None:
        self.work_dir = Path(work_dir)
        self.verifier = DeterministicVerifier()

    def run(self, cases: tuple[EvalCase, ...]) -> SuiteRun:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        case_runs = tuple(self._run_case(case) for case in cases)
        return SuiteRun(
            dataset_version=cases[0].dataset_version,
            case_runs=case_runs,
            summary=self._summarize(case_runs),
        )

    def _run_case(self, case: EvalCase) -> CaseRun:
        database_path = self.work_dir / f"{case.case_id}.db"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{database_path}{suffix}").unlink(missing_ok=True)
        database_url = f"sqlite:///{database_path.as_posix()}"
        service = OrderAfterSalesService(database_url)
        service.seed_demo_data()
        memory = MemoryManager(database_url)

        def build_agent() -> OrderAfterSalesAgent:
            return OrderAfterSalesAgent(
                session_id=f"eval-{case.case_id}",
                service=service,
                tenant_id="demo",
                user_id="user-a",
                memory_manager=memory,
                context_engine=ContextEngine(memory),
            )

        agent = build_agent()
        all_events: list[EvalTraceEvent] = []
        answers: list[str] = []
        terminal_status = "completed"
        try:
            for index, turn in enumerate(case.turns):
                tokens = asyncio.run(self._collect(agent, turn))
                turn_events, answer, terminal_status = self._observe_turn(tokens)
                all_events.extend(turn_events)
                answers.append(answer)
                if case.recreate_between_turns and index < len(case.turns) - 1:
                    agent = build_agent()
            observation = EvalObservation(
                case_id=case.case_id,
                terminal_status=terminal_status,
                answers=tuple(answers),
                write_count=service.count_return_requests(),
                events=tuple(all_events),
            )
            return CaseRun(
                case_id=case.case_id,
                category=case.category,
                observation=observation,
                result=self.verifier.verify(case, observation),
            )
        finally:
            memory.close()
            service.close()

    @staticmethod
    async def _collect(agent: OrderAfterSalesAgent, message: str) -> list[str]:
        return [token async for token in agent.run_stream(message)]

    @staticmethod
    def _observe_turn(
        tokens: list[str],
    ) -> tuple[list[EvalTraceEvent], str, str]:
        events: list[EvalTraceEvent] = []
        answer_parts: list[str] = []
        terminal_status = "completed"
        for token in tokens:
            if token.startswith("[EVENT]"):
                payload = json.loads(token.removeprefix("[EVENT]"))
                data = payload.get("data") or {}
                event = EvalTraceEvent(
                    type=payload["type"],
                    tool=data.get("tool"),
                    status=data.get("status"),
                    step=data.get("step"),
                )
                events.append(event)
                if event.type == "turn_finished" and event.status:
                    terminal_status = event.status
                elif event.type in {"input_required", "confirmation_required"}:
                    terminal_status = "needs_input"
                continue
            if token.startswith("[ERROR]"):
                terminal_status = "failed"
                continue
            if token.startswith("[REPLY]"):
                answer_parts.append(token.split("]", 2)[-1])
        return events, "".join(answer_parts), terminal_status

    @staticmethod
    def _summarize(
        case_runs: tuple[CaseRun, ...]
    ) -> dict[str, dict[str, int | float]]:
        total = len(case_runs)

        def ratio(passed: int) -> dict[str, int | float]:
            return {
                "passed": passed,
                "total": total,
                "rate": round(passed / total, 6) if total else 0.0,
            }

        summary = {
            "end_to_end_task_success": ratio(
                sum(item.result.passed for item in case_runs)
            )
        }
        metric_names = tuple(case_runs[0].result.metrics) if case_runs else ()
        for name in metric_names:
            summary[name] = ratio(
                sum(item.result.metrics[name] for item in case_runs)
            )
        return summary
