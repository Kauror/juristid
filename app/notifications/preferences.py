"""What one person has chosen to be told, and what they get without choosing (docs/adr/0198 §5).

The same shape as remembered blocks (ADR 0194): **only an explicit choice is
stored**, and a category nobody touched follows its registry default, so a
default changed in a later release reaches everybody who never chose otherwise.
Saving a form that restates the defaults stores nothing; «Taasta vaikimisi»
deletes the person's rows and nothing else.

Presentation state of the signed-in account — the persona under the shared
gate, the person under personal sign-in — never read by authorization and
never a business fact, so a save writes no `ChangeEvent`. Keyed to the `User`
foreign key, so personal sign-in later keeps every choice with no conversion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import transaction

from app.notifications.categories import CATEGORIES, ESCALATION_KEY, PREFERENCE_KEYS
from app.notifications.models import (
    ALLOWED_OFFSETS,
    NotificationPreference,
    NotificationSettings,
)


@dataclass(frozen=True)
class Preference:
    enabled: bool
    offsets: tuple[int, ...]
    on_day: bool


@dataclass(frozen=True)
class Choices:
    """One person's effective preferences: every key, stored or default."""

    preferences: dict[str, Preference]
    paused: bool
    email_requested: bool

    def of(self, key: str) -> Preference:
        return self.preferences[key]


def default_preference(key: str) -> Preference:
    if key == ESCALATION_KEY:
        return Preference(enabled=True, offsets=(), on_day=False)
    category = CATEGORIES[key]
    return Preference(
        enabled=category.default_enabled,
        offsets=tuple(category.default_offsets),
        on_day=category.default_on_day and category.supports_on_day,
    )


def normalise_offsets(values: Any) -> tuple[int, ...]:
    """The allowed intervals among ``values``, largest first; anything else is dropped."""
    chosen = set()
    for value in values or ():
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number in ALLOWED_OFFSETS:
            chosen.add(number)
    return tuple(sorted(chosen, reverse=True))


def choices_for(user: Any) -> Choices:
    """Two small reads: the person's stored rows and their global switches."""
    preferences = {key: default_preference(key) for key in sorted(PREFERENCE_KEYS)}
    if user is None or getattr(user, "pk", None) is None:
        return Choices(preferences=preferences, paused=False, email_requested=False)
    for row in NotificationPreference.objects.filter(user=user, category__in=PREFERENCE_KEYS):
        category = CATEGORIES.get(row.category)
        preferences[row.category] = Preference(
            enabled=row.enabled,
            offsets=normalise_offsets(row.offsets) if category is not None else (),
            on_day=bool(row.on_day and category is not None and category.supports_on_day),
        )
    stored = NotificationSettings.objects.filter(user=user).first()
    return Choices(
        preferences=preferences,
        paused=bool(stored and stored.paused),
        email_requested=bool(stored and stored.email_requested),
    )


@transaction.atomic
def save_choices(
    user: Any,
    preferences: dict[str, Preference],
    *,
    paused: bool,
    email_requested: bool | None = None,
) -> None:
    """Store what differs from the defaults; forget what restates them.

    ``email_requested=None`` keeps the stored wish as it is: the settings page
    cannot change it until personal sign-in exists (its control is disabled).
    """
    for key in sorted(PREFERENCE_KEYS):
        if key not in preferences:
            continue
        chosen = preferences[key]
        if key in CATEGORIES:
            category = CATEGORIES[key]
            chosen = Preference(
                enabled=chosen.enabled,
                offsets=normalise_offsets(chosen.offsets),
                on_day=chosen.on_day and category.supports_on_day,
            )
        else:
            chosen = Preference(enabled=chosen.enabled, offsets=(), on_day=False)
        if chosen == default_preference(key):
            NotificationPreference.objects.filter(user=user, category=key).delete()
            continue
        NotificationPreference.objects.update_or_create(
            user=user,
            category=key,
            defaults={
                "enabled": chosen.enabled,
                "offsets": list(chosen.offsets),
                "on_day": chosen.on_day,
            },
        )
    stored = NotificationSettings.objects.filter(user=user).first()
    wish = (
        email_requested if email_requested is not None else bool(stored and stored.email_requested)
    )
    if not paused and not wish:
        NotificationSettings.objects.filter(user=user).delete()
        return
    NotificationSettings.objects.update_or_create(
        user=user, defaults={"paused": paused, "email_requested": wish}
    )


@transaction.atomic
def reset_choices(user: Any) -> None:
    """«Taasta vaikimisi»: forget every explicit choice of this person, and only those."""
    NotificationPreference.objects.filter(user=user).delete()
    NotificationSettings.objects.filter(user=user).delete()
