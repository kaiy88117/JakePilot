"""Check whether two formal Agent reports satisfy the release policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation.release_gate import FormalReleaseGate


def _load_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("report must contain a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "校验同一 Golden Set 上的 Baseline 与 JakePilot 正式评测报告；"
            "缺少证据、阈值不达标或出现显著回退时返回非零退出码。"
        )
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()

    try:
        baseline = _load_report(args.baseline)
        candidate = _load_report(args.candidate)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        print(
            json.dumps(
                {
                    "status": "release_gate_error",
                    "eligible": False,
                    "errors": ["invalid_report_json"],
                },
                ensure_ascii=False,
            )
        )
        return 2

    assessment = FormalReleaseGate().assess(
        baseline=baseline,
        candidate=candidate,
    )
    payload = {
        "status": (
            "release_gate_passed"
            if assessment.eligible
            else "release_gate_rejected"
        ),
        "eligible": assessment.eligible,
        "errors": list(assessment.errors),
        "metric_deltas": assessment.metric_deltas,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if assessment.eligible else 2


if __name__ == "__main__":
    raise SystemExit(main())
