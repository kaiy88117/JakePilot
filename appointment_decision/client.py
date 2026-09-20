"""OpenAI-compatible client for a locally served appointment model."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import requests

from .contracts import DecisionRequest


_SYSTEM_INSTRUCTION = """You are an appointment decision component.
Return exactly one JSON object and no prose or Markdown.
Allowed actions: ask_user, query_slots, request_confirmation, finish.
The object must contain action, slots, and missing_slots. Never invent values.
Only choose an action listed in allowed_actions. Preserve confirmed_slots exactly.
"""


@dataclass(slots=True)
class LocalDecisionClient:
    endpoint: str = "http://127.0.0.1:8080/v1/chat/completions"
    model: str = "appointment-decision-q4_k_m"
    timeout_seconds: float = 4.0
    api_key: str | None = None
    session: requests.Session = field(default_factory=requests.Session)

    @classmethod
    def from_env(cls) -> "LocalDecisionClient":
        endpoint = os.getenv("APPOINTMENT_LOCAL_ENDPOINT")
        if not endpoint:
            base_url = os.getenv(
                "APPOINTMENT_LOCAL_BASE_URL",
                "http://127.0.0.1:8080/v1",
            ).rstrip("/")
            endpoint = f"{base_url}/chat/completions"
        return cls(
            endpoint=endpoint,
            model=os.getenv(
                "APPOINTMENT_LOCAL_MODEL",
                "appointment-decision-q4_k_m",
            ),
            timeout_seconds=float(
                os.getenv("APPOINTMENT_LOCAL_TIMEOUT_SECONDS", "4")
            ),
            api_key=os.getenv("APPOINTMENT_LOCAL_API_KEY") or None,
        )

    def complete(self, request: DecisionRequest) -> str:
        user_data = request.model_dump(mode="json")
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _SYSTEM_INSTRUCTION},
                {
                    "role": "user",
                    "content": json.dumps(
                        user_data,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        response = self.session.post(
            self.endpoint,
            json=payload,
            headers=headers,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("local response content must be a string")
        return content
