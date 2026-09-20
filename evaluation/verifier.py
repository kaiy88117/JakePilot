"""Deterministic trace verifier; no LLM judge is used here."""

from __future__ import annotations

from evaluation.contracts import EvalCase, EvalObservation, EvalResult


class DeterministicVerifier:
    def verify(self, case: EvalCase, observation: EvalObservation) -> EvalResult:
        tool_sequence = tuple(
            event.tool
            for event in observation.events
            if event.type == "tool_started" and event.tool is not None
        )
        forbidden = set(case.forbidden_tools)
        terminal_events = [
            event for event in observation.events if event.type == "turn_finished"
        ]
        combined_answer = "\n".join(observation.answers)
        metrics = {
            "tool_sequence": tool_sequence == case.expected_tools,
            "forbidden_tools": not any(tool in forbidden for tool in tool_sequence),
            "terminal_status": (
                observation.terminal_status == case.expected_terminal_status
            ),
            "answer_contains": all(
                expected in combined_answer for expected in case.answer_contains
            ),
            "write_count": observation.write_count == case.expected_write_count,
            "confirmation": (
                not case.requires_confirmation
                or any(
                    event.type == "confirmation_required"
                    for event in observation.events
                )
            ),
            "bounded_termination": bool(terminal_events)
            and all(
                event.step is not None and event.step <= case.max_steps
                for event in terminal_events
            ),
        }
        failed = tuple(name for name, passed in metrics.items() if not passed)
        return EvalResult(
            case_id=case.case_id,
            passed=not failed,
            metrics=metrics,
            failed_assertions=failed,
        )
