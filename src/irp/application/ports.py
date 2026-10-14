"""Ports (interfaces) the application layer depends on. Infrastructure provides adapters."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from ..domain.alerts import RawAlertRecord
from ..domain.policy import RunbookPolicy
from ..domain.topology import ServiceTopology


class InputProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str
    sha256: str
    bytes: int
    read_at: str


class PipelineInputs(BaseModel):
    model_config = ConfigDict(frozen=True)
    alert_records: tuple[RawAlertRecord, ...]
    topology: ServiceTopology
    policy: RunbookPolicy
    raw_topology: dict[str, Any]
    raw_policy: dict[str, Any]
    provenance: dict[str, InputProvenance]


class InputSource(Protocol):
    """Reads alerts, topology and policy (e.g. from disk)."""

    def load(self) -> PipelineInputs: ...


class ArtifactStore(Protocol):
    """Persists pipeline artifacts and audit records."""

    def save_json(self, name: str, data: Any) -> str: ...
    def save_text(self, name: str, text: str) -> str: ...
    def append_jsonl(self, name: str, record: dict[str, Any]) -> None: ...
    def read_json(self, name: str) -> Any: ...
    def read_text(self, name: str) -> str: ...
    def exists(self, name: str) -> bool: ...
    def remove(self, name: str) -> None: ...
    def location(self, name: str) -> str: ...
    def sha256(self, name: str) -> str: ...


class LLMRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    stage: str
    messages: tuple[dict[str, str], ...]
    response_schema: dict[str, Any]
    schema_name: str
    payload: dict[str, Any]


@runtime_checkable
class LanguageModel(Protocol):
    """A provider that returns JSON matching ``request.response_schema``."""

    name: str
    model: str

    def complete(self, request: LLMRequest) -> dict[str, Any]: ...


class OperatorConsole(Protocol):
    """Human-in-the-loop channel for the review checkpoint."""

    mode: str

    def show(self, text: str) -> None: ...
    def commands(self) -> Iterator[str]: ...


class Clock(Protocol):
    def now(self) -> str: ...


class ReportPresenter(Protocol):
    """Formats human-facing reports (Markdown, console screens)."""

    def review_screen(self, view: Any) -> str: ...
    def review_summary(self, final_states: Any) -> str: ...
    def incident_brief(self, body: str, final_states: Any, provider_label: str, generated_at: str) -> str: ...
    def automation_plan(self, tiers: Any, narrative: str | None, provider_label: str) -> str: ...
    def postmortem(self, view: Any, provider_label: str) -> str: ...
