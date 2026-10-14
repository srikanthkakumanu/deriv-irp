from datetime import timezone

import pytest

from irp.domain.alerts import AlertNormalizer, RawAlertRecord, Severity, parse_utc
from irp.domain.errors import NormalizationError
from tests.conftest import raw_alert, records


class TestAlertNormalizer:
    def test_sample_file_normalizes_all_rows(self, sample_records):
        result = AlertNormalizer().normalize(sample_records)
        assert len(result.alerts) == 10
        assert result.rejected == ()

    def test_is_deterministic(self, sample_records):
        first = AlertNormalizer().normalize(sample_records)
        second = AlertNormalizer().normalize(sample_records)
        assert [a.model_dump() for a in first.alerts] == [a.model_dump() for a in second.alerts]

    def test_required_fields_and_delta(self, sample_alerts):
        a = sample_alerts[0]
        assert a.alert_id.startswith("ALR-0001-")
        assert a.delta_from_threshold == pytest.approx(a.observed_value - a.threshold)
        assert a.model_dump(mode="json")["ts"] == "2026-03-03T09:00:00Z"

    def test_alert_id_depends_on_content(self):
        a = AlertNormalizer().normalize(records(raw_alert(value=1))).alerts[0]
        b = AlertNormalizer().normalize(records(raw_alert(value=2))).alerts[0]
        assert a.alert_id != b.alert_id

    def test_sorted_by_timestamp_even_if_file_is_not(self):
        result = AlertNormalizer().normalize(records(raw_alert(ts="2026-03-03T09:00:10Z"), raw_alert(ts="2026-03-03T09:00:01Z")))
        assert [a.source_line for a in result.alerts] == [2, 1]

    def test_severity_is_case_insensitive(self):
        assert AlertNormalizer().normalize(records(raw_alert(severity="CRITICAL"))).alerts[0].severity is Severity.CRITICAL

    def test_numeric_strings_are_accepted(self):
        alert = AlertNormalizer().normalize(records(raw_alert(value="7.5", threshold="1"))).alerts[0]
        assert alert.delta_from_threshold == 6.5

    def test_negative_delta_below_threshold(self):
        assert AlertNormalizer().normalize(records(raw_alert(value=50, threshold=100))).alerts[0].delta_from_threshold == -50

    def test_timezone_offsets_are_converted_to_utc(self):
        alert = AlertNormalizer().normalize(records(raw_alert(ts="2026-03-03T10:00:00+01:00"))).alerts[0]
        assert alert.model_dump(mode="json")["ts"] == "2026-03-03T09:00:00Z"

    def test_naive_timestamp_assumed_utc(self):
        assert parse_utc("2026-03-03T09:00:00").tzinfo == timezone.utc

    @pytest.mark.parametrize("bad, reason", [
        (raw_alert(severity="fatal"), "unknown severity"),
        (raw_alert(ts="yesterday"), "unparseable ts"),
        (raw_alert(value="lots"), "not numeric"),
        (raw_alert(value=True), "boolean"),
        ({k: v for k, v in raw_alert().items() if k != "region"}, "missing fields"),
    ])
    def test_invalid_rows_are_rejected_with_reason(self, bad, reason):
        result = AlertNormalizer().normalize(records(raw_alert(), bad))
        assert len(result.alerts) == 1
        assert reason in result.rejected[0].reason
        assert result.rejected[0].line_no == 2

    def test_unparseable_json_line_is_rejected(self):
        result = AlertNormalizer().normalize([RawAlertRecord(line_no=3, parse_error="Expecting value")])
        assert result.alerts == () and "line 3" in result.rejected[0].reason

    def test_strict_mode_raises_on_first_error(self):
        with pytest.raises(NormalizationError):
            AlertNormalizer(strict=True).normalize(records(raw_alert(severity="nope")))

    def test_empty_input(self):
        result = AlertNormalizer().normalize([])
        assert result.alerts == () and result.rejected == ()

    @pytest.mark.parametrize("metric, message, expected", [
        ("deploy_event", "x", True),
        ("cpu", "Canary deployment started", True),
        ("release_marker", "x", True),
        ("cpu", "cpu high", False),
    ])
    def test_change_signal_detection(self, metric, message, expected):
        alert = AlertNormalizer().normalize(records(raw_alert(metric=metric, message=message))).alerts[0]
        assert alert.is_change_signal is expected
