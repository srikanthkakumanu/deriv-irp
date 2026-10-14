
## BUILD

Build a replayable incident-response orchestration pipeline that ingests live-like platform telemetry, classifies alerts, correlates them into incidents, generates a remediation plan with explicit safety gates, supports an operator approval/override checkpoint, and produces an execution-ready incident briefing plus post-incident recommendations.

This is not a generic monitoring dashboard or one-shot summary task. The evaluator will run your pipeline from a clean checkout, may replace the input telemetry and policy fixtures with equivalent files, and will verify that alert parsing is deterministic, incident classification follows the provided operating policy, escalation and remediation recommendations are staged, and operator decisions materially affect downstream outputs.

The pipeline should reflect production-quality thinking for an engineering leader operating in a high-stakes environment: controlled automation, rollback awareness, auditability, latency sensitivity, and separation between analysis and action.

Your system must preserve intermediate artifacts, enforce controlled incident classes and action policies, log LLM calls, and clearly distinguish AI-generated recommendations from approved operational actions.

---

## SCENARIO

You are designing an AI-assisted control-plane workflow for a trading platform engineering team. The platform has multiple signals coming from market-data ingestion, order-routing, pricing, API gateways, and model-serving infrastructure. During incidents, engineers need fast triage, but unsafe automation can worsen outages or introduce market risk.

Your task is to build a staged assistant that:

- reads telemetry and policy files from disk
- deterministically normalises and correlates events
- uses policy-constrained LLM analysis for incident classification and remediation planning
- pauses for human approval or override before any action plan is finalised
- emits clear operational outputs and audit logs

The goal is to demonstrate engineering judgment around AI-native operations rather than to build a chat interface.

---

## INPUT FILES

Your pipeline must read these files from disk:

- `alerts.jsonl`
- `service_topology.json`
- `runbook_policy.json`

The sample fixtures below are for local testing. The evaluator may replace them with equivalent files. Your implementation must not depend on exact alert IDs, wording, timestamps, service names, or final classifications from the public fixture.

---

## SAMPLE `alerts.jsonl`

```jsonl
{"ts":"2026-03-03T09:00:00Z","service":"market-data-gateway","severity":"warning","metric":"tick_ingest_lag_ms","value":180,"threshold":120,"region":"eu-west-1","message":"Tick ingestion lag above threshold"}
{"ts":"2026-03-03T09:00:08Z","service":"market-data-gateway","severity":"critical","metric":"tick_ingest_lag_ms","value":420,"threshold":120,"region":"eu-west-1","message":"Tick ingestion lag spiking"}
{"ts":"2026-03-03T09:00:10Z","service":"pricing-engine","severity":"critical","metric":"quote_freshness_ms_p95","value":950,"threshold":250,"region":"eu-west-1","message":"Quote freshness degraded"}
{"ts":"2026-03-03T09:00:12Z","service":"order-router","severity":"critical","metric":"order_reject_rate_pct","value":7.2,"threshold":1.0,"region":"eu-west-1","message":"Order rejects increased"}
{"ts":"2026-03-03T09:00:15Z","service":"api-edge","severity":"warning","metric":"http_5xx_rate_pct","value":2.8,"threshold":1.0,"region":"eu-west-1","message":"5xx elevated"}
{"ts":"2026-03-03T09:00:18Z","service":"feature-store","severity":"warning","metric":"feature_fetch_latency_ms_p95","value":180,"threshold":100,"region":"eu-west-1","message":"Feature latency elevated"}
{"ts":"2026-03-03T09:00:20Z","service":"model-serving","severity":"warning","metric":"inference_latency_ms_p95","value":310,"threshold":180,"region":"eu-west-1","message":"Inference latency elevated"}
{"ts":"2026-03-03T09:00:22Z","service":"model-serving","severity":"warning","metric":"gpu_memory_utilization_pct","value":96,"threshold":90,"region":"eu-west-1","message":"GPU memory pressure high"}
{"ts":"2026-03-03T09:00:25Z","service":"market-data-gateway","severity":"critical","metric":"packet_drop_rate_pct","value":4.4,"threshold":0.5,"region":"eu-west-1","message":"Packet loss detected upstream"}
{"ts":"2026-03-03T09:00:29Z","service":"api-edge","severity":"info","metric":"deploy_event","value":1,"threshold":0,"region":"eu-west-1","message":"Canary deployment started for edge cache refresh"}
```

---

## SAMPLE `service_topology.json`

```json
{
  "services": [
    {"name": "market-data-gateway", "depends_on": [], "tier": "critical_path"},
    {"name": "pricing-engine", "depends_on": ["market-data-gateway"], "tier": "critical_path"},
    {"name": "order-router", "depends_on": ["pricing-engine", "api-edge"], "tier": "critical_path"},
    {"name": "api-edge", "depends_on": [], "tier": "edge"},
    {"name": "feature-store", "depends_on": [], "tier": "ai_support"},
    {"name": "model-serving", "depends_on": ["feature-store"], "tier": "ai_support"}
  ]
}
```

---

## SAMPLE `runbook_policy.json`

```json
{
  "incident_classes": [
    "market_data_degradation",
    "pricing_staleness",
    "order_execution_risk",
    "edge_api_instability",
    "ai_inference_degradation",
    "deployment_related_change",
    "unknown"
  ],
  "urgency_levels": ["sev0", "sev1", "sev2", "sev3"],
  "allowed_actions": [
    "page_primary_oncall",
    "page_platform_lead",
    "freeze_deployments",
    "rollback_latest_change",
    "shift_traffic_region",
    "disable_ai_assist_path",
    "switch_to_cached_quotes",
    "throttle_non_critical_requests",
    "collect_additional_diagnostics",
    "open_status_page_draft"
  ],
  "action_policies": {
    "switch_to_cached_quotes": {
      "requires_human_approval": true,
      "max_allowed_if": ["pricing_staleness", "market_data_degradation"]
    },
    "shift_traffic_region": {
      "requires_human_approval": true,
      "max_allowed_if": ["edge_api_instability", "market_data_degradation"]
    },
    "rollback_latest_change": {
      "requires_human_approval": true,
      "max_allowed_if": ["deployment_related_change", "edge_api_instability", "ai_inference_degradation"]
    },
    "disable_ai_assist_path": {
      "requires_human_approval": true,
      "max_allowed_if": ["ai_inference_degradation"]
    },
    "freeze_deployments": {
      "requires_human_approval": false,
      "max_allowed_if": ["deployment_related_change", "order_execution_risk", "pricing_staleness", "market_data_degradation"]
    },
    "page_primary_oncall": {
      "requires_human_approval": false,
      "max_allowed_if": ["market_data_degradation", "pricing_staleness", "order_execution_risk", "edge_api_instability", "ai_inference_degradation", "deployment_related_change", "unknown"]
    },
    "page_platform_lead": {
      "requires_human_approval": false,
      "max_allowed_if": ["sev0", "sev1"]
    },
    "throttle_non_critical_requests": {
      "requires_human_approval": true,
      "max_allowed_if": ["edge_api_instability", "ai_inference_degradation"]
    },
    "collect_additional_diagnostics": {
      "requires_human_approval": false,
      "max_allowed_if": ["market_data_degradation", "pricing_staleness", "order_execution_risk", "edge_api_instability", "ai_inference_degradation", "deployment_related_change", "unknown"]
    },
    "open_status_page_draft": {
      "requires_human_approval": false,
      "max_allowed_if": ["sev0", "sev1", "sev2"]
    }
  }
}
```

---

## PIPELINE STAGES

Your implementation must enforce these stages in code:

```text
INIT
 -> INPUTS_LOADED
 -> ALERTS_NORMALIZED
 -> INCIDENTS_CORRELATED
 -> INCIDENTS_CLASSIFIED
 -> REMEDIATION_PLANS_DRAFTED
 -> OPERATOR_REVIEW_COMPLETE
 -> INCIDENT_BRIEF_GENERATED
 -> OPTIONAL_OUTPUTS_GENERATED
 -> VALIDATION_COMPLETE
 -> RESULTS_FINALISED
```

The incident brief must not be generated until alert normalization, correlation, classification, remediation drafting, and operator review are complete.

---

## MUST COMPLETE

### 1. Deterministic Alert Normalization

Parse `alerts.jsonl` deterministically before any LLM call.

Save output to `normalized_alerts.json`.

Each normalized alert must include:

```json
{
  "alert_id": "string",
  "ts": "ISO-8601 string",
  "service": "string",
  "severity": "info | warning | critical",
  "metric": "string",
  "observed_value": 0,
  "threshold": 0,
  "delta_from_threshold": 0,
  "region": "string",
  "message": "string"
}
```

`alert_id` may be derived deterministically from row order or content hash.

Do not ask the LLM to parse or normalize raw alerts.

---

### 2. Deterministic Incident Correlation

Group normalized alerts into one or more incidents using deterministic code.

Save output to `correlated_incidents.json`.

Your correlation logic must be explicit and documented. It should use some combination of:

- time proximity
- topology relationships
- shared region
- critical-path service impact
- metric family or causal adjacency

Each incident record must include:

```json
{
  "incident_id": "string",
  "alert_ids": ["string"],
  "primary_service": "string",
  "impacted_services": ["string"],
  "region": "string",
  "correlation_basis": ["string"],
  "contains_deploy_signal": true
}
```

Do not ask the LLM to perform correlation.

---

### 3. Incident Classification and Urgency Assignment

Make one Stage 1 LLM call using:

- all correlated incidents
- the full service topology
- the full runbook policy
- allowed incident classes
- allowed urgency levels

For each incident, output:

```json
{
  "incident_id": "string",
  "incident_class": "market_data_degradation",
  "urgency": "sev1",
  "customer_impact_summary": "string",
  "suspected_root_cause": "string",
  "confidence": "low | medium | high"
}
```

Every incident must use one incident class from the provided policy.

Every urgency must use one provided urgency level.

The model must not invent new classes or urgency labels.

Save output to `incident_analysis.json`.

---

### 4. Remediation Plan Drafting

For each incident, make one separate Stage 2 LLM call.

Do not batch incidents.

Each call must include:

- incident record
- Stage 1 classification result
- relevant action policies
- allowed actions
- whether any deploy signal is present

Each Stage 2 output must include:

```json
{
  "incident_id": "string",
  "recommended_actions": [
    {
      "action": "freeze_deployments",
      "reason": "string",
      "requires_human_approval": false,
      "risk_note": "string"
    }
  ],
  "explicitly_avoided_actions": [
    {
      "action": "shift_traffic_region",
      "reason": "string"
    }
  ],
  "rollback_consideration": "string",
  "verification_checks": ["string"]
}
```

The recommended actions must only use actions from `allowed_actions`.

Action policy constraints must be enforced. If the model proposes a disallowed action, your code should reject, filter, or fail validation clearly.

Append Stage 2 outputs into `incident_analysis.json` while preserving Stage 1 results.

---

### 5. Operator Review Checkpoint

Before generating the incident brief, display each incident with:

- incident class
- urgency
- recommended actions
- which actions require human approval

Pause for operator review with this prompt:

```text
Review incident plans. You may approve actions, reject actions, or override urgency before generating the incident brief.
Enter commands in the form:
approve <incident_id> <action>
reject <incident_id> <action>
urgency <incident_id> <sev0|sev1|sev2|sev3>
Press Enter on an empty line when done.
```

The operator must be able to:

- approve or reject one or more actions
- override urgency for one or more incidents

Save decisions to `operator_decisions.json`.

Stage 3 must use post-review approved/rejected actions and final urgencies.

The review checkpoint must affect downstream outputs, not merely log a message.

---

### 6. Incident Brief Generation

Make one Stage 3 LLM call after operator review.

The call must include:

- correlated incidents
- Stage 1 classifications
- Stage 2 remediation plans
- operator decisions
- final post-review urgencies
- final approved action set per incident

Generate `incident_brief.md` with these sections:

- Executive Summary
- Active Incidents
- Immediate Approved Actions
- Actions Pending Human Approval or Escalation
- Actions Explicitly Rejected
- Verification and Exit Criteria
- Communications Draft
- Follow-Up Engineering Recommendations

The brief must be operationally specific and incident-oriented, not a generic summary.

---

## SHOULD ATTEMPT

### 7. Deterministic Blast Radius Score

Compute a deterministic `blast_radius_score` from 0 to 100 for each incident.

Do not ask the LLM to assign the score.

Define the formula explicitly using factors such as:

- number of impacted services
- whether critical-path services are involved
- maximum alert severity
- whether order-routing is impacted
- whether a deploy signal is present

Save output to `blast_radius_scores.json`.

Include:

- formula
- per-incident score
- contributing factors
- short justification

---

### 8. Safe Automation Plan

Generate `safe_automation_plan.md` describing which actions could be safely automated in the future, under what guardrails, and which should remain human-gated.

This output must distinguish:

- fully automatable actions
- conditionally automatable actions
- never-automate actions in this scenario

Ground the recommendations in the provided policy and in observed incident characteristics.

---

### 9. Postmortem Skeleton

Generate `postmortem_template.md` with pre-filled content for each incident:

- timeline
- probable contributing factors
- what evidence still needs to be gathered
- prevention ideas across architecture, observability, and process

Clearly mark uncertain or AI-generated inferences.

---

## STRETCH

### 10. What-If Policy Simulation

Add a deterministic simulation that answers:

- What changes if `disable_ai_assist_path` were pre-approved?
- What changes if all deployment-related signals are ignored?

Save results to `policy_simulation.json` with before/after differences in recommended and pending actions.

---

## REQUIRED ARTIFACTS

Your repository must produce:

- `alerts.jsonl`
- `service_topology.json`
- `runbook_policy.json`
- `normalized_alerts.json`
- `correlated_incidents.json`
- `incident_analysis.json`
- `operator_decisions.json`
- `incident_brief.md`
- `blast_radius_scores.json`, if attempted
- `safe_automation_plan.md`, if attempted
- `postmortem_template.md`, if attempted
- `policy_simulation.json`, if attempted
- `llm_calls.jsonl`

---

## `llm_calls.jsonl` REQUIREMENTS

Log one JSON object per LLM call.

Each record must include:

```json
{
  "stage": "string",
  "incident_id": "string | null",
  "timestamp": "ISO-8601 timestamp",
  "provider": "string",
  "model": "string",
  "prompt_hash": "string",
  "input_artifacts": ["path"],
  "output_artifact": "path"
}
```

There must be separate records for:

- Stage 1 incident classification
- each Stage 2 remediation-plan call
- Stage 3 incident brief generation
- optional outputs if they use LLM calls

---

## VALIDATION REQUIREMENTS

The repository must include a validation command, for example:

```bash
make validate
```

or:

```bash
python validate.py
```

The validation command must check that:

- required artifacts exist
- JSON files are valid
- input files were read from disk
- alert normalization happened before any LLM call
- incident correlation happened before any LLM call
- every correlated incident has a Stage 1 classification
- incident classes use only the policy classes
- urgencies use only the policy urgency levels
- each incident has its own Stage 2 LLM call record
- incidents were not batched into a single Stage 2 call
- recommended actions use only allowed actions
- action policy compatibility is enforced
- operator decisions are saved and applied to Stage 3 inputs
- incident brief reflects post-review urgencies and approved/rejected actions
- `llm_calls.jsonl` contains separate records for required stages

---

## EXECUTION REQUIREMENTS

The evaluator will run the pipeline from a clean checkout.

Generated artifacts may be deleted before evaluation.

The evaluator may replace `alerts.jsonl`, `service_topology.json`, and `runbook_policy.json` with equivalent files.

Static precomputed outputs are not sufficient.

The solution must actually run the staged pipeline and regenerate the required artifacts from disk inputs.

---

## TOOLS

Any programming language may be used.

Any LLM provider or AI tooling may be used.

---

## TECHNICAL CONSTRAINTS

- Read all three input files from disk.
- Do not hardcode the sample alert stream, topology, or policy into prompts.
- Alert normalization and incident correlation must be deterministic code.
- Stage 1 must use the provided runbook policy exactly.
- Each incident must have its own Stage 2 remediation call.
- Stage 2 incidents must not be batched.
- Operator review must be interactive and able to modify Stage 3 inputs.
- Any blast radius score must be computed deterministically.
- Outputs must clearly separate AI recommendations from approved operational actions.
- Do not execute real infrastructure actions; this challenge is planning and control-plane output only.
