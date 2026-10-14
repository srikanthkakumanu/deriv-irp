import pytest
from pydantic import ValidationError

from irp.application.contracts import classification_contract, remediation_contract, split_sections, strict_json_schema


def test_classification_enums_come_from_policy(policy):
    model = classification_contract(["INC-001"], policy)
    ok = {"incidents": [{"incident_id": "INC-001", "incident_class": "unknown", "urgency": "sev2",
                         "customer_impact_summary": "x", "suspected_root_cause": "y", "confidence": "low"}]}
    model.model_validate(ok)
    for field, bad in (("incident_class", "made_up_class"), ("urgency", "P1"), ("incident_id", "INC-999"), ("confidence", "sure")):
        broken = {"incidents": [{**ok["incidents"][0], field: bad}]}
        with pytest.raises(ValidationError):
            model.model_validate(broken)


def test_extra_fields_rejected(policy):
    model = classification_contract(["INC-001"], policy)
    with pytest.raises(ValidationError):
        model.model_validate({"incidents": [], "extra": 1})


def test_remediation_requires_allowed_action_and_matching_id(policy):
    model = remediation_contract("INC-002", policy)
    good = {"incident_id": "INC-002", "recommended_actions": [{"action": "page_primary_oncall", "reason": "r",
            "requires_human_approval": False, "risk_note": "n"}], "explicitly_avoided_actions": [],
            "rollback_consideration": "rb", "verification_checks": []}
    model.model_validate(good)
    with pytest.raises(ValidationError):
        model.model_validate({**good, "incident_id": "INC-001"})
    with pytest.raises(ValidationError):
        model.model_validate({**good, "recommended_actions": [{**good["recommended_actions"][0], "action": "nuke"}]})
    with pytest.raises(ValidationError):
        model.model_validate({**good, "recommended_actions": []})


def test_strict_schema_shape(policy):
    schema = strict_json_schema(remediation_contract("INC-001", policy))

    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                yield node
            for v in node.values():
                yield from objects(v)
        elif isinstance(node, list):
            for v in node:
                yield from objects(v)

    objs = list(objects(schema))
    assert objs
    for obj in objs:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
    assert "default" not in str(schema)


def test_split_sections():
    md = "intro\n## A\nbody a\n## B\nbody b\n"
    assert split_sections(md) == {"A": "\nbody a\n", "B": "\nbody b\n"}
