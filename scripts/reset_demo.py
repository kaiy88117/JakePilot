"""Reset only JakePilot's anonymous order-after-sales demo records."""

from __future__ import annotations

import argparse

from sqlalchemy.engine import make_url

from config.database import db_config
from db.base.session_manager import SessionManager
from db.models import ActionExecution, AfterSalesRequest


DEMO_TENANT_ID = "demo"
DEMO_USER_ID = "user-a"


def reset_demo_state(database_url: str) -> dict[str, int]:
    """Delete demo-user writes while preserving seed orders and other users."""
    if make_url(database_url).get_backend_name() != "sqlite":
        raise ValueError("demo reset only supports SQLite")

    manager = SessionManager(database_url)
    try:
        with manager.session_scope() as session:
            request_count = (
                session.query(AfterSalesRequest)
                .filter(
                    AfterSalesRequest.tenant_id == DEMO_TENANT_ID,
                    AfterSalesRequest.user_id == DEMO_USER_ID,
                )
                .delete(synchronize_session=False)
            )
            action_count = (
                session.query(ActionExecution)
                .filter(
                    ActionExecution.tenant_id == DEMO_TENANT_ID,
                    ActionExecution.user_id == DEMO_USER_ID,
                )
                .delete(synchronize_session=False)
            )
        return {
            "after_sales_requests": request_count,
            "action_executions": action_count,
        }
    finally:
        manager.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="清理匿名演示用户的退货申请与幂等执行记录。"
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="确认执行；未提供时只显示说明，不修改数据库。",
    )
    args = parser.parse_args()
    if not args.yes:
        print("未修改数据库。确认只清理 demo/user-a 演示记录后，请重新运行并添加 --yes。")
        return 2

    result = reset_demo_state(db_config.connection_string)
    print(
        "演示数据已重置："
        f"退货申请 {result['after_sales_requests']} 条，"
        f"执行记录 {result['action_executions']} 条。"
    )
    print("订单、物流、知识库、预约及其他租户/用户数据未修改。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
