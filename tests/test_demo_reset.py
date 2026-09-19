from db.models import ActionExecution, AfterSalesRequest
from scripts.reset_demo import reset_demo_state
from services.order_after_sales_service import OrderAfterSalesService


def test_reset_removes_only_demo_users_after_sales_records(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'demo-reset.db').as_posix()}"
    service = OrderAfterSalesService(database_url)
    service.seed_demo_data()
    service.create_return_request(
        "demo",
        "user-a",
        "JP20260919002",
        "商品破损",
        "demo-idem",
        "demo-hash",
    )
    with service.session_manager.session_scope() as session:
        session.add(
            AfterSalesRequest(
                request_no="AS-FOREIGN",
                tenant_id="other",
                user_id="user-z",
                order_no="FOREIGN-ORDER",
                reason="other",
                status="submitted",
                idempotency_key="foreign-idem",
            )
        )
        session.add(
            ActionExecution(
                action_id="ACT-FOREIGN",
                tenant_id="other",
                user_id="user-z",
                tool_name="return.create",
                payload_hash="foreign-hash",
                idempotency_key="foreign-idem",
                status="succeeded",
                external_ref="AS-FOREIGN",
                confirmed_by="user-z",
            )
        )

    result = reset_demo_state(database_url)

    assert result == {"after_sales_requests": 1, "action_executions": 1}
    assert service.count_return_requests() == 1
    assert service.get_order("demo", "user-a", "JP20260919002") is not None
    with service.session_manager.session_scope() as session:
        remaining = session.query(AfterSalesRequest).one()
        assert (remaining.tenant_id, remaining.user_id) == ("other", "user-z")


def test_reset_refuses_non_sqlite_databases():
    try:
        reset_demo_state("postgresql://example.invalid/jakepilot")
    except ValueError as exc:
        assert str(exc) == "demo reset only supports SQLite"
    else:
        raise AssertionError("non-SQLite reset must be rejected")
