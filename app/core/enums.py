"""Cross-cutting domain vocabulary.

Visibility lives here because the authorization chokepoint in
``app.core.authorization`` is the one place allowed to interpret it.
"""

from __future__ import annotations

from django.db import models


class Visibility(models.TextChoices):
    NORMAL = "NORMAL", "Tavaline"
    RESTRICTED = "RESTRICTED", "Piiratud"


# Higher number means more restrictive. A child record may raise this value but
# never lower it below its parent (master specification 5.2).
VISIBILITY_RESTRICTIVENESS: dict[str, int] = {
    Visibility.NORMAL.value: 0,
    Visibility.RESTRICTED.value: 10,
}


def restrictiveness(value: str) -> int:
    """How restrictive a visibility value is. Unknown values are the most.

    Refusing to rank an unrecognised value would turn a data problem into an
    exception on a read path; ranking it as permissive would turn it into a
    leak. Treating it as maximally restrictive fails closed, which is the only
    acceptable direction for this particular fact.
    """
    if value in VISIBILITY_RESTRICTIVENESS:
        return VISIBILITY_RESTRICTIVENESS[value]
    return max(VISIBILITY_RESTRICTIVENESS.values())


def is_known_visibility(value: str) -> bool:
    return value in VISIBILITY_RESTRICTIVENESS


def most_restrictive(*values: str) -> str:
    """Return the most restrictive of the given visibility values.

    Always returns a value from the known vocabulary: an unrecognised input
    resolves to RESTRICTED rather than being echoed back, so callers can rely on
    the result without re-validating it.
    """
    if not values:
        raise ValueError("At least one visibility value is required.")
    if any(not is_known_visibility(value) for value in values):
        return Visibility.RESTRICTED.value
    return max(values, key=restrictiveness)


def is_at_least_as_restrictive(child: str, parent: str) -> bool:
    return restrictiveness(child) >= restrictiveness(parent)
