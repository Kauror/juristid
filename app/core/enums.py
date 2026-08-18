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
    try:
        return VISIBILITY_RESTRICTIVENESS[value]
    except KeyError as exc:  # pragma: no cover - guards a programming error
        raise ValueError(f"Unknown visibility {value!r}") from exc


def most_restrictive(*values: str) -> str:
    """Return the most restrictive of the given visibility values."""
    if not values:
        raise ValueError("At least one visibility value is required.")
    return max(values, key=restrictiveness)


def is_at_least_as_restrictive(child: str, parent: str) -> bool:
    return restrictiveness(child) >= restrictiveness(parent)
