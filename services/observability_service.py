"""Privacy-minimized read model for the local observability dashboard."""

from __future__ import annotations

import json
from pathlib import Path


_METRIC_LABELS = {
    "end_to_end_task_success": "端到端任务成功",
    "tool_sequence": "工具序列",
    "forbidden_tools": "禁用工具约束",
    "terminal_status": "终止状态",
    "answer_contains": "关键结果覆盖",
    "write_count": "写入次数",
    "confirmation": "确认门禁",
    "bounded_termination": "有界终止",
}


class ObservabilityService:
    """Build a safe dashboard projection from checkpoints and eval evidence."""

    def __init__(self, journal, reports_dir: str | Path) -> None:
        self.journal = journal
        self.reports_dir = Path(reports_dir)

    def snapshot(self) -> dict:
        turns = self.journal.list_recent(limit=20)
        return {
            "evaluation": self._latest_evaluation(),
            "turn_summary": {
                "total": len(turns),
                "completed": sum(item["status"] == "completed" for item in turns),
                "delivered": sum(
                    item["delivery_status"] == "delivered" for item in turns
                ),
                "business_writes": sum(
                    item["business_write_succeeded"] for item in turns
                ),
            },
            "turns": turns,
        }

    def _latest_evaluation(self) -> dict:
        if not self.reports_dir.exists():
            return self._empty_evaluation()
        reports = sorted(
            self.reports_dir.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for report_path in reports:
            try:
                if report_path.stat().st_size > 2_000_000:
                    continue
                payload = json.loads(report_path.read_text(encoding="utf-8"))
                return self._evaluation_projection(payload)
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        return self._empty_evaluation()

    @staticmethod
    def _evaluation_projection(payload: dict) -> dict:
        if payload.get("suite_kind") != "smoke":
            raise ValueError("unsupported evaluation suite")
        summary = payload["summary"]
        task_success = summary["end_to_end_task_success"]
        metrics = []
        for name, values in summary.items():
            metrics.append(
                {
                    "name": name,
                    "label": _METRIC_LABELS.get(name, name.replace("_", " ")),
                    "passed": int(values["passed"]),
                    "total": int(values["total"]),
                    "rate": float(values["rate"]),
                    "rate_percent": round(float(values["rate"]) * 100, 1),
                }
            )
        return {
            "available": True,
            "formal_benchmark": False,
            "run_id": str(payload["run_id"]),
            "dataset_version": str(payload["dataset_version"]),
            "code_revision": str(payload.get("code_revision") or "unknown")[:7],
            "created_at": str(payload["created_at"]),
            "case_count": int(payload["case_count"]),
            "passed_cases": int(task_success["passed"]),
            "metrics": metrics,
        }

    @staticmethod
    def _empty_evaluation() -> dict:
        return {
            "available": False,
            "formal_benchmark": False,
            "message": "尚未生成 Smoke 评测报告",
        }
