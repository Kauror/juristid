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
from app.legacy_import.opinion_enums import OpinionCandidateState
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
    text = getattr(binary, "text", None)
    linked = OpinionArchiveMatterLink.objects.filter(binary=binary)
    has_submission = OpinionSubmissionImport.objects.filter(item__binary=binary).exists()
    candidates = _candidate_rows(binary=binary)

    return {
        **_occurrence_values(
            sha256=binary.sha256,
            occurrences=_occurrence_rows(binary=binary),
            metadata=_metadata_rows(binary=binary),
            candidates=candidates,
        ),
        **_text_values(text),
        **_candidate_values(candidates),
        "is_linked": linked.exists(),
        "has_submission": has_submission,
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


def _occurrence_rows(*, binary: Any = None) -> list[dict[str, Any]]:
    """The occurrence rows the projection reads, in the order it reads them in.

    One definition, used by the row builder and by the drift check, for the same
    reason `_candidate_rows` has one: a verifier that fetched its own columns in
    its own order would be checking itself rather than the projection.

    Only materialised occurrences. An `OpinionArchiveItem` with no binary is a
    catalogued path whose bytes nobody has copied yet, and there is no row for it
    to contribute to.

    The order is the model's own (`filename_date`, then `original_filename`), and
    it is what makes `title`, `recipient` and `document_date` well defined when a
    letter is filed at several paths. Narrowing to one binary leaves the relative
    order of that binary's rows unchanged, so the per-row caller and the
    corpus-wide one see the same sequence for the same letter.
    """
    rows = (
        OpinionArchiveItem.objects.filter(binary=binary)
        if binary is not None
        else OpinionArchiveItem.objects.filter(binary__isnull=False)
    )
    # `values()` is typed as a TypedDict by the stubs, which is narrower than
    # either caller wants; the row shape is settled by `_occurrence_values`.
    selected: Any = rows.values(
        "binary_id",
        "archive_relative_path",
        "original_filename",
        "filename_title",
        "filename_recipient",
        "filename_date",
        "sha256",
    )
    return list(selected)


def _metadata_rows(*, binary: Any = None) -> list[dict[str, Any]]:
    """KodaDash's reading of those occurrences, on the same terms.

    Occurrence-scoped like the candidates: `OpinionArchiveMetadata.item` is
    `CASCADE`, so removing an occurrence removes the reading of it, and the
    fallback title, recipient, date and external id it contributed go with it.
    """
    rows = (
        OpinionArchiveMetadata.objects.filter(item__binary=binary)
        if binary is not None
        else OpinionArchiveMetadata.objects.filter(item__binary__isnull=False)
    )
    selected: Any = rows.values(
        "item__binary_id",
        "title",
        "recipient_raw",
        "recipient_normalized",
        "document_date",
        "external_id",
    )
    return list(selected)


def _occurrence_values(
    *,
    sha256: str,
    occurrences: Sequence[Mapping[str, Any]],
    metadata: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The columns a letter's live occurrences decide.

    Extracted for the same reason `_candidate_values` was: the row builder and
    `archive_index_findings` must not be able to disagree about what the
    projection *should* say. Every value here moves when an occurrence is
    catalogued, removed or moved to another binary — including `identifiers`,
    which unions the occurrence's own filename with the external ids and register
    references hanging off it, and would otherwise go stale behind the two
    obvious columns.

    `sha256` is the binary's own and is passed in rather than looked up: it is
    the one identifier that does not depend on the occurrences at all, and it is
    what keeps a letter with no occurrences left findable by its hash.
    """
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
        [sha256]
        + [occurrence["original_filename"] for occurrence in occurrences]
        + [row["external_id"] for row in metadata]
        + [candidate["excel_reference"] for candidate in candidates]
    )
    paths = _unique(occurrence["archive_relative_path"] for occurrence in occurrences)
    return {
        "title": title or "",
        "recipient": recipient or "",
        "document_date": document_date,
        "source_year": document_date.year if document_date else None,
        "identifiers": "\n".join(identifiers),
        "occurrence_paths": "\n".join(paths),
        "occurrence_count": len(occurrences),
    }


def _text_values(text: Any) -> dict[str, Any]:
    """The two columns a letter's extracted text decides.

    Extracted for the reason `_candidate_values` and `_occurrence_values` were:
    the row builder and `archive_index_findings` must not be able to disagree
    about what the projection *should* say. Here that mattered more than
    elsewhere, because the check this replaces asked a narrower question than
    the builder answers — it looked for a body the projection had not picked up
    yet and could not see a projection still carrying one nothing holds.

    The two columns move together or not at all. `has_body_text` is what the
    `Sisuga` filter reads and `body_text` is what the search vector is built
    from, so a row whose flag has been cleared while its body survives is
    filtered out of the corpus and still findable by the words in it.

    `has_body` is **not** restated here. It is the model's own property — the
    single definition of "this row has a searchable body", folding the
    `ArchiveTextState` question into the emptiness one — and both callers read
    it off an instance rather than re-deriving it, in Python or in SQL.
    """
    has_body = bool(text is not None and text.has_body)
    return {
        "body_text": text.body if has_body else "",
        "has_body_text": has_body,
    }


def _candidate_rows(*, binary: Any = None) -> list[dict[str, Any]]:
    """The candidate rows the projection reads, in the order it reads them in.

    One definition, used by the row builder and by the drift check, because two
    would come apart and the drift check would then be measuring itself rather
    than the projection.

    ``SUPERSEDED`` is excluded here and nowhere else. A retired proposal is the
    record of a belief that was replaced, so a workspace filtering on
    `review_state` must not find a letter under a state nothing holds any more
    — which is also why superseding a row moves the projection at all.

    The order is the model's own (`match_class` first), and it is what makes
    `match_class` below well defined. Narrowing to one binary leaves the
    relative order of that binary's rows unchanged, so the per-row caller and
    the corpus-wide one see the same sequence for the same letter.
    """
    rows = OpinionMatchCandidate.objects.exclude(state=OpinionCandidateState.SUPERSEDED)
    rows = (
        rows.filter(item__binary=binary)
        if binary is not None
        else rows.filter(item__binary__isnull=False)
    )
    # The stubs type `values()` as a TypedDict, which is narrower than either
    # caller wants and not assignable to the plain mapping they share. The row
    # shape is settled by `_candidate_values` reading it, not by this line.
    selected: Any = rows.values("item__binary_id", "match_class", "state", "excel_reference")
    return list(selected)


def _candidate_values(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The two projected columns a letter's live candidates decide.

    Extracted so the row builder and `archive_index_findings` cannot disagree
    about what the projection *should* say: a verifier carrying its own priority
    order would report drift where there is none and miss it where there is.
    """
    return {
        "match_class": _first(candidate["match_class"] for candidate in candidates) or "",
        "review_state": _review_state(candidates),
    }


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

    findings.extend(_candidate_drift_findings())
    findings.extend(_occurrence_drift_findings())
    findings.extend(_text_drift_findings())
    return findings


def _by_binary(rows: Iterable[Mapping[str, Any]], key: str) -> dict[Any, list[Mapping[str, Any]]]:
    """Corpus-wide rows, gathered under the binary each one describes.

    The drift checks read the whole corpus in a handful of queries and then walk
    it, rather than asking per row: a few hundred letters is a walk, and a query
    per letter is a corpus-sized loop inside a diagnostic.
    """
    grouped: dict[Any, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row[key], []).append(row)
    return grouped


def _candidate_drift_findings() -> list[str]:
    """Where the projection and the live candidates disagree about a letter.

    `review_state` and `match_class` are the other two derived columns, and they
    come from `OpinionMatchCandidate` — a relation that, like the two above,
    nothing about a binary touches. A reviewer rejecting a letter, a rerun
    superseding a proposal, an apply marking one APPLIED: each moves what the
    projection should say without moving the binary, and every check above
    passes cleanly throughout.

    Computed through the same two helpers the row builder uses rather than
    re-derived in SQL. `review_state` has a priority order and `match_class`
    depends on the model's ordering; a second implementation of either would be
    checking itself. Two queries and a walk over a few hundred rows, which is
    what this costs and what a corpus-wide join would cost anyway.

    Both directions, as above. A row still reading `Ootel` after a rejection is
    the common case; a row claiming a decision no live candidate justifies is
    the rarer and worse one, because the queue would then be filtering on
    something nobody said.
    """
    by_binary = _by_binary(_candidate_rows(), "item__binary_id")

    class_drift = state_drift = 0
    stored = OpinionArchiveSearchDocument.objects.values_list(
        "binary_id", "match_class", "review_state"
    )
    for binary_id, match_class, review_state in stored.iterator():
        canonical = _candidate_values(by_binary.get(binary_id, []))
        class_drift += int(canonical["match_class"] != match_class)
        state_drift += int(canonical["review_state"] != review_state)

    findings: list[str] = []
    if state_drift:
        findings.append(f"{state_drift} real ei ühti ülevaatuse olek elusate kandidaatidega")
    if class_drift:
        findings.append(f"{class_drift} real ei ühti sidumise klass elusate kandidaatidega")
    return findings


def _occurrence_drift_findings() -> list[str]:
    """Where the projection and the live occurrences disagree about a letter.

    The fourth relation of the same shape, and the one the other three hang off.
    `OpinionArchiveItem` is what says a letter was found at this path under this
    name, and `occurrence_count`, `occurrence_paths`, `identifiers`, `title`,
    `recipient` and `document_date` are all read off the live set of them at
    index time. Removing an occurrence therefore moves six columns at once,
    takes that occurrence's metadata and candidates with it — and, before the
    handlers in `opinion_search_signals`, left the row claiming the path, the
    filename and the count of a filing that no longer exists, with every check
    above reporting a clean run.

    Both directions, like the checks above, and inherently so: a stale extra
    path, a missing one, a count that never moved and an identifier left behind
    are all the recomputed value differing from the stored one.

    Computed through `_occurrence_values` rather than re-derived in SQL, for the
    reason `_candidate_drift_findings` gives: the precedence between an
    occurrence's filename and KodaDash's reading of it is the row builder's, and
    a verifier carrying a second copy of it would be checking itself.
    """
    occurrences = _by_binary(_occurrence_rows(), "binary_id")
    metadata = _by_binary(_metadata_rows(), "item__binary_id")
    candidates = _by_binary(_candidate_rows(), "item__binary_id")

    count_drift = path_drift = identifier_drift = heading_drift = 0
    stored = OpinionArchiveSearchDocument.objects.values_list(
        "binary_id",
        "binary__sha256",
        "occurrence_count",
        "occurrence_paths",
        "identifiers",
        "title",
        "recipient",
        "document_date",
        "source_year",
    )
    for (
        binary_id,
        sha256,
        stored_count,
        stored_paths,
        stored_identifiers,
        stored_title,
        stored_recipient,
        stored_date,
        stored_year,
    ) in stored.iterator():
        canonical = _occurrence_values(
            sha256=sha256,
            occurrences=occurrences.get(binary_id, []),
            metadata=metadata.get(binary_id, []),
            candidates=candidates.get(binary_id, []),
        )
        count_drift += int(canonical["occurrence_count"] != stored_count)
        path_drift += int(canonical["occurrence_paths"] != stored_paths)
        identifier_drift += int(canonical["identifiers"] != stored_identifiers)
        # One finding for the three columns a letter is *described* by, because
        # they move together: they are the first occurrence's filename fields,
        # falling back to KodaDash's reading of it, and `source_year` is only
        # `document_date`'s year.
        heading_drift += int(
            (
                canonical["title"],
                canonical["recipient"],
                canonical["document_date"],
                canonical["source_year"],
            )
            != (stored_title, stored_recipient, stored_date, stored_year)
        )

    findings: list[str] = []
    if count_drift:
        findings.append(f"{count_drift} real ei ühti esinemiste arv kataloogitud kirjetega")
    if path_drift:
        findings.append(f"{path_drift} real ei ühti arhiiviteed kataloogitud kirjetega")
    if identifier_drift:
        findings.append(f"{identifier_drift} real ei ühti tunnused kataloogitud kirjetega")
    if heading_drift:
        findings.append(
            f"{heading_drift} real ei ühti pealkiri, saaja või kuupäev kataloogitud kirjetega"
        )
    return findings


def _text_drift_findings() -> list[str]:
    """Where the projection and the extracted text disagree about a letter.

    The relation the projection reads for its two largest columns, and the last
    of the six to be checked properly. What stood here before was a single
    one-directional test — canonical `DONE` with a body, projection saying
    `has_body_text=False` — which caught the case that had actually happened
    (extract, forget to rebuild) and was silent about every other one. Once text
    became a freshness dependency rather than something a rebuild picked up, a
    check that could only see a body arriving was not enough: a re-extraction
    that replaced a body, a letter the malware policy later declined to open, a
    scanned file re-read as having no text layer, all move the projection the
    other way, and a row left carrying the previous extraction's body is
    findable by words the archive no longer holds.

    So both columns are recomputed through `_text_values` — the row builder's own
    function, for the reason `_candidate_drift_findings` shares
    `_candidate_values` — and compared against what is stored. Both directions
    then follow from the comparison rather than from a list of cases: a missing
    body, a stale one, a phantom one and a flag that has come apart from its
    body are all a recomputed value differing from a stored one.

    Reported as two findings rather than one, because they are two different
    operator problems. A flag that disagrees means the `Sisuga` filter and the
    coverage figures are wrong; a body that disagrees means the *search* is
    wrong, which is worse and much harder to notice by looking.

    The one drift check that has to hold the column it compares in memory. The
    canonical side is read as model instances so that `has_body` stays the
    model's property, deferred to the three fields that decide it, and bounded
    by `MAX_BODY_CHARACTERS` per letter over a corpus of hundreds — the same
    bytes `rebuild_archive_index` already streams through a search vector.
    """
    from app.legacy_import.opinion_binary import OpinionArchiveText

    canonical = {
        row.binary_id: row
        for row in OpinionArchiveText.objects.only("binary", "state", "body").iterator()
    }

    body_drift = flag_drift = 0
    stored = OpinionArchiveSearchDocument.objects.values_list(
        "binary_id", "body_text", "has_body_text"
    )
    for binary_id, body_text, has_body_text in stored.iterator():
        expected = _text_values(canonical.get(binary_id))
        body_drift += int(expected["body_text"] != body_text)
        flag_drift += int(expected["has_body_text"] != has_body_text)

    findings: list[str] = []
    if body_drift:
        findings.append(f"{body_drift} real ei ühti otsitav tekst eraldatud tekstiga")
    if flag_drift:
        findings.append(f"{flag_drift} real ei ühti tekstiolek eraldatud tekstiga")
    return findings
