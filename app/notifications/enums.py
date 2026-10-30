"""Vocabulary of the commencement-review reminder (docs/adr/0188)."""

from __future__ import annotations

from django.db import models


class ReminderStage(models.TextChoices):
    """Which of the two prompts for one commencement date this row is.

    Two, and fixed. The owner's brief asked for seven calendar days ahead and, if
    the check is still outstanding, the day itself (brief C3).

    Since owner decision D1 (2026-10-30) only ``ON_DATE`` is generated: the
    advance reminder became a personal, configurable `DeadlineNotification`
    (docs/adr/0198). ``SEVEN_DAYS`` stays because rows written before carry it.
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


# ---------------------------------------------------------------------------
# Personal deadline notifications (docs/adr/0198)
# ---------------------------------------------------------------------------


class NotificationCategory(models.TextChoices):
    """What a personal deadline notification is about. The registry is
    `app.notifications.categories`; this is only its stored vocabulary."""

    OPINION_DEADLINE = "OPINION_DEADLINE", "Arvamuse tähtaeg"
    FEEDBACK_DEADLINE = "FEEDBACK_DEADLINE", "Kaasamise tagasiside tähtaeg"
    ACTION_DEADLINE = "ACTION_DEADLINE", "Tegevuse tähtaeg"
    COMMENCEMENT_CHECK = "COMMENCEMENT_CHECK", "Jõustumise kontroll"
    IMPORTANT_DATE = "IMPORTANT_DATE", "Oluline tähtaeg"
    REVIEW_DATE = "REVIEW_DATE", "Ülevaatamise kuupäev"


class NotificationSource(models.TextChoices):
    """Which table ``source_id`` names. One per category, stored so a row
    can be read without the registry."""

    MATTER = "MATTER", "Teema"
    ENGAGEMENT = "ENGAGEMENT", "Kaasamine"
    NEXT_ACTION = "NEXT_ACTION", "Tegevus"
    EFFECTIVE_DATE = "EFFECTIVE_DATE", "Jõustumine"
    IMPORTANT_DATE = "IMPORTANT_DATE", "Oluline tähtaeg"


class NotificationPurpose(models.TextChoices):
    """Which delivery of a source date this row is.

    ``ADVANCE`` — *N* days before (``offset_days`` = *N* > 0);
    ``ON_DAY`` — the day itself (``offset_days`` = 0);
    ``ESCALATION`` — the morning after a missed deadline, to the holders of
    `department.view_management` (``offset_days`` = 0). A purpose rather than a
    magic negative offset, so the unique key says what it means.
    """

    ADVANCE = "ADVANCE", "Ette"
    ON_DAY = "ON_DAY", "Tähtpäeval"
    ESCALATION = "ESCALATION", "Tähtaeg ületatud"


class NotificationKind(models.TextChoices):
    """Whether the row asks for something or only tells.

    ``ACTION`` rows can be marked read but not hidden: they leave when their
    source is done. ``INFO`` rows can also be hidden («Peida»)."""

    ACTION = "ACTION", "Vajab tegevust"
    INFO = "INFO", "Teadmiseks"


class RetiredReason(models.TextChoices):
    """Why the system took a delivered row out of every list. History, kept.

    Disabling a category or pausing is **not** here: those hide rows while they
    last and retire nothing, so turning a category back on shows what is still
    true (docs/adr/0198 §5).
    """

    DISCHARGED = "DISCHARGED", "Tehtud või lõpetatud"
    DATE_CHANGED = "DATE_CHANGED", "Kuupäev muutus"
    REASSIGNED = "REASSIGNED", "Vastutaja muutus"
    INELIGIBLE = "INELIGIBLE", "Ei vasta enam tingimustele"
    SUPERSEDED = "SUPERSEDED", "Asendatud hilisema teavitusega"
