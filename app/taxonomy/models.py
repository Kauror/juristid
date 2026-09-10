"""Three separate classification dimensions.

``PolicyArea`` is the small, stable reporting classification that replaced the
exclusive OneNote folders. ``Tag`` is a governed thematic concept used for
search and reuse. Neither may encode owner, stage, institution, date,
confidentiality, legal instrument or workflow, and sector is deliberately not
squeezed into ``Tag`` (master specification 3.13, 14.7).

``LegalInstrumentType`` is the third, and it is the one the sentence above was
holding a place for: *legal instrument* was named as something the other two may
not encode, because it is a real dimension of its own and nobody had yet said
where it lived. It does now — ``Õigusakt``, a governed reference vocabulary read
from the historical register and reviewed once (docs/adr/0070).

The three answer different questions and none is derivable from another:
Valdkond is *which area of law*, Silt is *what specifically about it*, and
Õigusakt is *what kind of instrument*. Menetlusliik — *what kind of procedure* —
is a fourth, and it is a ``Matter`` column rather than a vocabulary because its
seven values are the product's own and never grew from a source.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models

from app.core.models import BaseModel
from app.core.text import normalize_for_matching


class PolicyArea(BaseModel):
    key = models.SlugField(max_length=64, unique=True, verbose_name="võti")
    name_et = models.CharField(max_length=200, verbose_name="nimi")
    description = models.TextField(blank=True, verbose_name="kirjeldus")
    is_active = models.BooleanField(default=True, verbose_name="aktiivne")
    sort_order = models.PositiveSmallIntegerField(default=100, verbose_name="järjekord")

    class Meta:
        verbose_name = "valdkond"
        verbose_name_plural = "valdkonnad"
        ordering = ["sort_order", "name_et"]

    def __str__(self) -> str:
        return self.name_et


class LegalInstrumentType(BaseModel):
    """One kind of legal or source instrument a Matter can concern — ``Õigusakt``.

    Reference data with a reviewed vocabulary behind it
    (``app/taxonomy/legal_instruments.py``), not a taxonomy people add to. A
    spelling nobody has reviewed does not become a row: ``Muu`` plus
    ``Matter.legal_instrument_other`` is where an unlisted instrument goes, and
    that text is one Matter's own and creates nothing here.

    Deliberately *not* ``Tag``: a tag is free subject vocabulary with aliases
    and merging, this is a closed reviewed list. Deliberately not
    ``Matter.track``: that is the procedure, this is the instrument, and the
    same file answers both (docs/adr/0070).
    """

    key = models.SlugField(max_length=64, unique=True, verbose_name="võti")
    label_et = models.CharField(max_length=200, verbose_name="nimi")
    description = models.TextField(blank=True, verbose_name="kirjeldus")
    is_active = models.BooleanField(default=True, verbose_name="aktiivne")
    sort_order = models.PositiveSmallIntegerField(default=100, verbose_name="järjekord")

    class Meta:
        verbose_name = "õigusakti liik"
        verbose_name_plural = "õigusakti liigid"
        ordering = ["sort_order", "label_et"]

    def __str__(self) -> str:
        return self.label_et


class Tag(BaseModel):
    key = models.SlugField(max_length=64, unique=True, verbose_name="võti")
    name_et = models.CharField(max_length=200, verbose_name="nimi")
    definition = models.TextField(
        blank=True,
        verbose_name="tähendus",
        help_text="Millal seda silti kasutada ja millal mitte.",
    )
    is_active = models.BooleanField(default=True, verbose_name="aktiivne")
    deprecated_at = models.DateTimeField(null=True, blank=True, verbose_name="aegunud")
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="merged_from",
        verbose_name="liidetud sildiga",
        help_text="Aegunud silt jääb otsitavaks kanoonilise sildi kaudu.",
    )
    owned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_tags",
        verbose_name="haldaja",
    )

    class Meta:
        verbose_name = "silt"
        verbose_name_plural = "sildid"
        ordering = ["name_et"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(merged_into=models.F("id")),
                name="taxonomy_tag_not_merged_into_itself",
            ),
            # A tag that has been merged away must not stay active.
            models.CheckConstraint(
                condition=models.Q(merged_into__isnull=True) | models.Q(is_active=False),
                name="taxonomy_tag_merged_is_inactive",
            ),
        ]

    def __str__(self) -> str:
        return self.name_et

    def canonical(self) -> Tag:
        """Follow merges to the tag that should carry assignments today."""
        seen: set[Any] = {self.pk}
        tag: Tag = self
        while tag.merged_into_id is not None and tag.merged_into_id not in seen:
            seen.add(tag.merged_into_id)
            successor = tag.merged_into
            if successor is None:
                break
            tag = successor
        return tag


class TagAlias(BaseModel):
    tag = models.ForeignKey(
        Tag, on_delete=models.CASCADE, related_name="aliases", verbose_name="silt"
    )
    alias = models.CharField(max_length=200, verbose_name="nimekuju")
    normalized_alias = models.CharField(max_length=200, editable=False, db_index=True)

    class Meta:
        verbose_name = "sildi nimekuju"
        verbose_name_plural = "sildi nimekujud"
        ordering = ["alias"]
        constraints = [
            models.UniqueConstraint(
                fields=["tag", "normalized_alias"],
                name="taxonomy_unique_alias_per_tag",
            ),
        ]

    def __str__(self) -> str:
        return self.alias

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.normalized_alias = normalize_for_matching(self.alias)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = sorted({*kwargs["update_fields"], "normalized_alias"})
        super().save(*args, **kwargs)
