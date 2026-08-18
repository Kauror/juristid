"""Code-managed procedural reference data.

There is no configurable workflow engine and no arbitrary admin create/delete
flow: stages are reference data reviewed with the lawyers and seeded through a
management command (master specification 11.2, 10).
"""

from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.db import models

from app.core.models import BaseModel
from app.workflow.enums import Disposition, Track


class StageVocabulary(BaseModel):
    """One `Hetkeseis` value: where the external process stands."""

    key = models.SlugField(max_length=64, unique=True, verbose_name="võti")
    label_et = models.CharField(max_length=200, verbose_name="nimetus")
    help_text = models.TextField(blank=True, verbose_name="selgitus")
    is_active = models.BooleanField(default=True, verbose_name="aktiivne")
    sort_order = models.PositiveSmallIntegerField(default=100, verbose_name="järjekord")
    applicable_tracks = ArrayField(
        models.CharField(max_length=32, choices=Track.choices),
        default=list,
        blank=True,
        verbose_name="kehtib menetlusliikidele",
        help_text="Tühi loend tähendab, et etapp kehtib kõikidele menetlusliikidele.",
    )
    is_provisional = models.BooleanField(
        default=False,
        verbose_name="esialgne",
        help_text=("Märgitud seni, kuni osakonnajuht ja juristid on etapisõnastiku üle vaadanud."),
    )

    class Meta:
        verbose_name = "menetlusetapp"
        verbose_name_plural = "menetlusetapid"
        ordering = ["sort_order", "label_et"]

    def __str__(self) -> str:
        return self.label_et

    def applies_to(self, track: str) -> bool:
        return not self.applicable_tracks or track in self.applicable_tracks


class LegacyStatusMapping(BaseModel):
    """How one verbatim historical `Hetkeseis` label is interpreted.

    Some legacy labels are not procedural stages at all: the workbook value
    `rohkem pole tegevusi plaanis` describes closure, not where the external
    process stands. Keeping the raw label and its interpretation in separate
    columns means the import never rewrites the source (specification 11.2).
    """

    raw_label = models.CharField(
        max_length=200,
        unique=True,
        verbose_name="algne väärtus",
        help_text="Täpselt nii, nagu see töövihikus esineb.",
    )
    stage = models.ForeignKey(
        StageVocabulary,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="legacy_labels",
        verbose_name="vastav etapp",
    )
    disposition = models.CharField(
        max_length=32,
        choices=Disposition.choices,
        blank=True,
        default="",
        verbose_name="vastav lõpetamise põhjus",
    )
    source_era = models.CharField(
        max_length=32,
        blank=True,
        verbose_name="allika periood",
        help_text="Näiteks 2023-2024 või 2025, kui tähendus on aastati erinev.",
    )
    reviewed_by = models.CharField(max_length=200, blank=True, verbose_name="üle vaadanud")
    notes = models.TextField(blank=True, verbose_name="märkused")

    class Meta:
        verbose_name = "ajaloolise seisundi vaste"
        verbose_name_plural = "ajalooliste seisundite vasted"
        ordering = ["raw_label"]
        constraints = [
            # A label maps to a stage or to a closure reason, never to both.
            models.CheckConstraint(
                condition=~models.Q(stage__isnull=False) | models.Q(disposition=""),
                name="workflow_legacy_status_single_interpretation",
            ),
        ]

    def __str__(self) -> str:
        return self.raw_label
