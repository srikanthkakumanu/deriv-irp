import pytest

from irp.application.review_commands import CommandSyntaxError, parse_command
from irp.application.use_cases.conduct_review import ConductOperatorReview
from irp.application.use_cases.generate_brief import brief_consistency_problems
from irp.application.contracts import BRIEF_SECTIONS
from irp.application.documents import ReviewView
from irp.domain.review import ReviewSession
from irp.infrastructure.clock import FixedStepClock
from irp.infrastructure.markdown_presenter import MarkdownPresenter
from tests.conftest import classification, proposal
from tests.fakes import ListConsole


class TestParser:
    def test_valid(self):
        cmd = parse_command("APPROVE INC-001 page_platform_lead")
        assert cmd.verb == "approve" and cmd.incident_id == "INC-001" and cmd.argument == "page_platform_lead"

    @pytest.mark.parametrize("line", ["approve INC-001", "approve INC-001 a b", "escalate INC-001 x", ""])
    def test_invalid(self, line):
        with pytest.raises(CommandSyntaxError):
            parse_command(line)


def _review(policy, lines):
    c = classification("INC-001", "market_data_degradation", "sev1")
    plan = policy.enforce(proposal("INC-001", (("page_platform_lead", False), ("switch_to_cached_quotes", True))), c)
    session = ReviewSession(policy, {"INC-001": c}, {"INC-001": plan})
    console = ListConsole(lines)
    view = ReviewView(incidents=[], classifications={"INC-001": c}, plans={"INC-001": plan}, blast={}, prompt="PROMPT")
    doc = ConductOperatorReview(console, MarkdownPresenter(), FixedStepClock(), "tester").execute(session, view)
    return doc, console


class TestConductReview:
    def test_errors_are_reported_and_logged_but_not_applied(self, policy):
        doc, console = _review(policy, ["bogus", "approve INC-404 page_platform_lead", "reject INC-001 switch_to_cached_quotes", ""])
        assert [c.status for c in doc.raw_commands] == ["error", "error", "applied"]
        assert len(doc.decisions) == 1
        assert doc.final_state["INC-001"].rejected_actions == ("switch_to_cached_quotes",)
        assert any("! " in s for s in console.shown)

    def test_empty_line_ends_review(self, policy):
        doc, _ = _review(policy, ["", "reject INC-001 switch_to_cached_quotes"])
        assert doc.decisions == []

    def test_eof_without_blank_line_ends_review(self, policy):
        doc, _ = _review(policy, ["urgency INC-001 sev3"])
        st = doc.final_state["INC-001"]
        assert st.final_urgency == "sev3" and "page_platform_lead" in st.invalidated_actions

    def test_prompt_is_shown(self, policy):
        _, console = _review(policy, [])
        assert "PROMPT" in console.shown[0]


class TestBriefConsistency:
    def _state(self, policy, lines):
        doc, _ = _review(policy, lines)
        return doc.final_state

    def _brief(self, approved="", pending="", rejected="", urgency="sev1"):
        body = {s: "" for s in BRIEF_SECTIONS}
        body["Executive Summary"] = f"INC-001 at {urgency}"
        body["Immediate Approved Actions"] = approved
        body["Actions Pending Human Approval or Escalation"] = pending
        body["Actions Explicitly Rejected"] = rejected
        return "\n".join(f"## {k}\n{v}" for k, v in body.items())

    def test_consistent_brief_passes(self, policy):
        st = self._state(policy, ["reject INC-001 switch_to_cached_quotes"])
        assert brief_consistency_problems(self._brief(approved="page_platform_lead", rejected="switch_to_cached_quotes"), st) == []

    def test_rejected_action_must_be_listed(self, policy):
        st = self._state(policy, ["reject INC-001 switch_to_cached_quotes"])
        assert any("rejected action" in p for p in brief_consistency_problems(self._brief(approved="page_platform_lead"), st))

    def test_final_urgency_must_appear(self, policy):
        st = self._state(policy, ["urgency INC-001 sev2"])
        problems = brief_consistency_problems(self._brief(approved="page_platform_lead", pending="switch_to_cached_quotes"), st)
        assert any("final urgency sev2" in p for p in problems)

    def test_missing_section_detected(self, policy):
        st = self._state(policy, [])
        assert "missing sections" in brief_consistency_problems("## Executive Summary\nINC-001 sev1", st)[0]
