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
from django.contrib.postgres.fields import ArrayField
from django.db import models

from app.core.models import BaseModel
from app.notifications.enums import (
    NotificationCategory,
    NotificationKind,
    NotificationPurpose,
    NotificationSource,
    ObsoleteReason,
    ReminderStage,
    ReminderStatus,
    RetiredReason,
)


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


# ---------------------------------------------------------------------------
# Personal deadline notifications (docs/adr/0198)
# ---------------------------------------------------------------------------

#: The advance intervals a person may choose, in days before the date.
ALLOWED_OFFSETS: tuple[int, ...] = (14, 7, 3, 2, 1)


class DeadlineNotification(BaseModel):
    """One delivery of one dated obligation to one account (docs/adr/0198).

    **A delivery, not a state.** The row says «on this day the system told
    this person that this source is due on that date». Whether the source is
    *still* due is never stored here: every list re-reads the source through
    its canonical selector, as the recipient, and leaves out a row whose source
    no longer matches — before the worker has retired it.

    **No business text is copied.** The Teema title, the step text, the
    owner's name are read live, like `MatterAssignmentNotice` (ADR 0051).

    **Once per interval, ever.** ``(recipient, category, source_type,
    source_id, source_date, purpose, offset_days)`` is unique and *not*
    partial: a dismissed, read or retired row still holds its slot, so a rerun,
    a restart, a reassignment back or a date restored to an old value never
    delivers the same interval twice. A changed date is a new ``source_date``
    and so a new schedule.

    ``read_at`` and ``dismissed_at`` are the recipient's presentation state:
    neither is a business fact and neither writes a `ChangeEvent`.
    ``retired_at`` is the system's: the source was done, moved, handed on or
    superseded by a later interval. Rows are never deleted, except with their
    Teema (``matter`` CASCADE, app/matters/deletion.py) or their account.
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="deadline_notifications",
        verbose_name="saaja",
    )
    #: The Teema the source hangs off — for scoping, the title and the cascade.
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="deadline_notifications",
        verbose_name="teema",
    )
    category = models.CharField(
        max_length=24, choices=NotificationCategory.choices, verbose_name="liik"
    )
    source_type = models.CharField(
        max_length=16, choices=NotificationSource.choices, verbose_name="allika tüüp"
    )
    #: The source row's primary key. Not a foreign key: five tables can be the
    #: source, and the row must survive its source being completed.
    source_id = models.UUIDField(verbose_name="allikas")
    #: The date this delivery was generated for. A source moved to another day
    #: no longer matches it, which is how the row knows it is stale.
    source_date = models.DateField(verbose_name="kuupäev")
    purpose = models.CharField(
        max_length=16, choices=NotificationPurpose.choices, verbose_name="eesmärk"
    )
    #: Days before ``source_date`` for ``ADVANCE``; 0 for the others.
    offset_days = models.PositiveSmallIntegerField(default=0, verbose_name="päevi ette")
    kind = models.CharField(max_length=8, choices=NotificationKind.choices, verbose_name="tüüp")
    #: The Tallinn day this delivery became due (``source_date − offset`` for an
    #: advance row, ``source_date`` on the day, ``source_date + 1`` for an
    #: escalation). Stored so a late generator still shows the right interval.
    due_on = models.DateField(verbose_name="tähtpäev")

    read_at = models.DateTimeField(null=True, blank=True, verbose_name="loetud")
    dismissed_at = models.DateTimeField(null=True, blank=True, verbose_name="peidetud")
    retired_at = models.DateTimeField(null=True, blank=True, verbose_name="aegunud")
    retired_reason = models.CharField(
        max_length=16, choices=RetiredReason.choices, blank=True, verbose_name="aegumise põhjus"
    )

    class Meta:
        verbose_name = "tähtaja teavitus"
        verbose_name_plural = "tähtaegade teavitused"
        ordering = ["source_date", "due_on", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "recipient",
                    "category",
                    "source_type",
                    "source_id",
                    "source_date",
                    "purpose",
                    "offset_days",
                ],
                name="notifications_deadline_once_per_interval",
            ),
            models.CheckConstraint(
                condition=models.Q(category__in=NotificationCategory.values),
                name="notifications_deadline_category_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(source_type__in=NotificationSource.values),
                name="notifications_deadline_source_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(purpose__in=NotificationPurpose.values),
                name="notifications_deadline_purpose_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=NotificationKind.values),
                name="notifications_deadline_kind_vocabulary",
            ),
            models.CheckConstraint(
                condition=models.Q(retired_reason="")
                | models.Q(retired_reason__in=RetiredReason.values),
                name="notifications_deadline_retired_reason_vocabulary",
            ),
            # An advance row names its interval; the others carry none.
            models.CheckConstraint(
                condition=(
                    models.Q(purpose=NotificationPurpose.ADVANCE, offset_days__in=ALLOWED_OFFSETS)
                    | (~models.Q(purpose=NotificationPurpose.ADVANCE) & models.Q(offset_days=0))
                ),
                name="notifications_deadline_offset_matches_purpose",
            ),
            # Only an INFO row can be hidden; an ACTION row leaves when it is done.
            models.CheckConstraint(
                condition=models.Q(dismissed_at__isnull=True)
                | models.Q(kind=NotificationKind.INFO),
                name="notifications_deadline_only_info_is_hidden",
            ),
            # A retirement always says why, and a reason always has a moment.
            models.CheckConstraint(
                condition=(
                    models.Q(retired_at__isnull=True, retired_reason="")
                    | (models.Q(retired_at__isnull=False) & ~models.Q(retired_reason=""))
                ),
                name="notifications_deadline_retirement_has_reason",
            ),
        ]
        indexes = [
            models.Index(
                fields=["recipient", "retired_at", "dismissed_at", "due_on"],
                name="notif_deadline_inbox",
            ),
            models.Index(fields=["source_type", "source_id"], name="notif_deadline_source"),
        ]

    def __str__(self) -> str:
        return f"{self.category} {self.purpose} {self.offset_days} {self.source_date.isoformat()}"


class NotificationPreference(BaseModel):
    """One person's explicit choice about one notification category.

    Like `accounts.DisclosurePreference` (docs/adr/0194): presentation state of
    the signed-in account, never read by authorization, and only an explicit
    choice is stored — no row means the registry default
    (`app.notifications.categories`), so a default changed later reaches
    everybody who never chose otherwise. «Taasta vaikimisi» deletes the rows.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
        verbose_name="kasutaja",
    )
    #: A registered preference key (`categories.PREFERENCE_KEYS`): a category,
    #: or the management escalation's own key.
    category = models.CharField(max_length=24, verbose_name="liik")
    enabled = models.BooleanField(verbose_name="sees")
    offsets = ArrayField(
        models.PositiveSmallIntegerField(), default=list, blank=True, verbose_name="päevi ette"
    )
    on_day = models.BooleanField(default=False, verbose_name="tähtpäeval")

    class Meta:
        verbose_name = "teavituse eelistus"
        verbose_name_plural = "teavituste eelistused"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "category"], name="notifications_preference_user_category"
            ),
            models.CheckConstraint(
                condition=models.Q(offsets__contained_by=list(ALLOWED_OFFSETS)),
                name="notifications_preference_allowed_offsets",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.category}={'sees' if self.enabled else 'väljas'}"


class NotificationSettings(BaseModel):
    """One person's global notification switches. Absent means the defaults.

    ``email_requested`` is a stored wish and nothing else: no address is read
    and nothing is sent until personal sign-in and a delivery setting both
    exist (`app.notifications.email.deliver_email`).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_settings",
        verbose_name="kasutaja",
    )
    paused = models.BooleanField(default=False, verbose_name="peatatud")
    email_requested = models.BooleanField(default=False, verbose_name="soovib e-posti")

    class Meta:
        verbose_name = "teavituste seaded"
        verbose_name_plural = "teavituste seaded"

    def __str__(self) -> str:
        return "peatatud" if self.paused else "sees"
