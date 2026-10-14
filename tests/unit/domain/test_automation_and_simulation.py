from irp.domain.automation import AutomationTier, AutomationTiering
from irp.domain.blast_radius import BlastRadiusCalculator
from irp.domain.incident import IncidentCorrelator
from irp.domain.policy import RunbookPolicy
from irp.domain.review import ReviewSession
from irp.domain.simulation import PolicySimulator
from tests.conftest import classification, proposal


def tiers_by_action(policy, final_states=None):
    return {t.action: t.tier for t in AutomationTiering(policy).classify(final_states or {})}


class TestAutomationTiering:
    def test_tiers_for_sample_policy(self, policy):
        tiers = tiers_by_action(policy)
        assert tiers["collect_additional_diagnostics"] is AutomationTier.FULL
        assert tiers["page_primary_oncall"] is AutomationTier.FULL
        assert tiers["freeze_deployments"] is AutomationTier.CONDITIONAL
        assert tiers["rollback_latest_change"] is AutomationTier.CONDITIONAL
        assert tiers["switch_to_cached_quotes"] is AutomationTier.NEVER
        assert tiers["shift_traffic_region"] is AutomationTier.NEVER

    def test_every_allowed_action_in_exactly_one_tier(self, policy):
        assert set(tiers_by_action(policy)) == set(policy.allowed_actions)

    def test_action_without_policy_is_never_automated(self, raw_policy):
        raw_policy["allowed_actions"].append("collect_heap_dump")
        assert tiers_by_action(RunbookPolicy.model_validate(raw_policy))["collect_heap_dump"] is AutomationTier.NEVER

    def test_conditional_actions_have_guardrails(self, policy):
        for t in AutomationTiering(policy).classify({}):
            assert (t.guardrails is not None) == (t.tier is AutomationTier.CONDITIONAL)


class TestPolicySimulator:
    def _setup(self, policy, topology, sample_alerts, decisions=()):
        correlator = IncidentCorrelator(topology)
        incidents = correlator.correlate(sample_alerts)
        c = {"INC-001": classification("INC-001", "market_data_degradation", "sev1"),
             "INC-002": classification("INC-002", "ai_inference_degradation", "sev2")}
        plans = {
            "INC-001": policy.enforce(proposal("INC-001", (("page_primary_oncall", False), ("freeze_deployments", False))), c["INC-001"]),
            "INC-002": policy.enforce(proposal("INC-002", (("disable_ai_assist_path", True), ("rollback_latest_change", True))), c["INC-002"]),
        }
        session = ReviewSession(policy, c, plans)
        for fn in decisions:
            fn(session)
        calc = BlastRadiusCalculator(topology)
        scores = {b.incident_id: b.blast_radius_score for b in calc.score_all(incidents, sample_alerts)}
        sim = PolicySimulator(policy, topology, correlator, calc)
        return sim.run(sample_alerts, incidents, c, plans, list(session.decisions), session.final_states(), scores)

    def test_preapproval_moves_pending_to_cleared(self, policy, topology, sample_alerts):
        report = self._setup(policy, topology, sample_alerts)
        a = report.scenarios[0]
        assert a.diff["INC-002"].moved_pending_to_cleared == ("disable_ai_assist_path",)
        assert not a.diff["INC-001"].changed and not a.no_change

    def test_preapproval_no_change_when_operator_rejected(self, policy, topology, sample_alerts):
        report = self._setup(policy, topology, sample_alerts,
                             [lambda s: s.reject("INC-002", "disable_ai_assist_path", "t")])
        assert report.scenarios[0].diff["INC-002"].moved_pending_to_cleared == ()
        # ...but the policy effect is still visible before operator review
        assert report.scenarios[0].pre_review_diff["INC-002"].moved_pending_to_cleared == ("disable_ai_assist_path",)

    def test_ignore_deploys_recomputes_blast_and_drops_rollback(self, policy, topology, sample_alerts):
        b = self._setup(policy, topology, sample_alerts).scenarios[1]
        assert len(b.dropped_alert_ids) == 1
        assert b.diff["INC-001"].blast_radius.delta == -10
        assert b.diff["INC-002"].deploy_dependent_actions_removed == ("rollback_latest_change",)
        assert all(not i.contains_deploy_signal for i in b.incidents_after_recorrelation)

    def test_scenario_a_noop_when_action_absent(self, raw_policy, topology, sample_alerts):
        raw_policy["allowed_actions"].remove("disable_ai_assist_path")
        del raw_policy["action_policies"]["disable_ai_assist_path"]
        policy = RunbookPolicy.model_validate(raw_policy)
        correlator = IncidentCorrelator(topology)
        incidents = correlator.correlate(sample_alerts)
        c = {i.incident_id: classification(i.incident_id) for i in incidents}
        plans = {k: policy.enforce(proposal(k, (("page_primary_oncall", False),)), v) for k, v in c.items()}
        report = PolicySimulator(policy, topology, correlator, BlastRadiusCalculator(topology)).run(
            sample_alerts, incidents, c, plans, [], ReviewSession(policy, c, plans).final_states(), {})
        assert report.scenarios[0].no_change
        assert report.llm_calls == 0
