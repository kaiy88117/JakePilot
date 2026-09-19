"""Order and after-sales domain agent backed by the bounded tool runtime."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import uuid4

from runtime.contracts import RuntimeEvent, TurnRequest, TurnStatus
from runtime.ecommerce_tools import register_ecommerce_tools
from runtime.loop import BoundedAgentRuntime, PlanAction
from runtime.tools import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    canonical_payload_hash,
)
from services.order_after_sales_service import OrderAfterSalesService


_ORDER_PATTERN = re.compile(r"JP\d{11}", re.IGNORECASE)


@dataclass(frozen=True)
class PendingAction:
    tool_name: str
    arguments: dict
    payload_hash: str
    idempotency_key: str


@dataclass(frozen=True)
class ReturnDraft:
    order_id: str
    reason: str = ""


class _OrderPlanner:
    def __init__(self, mode: str, order_id: str, reason: str = "") -> None:
        self.mode = mode
        self.order_id = order_id
        self.reason = reason

    async def next_action(self, turn, history):
        if not history:
            if self.mode == "logistics":
                return PlanAction.tool(
                    "logistics.get", {"order_id": self.order_id}
                )
            if self.mode == "eligibility":
                return PlanAction.tool(
                    "return.check", {"order_id": self.order_id}
                )
            if self.mode == "return":
                return PlanAction.tool(
                    "return.check", {"order_id": self.order_id}
                )
            if self.mode == "confirmed_return":
                return PlanAction.tool(
                    "return.create",
                    {"order_id": self.order_id, "reason": self.reason},
                )
            return PlanAction.tool("order.get", {"order_id": self.order_id})

        _, result = history[-1]
        if result is None:
            return PlanAction.answer_now("暂时无法完成该请求")
        if result.status != "succeeded":
            return PlanAction.answer_now(result.public_message or "暂时无法完成该请求")

        if self.mode == "return" and history[-1][0].tool_name == "return.check":
            if not result.data.get("eligible"):
                return PlanAction.answer_now(
                    _eligibility_message(result.data.get("reason", ""))
                )
            return PlanAction.tool(
                "return.create",
                {"order_id": self.order_id, "reason": self.reason},
            )

        if self.mode == "logistics":
            events = result.data.get("events", [])
            if not events:
                return PlanAction.answer_now("暂未查询到物流节点")
            summary = "；".join(event["description"] for event in events)
            return PlanAction.answer_now(
                f"订单 {self.order_id} 的物流进度：{summary}。"
            )
        if self.mode == "eligibility":
            return PlanAction.answer_now(
                _eligibility_message(result.data.get("reason", ""))
            )
        if self.mode in {"return", "confirmed_return"}:
            request_id = result.data.get("request_id", "")
            return PlanAction.answer_now(
                f"退货申请已提交，申请编号为 {request_id}。"
            )

        return PlanAction.answer_now(
            f"订单 {self.order_id} 当前状态为 {result.data.get('status', '未知')}，"
            f"商品为{result.data.get('item_name', '未知商品')}。"
        )


def _eligibility_message(reason: str) -> str:
    messages = {
        "within_return_window": "该订单仍在退货期限内，可以申请退货。",
        "activated_product": "该商品已激活，不符合当前演示退货规则。",
        "not_delivered": "该订单尚未签收，暂不能发起退货申请。",
        "return_window_expired": "该订单已超过七日退货期限。",
        "order_not_found": "未找到该订单，请核对订单号。",
    }
    return messages.get(reason, "暂时无法确认该订单的退货资格。")


class OrderAfterSalesAgent:
    def __init__(
        self,
        session_id: str,
        service: OrderAfterSalesService,
        tenant_id: str = "demo",
        user_id: str = "user-a",
    ) -> None:
        self.session_id = session_id
        self.service = service
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.pending_action: PendingAction | None = None
        self.return_draft: ReturnDraft | None = None
        registry = ToolRegistry()
        register_ecommerce_tools(registry, service)
        self.runtime = BoundedAgentRuntime(registry)

    @property
    def has_pending_action(self) -> bool:
        return self.pending_action is not None

    @property
    def has_active_flow(self) -> bool:
        return self.pending_action is not None or self.return_draft is not None

    async def run_stream(self, message: str):
        normalized = message.strip()
        if normalized in {"取消", "取消操作", "不提交"}:
            self.pending_action = None
            self.return_draft = None
            yield "[REPLY][订单售后 Agent]已取消当前待确认操作。"
            return

        if normalized in {"确认", "确认提交"}:
            async for token in self._confirm_pending_action():
                yield token
            return

        reason_update = self._extract_reason(normalized)
        order_match = _ORDER_PATTERN.search(normalized.upper())

        if self.pending_action is not None:
            previous = self.pending_action
            self.pending_action = None
            if reason_update and order_match is None:
                self.return_draft = ReturnDraft(
                    order_id=previous.arguments["order_id"],
                    reason=reason_update,
                )

        if order_match is not None:
            order_id = order_match.group(0).upper()
        elif self.return_draft is not None:
            order_id = self.return_draft.order_id
        else:
            yield "[REPLY][订单售后 Agent]请提供 JP 开头的订单号。"
            return

        if "物流" in normalized or "到哪" in normalized:
            mode = "logistics"
            reason = ""
            self.return_draft = None
        elif (
            "申请退货" in normalized
            or "我要退货" in normalized
            or (self.return_draft is not None and bool(reason_update))
        ):
            mode = "return"
            reason = reason_update or (
                self.return_draft.reason if self.return_draft else ""
            )
            if not reason:
                self.return_draft = ReturnDraft(order_id=order_id)
                yield "[REPLY][订单售后 Agent]请补充退货原因。"
                return
            self.return_draft = None
        elif "能退" in normalized or "退货资格" in normalized:
            mode = "eligibility"
            reason = ""
            self.return_draft = None
        else:
            mode = "order"
            reason = ""
            self.return_draft = None

        turn = self._turn(normalized)
        context = self._context(turn)
        run = await self.runtime.run(
            turn,
            _OrderPlanner(mode, order_id, reason),
            context,
        )
        for event in run.events:
            yield self._event_token(event)

        if run.outcome.status == TurnStatus.FAILED:
            yield f"[ERROR]{run.outcome.answer}"
            return

        if mode == "return" and run.outcome.status == TurnStatus.NEEDS_INPUT:
            arguments = {"order_id": order_id, "reason": reason}
            self.pending_action = PendingAction(
                tool_name="return.create",
                arguments=arguments,
                payload_hash=canonical_payload_hash(arguments),
                idempotency_key=f"return-{uuid4().hex}",
            )
            yield self._event_token(
                RuntimeEvent(
                    type="confirmation_required",
                    data={
                        "tool": "return.create",
                        "order_id": order_id,
                        "summary": f"为订单 {order_id} 提交退货申请",
                    },
                )
            )
            answer = (
                f"订单 {order_id} 符合退货条件，退货原因为“{reason}”。"
                "如信息无误，请回复“确认提交”。"
            )
        else:
            answer = run.outcome.answer
        yield f"[REPLY][订单售后 Agent]{answer}"

    async def _confirm_pending_action(self):
        pending = self.pending_action
        if pending is None:
            yield "[REPLY][订单售后 Agent]当前没有待确认操作。"
            return

        turn = self._turn("确认提交")
        context = self._context(
            turn,
            idempotency_key=pending.idempotency_key,
            confirmed_payload_hash=pending.payload_hash,
        )
        run = await self.runtime.run(
            turn,
            _OrderPlanner(
                "confirmed_return",
                pending.arguments["order_id"],
                pending.arguments["reason"],
            ),
            context,
        )
        for event in run.events:
            yield self._event_token(event)
        if run.outcome.status == TurnStatus.COMPLETED:
            self.pending_action = None
        if run.outcome.status == TurnStatus.FAILED:
            yield f"[ERROR]{run.outcome.answer}"
            return
        yield f"[REPLY][订单售后 Agent]{run.outcome.answer}"

    def _turn(self, message: str) -> TurnRequest:
        return TurnRequest(
            turn_id=f"turn-{uuid4().hex}",
            session_id=self.session_id,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            message=message,
        )

    def _context(
        self,
        turn: TurnRequest,
        idempotency_key: str | None = None,
        confirmed_payload_hash: str | None = None,
    ) -> ToolContext:
        return ToolContext(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            session_id=self.session_id,
            turn_id=turn.turn_id,
            idempotency_key=idempotency_key,
            confirmed_payload_hash=confirmed_payload_hash,
        )

    @staticmethod
    def _extract_reason(message: str) -> str:
        match = re.search(r"(?:原因改为|原因是|原因[:：]|因为)(.+)$", message)
        return match.group(1).strip(" ，。") if match else ""

    @staticmethod
    def _event_token(event: RuntimeEvent) -> str:
        return f"[EVENT]{event.model_dump_json()}"
