"""Schema-validated tool registry with deterministic write guards."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError


logger = logging.getLogger(__name__)


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ToolRisk(StrEnum):
    READ = "read"
    WRITE = "write"


class ToolContext(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=128)
    confirmed_payload_hash: str | None = Field(default=None, max_length=64)


class ToolResult(BaseModel):
    status: Literal[
        "succeeded",
        "failed",
        "not_found",
        "invalid_arguments",
        "confirmation_required",
    ]
    data: dict[str, Any] = Field(default_factory=dict)
    public_message: str = ""

    @classmethod
    def succeeded(
        cls, data: dict[str, Any] | None = None, public_message: str = ""
    ) -> "ToolResult":
        return cls(
            status="succeeded",
            data=data or {},
            public_message=public_message,
        )


ToolHandler = Callable[[BaseModel, ToolContext], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    args_model: type[BaseModel]
    risk: ToolRisk
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.name.strip():
            raise ValueError("tool name must not be blank")
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def normalize_arguments(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Return the schema-approved canonical payload, if it is valid."""
        spec = self.get(name)
        if spec is None:
            return None
        try:
            parsed_arguments = spec.args_model.model_validate(arguments)
        except ValidationError:
            return None
        return parsed_arguments.model_dump(mode="json")

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        spec = self.get(name)
        if spec is None:
            return ToolResult(
                status="failed",
                public_message="请求的工具不可用",
            )

        normalized_arguments = self.normalize_arguments(name, arguments)
        if normalized_arguments is None:
            return ToolResult(
                status="invalid_arguments",
                public_message="工具参数不完整或格式不正确",
            )

        parsed_arguments = spec.args_model.model_validate(normalized_arguments)
        payload_hash = canonical_payload_hash(normalized_arguments)
        if spec.risk == ToolRisk.WRITE:
            if context.confirmed_payload_hash is None:
                return ToolResult(
                    status="confirmation_required",
                    data={"payload_hash": payload_hash},
                    public_message="该操作需要用户确认",
                )
            if (
                context.confirmed_payload_hash != payload_hash
                or not context.idempotency_key
            ):
                return ToolResult(
                    status="failed",
                    public_message="确认信息已失效，请重新确认",
                )

        try:
            return await spec.handler(parsed_arguments, context)
        except Exception:
            logger.exception("Tool execution failed: %s", name)
            return ToolResult(
                status="failed",
                public_message="工具执行失败，请稍后重试",
            )
