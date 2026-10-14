"""Pipeline stage state machine (pure domain logic; persistence is done by the application)."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from .base import ValueObject
from .errors import StageOrderError


class Stage(str, Enum):
    INIT = "INIT"
    INPUTS_LOADED = "INPUTS_LOADED"
    ALERTS_NORMALIZED = "ALERTS_NORMALIZED"
    INCIDENTS_CORRELATED = "INCIDENTS_CORRELATED"
    INCIDENTS_CLASSIFIED = "INCIDENTS_CLASSIFIED"
    REMEDIATION_PLANS_DRAFTED = "REMEDIATION_PLANS_DRAFTED"
    OPERATOR_REVIEW_COMPLETE = "OPERATOR_REVIEW_COMPLETE"
    INCIDENT_BRIEF_GENERATED = "INCIDENT_BRIEF_GENERATED"
    OPTIONAL_OUTPUTS_GENERATED = "OPTIONAL_OUTPUTS_GENERATED"
    VALIDATION_COMPLETE = "VALIDATION_COMPLETE"
    RESULTS_FINALISED = "RESULTS_FINALISED"

    @property
    def index(self) -> int:
        return list(Stage).index(self)


class StageTransition(ValueObject):
    stage: Stage
    entered_at: str
    artifacts: tuple[str, ...] = ()
    details: dict[str, Any] = Field(default_factory=dict)


class StageMachine:
    """Enforces the strict stage order. Entering a stage out of order raises."""

    def __init__(self, started_at: str) -> None:
        self._current = Stage.INIT
        self._history: list[StageTransition] = [StageTransition(stage=Stage.INIT, entered_at=started_at)]

    @property
    def current(self) -> Stage:
        return self._current

    @property
    def history(self) -> tuple[StageTransition, ...]:
        return tuple(self._history)

    def advance(self, to: Stage, at: str, artifacts: tuple[str, ...] = (), **details: Any) -> StageTransition:
        stages = list(Stage)
        expected = stages[self._current.index + 1] if self._current.index + 1 < len(stages) else None
        if to is not expected:
            raise StageOrderError(f"illegal stage transition {self._current.value} -> {to.value}; expected {expected.value if expected else 'none'}")
        transition = StageTransition(stage=to, entered_at=at, artifacts=artifacts, details=details)
        self._current = to
        self._history.append(transition)
        return transition

    def require_at_least(self, stage: Stage, what: str) -> None:
        if self._current.index < stage.index:
            raise StageOrderError(f"{what} requires stage >= {stage.value}; current stage is {self._current.value}")

    def require_exactly(self, stage: Stage, what: str) -> None:
        if self._current is not stage:
            raise StageOrderError(f"{what} requires stage == {stage.value}; current stage is {self._current.value}")
