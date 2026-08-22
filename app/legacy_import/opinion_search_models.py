"""A search projection for the archive, kept apart from the Matter-bound one.

`SearchDocument.matter` is not nullable, and this module exists because it must
stay that way.

Every authorization decision in the global search rests on one property: each
projection row can name the Matter that authorizes it, and the live visibility
predicate joins that Matter before anything is counted or ranked. An unmatched
archive letter cannot answer that question — there is no Matter, and inventing
one to hold the unfiled would put a row in the register that nobody opened.
Making the column nullable to accommodate it would remove the invariant from
every *other* row at the same time, so that the safety of the whole projection
would rest on remembering to filter for a null.

So the archive gets its own table. It is derived, rebuildable, and never
consulted for a business decision. It answers a different question from the
global search — *what historical evidence do we hold, including what is not
filed* rather than *what does our filed legal record contain* — and keeping the
two apart is why neither has to compromise.

Rebuildable means exactly that: everything here can be reconstructed from the
canonical binary, its occurrences, its derived metadata and its derived text.
Losing this table costs a rebuild and no evidence.
"""

from __future__ import annotations

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models

from app.core.models import BaseModel
from app.legacy_import.opinion_binary import OpinionArchiveBinary

#: Bumped when the projection's shape or its vector recipe changes, so a rebuild
#: can find rows written by an older version rather than guessing from a
#: timestamp.
ARCHIVE_INDEX_VERSION = "1"


class OpinionArchiveSearchDocument(BaseModel):
    """One searchable row per archive binary. Derived, never canonical.

    Per *binary* rather than per occurrence: two paths holding the same letter
    are one thing to find. The occurrence paths are searchable text on this row,
    so a reader looking for a path still finds it, and the detail page is where
    the several occurrences are enumerated.
    """

    binary = models.OneToOneField(
        OpinionArchiveBinary,
        on_delete=models.CASCADE,
        related_name="search_document",
        verbose_name="bait",
    )

    # -- what the row can be found by -------------------------------------
    #
    # Copied rather than joined, unlike the Matter-bound projection. There the
    # copies would go stale against a live restriction and the join is the
    # safety property; here there is no authorization to keep live, and the
    # source rows are immutable catalogue provenance.
    title = models.TextField(blank=True, verbose_name="pealkiri")
    recipient = models.TextField(blank=True, verbose_name="saaja")
    #: Every path this binary was found at, one per line.
    occurrence_paths = models.TextField(blank=True, verbose_name="teed arhiivis")
    #: References, external ids and the SHA itself: things somebody pastes in.
    identifiers = models.TextField(blank=True, verbose_name="tunnused")
    body_text = models.TextField(blank=True, verbose_name="sisu")

    # -- what the row can be filtered by ----------------------------------
    document_date = models.DateField(null=True, blank=True, verbose_name="dokumendi kuupäev")
    source_year = models.PositiveIntegerField(null=True, blank=True, verbose_name="aasta")
    #: Denormalised so a filter does not need three joins, and rebuilt with the
    #: row. Never read as the truth about a candidate's state — the queue is.
    match_class = models.CharField(max_length=32, blank=True, verbose_name="sidumise klass")
    review_state = models.CharField(max_length=20, blank=True, verbose_name="ülevaatuse olek")
    has_body_text = models.BooleanField(default=False, verbose_name="sisu olemas")
    is_linked = models.BooleanField(default=False, verbose_name="teemaga seotud")
    has_submission = models.BooleanField(default=False, verbose_name="kanooniline arvamus")
    occurrence_count = models.PositiveIntegerField(default=0, verbose_name="esinemisi")

    search_estonian = SearchVectorField(null=True, editable=False)
    search_simple = SearchVectorField(null=True, editable=False)
    index_version = models.CharField(
        max_length=16,
        default=ARCHIVE_INDEX_VERSION,
        editable=False,
        verbose_name="indeksi versioon",
    )
    indexed_at = models.DateTimeField(null=True, blank=True, verbose_name="indekseeritud")

    class Meta:
        verbose_name = "arvamuste arhiivi otsingurida"
        verbose_name_plural = "arvamuste arhiivi otsinguread"
        # Deterministic and total. `document_date` alone repeats — the corpus
        # sends several letters on one day — and a paginated list whose second
        # page depends on the planner's mood is a list that loses rows.
        ordering = ["-document_date", "-created_at", "pk"]
        indexes = [
            GinIndex(fields=["search_estonian"], name="opinion_arch_est_gin"),
            GinIndex(fields=["search_simple"], name="opinion_arch_simple_gin"),
            models.Index(fields=["source_year"], name="opinion_arch_year"),
            models.Index(fields=["review_state"], name="opinion_arch_state"),
            models.Index(fields=["-document_date", "-created_at"], name="opinion_arch_order"),
        ]

    def __str__(self) -> str:
        return self.title or str(self.binary_id)
