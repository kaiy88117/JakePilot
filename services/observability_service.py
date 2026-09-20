"""Privacy-minimized read model for the local observability dashboard."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.release_gate import formal_report_errors


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

_HANDOFF_REASON_LABELS = {
    "user_requested": "用户主动请求",
    "evidence_insufficient": "证据不足",
    "risk_threshold": "风险升级",
    "tool_failure": "工具执行失败",
    "intent_unstable": "意图无法稳定判断",
}


class ObservabilityService:
    """Build a safe dashboard projection from checkpoints and eval evidence."""

    def __init__(
        self,
        journal,
        reports_dir: str | Path,
        handoff_reader=None,
        handoff_tenant_id: str = "demo",
    ) -> None:
        self.journal = journal
        self.reports_dir = Path(reports_dir)
        self.handoff_reader = handoff_reader
        self.handoff_tenant_id = handoff_tenant_id

    def snapshot(self) -> dict:
        turns = self.journal.list_recent(limit=20)
        handoffs = self._recent_handoffs()
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
            "handoff_summary": {
                "total": len(handoffs),
                "open": sum(item["status"] == "open" for item in handoffs),
            },
            "handoffs": handoffs,
        }

    def _recent_handoffs(self) -> list[dict]:
        if self.handoff_reader is None:
            return []
        try:
            records = self.handoff_reader.list_recent(
                self.handoff_tenant_id, limit=20
            )
        except Exception:
            return []

        projected = []
        for record in records:
            created_at = record.get("created_at", "")
            if hasattr(created_at, "isoformat"):
                created_at = created_at.isoformat()
            projected.append(
                {
                    "ticket_no": str(record.get("ticket_no", "")),
                    "reason_label": _HANDOFF_REASON_LABELS.get(
                        record.get("reason_code"), "其他原因"
                    ),
                    "status": str(record.get("status", "unknown")),
                    "created_at": str(created_at),
                }
            )
        return projected

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
        suite_kind = payload.get("suite_kind")
        formal_benchmark = payload.get("formal_benchmark", False)
        repeat_count = int(payload.get("repeat_count", 1))
        dataset_digest = str(payload.get("dataset_digest") or "")
        if suite_kind == "smoke":
            if formal_benchmark or repeat_count != 1:
                raise ValueError("smoke report cannot claim formal status")
            gate_label = "开发 Smoke 门禁"
        elif suite_kind == "golden":
            if formal_report_errors(payload):
                raise ValueError("formal report failed evidence checks")
            gate_label = "正式 Golden Set"
        else:
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
            "formal_benchmark": formal_benchmark,
            "suite_kind": suite_kind,
            "gate_label": gate_label,
            "repeat_count": repeat_count,
            "dataset_digest": dataset_digest[:12],
            "run_id": str(payload["run_id"]),
            "dataset_version": str(payload["dataset_version"]),
            "code_revision": str(payload.get("code_revision") or "unknown")[:7],
            "created_at": str(payload["created_at"]),
            "case_count": int(payload["case_count"]),
            "passed_cases": int(task_success["passed"]),
            "result_label": (
                f"{int(task_success['passed'])}/{int(task_success['total'])} "
                + ("次运行通过" if formal_benchmark else "Case 通过")
            ),
            "metrics": metrics,
        }

    @staticmethod
    def _empty_evaluation() -> dict:
        return {
            "available": False,
            "formal_benchmark": False,
            "message": "尚未生成 Smoke 评测报告",
        }
