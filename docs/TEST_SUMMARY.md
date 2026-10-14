# Test Summary — deriv-irp

| | |
|---|---|
| **Date** | 2026-10-15 |
| **Code version** | `main` @ `17db87a` (plus this document) |
| **Environment** | macOS (darwin, arm64) · Python 3.12.15 · uv · pytest 9.1.1 · pytest-cov 7.1.0 · pydantic 2.14.1 · openai 3.31.0 |
| **LLM** | OpenAI `gpt-4.1` (live and replay); `offline-deterministic:rule-based-v1` (offline) |
| **Overall result** | ✅ **175 / 175 tests passed** · **99% line coverage** · **21 / 21 validation checks passed** on every end-to-end run |

---

## 1. Summary

| Area | Result |
|---|---|
| Automated tests (pytest) | **175 passed**, 0 failed, 0 skipped, 0 errors, in 4.7 s |
| Line coverage (`src/irp`) | **99%** (2,308 statements, 27 missed) |
| Requirement validator (`make validate`) | **21 / 21 PASS** |
| Live end-to-end run (OpenAI `gpt-4.1`) | ✅ ok: 7 LLM calls, all first-attempt successes, 49.0 s total LLM latency |
| Replay run (cached OpenAI responses, no network) | ✅ ok in 0.47 s; `incident_analysis.json` identical to the live run |
| Offline run (no API key needed) | ✅ ok in 0.24 s |
| Clean-checkout run (fresh copy, `make setup` → `run-scripted` → `validate` → `test`) | ✅ ok |
| Start / stop / status scripts (live) | ✅ background start, duplicate-run refusal, graceful SIGTERM stop recorded as `aborted` |

---

## 2. How to reproduce

```bash
make setup                                   # uv sync --group dev
make test                                    # 175 tests
make coverage                                # tests + coverage report
make run-scripted PROVIDER=openai            # live run (needs OPENAI_API_KEY in .env)
make validate                                # 21 requirement checks
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
| Application | 3 | 26 | ✅ all passed |
| Infrastructure + interface | 3 | 26 | ✅ all passed |
| Integration (end to end) | 3 | 28 | ✅ all passed |
| **Total** | **17** | **175** | ✅ **175 passed** |

### 3.2 By test module

| Module | Tests | Passed | Failed | Time (s) | What it covers |
|---|---:|---:|---:|---:|---|
| `unit/domain/test_alerts.py` | 22 | 22 | 0 | 0.00 | Normalisation determinism, IDs, delta, UTC, rejection reasons, strict mode, change-signal detection |
| `unit/domain/test_topology.py` | 5 | 5 | 0 | 0.00 | Transitive up/downstream, adjacency, unknown services, cycles, empty topology |
| `unit/domain/test_incident.py` | 19 | 19 | 0 | 0.00 | Correlation rules (region, window, chaining, topology), primary service, deploy attachment |
| `unit/domain/test_policy.py` | 14 | 14 | 0 | 0.00 | Class/urgency permission, missing policy entry, `enforce()` filtering, approval flags, model validation |
| `unit/domain/test_review.py` | 16 | 16 | 0 | 0.00 | Approve/reject/override semantics, invalidation, unlocking, ordering independence, invalid decisions |
| `unit/domain/test_blast_radius.py` | 6 | 6 | 0 | 0.00 | Formula, factor sums, cap, order-routing fallback, custom pattern, determinism |
| `unit/domain/test_automation_and_simulation.py` | 8 | 8 | 0 | 0.01 | Automation tiers and guardrails; what-if scenarios A/B including pre-review diff |
| `unit/domain/test_pipeline_stage.py` | 5 | 5 | 0 | 0.00 | Strict stage order, skip/repeat rejection, guards |
| `unit/application/test_contracts.py` | 5 | 5 | 0 | 0.01 | Runtime policy-derived enums, extra fields, strict JSON schema shape |
| `unit/application/test_llm_gateway.py` | 8 | 8 | 0 | 0.00 | Logging fields, corrective retry, semantic failure, provider error, stage guard, replay, prompt hash |
| `unit/application/test_review_and_brief.py` | 13 | 13 | 0 | 0.00 | Command parser, review use case (errors, EOF, prompt), brief consistency checks |
| `unit/infrastructure/test_adapters.py` | 9 | 9 | 0 | 0.04 | Input loading and provenance, missing/invalid files, malformed lines, store, path traversal, offline model |
| `unit/infrastructure/test_openai_model.py` | 4 | 4 | 0 | 0.00 | Strict schema request, temperature fallback, error propagation, refusal (mocked client) |
| `unit/infrastructure/test_interface.py` | 13 | 13 | 0 | 0.99 | Consoles, `.env` loader, provider selection, CLI run/validate, default `data/` paths, `validate.py` |
| `integration/test_pipeline_end_to_end.py` | 15 | 15 | 0 | 2.59 | Full runs, decision impact, determinism, replay, stale cleanup, malformed rows, tamper detection |
| `integration/test_replaced_fixtures.py` | 1 | 1 | 0 | 0.17 | Renamed services, new region, new classes, `P1–P3` urgencies, action with no policy entry |
| `integration/test_lifecycle.py` | 12 | 12 | 0 | 0.53 | Interrupt/failure recorded in manifest, exit 130, start/stop/status script behaviour |

Slowest tests: provider auto-selection with a key (0.64 s, constructs the OpenAI client), replay end to end (0.33 s), scripted foreground run via `start.sh` (0.32 s).

### 3.3 Edge cases covered

| Category | Edge cases |
|---|---|
| Alert input | invalid JSON line, non-object line, blank lines, missing fields, unknown severity, unparseable timestamp, non-numeric or boolean values, numeric strings, timezone offsets, naive timestamps, unsorted rows, empty file, `--strict` fail-fast |
| Topology and correlation | service missing from topology, cyclic dependencies, empty topology, cross-region split, inclusive window boundary, chained windows, siblings joined only via a common dependent, zero-second window, deploy with no matching incident, deploy outside the window, deploy never merges incidents, same-service preference |
| Policy | action not in `allowed_actions`, action with no policy entry (gated), urgency-based permission, duplicate recommendations, wrong LLM approval flag, all actions filtered out, duplicate or empty policy lists |
| Operator review | unknown incident/action/urgency, malformed command, last decision wins, reject of an action not in the plan, operator-added action, downgrade invalidates an action and voids its approval, upgrade unlocks an action as pending, decision order independence, EOF without an empty line |
| LLM gateway | schema violation then corrective retry, semantic failure after retries, provider exception, call before correlation blocked, replay hit and miss, distinct prompt hashes |
| Validator (tamper detection) | edited brief, modified input file, missing Stage 2 record, disallowed action injected, tampered final urgency, missing artifact |
| Lifecycle | interruption mid-review recorded as `aborted`, failure recorded as `failed`, exit code 130, stale PID file, bad script arguments, missing inputs |

---

## 4. Coverage

| Layer | Statements | Missed | Coverage |
|---|---:|---:|---:|
| `domain/` | 853 | 6 | 99.3% |
| `application/` | 886 | 8 | 99.1% |
| `infrastructure/` | 405 | 7 | 98.3% |
| `interface/` | 101 | 2 | 98.0% |
| `bootstrap.py`, `shared/`, `__main__.py` | 63 | 4 | 93.7% |
| **Total** | **2,308** | **27** | **99%** |

Uncovered lines are mostly defensive branches (empty manifest on very early failure, a `getpass` fallback, unreachable offline-model branches) and `__main__.py` (the `python -m irp` entry point, exercised manually but not in pytest).

---

## 5. Requirement validation (`make validate`), latest live run

| # | Check | Result |
|---:|---|---|
| 1 | Required artifacts exist | ✅ PASS |
| 2 | JSON artifacts are valid | ✅ PASS |
| 3 | Input files were read from disk (SHA-256 matches the manifest) | ✅ PASS |
| 4 | Normalised alerts have the required schema | ✅ PASS (10 alerts) |
| 5 | Correlated incidents have the required schema | ✅ PASS (2 incidents covering 10 alerts) |
| 6 | Normalisation happened before any LLM call | ✅ PASS |
| 7 | Correlation happened before any LLM call | ✅ PASS |
| 8 | Pipeline stages ran in the required order | ✅ PASS (all 11 stages) |
| 9 | Every incident has a Stage 1 classification | ✅ PASS |
| 10 | Incident classes use only policy classes | ✅ PASS |
| 11 | Urgencies use only policy urgency levels | ✅ PASS |
| 12 | Exactly one Stage 1 call | ✅ PASS |
| 13 | Each incident has its own Stage 2 call (not batched) | ✅ PASS (2 separate calls) |
| 14 | Recommended actions use only allowed actions | ✅ PASS (11 actions) |
| 15 | Action policy compatibility is enforced | ✅ PASS |
| 16 | Approval flags come from policy | ✅ PASS |
| 17 | Operator decisions saved and applied to Stage 3 inputs | ✅ PASS (digest matches; replaying decisions reproduces the final state) |
| 18 | Incident brief reflects post-review urgencies and actions | ✅ PASS (8 sections + ledger) |
| 19 | `llm_calls.jsonl` has separate records for the required stages | ✅ PASS (7 records) |
| 20 | Blast radius scores are valid | ✅ PASS (INC-001 = 100, INC-002 = 30) |
| 21 | Policy simulation is valid | ✅ PASS (scenarios A and B) |

---

## 6. End-to-end runs

### 6.1 Live run: OpenAI `gpt-4.1` (2026-10-14 19:23:23Z → 19:24:12Z, ~49 s)

**LLM calls (`data/output/llm_calls.jsonl`)**

| Stage | Incident | Status | Attempts | Latency |
|---|---|---|---:|---:|
| `stage1_classification` | — | ok | 1 | 4.0 s |
| `stage2_remediation` | INC-001 | ok | 1 | 9.4 s |
| `stage2_remediation` | INC-002 | ok | 1 | 9.0 s |
| `stage3_brief` | — | ok | 1 | 12.5 s |
| `optional_automation_plan` | — | ok | 1 | 2.6 s |
| `optional_postmortem` | INC-001 | ok | 1 | 5.8 s |
| `optional_postmortem` | INC-002 | ok | 1 | 5.7 s |
| **Total** | | **7 / 7 ok** | **no retries** | **49.0 s** |

**Deterministic stages**

| Output | Result |
|---|---|
| Normalisation | 10 alerts, 0 rejected rows |
| Correlation | **INC-001**: `market-data-gateway`, `pricing-engine`, `order-router`, `api-edge` + canary deploy (7 alerts). **INC-002**: `feature-store`, `model-serving` (3 alerts). |
| Blast radius | INC-001 = **100** (high), INC-002 = **30** (low) |

**AI analysis (policy-checked)**

| Incident | Class | Urgency | Confidence | Recommended actions | Policy-filtered | Approval-flag mismatches |
|---|---|---|---|---|---:|---:|
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
| INC-001 | sev1 | — | diagnostics, page on-call, page platform lead, freeze deployments, status-page draft | `shift_traffic_region` | `switch_to_cached_quotes` |
| INC-002 | sev3 | `disable_ai_assist_path` | diagnostics, page on-call | `throttle_non_critical_requests` | — |

**What-if simulation**

| Scenario | Post-review effect | Pre-review effect |
|---|---|---|
| A: `disable_ai_assist_path` pre-approved | no change (the operator had already approved it) | INC-002: `disable_ai_assist_path` moves from pending to cleared |
| B: deploy signals ignored | INC-001 blast radius −10 (100 → 90) | no action changes |

### 6.2 Replay run (cached OpenAI responses)

| Item | Result |
|---|---|
| Network calls | 0 (all 7 served from `llm_cache/`, logged with provider `openai (replay)`) |
| Wall time | 0.47 s |
| `incident_analysis.json` vs live run | **identical** |
| Validation | 21 / 21 PASS |

### 6.3 Offline run (no API key)

| Item | Result |
|---|---|
| Wall time | 0.24 s |
| Classification | INC-001 `market_data_degradation` sev1; INC-002 `ai_inference_degradation` **sev2** |
| Urgency override `INC-002 sev2 → sev3` | applied; `open_status_page_draft` → **invalidated_by_override** (shows the urgency-gating rule end to end) |
| Validation | 21 / 21 PASS |

### 6.4 Operational checks

| Check | Result |
|---|---|
| Clean checkout (copy without `.venv`, `.env` or outputs) → `make setup`, `make run-scripted PROVIDER=offline`, `make validate`, `make test` | ✅ all succeeded |
| `scripts/start.sh -b` (background OpenAI run) → second `start.sh` | ✅ refused: "a pipeline run is already in progress" |
| `scripts/stop.sh` during the run | ✅ graceful stop; manifest records `termination: aborted after INCIDENTS_CORRELATED` |
| `scripts/status.sh` | ✅ RUNNING with PID, elapsed time and current stage; IDLE with the last result afterwards |
| Invalid OpenAI key | ✅ fails cleanly: both attempts logged as `failed` in `llm_calls.jsonl`, no brief generated, exit code 1 |

---

## 7. Observations and limitations

1. **The urgency override was a no-op in the live run.** OpenAI rated INC-002 sev3 directly, so `urgency INC-002 sev3` changed nothing and the invalidation path was not exercised live. It is covered by unit and integration tests and shown in the offline run (§6.3). Use `urgency INC-001 sev3` in the decisions file to see it with OpenAI.
2. **LLM outputs can vary between live runs.** Calls use temperature 0 and a fixed seed, but OpenAI does not guarantee identical outputs. Use `make replay` for byte-stable reruns.
3. **The OpenAI adapter's unit tests use a mocked client.** Live behaviour was verified by the end-to-end runs above, not inside pytest, so tests never need network access or an API key.
4. **The replaced-fixture test uses the offline model.** It proves nothing depends on sample names, IDs, regions or labels, but it was not repeated against live OpenAI.
5. **`__main__.py` (`python -m irp`) is not covered by pytest.** It is a 3-line wrapper around the tested CLI.
