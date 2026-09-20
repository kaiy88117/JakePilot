import asyncio
from collections import OrderedDict
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from agents.appointment_agent import AppointmentAgent
from agents.consultant_agent import ConsultantAgent
from agents.task_classification_agent import TaskClassificationAgent
from agents.order_after_sales_agent import OrderAfterSalesAgent
from config.database import db_config
from runtime.context_engine import ContextEngine
from services.memory_manager import MemoryManager
from services.memory_consolidator import (
    MemoryConsolidationDispatcher,
    MemoryConsolidator,
)
from services.order_after_sales_service import OrderAfterSalesService
from services.handoff_service import HandoffService
from agents.human_handoff_agent import HumanHandoffAgent


LEGACY_SESSION_ID = "legacy-default"
_order_after_sales_service: OrderAfterSalesService | None = None
_memory_manager: MemoryManager | None = None
_handoff_service: HandoffService | None = None
_memory_dispatcher: MemoryConsolidationDispatcher | None = None


def _get_order_after_sales_service() -> OrderAfterSalesService:
    global _order_after_sales_service
    if _order_after_sales_service is None:
        service = OrderAfterSalesService(db_config.connection_string)
        service.seed_demo_data()
        _order_after_sales_service = service
    return _order_after_sales_service


def _get_memory_manager() -> MemoryManager:
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager(db_config.connection_string)
    return _memory_manager


def _get_handoff_service() -> HandoffService:
    global _handoff_service
    if _handoff_service is None:
        _handoff_service = HandoffService(db_config.connection_string)
    return _handoff_service


def _get_memory_dispatcher() -> MemoryConsolidationDispatcher:
    global _memory_dispatcher
    if _memory_dispatcher is None:
        _memory_dispatcher = MemoryConsolidationDispatcher(
            MemoryConsolidator(_get_memory_manager())
        )
    return _memory_dispatcher


class SessionRegistryFull(RuntimeError):
    """Raised when every bounded session slot is currently active."""


@dataclass
class _SessionEntry:
    agent: Any
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


def _create_task_agent(session_id: str) -> TaskClassificationAgent:
    """Create one stateful agent graph for a single browser session."""
    memory_manager = _get_memory_manager()
    return TaskClassificationAgent(
        AppointmentAgent(session_id=session_id),
        ConsultantAgent(session_id=session_id),
        OrderAfterSalesAgent(
            session_id=session_id,
            service=_get_order_after_sales_service(),
            memory_manager=memory_manager,
            context_engine=ContextEngine(memory_manager),
        ),
        HumanHandoffAgent(
            session_id=session_id,
            service=_get_handoff_service(),
        ),
    )


class AgentSessionRegistry:
    """Bounded process-local registry for stateful conversation agents."""

    def __init__(
        self,
        factory: Callable[[str], Any] = _create_task_agent,
        max_sessions: int = 100,
    ) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        self._factory = factory
        self._max_sessions = max_sessions
        self._entries: OrderedDict[str, _SessionEntry] = OrderedDict()

    def _get_or_create(self, session_id: str) -> _SessionEntry:
        entry = self._entries.get(session_id)
        if entry is not None:
            self._entries.move_to_end(session_id)
            return entry

        if len(self._entries) >= self._max_sessions:
            idle_session_id = next(
                (
                    candidate_id
                    for candidate_id, candidate in self._entries.items()
                    if candidate.users == 0 and not candidate.lock.locked()
                ),
                None,
            )
            if idle_session_id is None:
                raise SessionRegistryFull("all session slots are active")
            self._entries.pop(idle_session_id)

        entry = _SessionEntry(agent=self._factory(session_id))
        self._entries[session_id] = entry
        return entry

    def get(self, session_id: str) -> Any:
        return self._get_or_create(session_id).agent

    @asynccontextmanager
    async def acquire(self, session_id: str):
        entry = self._get_or_create(session_id)
        entry.users += 1
        try:
            async with entry.lock:
                yield entry.agent
        finally:
            entry.users -= 1

    def reset(self, session_id: str) -> None:
        entry = self._entries.get(session_id)
        if entry is not None and (entry.users > 0 or entry.lock.locked()):
            raise RuntimeError("cannot reset an active session")
        self._entries.pop(session_id, None)


session_registry = AgentSessionRegistry()


async def ProcessUserInput_stream(
    user_input,
    state=None,
    context=None,
    session_id: str | None = None,
    turn_id: str | None = None,
):
    """
    user_input: 用户输入
    state: 当前对话状态（如 None, 'classify', 'appointment', 'query', ...）
    context: 可选，保存多轮对话上下文（如 dict，可存储 agent 的 history 等）
    返回: (reply, next_state, next_context)
    """
    # 初始化 context
    if context is None:
        context = {}

    async with session_registry.acquire(session_id or LEGACY_SESSION_ID) as task_agent:
        if turn_id is None:
            token_stream = task_agent.classify_task_stream(user_input)
        else:
            token_stream = task_agent.classify_task_stream(
                user_input, turn_id=turn_id
            )
        async for token in token_stream:
            yield token
