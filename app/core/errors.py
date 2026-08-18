"""Domain-level exceptions raised by application services."""

from __future__ import annotations


class DomainError(Exception):
    """A business rule rejected the requested operation."""


class InvariantViolation(DomainError):
    """Code attempted to break an invariant the product guarantees."""


class ImmutableRecordError(InvariantViolation):
    """An append-only or immutable record was modified."""
