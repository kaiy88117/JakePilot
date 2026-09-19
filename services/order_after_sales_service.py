"""Business rules for anonymous demo order and return workflows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from db.base.session_manager import SessionManager
from db.repositories.order_repository import OrderRepository


class OrderAfterSalesService:
    def __init__(self, database_url: str) -> None:
        self.session_manager = SessionManager(database_url)
        self.repository = OrderRepository(self.session_manager)

    def seed_demo_data(self) -> None:
        self.repository.seed_demo_data()

    def get_order(
        self, tenant_id: str, user_id: str, order_id: str
    ) -> dict | None:
        return self.repository.get_order(tenant_id, user_id, order_id)

    def get_logistics(
        self, tenant_id: str, user_id: str, order_id: str
    ) -> list[dict]:
        return self.repository.get_logistics(tenant_id, user_id, order_id)

    def check_return_eligibility(
        self, tenant_id: str, user_id: str, order_id: str
    ) -> dict:
        order = self.get_order(tenant_id, user_id, order_id)
        if order is None:
            return {"eligible": False, "reason": "order_not_found"}
        if order["return_policy"] == "activated_product":
            return {"eligible": False, "reason": "activated_product"}
        if order["status"] != "delivered" or order["delivered_at"] is None:
            return {"eligible": False, "reason": "not_delivered"}
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if now - order["delivered_at"] > timedelta(days=7):
            return {"eligible": False, "reason": "return_window_expired"}
        return {"eligible": True, "reason": "within_return_window"}

    def create_return_request(
        self,
        tenant_id: str,
        user_id: str,
        order_id: str,
        reason: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> dict:
        completed = self.repository.get_completed_action_result(
            tenant_id,
            user_id,
            idempotency_key,
            payload_hash,
        )
        if completed is not None:
            return completed
        eligibility = self.check_return_eligibility(
            tenant_id, user_id, order_id
        )
        if not eligibility["eligible"]:
            raise ValueError(eligibility["reason"])
        return self.repository.create_return_request(
            tenant_id,
            user_id,
            order_id,
            reason,
            idempotency_key,
            payload_hash,
        )

    def count_return_requests(self) -> int:
        return self.repository.count_return_requests()

    def close(self) -> None:
        self.session_manager.close()
