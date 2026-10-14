"""Run validator: checks every requirement against the persisted artifacts.

Used by ``make validate`` / ``python validate.py`` and by the pipeline's VALIDATION stage.
It reads artifacts only through the ArtifactStore port, plus the current input files,
so it validates what is on disk, not in-memory state.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from ..domain.pipeline_stage import Stage
from ..domain.policy import RunbookPolicy
from ..domain.review import IncidentFinalState
from .contracts import LEDGER_HEADING
from .documents import OperatorDecisionsDocument
from .use_cases.generate_brief import brief_consistency_problems

REQUIRED_ARTIFACTS = (
    "alerts.jsonl", "service_topology.json", "runbook_policy.json", "normalized_alerts.json",
    "correlated_incidents.json", "incident_analysis.json", "operator_decisions.json", "incident_brief.md",
    "llm_calls.jsonl",
)
OPTIONAL_ARTIFACTS = ("blast_radius_scores.json", "safe_automation_plan.md", "postmortem_template.md", "policy_simulation.json")
LLM_RECORD_FIELDS = ("stage", "incident_id", "timestamp", "provider", "model", "prompt_hash", "input_artifacts", "output_artifact")
NORMALIZED_FIELDS = ("alert_id", "ts", "service", "severity", "metric", "observed_value", "threshold",
                     "delta_from_threshold", "region", "message")
INCIDENT_FIELDS = ("incident_id", "alert_ids", "primary_service", "impacted_services", "region",
                   "correlation_basis", "contains_deploy_signal")


class CheckResult(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class _Skip(Exception):
    pass


class RunValidator:
    def __init__(self, artifacts: Any, inputs: Any, during_run: bool = False) -> None:
        """``artifacts``: ArtifactStore for outputs; ``inputs``: ArtifactStore rooted at the input dir."""
        self._out = artifacts
        self._in = inputs
        self._during_run = during_run
        self._cache: dict[str, Any] = {}

    # -- loading helpers ---------------------------------------------------------
    def _json(self, name: str) -> Any:
        if name not in self._cache:
            self._cache[name] = self._out.read_json(name)
        return self._cache[name]

    def _jsonl(self, name: str) -> list[dict[str, Any]]:
        if name not in self._cache:
            self._cache[name] = [json.loads(l) for l in self._out.read_text(name).splitlines() if l.strip()]
        return self._cache[name]

    def _store_for(self, name: str) -> Any:
        return self._in if name in ("alerts.jsonl", "service_topology.json", "runbook_policy.json") else self._out

    @property
    def _policy(self) -> RunbookPolicy:
        return RunbookPolicy.model_validate(json.loads(self._in.read_text("runbook_policy.json")))

    @property
    def _calls(self) -> list[dict[str, Any]]:
        return self._jsonl("llm_calls.jsonl")

    @property
    def _decisions(self) -> OperatorDecisionsDocument:
        return OperatorDecisionsDocument.model_validate(self._json("operator_decisions.json"))

    # -- runner -----------------------------------------------------------------
    def run(self) -> list[CheckResult]:
        results: list[CheckResult] = []
        for name, fn in self._checks():
            try:
                detail = fn() or ""
                results.append(CheckResult(name=name, ok=True, detail=detail))
            except _Skip as exc:
                results.append(CheckResult(name=name, ok=True, detail=f"skipped: {exc}"))
            except (AssertionError, ValidationError, KeyError, ValueError, FileNotFoundError, TypeError) as exc:
                results.append(CheckResult(name=name, ok=False, detail=f"{type(exc).__name__}: {exc}"))
        return results

    def _checks(self) -> list[tuple[str, Callable[[], str | None]]]:
        return [
            ("required artifacts exist", self.check_required_exist),
            ("JSON artifacts are valid", self.check_json_valid),
            ("input files were read from disk (hashes match)", self.check_inputs_read),
            ("normalized alerts have the required schema", self.check_normalized_schema),
            ("correlated incidents have the required schema", self.check_incident_schema),
            ("normalization happened before any LLM call", lambda: self.check_stage_before_llm(Stage.ALERTS_NORMALIZED)),
            ("correlation happened before any LLM call", lambda: self.check_stage_before_llm(Stage.INCIDENTS_CORRELATED)),
            ("pipeline stages ran in the required order", self.check_stage_order),
            ("every incident has a Stage 1 classification", self.check_stage1_coverage),
            ("incident classes use only policy classes", self.check_classes),
            ("urgencies use only policy urgency levels", self.check_urgencies),
            ("exactly one Stage 1 call", self.check_single_stage1_call),
            ("each incident has its own Stage 2 call (not batched)", self.check_stage2_calls),
            ("recommended actions use only allowed actions", self.check_allowed_actions),
            ("action policy compatibility is enforced", self.check_policy_compat),
            ("approval flags come from policy", self.check_approval_flags),
            ("operator decisions saved and applied to Stage 3 inputs", self.check_decisions_applied),
            ("incident brief reflects post-review urgencies and actions", self.check_brief),
            ("llm_calls.jsonl has separate records for required stages", self.check_llm_log),
            ("blast radius scores are valid", self.check_blast_radius),
            ("policy simulation is valid", self.check_simulation),
        ]

    # -- checks -------------------------------------------------------------------
    def check_required_exist(self) -> str:
        missing = [n for n in REQUIRED_ARTIFACTS if not self._store_for(n).exists(n)]
        assert not missing, f"missing: {missing}"
        optional = [n for n in OPTIONAL_ARTIFACTS if self._out.exists(n)]
        return f"{len(REQUIRED_ARTIFACTS)} required present; optional present: {optional}"

    def check_json_valid(self) -> str:
        names = [n for n in REQUIRED_ARTIFACTS + OPTIONAL_ARTIFACTS + ("run_manifest.json",) if n.endswith(".json")]
        checked = 0
        for n in names:
            store = self._store_for(n)
            if store.exists(n):
                json.loads(store.read_text(n))
                checked += 1
        self._jsonl("llm_calls.jsonl")
        # Malformed alert rows are tolerated only if the run recorded them as rejected.
        rejected = {r["line_no"] for s in self._json("run_manifest.json")["stages"] if s["stage"] == Stage.ALERTS_NORMALIZED.value
                    for r in s.get("details", {}).get("rejected_rows", [])}
        bad: list[int] = []
        for i, line in enumerate(self._in.read_text("alerts.jsonl").splitlines(), 1):
            if line.strip():
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    bad.append(i)
        unreported = [i for i in bad if i not in rejected]
        assert not unreported, f"alerts.jsonl lines {unreported} are invalid JSON and were not reported as rejected"
        return f"{checked} JSON files + 2 JSONL files parsed" + (f"; {len(bad)} malformed alert row(s) reported as rejected" if bad else "")

    def check_inputs_read(self) -> str:
        manifest = self._json("run_manifest.json")
        for label, name in (("alerts", "alerts.jsonl"), ("topology", "service_topology.json"), ("policy", "runbook_policy.json")):
            rec = manifest["inputs"][label]
            actual = self._in.sha256(name)
            assert rec["sha256"] == actual, f"{name}: manifest hash {rec['sha256'][:12]} != file hash {actual[:12]} (inputs changed since run?)"
        return "sha256 of all 3 inputs match the run manifest"

    def check_normalized_schema(self) -> str:
        alerts = self._json("normalized_alerts.json")
        assert isinstance(alerts, list) and alerts, "normalized_alerts.json must be a non-empty list"
        for a in alerts:
            missing = [f for f in NORMALIZED_FIELDS if f not in a]
            assert not missing, f"{a.get('alert_id')}: missing {missing}"
            assert a["severity"] in ("info", "warning", "critical"), f"bad severity {a['severity']}"
            assert abs(a["delta_from_threshold"] - (a["observed_value"] - a["threshold"])) < 1e-6, f"{a['alert_id']}: wrong delta"
        ids = [a["alert_id"] for a in alerts]
        assert len(ids) == len(set(ids)), "duplicate alert_id"
        return f"{len(alerts)} alerts"

    def check_incident_schema(self) -> str:
        incidents = self._json("correlated_incidents.json")
        alert_ids = {a["alert_id"] for a in self._json("normalized_alerts.json")}
        seen: list[str] = []
        for inc in incidents:
            missing = [f for f in INCIDENT_FIELDS if f not in inc]
            assert not missing, f"{inc.get('incident_id')}: missing {missing}"
            assert inc["correlation_basis"], f"{inc['incident_id']}: empty correlation_basis"
            seen += inc["alert_ids"]
        assert sorted(seen) == sorted(alert_ids), "every normalized alert must belong to exactly one incident"
        return f"{len(incidents)} incidents covering {len(seen)} alerts"

    def _stage_time(self, stage: Stage) -> str:
        for s in self._json("run_manifest.json")["stages"]:
            if s["stage"] == stage.value:
                return s["entered_at"]
        raise AssertionError(f"stage {stage.value} not recorded in run_manifest.json")

    def check_stage_before_llm(self, stage: Stage) -> str:
        entered = self._stage_time(stage)
        first = min(c["timestamp"] for c in self._calls)
        assert entered < first, f"{stage.value} at {entered} is not before first LLM call at {first}"
        bad = [c["call_id"] for c in self._calls if Stage(c["pipeline_stage_at_call"]).index < stage.index]
        assert not bad, f"LLM calls made before {stage.value}: {bad}"
        return f"{stage.value} {entered} < first LLM call {first}"

    def check_stage_order(self) -> str:
        recorded = [s["stage"] for s in self._json("run_manifest.json")["stages"]]
        order = [s.value for s in Stage]
        assert recorded == order[: len(recorded)], f"stages out of order: {recorded}"
        need = Stage.OPTIONAL_OUTPUTS_GENERATED if self._during_run else Stage.RESULTS_FINALISED
        assert len(recorded) > need.index, f"run incomplete: last stage {recorded[-1]}"
        brief_at = self._stage_time(Stage.INCIDENT_BRIEF_GENERATED)
        review_at = self._stage_time(Stage.OPERATOR_REVIEW_COMPLETE)
        assert review_at < brief_at, "brief generated before operator review completed"
        s3 = [c for c in self._calls if c["stage"] == "stage3_brief"]
        assert s3 and all(c["pipeline_stage_at_call"] == Stage.OPERATOR_REVIEW_COMPLETE.value for c in s3), \
            "Stage 3 call not made at OPERATOR_REVIEW_COMPLETE"
        return " -> ".join(recorded)

    def _analysis(self) -> dict[str, Any]:
        return self._json("incident_analysis.json")

    def _incident_ids(self) -> list[str]:
        return [i["incident_id"] for i in self._json("correlated_incidents.json")]

    def check_stage1_coverage(self) -> str:
        s1 = self._analysis()["stage1"]
        missing = [i for i in self._incident_ids() if i not in s1]
        extra = [i for i in s1 if i not in self._incident_ids()]
        assert not missing and not extra, f"missing {missing}, unexpected {extra}"
        for iid, c in s1.items():
            for f in ("incident_class", "urgency", "customer_impact_summary", "suspected_root_cause", "confidence"):
                assert f in c, f"{iid}: missing {f}"
            assert c["confidence"] in ("low", "medium", "high"), f"{iid}: bad confidence"
        return f"{len(s1)} classifications"

    def check_classes(self) -> str:
        bad = {i: c["incident_class"] for i, c in self._analysis()["stage1"].items() if c["incident_class"] not in self._policy.incident_classes}
        assert not bad, f"classes not in policy: {bad}"
        return ", ".join(f"{i}={c['incident_class']}" for i, c in self._analysis()["stage1"].items())

    def check_urgencies(self) -> str:
        levels = self._policy.urgency_levels
        bad = {i: c["urgency"] for i, c in self._analysis()["stage1"].items() if c["urgency"] not in levels}
        bad.update({f"{i}(final)": s.final_urgency for i, s in self._decisions.final_state.items() if s.final_urgency not in levels})
        assert not bad, f"urgencies not in policy: {bad}"
        return "stage1 and final urgencies all within " + "|".join(levels)

    def check_single_stage1_call(self) -> str:
        s1 = [c for c in self._calls if c["stage"] == "stage1_classification"]
        assert len(s1) == 1, f"expected exactly 1 Stage 1 call, found {len(s1)}"
        return "1 call"

    def check_stage2_calls(self) -> str:
        s2 = [c for c in self._calls if c["stage"] == "stage2_remediation"]
        ids = [c["incident_id"] for c in s2]
        assert None not in ids, "a Stage 2 record has incident_id=null (batched call?)"
        assert sorted(ids) == sorted(self._incident_ids()), f"Stage 2 calls {ids} != incidents {self._incident_ids()}"
        assert len(set(c["prompt_hash"] for c in s2)) == len(s2), "Stage 2 calls share a prompt hash (batched?)"
        for c in s2:
            raw = self._out.read_json(c["raw_record"])
            user = raw["messages"][1]["content"]
            others = [i for i in self._incident_ids() if i != c["incident_id"] and f'"incident_id": "{i}"' in user]
            assert not others, f"Stage 2 call for {c['incident_id']} contains other incidents {others}"
        stage2 = self._analysis()["stage2"]
        assert sorted(stage2) == sorted(self._incident_ids()), "incident_analysis.json stage2 does not cover every incident"
        return f"{len(s2)} separate calls: {ids}"

    def check_allowed_actions(self) -> str:
        allowed = set(self._policy.allowed_actions)
        n = 0
        for iid, plan in self._analysis()["stage2"].items():
            for a in plan["recommended_actions"]:
                assert a["action"] in allowed, f"{iid}: {a['action']} not in allowed_actions"
                n += 1
            for a in plan.get("explicitly_avoided_actions", []):
                assert a["action"] in allowed, f"{iid}: avoided {a['action']} not in allowed_actions"
        for iid, st in self._decisions.final_state.items():
            for o in st.actions:
                assert o.action in allowed, f"{iid}: final action {o.action} not allowed"
        return f"{n} recommended actions checked"

    def check_policy_compat(self) -> str:
        policy, s1 = self._policy, self._analysis()["stage1"]
        for iid, plan in self._analysis()["stage2"].items():
            cls, urg = s1[iid]["incident_class"], s1[iid]["urgency"]
            for a in plan["recommended_actions"]:
                assert policy.is_permitted(a["action"], cls, urg), f"{iid}: {a['action']} not permitted for {cls}/{urg}"
        for iid, st in self._decisions.final_state.items():
            for o in st.actions:
                if o.status.value in ("approved", "auto_cleared_by_policy", "pending_approval"):
                    assert policy.is_permitted(o.action, st.incident_class, st.final_urgency), \
                        f"{iid}: {o.action} is {o.status.value} but impermissible at final urgency {st.final_urgency}"
                if o.status.value == "auto_cleared_by_policy":
                    assert not policy.requires_approval(o.action), f"{iid}: gated action {o.action} was auto-cleared"
        return "Stage 2 plans and post-review actions comply with action_policies"

    def check_approval_flags(self) -> str:
        policy = self._policy
        for iid, plan in self._analysis()["stage2"].items():
            for a in plan["recommended_actions"]:
                assert a["requires_human_approval"] == policy.requires_approval(a["action"]), f"{iid}: {a['action']} approval flag differs from policy"
        return "ok"

    def check_decisions_applied(self) -> str:
        doc = self._decisions
        assert doc.stage3_input_digest, "operator_decisions.json lacks stage3_input_digest"
        s3 = [c for c in self._calls if c["stage"] == "stage3_brief"]
        assert len(s3) == 1, f"expected exactly 1 Stage 3 call, found {len(s3)}"
        raw = self._out.read_json(s3[0]["raw_record"])
        assert raw["payload_digest"] == doc.stage3_input_digest, "Stage 3 input digest does not match the saved operator decisions"
        user = raw["messages"][1]["content"]
        payload = json.loads(user.split("INPUT (JSON):\n", 1)[1])
        for iid, st in doc.final_state.items():
            p = payload["final_state"][iid]
            assert p["final_urgency"] == st.final_urgency, f"{iid}: Stage 3 got urgency {p['final_urgency']} != {st.final_urgency}"
            assert sorted(p["final_approved_action_set"]) == sorted(st.final_approved_action_set), f"{iid}: Stage 3 approved set differs"
            assert sorted(p["rejected_actions"]) == sorted(st.rejected_actions), f"{iid}: Stage 3 rejected set differs"
        # Replaying the logged decisions over the AI plans must reproduce the saved final state.
        from ..domain.remediation import Classification, RemediationPlan
        from ..domain.review import ReviewSession
        a = self._analysis()
        session = ReviewSession(self._policy,
                                {k: Classification.model_validate(v) for k, v in a["stage1"].items()},
                                {k: RemediationPlan.model_validate(v) for k, v in a["stage2"].items()})
        for d in doc.decisions:
            if d.type == "urgency":
                session.override_urgency(d.incident_id, d.to_urgency or "", d.ts)
            elif d.type == "approve":
                session.approve(d.incident_id, d.action or "", d.ts)
            else:
                session.reject(d.incident_id, d.action or "", d.ts)
        replayed = {k: v.model_dump(mode="json") for k, v in session.final_states().items()}
        saved = {k: v.model_dump(mode="json") for k, v in doc.final_state.items()}
        assert replayed == saved, "replaying logged decisions does not reproduce the saved final_state"
        return f"{len(doc.decisions)} decision(s) applied; Stage 3 digest matches"

    def check_brief(self) -> str:
        md = self._out.read_text("incident_brief.md")
        final: dict[str, IncidentFinalState] = self._decisions.final_state
        problems = brief_consistency_problems(md, final)
        assert not problems, "; ".join(problems)
        assert LEDGER_HEADING in md, "Authoritative Action Ledger missing"
        ledger = md.split(LEDGER_HEADING, 1)[1]
        for iid, st in final.items():
            assert f"final urgency **{st.final_urgency}**" in ledger, f"{iid}: ledger does not state final urgency {st.final_urgency}"
            for o in st.actions:
                assert f"| `{o.action}` | **{o.status.value}** |" in ledger, f"{iid}: ledger row for {o.action} != {o.status.value}"
        assert "No infrastructure actions have been executed" in md, "AI/no-execution banner missing"
        return "8 sections present; urgencies and action statuses match operator_decisions.json"

    def check_llm_log(self) -> str:
        for c in self._calls:
            missing = [f for f in LLM_RECORD_FIELDS if f not in c]
            assert not missing, f"{c.get('call_id')}: missing {missing}"
            assert c["status"] == "ok", f"{c['call_id']}: status {c['status']}"
        stages = [c["stage"] for c in self._calls]
        for required in ("stage1_classification", "stage2_remediation", "stage3_brief"):
            assert required in stages, f"no {required} record"
        if self._out.exists("postmortem_template.md"):
            assert "optional_postmortem" in stages, "postmortem generated without a logged LLM call"
        if self._out.exists("safe_automation_plan.md"):
            assert "optional_automation_plan" in stages, "automation plan generated without a logged LLM call"
        return f"{len(self._calls)} records: " + ", ".join(sorted(set(stages)))

    def check_blast_radius(self) -> str:
        if not self._out.exists("blast_radius_scores.json"):
            raise _Skip("not generated")
        doc = self._json("blast_radius_scores.json")
        assert doc.get("formula") and doc["computed_by"] == "deterministic_code"
        ids = [s["incident_id"] for s in doc["scores"]]
        assert sorted(ids) == sorted(self._incident_ids()), "blast radius scores do not cover every incident"
        for s in doc["scores"]:
            assert 0 <= s["blast_radius_score"] <= 100
            total = min(100, sum(f["points"] for f in s["contributing_factors"].values()))
            assert total == s["blast_radius_score"], f"{s['incident_id']}: factors sum {total} != score"
            assert s["justification"]
        return ", ".join(f"{s['incident_id']}={s['blast_radius_score']}" for s in doc["scores"])

    def check_simulation(self) -> str:
        if not self._out.exists("policy_simulation.json"):
            raise _Skip("not generated")
        sim = self._json("policy_simulation.json")
        assert sim["llm_calls"] == 0 and sim["computed_by"] == "deterministic_code"
        names = [s["scenario"] for s in sim["scenarios"]]
        assert any("disable_ai_assist_path" in n for n in names) and any("deploy" in n for n in names), names
        for s in sim["scenarios"]:
            assert {"before", "after", "diff"} <= set(s), f"{s['scenario']}: missing before/after/diff"
        return f"scenarios: {names}"
