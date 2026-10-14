import pytest

from irp.domain.errors import InvalidDecisionError
from irp.domain.review import ActionOrigin, ActionStatus, ReviewSession
from tests.conftest import classification, proposal

TS = "2026-03-03T09:10:00Z"


@pytest.fixture
def session(policy):
    c1 = classification("INC-001", "market_data_degradation", "sev1")
    c2 = classification("INC-002", "ai_inference_degradation", "sev2")
    p1 = policy.enforce(proposal("INC-001", (("page_primary_oncall", False), ("page_platform_lead", False),
                                             ("switch_to_cached_quotes", True), ("freeze_deployments", False))), c1)
    p2 = policy.enforce(proposal("INC-002", (("disable_ai_assist_path", True), ("open_status_page_draft", False))), c2)
    return ReviewSession(policy, {"INC-001": c1, "INC-002": c2}, {"INC-001": p1, "INC-002": p2})


def status(session, iid, action):
    return next(o for o in session.final_states()[iid].actions if o.action == action)


class TestDefaults:
    def test_no_decisions(self, session):
        st = session.final_states()["INC-001"]
        assert set(st.auto_cleared_actions) == {"page_primary_oncall", "page_platform_lead", "freeze_deployments"}
        assert st.pending_actions == ("switch_to_cached_quotes",)
        assert st.approved_actions == () and not st.urgency_overridden


class TestDecisions:
    def test_approve_gated_action(self, session):
        session.approve("INC-001", "switch_to_cached_quotes", TS)
        o = status(session, "INC-001", "switch_to_cached_quotes")
        assert o.status is ActionStatus.APPROVED and o.decided_by == "operator"

    def test_reject_non_gated_action(self, session):
        session.reject("INC-001", "freeze_deployments", TS)
        assert status(session, "INC-001", "freeze_deployments").status is ActionStatus.REJECTED
        assert "freeze_deployments" not in session.final_states()["INC-001"].final_approved_action_set

    def test_last_decision_wins(self, session):
        session.approve("INC-001", "switch_to_cached_quotes", TS)
        session.reject("INC-001", "switch_to_cached_quotes", TS)
        assert status(session, "INC-001", "switch_to_cached_quotes").status is ActionStatus.REJECTED
        session.approve("INC-001", "switch_to_cached_quotes", TS)
        assert status(session, "INC-001", "switch_to_cached_quotes").status is ActionStatus.APPROVED

    def test_operator_can_add_permitted_action(self, session):
        session.approve("INC-001", "collect_additional_diagnostics", TS)
        o = status(session, "INC-001", "collect_additional_diagnostics")
        assert o.origin is ActionOrigin.OPERATOR and o.status is ActionStatus.APPROVED

    def test_cannot_approve_impermissible_action(self, session):
        with pytest.raises(InvalidDecisionError, match="cannot approve"):
            session.approve("INC-001", "disable_ai_assist_path", TS)

    def test_reject_of_action_not_in_plan_is_recorded(self, session):
        session.reject("INC-001", "shift_traffic_region", TS)
        assert status(session, "INC-001", "shift_traffic_region").status is ActionStatus.REJECTED

    @pytest.mark.parametrize("call", [
        lambda s: s.approve("INC-999", "page_primary_oncall", TS),
        lambda s: s.reject("INC-001", "launch_rockets", TS),
        lambda s: s.override_urgency("INC-001", "sev9", TS),
        lambda s: s.override_urgency("INC-404", "sev1", TS),
    ])
    def test_invalid_decisions_raise(self, session, call):
        with pytest.raises(InvalidDecisionError):
            call(session)
        assert session.decisions == ()


class TestUrgencyOverride:
    def test_downgrade_invalidates_urgency_gated_action(self, session):
        session.override_urgency("INC-001", "sev3", TS)
        st = session.final_states()["INC-001"]
        assert st.final_urgency == "sev3" and st.original_urgency == "sev1" and st.urgency_overridden
        assert "page_platform_lead" in st.invalidated_actions
        assert "page_platform_lead" not in st.final_approved_action_set

    def test_downgrade_voids_prior_approval(self, session):
        session.approve("INC-002", "open_status_page_draft", TS)
        session.override_urgency("INC-002", "sev3", TS)
        o = status(session, "INC-002", "open_status_page_draft")
        assert o.status is ActionStatus.INVALIDATED and "approval voided" in o.note

    def test_upgrade_unlocks_action_as_pending_not_auto_cleared(self, session):
        session.override_urgency("INC-002", "sev0", TS)
        o = status(session, "INC-002", "page_platform_lead")
        assert o.origin is ActionOrigin.UNLOCKED and o.status is ActionStatus.PENDING

    def test_override_order_does_not_matter(self, session, policy):
        session.override_urgency("INC-001", "sev3", TS)
        session.reject("INC-001", "freeze_deployments", TS)
        a = session.final_states()
        other = ReviewSession(policy, session._classifications, session._plans)
        other.reject("INC-001", "freeze_deployments", TS)
        other.override_urgency("INC-001", "sev3", TS)
        assert a == other.final_states()

    def test_decisions_are_logged_with_sequence(self, session):
        session.override_urgency("INC-001", "sev2", TS)
        session.approve("INC-001", "switch_to_cached_quotes", TS)
        assert [d.seq for d in session.decisions] == [1, 2]
        assert session.decisions[0].from_urgency == "sev1" and session.decisions[0].to_urgency == "sev2"
