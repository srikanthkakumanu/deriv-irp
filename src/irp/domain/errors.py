"""Domain-level exceptions."""


class DomainError(Exception):
    """Base class for violations of domain rules."""


class NormalizationError(DomainError):
    """A raw alert record cannot be turned into a normalized alert."""


class InvalidDecisionError(DomainError):
    """An operator decision is not valid for the incident or policy."""


class StageOrderError(DomainError):
    """A pipeline stage was entered out of order or a guard was violated."""
