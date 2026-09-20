"""Run a frozen component evaluation against the configured local endpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from appointment_decision.client import LocalDecisionClient
from appointment_training.evaluator import EvaluationMetadata, evaluate_model
from appointment_training.evidence import write_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--prompt-version", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--hardware-label", required=True)
    parser.add_argument("--formal", action="store_true")
    args = parser.parse_args()

    client = LocalDecisionClient.from_env()
    report = evaluate_model(
        args.input,
        client.complete,
        metadata=EvaluationMetadata(
            code_revision=args.code_revision,
            prompt_version=args.prompt_version,
            model_version=args.model_version,
            hardware_label=args.hardware_label,
        ),
        formal=args.formal,
    )
    report_path = write_report(report, args.output_dir)
    print(
        json.dumps(
            {
                "report_file": str(report_path),
                "sample_count": report.sample_count,
                "formal_benchmark": report.formal_benchmark,
                "promotion_eligible": report.promotion_eligible,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
