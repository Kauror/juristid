"""Building and refreshing the search projection.

Two operations, both idempotent and both safe to run again at any time:
:func:`refresh_matters` for a known set, and :func:`rebuild_all` for everything.
Neither reads the existing index to decide what to write — a projection that
depends on its own previous state cannot be trusted to converge.

A full rebuild is **atomic**. "Derived data" makes an index cheap to *recreate*;
it does not make a half-built one safe to *serve*, because a partial index is
indistinguishable from a complete one to everybody reading it. See
:func:`rebuild_all`.

The vectors are computed in the database rather than in Python. That keeps the
lexeme rules wherever PostgreSQL's Estonian configuration says they are, so a
rebuild after a dictionary change actually produces different vectors instead of
faithfully reproducing what the application thought last year.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass

from django.contrib.postgres.search import SearchVector
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from app.core.text import normalize_for_matching
from app.documents.models import DocumentVersion
from app.matters.models import Entry, Matter
from app.search.models import INDEX_VERSION, SearchDocument, SearchSourceKind
from app.submissions.models import Submission

#: Rows per statement batch during a full rebuild. This bounds **memory**, not
#: loss: a full rebuild is one transaction, so a failure in the last batch rolls
#: back the first one too, by design (see :func:`rebuild_all`).
BATCH_SIZE = 500

#: Set while a bulk operation is running. The signal handlers check it and do
#: nothing, so an import of 2,455 rows does not perform 2,455 separate index
#: refreshes; the caller refreshes once at the end instead.
_suspended = False


def indexing_is_suspended() -> bool:
    return _suspended


@contextlib.contextmanager
def suspend_indexing() -> Iterator[None]:
    """Stop per-row reindexing for a bulk operation.

    The caller takes on the obligation to refresh what it touched. Used by the
    importer, which knows exactly which Matters it wrote and can do it in one
    pass.
    """
    global _suspended
    previous = _suspended
    _suspended = True
    try:
        yield
    finally:
        _suspended = previous


@dataclass(frozen=True)
class RebuildResult:
    documents: int
    matters: int
    seconds: float
    index_version: str
    entries: int = 0
    submissions: int = 0
    fragments: int = 0


def indexable_matters() -> QuerySet[Matter]:
    """Every Matter, with the related rows the projection needs.

    Unscoped on purpose: the index covers everything, and *reading* it is what
    authorization filters. An index that only held rows the indexing user could
    see would silently differ between operators.
    """
    return Matter.objects.select_related(
        "source_organisation", "addressee_organisation"
    ).prefetch_related(
        "source_organisation__aliases",
        "addressee_organisation__aliases",
        "policy_areas",
        "tags",
        "tags__aliases",
    )


def _identifiers_for(matter: Matter) -> str:
    """Reference tokens, in every shape a lawyer might type them.

    ``2026_184`` is what the register says. People also type ``2026 184`` and
    ``2026-184``, and the identifier column exists so those all reach the same
    exact-match tier rather than falling through to fuzzy matching.
    """
    if matter.reference_year is None or matter.reference_number is None:
        return ""
    year, number = matter.reference_year, matter.reference_number
    return " ".join([f"{year}_{number}", f"{year}-{number}", f"{year} {number}"])


def _alias_text_for(matter: Matter) -> str:
    """Organisation, policy-area and tag names plus their recorded aliases.

    Aliases are what make a search for ``MKM`` find matters filed under the
    ministry's full name, and what keeps a merged tag findable through the tag
    that replaced it (master specification 14.7). They are reviewed data, so
    using them here is not fuzzy matching — it is using somebody's decision.
    """
    parts: list[str] = []
    for organisation in (matter.source_organisation, matter.addressee_organisation):
        if organisation is None:
            continue
        parts.append(organisation.name)
        parts.extend(alias.alias for alias in organisation.aliases.all())
    for area in matter.policy_areas.all():
        parts.append(area.name_et)
    for tag in matter.tags.all():
        parts.append(tag.name_et)
        parts.extend(alias.alias for alias in tag.aliases.all())

    # The diacritic-free form as well, so `oigusloome` finds `õigusloome`
    # without unaccent having to be in the query path.
    normalized = [normalize_for_matching(part) for part in parts]
    return " ".join(dict.fromkeys([*parts, *normalized]))


def _title_text_for(matter: Matter) -> str:
    titles = [matter.title, *(matter.alternate_titles or [])]
    return " ".join(title for title in titles if title)


def _document_values(matter: Matter, now: object) -> dict[str, object]:
    return {
        "matter": matter,
        "source_kind": SearchSourceKind.MATTER,
        "source_object_id": matter.pk,
        "title": _title_text_for(matter),
        "identifiers": _identifiers_for(matter),
        "alias_text": _alias_text_for(matter),
        # The Matter's own authored summaries. Entry, Submission and document
        # text live in their own rows, so a result can say which of them
        # matched — folding them in here would make every hit read "the matter
        # matched" and lose the locator entirely (docs/adr/0014).
        "body_text": " ".join(
            part for part in (matter.position_summary, matter.rationale_summary) if part
        ),
        "source_locator": "",
        "index_version": INDEX_VERSION,
        "indexed_at": now,
    }


@transaction.atomic
def refresh_matters(matters: QuerySet[Matter]) -> int:
    """Rewrite the projection for the given Matters. Idempotent."""
    now = timezone.now()
    rows = list(matters)
    if not rows:
        return 0

    matter_ids = [matter.pk for matter in rows]
    # Delete-then-insert rather than update: it is one shape of statement
    # regardless of whether a Matter was indexed before, so a half-built index
    # and a fully built one converge to the same result.
    SearchDocument.objects.filter(
        matter_id__in=matter_ids, source_kind=SearchSourceKind.MATTER
    ).delete()
    SearchDocument.objects.bulk_create(
        [SearchDocument(**_document_values(matter, now)) for matter in rows]
    )
    # Scoped to the rows just written. Without the kind filter this would also
    # recompute every entry, submission and document fragment belonging to these
    # matters — correct output, and at fragment scale an enormous amount of
    # pointless work on every Matter save.
    _recompute_vectors(
        SearchDocument.objects.filter(matter_id__in=matter_ids, source_kind=SearchSourceKind.MATTER)
    )
    return len(rows)


def _recompute_vectors(documents: QuerySet[SearchDocument]) -> None:
    """Let PostgreSQL build the vectors, with the weights ranking depends on.

    A over B over C over D: a term in the title outranks the same term in an
    identifier, which outranks an organisation alias, which outranks body text.
    ``ts_rank`` reads these weights, so this is where most of the relevance
    ordering is actually decided.
    """
    documents.update(
        search_estonian=(
            SearchVector("title", weight="A", config="estonian")
            + SearchVector("identifiers", weight="B", config="estonian")
            + SearchVector("alias_text", weight="C", config="estonian")
            + SearchVector("body_text", weight="D", config="estonian")
        ),
        search_simple=(
            SearchVector("title", weight="A", config="simple")
            + SearchVector("identifiers", weight="B", config="simple")
            + SearchVector("alias_text", weight="C", config="simple")
            + SearchVector("body_text", weight="D", config="simple")
        ),
        search_title=SearchVector("title", weight="A", config="estonian"),
    )


def refresh_matter(matter: Matter) -> int:
    return refresh_matters(indexable_matters().filter(pk=matter.pk))


@transaction.atomic
def refresh_entry(entry: Entry) -> int:
    from app.search.child_indexing import indexable_entries, refresh_entries

    count = refresh_entries(indexable_entries().filter(pk=entry.pk))
    _recompute_vectors(
        SearchDocument.objects.filter(source_kind=SearchSourceKind.ENTRY, source_object_id=entry.pk)
    )
    return count


@transaction.atomic
def refresh_submission(submission: Submission) -> int:
    from app.search.child_indexing import indexable_submissions, refresh_submissions

    count = refresh_submissions(indexable_submissions().filter(pk=submission.pk))
    _recompute_vectors(
        SearchDocument.objects.filter(
            source_kind=SearchSourceKind.SUBMISSION, source_object_id=submission.pk
        )
    )
    return count


@transaction.atomic
def refresh_document_version(version: DocumentVersion) -> int:
    """Reproject one version's extracted content.

    Called from inside the extraction publish transaction, so a committed
    derivative and a findable document are the same event. A derivative that
    committed without its search rows would be content that exists and cannot be
    found — the silent half of every search complaint.
    """
    from app.search.child_indexing import refresh_version_fragments

    count = refresh_version_fragments(version)
    _recompute_vectors(
        SearchDocument.objects.filter(
            source_kind=SearchSourceKind.DOCUMENT_FRAGMENT, document_version=version
        )
    )
    return count


def rebuild_all(*, batch_size: int = BATCH_SIZE, clear: bool = True) -> RebuildResult:
    """Rebuild the whole projection from canonical records. All or nothing.

    **The empty-and-refill runs inside one transaction**, and that is the whole
    point of this function rather than an incidental detail.

    An earlier version committed each batch as it went, on the reasoning that a
    projection is derived data so a partial rebuild is merely stale. That
    reasoning is wrong, and wrong in the worst direction. A stale index returns
    slightly old answers; a *partially rebuilt* index returns confident, silent,
    incomplete ones. Nothing is marked, nothing errors, and the page that says
    "vasteid ei leitud" looks exactly the same whether the matter does not exist
    or whether the rebuild died before reaching it. A lawyer concluding Koda
    never worked on something, from a search that quietly lost half its corpus,
    is the failure this system exists to prevent.

    So readers keep seeing the previous complete index for the whole run —
    PostgreSQL's MVCC gives other connections the last committed state — and the
    new one becomes visible only when the entire rebuild has succeeded. If
    anything raises partway, the old index is still there, still complete, still
    searchable.

    Batching survives inside the transaction because it bounds memory, not
    because it bounds loss. At the current scale (2,455 matters) the whole thing
    is a few seconds and a handful of statements. If the corpus ever grew to
    where one transaction is genuinely too long, the answer is a generation
    column or a shadow table swapped in at the end — *not* a return to committing
    partial state, which trades a visible pause for an invisible gap.

    With ``clear`` the table is emptied first, so documents for Matters that no
    longer exist, and any orphans left by an earlier interrupted attempt, cannot
    survive a rebuild.
    """
    started = timezone.now()

    with transaction.atomic():
        if clear:
            SearchDocument.objects.all().delete()

        identifiers = list(indexable_matters().order_by("pk").values_list("pk", flat=True))
        total = 0
        for offset in range(0, len(identifiers), batch_size):
            chunk = identifiers[offset : offset + batch_size]
            total += refresh_matters(indexable_matters().filter(pk__in=chunk))

        entries, submissions, fragments = _rebuild_children(batch_size=batch_size)
        documents = SearchDocument.objects.count()

    return RebuildResult(
        documents=documents,
        matters=total,
        entries=entries,
        submissions=submissions,
        fragments=fragments,
        seconds=(timezone.now() - started).total_seconds(),
        index_version=INDEX_VERSION,
    )


def _rebuild_children(*, batch_size: int) -> tuple[int, int, int]:
    """Entries, submissions and document fragments, inside the caller's
    transaction.

    Deliberately not a separate atomic block. The whole rebuild is one
    transaction so readers keep the previous *complete* index throughout, and a
    child pass that committed on its own would give them a corpus whose matters
    are new and whose documents are half old — worse than either alone, because
    nothing about it looks wrong (docs/adr/0013).
    """
    from app.search.child_indexing import (
        indexable_entries,
        indexable_fragments,
        indexable_submissions,
        project_fragments,
        refresh_entries,
        refresh_submissions,
    )

    entries = _in_batches(indexable_entries(), refresh_entries, batch_size)
    submissions = _in_batches(indexable_submissions(), refresh_submissions, batch_size)
    fragments = _in_batches(indexable_fragments(), project_fragments, batch_size)

    # One statement for every child row, rather than one per batch. The vectors
    # are computed in the database either way; doing it once means PostgreSQL
    # plans it once.
    _recompute_vectors(SearchDocument.objects.exclude(source_kind=SearchSourceKind.MATTER))
    return entries, submissions, fragments


def _in_batches(queryset: QuerySet, refresh: object, batch_size: int) -> int:
    identifiers = list(queryset.order_by("pk").values_list("pk", flat=True))
    total = 0
    for offset in range(0, len(identifiers), batch_size):
        chunk = identifiers[offset : offset + batch_size]
        total += refresh(queryset.filter(pk__in=chunk))  # type: ignore[operator]
    return total
