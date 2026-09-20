"""Run JakePilot's deterministic offline Agent smoke evaluation."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from evaluation.evidence_store import EvidenceStore
from evaluation.formal_gate import FormalEvaluationGate, FormalEvaluationManifest
from evaluation.order_runner import OrderAfterSalesEvalRunner, load_cases


def _git_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "运行离线 Agent Smoke Case 并生成 Evidence Report；"
            "该小样本结果不是正式 Golden Set 指标。"
        )
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=None,
        help="可选 Case JSON 路径；默认使用仓库内订单售后 Smoke Set。",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("artifacts/eval/work"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/eval/reports"),
    )
    parser.add_argument("--code-revision", default=None)
    parser.add_argument(
        "--formal",
        action="store_true",
        help="启用正式评测门禁；不满足数据集与版本条件时不运行、不写报告。",
    )
    parser.add_argument("--model-version", default="not_pinned")
    parser.add_argument("--prompt-version", default="not_pinned")
    parser.add_argument("--tool-fixture-version", default="not_pinned")
    args = parser.parse_args()

    cases = load_cases(args.cases) if args.cases else load_cases()
    if args.formal:
        manifest = FormalEvaluationManifest(
            dataset_version=cases[0].dataset_version,
            model_version=args.model_version,
            prompt_version=args.prompt_version,
            tool_fixture_version=args.tool_fixture_version,
            repeats=3,
        )
        assessment = FormalEvaluationGate().assess(cases, manifest)
        if not assessment.eligible:
            print(
                json.dumps(
                    {
                        "status": "formal_gate_rejected",
                        "formal_benchmark": False,
                        "case_count": assessment.case_count,
                        "dataset_digest": assessment.dataset_digest,
                        "errors": list(assessment.errors),
                    },
                    ensure_ascii=False,
                )
            )
            return 2
        print(
            json.dumps(
                {
                    "status": "formal_runner_not_configured",
                    "formal_benchmark": False,
                    "case_count": assessment.case_count,
                    "dataset_digest": assessment.dataset_digest,
                    "errors": [
                        "multi_domain_runner_and_baseline_are_required"
                    ],
                },
                ensure_ascii=False,
            )
        )
        return 2
    suite = OrderAfterSalesEvalRunner(args.work_dir).run(cases)
    report_path = EvidenceStore(args.output_dir).write(
        suite,
        code_revision=args.code_revision or _git_revision(),
    )
    passed_cases = sum(item.result.passed for item in suite.case_runs)
    output = {
        "suite_kind": "smoke",
        "dataset_version": suite.dataset_version,
        "case_count": len(suite.case_runs),
        "passed_cases": passed_cases,
        "report_file": report_path.name,
        "formal_benchmark": False,
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0 if passed_cases == len(suite.case_runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
