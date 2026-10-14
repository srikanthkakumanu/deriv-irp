"""Offline deterministic LanguageModel adapter.

Used when OPENAI_API_KEY is absent (or ``--provider offline``). It honours the same
request/response contract as the OpenAI provider, reads ONLY the structured payload
that is embedded in the prompt, and never hardcodes service names, alert IDs or
class labels: every label it emits is drawn from the policy in the payload.
It is logged in ``llm_calls.jsonl`` with ``provider = "offline-deterministic"``.
"""
from __future__ import annotations

import re
from typing import Any

from ..application.ports import LLMRequest

# Keyword synonyms keyed by tokens that may appear in policy class names.
TOKEN_SYNONYMS = {
    "market": ["tick", "packet", "ingest", "feed", "market"],
    "data": ["tick", "ingest", "feed"],
    "pricing": ["quote", "price", "pricing", "fresh"],
    "order": ["order", "reject", "fill", "execution"],
    "execution": ["order", "execution", "fill"],
    "edge": ["http", "5xx", "edge", "latency_edge"],
    "api": ["http", "5xx", "api"],
    "ai": ["inference", "gpu", "model", "feature"],
    "inference": ["inference", "gpu", "model", "feature"],
    "deployment": ["deploy", "canary", "rollout", "release"],
    "change": ["deploy", "canary", "rollout", "release", "change"],
}
SEV_W = {"critical": 3.0, "warning": 1.5, "info": 0.5}


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def _class_keywords(cls: str) -> set[str]:
    kws: set[str] = set()
    for tok in _tokens(cls):
        kws.update(TOKEN_SYNONYMS.get(tok, []))
    return kws


class OfflineModel:
    name = "offline-deterministic"
    model = "rule-based-v1"
    is_llm = False  # a rule-based stand-in, not a language model

    def complete(self, request: LLMRequest) -> dict[str, Any]:
        stage, payload = request.stage, request.payload
        if stage.startswith("stage1"):
            return self._stage1(payload)
        if stage.startswith("stage2"):
            return self._stage2(payload)
        if stage.startswith("stage3"):
            return self._stage3(payload)
        if stage.startswith("optional_postmortem"):
            return self._postmortem(payload)
        if stage.startswith("optional_automation"):
            return self._automation(payload)
        raise ValueError(f"offline provider: unsupported stage {stage}")

    # ---------------- Stage 1 ----------------
    def _stage1(self, p: dict[str, Any]) -> dict[str, Any]:
        classes, urgencies = p["allowed_incident_classes"], p["allowed_urgency_levels"]
        alerts = {a["alert_id"]: a for a in p["alerts"]}
        tiers = {s["name"]: s.get("tier") for s in p["service_topology"].get("services", [])}
        out = []
        for inc in p["correlated_incidents"]:
            members = [alerts[a] for a in inc["alert_ids"]]
            scores: dict[str, float] = {}
            for cls in classes:
                kws = _class_keywords(cls)
                if not kws:
                    continue
                sc = 0.0
                for a in members:
                    text = f"{a['metric']} {a['message']}".lower()
                    hits = sum(1 for k in kws if k in text)
                    if not hits:
                        continue
                    w = SEV_W[a["severity"]]
                    if a["service"] == inc["primary_service"]:
                        w *= 3  # root-cause service dominates
                    sc += w * min(hits, 2)
                scores[cls] = sc
            ranked = sorted(scores.items(), key=lambda kv: (-kv[1], classes.index(kv[0])))
            fallback = "unknown" if "unknown" in classes else classes[-1]
            if not ranked or ranked[0][1] == 0:
                cls, conf = fallback, "low"
            else:
                cls = ranked[0][0]
                runner = ranked[1][1] if len(ranked) > 1 else 0
                conf = "high" if ranked[0][1] >= 2 * max(runner, 1e-9) else "medium"
            urg = self._urgency(inc, members, tiers, urgencies)
            worst = max(members, key=lambda a: (SEV_W[a["severity"]], a["delta_from_threshold"] / (abs(a["threshold"]) or 1)))
            out.append({
                "incident_id": inc["incident_id"],
                "incident_class": cls,
                "urgency": urg,
                "customer_impact_summary": (
                    f"{len(inc['impacted_services'])} service(s) affected in {inc['region']} "
                    f"({', '.join(inc['impacted_services'])}); worst signal {worst['service']} {worst['metric']}="
                    f"{worst['observed_value']} vs threshold {worst['threshold']}."
                    + (" Critical trading path exposed." if inc.get("critical_path_services") else " No critical-path service directly impacted.")
                ),
                "suspected_root_cause": (
                    f"Most-upstream alerting service '{inc['primary_service']}' "
                    f"({', '.join(sorted({a['metric'] for a in members if a['service'] == inc['primary_service']}))}) "
                    f"propagating via topology to {', '.join(s for s in inc['impacted_services'] if s != inc['primary_service']) or 'no dependents'}."
                    + (" A concurrent deploy/change signal is present and must be ruled out." if inc["contains_deploy_signal"] else "")
                ),
                "confidence": conf,
            })
        return {"incidents": out}

    @staticmethod
    def _urgency(inc, members, tiers, urgencies) -> str:
        # urgency_levels are listed most-severe first (sev0..sevN)
        crit = [a for a in members if a["severity"] == "critical"]
        cp = bool(inc.get("critical_path_services")) or any(tiers.get(s) == "critical_path" for s in inc["impacted_services"])
        if crit and cp:
            idx = 1
        elif crit or (cp and len(members) >= 2):
            idx = 2
        elif len(members) >= 3:
            idx = 2
        else:
            idx = 3
        return urgencies[min(idx, len(urgencies) - 1)]

    # ---------------- Stage 2 ----------------
    def _stage2(self, p: dict[str, Any]) -> dict[str, Any]:
        inc, s1 = p["incident"], p["stage1_classification"]
        pol = p["action_policies_for_this_incident"]
        cls_kws = _class_keywords(s1["incident_class"]) | set(_tokens(s1["incident_class"]))
        deploy = p["contains_deploy_signal"]
        rec, avoid = [], []
        ordered = sorted(pol.items(), key=lambda kv: self._stage_rank(kv[0]))
        for action, info in ordered:
            if not info["permitted_for_this_incident"]:
                continue
            toks = set(_tokens(action))
            gated = info["requires_human_approval"]
            reason, risk, include = "", "", False
            if toks & {"page", "diagnostics", "diagnostic", "status", "collect"}:
                include, reason = True, "Low-risk triage/communication step permitted by policy for this incident."
                risk = "No production change; communications drafts are not published automatically."
            elif toks & {"freeze"}:
                include, reason = True, "Prevent further changes while the incident is active."
                risk = "Blocks unrelated hotfixes; lift once exit criteria hold."
            elif toks & {"rollback"}:
                if deploy:
                    include, reason = True, "A deploy/change signal coincides with the incident; rolling it back removes a candidate cause."
                    risk = "Rollback itself is a change; verify the canary/change scope first."
                else:
                    avoid.append({"action": action, "reason": "No deploy/change signal in this incident; rollback has no target."})
            elif toks & {"traffic", "region", "shift"}:
                avoid.append({"action": action, "reason": "Region shift moves order flow and market-data sessions; premature without evidence the region itself is unhealthy."})
            elif toks & cls_kws or (toks & {"quotes", "cached"} and cls_kws & {"quote", "price", "tick", "feed"}) \
                    or (toks & {"throttle"} and cls_kws & {"inference", "http", "edge", "api"}):
                include, reason = True, f"Targeted mitigation for {s1['incident_class']}."
                risk = "Changes live behaviour; requires human approval and a clear revert path."
            else:
                avoid.append({"action": action, "reason": "Not targeted at this incident's class; avoid unnecessary change."})
            if include:
                rec.append({"action": action, "reason": reason, "requires_human_approval": gated, "risk_note": risk})
        alerts = p["incident_alerts"]
        checks = [f"{a['service']} {a['metric']} <= {a['threshold']} (currently {a['observed_value']}) for 10 consecutive minutes"
                  for a in alerts if a["severity"] != "info"]
        return {
            "incident_id": inc["incident_id"],
            "recommended_actions": rec,
            "explicitly_avoided_actions": avoid,
            "rollback_consideration": (
                "Every mitigation must be individually reversible; revert any approved mitigation if the primary metric worsens "
                "within 5 minutes." + (" The concurrent deploy/change is the first rollback candidate." if deploy else "")
            ),
            "verification_checks": sorted(set(checks)),
        }

    @staticmethod
    def _stage_rank(action: str) -> tuple[int, str]:
        t = set(_tokens(action))
        if t & {"collect", "diagnostics"}:
            return (0, action)
        if t & {"page"}:
            return (1, action)
        if t & {"freeze", "status"}:
            return (2, action)
        return (3, action)

    # ---------------- Stage 3 ----------------
    def _stage3(self, p: dict[str, Any]) -> dict[str, Any]:
        fs, s1, s2 = p["final_state"], p["stage1_classifications"], p["stage2_remediation_plans"]
        incs = {i["incident_id"]: i for i in p["correlated_incidents"]}
        br = p["blast_radius_scores"]
        L: list[str] = []
        L += ["## Executive Summary", ""]
        for iid, st in fs.items():
            ov = f" (operator override from {st['original_urgency']})" if st["urgency_overridden"] else ""
            L.append(f"- **{iid}** — `{st['incident_class']}` at **{st['final_urgency']}**{ov}; primary service "
                     f"`{incs[iid]['primary_service']}`, blast radius {br[iid]['blast_radius_score']}/100. "
                     f"{len(st['approved_actions']) + len(st['auto_cleared_actions'])} action(s) cleared for execution, "
                     f"{len(st['pending_actions'])} pending, {len(st['rejected_actions']) + len(st['invalidated_actions'])} rejected/invalidated.")
        L += ["", "## Active Incidents", ""]
        for iid, st in fs.items():
            inc = incs[iid]
            L += [f"### {iid} — {st['incident_class']} — {st['final_urgency']}", "",
                  f"- Region: {inc['region']} · Window: {inc['start_ts']} → {inc['end_ts']}",
                  f"- Primary service: `{inc['primary_service']}`; impacted: {', '.join(inc['impacted_services'])}",
                  f"- Deploy/change signal: {'yes' if inc['contains_deploy_signal'] else 'no'}",
                  f"- Customer impact (AI): {s1[iid]['customer_impact_summary']}",
                  f"- Suspected root cause (AI, confidence {s1[iid]['confidence']}): {s1[iid]['suspected_root_cause']}",
                  f"- Blast radius: {br[iid]['justification']}", ""]
        L += ["## Immediate Approved Actions", ""]
        for iid, st in fs.items():
            for a in st["approved_actions"]:
                L.append(f"- {iid}: `{a}` — **operator-approved**, approved for execution (not executed by this system).")
            for a in st["auto_cleared_actions"]:
                L.append(f"- {iid}: `{a}` — **auto-cleared by policy** (no approval required; operator did not reject).")
            if not st["final_approved_action_set"]:
                L.append(f"- {iid}: none.")
        L += ["", "## Actions Pending Human Approval or Escalation", ""]
        for iid, st in fs.items():
            for e in st["actions"]:
                if e["status"] == "pending_approval":
                    L.append(f"- {iid}: `{e['action']}` — awaiting human approval. {e['note']}".rstrip())
            if not st["pending_actions"]:
                L.append(f"- {iid}: none pending.")
            if st["final_urgency"] in p["final_urgencies"].values() and st["final_urgency"] == min(p["final_urgencies"].values()):
                L.append(f"- {iid}: highest active urgency ({st['final_urgency']}); keep incident commander and platform lead engaged.")
        L += ["", "## Actions Explicitly Rejected", ""]
        for iid, st in fs.items():
            for e in st["actions"]:
                if e["status"] in ("rejected", "invalidated_by_override"):
                    L.append(f"- {iid}: `{e['action']}` — {e['status'].replace('_', ' ')}. {e['note']}".rstrip())
            for r in st["policy_rejected_actions"]:
                L.append(f"- {iid}: `{r['action']}` — filtered by policy: {r['reason']}")
            for r in s2[iid].get("explicitly_avoided_actions", []):
                L.append(f"- {iid}: `{r['action']}` — avoided by AI plan: {r['reason']}")
        L += ["", "## Verification and Exit Criteria", ""]
        for iid in fs:
            L.append(f"### {iid}")
            L += [f"- [ ] {c}" for c in s2[iid]["verification_checks"]]
            L.append(f"- Rollback: {s2[iid]['rollback_consideration']}")
            L.append("")
        L += ["## Communications Draft", "",
              "**Internal (incident channel):**", ""]
        for iid, st in fs.items():
            L.append(f"> {iid} ({st['final_urgency']}, {st['incident_class']}): investigating `{incs[iid]['primary_service']}`; "
                     f"approved: {', '.join(st['final_approved_action_set']) or 'none'}; pending: {', '.join(st['pending_actions']) or 'none'}.")
        L += ["", "**External status page — DRAFT, not published:**", "",
              "> We are investigating degraded performance affecting some platform services. Some requests may be delayed or rejected. "
              "Next update in 30 minutes.", ""]
        L += ["## Follow-Up Engineering Recommendations", ""]
        for iid, st in fs.items():
            inc = incs[iid]
            L.append(f"- {iid}: add alerting on leading indicators for `{inc['primary_service']}` and dependency-aware suppression for "
                     f"{', '.join(s for s in inc['impacted_services'] if s != inc['primary_service']) or 'its dependents'}.")
            if inc["contains_deploy_signal"]:
                L.append(f"- {iid}: gate canary deploys on critical-path health; auto-pause canaries during active incidents.")
        return {"brief_markdown": "\n".join(L)}

    # ---------------- Optional ----------------
    def _postmortem(self, p: dict[str, Any]) -> dict[str, Any]:
        inc, s1 = p["incident"], p["stage1_classification"]
        return {
            "incident_id": inc["incident_id"],
            "contributing_factors": [
                f"{s1['suspected_root_cause']}",
                f"Dependency fan-out from {inc['primary_service']} to {len(inc['impacted_services']) - 1} downstream service(s).",
            ] + (["Concurrent deploy/change during degradation increased diagnostic ambiguity."] if inc["contains_deploy_signal"] else []),
            "prevention_architecture": [f"Add graceful-degradation mode for dependents of {inc['primary_service']}.",
                                        "Bulkhead critical-path services from non-critical consumers."],
            "prevention_observability": [f"Leading-indicator SLOs on {inc['primary_service']}.",
                                         "Topology-aware alert grouping to reduce alert storms."],
            "prevention_process": ["Change-freeze automation when critical-path alerts fire.",
                                   "Runbook drill for this incident class."],
        }

    def _automation(self, p: dict[str, Any]) -> dict[str, Any]:
        return {"narrative": (
            "Automation should expand only for actions that are read-only or communicative and already exempt from approval "
            "in policy. State-changing mitigations stay human-gated until replayed incidents show the AI recommendation "
            "matched the operator decision consistently. Actions that move order flow or alter quoting carry direct "
            "market risk and stay manual."
        )}
