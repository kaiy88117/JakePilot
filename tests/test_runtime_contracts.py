import pytest


def test_turn_request_rejects_blank_identity_and_message():
    from runtime.contracts import TurnRequest

    with pytest.raises(ValueError):
        TurnRequest(
            turn_id="t1",
            session_id="",
            user_id="u1",
            tenant_id="demo",
            message="x",
        )

    with pytest.raises(ValueError):
        TurnRequest(
            turn_id="t1",
            session_id="s1",
            user_id="u1",
            tenant_id="demo",
            message=" ",
        )


def test_budget_uses_the_approved_hard_limits():
    from runtime.contracts import ExecutionBudget

    budget = ExecutionBudget()

    assert (budget.max_steps, budget.max_tool_calls, budget.max_replans) == (
        6,
        8,
        2,
    )


def test_trace_snapshot_redacts_sensitive_fields():
    from runtime.contracts import RuntimeEvent
    from runtime.trace import TraceRecorder

    trace = TraceRecorder("trace-1")
    trace.record(
        RuntimeEvent(
            type="tool_started",
            data={
                "tool": "order.get",
                "phone": "13800138000",
                "arguments": {"order_id": "JP20260919001"},
            },
        )
    )

    public_event = trace.snapshot(public=True)[0]
    assert public_event.type == "tool_started"
    assert public_event.data == {"tool": "order.get"}
