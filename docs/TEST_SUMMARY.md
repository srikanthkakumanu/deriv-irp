# Test Summary — deriv-irp

| | |
|---|---|
| **Date** | 2026-10-15 |
| **Code version** | `main` @ `2827aec` plus the review follow-ups: non-LLM runs flagged (`--require-llm`), published artifact JSON Schemas, Hypothesis property tests |
| **Environment** | macOS (darwin, arm64) · Python 3.12.15 · uv · pytest 9.1.1 · pytest-cov 7.1.0 · hypothesis 6.169.1 · jsonschema 4.26.0 · pydantic 2.14.1 · openai 3.31.0 |
| **LLM** | OpenAI `gpt-4.1` (live run in §6.1, made before the review follow-ups); `offline-deterministic:rule-based-v1` (offline; a rule-based stand-in, not an LLM) |
| **Overall result** | ✅ **213 / 213 tests passed** · **99% line coverage** · **24 / 24 validation checks passed** on every offline end-to-end run, with the expected 1 WARN for the non-LLM provider |

---

## 1. Summary

| Area | Result |
|---|---|
| Automated tests (pytest) | **213 passed**, 0 failed, 0 skipped, 0 errors, in 12.0 s (includes 10 Hypothesis properties × 150 examples) |
| Line coverage (`src/irp`) | **99%** (98.83%: 2,649 statements, 31 missed) |
| Requirement validator (`make validate`) | **24 / 24 PASS**; on an offline run, check 22 is a **WARN** (not an LLM), and `make validate REQUIRE_LLM=1` turns it into a FAIL (exit 1) |
| Published JSON Schemas (`schemas/`) | ✅ 7 schemas, valid draft 2020-12; `irp schemas --check` up to date; run artifacts pass the third-party `jsonschema` validator |
| Property-based tests (Hypothesis) | ✅ 10 properties; a deliberately broken correlator is caught (mutation check) |
| Live end-to-end run (OpenAI `gpt-4.1`) | ✅ ok: 7 LLM calls, all first-attempt successes, 51.2 s total LLM latency |
| Evaluator scenario: replacement fixtures in the repo root | ✅ the replaced files are used and logged, and validation checks those exact files (was a false pass before the fix) |
| Replay / offline runs | ✅ replay reproduces the live analysis with no network calls; offline run needs no API key |
| Clean-checkout run (`make setup` → `run-scripted` → `validate` → `test`) | ✅ ok |
| Start / stop / status scripts | ✅ background start, duplicate-run refusal, graceful stop recorded as `aborted` |
| Requirements cross-check (`docs/REQUIREMENT.md`) | ✅ steps 1–10 and all 13 required artifacts (see §7) |

---

## 2. How to reproduce

```bash
make setup                                   # uv sync --group dev
make test                                    # 213 tests (incl. Hypothesis property tests)
make coverage                                # tests + coverage report
make run-scripted PROVIDER=openai            # live run (needs OPENAI_API_KEY in .env)
make validate REQUIRE_LLM=1                  # 24 checks; fails if the AI stages did not use a real LLM
uv run irp schemas --check                   # published schemas match the code
make replay PROVIDER=openai                  # replay from data/output/llm_cache, no network
make run-scripted PROVIDER=offline           # no API key needed
```

Scripted operator decisions used for every end-to-end run (`fixtures/sample_decisions.txt`):

```text
approve INC-002 disable_ai_assist_path
reject INC-001 switch_to_cached_quotes
approve INC-001 rollback_latest_change      # deliberately invalid: refused by policy
urgency INC-002 sev3
```

---

## 3. Automated test results (pytest)

### 3.1 By layer

| Layer | Test modules | Tests | Result |
|---|---|---:|---|
| Domain | 8 | 95 | ✅ all passed |
| Application | 3 | 27 | ✅ all passed |
| Infrastructure + interface | 4 | 32 | ✅ all passed |
| Property-based (Hypothesis) | 1 | 10 | ✅ all passed |
| Integration (end to end) | 5 | 49 | ✅ all passed |
| **Total** | **21** | **213** | ✅ **213 passed** |

### 3.2 By test module

| Module | Tests | Passed | Failed | Time (s) | What it covers |
|---|---:|---:|---:|---:|---|
| `unit/domain/test_alerts.py` | 22 | 22 | 0 | 0.00 | Normalisation determinism, IDs, delta, UTC, rejection reasons, strict mode, change-signal detection |
| `unit/domain/test_topology.py` | 5 | 5 | 0 | 0.00 | Transitive up/downstream, adjacency, unknown services, cycles, empty topology |
| `unit/domain/test_incident.py` | 19 | 19 | 0 | 0.00 | Correlation rules (region, window, chaining, topology), primary service, deploy attachment |
| `unit/domain/test_policy.py` | 14 | 14 | 0 | 0.00 | Class/urgency permission, missing policy entry, `enforce()` filtering, approval flags, model validation |
| `unit/domain/test_review.py` | 16 | 16 | 0 | 0.00 | Approve/reject/override semantics, invalidation, unlocking, ordering independence, invalid decisions |
| `unit/domain/test_blast_radius.py` | 6 | 6 | 0 | 0.00 | Formula, factor sums, cap, order-routing fallback, custom pattern, determinism |
| `unit/domain/test_automation_and_simulation.py` | 8 | 8 | 0 | 0.01 | Automation tiers and guardrails; what-if scenarios A/B including the pre-review diff |
| `unit/domain/test_pipeline_stage.py` | 5 | 5 | 0 | 0.00 | Strict stage order, skip/repeat rejection, guards |
| `unit/application/test_contracts.py` | 5 | 5 | 0 | 0.01 | Runtime policy-derived enums, extra fields, strict JSON schema shape |
| `unit/application/test_llm_gateway.py` | 9 | 9 | 0 | 0.01 | Logging fields, corrective retry, semantic failure, provider error, stage guard, replay, prompt hash, **artifact name → real path + sections** |
| `unit/application/test_review_and_brief.py` | 13 | 13 | 0 | 0.00 | Command parser, review use case (errors, EOF, prompt), brief consistency checks |
| `unit/infrastructure/test_adapters.py` | 9 | 9 | 0 | 0.03 | Input loading and provenance, missing/invalid files, malformed lines, store, path traversal, offline model |
| `unit/infrastructure/test_input_locator.py` | 6 | 6 | 0 | 0.01 | **New.** Repo root before `data/`, per-file resolution, notice only for a different file, root-only, missing everywhere, explicit `--input-dir` |
| `unit/infrastructure/test_openai_model.py` | 4 | 4 | 0 | 0.00 | Strict schema request, temperature fallback, error propagation, refusal (mocked client) |
| `unit/infrastructure/test_interface.py` | 13 | 13 | 0 | 0.81 | Consoles, `.env` loader, provider selection, CLI run/validate, default paths, `validate.py` |
| `property/test_deterministic_properties.py` | 10 | 10 | 0 | 4.31 | **New.** Hypothesis over random topologies (cycles included), alert streams and AI proposals: normalisation determinism and key-order independence; one incident per alert; order-independent correlation; link-rule soundness; change signals never merge; blast-radius formula; policy enforcement |
| `integration/test_llm_provider_and_schemas.py` | 14 | 14 | 0 | 1.75 | **New.** Offline run flagged (banner ×2, manifest, log, brief) and WARN; `--require-llm` fails it (validator and CLI); an LLM provider passes clean; committed schemas up to date and valid; artifacts pass `jsonschema`; 5 kinds of non-conforming artifact fail both validators; `irp schemas` export and `--check` |
| `integration/test_pipeline_end_to_end.py` | 15 | 15 | 0 | 2.68 | Full runs, decision impact, determinism, replay, stale cleanup, malformed rows, tamper detection |
| `integration/test_evaluator_scenarios.py` | 7 | 7 | 0 | 0.74 | **New.** Clean-checkout layout: bundled fixtures by default, **repo-root replacements used**, validation of the files actually used, real paths in `llm_calls.jsonl`, bogus path detected, no-input and corrupt-manifest handling |
| `integration/test_replaced_fixtures.py` | 1 | 1 | 0 | 0.17 | Renamed services, new region, new classes, `P1–P3` urgencies, action with no policy entry |
| `integration/test_lifecycle.py` | 12 | 12 | 0 | 0.42 | Interrupt/failure recorded in the manifest, exit 130, start/stop/status script behaviour |

### 3.3 Edge cases covered

| Category | Edge cases |
|---|---|
| Input files | replacements in the repo root override `data/` **per file**; only a *different* override raises a notice; root-only layout; file missing from both locations (error names both paths); explicit `--input-dir` must be complete; input changed after the run is detected by validation |
| Alert input | invalid JSON line, non-object line, blank lines, missing fields, unknown severity, unparseable timestamp, non-numeric or boolean values, numeric strings, timezone offsets, naive timestamps, unsorted rows, empty file, `--strict` fail-fast |
| Topology and correlation | service missing from topology, cyclic dependencies, empty topology, cross-region split, inclusive window boundary, chained windows, siblings joined only via a common dependent, zero-second window, deploy with no matching incident, deploy outside the window, deploy never merges incidents, same-service preference |
| Policy | action not in `allowed_actions`, action with no policy entry (gated), urgency-based permission, duplicate recommendations, wrong LLM approval flag, all actions filtered out, duplicate or empty policy lists |
| Operator review | unknown incident/action/urgency, malformed command, last decision wins, reject of an action not in the plan, operator-added action, downgrade invalidates an action and voids its approval, upgrade unlocks an action as pending, decision order independence, EOF without an empty line |
| LLM gateway | schema violation then corrective retry, semantic failure after retries, provider exception, call before correlation blocked, replay hit and miss, distinct prompt hashes, logical names resolved to real paths with sections |
| Property-based | arbitrary service graphs with cycles and unknown services, mixed-case severities, deploy events, two regions, up to 25 alerts; AI proposals mixing allowed, disallowed, duplicate and unknown actions with wrong approval flags |
| LLM provider | offline run (WARN), offline run with `--require-llm` (FAIL), LLM provider (clean PASS) |
| Published schemas | missing required field, empty `correlation_basis`, unknown `confidence`, `blast_radius_score` 101, non-ISO `llm_calls.jsonl` timestamp (reported by line number), stale or missing schema files |
| Validator (tamper detection) | edited brief, modified input file, missing Stage 2 record, disallowed action injected, tampered final urgency, missing artifact, logged artifact path that does not exist, corrupt manifest |
| Lifecycle | interruption mid-review recorded as `aborted`, failure recorded as `failed`, exit code 130, stale PID file, bad script arguments, missing inputs |

---

## 4. Coverage

| Layer | Statements | Missed | Coverage |
|---|---:|---:|---:|
| `domain/` | 853 | 6 | 99.3% |
| `application/` | 1,110 | 7 | 99.4% |
| `infrastructure/` | 473 | 11 | 97.7% |
| `interface/` | 123 | 3 | 97.6% |
| `bootstrap.py`, `shared/`, `__main__.py` | 90 | 4 | 95.6% |
| **Total** | **2,649** | **31** | **98.83% (99%)** |

Uncovered lines are mostly defensive branches (empty manifest on very early failure, a `getpass` fallback, unreachable offline-model branches) and `__main__.py` (the `python -m irp` entry point, exercised manually). CI enforces a minimum of 95%.

---

## 5. Requirement validation (`make validate`)

Results of the offline run on 2026-10-15 (`make run-scripted PROVIDER=offline`, then `make validate`). Checks 3 and 22 are new. The live run in §6.1 passed the 22 checks that existed then; it has not been re-run against checks 3 and 22 (see §8).

| # | Check | Result |
|---:|---|---|
| 1 | Required artifacts exist | ✅ PASS |
| 2 | JSON artifacts are valid | ✅ PASS |
| 3 | **Artifacts conform to the published JSON Schemas** (new) | ✅ PASS (7 artifacts match `schemas/`) |
| 4 | Input files were read from disk (the validated files are the ones the run used; SHA-256 matches) | ✅ PASS (`alerts=data_dir, topology=data_dir, policy=data_dir`) |
| 5 | Normalised alerts have the required schema | ✅ PASS (10 alerts) |
| 6 | Correlated incidents have the required schema | ✅ PASS (2 incidents covering 10 alerts) |
| 7 | Normalisation happened before any LLM call | ✅ PASS |
| 8 | Correlation happened before any LLM call | ✅ PASS |
| 9 | Pipeline stages ran in the required order | ✅ PASS (all 11 stages) |
| 10 | Every incident has a Stage 1 classification | ✅ PASS |
| 11 | Incident classes use only policy classes | ✅ PASS |
| 12 | Urgencies use only policy urgency levels | ✅ PASS |
| 13 | Exactly one Stage 1 call | ✅ PASS |
| 14 | Each incident has its own Stage 2 call (not batched) | ✅ PASS (2 separate calls) |
| 15 | Recommended actions use only allowed actions | ✅ PASS (11 actions) |
| 16 | Action policy compatibility is enforced | ✅ PASS |
| 17 | Approval flags come from policy | ✅ PASS |
| 18 | Operator decisions saved and applied to Stage 3 inputs | ✅ PASS (digest matches; replaying decisions reproduces the final state) |
| 19 | Incident brief reflects post-review urgencies and actions | ✅ PASS (8 sections + ledger) |
| 20 | `llm_calls.jsonl` has separate records for the required stages | ✅ PASS (7 records) |
| 21 | `llm_calls.jsonl` artifact paths exist on disk | ✅ PASS (all logged paths are real files) |
| 22 | **AI stages were answered by a real LLM** (new) | ⚠️ WARN on the offline run: "7/7 AI calls were answered by offline-deterministic:rule-based-v1, a rule-based stand-in, not an LLM". ❌ FAIL with `REQUIRE_LLM=1` (exit 1), as intended. |
| 23 | Blast radius scores are valid | ✅ PASS (INC-001 = 100, INC-002 = 30) |
| 24 | Policy simulation is valid | ✅ PASS (scenarios A and B) |

---

## 6. End-to-end runs

### 6.1 Live run: OpenAI `gpt-4.1` (2026-10-14 19:51:02Z → 19:51:54Z, ~51 s)

**LLM calls (`data/output/llm_calls.jsonl`), now with real paths**

| Stage | Incident | Status | Attempts | Latency | `output_artifact` (`output_section`) |
|---|---|---|---:|---:|---|
| `stage1_classification` | — | ok | 1 | 5.6 s | `data/output/incident_analysis.json` (`stage1`) |
| `stage2_remediation` | INC-001 | ok | 1 | 9.4 s | `data/output/incident_analysis.json` (`stage2.INC-001`) |
| `stage2_remediation` | INC-002 | ok | 1 | 11.7 s | `data/output/incident_analysis.json` (`stage2.INC-002`) |
| `stage3_brief` | — | ok | 1 | 11.5 s | `data/output/incident_brief.md` |
| `optional_automation_plan` | — | ok | 1 | 2.8 s | `data/output/safe_automation_plan.md` |
| `optional_postmortem` | INC-001 | ok | 1 | 4.8 s | `data/output/postmortem_template.md` |
| `optional_postmortem` | INC-002 | ok | 1 | 5.4 s | `data/output/postmortem_template.md` |
| **Total** | | **7 / 7 ok** | **no retries** | **51.2 s** | |

`input_artifacts` example (Stage 1): `data/output/correlated_incidents.json`, `data/output/normalized_alerts.json`, `data/service_topology.json`, `data/runbook_policy.json`, `data/output/blast_radius_scores.json`.

**Deterministic stages**

| Output | Result |
|---|---|
| Input resolution | all three files from `data/` (origin `data_dir`), no overrides |
| Normalisation | 10 alerts, 0 rejected rows |
| Correlation | **INC-001**: `market-data-gateway`, `pricing-engine`, `order-router`, `api-edge` + canary deploy (7 alerts). **INC-002**: `feature-store`, `model-serving` (3 alerts). |
| Blast radius | INC-001 = **100** (high), INC-002 = **30** (low) |

**AI analysis (policy-checked)**

| Incident | Class | Urgency | Confidence | Recommended actions | Policy-filtered | Approval-flag mismatches |
|---|---|---|---|---:|---:|---:|
| INC-001 | `market_data_degradation` | sev1 | high | 7 | 0 | 0 |
| INC-002 | `ai_inference_degradation` | sev3 | high | 4 | 0 | 0 |

**Operator review → post-review state**

| Command | Outcome |
|---|---|
| `approve INC-002 disable_ai_assist_path` | applied → **approved** |
| `reject INC-001 switch_to_cached_quotes` | applied → **rejected** |
| `approve INC-001 rollback_latest_change` | **refused**: not permitted for `market_data_degradation`/sev1 (logged as an error, not applied) |
| `urgency INC-002 sev3` | applied (no-op: the model had already rated INC-002 sev3) |

| Incident | Final urgency | Approved | Auto-cleared by policy | Pending | Rejected |
|---|---|---|---|---|---|
| INC-001 | sev1 | — | page on-call, page platform lead, diagnostics, freeze deployments, status-page draft | `shift_traffic_region` | `switch_to_cached_quotes` |
| INC-002 | sev3 | `disable_ai_assist_path` | diagnostics, page on-call | `throttle_non_critical_requests` | — |

**What-if simulation**

| Scenario | Post-review effect | Pre-review effect |
|---|---|---|
| A: `disable_ai_assist_path` pre-approved | no change (the operator had already approved it) | INC-002: `disable_ai_assist_path` moves from pending to cleared |
| B: deploy signals ignored | INC-001 blast radius −10 (100 → 90) | no action changes |

### 6.2 Evaluator scenario: replacement fixtures in the repo root (clean copy)

Setup: clean copy of the repo; a replacement `alerts.jsonl` (3 alerts, region changed to `ap-south-1`) plus copies of the topology and policy dropped into the **repo root**; `make run-scripted PROVIDER=offline`, then `make validate`, then `scripts/start.sh -m scripted -p offline`.

| | Before the fix | After the fix |
|---|---|---|
| Files used | bundled `data/` copies (replacement silently ignored) | `alerts.jsonl <- alerts.jsonl (repo_root)` for all three; `NOTICE` that it overrides the different bundled `data/alerts.jsonl` |
| Alerts / regions processed | 10 / `eu-west-1` (wrong) | 3 / `ap-south-1` (correct) |
| Validation | 21/21 PASS: a **false pass** | 22/22 PASS against the files actually used (the checks that existed at the time) |
| `scripts/start.sh` path | ignored the replacement | uses the replacement |

### 6.3 Replay and offline runs

| Run | Result |
|---|---|
| Replay (cached OpenAI responses) | 0 network calls; `incident_analysis.json` identical to the live run; validation PASS |
| Offline (no API key) | INC-002 rated **sev2**, so `urgency INC-002 sev3` invalidates `open_status_page_draft` (`invalidated_by_override`), showing the urgency-gating rule end to end; validation 24/24 PASS with 1 WARN; the run prints the "NOT a language model" banner at its start and end |

### 6.4 Operational checks

| Check | Result |
|---|---|
| Clean checkout → `make setup`, `make run-scripted PROVIDER=offline`, `make validate`, `make test` | ✅ all succeeded |
| `make run-scripted PROVIDER=offline` → `make validate` → `make replay PROVIDER=offline` → `make validate` → `irp schemas --check` (the CI sequence) | ✅ all succeeded (exit 0) |
| `scripts/start.sh -b` (background OpenAI run) → second `start.sh` | ✅ refused: "a pipeline run is already in progress" |
| `scripts/stop.sh` during the run | ✅ graceful stop; manifest records `termination: aborted after INCIDENTS_CORRELATED` |
| `scripts/status.sh` | ✅ RUNNING with PID, elapsed time and current stage; IDLE with the last result afterwards |
| Invalid OpenAI key | ✅ fails cleanly: both attempts logged as `failed`, no brief generated, exit code 1 |

---

## 7. Requirements cross-check (`docs/REQUIREMENT.md`)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Deterministic normalisation → `normalized_alerts.json` | ✅ | All 10 required fields; content-hash IDs; no LLM involved; runs before any LLM call (validator checks 7–8) |
| 2 | Deterministic correlation → `correlated_incidents.json` | ✅ | All 7 required fields; documented rules (time, region, topology, critical path, change signals) |
| 3 | Stage 1: one classification call | ✅ | Payload has all incidents and the **full** topology and policy (byte-identical to the input files) plus class and urgency lists; enums derived from the policy at runtime |
| 4 | Stage 2: one call per incident, never batched, policy enforced | ✅ | 2 separate calls with no data from the other incident; payload has incident, classification, policy view, `allowed_actions` and deploy flag; disallowed actions filtered; Stage 1 kept |
| 5 | Operator review checkpoint | ✅ | Prompt text matches the requirement **exactly** (checked programmatically); approve, reject and urgency all applied; `operator_decisions.json`; decisions change the brief (tested) |
| 6 | Stage 3 brief | ✅ | One call after review with all 6 required inputs; all 8 sections present plus the deterministic ledger |
| 7 | Blast radius | ✅ | Formula, per-incident score, factors, justification; computed in code |
| 8 | Safe automation plan | ✅ | Fully, conditionally and never automatable tiers, grounded in the policy and the observed incidents |
| 9 | Postmortem skeleton | ✅ | Timeline, factors, evidence gaps, prevention per incident; AI lines tagged `[AI-INFERRED – unverified]` |
| 10 | What-if simulation | ✅ | Both scenarios with before/after diffs; no LLM calls |
| — | Required artifacts | ✅ | All 13: inputs in the repo root or `data/`, outputs in `data/output/`; formats published in `schemas/` and checked on every run (check 3) |
| — | "Policy-constrained LLM analysis" | ✅ with OpenAI | Offline runs are flagged everywhere and fail `make validate REQUIRE_LLM=1` (check 22) |
| — | `llm_calls.jsonl` | ✅ | One record per call; all 8 required fields; `input_artifacts` and `output_artifact` are real, existing paths |
| — | Replaced fixtures and clean checkout | ✅ | Repo-root or `data/` replacements used automatically; nothing depends on sample names, IDs or labels |

**Gaps found in the review, and their fixes:**

| Gap | Severity | Fix | Verified by |
|---|---|---|---|
| Replacement fixtures in the repo root were silently ignored (false 21/21 pass) | High | Per-file input resolution (repo root first, then `data/`); origin and notices recorded; validator checks the files the run used | §6.2, `test_input_locator.py`, `test_evaluator_scenarios.py` |
| `llm_calls.jsonl` artifact entries were not real paths (`incident_analysis.json#stage1`) | Medium | Real paths relative to the working directory; parts of files in `input_sections` / `output_section`; new validator check 21 | §6.1, `test_llm_gateway.py`, `test_evaluator_scenarios.py` |
| Stage 1 prompt mentioned the sample class `deployment_related_change` | Minor | Wording made generic ("a deployment- or change-related class, if the policy defines one") | Prompt review |
| A run without an API key silently used the rule-based offline model, which is not an LLM | Medium | `is_llm` on the provider port; banner, manifest flag, `provider_is_llm` per call, labelled Markdown; validator check 22 (WARN, or FAIL with `--require-llm`) | §5, `test_llm_provider_and_schemas.py` |
| Artifact formats could only be checked by this code | Low | JSON Schemas published in `schemas/`, checked on every run (check 3), drift-checked in tests and CI | §5, `test_llm_provider_and_schemas.py` |
| Determinism was shown only on the sample fixture | Low | Hypothesis property tests over random topologies, alert streams and AI proposals | §3, `tests/property/` |

---

## 8. Observations and limitations

1. **The urgency override was a no-op in the live run.** OpenAI rated INC-002 sev3 directly, so the invalidation path was exercised in the offline run (§6.3) and in tests rather than live. Use `urgency INC-001 sev3` to see it with OpenAI.
2. **LLM outputs can vary between live runs.** Calls use temperature 0 and a fixed seed, but OpenAI does not guarantee identical outputs. Use `make replay` for byte-stable reruns.
3. **The OpenAI adapter's unit tests use a mocked client.** Live behaviour was verified by the end-to-end run above, so tests never need network access or an API key.
4. **The replaced-fixture and evaluator-scenario tests use the offline model**, not live OpenAI.
5. **The review prompt always shows `sev0|sev1|sev2|sev3`.** The requirement mandates that exact text; urgency validation itself uses whatever levels the loaded policy defines.
6. **CI has not run yet** because of a GitHub account billing lock; the same steps pass locally.
7. **The live OpenAI run predates checks 3 and 22.** It was not repeated for this change (no API key in this environment). On a live run, check 22 should PASS, because the OpenAI adapter reports `is_llm = True`; this path is covered by a test with a provider that reports itself as an LLM. Re-run `make run-scripted PROVIDER=openai && make validate REQUIRE_LLM=1` to confirm.
