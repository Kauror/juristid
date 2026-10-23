"""One reminder row per commencement date per stage (docs/adr/0188).

What the row stores, and what it deliberately does not
------------------------------------------------------
It names its source — one `MatterEffectiveDate` — and **the date it was
generated for** (``source_date``). That copy is the point rather than a
duplication: when somebody moves the commencement from 1.1.2027 to 1.7.2027 the
row still says it was about 1.1.2027, so it can be retired as ``OBSOLETE`` and
two fresh rows generated for the new date, while the history of what was asked
and answered about the old one survives.

It stores **no recipient**. Who should check is the Matter's *current*
Vastutaja, read when the list is drawn, so a reassigned Matter's open reminders
follow the file to its new owner without a write, and a Matter with nobody
responsible lands in the department view instead of on a guessed desk (brief C4).

It stores no title, description or matter name: those are canonical on the
commencement and the Matter and are read live, like `MatterAssignmentNotice`.

Idempotence is the database's
-----------------------------
``(effective_date, source_date, stage)`` is unique among the rows that are not
``OBSOLETE``. The generator checks and inserts under one advisory lock and the
constraint backs it, so a restart, a second worker or a second run of the same
day cannot produce a second live row for the same prompt (brief C5). Retired
rows stay beside a regenerated one, so a date edited away and back keeps the
history of both.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from app.core.models import BaseModel
from app.notifications.enums import ObsoleteReason, ReminderStage, ReminderStatus


class CommencementReminderQuerySet(models.QuerySet["CommencementReminder"]):
    def open(self) -> CommencementReminderQuerySet:
        return self.filter(status=ReminderStatus.OPEN)


class CommencementReminder(BaseModel):
    """«Kontrolli jõustumist» — one prompt to verify one recorded commencement date."""

    #: CASCADE: a commencement row is never deleted as ordinary workflow (it is
    #: cancelled or removed, both of which keep the row), so the only way this
    #: fires is the permanent deletion of the whole Teema, which must take its
    #: reminders with it (app/matters/deletion.py).
    effective_date = models.ForeignKey(
        "intelligence.MatterEffectiveDate",
        on_delete=models.CASCADE,
        related_name="commencement_reminders",
        verbose_name="jõustumine",
    )
    source_date = models.DateField(verbose_name="kontrollitav kuupäev")
    stage = models.CharField(
        max_length=16, choices=ReminderStage.choices, verbose_name="meeldetuletus"
    )
    #: The Tallinn calendar day this prompt became due: ``source_date − 7`` or
    #: ``source_date``. Stored rather than derived so a list can order by it and
    #: a reader can see that a late generator still produced the right prompt.
    due_on = models.DateField(verbose_name="tähtpäev")
    status = models.CharField(
        max_length=16,
        choices=ReminderStatus.choices,
        default=ReminderStatus.OPEN,
        db_index=True,
        verbose_name="olek",
    )
    #: Hidden from the bell and the lists until this Tallinn date. Still OPEN.
    deferred_until = models.DateField(null=True, blank=True, verbose_name="edasi lükatud kuni")

    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name="vastatud")
    #: Who pressed the button — workflow association. Under the shared gate that
    #: is the persona somebody *chose*; the `ChangeEvent` written in the same
    #: transaction carries how they arrived (docs/adr/0180), and that row, not
    #: this column, is the audit claim.
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="vastas",
    )
    resolution_note = models.TextField(blank=True, verbose_name="põhjus")
    obsolete_reason = models.CharField(
        max_length=16, choices=ObsoleteReason.choices, blank=True, verbose_name="aegumise põhjus"
    )

    objects = CommencementReminderQuerySet.as_manager()

    class Meta:
        verbose_name = "jõustumise meeldetuletus"
        verbose_name_plural = "jõustumise meeldetuletused"
        ordering = ["due_on", "source_date", "id"]
        constraints = [
            # Partial: an OBSOLETE row is history about a date the commencement
            # left, and a date restored to it is asked about afresh.
            models.UniqueConstraint(
                fields=["effective_date", "source_date", "stage"],
                condition=~models.Q(status=ReminderStatus.OBSOLETE),
                name="notifications_reminder_once_per_stage",
            ),
            models.CheckConstraint(
                condition=models.Q(stage__in=ReminderStage.values),
                name="notifications_reminder_stage_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=ReminderStatus.values),
                name="notifications_reminder_status_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(obsolete_reason="")
                | models.Q(obsolete_reason__in=ObsoleteReason.values),
                name="notifications_reminder_obsolete_reason_vocabulary",
            ),
            # A person's answer always says when it was given.
            models.CheckConstraint(
                condition=~models.Q(
                    status__in=[ReminderStatus.ACKNOWLEDGED, ReminderStatus.DISMISSED]
                )
                | models.Q(resolved_at__isnull=False),
                name="notifications_reminder_answer_is_dated",
            ),
            # The two stages sit exactly where the brief puts them.
            models.CheckConstraint(
                condition=(
                    models.Q(stage=ReminderStage.ON_DATE, due_on=models.F("source_date"))
                    | (
                        models.Q(stage=ReminderStage.SEVEN_DAYS)
                        & models.Q(due_on__lt=models.F("source_date"))
                    )
                ),
                name="notifications_reminder_due_on_matches_stage",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "due_on"], name="notif_reminder_status_due"),
        ]

    def __str__(self) -> str:
        return f"{self.get_stage_display()} {self.source_date.isoformat()}"
