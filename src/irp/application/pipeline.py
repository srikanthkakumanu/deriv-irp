"""Application service orchestrating the staged incident-response pipeline.

INIT -> INPUTS_LOADED -> ALERTS_NORMALIZED -> INCIDENTS_CORRELATED -> INCIDENTS_CLASSIFIED
-> REMEDIATION_PLANS_DRAFTED -> OPERATOR_REVIEW_COMPLETE -> INCIDENT_BRIEF_GENERATED
-> OPTIONAL_OUTPUTS_GENERATED -> VALIDATION_COMPLETE -> RESULTS_FINALISED
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..domain.alerts import AlertNormalizer
from ..domain.blast_radius import FORMULA, BlastRadiusCalculator
from ..domain.incident import IncidentCorrelator
from ..domain.pipeline_stage import Stage, StageMachine
from ..domain.review import ReviewSession
from ..domain.simulation import PolicySimulator
from .documents import BlastRadiusDocument, IncidentAnalysisDocument, ReviewView
from .llm_gateway import AuditedLLMGateway
from .ports import ArtifactStore, Clock, InputSource, LanguageModel, OperatorConsole, ReportPresenter
from .review_commands import REVIEW_PROMPT
from .use_cases.classify_incidents import ClassifyIncidents
from .use_cases.conduct_review import ConductOperatorReview
from .use_cases.draft_remediation import DraftRemediationPlan
from .use_cases.generate_brief import GenerateIncidentBrief, build_brief_payload
from .use_cases.optional_outputs import GeneratePostmortemSkeleton, GenerateSafeAutomationPlan, SimulatePolicyWhatIfs
from .validation import CheckResult, RunValidator

MANIFEST = "run_manifest.json"
GENERATED_ARTIFACTS = (
    "normalized_alerts.json", "correlated_incidents.json", "incident_analysis.json", "operator_decisions.json",
    "incident_brief.md", "blast_radius_scores.json", "safe_automation_plan.md", "postmortem_template.md",
    "policy_simulation.json", "llm_calls.jsonl", MANIFEST,
)


class PipelineSettings(BaseModel):
    correlation_window_s: int = 120
    strict_normalization: bool = False
    include_optional_outputs: bool = True
    replay: bool = False
    operator_name: str = "unknown"
    order_path_pattern: str = r"order|execution|routing|router"


class PipelineResult(BaseModel):
    status: str
    validation: list[CheckResult]
    incident_ids: list[str]
    brief_location: str


class IncidentResponsePipeline:
    def __init__(self, inputs: InputSource, store: ArtifactStore, input_store: ArtifactStore, model: LanguageModel,
                 console: OperatorConsole, presenter: ReportPresenter, clock: Clock, settings: PipelineSettings) -> None:
        self._inputs = inputs
        self._store = store
        self._input_store = input_store
        self._model = model
        self._console = console
        self._presenter = presenter
        self._clock = clock
        self._settings = settings
        self._stages = StageMachine(clock.now())
        self._manifest: dict[str, Any] = {}

    # -- manifest -----------------------------------------------------------------
    def _advance(self, stage: Stage, artifacts: tuple[str, ...] = (), **details: Any) -> None:
        self._stages.advance(stage, self._clock.now(), artifacts, **details)
        self._manifest["stages"] = [t.model_dump(mode="json") for t in self._stages.history]
        self._store.save_json(MANIFEST, self._manifest)

    def _log(self, text: str) -> None:
        self._console.show(f"[irp] {text}")

    # -- run ------------------------------------------------------------------------
    def run(self) -> PipelineResult:
        """Run all stages. An interruption (Ctrl-C / SIGTERM from ``scripts/stop.sh``) or an error is
        recorded in ``run_manifest.json`` with the stage reached, then re-raised."""
        try:
            return self._run()
        except KeyboardInterrupt:
            self._record_termination("aborted", "interrupted by operator or stop signal")
            raise
        except Exception as exc:
            self._record_termination("failed", f"{type(exc).__name__}: {exc}")
            raise

    def _record_termination(self, status: str, reason: str) -> None:
        if not self._manifest:
            return
        self._manifest["termination"] = {"status": status, "reason": reason, "at": self._clock.now(),
                                         "last_completed_stage": self._stages.current.value}
        self._store.save_json(MANIFEST, self._manifest)

    def _run(self) -> PipelineResult:
        for name in GENERATED_ARTIFACTS:
            self._store.remove(name)
        self._store.remove("llm_raw")
        gateway = AuditedLLMGateway(self._model, self._store, self._clock, self._stages, replay=self._settings.replay)
        self._manifest = {"run_started_at": self._clock.now(), "settings": self._settings.model_dump(),
                          "llm_provider": {"provider": self._model.name, "model": self._model.model}, "inputs": {},
                          "stages": [t.model_dump(mode="json") for t in self._stages.history]}
        self._store.save_json(MANIFEST, self._manifest)
        self._log(f"LLM provider: {gateway.provider_label}")

        # 1. Inputs from disk
        inputs = self._inputs.load()
        self._manifest["inputs"] = {k: v.model_dump() for k, v in inputs.provenance.items()}
        self._advance(Stage.INPUTS_LOADED, tuple(p.path for p in inputs.provenance.values()))
        policy, topology = inputs.policy, inputs.topology

        # 2. Deterministic normalization
        normalized = AlertNormalizer(strict=self._settings.strict_normalization).normalize(inputs.alert_records)
        if not normalized.alerts:
            raise ValueError("no valid alerts to process")
        alerts = normalized.alerts
        self._store.save_json("normalized_alerts.json", [a.model_dump(mode="json") for a in alerts])
        self._advance(Stage.ALERTS_NORMALIZED, ("normalized_alerts.json",), alert_count=len(alerts),
                      rejected_rows=[r.model_dump() for r in normalized.rejected])
        self._log(f"normalized {len(alerts)} alerts ({len(normalized.rejected)} rejected rows)")

        # 3. Deterministic correlation + blast radius (pure domain code; no LLM)
        correlator = IncidentCorrelator(topology, self._settings.correlation_window_s)
        incidents = correlator.correlate(alerts)
        calculator = BlastRadiusCalculator(topology, self._settings.order_path_pattern)
        blast = calculator.score_all(incidents, alerts)
        blast_by_id = {b.incident_id: b for b in blast}
        self._store.save_json("correlated_incidents.json", [i.model_dump(mode="json") for i in incidents])
        self._store.save_json("blast_radius_scores.json", BlastRadiusDocument(formula=FORMULA, scores=blast).model_dump(mode="json"))
        self._advance(Stage.INCIDENTS_CORRELATED, ("correlated_incidents.json", "blast_radius_scores.json"),
                      incident_count=len(incidents), correlation_window_s=correlator.window_s)
        self._log(f"correlated into {len(incidents)} incident(s): {', '.join(i.incident_id for i in incidents)}")

        # 4. Stage 1 — one classification call
        classifications = ClassifyIncidents(gateway, policy).execute(incidents, alerts, inputs.raw_topology, inputs.raw_policy, blast)
        analysis = IncidentAnalysisDocument(stage1=classifications)
        self._store.save_json("incident_analysis.json", analysis.model_dump(mode="json"))
        self._advance(Stage.INCIDENTS_CLASSIFIED, ("incident_analysis.json",))

        # 5. Stage 2 — one call per incident, appended while preserving Stage 1
        drafter = DraftRemediationPlan(gateway, policy)
        for inc in incidents:
            analysis.stage2[inc.incident_id] = drafter.execute(inc, classifications[inc.incident_id], alerts, blast_by_id[inc.incident_id])
            self._store.save_json("incident_analysis.json", analysis.model_dump(mode="json"))
        plans = analysis.stage2
        self._advance(Stage.REMEDIATION_PLANS_DRAFTED, ("incident_analysis.json",))

        # 6. Operator review checkpoint
        session = ReviewSession(policy, classifications, plans)
        view = ReviewView(incidents=incidents, classifications=classifications, plans=plans, blast=blast_by_id, prompt=REVIEW_PROMPT)
        decisions = ConductOperatorReview(self._console, self._presenter, self._clock, self._settings.operator_name).execute(session, view)
        payload = build_brief_payload(incidents, classifications, plans, decisions, blast)
        decisions = decisions.model_copy(update={"stage3_input_digest": GenerateIncidentBrief.input_digest(payload)})
        self._store.save_json("operator_decisions.json", decisions.model_dump(mode="json"))
        self._advance(Stage.OPERATOR_REVIEW_COMPLETE, ("operator_decisions.json",), decision_count=len(decisions.decisions))

        # 7. Stage 3 — brief from post-review state only
        final_states = decisions.final_state
        brief = GenerateIncidentBrief(gateway, self._presenter, self._stages, self._clock).execute(payload, final_states)
        self._store.save_text("incident_brief.md", brief)
        self._advance(Stage.INCIDENT_BRIEF_GENERATED, ("incident_brief.md",))

        # 8. Optional outputs
        optional: tuple[str, ...] = ("blast_radius_scores.json",)
        if self._settings.include_optional_outputs:
            self._store.save_text("safe_automation_plan.md",
                                  GenerateSafeAutomationPlan(gateway, self._presenter, policy, inputs.raw_policy).execute(final_states))
            self._store.save_text("postmortem_template.md", GeneratePostmortemSkeleton(gateway, self._presenter).execute(
                incidents, alerts, classifications, plans, final_states))
            simulator = PolicySimulator(policy, topology, correlator, calculator)
            report = SimulatePolicyWhatIfs(simulator).execute(alerts, incidents, classifications, plans,
                                                              list(decisions.decisions), final_states, blast)
            self._store.save_json("policy_simulation.json", report.model_dump(mode="json"))
            optional += ("safe_automation_plan.md", "postmortem_template.md", "policy_simulation.json")
        self._advance(Stage.OPTIONAL_OUTPUTS_GENERATED, optional)

        # 9. Validation, 10. Finalise
        results = RunValidator(self._store, self._input_store, during_run=True).run()
        failed = [r for r in results if not r.ok]
        self._advance(Stage.VALIDATION_COMPLETE, (), checks=len(results), failed=[r.name for r in failed])
        for r in failed:
            self._log(f"VALIDATION FAIL: {r.name}: {r.detail}")
        status = "ok" if not failed else "validation_failed"
        self._advance(Stage.RESULTS_FINALISED, (), status=status)
        self._log(f"done — {len(results) - len(failed)}/{len(results)} validation checks passed. "
                  f"Brief: {self._store.location('incident_brief.md')}")
        return PipelineResult(status=status, validation=results, incident_ids=[i.incident_id for i in incidents],
                              brief_location=self._store.location("incident_brief.md"))
