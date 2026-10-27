"""The nightly Lovable synchronisation's records (docs/adr/0192).

Four tables, kept apart from the pilot's immutable provenance:

* `LovableSyncControl` — one row: whether the job may run, and whether it may
  write. Final cutover sets ``cut_over`` and nothing turns it back;
* `LovableSyncRun` — one row per attempt, with counts, never business text;
* `LovableSyncState` — per synchronised Teema, the **last applied** and the
  **last observed** source values, kept apart so a held change stays pending
  until somebody resolves it rather than vanishing the night after it appeared;
* `LovableSyncDecision` — what each run decided about each field and why. The
  values it compares are business content and are shown only through
  `Matter.objects.visible_to`.
"""

from __future__ import annotations

from typing import Any

from django.db import models

from app.core.errors import ImmutableRecordError
from app.core.models import BaseModel


class SyncMode(models.TextChoices):
    OFF = "OFF", "Peatatud"
    COMPARE = "COMPARE", "Ainult võrdlus"
    APPLY = "APPLY", "Võrdlus ja ohutud uuendused"


class SyncTrigger(models.TextChoices):
    SCHEDULED = "SCHEDULED", "Öine käivitus"
    MANUAL = "MANUAL", "Käsitsi käivitus"
    CHECK = "CHECK", "Kontrolli muudatusi"


class SyncRunStatus(models.TextChoices):
    RUNNING = "RUNNING", "Käib"
    SUCCEEDED = "SUCCEEDED", "Õnnestus"
    REVIEW_ONLY = "REVIEW_ONLY", "Peatatud ülevaatuseks"
    FAILED = "FAILED", "Ebaõnnestus"


class SyncOutcome(models.TextChoices):
    UNCHANGED = "UNCHANGED", "Muutusteta"
    LOCAL_TEST_CHANGE = "LOCAL_TEST_CHANGE", "Kohalik testmuudatus"
    ELIGIBLE = "ELIGIBLE", "Ohutu uuendus (rakendamata)"
    APPLIED = "APPLIED", "Uuendatud"
    ALREADY_CONVERGED = "ALREADY_CONVERGED", "Juba kooskõlas"
    CONFLICT = "CONFLICT", "Vastuolu"
    REVIEW_REQUIRED = "REVIEW_REQUIRED", "Vajab ülevaatamist"
    SOURCE_MISSING = "SOURCE_MISSING", "Allikas puudub"
    IDENTIFIER_CHANGED = "IDENTIFIER_CHANGED", "Tunnus muutunud"
    FAILED = "FAILED", "Viga"


class LovableSyncControl(models.Model):
    """The one switch row (``pk=1``). Changed only by `lovable_sync control`."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    mode = models.CharField(max_length=16, choices=SyncMode.choices, default=SyncMode.OFF)
    #: Final cutover: Juristid is the operational source of truth and the
    #: synchronisation never runs again. Set once; `save` refuses to clear it.
    cut_over = models.BooleanField(default=False)
    cut_over_at = models.DateTimeField(null=True, blank=True)
    changed_at = models.DateTimeField(null=True, blank=True)
    changed_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = "Lovable sünkroonimise lüliti"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="lovable_sync_control_singleton"),
            models.CheckConstraint(
                condition=models.Q(cut_over=False) | models.Q(mode="OFF"),
                name="lovable_sync_cut_over_is_off",
            ),
        ]

    def __str__(self) -> str:
        return "cut over" if self.cut_over else self.mode

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            stored = (
                type(self).objects.filter(pk=self.pk).values_list("cut_over", flat=True).first()
            )
            if stored and not self.cut_over:
                raise ImmutableRecordError("Lõplikku üleminekut ei saa tagasi võtta.")
        super().save(*args, **kwargs)


class LovableSyncRun(BaseModel):
    """One attempt. ``run_key`` is the Tallinn calendar date for the nightly run,
    so a second scheduled attempt on the same date refuses instead of doubling."""

    run_key = models.CharField(max_length=40, unique=True)
    trigger = models.CharField(max_length=16, choices=SyncTrigger.choices)
    mode = models.CharField(max_length=16, choices=SyncMode.choices)
    status = models.CharField(
        max_length=16, choices=SyncRunStatus.choices, default=SyncRunStatus.RUNNING
    )
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    snapshot_taken_at = models.DateTimeField(null=True, blank=True)
    snapshot_sha256 = models.CharField(max_length=64, blank=True)
    mapping_version = models.CharField(max_length=40)
    backup_set = models.CharField(max_length=40, blank=True)
    #: Aggregate counts only (`lovable_sync.RunCounts`); never a title or a name.
    counts = models.JSONField(default=dict, blank=True)
    verified = models.BooleanField(default=False)
    #: A short code and a sentence with no business content.
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = "Lovable sünkroonimise käivitus"
        ordering = ["-started_at"]


class LovableSyncSnapshot(BaseModel):
    """The cohort's source rows as one run read them (docs/adr/0192 §4).

    Confidential and kept only as long as the owner's approval allows
    (`SNAPSHOT_RETENTION_DAYS`); `Kontrolli muudatusi` compares against the
    newest one without reaching Lovable."""

    run = models.OneToOneField(LovableSyncRun, on_delete=models.CASCADE, related_name="snapshot")
    taken_at = models.DateTimeField()
    sha256 = models.CharField(max_length=64)
    document = models.JSONField()

    class Meta:
        verbose_name = "Lovable lähteseis"
        ordering = ["-taken_at"]


class LovableSyncState(BaseModel):
    """Per synchronised Teema: A (last applied) and the last observation."""

    matter = models.OneToOneField(
        "matters.Matter", on_delete=models.CASCADE, related_name="lovable_sync_state"
    )
    source_id = models.UUIDField(unique=True)
    reference = models.CharField(max_length=20)
    #: Field key → ``{"source": canonical source value, "target": what Juristid
    #: was given for it}``. Advanced field by field, only when a field is applied,
    #: converges, or a person resolves it.
    applied = models.JSONField(default=dict)
    applied_version = models.IntegerField(null=True, blank=True)
    applied_snapshot_sha256 = models.CharField(max_length=64, blank=True)
    #: Canonical source fields as last observed; never a baseline.
    observed = models.JSONField(default=dict, blank=True)
    observed_version = models.IntegerField(null=True, blank=True)
    observed_run = models.ForeignKey(
        LovableSyncRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "Lovable sünkroonimise seis"
        ordering = ["reference"]


class LovableSyncDecision(BaseModel):
    """What one run decided about one field of one Teema."""

    run = models.ForeignKey(LovableSyncRun, on_delete=models.CASCADE, related_name="decisions")
    state = models.ForeignKey(LovableSyncState, on_delete=models.CASCADE, related_name="decisions")
    field = models.CharField(max_length=40)
    outcome = models.CharField(max_length=24, choices=SyncOutcome.choices)
    reason = models.CharField(max_length=60, blank=True)
    baseline = models.JSONField(null=True, blank=True)
    current = models.JSONField(null=True, blank=True)
    source = models.JSONField(null=True, blank=True)
    #: Set only on an applied write: the same source change applied twice is
    #: refused by the database, whatever the code above it does.
    idempotency_key = models.CharField(max_length=64, null=True, blank=True, unique=True)

    class Meta:
        verbose_name = "Lovable sünkroonimise otsus"
        ordering = ["state__reference", "field"]
