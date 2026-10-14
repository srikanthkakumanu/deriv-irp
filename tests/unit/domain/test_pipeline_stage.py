import pytest

from irp.domain.errors import StageOrderError
from irp.domain.pipeline_stage import Stage, StageMachine


def test_full_order_is_accepted():
    sm = StageMachine("t0")
    for i, stage in enumerate(list(Stage)[1:], 1):
        sm.advance(stage, f"t{i}")
    assert sm.current is Stage.RESULTS_FINALISED
    assert [t.stage for t in sm.history] == list(Stage)


def test_skipping_a_stage_raises():
    sm = StageMachine("t0")
    sm.advance(Stage.INPUTS_LOADED, "t1")
    with pytest.raises(StageOrderError, match="expected ALERTS_NORMALIZED"):
        sm.advance(Stage.INCIDENTS_CORRELATED, "t2")


def test_brief_cannot_precede_review():
    sm = StageMachine("t0")
    with pytest.raises(StageOrderError):
        sm.advance(Stage.INCIDENT_BRIEF_GENERATED, "t1")


def test_cannot_advance_past_final():
    sm = StageMachine("t0")
    for stage in list(Stage)[1:]:
        sm.advance(stage, "t")
    with pytest.raises(StageOrderError):
        sm.advance(Stage.RESULTS_FINALISED, "t")


def test_guards():
    sm = StageMachine("t0")
    with pytest.raises(StageOrderError):
        sm.require_at_least(Stage.INCIDENTS_CORRELATED, "LLM call")
    with pytest.raises(StageOrderError):
        sm.require_exactly(Stage.OPERATOR_REVIEW_COMPLETE, "brief")
    sm.require_at_least(Stage.INIT, "noop")
