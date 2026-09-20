"""Orchestrate a complete three-repeat formal evaluation run."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from evaluation.contracts import EvalCase
from evaluation.formal_evidence_store import FormalEvidenceStore
from evaluation.formal_gate import FormalEvaluationGate, FormalEvaluationManifest
from evaluation.order_runner import SuiteRun


class FormalSuiteRunner(Protocol):
    tool_fixture_version: str

    def run(self, cases: tuple[EvalCase, ...]) -> SuiteRun: ...


class FormalEvaluationExecutor:
    """Validate first, then run exactly three repeats and persist evidence."""

    def __init__(
        self,
        *,
        runner: FormalSuiteRunner,
        evidence_store: FormalEvidenceStore,
    ) -> None:
        self.runner = runner
        self.evidence_store = evidence_store

    def run(
        self,
        *,
        cases: tuple[EvalCase, ...],
        manifest: FormalEvaluationManifest,
        variant: Literal["baseline", "jakepilot"],
    ) -> Path:
        assessment = FormalEvaluationGate().assess(cases, manifest)
        if not assessment.eligible:
            raise ValueError(
                "formal evaluation gate failed: "
                + ", ".join(assessment.errors)
            )
        runner_fixture_version = getattr(
            self.runner,
            "tool_fixture_version",
            None,
        )
        if runner_fixture_version != manifest.tool_fixture_version:
            raise ValueError("formal evaluation tool fixture version mismatch")

        runs = tuple(self.runner.run(cases) for _ in range(manifest.repeats))
        return self.evidence_store.write(
            cases=cases,
            runs=runs,
            manifest=manifest,
            variant=variant,
        )
