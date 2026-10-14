"""Parser for the operator review command grammar required by the runbook process."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

REVIEW_PROMPT = (
    "Review incident plans. You may approve actions, reject actions, or override urgency before generating the incident brief.\n"
    "Enter commands in the form:\n"
    "approve <incident_id> <action>\n"
    "reject <incident_id> <action>\n"
    "urgency <incident_id> <sev0|sev1|sev2|sev3>\n"
    "Press Enter on an empty line when done."
)


class CommandSyntaxError(ValueError):
    pass


class ReviewCommand(BaseModel):
    model_config = ConfigDict(frozen=True)
    verb: Literal["approve", "reject", "urgency"]
    incident_id: str
    argument: str


def parse_command(line: str) -> ReviewCommand:
    parts = line.split()
    if len(parts) != 3:
        raise CommandSyntaxError("expected exactly 3 tokens: <approve|reject|urgency> <incident_id> <action|urgency>")
    verb = parts[0].lower()
    if verb not in ("approve", "reject", "urgency"):
        raise CommandSyntaxError(f"unknown command '{parts[0]}' (use approve | reject | urgency)")
    return ReviewCommand(verb=verb, incident_id=parts[1], argument=parts[2])  # type: ignore[arg-type]
