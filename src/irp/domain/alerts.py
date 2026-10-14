"""Alert aggregate: raw telemetry records -> normalized alerts (deterministic)."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import field_serializer

from ..shared.hashing import canonical_json, sha256_text
from .base import ValueObject
from .errors import NormalizationError

CHANGE_METRIC_RE = re.compile(r"deploy|release|rollout|change", re.I)
CHANGE_MESSAGE_RE = re.compile(r"deploy|canary|rollout|release", re.I)


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "critical": 2}[self.value]


def format_utc(ts: datetime) -> str:
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ" if ts.microsecond else "%Y-%m-%dT%H:%M:%SZ"
    return ts.astimezone(timezone.utc).strftime(fmt)


def parse_utc(raw: str) -> datetime:
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class RawAlertRecord(ValueObject):
    """One line of the telemetry file as delivered by an input adapter."""

    line_no: int
    payload: dict[str, Any] | None = None
    parse_error: str | None = None


class Alert(ValueObject):
    alert_id: str
    ts: datetime
    service: str
    severity: Severity
    metric: str
    observed_value: float
    threshold: float
    delta_from_threshold: float
    region: str
    message: str
    source_line: int

    @field_serializer("ts")
    def _serialize_ts(self, ts: datetime) -> str:
        return format_utc(ts)

    @property
    def is_change_signal(self) -> bool:
        return bool(CHANGE_METRIC_RE.search(self.metric) or CHANGE_MESSAGE_RE.search(self.message))

    @property
    def sort_key(self) -> tuple[datetime, str]:
        return (self.ts, self.alert_id)


class RejectedRecord(ValueObject):
    line_no: int
    reason: str


class NormalizationResult(ValueObject):
    alerts: tuple[Alert, ...]
    rejected: tuple[RejectedRecord, ...]


class AlertNormalizer:
    """Deterministic normalization (no LLM).

    * ``alert_id`` = ``ALR-<row:04d>-<sha256(canonical payload)[:8]>`` (stable per file)
    * ``ts`` -> UTC; ``severity`` lower-cased and validated
    * ``delta_from_threshold`` = ``observed_value - threshold`` rounded to 6 dp
    * output sorted by ``(ts, alert_id)``
    """

    REQUIRED_FIELDS: tuple[str, ...] = ("ts", "service", "severity", "metric", "value", "threshold", "region", "message")

    def __init__(self, strict: bool = False) -> None:
        self._strict = strict

    def normalize(self, records: Iterable[RawAlertRecord]) -> NormalizationResult:
        alerts: list[Alert] = []
        rejected: list[RejectedRecord] = []
        for row, record in enumerate(records, start=1):
            try:
                alerts.append(self._normalize_one(row, record))
            except NormalizationError as exc:
                if self._strict:
                    raise
                rejected.append(RejectedRecord(line_no=record.line_no, reason=str(exc)))
        alerts.sort(key=lambda a: a.sort_key)
        return NormalizationResult(alerts=tuple(alerts), rejected=tuple(rejected))

    def _normalize_one(self, row: int, record: RawAlertRecord) -> Alert:
        line = record.line_no
        if record.parse_error is not None or record.payload is None:
            raise NormalizationError(f"line {line}: invalid JSON ({record.parse_error})")
        raw: Mapping[str, Any] = record.payload
        missing = [f for f in self.REQUIRED_FIELDS if f not in raw]
        if missing:
            raise NormalizationError(f"line {line}: missing fields {missing}")
        try:
            severity = Severity(str(raw["severity"]).strip().lower())
        except ValueError as exc:
            raise NormalizationError(f"line {line}: unknown severity {raw['severity']!r}") from exc
        try:
            ts = parse_utc(raw["ts"])
        except ValueError as exc:
            raise NormalizationError(f"line {line}: unparseable ts {raw['ts']!r}") from exc
        value = self._number(raw["value"], "value", line)
        threshold = self._number(raw["threshold"], "threshold", line)
        return Alert(
            alert_id=f"ALR-{row:04d}-{sha256_text(canonical_json(dict(raw)))[:8]}",
            ts=ts,
            service=str(raw["service"]).strip(),
            severity=severity,
            metric=str(raw["metric"]).strip(),
            observed_value=value,
            threshold=threshold,
            delta_from_threshold=round(value - threshold, 6),
            region=str(raw["region"]).strip(),
            message=str(raw["message"]).strip(),
            source_line=line,
        )

    @staticmethod
    def _number(value: Any, field: str, line: int) -> float:
        if isinstance(value, bool):
            raise NormalizationError(f"line {line}: field '{field}' is boolean, expected number")
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise NormalizationError(f"line {line}: field '{field}' is not numeric: {value!r}") from exc
