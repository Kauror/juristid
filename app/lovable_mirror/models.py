"""The mirror tables. The only tables this app writes, and nothing canonical reads them.

A Lovable row stored here is **evidence of what the alpha said at one moment**,
never a fact about a Matter. No foreign key points from here to `Matter`,
`Submission`, `NextAction`, a document or a user: the reconciliation compares
by deterministic key at read time, over the reader-visible population, and
keeps nothing it decided (docs/adr/0157 §4).

All three tables are immutable once written. A database trigger refuses
``UPDATE``; ``DELETE`` stays possible, because removing a whole imported
snapshot is the retention path for real data that should no longer be held
(docs/lovable-mirror.md §"Retention"). There is no partial edit: a corrected
export is a new snapshot.
"""

from __future__ import annotations

from typing import Any

from django.db import models

from app.core.errors import ImmutableRecordError
from app.core.ids import uuid7
from app.lovable_mirror.catalogue import MappingClass

SOURCE_SYSTEM = "LOVABLE_ALPHA"


class DataClass(models.TextChoices):
    """What the bundle says it carries. REAL is refused unless REAL_DATA_ALLOWED."""

    REAL = "REAL", "Päris andmed"
    SYNTHETIC = "SYNTHETIC", "Väljamõeldud andmed"


class ImmutableRow(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRecordError(f"{type(self).__name__} rows are immutable.")
        super().save(*args, **kwargs)


class SourceSnapshot(ImmutableRow):
    """One operator-provided export of the Lovable database, imported once."""

    source_system = models.CharField(max_length=32, default=SOURCE_SYSTEM)
    manifest_sha256 = models.CharField(
        max_length=64,
        unique=True,
        verbose_name="manifesti SHA-256",
        help_text="Manifest kinnitab iga tabelifaili räsi, seega kinnitab see kogu paki.",
    )
    format_version = models.PositiveSmallIntegerField(verbose_name="vormingu versioon")
    data_class = models.CharField(max_length=16, choices=DataClass.choices)
    source_schema_version = models.CharField(max_length=200, verbose_name="allika skeem")
    catalogue_version = models.CharField(max_length=20, verbose_name="kataloogi versioon")
    reader = models.CharField(
        max_length=32,
        verbose_name="lugeja",
        help_text="Mis tõi andmed: operaatori eksport või (tulevikus) otselugeja.",
    )
    source_exported_at = models.DateTimeField(verbose_name="allikas eksporditud")
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name="imporditud")
    exported_by = models.CharField(max_length=200, blank=True)
    export_method = models.CharField(max_length=400, blank=True)
    bundle_label = models.CharField(
        max_length=200,
        blank=True,
        help_text="Paki kausta nimi. Täisrada ei salvestata.",
    )
    row_counts = models.JSONField(default=dict, verbose_name="ridu tabeliti")
    uncatalogued_fields = models.JSONField(
        default=dict, verbose_name="kataloogimata väljad tabeliti"
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-source_exported_at", "-imported_at", "-id"]
        verbose_name = "Lovable'i hetktõmmis"
        verbose_name_plural = "Lovable'i hetktõmmised"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(manifest_sha256__regex=r"^[0-9a-f]{64}$"),
                name="lovable_mirror_snapshot_sha256_hex",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_system} {self.source_exported_at:%Y-%m-%d %H:%M}"


class SourceRecord(ImmutableRow):
    """One row of one Lovable table in one snapshot."""

    snapshot = models.ForeignKey(SourceSnapshot, on_delete=models.CASCADE, related_name="records")
    table = models.CharField(max_length=64)
    source_id = models.CharField(max_length=200, verbose_name="allika ID")
    stable_key = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="püsiv võti",
        help_text="Kataloogi järgi püsiv võti (topics: nr). Tühi, kui allikas seda ei andnud.",
    )
    row_sha256 = models.CharField(max_length=64)

    class Meta:
        ordering = ["table", "stable_key", "source_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "table", "source_id"],
                name="lovable_mirror_record_unique_per_snapshot",
            ),
        ]
        indexes = [
            models.Index(fields=["snapshot", "table", "stable_key"], name="lovmir_record_key"),
        ]

    def __str__(self) -> str:
        return f"{self.table}:{self.stable_key or self.source_id}"


class SourceFieldValue(ImmutableRow):
    """One column of one source row, exactly as the export carried it."""

    record = models.ForeignKey(SourceRecord, on_delete=models.CASCADE, related_name="values")
    field = models.CharField(max_length=128)
    value = models.JSONField(null=True, blank=True)
    mapping_class = models.CharField(max_length=32, choices=MappingClass.choices)

    class Meta:
        ordering = ["field"]
        constraints = [
            models.UniqueConstraint(
                fields=["record", "field"], name="lovable_mirror_value_unique_per_record"
            ),
        ]

    def __str__(self) -> str:
        return self.field
