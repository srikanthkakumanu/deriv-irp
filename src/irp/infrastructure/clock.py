"""Clock adapters."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


class SystemClock:
    def now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class FixedStepClock:
    """Deterministic clock for tests: each call advances by ``step_ms``."""

    def __init__(self, start: str = "2026-01-01T00:00:00", step_ms: int = 1) -> None:
        self._t = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        self._step = timedelta(milliseconds=step_ms)

    def now(self) -> str:
        self._t += self._step
        return self._t.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
