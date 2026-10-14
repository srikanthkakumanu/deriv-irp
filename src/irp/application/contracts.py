"""LLM response contracts as Pydantic models.

Enums (incident classes, urgency levels, actions, incident IDs) are generated at RUNTIME
from the loaded policy and correlated incidents, so replacing the fixtures automatically
changes what the model may answer. The same models produce the strict JSON schema sent
to the provider and validate the response.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from ..domain.policy import RunbookPolicy

BRIEF_SECTIONS: tuple[str, ...] = (
    "Executive Summary",
    "Active Incidents",
    "Immediate Approved Actions",
    "Actions Pending Human Approval or Escalation",
    "Actions Explicitly Rejected",
    "Verification and Exit Criteria",
    "Communications Draft",
    "Follow-Up Engineering Recommendations",
)
LEDGER_HEADING = "Authoritative Action Ledger (system of record)"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _literal(values: tuple[str, ...] | list[str]) -> Any:
    return Literal[tuple(values)]  # type: ignore[valid-type]


# ---------------------------------------------------------------- Stage 1
def classification_contract(incident_ids: list[str], policy: RunbookPolicy) -> type[Contract]:
    item = create_model(
        "IncidentClassification",
        __base__=Contract,
        incident_id=(_literal(incident_ids), ...),
        incident_class=(_literal(policy.incident_classes), ...),
        urgency=(_literal(policy.urgency_levels), ...),
        customer_impact_summary=(str, ...),
        suspected_root_cause=(str, ...),
        confidence=(Literal["low", "medium", "high"], ...),
    )
    return create_model("ClassificationResponse", __base__=Contract, incidents=(list[item], ...))  # type: ignore[valid-type]


# ---------------------------------------------------------------- Stage 2
def remediation_contract(incident_id: str, policy: RunbookPolicy) -> type[Contract]:
    action_t = _literal(policy.allowed_actions)
    proposed = create_model("ProposedActionOut", __base__=Contract, action=(action_t, ...), reason=(str, ...),
                            requires_human_approval=(bool, ...), risk_note=(str, ...))
    avoided = create_model("AvoidedActionOut", __base__=Contract, action=(action_t, ...), reason=(str, ...))
    return create_model(
        "RemediationResponse", __base__=Contract,
        incident_id=(_literal([incident_id]), ...),
        recommended_actions=(list[proposed], Field(..., min_length=1)),  # type: ignore[valid-type]
        explicitly_avoided_actions=(list[avoided], ...),  # type: ignore[valid-type]
        rollback_consideration=(str, ...),
        verification_checks=(list[str], ...),
    )


# ---------------------------------------------------------------- Stage 3 / optional
class BriefResponse(Contract):
    brief_markdown: str


class PostmortemResponse(Contract):
    incident_id: str
    contributing_factors: list[str]
    prevention_architecture: list[str]
    prevention_observability: list[str]
    prevention_process: list[str]


class AutomationNarrativeResponse(Contract):
    narrative: str


# ---------------------------------------------------------------- helpers
def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema -> OpenAI strict structured-output schema
    (every property required, no additional properties, no defaults)."""
    schema = copy.deepcopy(model.model_json_schema())

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            node.pop("title", None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            if "const" in node and "enum" not in node:
                node["enum"] = [node.pop("const")]
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return schema


def split_sections(markdown: str) -> dict[str, str]:
    """Map each level-2 heading to its body text."""
    parts = re.split(r"^##\s+(.+?)\s*$", markdown, flags=re.M)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}
