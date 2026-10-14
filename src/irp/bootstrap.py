"""Composition root: wires infrastructure adapters into the application layer."""
from __future__ import annotations

import getpass
import os
from pathlib import Path

from pydantic import BaseModel

from .application.pipeline import IncidentResponsePipeline, PipelineSettings
from .application.ports import Clock, LanguageModel, OperatorConsole
from .application.validation import RunValidator
from .infrastructure.clock import SystemClock
from .infrastructure.filesystem import FileSystemArtifactStore, FileSystemInputSource
from .infrastructure.markdown_presenter import MarkdownPresenter
from .infrastructure.offline_model import OfflineModel
from .infrastructure.openai_model import OpenAIModel
from .interface.operator_console import ScriptedOperatorConsole, TerminalOperatorConsole

DEFAULT_OPENAI_MODEL = "gpt-4.1"
DEFAULT_INPUT_DIR = Path("data")            # alerts.jsonl, service_topology.json, runbook_policy.json
DEFAULT_OUTPUT_DIR = Path("data/output")    # every generated artifact, llm_raw/ and llm_cache/


class RunConfig(BaseModel):
    input_dir: Path = DEFAULT_INPUT_DIR
    out_dir: Path = DEFAULT_OUTPUT_DIR
    provider: str = "auto"  # auto | openai | offline
    decisions_file: Path | None = None
    replay: bool = False
    correlation_window_s: int = 120
    strict: bool = False
    include_optional_outputs: bool = True


def build_language_model(provider: str) -> LanguageModel:
    choice = provider
    if choice == "auto":
        choice = "openai" if os.environ.get("OPENAI_API_KEY") else "offline"
    if choice == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("--provider openai requires OPENAI_API_KEY (set it in .env or the environment)")
        return OpenAIModel(model=os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL))
    if choice == "offline":
        return OfflineModel()
    raise ValueError(f"unknown provider {provider!r}")


def operator_name() -> str:
    if name := os.environ.get("IRP_OPERATOR"):
        return name
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover
        return "unknown"


def build_pipeline(config: RunConfig, console: OperatorConsole | None = None, clock: Clock | None = None,
                   model: LanguageModel | None = None) -> IncidentResponsePipeline:
    clock = clock or SystemClock()
    if console is None:
        console = (ScriptedOperatorConsole.from_file(config.decisions_file) if config.decisions_file
                   else TerminalOperatorConsole())
    settings = PipelineSettings(
        correlation_window_s=config.correlation_window_s, strict_normalization=config.strict,
        include_optional_outputs=config.include_optional_outputs, replay=config.replay, operator_name=operator_name(),
        order_path_pattern=os.environ.get("IRP_ORDER_PATH_PATTERN", PipelineSettings().order_path_pattern),
    )
    return IncidentResponsePipeline(
        inputs=FileSystemInputSource(config.input_dir, clock),
        store=FileSystemArtifactStore(config.out_dir),
        input_store=FileSystemArtifactStore(config.input_dir),
        model=model or build_language_model(config.provider),
        console=console, presenter=MarkdownPresenter(), clock=clock, settings=settings,
    )


def build_validator(input_dir: Path, out_dir: Path) -> RunValidator:
    return RunValidator(FileSystemArtifactStore(out_dir), FileSystemArtifactStore(input_dir))
