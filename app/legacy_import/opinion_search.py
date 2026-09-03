"""Building and querying the archive's own search projection.

Two things live here: the rebuild that fills `OpinionArchiveSearchDocument` from
canonical rows, and the query that reads it.

Neither touches the global `SearchDocument`. A letter that has been filed onto a
Matter is searchable there through its `DocumentVersion`, by the ordinary
machinery, and duplicating it into the global projection from here would produce
two results for one document with two different authorization stories.

**Authorization is a corpus boundary, not the Matter one.** An unmatched
archive letter has no Matter to inherit visibility from, so there is nothing to
inherit, and the question has to be asked about the corpus instead. It is
`may_read_archive` and nothing else — the two lawyer roles plus the
administrator, since these are the department's own outgoing letters rather
than a migration artefact (docs/adr/0056). It is deliberately *not* the
reconciliation queue's boundary, which is narrower and stays the
administrator's: reading a letter and deciding which Matter it belongs to are
different acts.

Applied in one place, before anything is counted, so a refused reader cannot
learn the size of the corpus from a total.

Nothing about a reader is stored in the projection — `visible_archive` asks at
query time — so widening who may read needs no rebuild and does not move
`ARCHIVE_INDEX_VERSION`.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from django.contrib.postgres.search import SearchQuery, SearchVector
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q, QuerySet
from django.utils import timezone

from app.legacy_import.opinion_archive import (
    OpinionArchiveItem,
    OpinionArchiveMetadata,
    OpinionMatchCandidate,
    OpinionSubmissionImport,
)
from app.legacy_import.opinion_binary import (
    OpinionArchiveBinary,
    OpinionArchiveMatterLink,
)
from app.legacy_import.opinion_enums import ArchiveTextState, OpinionCandidateState
from app.legacy_import.opinion_search_models import (
    ARCHIVE_INDEX_VERSION,
    OpinionArchiveSearchDocument,
)

#: The same bound the global search enforces. Stated here rather than imported
#: so the archive query cannot be widened by a change made for another reason,
#: and small enough that a pasted paragraph is refused rather than parsed.
MAX_QUERY_CHARACTERS = 500

#: Results per page of the archive browse. The corpus is hundreds of rows, so
#: this is about readability rather than cost.
PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# Rebuilding
# ---------------------------------------------------------------------------


@dataclass
class ArchiveIndexReport:
    binaries: int = 0
    written: int = 0
    unchanged: int = 0
    findings: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        rows = [
            ("baite", self.binaries),
            ("ridu kirjutatud", self.written),
            ("muutmata", self.unchanged),
        ]
        lines = [f"  {label:<32} {value:>12}" for label, value in rows]
        lines.extend(f"  leid: {finding}" for finding in self.findings)
        return "\n".join(lines)


def rebuild_archive_index(*, force: bool = False) -> ArchiveIndexReport:
    """Rewrite the projection from canonical rows.

    Safe to run at any time and safe to interrupt: it only ever writes derived
    rows, and a binary whose row is missing or stale is simply written again.
    Canonical bytes are never touched, which is what makes "rebuild the index"
    an unremarkable operation rather than one that needs a backup first.

    **Every row is recomputed, and only differing rows are written.** The
    obvious cheaper rule — skip anything already at the current index version —
    is wrong in the one case the runbook actually performs: `extract-text`
    followed by `rebuild` would skip every row and leave the newly extracted
    bodies out of the index, while reporting a clean run. The projection also
    moves when a candidate is decided or a Matter linked, neither of which
    touches the binary. Recomputing a few hundred rows is cheap; a rebuild that
    silently does nothing is not.

    `force` therefore only forces the *write*, for the case where the stored
    values are right and the vectors are suspect.
    """
    # No purge step, and none is missing: a projection row cannot outlive its
    # binary. The FK is CASCADE, so removing a binary removes its row in the
    # same statement, and there is no other way for a row to become orphaned.
    report = ArchiveIndexReport()
    binaries = OpinionArchiveBinary.objects.select_related("text", "search_document").order_by("pk")
    report.binaries, report.written = _reindex(binaries, force=force)
    report.unchanged = report.binaries - report.written
    return report


def _reindex(binaries: QuerySet[OpinionArchiveBinary], *, force: bool = False) -> tuple[int, int]:
    """Recompute these binaries and write the rows that differ.

    Returns ``(seen, written)``. Shared by the full rebuild and the targeted
    refresh so there is one definition of "what this row should say" and one of
    "is it already saying it" — two implementations would drift, and the whole
    point of the projection is that it does not.
    """
    seen = written = 0
    for binary in binaries.iterator():
        seen += 1
        values = _row_values(binary)
        existing = getattr(binary, "search_document", None)
        if existing is not None and not force and _matches_row(existing, values):
            continue
        _write_row(binary, values)
        written += 1
    return seen, written


# ---------------------------------------------------------------------------
# Staying fresh: the bounded half of the ADR 0041 contract
# ---------------------------------------------------------------------------

_suspended: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "archive_indexing_suspended", default=False
)


def archive_indexing_is_suspended() -> bool:
    return _suspended.get()


@contextmanager
def suspend_archive_indexing() -> Iterator[None]:
    """Stop per-row archive reindexing for a bulk operation.

    The caller takes on the obligation to refresh what it touched, with
    :func:`refresh_archive_binaries`. The same bargain `app.search` strikes for
    the global projection, and it is a bargain rather than a licence: a bulk
    writer that suspends and then refreshes nothing is the defect this contract
    exists to close.
    """
    token = _suspended.set(True)
    try:
        yield
    finally:
        _suspended.reset(token)


def refresh_archive_binaries(binary_ids: Iterable[Any]) -> int:
    """Rewrite the projection rows for exactly these binaries.

    The bounded refresh a bulk caller owes, and what the signal handlers use for
    a single row. Bounded by the write rather than by the corpus: relinking one
    letter must not cost a rebuild of the other seven hundred, and the row-level
    comparison in :func:`_reindex` means a refresh that finds nothing changed
    writes nothing at all.

    Ignores ``None`` — an archive item need not have a binary yet, and a caller
    collecting ids from a batch should not have to filter them itself.
    """
    wanted = {pk for pk in binary_ids if pk is not None}
    if not wanted:
        return 0
    binaries = (
        OpinionArchiveBinary.objects.select_related("text", "search_document")
        .filter(pk__in=wanted)
        .order_by("pk")
    )
    _, written = _reindex(binaries)
    return written


def refresh_archive_binary(binary_id: Any) -> int:
    """One binary, unless indexing is suspended for a bulk write in progress."""
    if archive_indexing_is_suspended() or binary_id is None:
        return 0
    return refresh_archive_binaries([binary_id])


def _matches_row(row: OpinionArchiveSearchDocument, values: dict[str, Any]) -> bool:
    """Whether the stored row already says exactly this.

    `indexed_at` is excluded: it records when the projection last looked, not
    what it found, and comparing it would make every row differ from itself.
    """
    if row.index_version != ARCHIVE_INDEX_VERSION or row.search_estonian is None:
        return False
    return all(getattr(row, field) == value for field, value in values.items())


def _row_values(binary: OpinionArchiveBinary) -> dict[str, Any]:
    """Everything the projection says about one letter, computed from canon."""
    occurrences = list(
        OpinionArchiveItem.objects.filter(binary=binary).values(
            "archive_relative_path",
            "original_filename",
            "filename_title",
            "filename_recipient",
            "filename_date",
            "sha256",
        )
    )
    metadata = list(
        OpinionArchiveMetadata.objects.filter(item__binary=binary).values(
            "title", "recipient_raw", "recipient_normalized", "document_date", "external_id"
        )
    )
    candidates = list(
        OpinionMatchCandidate.objects.filter(item__binary=binary)
        .exclude(state=OpinionCandidateState.SUPERSEDED)
        .values("match_class", "state", "excel_reference")
    )
    text = getattr(binary, "text", None)

    title = _first(
        [occurrence["filename_title"] for occurrence in occurrences]
        + [row["title"] for row in metadata]
    )
    recipient = _first(
        [occurrence["filename_recipient"] for occurrence in occurrences]
        + [row["recipient_raw"] for row in metadata]
    )
    document_date = _first(
        [occurrence["filename_date"] for occurrence in occurrences]
        + [row["document_date"] for row in metadata]
    )

    identifiers = _unique(
        [binary.sha256]
        + [occurrence["original_filename"] for occurrence in occurrences]
        + [row["external_id"] for row in metadata]
        + [candidate["excel_reference"] for candidate in candidates]
    )
    paths = _unique(occurrence["archive_relative_path"] for occurrence in occurrences)

    linked = OpinionArchiveMatterLink.objects.filter(binary=binary)
    has_submission = OpinionSubmissionImport.objects.filter(item__binary=binary).exists()

    return {
        "title": title or "",
        "recipient": recipient or "",
        "occurrence_paths": "\n".join(paths),
        "identifiers": "\n".join(identifiers),
        "body_text": text.body if text is not None and text.has_body else "",
        "document_date": document_date,
        "source_year": document_date.year if document_date else None,
        "match_class": _first([candidate["match_class"] for candidate in candidates]) or "",
        "review_state": _review_state(candidates),
        "has_body_text": bool(text is not None and text.has_body),
        "is_linked": linked.exists(),
        "has_submission": has_submission,
        "occurrence_count": len(occurrences),
        "index_version": ARCHIVE_INDEX_VERSION,
    }


@transaction.atomic
def _write_row(
    binary: OpinionArchiveBinary, values: dict[str, Any]
) -> OpinionArchiveSearchDocument:
    row, _ = OpinionArchiveSearchDocument.objects.update_or_create(
        binary=binary, defaults={**values, "indexed_at": timezone.now()}
    )
    # Vectors in the database rather than in Python, so the text search
    # configuration is the one PostgreSQL will use to answer the query.
    OpinionArchiveSearchDocument.objects.filter(pk=row.pk).update(
        search_estonian=(
            SearchVector("title", weight="A", config="estonian")
            + SearchVector("recipient", weight="B", config="estonian")
            + SearchVector("identifiers", weight="B", config="estonian")
            + SearchVector("body_text", weight="D", config="estonian")
        ),
        search_simple=(
            SearchVector("title", weight="A", config="simple")
            + SearchVector("identifiers", weight="A", config="simple")
            + SearchVector("occurrence_paths", weight="C", config="simple")
            + SearchVector("body_text", weight="D", config="simple")
        ),
    )
    row.refresh_from_db()
    return row


def _first(values: Any) -> Any:
    for value in values:
        if value:
            return value
    return None


def _unique(values: Any) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def _review_state(candidates: Sequence[Mapping[str, Any]]) -> str:
    """The state worth filtering on, when an occurrence has several candidates.

    A decided answer outranks an undecided one: if a person has said something
    about this letter, that is what the queue filter should find it under.
    """
    states = {candidate["state"] for candidate in candidates}
    for state in (
        OpinionCandidateState.APPLIED,
        OpinionCandidateState.LINKED,
        OpinionCandidateState.REJECTED,
        OpinionCandidateState.DUPLICATE,
        OpinionCandidateState.NOT_AN_OPINION,
        OpinionCandidateState.DEFERRED,
        OpinionCandidateState.PENDING,
    ):
        if state in states:
            return str(state)
    return ""


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------


class ArchiveQueryRefused(ValueError):
    """The query was not run, and saying "no results" would be a lie about that."""


@dataclass(frozen=True)
class ArchiveFilters:
    query: str = ""
    year: str = ""
    review_state: str = ""
    linked: str = ""
    body: str = ""

    @property
    def any_applied(self) -> bool:
        return bool(self.year or self.review_state or self.linked or self.body)


def visible_archive(user: Any) -> QuerySet[OpinionArchiveSearchDocument]:
    """The archive rows this reader may see, before anything is counted.

    All or nothing, and deliberately so. The unresolved archive has no Matter to
    inherit a restriction from, so there is no per-row rule to apply — only the
    question `may_read_archive` answers about the corpus. Anyone else gets an
    empty queryset, which also means an empty count: a refused reader must not
    be able to learn how large the corpus is.

    Asked here rather than stored on the row, which is what lets the reader set
    widen without a projection rebuild (docs/adr/0056).
    """
    from app.legacy_import.opinion_access import may_read_archive

    if not may_read_archive(user):
        return OpinionArchiveSearchDocument.objects.none()
    return OpinionArchiveSearchDocument.objects.all()


def search_archive(*, user: Any, filters: ArchiveFilters) -> QuerySet[OpinionArchiveSearchDocument]:
    """Filtered, ordered archive rows for one reader.

    The authorization comes first and the filters narrow what it returned, never
    the other way round: a filter that ran before the boundary would decide how
    many rows exist and only then ask whether this person may see them.
    """
    rows = visible_archive(user)

    term = (filters.query or "").strip()
    if term:
        if len(term) > MAX_QUERY_CHARACTERS:
            raise ArchiveQueryRefused(f"Otsingusõna on pikem kui {MAX_QUERY_CHARACTERS} tähemärki.")
        rows = rows.filter(_matches(term))

    if filters.year:
        if not filters.year.isdigit():
            raise ArchiveQueryRefused("Aasta peab olema arv.")
        rows = rows.filter(source_year=int(filters.year))
    if filters.review_state:
        rows = rows.filter(review_state=filters.review_state)
    if filters.linked == "jah":
        rows = rows.filter(is_linked=True)
    elif filters.linked == "ei":
        rows = rows.filter(is_linked=False)
    if filters.body == "jah":
        rows = rows.filter(has_body_text=True)
    elif filters.body == "ei":
        rows = rows.filter(has_body_text=False)

    return rows.select_related("binary")


def _matches(term: str) -> Q:
    """Full text in both configurations, plus the exact-identifier route.

    The identifier route is an exact containment test rather than a text search
    on purpose: somebody pasting a SHA-256 or an archive path is not searching
    for words, and the Estonian stemmer would make a hash unrecognisable.
    """
    estonian = SearchQuery(term, config="estonian", search_type="websearch")
    simple = SearchQuery(term, config="simple", search_type="websearch")
    return (
        Q(search_estonian=estonian)
        | Q(search_simple=simple)
        | Q(identifiers__icontains=term)
        | Q(occurrence_paths__icontains=term)
    )


def archive_counts(user: Any) -> dict[str, int]:
    """Coverage figures for the browse header, under the same boundary."""
    rows = visible_archive(user)
    return {
        "total": rows.count(),
        "with_body": rows.filter(has_body_text=True).count(),
        "linked": rows.filter(is_linked=True).count(),
        "with_submission": rows.filter(has_submission=True).count(),
    }


def unindexed_binaries() -> QuerySet[OpinionArchiveBinary]:
    """Materialised binaries the projection does not describe yet."""
    current = OpinionArchiveSearchDocument.objects.filter(
        binary=OuterRef("pk"), index_version=ARCHIVE_INDEX_VERSION
    )
    return OpinionArchiveBinary.objects.annotate(indexed=Exists(current)).filter(indexed=False)


def archive_index_findings() -> list[str]:
    """Every way the projection currently disagrees with what is held.

    Aggregates only — a count and a class of problem, never a filename, a title
    or a SHA. This output is meant to be pasted into an issue, and a verify
    command that leaks the corpus into a ticket would be its own finding.
    """
    findings: list[str] = []

    missing = unindexed_binaries().count()
    if missing:
        findings.append(f"{missing} hoitud baiti ei ole otsinguprojektsioonis (või on aegunud)")

    stale_version = OpinionArchiveSearchDocument.objects.exclude(
        index_version=ARCHIVE_INDEX_VERSION
    ).count()
    if stale_version:
        findings.append(f"{stale_version} rida on kirjutatud vana indeksi versiooniga")

    # A row with content but no vector is invisible to every query while
    # looking perfectly healthy in a list — the one drift that a coverage
    # figure alone cannot show.
    unvectorised = OpinionArchiveSearchDocument.objects.filter(search_estonian__isnull=True).count()
    if unvectorised:
        findings.append(f"{unvectorised} real puudub otsinguvektor")

    # Text was extracted, but the projection still says the row has none. The
    # rebuild is what closes this, and until it runs those letters are held,
    # readable and unfindable by their contents.
    from app.legacy_import.opinion_binary import OpinionArchiveText

    with_text = OpinionArchiveText.objects.filter(state=ArchiveTextState.DONE).exclude(body="")
    lagging = OpinionArchiveSearchDocument.objects.filter(
        has_body_text=False, binary__text__in=with_text
    ).count()
    if lagging:
        findings.append(f"{lagging} real on tekst olemas, kuid projektsioon seda ei kajasta")

    # The two columns the archive workspace answers "is this letter attached to
    # anything?" from. They are computed at index time from relations that
    # nothing about a binary touches, so before the freshness handlers existed a
    # whole corpus could be linked while every row still read `Sidumata` — and
    # this function reported a clean run throughout, which is how 320 links
    # stayed invisible for a fortnight (UX-006, docs/adr/0041).
    #
    # Reported in both directions. A row claiming a link it does not have is
    # rarer and worse: the list would offer `Teemaga seotud` and the letter's own
    # page would name no Teema at all.
    linked = OpinionArchiveMatterLink.objects.filter(binary=OuterRef("binary_id"))
    imported = OpinionSubmissionImport.objects.filter(item__binary=OuterRef("binary_id"))
    drift = OpinionArchiveSearchDocument.objects.annotate(
        really_linked=Exists(linked), really_imported=Exists(imported)
    )

    link_drift = drift.exclude(is_linked=F("really_linked")).count()
    if link_drift:
        findings.append(f"{link_drift} real ei ühti teemaseose olek kanooniliste seostega")

    submission_drift = drift.exclude(has_submission=F("really_imported")).count()
    if submission_drift:
        findings.append(f"{submission_drift} real ei ühti arvamuse olek kanooniliste kirjetega")

    return findings
