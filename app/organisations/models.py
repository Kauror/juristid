"""Institutions, member companies and associations.

Reorganisations are recorded as predecessor/successor links and aliases.
Nothing here merges two institutions because their names look similar
(master specification 11.2, 19.7).
"""

from __future__ import annotations

from typing import Any

from django.db import models

from app.core.models import BaseModel
from app.core.text import normalize_for_matching


class OrganisationType(models.TextChoices):
    MINISTRY = "MINISTRY", "Ministeerium"
    AUTHORITY = "AUTHORITY", "Amet või inspektsioon"
    PARLIAMENT = "PARLIAMENT", "Riigikogu"
    GOVERNMENT = "GOVERNMENT", "Valitsus"
    EU_INSTITUTION = "EU_INSTITUTION", "ELi institutsioon"
    COMPANY = "COMPANY", "Ettevõte"
    ASSOCIATION = "ASSOCIATION", "Liit või ühendus"
    CHAMBER = "CHAMBER", "Koda"
    OTHER = "OTHER", "Muu"


class AliasType(models.TextChoices):
    HISTORICAL_NAME = "HISTORICAL_NAME", "Ajalooline nimi"
    ABBREVIATION = "ABBREVIATION", "Lühend"
    SYNONYM = "SYNONYM", "Sünonüüm"
    SOURCE_SPELLING = "SOURCE_SPELLING", "Allika kirjapilt"


class Organisation(BaseModel):
    name = models.CharField(max_length=300, verbose_name="nimi")
    normalized_name = models.CharField(max_length=300, editable=False, db_index=True)
    registry_code = models.CharField(
        max_length=50, blank=True, db_index=True, verbose_name="registrikood"
    )
    organisation_type = models.CharField(
        max_length=32,
        choices=OrganisationType.choices,
        default=OrganisationType.OTHER,
        verbose_name="tüüp",
    )
    valid_from = models.DateField(null=True, blank=True, verbose_name="kehtiv alates")
    valid_to = models.DateField(null=True, blank=True, verbose_name="kehtiv kuni")
    predecessor = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="successors",
        verbose_name="eelkäija",
    )
    source_reference = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="allika viide",
        help_text="Liikmeregistri või muu välise allika identifikaator, kui see on teada.",
    )
    notes = models.TextField(blank=True, verbose_name="märkused")

    class Meta:
        verbose_name = "organisatsioon"
        verbose_name_plural = "organisatsioonid"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["registry_code"],
                condition=~models.Q(registry_code=""),
                name="organisations_unique_registry_code",
            ),
            models.CheckConstraint(
                condition=models.Q(valid_to__isnull=True)
                | models.Q(valid_from__isnull=True)
                | models.Q(valid_to__gte=models.F("valid_from")),
                name="organisations_valid_period_ordered",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.normalized_name = normalize_for_matching(self.name)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = sorted({*kwargs["update_fields"], "normalized_name"})
        super().save(*args, **kwargs)


class OrganisationAlias(BaseModel):
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name="aliases",
        verbose_name="organisatsioon",
    )
    alias = models.CharField(max_length=300, verbose_name="nimekuju")
    normalized_alias = models.CharField(max_length=300, editable=False, db_index=True)
    alias_type = models.CharField(
        max_length=32,
        choices=AliasType.choices,
        default=AliasType.SYNONYM,
        verbose_name="tüüp",
    )

    class Meta:
        verbose_name = "organisatsiooni nimekuju"
        verbose_name_plural = "organisatsiooni nimekujud"
        ordering = ["alias"]
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "normalized_alias"],
                name="organisations_unique_alias_per_organisation",
            ),
        ]

    def __str__(self) -> str:
        return self.alias

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.normalized_alias = normalize_for_matching(self.alias)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = sorted({*kwargs["update_fields"], "normalized_alias"})
        super().save(*args, **kwargs)
