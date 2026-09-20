import json
from html.parser import HTMLParser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from services.observability_service import ObservabilityService
from services.turn_journal import TurnJournal
from web.routes import _is_local_client


ROOT = Path(__file__).resolve().parents[1]


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self.assets: set[str] = set()

    def handle_starttag(self, tag: str, attrs):
        values = dict(attrs)
        for key in ("href", "src"):
            if values.get(key):
                self.assets.add(values[key])

    def handle_data(self, data: str):
        if data.strip():
            self.text.append(data.strip())


def _journal(tmp_path):
    return TurnJournal(
        f"sqlite:///{(tmp_path / 'observability.db').as_posix()}"
    )


def test_turn_journal_lists_recent_privacy_minimized_checkpoints(tmp_path):
    journal = _journal(tmp_path)
    journal.begin(
        turn_id="turn-first",
        tenant_id="demo",
        user_id="private-user",
        session_id="private-session",
    )
    journal.begin(
        turn_id="turn-latest",
        tenant_id="demo",
        user_id="private-user",
        session_id="private-session",
    )
    journal.mark_delivered("turn-latest")

    recent = journal.list_recent(limit=1)

    assert len(recent) == 1
    assert recent[0]["turn_id"] == "turn-latest"
    assert recent[0]["delivery_status"] == "delivered"
    assert "updated_at" in recent[0]
    serialized = json.dumps(recent, ensure_ascii=False)
    assert "private-user" not in serialized
    assert "private-session" not in serialized


def test_observability_snapshot_uses_latest_valid_report_without_raw_cases(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    valid = {
        "schema_version": "1.0",
        "run_id": "smoke_20260920T010000Z_valid",
        "suite_kind": "smoke",
        "dataset_version": "order-after-sales-smoke-v1",
        "code_revision": "abc123456789",
        "created_at": "2026-09-20T01:00:00+00:00",
        "case_count": 6,
        "summary": {
            "end_to_end_task_success": {"passed": 5, "total": 6, "rate": 0.833333},
            "tool_sequence": {"passed": 6, "total": 6, "rate": 1.0},
        },
        "cases": [{"answer_digest": "secret-digest", "trace": [{"tool": "return.create"}]}],
    }
    (reports / "smoke_valid.json").write_text(
        json.dumps(valid, ensure_ascii=False), encoding="utf-8"
    )
    (reports / "smoke_broken.json").write_text("{broken", encoding="utf-8")

    class FakeJournal:
        def list_recent(self, limit=20):
            return [
                {
                    "turn_id": "turn-safe",
                    "status": "completed",
                    "last_event_type": "turn_ended",
                    "delivery_status": "delivered",
                    "business_write_succeeded": False,
                    "event_count": 4,
                    "updated_at": "2026-09-20T01:01:00Z",
                }
            ]

    snapshot = ObservabilityService(FakeJournal(), reports).snapshot()

    assert snapshot["evaluation"]["available"] is True
    assert snapshot["evaluation"]["formal_benchmark"] is False
    assert snapshot["evaluation"]["passed_cases"] == 5
    assert snapshot["evaluation"]["code_revision"] == "abc1234"
    assert snapshot["turns"][0]["turn_id"] == "turn-safe"
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "secret-digest" not in serialized
    assert "return.create" not in serialized


def test_observability_snapshot_has_honest_empty_evaluation_state(tmp_path):
    class EmptyJournal:
        def list_recent(self, limit=20):
            return []

    snapshot = ObservabilityService(
        EmptyJournal(), tmp_path / "missing"
    ).snapshot()

    assert snapshot["evaluation"] == {
        "available": False,
        "formal_benchmark": False,
        "message": "尚未生成 Smoke 评测报告",
    }
    assert snapshot["turn_summary"]["total"] == 0


def test_observability_projects_handoffs_without_raw_content(tmp_path):
    class EmptyJournal:
        def list_recent(self, limit=20):
            return []

    class FakeHandoffReader:
        def list_recent(self, tenant_id, limit=20):
            assert tenant_id == "demo"
            return [
                {
                    "ticket_no": "HO-0001",
                    "reason_code": "user_requested",
                    "status": "open",
                    "created_at": "2026-09-20T02:00:00Z",
                    "updated_at": "2026-09-20T02:00:00Z",
                    "summary": "private 13800138000",
                    "verified_facts": ["raw_message"],
                }
            ]

    snapshot = ObservabilityService(
        EmptyJournal(),
        tmp_path / "missing",
        handoff_reader=FakeHandoffReader(),
    ).snapshot()

    assert snapshot["handoff_summary"] == {"total": 1, "open": 1}
    assert snapshot["handoffs"] == [
        {
            "ticket_no": "HO-0001",
            "reason_label": "用户主动请求",
            "status": "open",
            "created_at": "2026-09-20T02:00:00Z",
        }
    ]
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "13800138000" not in serialized
    assert "raw_message" not in serialized


def test_observability_page_is_local_only_and_labels_smoke_results():
    assert _is_local_client("127.0.0.1") is True
    assert _is_local_client("::1") is True
    assert _is_local_client("203.0.113.9") is False

    environment = Environment(
        loader=FileSystemLoader(ROOT / "web" / "templates"),
        autoescape=True,
    )
    rendered = environment.get_template("observability.html").render(
        snapshot={
            "evaluation": {
                "available": False,
                "formal_benchmark": False,
                "message": "尚未生成 Smoke 评测报告",
            },
            "turn_summary": {
                "total": 0,
                "completed": 0,
                "delivered": 0,
                "business_writes": 0,
            },
            "turns": [],
            "handoff_summary": {"total": 1, "open": 1},
            "handoffs": [
                {
                    "ticket_no": "HO-0001",
                    "reason_label": "用户主动请求",
                    "status": "open",
                    "created_at": "2026-09-20T02:00:00Z",
                }
            ],
        }
    )
    page = _PageParser()
    page.feed(rendered)
    visible = " ".join(page.text)

    assert "运行观测" in visible
    assert "开发 Smoke 门禁" in visible
    assert "不是正式 Golden Set 指标" in visible
    assert "脱敏生命周期" in visible
    assert "人工接管" in visible
    assert "HO-0001" in visible
    assert "用户主动请求" in visible
    assert "raw_message" not in visible
    assert "/static/observability.css" in page.assets
    assert "/" in page.assets
