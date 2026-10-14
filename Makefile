.DEFAULT_GOAL := help
PROVIDER ?= auto
DECISIONS ?= fixtures/sample_decisions.txt
UV ?= uv

# DATA_DIR unset -> each input file is taken from the repo root if present, else from data/
DATA_DIR ?=
OUT_DIR ?= data/output
GENERATED := normalized_alerts.json correlated_incidents.json incident_analysis.json operator_decisions.json \
             incident_brief.md blast_radius_scores.json safe_automation_plan.md postmortem_template.md \
             policy_simulation.json llm_calls.jsonl run_manifest.json
IO_ARGS = $(if $(DATA_DIR),--input-dir $(DATA_DIR)) --out-dir $(OUT_DIR)
IN_OPT = $(if $(DATA_DIR),-i $(DATA_DIR))

.PHONY: help setup run run-scripted replay start start-bg stop status restart validate test coverage clean clean-all

help: ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  %-14s %s\n", $$1, $$2}'

setup: ## Create the virtualenv and install dependencies (incl. dev)
	$(UV) sync --group dev

run: ## Run the pipeline with the interactive operator review (PROVIDER=auto|openai|offline)
	$(UV) run irp run --provider $(PROVIDER) $(IO_ARGS)

run-scripted: ## Run with a scripted operator review (DECISIONS=fixtures/sample_decisions.txt)
	$(UV) run irp run --provider $(PROVIDER) $(IO_ARGS) --decisions-file $(DECISIONS)

replay: ## Re-run from cached LLM responses (no network) with the same scripted decisions
	$(UV) run irp run --provider $(PROVIDER) $(IO_ARGS) --decisions-file $(DECISIONS) --replay

MODE ?= scripted

start: ## Start via scripts/start.sh in the foreground (MODE=interactive|scripted|replay, PROVIDER=...)
	scripts/start.sh $(IN_OPT) -o $(OUT_DIR) --mode $(MODE) --provider $(PROVIDER) $(if $(filter-out interactive,$(MODE)),--decisions $(DECISIONS))

start-bg: ## Start a scripted/replay run in the background (log: .run/irp.log)
	scripts/start.sh $(IN_OPT) -o $(OUT_DIR) --mode $(MODE) --provider $(PROVIDER) --decisions $(DECISIONS) --background

stop: ## Gracefully stop an in-flight run (SIGTERM, then SIGKILL after 15 s)
	scripts/stop.sh

status: ## Show whether a run is in progress and the stage reached by the latest run
	scripts/status.sh -o $(OUT_DIR)

restart: ## Stop any in-flight run, then start a new one in the background
	scripts/restart.sh $(IN_OPT) -o $(OUT_DIR) --mode $(MODE) --provider $(PROVIDER) --decisions $(DECISIONS) --background

validate: ## Validate the artifacts of the last run
	$(UV) run python validate.py $(IO_ARGS)

test: ## Run the pytest suite
	$(UV) run --group dev pytest -q

coverage: ## Run tests with a coverage report
	$(UV) run --group dev pytest -q --cov=irp --cov-report=term-missing

clean: ## Delete generated artifacts in data/output (keeps inputs and llm_cache/)
	rm -f $(addprefix $(OUT_DIR)/,$(GENERATED))
	rm -rf $(OUT_DIR)/llm_raw

clean-all: clean ## Also delete data/output entirely (incl. LLM replay cache) and Python caches
	rm -rf $(OUT_DIR) .pytest_cache .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
