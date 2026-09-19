"""Schema tools for order, logistics and return workflows."""

from pydantic import BaseModel, Field

from runtime.tools import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolRisk,
    ToolSpec,
)
from services.order_after_sales_service import OrderAfterSalesService


class OrderArgs(BaseModel):
    order_id: str = Field(pattern=r"^JP\d{11}$")


class CreateReturnArgs(OrderArgs):
    reason: str = Field(min_length=2, max_length=200)


def register_ecommerce_tools(
    registry: ToolRegistry, service: OrderAfterSalesService
) -> None:
    async def get_order(args: OrderArgs, context: ToolContext) -> ToolResult:
        order = service.get_order(context.tenant_id, context.user_id, args.order_id)
        if order is None:
            return ToolResult(status="not_found", public_message="未找到该订单")
        return ToolResult.succeeded(order, "订单查询成功")

    async def get_logistics(args: OrderArgs, context: ToolContext) -> ToolResult:
        order = service.get_order(context.tenant_id, context.user_id, args.order_id)
        if order is None:
            return ToolResult(status="not_found", public_message="未找到该订单")
        events = service.get_logistics(
            context.tenant_id, context.user_id, args.order_id
        )
        return ToolResult.succeeded({"events": events}, "物流查询成功")

    async def check_return(args: OrderArgs, context: ToolContext) -> ToolResult:
        result = service.check_return_eligibility(
            context.tenant_id, context.user_id, args.order_id
        )
        status = "not_found" if result["reason"] == "order_not_found" else "succeeded"
        return ToolResult(status=status, data=result, public_message="退货资格核验完成")

    async def create_return(
        args: CreateReturnArgs, context: ToolContext
    ) -> ToolResult:
        request = service.create_return_request(
            context.tenant_id,
            context.user_id,
            args.order_id,
            args.reason,
            context.idempotency_key or "",
            context.confirmed_payload_hash or "",
        )
        return ToolResult.succeeded(request, "退货申请已提交")

    for spec in (
        ToolSpec("order.get", OrderArgs, ToolRisk.READ, get_order),
        ToolSpec("logistics.get", OrderArgs, ToolRisk.READ, get_logistics),
        ToolSpec("return.check", OrderArgs, ToolRisk.READ, check_return),
        ToolSpec("return.create", CreateReturnArgs, ToolRisk.WRITE, create_return),
    ):
        registry.register(spec)
