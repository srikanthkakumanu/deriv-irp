# BRD — AI-Assisted Incident-Response Control Plane

## Domain
**AI-Assisted Incident-Response Control Plane for a Trading Platform**

| Module | Purpose |
|---|---|
| M1 Telemetry Intake & Correlation | Read platform alerts, topology and operating policy; turn raw alerts into uniform records and group them into incidents by rules. |
| M2 AI-Assisted Analysis & Planning | Classify incidents and set urgency within policy; draft a separately gated remediation plan for each incident; score blast radius. |
| M3 Operator Control & Briefing | Human checkpoint to approve or reject actions and override urgency; produce the execution-ready incident briefing. |
| M4 Audit, Validation & Insights | Keep every intermediate result and AI interaction; prove the run followed the stage order and policy; produce the automation plan, postmortem skeleton and policy what-ifs. |

## Personas
| ID | Name | Role | Top JTBD | Top pain point |
|---|---|---|---|---|
| P1 | On-call Incident Commander | Platform/SRE engineer on call during a trading incident | Triage a burst of alerts into a few incidents fast, then approve only safe actions | Alert storms across dependent services; fear that automation makes an outage worse or adds market risk |
| P2 | Engineering Leader | Platform lead accountable for trading-platform reliability | Get a trustworthy briefing and know which actions were approved, pending or rejected | AI output mixed up with approved actions; no rollback awareness; no audit trail |
| P3 | Evaluator / Auditor | Reviewer checking the run is correct, policy-compliant and repeatable | Re-run the workflow on new inputs and verify ordering, policy compliance and that decisions took effect | Static outputs that cannot be reproduced; AI steps that cannot be traced |

## Use Cases

### UC-01 Ingest & normalise telemetry. Actor: P1 (starts the run)
- **Happy path:** 1) P1 starts a run against the alert, topology and policy files on disk. 2) The system reads all three. 3) The system turns each alert into a uniform record (stable ID, timestamp, service, severity, metric, observed value, threshold, delta, region, message) without any AI.
- **Exception:** missing or malformed input file or alert row → the run stops (file missing) or reports the bad row with its line number. Nothing is guessed.
- **AC:** *Given* the same alert file *When* normalised twice *Then* the records and IDs are identical and no AI interaction has happened yet.

### UC-02 Correlate alerts into incidents. Actor: System for P1
- **Happy path:** 1) The system groups uniform alerts using time proximity, shared region, topology relationships and causal adjacency. 2) It marks the primary service, the impacted services and whether a deploy/change signal is present. 3) It records why the alerts were grouped.
- **Exception:** an alert matches no group, or its service is not in the topology → it becomes its own incident, with that basis stated.
- **AC:** *Given* normalised alerts *When* they are correlated *Then* every alert belongs to exactly one incident, each incident lists its correlation basis, and no AI was used.

### UC-03 Classify incidents & assign urgency. Actor: System (AI-assisted) for P1
- **Happy path:** 1) The system sends all incidents, the topology and the full policy for one classification analysis. 2) It receives a class, urgency, customer-impact summary, suspected root cause and confidence per incident. 3) It accepts only classes and urgencies listed in the policy.
- **Exception:** a label outside the policy, or an incident missing → retry once, then fail clearly. Labels are never invented.
- **AC:** *Given* correlated incidents *When* they are classified *Then* every incident has exactly one policy class and one policy urgency.

### UC-04 Draft a gated remediation plan per incident. Actor: System (AI-assisted) for P1
- **Happy path:** 1) For each incident separately, the system requests a plan using the incident, its classification, the relevant action policies, the allowed actions and its deploy signal. 2) It gets back recommended actions (reason, approval need, risk note), actions explicitly avoided, rollback considerations and verification checks. 3) It removes or rejects any action the policy does not allow, and sets the approval need from the policy.
- **Exception:** a disallowed or unknown action is proposed → it is filtered out and recorded as "rejected by policy" with a reason.
- **AC:** *Given* N incidents *When* plans are drafted *Then* there are N separate planning interactions and every recommended action is allowed for that incident's class or urgency.

### UC-05 Review, approve/reject actions and override urgency. Actor: P1
- **Happy path:** 1) The system shows each incident's class, urgency, recommended actions and which actions need approval, then pauses with the prescribed prompt. 2) P1 enters approve, reject or urgency commands and finishes with an empty line. 3) The system saves the decisions and works out each incident's final urgency and final approved, pending and rejected action sets.
- **Exception:** invalid command, incident, action or urgency → inline error and re-prompt. Nothing is applied.
- **AC:** *Given* P1 rejects an action and overrides an urgency *When* the briefing is generated *Then* the briefing shows the new urgency and lists that action as rejected.

### UC-06 Generate the incident briefing. Actor: System (AI-assisted) for P1/P2
- **Happy path:** 1) After review only, the system requests one briefing using the incidents, classifications, plans, decisions, final urgencies and final approved actions. 2) It produces a briefing with the 8 required sections. 3) It clearly labels AI-written content apart from operator-approved actions, and states that no action was executed.
- **Exception:** a section is missing, or the content contradicts the decisions → regenerate once, then fail validation.
- **AC:** *Given* review is complete *When* the briefing is generated *Then* it contains all 8 sections and matches the post-review urgencies and action statuses.

### UC-07 Score blast radius. Actor: System for P1/P2
- **Happy path:** 1) For each incident, the system computes a 0–100 score from a published formula (number of impacted services, critical-path involvement, maximum severity, order-routing impact, deploy signal). 2) It records the factors. 3) It records a short justification.
- **Exception:** a factor cannot be determined (e.g. no tier) → it scores 0 and is noted.
- **AC:** *Given* the same incidents *When* they are scored *Then* the scores are identical and no AI was used.

### UC-08 Produce a safe automation plan. Actor: P2
- **Happy path:** 1) The system sorts every allowed action into fully automatable, conditionally automatable or never-automate. 2) It grounds each choice in the policy and the observed incidents. 3) It states the guardrails for the conditional actions.
- **Exception:** an action has no policy entry → it defaults to never-automate, with a note.
- **AC:** *Given* the policy and incidents *When* the plan is produced *Then* every allowed action appears in exactly one tier with a rationale.

### UC-09 Produce a postmortem skeleton. Actor: P2
- **Happy path:** 1) For each incident, the system pre-fills a timeline from the alerts. 2) It lists probable contributing factors and the evidence still needed. 3) It proposes prevention ideas covering architecture, observability and process, with AI inferences marked.
- **Exception:** little evidence available → the gaps are listed explicitly rather than filled with guesses.
- **AC:** *Given* incidents *When* the skeleton is produced *Then* every incident has all four parts and every AI inference is labelled.

### UC-10 Simulate policy what-ifs. Actor: P2
- **Happy path:** 1) The system re-evaluates outcomes as if `disable_ai_assist_path` were pre-approved. 2) It re-evaluates outcomes as if deploy-related signals were ignored. 3) It reports the before/after differences in recommended and pending actions.
- **Exception:** a scenario changes nothing → it reports "no change" explicitly.
- **AC:** *Given* the same inputs *When* the simulation runs *Then* the results are identical across runs and show the differences per incident.

### UC-11 Audit & validate a run. Actor: P3
- **Happy path:** 1) P3 runs the validation command. 2) The system checks artifact presence and validity, stage order, policy compliance, one plan per incident, that decisions were applied, the briefing's consistency and the AI interaction log. 3) It reports pass/fail for each check.
- **Exception:** any check fails → non-zero result, naming the failing check.
- **AC:** *Given* a completed run *When* validation runs *Then* every requirement check passes or the failures are named.

### UC-12 Replay with replaced inputs. Actor: P3
- **Happy path:** 1) P3 deletes the generated outputs and supplies equivalent input files. 2) P3 re-runs the full workflow, including the review (live or pre-recorded decisions). 3) All outputs are regenerated from the new inputs.
- **Exception:** the inputs use different service names, IDs or classes → handled with no dependence on sample values.
- **AC:** *Given* new equivalent inputs *When* the workflow re-runs *Then* every required output is regenerated and passes validation.

## Scope
| Module | In-scope | Out-of-scope | Reason |
|---|---|---|---|
| M1 | Read the 3 input files; uniform alert records; rule-based correlation into incidents (UC-01, UC-02) | Live streaming ingestion, polling real monitoring systems | The challenge supplies "live-like" files on disk |
| M2 | Policy-constrained classification and urgency; per-incident gated plans; policy enforcement; blast radius (UC-03, UC-04, UC-07) | AI parsing or correlation of raw alerts; AI-assigned scores | Determinism is required for these steps |
| M3 | Interactive approve/reject/urgency checkpoint; recorded decisions; post-review briefing (UC-05, UC-06) | Executing any real infrastructure action; chat interface; dashboard UI | Planning and control-plane output only; not a monitoring dashboard |
| M4 | Retaining intermediate artifacts; AI interaction log; validation command; automation plan; postmortem; what-if simulation; replay (UC-08 to UC-12) | Automatic paging or status-page publishing; ticketing integration | Recommendations only; no outward-facing actions |

## Self-Check Findings
> Status: the user accepted every proposed default on 2026-10-14. Each finding is **resolved** with the default below, and the design section of this plan builds to them.

| Type | Description | Ref | Resolution (confirmed) |
|---|---|---|---|
| Framing | "Replayable" is in the title but the source has no explicit replay requirement beyond "evaluator may replace inputs"; replaying operator decisions is not defined. | UC-12, UC-05 | Replay = re-run from disk inputs plus optional pre-recorded operator decisions and optional cached AI responses |
| Contradiction | "Operator review must be interactive" vs. the evaluator running the pipeline from a clean checkout, possibly unattended. | UC-05, UC-12 | Interactive by default; also accept piped or pre-recorded commands through the same parser |
| Contradiction | The policy field `max_allowed_if` mixes incident classes and urgency levels (e.g. `page_platform_lead`: sev0/sev1), and the name suggests a ceiling, not a membership list. | UC-04 | Treat as an allow-list: permitted if the incident's class OR urgency is listed |
| Contradiction | An urgency override can make an already-recommended action impermissible (e.g. `page_platform_lead` after a downgrade to sev3), or make an urgency-gated action newly permissible. The source does not say which wins. | UC-05, UC-06 | Re-apply the policy after review: newly disallowed → "invalidated by override"; newly allowed → "pending", never auto-approved |
| Contradiction | "Pause for approval before any action plan is finalised" vs. actions marked `requires_human_approval: false`. It is unclear whether the operator can reject those, and whether they count as "approved". | UC-05, UC-06 | The operator can reject any action; non-gated actions not rejected show as "cleared by policy" (distinct from "operator-approved") |
| Framing | The source says "approved operational actions" and "Immediate Approved Actions", but no action may be executed, which risks implying that execution happened. | UC-06 | The briefing carries a "nothing executed" banner; status vocabulary: approved / cleared-by-policy / pending / rejected |
| Gap | `allowed_actions` may include an action with no entry in `action_policies` (possible with replaced fixtures); its approval need and permitted classes are undefined. | UC-04, UC-08 | Permitted for any class, but always requires human approval and is never automated |
| Gap | The blast radius factor "order-routing impacted" depends on a service role, but service names must not be hardcoded. | UC-07 | Identify it from a configurable name pattern, falling back to the critical-path service at the end of the dependency chain |
| Gap | The what-if "ignore deployment-related signals" may change the incident class, which is an AI output; a deterministic simulation cannot re-classify. | UC-10 | Re-run the rule-based steps; flag incidents classed as deployment-related as "needs reclassification" rather than calling the AI |
| Gap | Behaviour when the AI service is unavailable or not configured is unspecified, but the evaluator must be able to run the workflow end to end. | UC-03, UC-04, UC-06, UC-12 | Fall back to a rule-based analyser that follows the same contract and is logged as such |
| Coverage | All in-scope rows trace to ≥1 use case (M1→UC-01/02, M2→UC-03/04/07, M3→UC-05/06, M4→UC-08 to UC-12). | All | No orphans |
