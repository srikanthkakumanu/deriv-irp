"""OpenAI LanguageModel adapter using Structured Outputs (strict JSON schema)."""
from __future__ import annotations

import json
from typing import Any

from ..application.ports import LLMRequest


class OpenAIModel:
    name = "openai"

    def __init__(self, model: str, temperature: float = 0.0, seed: int = 7, timeout_s: float = 60.0) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("openai package not installed; run `make setup` or use --provider offline") from exc
        self._client = OpenAI(timeout=timeout_s)
        self.model = model
        self._temperature = temperature
        self._seed = seed
        self._send_temperature = True

    def complete(self, request: LLMRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": list(request.messages),
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": request.schema_name, "strict": True, "schema": request.response_schema}},
            "seed": self._seed,
        }
        if self._send_temperature:
            kwargs["temperature"] = self._temperature
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            # Some reasoning models reject `temperature`; retry once without it.
            if self._send_temperature and "temperature" in str(exc).lower():
                self._send_temperature = False
                kwargs.pop("temperature", None)
                response = self._client.chat.completions.create(**kwargs)
            else:
                raise
        message = response.choices[0].message
        if getattr(message, "refusal", None):
            raise RuntimeError(f"model refused: {message.refusal}")
        return json.loads(message.content or "{}")
