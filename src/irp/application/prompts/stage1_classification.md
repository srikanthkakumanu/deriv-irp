TASK: Stage 1 — incident classification and urgency assignment.

For EVERY incident in `correlated_incidents` (and only those), return one entry with:
- incident_id: exactly as given
- incident_class: exactly one value from `policy.incident_classes`
- urgency: exactly one value from `policy.urgency_levels` (listed most-severe first)
- customer_impact_summary: 1-2 sentences on trader/client-facing impact, grounded in the alert values
- suspected_root_cause: the most likely upstream cause, using the topology (dependencies point upstream) and the correlation basis; name the evidence
- confidence: low | medium | high

Guidance:
- Classify by the most upstream causal service, not by the loudest symptom. Choose a deployment- or change-related class (if the policy defines one) only when the change signal is the most plausible cause.
- Urgency should reflect critical-path / order-flow exposure and market risk, not just alert count.
- The `alerts` list contains the normalised alerts referenced by `alert_ids`.
