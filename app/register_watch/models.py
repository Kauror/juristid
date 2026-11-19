"""What the register watch looks at, what it found, and each run (ADR 0217).

A `WatchTarget` is one place a Teema's proceeding can be read by a program: a
Riigikogu bill, or a title search in a ministry register. A `WatchFinding` is one
document or step found there, waiting for a lawyer. Nothing here is a fact about
the Teema until a lawyer adds it, and then the fact is the `Menetluse areng` the
addition wrote (`development`), not this row.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.db import models
from django.utils import timezone

from app.core.models import BaseModel


class WatchSource(models.TextChoices):
    RIIGIKOGU = "RIIGIKOGU", "Riigikogu"
    HTM_REGISTER = "HTM_REGISTER", "Haridus- ja Teadusministeeriumi dokumendiregister"
    KLIM_REGISTER = "KLIM_REGISTER", "Kliimaministeeriumi dokumendiregister"
    ADR_JDM = "ADR_JDM", "Justiits- ja Digiministeeriumi dokumendiregister"
    ADR_MKM = "ADR_MKM", "Majandus- ja Kommunikatsiooniministeeriumi dokumendiregister"
    ADR_RAM = "ADR_RAM", "Rahandusministeeriumi dokumendiregister"
    ADR_SOM = "ADR_SOM", "Sotsiaalministeeriumi dokumendiregister"
    ADR_KUM = "ADR_KUM", "Kultuuriministeeriumi dokumendiregister"
    ADR_KAM = "ADR_KAM", "Kaitseministeeriumi dokumendiregister"


class TargetOrigin(models.TextChoices):
    #: The Teema's own title names it («… eelnõu (973 SE)»).
    TITLE = "TITLE", "Pealkirjast"
    #: Words taken from the Teema's title, searched in its ministry's register.
    PHRASE = "PHRASE", "Pealkirja sõnadest"
    #: Words a lawyer set for the Teema.
    PERSON = "PERSON", "Juristi otsingusõnad"


class FindingStatus(models.TextChoices):
    PENDING = "PENDING", "Ootab"
    ADDED = "ADDED", "Lisatud"
    #: Kept only as the Teema's `Menetluse link`, nothing on the timeline.
    LINKED = "LINKED", "Lisatud lingina"
    DISMISSED = "DISMISSED", "Jäetud vahele"
    #: Already in the register long before the first check; never offered.
    BASELINE = "BASELINE", "Varasem"


class Relevance(models.TextChoices):
    #: Not judged: the model is off, or the item belongs to the Teema by construction.
    UNJUDGED = "", "Hindamata"
    RELEVANT = "RELEVANT", "Puutub teemasse"
    UNSURE = "UNSURE", "Ebaselge"
    UNRELATED = "UNRELATED", "Ei puutu teemasse"


class WatchTarget(BaseModel):
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="watch_targets",
        verbose_name="teema",
    )
    source = models.CharField(max_length=32, choices=WatchSource.choices, verbose_name="allikas")
    #: What the source is asked for: the bill's uuid, or the title search text.
    identifier = models.CharField(max_length=200, verbose_name="tunnus")
    #: How a person reads it: «973 SE».
    label = models.CharField(max_length=120, blank=True, verbose_name="nimetus")
    origin = models.CharField(max_length=16, choices=TargetOrigin.choices, verbose_name="päritolu")
    active = models.BooleanField(default=True, verbose_name="jälgitakse")
    #: The public page of the bill, when the source has one.
    url = models.URLField(max_length=1000, blank=True, verbose_name="link")
    checked_at = models.DateTimeField(null=True, blank=True, verbose_name="viimati kontrollitud")
    last_error = models.CharField(max_length=500, blank=True, verbose_name="viimane viga")

    class Meta:
        verbose_name = "jälgitav allikas"
        verbose_name_plural = "jälgitavad allikad"
        constraints = [
            models.UniqueConstraint(
                fields=["matter", "source", "identifier"], name="register_watch_target_once"
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_source_display()}: {self.label or self.identifier}"


class WatchFinding(BaseModel):
    target = models.ForeignKey(
        WatchTarget, on_delete=models.CASCADE, related_name="findings", verbose_name="allikas"
    )
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="register_findings",
        verbose_name="teema",
    )
    #: The source's own id for the item, stable across checks.
    external_id = models.CharField(max_length=200, verbose_name="välistunnus")
    title = models.CharField(max_length=500, verbose_name="pealkiri")
    occurred_on = models.DateField(null=True, blank=True, db_index=True, verbose_name="kuupäev")
    url = models.URLField(max_length=1000, blank=True, verbose_name="link")
    #: Register number and sender, or the reading, as the source states them.
    detail = models.CharField(max_length=300, blank=True, verbose_name="täpsustus")
    status = models.CharField(
        max_length=16,
        choices=FindingStatus.choices,
        default=FindingStatus.PENDING,
        db_index=True,
        verbose_name="olek",
    )
    relevance = models.CharField(
        max_length=16, choices=Relevance.choices, blank=True, default="", verbose_name="hinnang"
    )
    #: The model's one line: what the document is.
    ai_note = models.CharField(max_length=500, blank=True, verbose_name="selgitus")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="otsustas",
    )
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name="otsustatud")
    development = models.ForeignKey(
        "matters.MatterProceduralDevelopment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="märge",
    )

    class Meta:
        verbose_name = "registrist leitud dokument"
        verbose_name_plural = "registritest leitud dokumendid"
        constraints = [
            models.UniqueConstraint(
                fields=["target", "external_id"], name="register_watch_finding_once"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=[s.value for s in FindingStatus]),
                name="register_watch_finding_status",
            ),
            models.CheckConstraint(
                condition=models.Q(relevance__in=[r.value for r in Relevance]),
                name="register_watch_finding_relevance",
            ),
        ]
        indexes = [models.Index(fields=["matter", "status"], name="register_watch_by_matter")]

    def __str__(self) -> str:
        return self.title


#: A run that has not finished after this long was stopped from outside. The
#: nightly run takes about 16 minutes (18.11.2026, 335 sources).
ABANDONED_AFTER = dt.timedelta(hours=3)


class WatchRun(BaseModel):
    started_at = models.DateTimeField(verbose_name="algas")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="lõppes")
    targets_checked = models.PositiveIntegerField(default=0)
    findings_new = models.PositiveIntegerField(default=0)
    findings_offered = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    ai_calls = models.PositiveIntegerField(default=0)
    ai_input_tokens = models.PositiveIntegerField(default=0)
    ai_output_tokens = models.PositiveIntegerField(default=0)
    #: The model the run asked, as the API named it in its answer; empty when
    #: the AI step was off for the run. Defaults in the database too, so the
    #: release before this one still records a run (rollout and rollback).
    ai_model = models.CharField(max_length=80, blank=True, default="", db_default="")
    ai_judged = models.PositiveIntegerField(default=0, db_default=0)
    ai_failures = models.PositiveIntegerField(default=0, db_default=0)

    class Meta:
        verbose_name = "registrite kontroll"
        verbose_name_plural = "registrite kontrollid"
        ordering = ["-started_at"]

    @property
    def aborted(self) -> bool:
        """The run stopped before its end: it said so, or it never finished.

        A run that raised records an error marked ``katkes`` and is closed; one
        whose process was killed (a release recreating the container, the host
        going down) never sets ``finished_at`` and is taken as stopped once it
        is older than any run takes (`ABANDONED_AFTER`).
        """
        if any(isinstance(error, dict) and error.get("katkes") for error in self.errors):
            return True
        return self.finished_at is None and self.started_at < timezone.now() - ABANDONED_AFTER

    @property
    def running(self) -> bool:
        return self.finished_at is None and not self.aborted


class RegisterNotice(BaseModel):
    """«Registris uus dokument» in the bell: one per Teema for its owner (ADR 0217 §6).

    A run that offers new documents on a Teema tells the Teema's owner once; a
    later run adds to the same unread notice instead of making another. It is
    presentation state of the owner's account, like `PersonalReminder`: read and
    unread by its own person only. It drops out of the inbox when every document
    it points to has been added or skipped (`app.register_watch.notices`).
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="register_notices",
        verbose_name="saaja",
    )
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="register_notices",
        verbose_name="teema",
    )
    documents = models.PositiveIntegerField(default=0, verbose_name="dokumente")
    read_at = models.DateTimeField(null=True, blank=True, verbose_name="loetud")

    class Meta:
        verbose_name = "registriteavitus"
        verbose_name_plural = "registriteavitused"
        indexes = [models.Index(fields=["recipient", "read_at"], name="register_notice_inbox")]
