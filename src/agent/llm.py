from __future__ import annotations

import json
import os
from typing import Any, Protocol

import httpx

DEFAULT_BASE_URL = os.environ.get(
    "SPARK_BASE_URL",
    "https://maas-api.cn-huabei-1.xf-yun.com/v2/chat/completions",
)
DEFAULT_MODEL = os.environ.get("SPARK_MODEL", "spark-x2.6-4b")


class LLMClient(Protocol):
    def chat(self, messages: list[dict[str, str]]) -> str: ...


class EchoLLM:
    """Deterministic stand-in used by tests and offline runs."""

    def chat(self, messages: list[dict[str, str]]) -> str:
        user = messages[-1]["content"]
        return json.dumps({"echo": user[:200]}, ensure_ascii=False)


class SparkLLM:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("SPARK_API_KEY", "")
        self.base_url = base_url
        self.model = model
        self.timeout = timeout

    def chat(self, messages: list[dict[str, str]]) -> str:
        if not self.api_key:
            raise RuntimeError("SPARK_API_KEY is not set")
        response = httpx.post(
            self.base_url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            json={"model": self.model, "messages": messages, "stream": False},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data["choices"][0]["message"]["content"]
