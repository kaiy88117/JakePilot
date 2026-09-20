"""Bind formal evaluation categories to JakePilot's central task graph."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from evaluation.contracts import EvalCase
from evaluation.formal_gate import FORMAL_CATEGORY_TARGETS
from evaluation.multidomain_runner import MultiDomainEvalRunner
from evaluation.stream_runner import (
    CloseAgent,
    StreamCategoryEvalRunner,
    WriteCount,
)


FORMAL_CATEGORIES = tuple(FORMAL_CATEGORY_TARGETS)


def build_task_graph_category_runners(
    *,
    agent_factory: Callable[[EvalCase], Any],
    write_count: WriteCount | None = None,
    close_agent: CloseAgent | None = None,
) -> dict[str, StreamCategoryEvalRunner]:
    """Route every formal category through the production task-graph entrypoint."""

    async def stream_turn(agent: Any, message: str, turn_id: str):
        async for token in agent.classify_task_stream(
            message,
            turn_id=turn_id,
        ):
            yield token

    runner = StreamCategoryEvalRunner(
        agent_factory=agent_factory,
        stream_turn=stream_turn,
        write_count=write_count,
        close_agent=close_agent,
    )
    return {category: runner for category in FORMAL_CATEGORIES}


def build_isolated_task_graph_category_runners(
    fixture_factory: Callable[[EvalCase], Any],
) -> dict[str, StreamCategoryEvalRunner]:
    """Wire an isolated fixture factory into every formal category."""

    return build_task_graph_category_runners(
        agent_factory=fixture_factory,
        write_count=lambda session: session.evaluation_write_count(),
        close_agent=lambda session: session.close(),
    )


def build_isolated_task_graph_runner(
    fixture_factory: Callable[[EvalCase], Any],
    *,
    tool_fixture_version: str,
) -> MultiDomainEvalRunner:
    """Build a version-bound multi-domain runner for formal execution."""

    return MultiDomainEvalRunner(
        build_isolated_task_graph_category_runners(fixture_factory),
        tool_fixture_version=tool_fixture_version,
    )
