"""Versioned, privacy-minimized evidence report writer."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from evaluation.order_runner import SuiteRun


class EvidenceStore:
    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)

    def write(self, report: SuiteRun, *, code_revision: str) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        run_id = f"smoke_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
        target = self.output_dir / f"{run_id}.json"
        temporary = self.output_dir / f".{run_id}.tmp"
        payload = {
            "schema_version": "1.0",
            "run_id": run_id,
            "suite_kind": "smoke",
            "formal_benchmark": False,
            "repeat_count": 1,
            "dataset_version": report.dataset_version,
            "dataset_digest": report.dataset_digest,
            "run_config": {
                "runner": "order_after_sales_deterministic",
                "model_version": "not_applicable",
                "prompt_version": "order-after-sales-runtime-v1",
                "tool_fixture_version": "ecommerce-mock-v1",
            },
            "code_revision": code_revision or "unknown",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "case_count": len(report.case_runs),
            "summary": report.summary,
            "cases": [self._case_payload(item) for item in report.case_runs],
        }
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, target)
        return target

    @staticmethod
    def _case_payload(case_run) -> dict:
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

