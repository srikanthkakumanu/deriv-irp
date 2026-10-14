"""Audited LLM gateway: stage guard, schema validation + one corrective retry,
call logging (llm_calls.jsonl), raw capture (llm_raw/) and a replay cache (llm_cache/)."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from ..domain.pipeline_stage import Stage, StageMachine
from ..shared.hashing import canonical_json, digest, sha256_text
from ..shared.paths import split_section
from .contracts import strict_json_schema
from .ports import ArtifactStore, Clock, LanguageModel, LLMRequest

T = TypeVar("T", bound=BaseModel)
LLM_CALLS_LOG = "llm_calls.jsonl"


class LLMGatewayError(RuntimeError):
    pass


class LLMTask(BaseModel, Generic[T]):
    """Everything needed for one logical LLM call."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    stage: str
    incident_id: str | None
    system_prompt: str
    instructions: str
    payload: dict[str, Any]
    response_model: type[BaseModel]
    input_artifacts: tuple[str, ...]
    output_artifact: str
    semantic_check: Callable[[BaseModel], list[str]] | None = None


class LLMCallRecord(BaseModel):
    call_id: str
    stage: str
    incident_id: str | None
    timestamp: str
    provider: str
    model: str
    provider_is_llm: bool
    prompt_hash: str
    input_artifacts: list[str]
    output_artifact: str
    input_sections: dict[str, str]
    output_section: str | None
    pipeline_stage_at_call: str
    attempts: int
    status: str
    latency_ms: float
    errors: list[str]
    raw_record: str


class AuditedLLMGateway:
    def __init__(self, model: LanguageModel, store: ArtifactStore, clock: Clock, stages: StageMachine,
                 replay: bool = False, max_attempts: int = 2,
                 artifact_path: Callable[[str], str] | None = None) -> None:
        self._artifact_path = artifact_path or (lambda name: name)
        self._model = model
        self._store = store
        self._clock = clock
        self._stages = stages
        self._replay = replay
        self._max_attempts = max_attempts
        self._seq = 0

    @property
    def provider_label(self) -> str:
        return (f"{self._model.name}:{self._model.model}" + (" (replay)" if self._replay else "")
                + ("" if self.uses_llm else " [rule-based stand-in, NOT an LLM]"))

    @property
    def uses_llm(self) -> bool:
        return self._model.is_llm

    @property
    def provider_name(self) -> str:
        return self._model.name

    @property
    def model_name(self) -> str:
        return self._model.model

    def set_artifact_path_resolver(self, resolver: Callable[[str], str]) -> None:
        """Maps logical artifact names (e.g. ``incident_analysis.json#stage1``) to real file paths."""
        self._artifact_path = resolver

    def _resolve_artifacts(self, task: LLMTask) -> tuple[list[str], str, dict[str, str], str | None]:
        sections: dict[str, str] = {}
        inputs: list[str] = []
        for name in task.input_artifacts:
            base, section = split_section(name)
            path = self._artifact_path(base)
            if path not in inputs:
                inputs.append(path)
            if section:
                sections[path] = section
        out_base, out_section = split_section(task.output_artifact)
        return inputs, self._artifact_path(out_base), sections, out_section

    def run(self, task: LLMTask) -> BaseModel:
        # Hard gate: no LLM call before deterministic normalisation and correlation.
        self._stages.require_at_least(Stage.INCIDENTS_CORRELATED, f"LLM call '{task.stage}'")
        self._seq += 1
        call_id = f"{self._seq:03d}-{task.stage}" + (f"-{task.incident_id}" if task.incident_id else "")
        schema = strict_json_schema(task.response_model)
        user = task.instructions + "\n\nINPUT (JSON):\n" + json.dumps(task.payload, indent=2, ensure_ascii=False, default=str)
        messages = ({"role": "system", "content": task.system_prompt}, {"role": "user", "content": user})
        prompt_hash = sha256_text(canonical_json({"model": self._model.model, "messages": messages, "schema": schema}))
        started = time.monotonic()
        errors: list[str] = []
        result: BaseModel | None = None
        raw: dict[str, Any] | None = None
        attempts = 0
        cache_name = f"llm_cache/{prompt_hash}.json"

        if self._replay:
            attempts = 1
            if not self._store.exists(cache_name):
                raise LLMGatewayError(f"--replay: no cached response for {task.stage} {task.incident_id or ''} (prompt_hash {prompt_hash[:12]})")
            raw = self._store.read_json(cache_name)["response"]
            result, problems = self._validate(task, raw)
            if result is None:
                raise LLMGatewayError(f"cached response for {task.stage} fails validation: {problems}")
        else:
            convo = list(messages)
            while attempts < self._max_attempts and result is None:
                attempts += 1
                request = LLMRequest(stage=task.stage, messages=tuple(convo), response_schema=schema,
                                     schema_name=task.stage[:64], payload=task.payload)
                try:
                    raw = self._model.complete(request)
                except Exception as exc:  # provider / network failure
                    errors.append(f"attempt {attempts}: provider error: {exc}")
                    continue
                result, problems = self._validate(task, raw)
                if result is None:
                    errors.append(f"attempt {attempts}: validation failed: {problems}")
                    convo = list(messages) + [
                        {"role": "assistant", "content": json.dumps(raw)},
                        {"role": "user", "content": "Your output violated these constraints; return corrected JSON only: " + "; ".join(problems)},
                    ]

        input_paths, output_path, input_sections, output_section = self._resolve_artifacts(task)
        record = LLMCallRecord(
            call_id=call_id, stage=task.stage, incident_id=task.incident_id, timestamp=self._clock.now(),
            provider=self._model.name + (" (replay)" if self._replay else ""), model=self._model.model,
            provider_is_llm=self._model.is_llm, prompt_hash=prompt_hash, input_artifacts=input_paths, output_artifact=output_path,
            input_sections=input_sections, output_section=output_section,
            pipeline_stage_at_call=self._stages.current.value, attempts=attempts,
            status="ok" if result is not None else "failed",
            latency_ms=round((time.monotonic() - started) * 1000, 1), errors=errors, raw_record=f"llm_raw/{call_id}.json",
        )
        self._store.save_json(f"llm_raw/{call_id}.json", {
            **record.model_dump(), "messages": list(messages), "response_schema": schema,
            "payload_digest": digest(task.payload), "response": raw,
        })
        self._store.append_jsonl(LLM_CALLS_LOG, record.model_dump())
        if result is None:
            raise LLMGatewayError(f"LLM call {task.stage} ({task.incident_id}) failed after {attempts} attempt(s): {errors}")
        if not self._replay:
            self._store.save_json(cache_name, {"stage": task.stage, "incident_id": task.incident_id, "response": raw})
        return result

    @staticmethod
    def _validate(task: LLMTask, raw: Any) -> tuple[BaseModel | None, list[str]]:
        try:
            parsed = task.response_model.model_validate(raw)
        except ValidationError as exc:
            return None, [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        problems = task.semantic_check(parsed) if task.semantic_check else []
        return (None, problems) if problems else (parsed, [])
