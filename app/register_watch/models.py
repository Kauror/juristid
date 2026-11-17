"""What the register watch looks at, what it found, and each run (ADR 0216).

A `WatchTarget` is one place a Teema's proceeding can be read by a program: a
Riigikogu bill, or a title search in a ministry register. A `WatchFinding` is one
document or step found there, waiting for a lawyer. Nothing here is a fact about
the Teema until a lawyer adds it, and then the fact is the `Menetluse areng` the
addition wrote (`development`), not this row.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from app.core.models import BaseModel


class WatchSource(models.TextChoices):
    RIIGIKOGU = "RIIGIKOGU", "Riigikogu"
    HTM_REGISTER = "HTM_REGISTER", "Haridus- ja Teadusministeeriumi dokumendiregister"
    KLIM_REGISTER = "KLIM_REGISTER", "Kliimaministeeriumi dokumendiregister"


class TargetOrigin(models.TextChoices):
    #: The Teema's own title names it («… eelnõu (973 SE)»).
    TITLE = "TITLE", "Pealkirjast"


class FindingStatus(models.TextChoices):
    PENDING = "PENDING", "Ootab"
    ADDED = "ADDED", "Lisatud"
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

    class Meta:
        verbose_name = "registrite kontroll"
        verbose_name_plural = "registrite kontrollid"
        ordering = ["-started_at"]
