# Incident-Response Orchestration Pipeline

[![CI](https://github.com/srikanthkakumanu/deriv-irp/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/srikanthkakumanu/deriv-irp/actions/workflows/ci.yml)

A replayable, policy-gated incident-response control plane for a trading platform. It works in five steps:

1. Ingest telemetry from disk.
2. Normalise and correlate alerts **deterministically**.
3. Classify incidents and draft remediation plans with a **policy-constrained LLM** (OpenAI).
4. Pause for an **operator approval/override checkpoint**.
5. Produce an execution-ready incident brief, plus a postmortem skeleton, an automation plan and a what-if simulation.

> **Nothing is executed.** Everything the AI produces is a recommendation. Only operator decisions and the runbook policy determine what is *approved*.

| Document | Purpose |
|---|---|
| [`docs/REQUIREMENT.md`](docs/REQUIREMENT.md) | Original challenge |
| [`docs/BRD.md`](docs/BRD.md) | Personas, use cases, scope, resolved ambiguities |
| [`docs/IMPLEMENTATION_SPEC.md`](docs/IMPLEMENTATION_SPEC.md) | Architecture, workflows, layers, tech stack, out of scope |
| [`docs/TEST_SUMMARY.md`](docs/TEST_SUMMARY.md) | Test results: pytest, coverage, validation checks, live/replay/offline runs |

## Quick start

```bash
make setup                                  # uv sync (Python >= 3.11, openai, pydantic, pytest)
cp .env.example .env                        # optional: add OPENAI_API_KEY (and OPENAI_MODEL)

make run                                    # interactive operator review
make run-scripted PROVIDER=offline          # scripted review from fixtures/sample_decisions.txt, no network
make validate                               # 21 requirement checks (also: python validate.py)
make test                                   # 175 pytest tests (make coverage for the report)

scripts/start.sh -m scripted -b             # or use the start/stop scripts (see below)
scripts/status.sh && scripts/stop.sh
```

## Start / stop scripts

The application is a **batch pipeline**, not a long-running server. "Start" launches a pipeline run, in the foreground or the background. "Stop" shuts down an in-flight run, typically one waiting on the operator or on LLM calls.

| Script | Make target | What it does |
|---|---|---|
| `scripts/start.sh` | `make start`, `make start-bg` | Checks the inputs, runs `uv sync` if `.venv` is missing, refuses to start if a run is already in progress, writes `.run/irp.pid`, then runs `irp run` |
| `scripts/stop.sh` | `make stop` | Sends SIGTERM. The pipeline records `termination` (status `aborted`, last completed stage) in `run_manifest.json` and exits with code 130. After the timeout it sends SIGKILL. |
| `scripts/status.sh` | `make status` | Shows RUNNING or IDLE, PID, elapsed time, run options, the latest run's last stage and result or termination, and the background log tail |
| `scripts/restart.sh` | `make restart` | `stop.sh`, then `start.sh` with the given options |

### `scripts/start.sh` options

| Option | Values | Default |
|---|---|---|
| `-m, --mode` | `interactive` (operator types commands) · `scripted` (decisions file) · `replay` (decisions file + cached LLM responses, no network) | `interactive` |
| `-p, --provider` | `auto` · `openai` · `offline` | `auto` (or `$IRP_PROVIDER`) |
| `-d, --decisions FILE` | operator commands, one per line, `#` for comments | `fixtures/sample_decisions.txt` for scripted/replay |
| `-b, --background` | run detached, logging to `.run/irp.log` (not allowed with `interactive`) | off |
| `-i, --input-dir DIR` | folder with `alerts.jsonl`, `service_topology.json`, `runbook_policy.json` | `data` |
| `-o, --out-dir DIR` | artifact output folder | `data/output` |
| `-s, --skip-optional` | skip the automation plan, postmortem and simulation | off |
| `-- …` | extra arguments passed to `irp run` (e.g. `-- --window 60 --strict`) | — |

`scripts/stop.sh` options: `-t, --timeout SECONDS` (default 15) and `-f, --force` (immediate SIGKILL, with no termination record).

```bash
scripts/start.sh                                  # interactive operator review (Ctrl-C to abort)
scripts/start.sh -m scripted -p offline           # scripted review, no network
scripts/start.sh -m scripted -p openai -b         # background run with OpenAI
scripts/status.sh                                 # RUNNING (PID …) + current stage
scripts/stop.sh                                   # graceful stop; data/output/run_manifest.json shows where it stopped
scripts/start.sh -m replay                        # replay the last run from data/output/llm_cache/
scripts/restart.sh -m scripted -b                 # stop + start again

make start MODE=scripted PROVIDER=offline         # the same via make
make start-bg MODE=scripted PROVIDER=openai
make status && make stop
```

Exit codes from `start.sh` (foreground) and `irp run`: `0` ok · `1` error (bad input, LLM failure, bad arguments) · `2` validation failed · `130` stopped or interrupted.

Run state is kept in `.run/` (`irp.pid`, `irp.meta`, `irp.log`), which is git-ignored. Stale PID files left by a crashed run are cleaned up automatically. Set `IRP_RUN_DIR` to move this folder.

Provider selection: `--provider auto` is the default. It uses OpenAI when `OPENAI_API_KEY` is set in the environment or `.env`, and otherwise the **offline deterministic model**. That model follows the same contract and is logged as `offline-deterministic`.

### Operator review

After Stage 2 the pipeline shows each incident: its class, urgency, blast radius, and recommended actions marked `[NEEDS HUMAN APPROVAL]` or `[policy: no approval required]`. It then waits for commands:

```text
approve <incident_id> <action>
reject <incident_id> <action>
urgency <incident_id> <sev0|sev1|sev2|sev3>
<empty line to finish>
```

Invalid commands are rejected inline and logged. After the review, the policy is re-applied at the final urgency. For example, downgrading to `sev3` invalidates `page_platform_lead` and `open_status_page_draft`. Stage 3 receives only this post-review state.

### Replay

- `--decisions-file FILE` replays the operator's commands.
- `--replay` (or `make replay`) serves LLM responses from `data/output/llm_cache/` by prompt hash, with no network calls.
- Prompts contain no wall-clock data, so the same inputs and decisions give the same prompt hashes.

## Architecture (summary)

Layered, hexagonal architecture following Domain-Driven Design. Dependencies point inward only.

```
src/irp/
├── domain/          # pure rules: AlertNormalizer, IncidentCorrelator, RunbookPolicy, ReviewSession,
│                    #   BlastRadiusCalculator, AutomationTiering, PolicySimulator, StageMachine
├── application/     # use cases, ports, Pydantic LLM contracts, AuditedLLMGateway, RunValidator, prompts
├── infrastructure/  # filesystem, OpenAI, offline model, Markdown presenter, clock adapters
├── interface/       # CLI (irp run / irp validate), terminal and scripted operator consoles
└── bootstrap.py     # composition root
scripts/             # start.sh · stop.sh · status.sh · restart.sh (+ _common.sh helpers)
docs/                # REQUIREMENT.md · BRD.md · IMPLEMENTATION_SPEC.md · TEST_SUMMARY.md
```

Pipeline stages (enforced; out-of-order transitions raise):
`INIT → INPUTS_LOADED → ALERTS_NORMALIZED → INCIDENTS_CORRELATED → INCIDENTS_CLASSIFIED → REMEDIATION_PLANS_DRAFTED → OPERATOR_REVIEW_COMPLETE → INCIDENT_BRIEF_GENERATED → OPTIONAL_OUTPUTS_GENERATED → VALIDATION_COMPLETE → RESULTS_FINALISED`

**Correlation rule.** Alerts are linked when they share a region, fall within 120 s of each other (the window chains) and are causally adjacent in the topology: the same service, or one transitively depends on the other. Deploy/change signals attach to the best adjacent incident and never merge incidents.

**Blast radius.** `min(100, S + C + V + O + D)`:

| Factor | Points |
|---|---|
| S: impacted services | `min(30, 10 × n)` |
| C: critical path involved | 25 |
| V: maximum severity | critical 20 · warning 10 · info 0 |
| O: order routing impacted | 15 |
| D: deploy signal present | 10 |

Full details are in [`docs/IMPLEMENTATION_SPEC.md`](docs/IMPLEMENTATION_SPEC.md).

## Data layout

```
data/
├── alerts.jsonl              # input: telemetry, one JSON object per line
├── service_topology.json     # input: services, dependencies, tiers
├── runbook_policy.json       # input: incident classes, urgency levels, allowed actions, action policies
└── output/                   # generated by every run (git-ignored)
    ├── *.json, *.jsonl, *.md # artifacts listed below
    ├── llm_raw/              # full LLM request/response per call
    └── llm_cache/            # replay cache keyed by prompt hash
fixtures/sample_decisions.txt # scripted operator review
```

To use replacement fixtures, overwrite the three files in `data/`, or point the run at another folder with `--input-dir DIR` (`-i DIR` for the scripts, `DATA_DIR=DIR` for make). Outputs can be redirected the same way with `--out-dir`, `-o` or `OUT_DIR=`.

## Artifacts

Each run deletes the previous generated artifacts in `data/output/` first, then writes:

| Artifact | |
|---|---|
| `normalized_alerts.json`, `correlated_incidents.json`, `blast_radius_scores.json` | deterministic |
| `incident_analysis.json` | Stage 1 classifications + Stage 2 policy-filtered plans |
| `operator_decisions.json` | commands, decisions, per-incident final state, Stage 3 input digest |
| `incident_brief.md` | 8 required sections + deterministic **Authoritative Action Ledger** |
| `safe_automation_plan.md`, `postmortem_template.md`, `policy_simulation.json` | optional outputs |
| `llm_calls.jsonl`, `llm_raw/`, `llm_cache/`, `run_manifest.json` | audit trail and replay (`run_manifest.json` gains a `termination` record if the run was stopped or failed) |

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push and pull request to `main`, and can also be started manually (**Actions → CI → Run workflow**).

| Job | When | What it does |
|---|---|---|
| **Tests** (Python 3.11 / 3.12 / 3.13) | always | `uv sync --locked`, byte-compile, shell-script syntax check, pytest with coverage (fails below 95%); uploads `junit.xml` and `coverage.xml` |
| **End-to-end pipeline (offline model)** | after Tests | `make run-scripted PROVIDER=offline`, `make validate`, `make replay` + validate, start/status/stop script smoke test; uploads `data/output/` (except `llm_raw/`) |
| **End-to-end pipeline (live OpenAI)** | manual run with **live_openai** ticked | same run against OpenAI and validate. Needs the `OPENAI_API_KEY` repository secret (optional `OPENAI_MODEL` variable) and incurs API cost. |

CI needs no secrets by default: the offline deterministic model covers the full pipeline.


| Variable | Default |
|---|---|
| `OPENAI_API_KEY` | unset → offline model |
| `OPENAI_MODEL` | `gpt-4.1` |
| `IRP_PROVIDER` | `auto` |
| `IRP_CORRELATION_WINDOW_S` | `120` |
| `IRP_ORDER_PATH_PATTERN` | `order\|execution\|routing\|router` |
| `IRP_OPERATOR` | OS user |
| `--input-dir` / `--out-dir` (`DATA_DIR` / `OUT_DIR` for make) | `data` / `data/output` |

Secrets in `.env` and everything under `data/output/` (including the raw LLM captures in `llm_raw/`) are git-ignored.
