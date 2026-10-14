"""Test doubles for application ports."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from irp.application.ports import LLMRequest


class FakeModel:
    """LanguageModel double: returns queued responses (or calls a function) and records requests."""

    name = "fake"
    model = "fake-1"
    is_llm = True

    def __init__(self, responses: list[Any] | Callable[[LLMRequest], Any]) -> None:
        self._responses = responses
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> dict[str, Any]:
        self.requests.append(request)
        if callable(self._responses):
            return self._responses(request)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class MemoryStore:
    """In-memory ArtifactStore."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    def save_json(self, name: str, data: Any) -> str:
        import json
        self.files[name] = json.dumps(data, default=str)
        return name

    def save_text(self, name: str, text: str) -> str:
        self.files[name] = text
        return name

    def append_jsonl(self, name: str, record: dict[str, Any]) -> None:
        import json
        self.files[name] = self.files.get(name, "") + json.dumps(record, default=str) + "\n"

    def read_json(self, name: str) -> Any:
        import json
        return json.loads(self.files[name])

    def read_text(self, name: str) -> str:
        return self.files[name]

    def exists(self, name: str) -> bool:
        return name in self.files

    def remove(self, name: str) -> None:
        for key in [k for k in self.files if k == name or k.startswith(name + "/")]:
            del self.files[key]

    def location(self, name: str) -> str:
        return f"mem://{name}"

    def sha256(self, name: str) -> str:
        import hashlib
        return hashlib.sha256(self.files[name].encode()).hexdigest()


class ListConsole:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.shown: list[str] = []
        self.mode = "test"

    def show(self, text: str) -> None:
        self.shown.append(text)

    def commands(self) -> Iterator[str]:
        yield from self._lines
