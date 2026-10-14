TASK: Stage 2 — remediation plan for ONE incident (`incident.incident_id`).

Using the incident record, its Stage 1 classification, the per-action policy view in `action_policies_for_this_incident`, `allowed_actions`, and `contains_deploy_signal`:
- recommended_actions: ordered, staged list (diagnose/communicate first, then reversible mitigations, then riskier steps). Only use actions where `permitted_for_this_incident` is true. For each: why (cite the evidence), `requires_human_approval` copied from policy, and a risk_note (market risk, blast radius, rollback path).
- explicitly_avoided_actions: allowed actions you deliberately do NOT recommend, with the reason (policy disallows, unsafe, premature, or market risk).
- rollback_consideration: how each mitigation would be reverted and what would trigger a rollback; mention the deploy/change signal if present.
- verification_checks: concrete, measurable checks (metric + threshold + time window) that show the incident is resolving.
Remember: these are AI recommendations only. A human operator approves or rejects them later.
