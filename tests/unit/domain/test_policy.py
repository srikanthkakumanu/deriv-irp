import pytest
from pydantic import ValidationError

from irp.domain.policy import RunbookPolicy
from tests.conftest import classification, proposal


class TestPermission:
    def test_class_based_permission(self, policy):
        assert policy.is_permitted("switch_to_cached_quotes", "pricing_staleness", "sev3")
        assert not policy.is_permitted("switch_to_cached_quotes", "ai_inference_degradation", "sev0")

    def test_urgency_based_permission(self, policy):
        assert policy.is_permitted("page_platform_lead", "unknown", "sev1")
        assert not policy.is_permitted("page_platform_lead", "unknown", "sev2")

    def test_action_outside_allowed_actions(self, policy):
        perm = policy.permission("reboot_everything", "unknown", "sev0")
        assert not perm.allowed and "not in policy allowed_actions" in perm.reason

    def test_action_without_policy_entry_is_permitted_but_gated(self, raw_policy):
        raw_policy["allowed_actions"].append("restart_pod")
        policy = RunbookPolicy.model_validate(raw_policy)
        assert policy.is_permitted("restart_pod", "unknown", "sev3")
        assert policy.requires_approval("restart_pod")


class TestEnforce:
    def test_filters_disallowed_actions(self, policy):
        plan = policy.enforce(proposal(actions=(("disable_ai_assist_path", True), ("page_primary_oncall", False))),
                              classification(incident_class="market_data_degradation"))
        assert plan.action_names == ["page_primary_oncall"]
        assert plan.policy_rejected_actions[0].action == "disable_ai_assist_path"
        assert "policy violation" in plan.policy_rejected_actions[0].reason

    def test_approval_flag_comes_from_policy(self, policy):
        plan = policy.enforce(proposal(actions=(("switch_to_cached_quotes", False),)), classification())
        action = plan.recommended_actions[0]
        assert action.requires_human_approval is True
        assert action.llm_approval_flag_mismatch is True

    def test_duplicates_removed(self, policy):
        plan = policy.enforce(proposal(actions=(("page_primary_oncall", False), ("page_primary_oncall", False))), classification())
        assert plan.action_names == ["page_primary_oncall"]
        assert plan.policy_rejected_actions[0].reason == "duplicate recommendation removed"

    def test_avoided_actions_that_are_also_recommended_are_dropped(self, policy):
        from irp.domain.remediation import AvoidedAction
        prop = proposal(actions=(("page_primary_oncall", False),)).model_copy(
            update={"explicitly_avoided_actions": (AvoidedAction(action="page_primary_oncall", reason="x"),)})
        assert policy.enforce(prop, classification()).explicitly_avoided_actions == ()

    def test_everything_filtered_leaves_empty_plan(self, policy):
        plan = policy.enforce(proposal(actions=(("disable_ai_assist_path", True),)), classification())
        assert plan.recommended_actions == () and len(plan.policy_rejected_actions) == 1


class TestPolicyModel:
    def test_with_preapproved_returns_copy(self, policy):
        changed = policy.with_preapproved("disable_ai_assist_path")
        assert not changed.requires_approval("disable_ai_assist_path")
        assert policy.requires_approval("disable_ai_assist_path")

    def test_duplicates_rejected(self, raw_policy):
        raw_policy["urgency_levels"] = ["sev1", "sev1"]
        with pytest.raises(ValidationError):
            RunbookPolicy.model_validate(raw_policy)

    def test_empty_classes_rejected(self, raw_policy):
        raw_policy["incident_classes"] = []
        with pytest.raises(ValidationError):
            RunbookPolicy.model_validate(raw_policy)

    def test_fallback_class(self, policy, raw_policy):
        assert policy.fallback_class == "unknown"
        raw_policy["incident_classes"] = ["a", "b"]
        assert RunbookPolicy.model_validate(raw_policy).fallback_class == "b"

    def test_view_for_marks_permissions(self, policy):
        view = policy.view_for("ai_inference_degradation", "sev3")
        assert view["disable_ai_assist_path"].permitted_for_this_incident
        assert not view["switch_to_cached_quotes"].permitted_for_this_incident
        assert not view["open_status_page_draft"].permitted_for_this_incident
