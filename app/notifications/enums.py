"""Vocabulary of the commencement-review reminder (docs/adr/0188)."""

from __future__ import annotations

from django.db import models


class ReminderStage(models.TextChoices):
    """Which of the two prompts for one commencement date this row is.

    Two, and fixed. The owner's brief asks for seven calendar days ahead and, if
    the check is still outstanding, the day itself; a configurable schedule is
    explicitly out of scope (brief C3).
    """

    SEVEN_DAYS = "SEVEN_DAYS", "7 päeva enne"
    ON_DATE = "ON_DATE", "Jõustumise päeval"


class ReminderStatus(models.TextChoices):
    """Where one reminder row stands. Nothing is ever deleted.

    ``OPEN`` is the only state anybody is asked to act on; a deferral is an
    ``OPEN`` row with ``deferred_until`` in the future, not a state of its own,
    so «is anything outstanding» stays one equality.

    ``ACKNOWLEDGED`` and ``DISMISSED`` are a person's answers and close every
    stage of the same commencement date. ``SUPERSEDED`` is the system's: the
    on-the-day prompt replaced the seven-day one that nobody answered.
    ``OBSOLETE`` is the system's too: the commencement the row was generated for
    no longer stands as it did — moved to another day, cancelled, replaced,
    removed, made approximate or undated.
    """

    OPEN = "OPEN", "Kontrollimata"
    ACKNOWLEDGED = "ACKNOWLEDGED", "Kontrollitud"
    DISMISSED = "DISMISSED", "Suletud ilma kontrollita"
    SUPERSEDED = "SUPERSEDED", "Asendatud järgmise meeldetuletusega"
    OBSOLETE = "OBSOLETE", "Aegunud"


#: The answers a person gives. Either one ends every stage of that date.
RESOLVED_STATUSES: frozenset[str] = frozenset(
    {ReminderStatus.ACKNOWLEDGED.value, ReminderStatus.DISMISSED.value}
)


class ObsoleteReason(models.TextChoices):
    """Why the system retired an open reminder. History, not a refusal."""

    DATE_CHANGED = "DATE_CHANGED", "Jõustumise kuupäev muutus"
    NOT_ACTIVE = "NOT_ACTIVE", "Jõustumine tühistati või asendati"
    REMOVED = "REMOVED", "Jõustumine eemaldati teemalt"
    NOT_EXACT = "NOT_EXACT", "Jõustumisel ei ole enam täpset kuupäeva"
