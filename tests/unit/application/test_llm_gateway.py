import json

import pytest
from pydantic import BaseModel

from irp.application.llm_gateway import AuditedLLMGateway, LLMGatewayError, LLMTask
from irp.domain.errors import StageOrderError
from irp.domain.pipeline_stage import Stage, StageMachine
from irp.infrastructure.clock import FixedStepClock
from tests.fakes import FakeModel, MemoryStore


class Out(BaseModel):
    value: int


def task(check=None, **kw):
    return LLMTask(stage="stage_x", incident_id=kw.get("iid"), system_prompt="sys", instructions="do it",
                   payload={"k": 1}, response_model=Out, input_artifacts=("a.json",), output_artifact="b.json",
                   semantic_check=check)


def correlated_machine():
    sm = StageMachine("t0")
    for s in (Stage.INPUTS_LOADED, Stage.ALERTS_NORMALIZED, Stage.INCIDENTS_CORRELATED):
        sm.advance(s, "t")
    return sm


def gateway(model, store=None, replay=False, sm=None):
    return AuditedLLMGateway(model, store or MemoryStore(), FixedStepClock(), sm or correlated_machine(), replay=replay)


def calls(store):
    return [json.loads(l) for l in store.read_text("llm_calls.jsonl").splitlines()]


def test_success_logs_one_record_with_required_fields():
    store = MemoryStore()
    out = gateway(FakeModel([{"value": 3}]), store).run(task(iid="INC-001"))
    assert out.value == 3
    [rec] = calls(store)
    for f in ("stage", "incident_id", "timestamp", "provider", "model", "prompt_hash", "input_artifacts", "output_artifact"):
        assert f in rec
    assert rec["incident_id"] == "INC-001" and rec["attempts"] == 1 and rec["status"] == "ok"
    assert store.exists(rec["raw_record"]) and store.exists(f"llm_cache/{rec['prompt_hash']}.json")


def test_schema_violation_triggers_one_corrective_retry():
    model = FakeModel([{"value": "nope"}, {"value": 5}])
    store = MemoryStore()
    assert gateway(model, store).run(task()).value == 5
    assert len(model.requests) == 2
    assert "violated these constraints" in model.requests[1].messages[-1]["content"]
    assert calls(store)[0]["attempts"] == 2


def test_semantic_check_failure_retries_then_fails():
    model = FakeModel([{"value": 1}, {"value": 1}])
    store = MemoryStore()
    with pytest.raises(LLMGatewayError):
        gateway(model, store).run(task(check=lambda o: ["must be even"] if o.value % 2 else []))
    assert calls(store)[0]["status"] == "failed"


def test_provider_exception_is_retried():
    model = FakeModel([RuntimeError("timeout"), {"value": 9}])
    assert gateway(model).run(task()).value == 9


def test_llm_call_before_correlation_is_blocked():
    with pytest.raises(StageOrderError):
        gateway(FakeModel([{"value": 1}]), sm=StageMachine("t0")).run(task())


def test_replay_serves_cached_response_without_calling_model():
    store = MemoryStore()
    gateway(FakeModel([{"value": 4}]), store).run(task())
    replay_model = FakeModel([])
    assert gateway(replay_model, store, replay=True).run(task()).value == 4
    assert replay_model.requests == []
    assert calls(store)[-1]["provider"].endswith("(replay)")


def test_replay_missing_cache_fails():
    with pytest.raises(LLMGatewayError, match="no cached response"):
        gateway(FakeModel([]), replay=True).run(task())


def test_prompt_hash_changes_with_payload():
    store = MemoryStore()
    g = gateway(FakeModel([{"value": 1}, {"value": 1}]), store)
    g.run(task())
    g.run(task().model_copy(update={"payload": {"k": 2}}))
    h = [c["prompt_hash"] for c in calls(store)]
    assert h[0] != h[1]
