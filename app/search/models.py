"""The rebuildable search projection.

``SearchDocument`` is derived data and nothing else. It can be deleted in full
and rebuilt from canonical records, and the product is expected to survive that
with no loss beyond the time the rebuild takes. Nothing in the domain reads from
it, nothing writes business state through it, and no decision depends on it
(master specification 11.3).

**It stores no visibility.** The specification's sketch of this table mentions an
effective-visibility field, and Stage 0 already learned why that is the one field
it must not have: a stored authorization value goes stale the moment a Matter is
restricted, and a projection is refreshed *after* the fact by definition. Every
search query therefore joins the live Matter and applies the same
``app.core.authorization`` predicate as the rest of the system. The index makes
results fast; it never makes them visible (docs/adr/0005, docs/adr/0013).
"""

from __future__ import annotations

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models

from app.core.models import BaseModel

#: Bumped when the indexed text or the vector configuration changes, so a
#: partially rebuilt index is visible rather than merely stale.
INDEX_VERSION = "2A.2"


class SearchSourceKind(models.TextChoices):
    """What a document projects.

    Stage 2A indexes Matter-level content only. ``ENTRY`` and ``SUBMISSION``
    are named here because the kinds are part of the contract a later stage
    fills in, not because anything writes them yet (docs/adr/0013).
    """

    MATTER = "MATTER", "Teema"
    ENTRY = "ENTRY", "Sissekanne"
    SUBMISSION = "SUBMISSION", "Arvamus"


class SearchDocument(BaseModel):
    matter = models.ForeignKey(
        "matters.Matter",
        on_delete=models.CASCADE,
        related_name="search_documents",
        verbose_name="teema",
    )
    source_kind = models.CharField(
        max_length=32,
        choices=SearchSourceKind.choices,
        default=SearchSourceKind.MATTER,
        verbose_name="allika liik",
    )
    source_object_id = models.UUIDField(
        null=True,
        blank=True,
        verbose_name="allika objekt",
        help_text="Sama mis teema, kui dokument projitseerib teemat ennast.",
    )

    title = models.TextField(blank=True, verbose_name="pealkiri")
    identifiers = models.TextField(
        blank=True,
        verbose_name="identifikaatorid",
        help_text="Normaliseeritud viited, mille järgi saab täpselt otsida.",
    )
    alias_text = models.TextField(
        blank=True,
        verbose_name="nimekujud",
        help_text="Asutuste, valdkondade ja siltide nimed ning nimekujud.",
    )
    body_text = models.TextField(
        blank=True,
        verbose_name="sisutekst",
        help_text="Tühi 2A etapis; sissekannete ja arvamuste tekst tuleb 2B-s.",
    )
    source_locator = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="allika asukoht",
        help_text="Kust tulemus avaneb, kui allikas ei ole teema ise.",
    )

    search_estonian = SearchVectorField(null=True, editable=False)
    search_simple = SearchVectorField(null=True, editable=False)
    # Title and alternate titles only. The combined vector above also carries
    # identifiers and aliases, so a phrase query against it matches an
    # organisation name and calls it a title hit. Ranking tiers are only
    # meaningful if each one tests what it claims to test.
    search_title = SearchVectorField(null=True, editable=False)

    index_version = models.CharField(max_length=16, default=INDEX_VERSION, editable=False)
    indexed_at = models.DateTimeField(db_index=True, verbose_name="indekseeritud")

    class Meta:
        verbose_name = "otsingudokument"
        verbose_name_plural = "otsingudokumendid"
        ordering = ["matter", "source_kind"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_kind", "source_object_id"],
                condition=models.Q(source_object_id__isnull=False),
                name="search_one_document_per_source_object",
            ),
        ]
        indexes = [
            GinIndex(fields=["search_estonian"], name="search_estonian_gin"),
            GinIndex(fields=["search_simple"], name="search_simple_gin"),
            GinIndex(fields=["search_title"], name="search_title_gin"),
            # Trigram indexes are for short strings only. The body text column
            # is deliberately absent: trigram-indexing extracted document text
            # is how a PostgreSQL search installation becomes unmaintainable
            # (master specification 14.1).
            GinIndex(
                fields=["title"],
                opclasses=["gin_trgm_ops"],
                name="search_title_trigram",
            ),
            GinIndex(
                fields=["identifiers"],
                opclasses=["gin_trgm_ops"],
                name="search_identifiers_trigram",
            ),
            models.Index(fields=["matter", "source_kind"], name="search_matter_kind"),
        ]

    def __str__(self) -> str:
        return f"{self.source_kind}:{self.title[:60]}"
