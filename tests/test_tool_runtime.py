import asyncio

from pydantic import BaseModel, Field

from runtime.contracts import ExecutionBudget, TurnRequest, TurnStatus


class LookupArgs(BaseModel):
    order_id: str = Field(pattern=r"^JP\d{11}$")


class ReturnArgs(BaseModel):
    order_id: str = Field(pattern=r"^JP\d{11}$")
    reason: str = Field(min_length=2, max_length=200)


def _turn() -> TurnRequest:
    return TurnRequest(
        turn_id="turn-1",
        session_id="session-1",
        user_id="user-1",
        tenant_id="demo",
        message="查询订单 JP20260919001",
    )


def _context(**overrides):
    from runtime.tools import ToolContext

    values = {
        "tenant_id": "demo",
        "user_id": "user-1",
        "session_id": "session-1",
        "turn_id": "turn-1",
    }
    values.update(overrides)
    return ToolContext(**values)


def test_registry_rejects_invalid_arguments_before_handler_runs():
    from runtime.tools import (
        ToolRegistry,
        ToolResult,
        ToolRisk,
        ToolSpec,
    )

    calls = []

    async def lookup(args: LookupArgs, context):
        calls.append(args)
        return ToolResult.succeeded({"order_id": args.order_id})

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="order.get",
            args_model=LookupArgs,
            risk=ToolRisk.READ,
            handler=lookup,
        )
    )

    result = asyncio.run(
        registry.execute("order.get", {"order_id": "bad"}, _context())
    )

    assert result.status == "invalid_arguments"
    assert calls == []


def test_write_tool_requires_matching_confirmation_hash_and_idempotency_key():
    from runtime.tools import (
        ToolRegistry,
        ToolResult,
        ToolRisk,
        ToolSpec,
        canonical_payload_hash,
    )

    calls = []

    async def create_return(args: ReturnArgs, context):
        calls.append(args)
        return ToolResult.succeeded({"request_id": "AS001"})

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="return.create",
            args_model=ReturnArgs,
            risk=ToolRisk.WRITE,
            handler=create_return,
        )
    )
    arguments = {"order_id": "JP20260919002", "reason": "商品破损"}

    missing = asyncio.run(
        registry.execute("return.create", arguments, _context())
    )
    mismatch = asyncio.run(
        registry.execute(
            "return.create",
            arguments,
            _context(
                confirmed_payload_hash="wrong",
                idempotency_key="idem-1",
            ),
        )
    )
    confirmed = asyncio.run(
        registry.execute(
            "return.create",
            arguments,
            _context(
                confirmed_payload_hash=canonical_payload_hash(arguments),
                idempotency_key="idem-1",
            ),
        )
    )

    assert missing.status == "confirmation_required"
    assert missing.data["payload_hash"] == canonical_payload_hash(arguments)
    assert mismatch.status == "failed"
    assert confirmed.status == "succeeded"
    assert len(calls) == 1


def test_tool_exception_is_replaced_with_a_generic_public_failure():
    from runtime.tools import ToolRegistry, ToolRisk, ToolSpec

    async def failing_lookup(args: LookupArgs, context):
        raise RuntimeError("PRIVATE_DATABASE_DIAGNOSTIC")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="order.get",
            args_model=LookupArgs,
            risk=ToolRisk.READ,
            handler=failing_lookup,
        )
    )

    result = asyncio.run(
        registry.execute(
            "order.get",
            {"order_id": "JP20260919001"},
            _context(),
        )
    )

    assert result.status == "failed"
    assert result.public_message == "工具执行失败，请稍后重试"
    assert "PRIVATE_DATABASE_DIAGNOSTIC" not in result.model_dump_json()


class AlwaysReplanPlanner:
    async def next_action(self, turn, history):
        from runtime.loop import PlanAction

        return PlanAction.replan("try_again")


class RepeatedLookupPlanner:
    async def next_action(self, turn, history):
        from runtime.loop import PlanAction

        return PlanAction.tool("order.get", {"order_id": "JP20260919001"})


def _read_registry():
    from runtime.tools import ToolRegistry, ToolResult, ToolRisk, ToolSpec

    async def lookup(args: LookupArgs, context):
        return ToolResult.succeeded({"order_id": args.order_id})

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="order.get",
            args_model=LookupArgs,
            risk=ToolRisk.READ,
            handler=lookup,
        )
    )
    return registry


def test_loop_stops_after_the_replan_budget():
    from runtime.loop import BoundedAgentRuntime

    run = asyncio.run(
        BoundedAgentRuntime(_read_registry()).run(
            _turn(), AlwaysReplanPlanner(), _context()
        )
    )

    assert run.outcome.status == TurnStatus.FAILED
    assert run.outcome.steps == 3
    assert run.events[-1].data["reason"] == "replan_budget_exhausted"


def test_loop_stops_repeating_the_same_tool_call():
    from runtime.loop import BoundedAgentRuntime

    run = asyncio.run(
        BoundedAgentRuntime(
            _read_registry(), budget=ExecutionBudget(max_steps=6)
        ).run(_turn(), RepeatedLookupPlanner(), _context())
    )

    assert run.outcome.status == TurnStatus.FAILED
    assert sum(event.type == "tool_started" for event in run.events) == 1
    assert run.events[-1].data["reason"] == "repeated_tool_call"
