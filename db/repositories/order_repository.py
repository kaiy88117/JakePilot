"""Tenant-scoped persistence for demo orders and after-sales actions."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from db.base.session_manager import SessionManager
from db.models import ActionExecution, AfterSalesRequest, LogisticsEvent, Order


class OrderRepository:
    def __init__(self, session_manager: SessionManager) -> None:
        self.session_manager = session_manager

    def get_order(
        self, tenant_id: str, user_id: str, order_no: str
    ) -> dict | None:
        with self.session_manager.session_scope() as session:
            order = self._find_order(session, tenant_id, user_id, order_no)
            return self._order_to_dict(order) if order else None

    def get_logistics(
        self, tenant_id: str, user_id: str, order_no: str
    ) -> list[dict]:
        with self.session_manager.session_scope() as session:
            order = self._find_order(session, tenant_id, user_id, order_no)
            if order is None:
                return []
            events = (
                session.query(LogisticsEvent)
                .filter(
                    LogisticsEvent.order_id == order.id,
                    LogisticsEvent.tenant_id == tenant_id,
                    LogisticsEvent.user_id == user_id,
                )
                .order_by(LogisticsEvent.occurred_at.asc())
                .all()
            )
            return [
                {
                    "status": event.status,
                    "description": event.description,
                    "occurred_at": event.occurred_at,
                }
                for event in events
            ]

    def seed_demo_data(self) -> None:
        with self.session_manager.session_scope() as session:
            exists = (
                session.query(Order)
                .filter(Order.tenant_id == "demo", Order.order_no == "JP20260919001")
                .first()
            )
            if exists:
                return

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            orders = [
                Order(
                    tenant_id="demo",
                    user_id="user-a",
                    order_no="JP20260919001",
                    item_name="降噪蓝牙耳机",
                    status="in_transit",
                    return_policy="seven_day",
                ),
                Order(
                    tenant_id="demo",
                    user_id="user-a",
                    order_no="JP20260919002",
                    item_name="智能空气炸锅",
                    status="delivered",
                    delivered_at=now.replace(microsecond=0),
                    return_policy="seven_day",
                ),
                Order(
                    tenant_id="demo",
                    user_id="user-a",
                    order_no="JP20260919003",
                    item_name="已激活智能手表",
                    status="delivered",
                    delivered_at=now.replace(microsecond=0),
                    return_policy="activated_product",
                ),
            ]
            session.add_all(orders)
            session.flush()
            session.add_all(
                [
                    LogisticsEvent(
                        tenant_id="demo",
                        user_id="user-a",
                        order_id=orders[0].id,
                        status="shipped",
                        description="商品已从仓库发出",
                        occurred_at=now.replace(hour=8, minute=0, second=0, microsecond=0),
                    ),
                    LogisticsEvent(
                        tenant_id="demo",
                        user_id="user-a",
                        order_id=orders[0].id,
                        status="in_transit",
                        description="商品正在运输途中",
                        occurred_at=now.replace(hour=10, minute=0, second=0, microsecond=0),
                    ),
                ]
            )

    def create_return_request(
        self,
        tenant_id: str,
        user_id: str,
        order_no: str,
        reason: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> dict:
        with self.session_manager.session_scope() as session:
            existing_action = (
                session.query(ActionExecution)
                .filter(
                    ActionExecution.tenant_id == tenant_id,
                    ActionExecution.idempotency_key == idempotency_key,
                )
                .first()
            )
            if existing_action:
                if existing_action.payload_hash != payload_hash:
                    raise ValueError("idempotency key payload mismatch")
                existing_request = (
                    session.query(AfterSalesRequest)
                    .filter(
                        AfterSalesRequest.tenant_id == tenant_id,
                        AfterSalesRequest.request_no == existing_action.external_ref,
                    )
                    .first()
                )
                if existing_request is None:
                    raise RuntimeError("action ledger has no business result")
                return self._request_to_dict(existing_request)

            order = self._find_order(session, tenant_id, user_id, order_no)
            if order is None:
                raise LookupError("order not found")

            action = ActionExecution(
                action_id=f"ACT-{uuid4().hex}",
                tenant_id=tenant_id,
                user_id=user_id,
                tool_name="return.create",
                payload_hash=payload_hash,
                idempotency_key=idempotency_key,
                status="prepared",
                confirmed_by=user_id,
            )
            session.add(action)
            session.flush()
            action.status = "executing"

            request = AfterSalesRequest(
                request_no=f"AS-{uuid4().hex[:12].upper()}",
                tenant_id=tenant_id,
                user_id=user_id,
                order_no=order_no,
                reason=reason,
                status="submitted",
                idempotency_key=idempotency_key,
            )
            session.add(request)
            session.flush()
            action.status = "succeeded"
            action.external_ref = request.request_no
            return self._request_to_dict(request)

    def count_return_requests(self) -> int:
        with self.session_manager.session_scope() as session:
            return session.query(AfterSalesRequest).count()

    @staticmethod
    def _find_order(session, tenant_id: str, user_id: str, order_no: str):
        return (
            session.query(Order)
            .filter(
                Order.tenant_id == tenant_id,
                Order.user_id == user_id,
                Order.order_no == order_no,
            )
            .first()
        )

    @staticmethod
    def _order_to_dict(order: Order) -> dict:
        return {
            "order_id": order.order_no,
            "item_name": order.item_name,
            "status": order.status,
            "delivered_at": order.delivered_at,
            "return_policy": order.return_policy,
        }

    @staticmethod
    def _request_to_dict(request: AfterSalesRequest) -> dict:
        return {
            "request_id": request.request_no,
            "order_id": request.order_no,
            "status": request.status,
        }
