"""Fail-closed writer for repeatable, privacy-minimized formal evidence."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from evaluation.contracts import EvalCase
from evaluation.formal_gate import (
    FormalEvaluationGate,
    FormalEvaluationManifest,
)
from evaluation.order_runner import CaseRun, SuiteRun, summarize_case_runs


class FormalEvidenceStore:
    """Validate and atomically persist a three-run formal evaluation report."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)

    def write(
        self,
        *,
        cases: tuple[EvalCase, ...],
        runs: tuple[SuiteRun, ...],
        manifest: FormalEvaluationManifest,
        variant: Literal["baseline", "jakepilot"],
    ) -> Path:
        if len(runs) != 3:
            raise ValueError("formal evidence requires exactly three runs")

        gate = FormalEvaluationGate().assess(cases, manifest)
        if not gate.eligible:
            raise ValueError(
                "formal evaluation gate failed: " + ", ".join(gate.errors)
            )

        expected_case_ids = {case.case_id for case in cases}
        for index, run in enumerate(runs, start=1):
            if run.dataset_version != manifest.dataset_version:
                raise ValueError(f"run {index} dataset version does not match manifest")
            if run.dataset_digest != gate.dataset_digest:
                raise ValueError(f"run {index} dataset digest does not match cases")
            run_case_ids = [case_run.case_id for case_run in run.case_runs]
            if len(run_case_ids) != len(expected_case_ids):
                raise ValueError(f"run {index} case count does not match dataset")
            if len(set(run_case_ids)) != len(run_case_ids):
                raise ValueError(f"run {index} contains duplicate case ids")
            if set(run_case_ids) != expected_case_ids:
                raise ValueError(f"run {index} case ids do not match dataset")
            for case_run in run.case_runs:
                if case_run.observation.case_id != case_run.case_id:
                    raise ValueError(f"run {index} observation case id mismatch")
                if case_run.result.case_id != case_run.case_id:
                    raise ValueError(f"run {index} result case id mismatch")
            if run.summary != summarize_case_runs(run.case_runs):
                raise ValueError(
                    f"run {index} summary does not match case results"
                )

        run_summaries = [run.summary for run in runs]
        payload = {
            "schema_version": "2.0",
            "run_id": self._run_id(),
            "suite_kind": "golden",
            "formal_benchmark": True,
            "repeat_count": 3,
            "dataset_version": manifest.dataset_version,
            "dataset_digest": gate.dataset_digest,
            "code_revision": manifest.code_revision,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "case_count": len(cases),
            "formal_gate": gate.model_dump(mode="json"),
            "run_config": {
                "variant": variant,
                "model_version": manifest.model_version,
                "prompt_version": manifest.prompt_version,
                "tool_fixture_version": manifest.tool_fixture_version,
            },
            "run_summaries": run_summaries,
            "summary": self._aggregate_summaries(run_summaries),
            "runs": [
                {
                    "run_index": index,
                    "cases": [self._case_payload(item) for item in run.case_runs],
                }
                for index, run in enumerate(runs, start=1)
            ],
        }

        self.output_dir.mkdir(parents=True, exist_ok=True)
        target = self.output_dir / f"{payload['run_id']}.json"
        temporary = self.output_dir / f".{payload['run_id']}.tmp"
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, target)
        return target

    @staticmethod
    def _run_id() -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"golden_{timestamp}_{uuid4().hex[:8]}"

    @staticmethod
    def _aggregate_summaries(
        run_summaries: list[dict[str, dict[str, int | float]]],
    ) -> dict[str, dict[str, int | float]]:
        metric_names = sorted(
            {name for summary in run_summaries for name in summary}
        )
        aggregate: dict[str, dict[str, int | float]] = {}
        for metric_name in metric_names:
            if any(metric_name not in summary for summary in run_summaries):
                raise ValueError(
                    f"metric {metric_name} is missing from one or more runs"
                )
            passed = sum(
                int(summary[metric_name]["passed"])
                for summary in run_summaries
            )
            total = sum(
                int(summary[metric_name]["total"])
                for summary in run_summaries
            )
            if total <= 0 or passed < 0 or passed > total:
                raise ValueError(f"metric {metric_name} has invalid counts")
            aggregate[metric_name] = {
                "passed": passed,
                "total": total,
                "rate": round(passed / total, 6),
            }
        return aggregate

    @staticmethod
    def _case_payload(case_run: CaseRun) -> dict:
        answer_bytes = "\n".join(case_run.observation.answers).encode("utf-8")
        return {
            "case_id": case_run.case_id,
            "category": case_run.category,
            "passed": case_run.result.passed,
            "metrics": case_run.result.metrics,
            "failed_assertions": list(case_run.result.failed_assertions),
            "terminal_status": case_run.observation.terminal_status,
            "write_count": case_run.observation.write_count,
            "answer_digest": hashlib.sha256(answer_bytes).hexdigest(),
            "answer_length": len(answer_bytes),
            "trace": [
                event.model_dump(exclude_none=True)
                for event in case_run.observation.events
            ],
        }
