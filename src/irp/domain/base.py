"""Base classes for domain models (immutable Pydantic value objects / entities)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ValueObject(BaseModel):
    """Immutable, equality-by-value domain object."""

    model_config = ConfigDict(frozen=True, extra="forbid")
