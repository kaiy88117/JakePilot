"""Persist privacy-safe appointment model reports as immutable evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .evaluator import AppointmentModelReport


def write_report(report: AppointmentModelReport, output_dir: Path) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"appointment_model_{timestamp}_{report.dataset_sha256[:8]}.json"
    path.write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return path
