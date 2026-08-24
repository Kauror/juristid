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
import contextvars
from collections.abc import Iterator
from dataclasses import dataclass

from django.contrib.postgres.search import SearchVector
from django.db import connection, transaction
from django.db.models import QuerySet
from django.utils import timezone

from app.core.text import normalize_for_matching
from app.documents.models import DocumentVersion
from app.legacy_import.source_pages import MatterSourcePage
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
#:
#: A :class:`~contextvars.ContextVar` rather than a module global, and the
#: difference is not stylistic. A module global is one flag for the whole
#: process, so a bulk operation running in one request thread suppresses
#: indexing in *every* other thread for its duration — and the writes that lose
#: their refresh belong to somebody else, who never suspended anything and has
#: no obligation to reindex. The result is a Matter that saved successfully and
#: cannot be found, with nothing anywhere recording that it happened.
#:
#: Today's only caller is a management command, so the process is its own. That
#: is a property of the current caller, not of this function, and
#: `suspend_indexing` is a public context manager that any future service can
#: reach for. A ContextVar is per-thread and per-async-task for free, so the
#: guarantee stops depending on who happens to call it.
_suspended: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "search_indexing_suspended", default=False
)

#: Advisory-lock keys. The namespace is arbitrary but fixed, so this subsystem
#: cannot collide with another one that also reaches for advisory locks.
_LOCK_NAMESPACE = 24601
_REBUILD_LOCK = 1


def indexing_is_suspended() -> bool:
    return _suspended.get()


@contextlib.contextmanager
def suspend_indexing() -> Iterator[None]:
    """Stop per-row reindexing for a bulk operation.

    The caller takes on the obligation to refresh what it touched. Used by the
    importer, which knows exactly which Matters it wrote and can do it in one
    pass.
    """
    token = _suspended.set(True)
    try:
        yield
    finally:
        _suspended.reset(token)


def _hold_off_a_rebuild() -> None:
    """Take the shared side of the rebuild gate for the rest of this transaction.

    Every targeted refresh calls this, and the reason is a race that costs a
    user their save. A full rebuild empties the table and refills it in one
    transaction. A Matter save that lands in the middle deletes the row it is
    about to replace — blocks on the rebuild's lock, waits for it to commit,
    then finds its delete matched nothing, because the row it could see is gone
    and the row the rebuild inserted is not in its statement's snapshot. The
    insert that follows hits ``search_one_document_per_source_object`` and
    raises, and since the refresh runs inside the user's business transaction,
    the *business write* is what rolls back. Rebuilding the index is the
    documented recovery tool; it must not make ordinary work fail.

    Shared, so concurrent writers never wait for each other — only for a
    rebuild, which is rare and takes seconds. Held to the end of the
    transaction by PostgreSQL, so there is nothing to release.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock_shared(%s, %s)", [_LOCK_NAMESPACE, _REBUILD_LOCK]
        )


def _hold_off_refreshes() -> None:
    """Take the exclusive side of the same gate, for the whole rebuild.

    Waits for the targeted refreshes already in flight and makes the ones that
    arrive during the rebuild wait for it. A refresh that took the shared lock
    inside this transaction — every one the rebuild performs itself — is granted
    it immediately, because a transaction never blocks on a lock it already
    holds.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [_LOCK_NAMESPACE, _REBUILD_LOCK])


@dataclass(frozen=True)
class RebuildResult:
    documents: int
    matters: int
    seconds: float
    index_version: str
    entries: int = 0
    submissions: int = 0
    fragments: int = 0
    source_pages: int = 0


def indexable_matters() -> QuerySet[Matter]:
    """Every Matter, with the related rows the projection needs.

    Unscoped on purpose: the index covers everything, and *reading* it is what
    authorization filters. An index that only held rows the indexing user could
    see would silently differ between operators.
    """
    return Matter.objects.select_related("addressee_organisation").prefetch_related(
        "engagements",
        "source_organisations",
        "source_organisations__aliases",
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
    # Every sender, not the first one. A Matter that arrived from a ministry and
    # an association has to be findable through either name, and indexing only
    # one of them would fail in the way nobody reports: the search returns
    # results, just not that record (Agent-E brief 40).
    #
    # Sorted here rather than taken in join order. The projection is hashed and
    # compared between rebuilds to decide whether a row changed, so text whose
    # word order depended on what the database happened to return would make
    # every rebuild look like a content change (brief 41).
    senders = sorted(
        matter.source_organisations.all(),
        key=lambda organisation: (organisation.name, str(organisation.pk)),
    )
    for organisation in (*senders, matter.addressee_organisation):
        if organisation is None:
            continue
        parts.append(organisation.name)
        parts.extend(alias.alias for alias in organisation.aliases.all())
    for area in matter.policy_areas.all():
        parts.append(area.name_et)
    # The free-text area, beside the canonical names rather than instead of
    # them. It is descriptive metadata somebody typed, so it belongs in the
    # alias column at weight C with the other names — and it is emphatically
    # *not* a PolicyArea: no statistic counts it and no taxonomy row exists for
    # it (Stage-2E.1 brief 20, app/matters/models.py).
    if matter.policy_area_other:
        parts.append(matter.policy_area_other)
    for tag in matter.tags.all():
        parts.append(tag.name_et)
        parts.extend(alias.alias for alias in tag.aliases.all())

    # The diacritic-free form as well, so `oigusloome` finds `õigusloome`
    # without unaccent having to be in the query path.
    normalized = [normalize_for_matching(part) for part in parts]
    return " ".join(dict.fromkeys([*parts, *normalized]))


def _engagement_text_for(matter: Matter) -> str:
    """What a `Kaasamine` record makes a Matter findable by.

    The title, the note, and the link's **host** rather than the link. A
    campaign URL is mostly tracking parameters, and indexing those adds
    thousands of meaningless tokens per Matter without making anything easier
    to find. The host's own labels go in beside it, because PostgreSQL treats
    ``survey.alchemer.example`` as a single token and a reader typing the
    vendor's name would otherwise get nothing (Agent-F brief 47).

    Sorted before joining, so two rebuilds of an unchanged Matter produce
    identical text. The projection is compared to decide whether a row changed;
    text whose word order came from the join would make every rebuild look like
    an edit (brief 48).
    """
    parts: list[str] = []
    for engagement in sorted(
        matter.engagements.all(), key=lambda record: (record.title, str(record.pk))
    ):
        parts.append(engagement.title)
        if engagement.note:
            parts.append(engagement.note)
        parts.extend(engagement.link_search_terms)
    return " ".join(parts)


def _title_text_for(matter: Matter) -> str:
    titles = [matter.title, *(matter.alternate_titles or [])]
    return " ".join(title for title in titles if title)


def indexed_text_for(matter: Matter) -> dict[str, str]:
    """The four searchable columns a Matter projects, and nothing else.

    Separated from the rest of the row so there is one owner of *what text
    represents a Matter*. The refresh writes it; `check_search_integrity`
    recomputes it to find rows whose text has gone stale behind a rename. Two
    copies of this composition would drift, and the check would then report on
    a rule the indexer no longer follows.
    """
    return {
        "title": _title_text_for(matter),
        "identifiers": _identifiers_for(matter),
        "alias_text": _alias_text_for(matter),
        # The Matter's own authored summaries. Entry, Submission and document
        # text live in their own rows, so a result can say which of them
        # matched — folding them in here would make every hit read "the matter
        # matched" and lose the locator entirely (docs/adr/0014).
        # The Matter's own authored summaries, plus what `Kaasamine` says. An
        # engagement has no row of its own in the projection — it is a pointer,
        # not a document — so its text belongs on the Matter, which is the
        # thing a reader is looking for when they type a campaign's name.
        "body_text": " ".join(
            part
            for part in (
                # The plain-language summary first. It is what somebody who
                # remembers a Matter by what it *was about* rather than by its
                # formal title will type, and it is frequently the only text on
                # the record written in those words (Teema redesign §6.2).
                matter.brief_summary,
                matter.position_summary,
                matter.rationale_summary,
                _engagement_text_for(matter),
            )
            if part
        ),
    }


def _document_values(matter: Matter, now: object) -> dict[str, object]:
    return {
        "matter": matter,
        "source_kind": SearchSourceKind.MATTER,
        "source_object_id": matter.pk,
        **indexed_text_for(matter),
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

    _hold_off_a_rebuild()
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


def reindex_submission(submission: Submission) -> None:
    """Refresh one submission from a service that bulk-writes its children.

    The signal handlers in :mod:`app.search.signals` cannot cover this. Django
    sends ``post_save`` per instance, and ``bulk_create`` sends none — so
    ``set_recipients``, which deletes the old recipient rows and bulk-creates
    the new ones, fires the *removal* handler and not the *addition* one. The
    submission is reindexed with an empty recipient list and then left there:
    findable under no ministry at all, which is the exact shape of the question
    the product exists to answer ("where is the opinion we sent to MKM").

    Suspension-aware like the handlers, so a bulk importer that suspended
    indexing still owns its own refresh.
    """
    if indexing_is_suspended():
        return
    refresh_submission(submission)


@transaction.atomic
def refresh_entry(entry: Entry) -> int:
    from app.search.child_indexing import indexable_entries, refresh_entries

    _hold_off_a_rebuild()
    count = refresh_entries(indexable_entries().filter(pk=entry.pk))
    _recompute_vectors(
        SearchDocument.objects.filter(source_kind=SearchSourceKind.ENTRY, source_object_id=entry.pk)
    )
    return count


@transaction.atomic
def refresh_submission(submission: Submission) -> int:
    from app.search.child_indexing import indexable_submissions, refresh_submissions

    _hold_off_a_rebuild()
    count = refresh_submissions(indexable_submissions().filter(pk=submission.pk))
    _recompute_vectors(
        SearchDocument.objects.filter(
            source_kind=SearchSourceKind.SUBMISSION, source_object_id=submission.pk
        )
    )
    return count


@transaction.atomic
def refresh_source_link(link: MatterSourcePage) -> int:
    """Reproject one Matter↔page relationship."""
    from app.search.child_indexing import indexable_source_links, refresh_source_links

    _hold_off_a_rebuild()
    count = refresh_source_links(indexable_source_links().filter(pk=link.pk))
    _recompute_vectors(
        SearchDocument.objects.filter(
            source_kind=SearchSourceKind.LEGACY_SOURCE_PAGE, source_object_id=link.pk
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

    Atomic in its own right as well, and not only because its three siblings
    are. The other caller is the ``Document`` ``post_save`` handler, which fires
    wherever a document is saved — today always inside a service transaction,
    tomorrow from whatever rename or reclassify route gets written. Every
    refresh here deletes before it inserts, so an unwrapped call that failed
    in between would leave the file's every page deleted from the index and
    nothing put back: precisely the outcome the paragraph above rules out, but
    reached from the other side.
    """
    from app.search.child_indexing import refresh_version_fragments

    _hold_off_a_rebuild()
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
        # Before the first statement that touches the table, so an in-flight
        # targeted refresh finishes and the next one waits rather than
        # colliding with the refill (:func:`_hold_off_a_rebuild`).
        _hold_off_refreshes()

        if clear:
            SearchDocument.objects.all().delete()

        identifiers = list(indexable_matters().order_by("pk").values_list("pk", flat=True))
        total = 0
        for offset in range(0, len(identifiers), batch_size):
            chunk = identifiers[offset : offset + batch_size]
            total += refresh_matters(indexable_matters().filter(pk__in=chunk))

        entries, submissions, fragments, source_pages = _rebuild_children(batch_size=batch_size)
        documents = SearchDocument.objects.count()

    return RebuildResult(
        documents=documents,
        matters=total,
        entries=entries,
        submissions=submissions,
        fragments=fragments,
        source_pages=source_pages,
        seconds=(timezone.now() - started).total_seconds(),
        index_version=INDEX_VERSION,
    )


def _rebuild_children(*, batch_size: int) -> tuple[int, int, int, int]:
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
        indexable_source_links,
        indexable_submissions,
        refresh_entries,
        refresh_fragments,
        refresh_source_links,
        refresh_submissions,
    )

    entries = _in_batches(indexable_entries(), refresh_entries, batch_size)
    submissions = _in_batches(indexable_submissions(), refresh_submissions, batch_size)
    fragments = _in_batches(indexable_fragments(), refresh_fragments, batch_size)
    source_pages = _in_batches(indexable_source_links(), refresh_source_links, batch_size)

    # One statement for every child row, rather than one per batch. The vectors
    # are computed in the database either way; doing it once means PostgreSQL
    # plans it once.
    _recompute_vectors(SearchDocument.objects.exclude(source_kind=SearchSourceKind.MATTER))
    return entries, submissions, fragments, source_pages


def _in_batches(queryset: QuerySet, refresh: object, batch_size: int) -> int:
    identifiers = list(queryset.order_by("pk").values_list("pk", flat=True))
    total = 0
    for offset in range(0, len(identifiers), batch_size):
        chunk = identifiers[offset : offset + batch_size]
        total += refresh(queryset.filter(pk__in=chunk))  # type: ignore[operator]
    return total
