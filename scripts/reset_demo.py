"""Reset only JakePilot's anonymous order-after-sales demo records."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

from config.database import DatabaseConfig
from db.base.session_manager import SessionManager
from db.models import (
    ActionExecution,
    AfterSalesRequest,
    MemoryEvent,
    Order,
    TurnCheckpoint,
    UserProfileMemory,
    WorkingState,
)


DEMO_TENANT_ID = "demo"
DEMO_USER_ID = "user-a"


def load_configured_database_url(
    dotenv_path: str | Path | None = None,
) -> str:
    """Load the same environment configuration used by the application."""
    load_dotenv(dotenv_path=dotenv_path, override=False)
    return DatabaseConfig().connection_string


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
            working_count = (
                session.query(WorkingState)
                .filter(
                    WorkingState.tenant_id == DEMO_TENANT_ID,
                    WorkingState.user_id == DEMO_USER_ID,
                )
                .delete(synchronize_session=False)
            )
            event_count = (
                session.query(MemoryEvent)
                .filter(
                    MemoryEvent.tenant_id == DEMO_TENANT_ID,
                    MemoryEvent.user_id == DEMO_USER_ID,
                )
                .delete(synchronize_session=False)
            )
            profile_count = (
                session.query(UserProfileMemory)
                .filter(
                    UserProfileMemory.tenant_id == DEMO_TENANT_ID,
                    UserProfileMemory.user_id == DEMO_USER_ID,
                )
                .delete(synchronize_session=False)
            )
            checkpoint_count = (
                session.query(TurnCheckpoint)
                .filter(
                    TurnCheckpoint.tenant_id == DEMO_TENANT_ID,
                    TurnCheckpoint.user_id == DEMO_USER_ID,
                )
                .delete(synchronize_session=False)
            )
            return_window_refreshed = (
                session.query(Order)
                .filter(
                    Order.tenant_id == DEMO_TENANT_ID,
                    Order.user_id == DEMO_USER_ID,
                    Order.order_no == "JP20260919002",
                )
                .update(
                    {
                        Order.delivered_at: datetime.now(timezone.utc).replace(
                            tzinfo=None
                        )
                    },
                    synchronize_session=False,
                )
            )
        return {
            "after_sales_requests": request_count,
            "action_executions": action_count,
            "working_states": working_count,
            "memory_events": event_count,
            "profile_memories": profile_count,
            "turn_checkpoints": checkpoint_count,
            "return_window_refreshed": return_window_refreshed,
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

    database_url = load_configured_database_url()
    database_path = make_url(database_url).database or "<memory>"
    print(f"目标数据库：{database_path}")
    result = reset_demo_state(database_url)
    print(
        "演示数据已重置："
        f"退货申请 {result['after_sales_requests']} 条，"
        f"执行记录 {result['action_executions']} 条，"
        f"工作记忆 {result['working_states']} 条，"
        f"事件记忆 {result['memory_events']} 条，"
        f"用户偏好 {result['profile_memories']} 条，"
        f"Turn 检查点 {result['turn_checkpoints']} 条，"
        f"退货时效刷新 {result['return_window_refreshed']} 条。"
    )
    print("除指定演示订单的签收时间外，订单、物流、知识库、预约及其他租户/用户数据未修改。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
