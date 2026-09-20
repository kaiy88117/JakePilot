"""Bounded semantic scoring for dimensions deterministic checks cannot cover."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


ReasonCode = Literal[
    "answers_user_intent",
    "covers_required_facts",
    "partially_relevant",
    "missing_required_fact",
    "off_topic",
    "conflicting_answer",
]


class _JudgePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relevance_score: float = Field(ge=0.0, le=1.0)
    completeness_score: float = Field(ge=0.0, le=1.0)
    reason_codes: tuple[ReasonCode, ...] = Field(max_length=4)


class SemanticJudgment(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["pass", "fail", "unknown"]
    relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)
    completeness_score: float | None = Field(default=None, ge=0.0, le=1.0)
    reason_codes: tuple[ReasonCode, ...] = ()
    error_code: Literal[
        "judge_timeout", "invalid_judge_output", "judge_error"
    ] | None = None


class SemanticJudge:
    """Call an injected chat model and fail closed to ``unknown``."""

    def __init__(
        self,
        model: Any,
        *,
        timeout_seconds: float = 15.0,
        pass_threshold: float = 0.8,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 0.0 <= pass_threshold <= 1.0:
            raise ValueError("pass_threshold must be between 0 and 1")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.pass_threshold = pass_threshold

    async def evaluate(
        self,
        *,
        question: str,
        answer: str,
        required_facts: tuple[str, ...],
    ) -> SemanticJudgment:
        messages = self._messages(question, answer, required_facts)
        try:
            response = await asyncio.wait_for(
                self.model.ainvoke(messages),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return SemanticJudgment(
                status="unknown", error_code="judge_timeout"
            )
        except Exception:
            return SemanticJudgment(status="unknown", error_code="judge_error")

        content = getattr(response, "content", None)
        if not isinstance(content, str):
            return SemanticJudgment(
                status="unknown", error_code="invalid_judge_output"
            )
        try:
            payload = _JudgePayload.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError, TypeError):
            return SemanticJudgment(
                status="unknown", error_code="invalid_judge_output"
            )

        passed = (
            payload.relevance_score >= self.pass_threshold
            and payload.completeness_score >= self.pass_threshold
        )
        return SemanticJudgment(
            status="pass" if passed else "fail",
            relevance_score=payload.relevance_score,
            completeness_score=payload.completeness_score,
            reason_codes=payload.reason_codes,
        )

    @staticmethod
    def _messages(
        question: str,
        answer: str,
        required_facts: tuple[str, ...],
    ) -> list[dict[str, str]]:
        facts = "\n".join(f"- {item[:2000]}" for item in required_facts[:20])
        prompt = (
            "你是离线问答评测器，只评估答案相关性与必要信息完整性。"
            "不要输出分析过程、思维链或自然语言解释，只输出一个 JSON 对象。\n"
            "字段必须且只能包含：relevance_score、completeness_score、reason_codes。\n"
            "两个分数范围均为 0 到 1。reason_codes 最多 4 个，只能从以下值选择："
            "answers_user_intent, covers_required_facts, partially_relevant, "
            "missing_required_fact, off_topic, conflicting_answer。\n\n"
            f"用户问题：\n{question[:8000]}\n\n"
            f"候选答案：\n{answer[:12000]}\n\n"
            f"必要事实：\n{facts or '- 无显式必要事实'}"
        )
        return [{"role": "user", "content": prompt}]
