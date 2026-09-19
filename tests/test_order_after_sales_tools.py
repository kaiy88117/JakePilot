import asyncio

from runtime.tools import ToolContext, ToolRegistry
from services.order_after_sales_service import OrderAfterSalesService


def _service(tmp_path) -> OrderAfterSalesService:
    database_file = tmp_path / "orders.db"
    service = OrderAfterSalesService(
        f"sqlite:///{database_file.as_posix()}"
    )
    service.seed_demo_data()
    return service


def test_order_lookup_is_scoped_by_tenant_and_user(tmp_path):
    service = _service(tmp_path)

    own_order = service.get_order("demo", "user-a", "JP20260919001")
    foreign_order = service.get_order("demo", "user-b", "JP20260919001")

    assert own_order is not None
    assert own_order["item_name"] == "降噪蓝牙耳机"
    assert foreign_order is None


def test_logistics_returns_ordered_public_events(tmp_path):
    service = _service(tmp_path)

    events = service.get_logistics("demo", "user-a", "JP20260919001")

    assert [item["status"] for item in events] == ["shipped", "in_transit"]
    assert events[0]["occurred_at"] < events[1]["occurred_at"]


def test_return_creation_is_idempotent(tmp_path):
    service = _service(tmp_path)

    first = service.create_return_request(
        "demo",
        "user-a",
        "JP20260919002",
        "商品破损",
        "idem-1",
        payload_hash="hash-1",
    )
    second = service.create_return_request(
        "demo",
        "user-a",
        "JP20260919002",
        "商品破损",
        "idem-1",
        payload_hash="hash-1",
    )

    assert first["request_id"] == second["request_id"]
    assert service.count_return_requests() == 1


def test_non_returnable_order_never_creates_a_request(tmp_path):
    service = _service(tmp_path)

    eligibility = service.check_return_eligibility(
        "demo", "user-a", "JP20260919003"
    )

    assert eligibility == {"eligible": False, "reason": "activated_product"}


def test_registered_tools_take_identity_only_from_tool_context(tmp_path):
    from runtime.ecommerce_tools import register_ecommerce_tools

    service = _service(tmp_path)
    registry = ToolRegistry()
    register_ecommerce_tools(registry, service)
    foreign_context = ToolContext(
        tenant_id="demo",
        user_id="user-b",
        session_id="session-b",
        turn_id="turn-b",
    )

    result = asyncio.run(
        registry.execute(
            "order.get",
            {"order_id": "JP20260919001"},
            foreign_context,
        )
    )

    assert result.status == "not_found"
    assert "JP20260919001" not in result.model_dump_json()
