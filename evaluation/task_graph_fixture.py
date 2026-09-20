"""Per-case isolated fixtures for the production JakePilot task graph."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agents.human_handoff_agent import HumanHandoffAgent
from agents.order_after_sales_agent import OrderAfterSalesAgent
from agents.task_classification_agent import TaskClassificationAgent
from evaluation.contracts import EvalCase
from runtime.context_engine import ContextEngine
from services.handoff_service import HandoffService
from services.memory_manager import MemoryManager
from services.order_after_sales_service import OrderAfterSalesService


RouterModelFactory = Callable[[EvalCase], Any]
DomainAgentFactory = Callable[[str, str], Any]
WriteCount = Callable[[Any], int]


class IsolatedTaskGraphSession:
    """Own all stateful resources used by one evaluation Case."""

    def __init__(
        self,
        *,
        agent: TaskClassificationAgent,
        database_path: Path,
        order_service: OrderAfterSalesService,
        memory_manager: MemoryManager,
        handoff_service: HandoffService,
        appointment_agent: Any,
        consultant_agent: Any,
        appointment_write_count: WriteCount | None = None,
    ) -> None:
        self.agent = agent
        self.database_path = database_path
        self.database_url = f"sqlite:///{database_path.as_posix()}"
        self.order_service = order_service
        self.memory_manager = memory_manager
        self.handoff_service = handoff_service
        self.appointment_agent = appointment_agent
        self.consultant_agent = consultant_agent
        self.appointment_write_count = appointment_write_count
        self._closed = False

    async def classify_task_stream(
        self,
        message: str,
        turn_id: str | None = None,
    ):
        async for token in self.agent.classify_task_stream(
            message,
            turn_id=turn_id,
        ):
            yield token

    def evaluation_write_count(self) -> int:
        appointment_count = (
            self.appointment_write_count(self.appointment_agent)
            if self.appointment_write_count is not None
            else 0
        )
        return (
            self.order_service.count_return_requests()
            + self.handoff_service.count("demo")
            + appointment_count
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for agent in (self.appointment_agent, self.consultant_agent):
            close = getattr(agent, "close", None)
            if callable(close):
                close()
        self.memory_manager.close()
        self.order_service.close()
        self.handoff_service.close()


class IsolatedTaskGraphFixtureFactory:
    """Build a central task graph backed by a fresh SQLite file per Case."""

    def __init__(
        self,
        *,
        work_dir: str | Path,
        router_model_factory: RouterModelFactory,
        appointment_agent_factory: DomainAgentFactory,
        consultant_agent_factory: DomainAgentFactory,
        appointment_write_count: WriteCount | None = None,
    ) -> None:
        self.work_dir = Path(work_dir)
        self.router_model_factory = router_model_factory
        self.appointment_agent_factory = appointment_agent_factory
        self.consultant_agent_factory = consultant_agent_factory
        self.appointment_write_count = appointment_write_count

    def __call__(self, case: EvalCase) -> IsolatedTaskGraphSession:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        case_key = hashlib.sha256(case.case_id.encode("utf-8")).hexdigest()[:20]
        database_path = self.work_dir / f"case_{case_key}.db"
        self._remove_previous_database(database_path)
        database_url = f"sqlite:///{database_path.as_posix()}"
        session_id = f"eval-{case_key}"

        order_service = OrderAfterSalesService(database_url)
        order_service.seed_demo_data()
        memory_manager = MemoryManager(database_url)
        handoff_service = HandoffService(database_url)
        appointment_agent = self.appointment_agent_factory(
            session_id,
            database_url,
        )
        consultant_agent = self.consultant_agent_factory(
            session_id,
            database_url,
        )
        order_agent = OrderAfterSalesAgent(
            session_id=session_id,
            service=order_service,
            memory_manager=memory_manager,
            context_engine=ContextEngine(memory_manager),
        )
        handoff_agent = HumanHandoffAgent(
            session_id=session_id,
            service=handoff_service,
        )
        task_agent = TaskClassificationAgent(
            appointment_agent,
            consultant_agent,
            order_agent,
            handoff_agent,
            llm=self.router_model_factory(case),
        )
        return IsolatedTaskGraphSession(
            agent=task_agent,
            database_path=database_path,
            order_service=order_service,
            memory_manager=memory_manager,
            handoff_service=handoff_service,
            appointment_agent=appointment_agent,
            consultant_agent=consultant_agent,
            appointment_write_count=self.appointment_write_count,
        )

    @staticmethod
    def _remove_previous_database(database_path: Path) -> None:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{database_path}{suffix}").unlink(missing_ok=True)
