from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from agents.appointment_agent import AppointmentAgent
from agents.appointment.input_parser import InputParser
from api.stream_protocol import iter_sse_events
from appointment_decision import AppointmentDecision, AppointmentSlots
from appointment_decision.gateway import AppointmentDecisionGateway
from appointment_training.evaluator import AppointmentModelReport


class FakeClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def complete(self, _request) -> str:
        self.calls += 1
        return self.response


def strong_data() -> dict:
    return {
        "gender": "未知",
        "start_time": "2026-09-21 10:00",
        "duration": "60分钟",
        "project": "洗衣机维修",
        "preference": "无",
        "technician_name": "未知",
        "confirmation": "未知",
        "info_complete": True,
        "unrelated": False,
        "missing_info": [],
    }


def valid_local_json() -> str:
    return AppointmentDecision(
        action="query_slots",
        slots=AppointmentSlots(
            product_ref="洗衣机",
            service_type="repair",
            region="杭州市西湖区",
            date_range="2026-09-21",
        ),
        missing_slots=(),
    ).model_dump_json()


def make_agent(gateway: AppointmentDecisionGateway, reason: str | None = None):
    agent = object.__new__(AppointmentAgent)
    agent.decision_gateway = gateway
    agent.decision_mode_reason = reason
    agent.appointment_history = {}
    return agent


def test_disabled_mode_does_not_call_local_and_preserves_strong_data() -> None:
    client = FakeClient(valid_local_json())
    agent = make_agent(AppointmentDecisionGateway("disabled", client))

    data, event = agent._evaluate_decision_model(
        "周末想约洗衣机维修",
        strong_data(),
        recent_history=(),
        current_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )

    assert client.calls == 0
    assert data == strong_data()
    payload = json.loads(event.removeprefix("[EVENT]"))
    assert payload["data"]["mode"] == "disabled"
    assert payload["data"]["source"] == "strong_model"


def test_shadow_calls_local_but_never_changes_strong_data() -> None:
    client = FakeClient(valid_local_json())
    agent = make_agent(AppointmentDecisionGateway("shadow", client))

    data, event = agent._evaluate_decision_model(
        "周末想约洗衣机维修",
        strong_data(),
        recent_history=("上次想约空调安装",),
        current_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )

    assert client.calls == 1
    assert data == strong_data()
    payload = json.loads(event.removeprefix("[EVENT]"))
    assert payload["type"] == "decision_model_trace"
    assert set(payload["data"]) == {
        "mode",
        "source",
        "latency_ms",
        "validation_status",
        "fallback_reason",
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "周末想约洗衣机维修" not in serialized
    assert "杭州市西湖区" not in serialized
    assert "上次想约空调安装" not in serialized


def test_local_first_invalid_output_falls_back_without_changing_data() -> None:
    agent = make_agent(
        AppointmentDecisionGateway("local_first", FakeClient("not-json"))
    )

    data, event = agent._evaluate_decision_model(
        "周末想约洗衣机维修",
        strong_data(),
        recent_history=(),
        current_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )

    payload = json.loads(event.removeprefix("[EVENT]"))
    assert data == strong_data()
    assert payload["data"]["source"] == "strong_model_fallback"
    assert payload["data"]["fallback_reason"] == "invalid_json"


def test_local_first_without_formal_evidence_downgrades_to_shadow(
    tmp_path: Path,
) -> None:
    mode, reason = AppointmentAgent._resolve_decision_mode(
        "local_first", tmp_path
    )

    assert mode == "shadow"
    assert reason == "formal_evidence_missing"


def test_component_report_alone_does_not_allow_local_first(
    tmp_path: Path,
) -> None:
    report = AppointmentModelReport(
        dataset_sha256="a" * 64,
        code_revision="abc123",
        prompt_version="v1",
        model_version="qwen3-1.7b-q4_k_m",
        hardware_label="demo-gpu",
        sample_count=150,
        formal_benchmark=True,
        promotion_eligible=True,
        counts={},
        structure_validity=0.99,
        slot_exact_match=0.93,
        field_f1=0.94,
        action_accuracy=0.92,
        hallucinated_slot_rate=0.01,
        refusal_boundary_rate=0.98,
        p95_local_latency_ms=800,
        fallback_rate=0.02,
    )
    (tmp_path / "appointment_model_20260920.json").write_text(
        report.model_dump_json(), encoding="utf-8"
    )

    mode, reason = AppointmentAgent._resolve_decision_mode(
        "local_first", tmp_path
    )

    assert mode == "shadow"
    assert reason == "integration_runtime_not_ready"


def test_legacy_confirmed_fields_are_projected_into_local_request() -> None:
    request = InputParser.build_decision_request(
        "改约其他时间",
        recent_history=(),
        appointment_history={
            "project": "洗衣机维修",
            "start_time": "2026-09-21 10:00",
        },
        current_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )

    assert request.confirmed_slots.product_ref == "洗衣机"
    assert request.confirmed_slots.service_type == "repair"
    assert request.confirmed_slots.date_range == "2026-09-21 10:00"


def test_invalid_legacy_slot_does_not_clear_other_confirmed_slots() -> None:
    request = InputParser.build_decision_request(
        "继续预约",
        recent_history=(),
        appointment_history={
            "product_ref": "洗衣机",
            "service_type": "unsupported-value",
            "date_range": "2026-09-21 10:00",
        },
        current_time=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )

    assert request.confirmed_slots.product_ref == "洗衣机"
    assert request.confirmed_slots.service_type is None
    assert request.confirmed_slots.date_range == "2026-09-21 10:00"


def test_injected_local_first_gateway_is_also_downgraded() -> None:
    gateway, reason = AppointmentAgent._guard_injected_gateway(
        AppointmentDecisionGateway("local_first", FakeClient(valid_local_json()))
    )

    assert gateway.mode == "shadow"
    assert reason == "integration_runtime_not_ready"


def test_decision_trace_sse_projection_hides_private_fields() -> None:
    async def tokens():
        yield (
            '[EVENT]{"type":"decision_model_trace","data":'
            '{"mode":"shadow","source":"strong_model",'
            '"latency_ms":12.5,"validation_status":"passed",'
            '"fallback_reason":null,"message":"PRIVATE_MESSAGE",'
            '"slots":{"region":"PRIVATE_REGION"}}}'
        )
        yield "[REPLY][预约机器人]请补充上门区域"

    async def collect() -> list[tuple[str, dict]]:
        result = []
        async for frame in iter_sse_events(tokens(), "turn-appointment"):
            lines = frame.strip().splitlines()
            result.append(
                (
                    lines[0].removeprefix("event: "),
                    json.loads(lines[1].removeprefix("data: ")),
                )
            )
        return result

    events = asyncio.run(collect())
    trace = next(
        payload for name, payload in events if name == "decision_model_trace"
    )
    assert trace == {
        "turn_id": "turn-appointment",
        "mode": "shadow",
        "source": "strong_model",
        "latency_ms": 12.5,
        "validation_status": "passed",
        "fallback_reason": None,
    }
    serialized = json.dumps(events)
    assert "PRIVATE_MESSAGE" not in serialized
    assert "PRIVATE_REGION" not in serialized
