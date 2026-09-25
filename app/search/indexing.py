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
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from django.contrib.postgres.search import SearchVector
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import F, Func, QuerySet, TextField
from django.utils import timezone

from app.core.text import normalize_for_matching
from app.documents.models import Document, DocumentVersion
from app.legacy_import.source_pages import MatterSourcePage
from app.matters.models import (
    Entry,
    Matter,
    MatterEngagement,
    MatterExternalPosition,
    MatterProceduralDevelopment,
)
from app.search.child_indexing import _generations, bounded_body
from app.search.models import INDEX_VERSION, INITIAL_GENERATION, SearchDocument, SearchSourceKind
from app.submissions.models import Submission

#: Sources per batch during a full rebuild. Each batch is one transaction under
#: the exclusive side of the refresh gate, so this is what bounds how long a
#: concurrent save can wait (ENG-011). Measured on the LARGE synthetic corpus
#: (81,633 rows): 500 held the gate for up to 1.5 s — a page of document
#: fragments — and 200 for up to 0.7 s, for a rebuild 3 % slower overall
#: (docs/adr/0118). A failed batch loses nothing readers see: the generation it
#: was filling never becomes active.
BATCH_SIZE = 200

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
#: A second key in the same namespace: held (at session level) by the one full
#: rebuild allowed at a time. Distinct from the gate, which orders refreshes
#: against a rebuild's batches rather than rebuilds against each other.
_REBUILD_OWNER = 2


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

    Every targeted refresh calls this. A full rebuild fills its generation one
    batch at a time, each batch under the exclusive side
    (:func:`_hold_off_refreshes`), and a refresh that ran in the middle of a
    batch would race it for the same rows: whichever inserted second would hit
    the one-row-per-source constraint, and since a refresh runs inside the
    user's business transaction, the *business write* is what would roll back.
    Shared, so concurrent writers never wait for each other; held to the end of
    the transaction by PostgreSQL, so there is nothing to release.

    **Never a deadlock victim.** A writer reaches this holding its own row
    locks. A batch holding the exclusive side can need one of them — inserting a
    row whose foreign key names a record the writer is deleting — and then each
    waits for the other. PostgreSQL would break that cycle by cancelling
    whichever waiter checks first, which can be the user's save. So a writer
    that has to wait does so in slices shorter than ``deadlock_timeout``, inside
    a savepoint, and never lets the deadlock check run; the batch waits for row
    locks for at most half of it (:func:`_bounded_lock_waits`), gives up first,
    rolls back and retries. Each slice queues behind a waiting batch like an
    ordinary lock request, so a writer is not starved by the next batch either.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_xact_lock_shared(%s, %s)", [_LOCK_NAMESPACE, _REBUILD_LOCK]
        )
        (acquired,) = cursor.fetchone()
    if acquired:
        return
    slice_ms = max(10, min(100, _deadlock_timeout_ms() // 4))
    original = _lock_timeout()
    while True:
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT set_config('lock_timeout', %s, true)", [f"{slice_ms}ms"])
                cursor.execute(
                    "SELECT pg_advisory_xact_lock_shared(%s, %s)",
                    [_LOCK_NAMESPACE, _REBUILD_LOCK],
                )
                # Restored inside the savepoint, so the rest of the business
                # transaction waits for locks exactly as it would have.
                cursor.execute("SELECT set_config('lock_timeout', %s, true)", [original])
            return
        except OperationalError as error:
            if _sqlstate(error) != _LOCK_NOT_AVAILABLE:
                raise


def _hold_off_refreshes() -> None:
    """Take the exclusive side of the same gate, for one step of a rebuild.

    Waits for the targeted refreshes already in flight and makes the ones that
    arrive meanwhile wait for this transaction. A refresh the rebuild performs
    itself takes the shared side inside this transaction and is granted it
    immediately, because a transaction never blocks on a lock it already holds.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [_LOCK_NAMESPACE, _REBUILD_LOCK])


def _bounded_lock_waits() -> None:
    """For the rest of a rebuild batch, give up on a row lock after half ``deadlock_timeout``.

    The other half of :func:`_hold_off_a_rebuild`'s promise: a batch that waits
    on a writer who is waiting on the batch times out before PostgreSQL's
    deadlock check could cancel either of them, and the batch is retried.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('lock_timeout', %s, true)",
            [f"{max(20, _deadlock_timeout_ms() // 2)}ms"],
        )


#: SQLSTATEs a rebuild batch retries: lock_not_available (its bounded lock wait
#: ran out) and deadlock_detected (should the check ever get there first).
_LOCK_NOT_AVAILABLE = "55P03"
_DEADLOCK_DETECTED = "40P01"


def _sqlstate(error: BaseException) -> str | None:
    return getattr(getattr(error, "__cause__", None), "sqlstate", None)


def _deadlock_timeout_ms() -> int:
    with connection.cursor() as cursor:
        # pg_settings reports deadlock_timeout in milliseconds whatever unit it was set in.
        cursor.execute("SELECT setting::int FROM pg_settings WHERE name = 'deadlock_timeout'")
        (value,) = cursor.fetchone()
    return int(value)


def _lock_timeout() -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('lock_timeout')")
        (value,) = cursor.fetchone()
    return str(value)


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
    engagements: int = 0
    developments: int = 0
    positions: int = 0
    document_rows: int = 0
    #: The generation this rebuild built and activated (docs/adr/0118).
    generation: int = 0
    #: The longest time the exclusive side of the refresh gate was held — the
    #: longest a business write could have waited on this rebuild.
    longest_gate_seconds: float = 0.0
    #: How long the swap itself held it.
    swap_gate_seconds: float = 0.0
    batches: int = 0
    #: Batches rolled back and tried again because a concurrent write got in
    #: their way — a deleted source, or a row lock a waiting writer held.
    retried_batches: int = 0


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
        "tags__merged_into__aliases",
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
        # A tag merged into another stays findable through the one that
        # replaced it: the governed name and its aliases are what a lawyer
        # searches by today, and the Matter still carries the old assignment
        # (`Tag.merged_into`'s own promise, master specification 14.7). Until
        # ENG-081 this loop never followed the merge, and a Matter tagged with
        # a retired tag was unreachable through its successor.
        canonical = tag.canonical()
        if canonical.pk != tag.pk:
            parts.append(canonical.name_et)
            parts.extend(alias.alias for alias in canonical.aliases.all())

    # The diacritic-free form as well, so `oigusloome` finds `õigusloome`
    # without unaccent having to be in the query path.
    normalized = [normalize_for_matching(part) for part in parts]
    return " ".join(dict.fromkeys([*parts, *normalized]))


def _title_text_for(matter: Matter) -> str:
    titles = [matter.title, *(matter.alternate_titles or [])]
    return " ".join(title for title in titles if title)


def indexed_text_for(matter: Matter) -> dict[str, str]:
    """The searchable text columns a Matter projects, and nothing else.

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
        "people_text": "",
        # The Matter's own authored summaries. Entry, Submission and document
        # text live in their own rows, so a result can say which of them
        # matched — folding them in here would make every hit read "the matter
        # matched" and lose the locator entirely (docs/adr/0014).
        # The Matter's own authored summaries, plus what `Kaasamine` says. An
        # engagement has no row of its own in the projection — it is a pointer,
        # not a document — so its text belongs on the Matter, which is the
        # thing a reader is looking for when they type a campaign's name.
        # Bounded for the projection only, like every authored body: three
        # summaries of a few hundred thousand unique words each would exceed
        # PostgreSQL's 1 MiB tsvector limit inside the lawyer's own save
        # (ENG-084). The Matter keeps every character.
        "body_text": bounded_body(
            " ".join(
                part
                for part in (
                    # The plain-language summary first. It is what somebody who
                    # remembers a Matter by what it *was about* rather than by its
                    # formal title will type, and it is frequently the only text on
                    # the record written in those words (Teema redesign §6.2).
                    matter.brief_summary,
                    matter.position_summary,
                    matter.rationale_summary,
                    # `Kaasamine` is deliberately absent. It has its own row kind
                    # now, because it has its own visibility and this row does not
                    # (app/search/child_indexing.py, AUTH-003). A MATTER row may
                    # only carry content the Matter itself governs.
                )
                if part
            )
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
def refresh_matters(matters: QuerySet[Matter], *, generations: Sequence[int] | None = None) -> int:
    """Rewrite the projection for the given Matters. Idempotent.

    In every live generation — or in ``generations``, which is how a full
    rebuild fills its own (`app.search.child_indexing._generations`).
    """
    now = timezone.now()
    rows = list(matters)
    if not rows:
        return 0

    _hold_off_a_rebuild()
    generations = _generations(generations)
    matter_ids = [matter.pk for matter in rows]
    # Delete-then-insert rather than update: it is one shape of statement
    # regardless of whether a Matter was indexed before, so a half-built index
    # and a fully built one converge to the same result.
    SearchDocument.objects.filter(
        generation__in=generations, matter_id__in=matter_ids, source_kind=SearchSourceKind.MATTER
    ).delete()
    SearchDocument.objects.bulk_create(
        [
            SearchDocument(**_document_values(matter, now), generation=generation)
            for matter in rows
            for generation in generations
        ]
    )
    # Scoped to the rows just written. Without the kind filter this would also
    # recompute every entry, submission and document fragment belonging to these
    # matters — correct output, and at fragment scale an enormous amount of
    # pointless work on every Matter save.
    _recompute_vectors(
        SearchDocument.objects.filter(
            generation__in=generations,
            matter_id__in=matter_ids,
            source_kind=SearchSourceKind.MATTER,
        )
    )
    return len(rows)


def _folded(column: str) -> Func:
    """A column with its diacritics removed, by the database's own `unaccent`.

    The query side folds with the same function (`app.search.services`), so an
    index built here and a query built there cannot disagree about what «õ»
    becomes.
    """
    return Func(F(column), function="unaccent", output_field=TextField())


def _recompute_vectors(documents: QuerySet[SearchDocument]) -> None:
    """Let PostgreSQL build the vectors, with the weights ranking depends on.

    A over B over C over D: a term in the title outranks the same term in an
    identifier, which outranks an organisation alias or an author's name,
    which outranks body text. ``ts_rank`` reads these weights, so this is where
    most of the relevance ordering is actually decided.

    ``search_folded`` is every column once more, diacritics removed and nothing
    stemmed, for the diacritic-free and word-beginning tiers (ENG-031).
    """
    documents.update(
        search_estonian=(
            SearchVector("title", weight="A", config="estonian")
            + SearchVector("identifiers", weight="B", config="estonian")
            + SearchVector("alias_text", weight="C", config="estonian")
            + SearchVector("people_text", weight="C", config="estonian")
            + SearchVector("body_text", weight="D", config="estonian")
        ),
        search_simple=(
            SearchVector("title", weight="A", config="simple")
            + SearchVector("identifiers", weight="B", config="simple")
            + SearchVector("alias_text", weight="C", config="simple")
            + SearchVector("people_text", weight="C", config="simple")
            + SearchVector("body_text", weight="D", config="simple")
        ),
        search_title=SearchVector("title", weight="A", config="estonian"),
        search_folded=(
            SearchVector(_folded("title"), weight="A", config="simple")
            + SearchVector(_folded("identifiers"), weight="B", config="simple")
            + SearchVector(_folded("alias_text"), weight="C", config="simple")
            + SearchVector(_folded("people_text"), weight="C", config="simple")
            + SearchVector(_folded("body_text"), weight="D", config="simple")
        ),
    )


def refresh_matter(matter: Matter) -> int:
    return refresh_matters(indexable_matters().filter(pk=matter.pk))


def forget_matter(matter_id: Any) -> int:
    """Take one Matter out of the projection, with everything hanging off it.

    The one operation `refresh_matters` cannot express. It writes the rows a
    Matter *should* have, and «none, because the Matter is gone» reaches it as
    an empty queryset — on which it returns early and deletes nothing.

    Called by `app.matters.deletion` and by nothing else. It lives here because
    `SearchDocument` is this app's table: no module outside `app.search` may
    name it, which is the rule that keeps business state from depending on
    derived data, and a deletion that reached across to empty the table itself
    would be the first exception to it (master specification 11.3,
    tests/test_search_reliability.py).

    Unconditional and not suspension-aware, unlike the handlers: a caller that
    has just removed a Matter's rows is not refreshing a projection, it is
    stating that there is nothing left to project. Returns how many rows went,
    so the caller can report it.

    Every generation, and under the shared side of the refresh gate: a full
    rebuild's batch reads the Matter under the exclusive side, so a deletion
    either lands before the batch — which then no longer sees the Matter — or
    after it, and removes what the batch wrote (ENG-011).
    """
    with transaction.atomic():
        _hold_off_a_rebuild()
        removed, _ = SearchDocument.objects.filter(matter_id=matter_id).delete()
    return int(removed)


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
def refresh_engagement(engagement: MatterEngagement) -> int:
    """Reproject one `Kaasamine`.

    AUTH-003 gave engagements a row of their own, because they carry a
    `visibility_override` and a MATTER row cannot express one. What it did not
    give them was a way to *arrive*: nothing but a full rebuild ever wrote an
    ENGAGEMENT row, so a consultation recorded this morning was not searchable
    at all — and the integrity check did not count the kind, so nothing said so
    either. Content that exists and cannot be found, with no detector, is the
    worst shape a search defect takes.

    Bounded fanout: one engagement is one row, exactly like an Entry. So it is
    refreshed synchronously, in the caller's transaction, and a recorded
    engagement is a findable one.

    The vector recomputation is not optional bookkeeping. A row inserted without
    it exists, counts as indexed and can never match a full-text query — the one
    defect the projection can hold that looks like success from every angle
    except a lawyer's.
    """
    from app.search.child_indexing import indexable_engagements, refresh_engagements

    _hold_off_a_rebuild()
    count = refresh_engagements(indexable_engagements().filter(pk=engagement.pk))
    _recompute_vectors(
        SearchDocument.objects.filter(
            source_kind=SearchSourceKind.ENGAGEMENT, source_object_id=engagement.pk
        )
    )
    return count


@transaction.atomic
def refresh_development(development: MatterProceduralDevelopment) -> int:
    """Reproject one `Märge`.

    The same shape as `refresh_engagement`, and here for the same defect one
    step further on. `+ Märge` writes a `MatterProceduralDevelopment`, the
    indexer was built around `Entry`, and nothing bridged the two — so every
    note written after the composer simplification was absent from the corpus
    while the seeded legacy entries went on matching. A lawyer searching for
    what they wrote got silence, which reads as «not recorded» rather than as
    «not indexed» (QA-003).

    Bounded fanout — one development is one row — so it is refreshed
    synchronously inside the caller's transaction, and a recorded `Märge` is a
    findable one. The vector recomputation is not bookkeeping: a row inserted
    without it exists, counts as indexed, and can never match.
    """
    from app.search.child_indexing import indexable_developments, refresh_developments

    _hold_off_a_rebuild()
    count = refresh_developments(indexable_developments().filter(pk=development.pk))
    _recompute_vectors(
        SearchDocument.objects.filter(
            source_kind=SearchSourceKind.PROCEDURAL_DEVELOPMENT,
            source_object_id=development.pk,
        )
    )
    return count


@transaction.atomic
def refresh_external_position(position: MatterExternalPosition) -> int:
    """Reproject one recorded opinion or piece of received feedback."""
    from app.search.child_indexing import indexable_positions, refresh_positions

    _hold_off_a_rebuild()
    count = refresh_positions(indexable_positions().filter(pk=position.pk))
    _recompute_vectors(
        SearchDocument.objects.filter(
            source_kind=SearchSourceKind.EXTERNAL_POSITION, source_object_id=position.pk
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
def refresh_document(document: Document) -> int:
    """Reproject one Document's own row: its title and its filenames (ENG-030).

    Called when the document is saved — created, renamed, moved, its current
    version changed — and when a version is added to it. One row, so it is
    cheap enough to run inside the write that caused it, which is what keeps a
    new upload findable by its name the moment its transaction commits.
    """
    from app.search.child_indexing import indexable_documents, refresh_documents

    _hold_off_a_rebuild()
    count = refresh_documents(indexable_documents().filter(pk=document.pk))
    if not count:
        # A document on a deleted Matter projects nothing; a row it had goes.
        SearchDocument.objects.filter(
            source_kind=SearchSourceKind.DOCUMENT, source_object_id=document.pk
        ).delete()
        return 0
    _recompute_vectors(
        SearchDocument.objects.filter(
            source_kind=SearchSourceKind.DOCUMENT, source_object_id=document.pk
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


class RebuildAlreadyRunning(RuntimeError):
    """Another full rebuild holds the rebuild lock; this one did not start."""


@contextlib.contextmanager
def _sole_rebuild() -> Iterator[None]:
    """At most one full rebuild at a time, in the whole database.

    A *session* advisory lock rather than a transaction one, because a rebuild
    is many transactions. PostgreSQL releases it when the session ends, so a
    rebuild whose process died cannot hold the next one off. A second rebuild
    does not wait for the first: it refuses, which is what an operator and the
    worker both want to hear.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [_LOCK_NAMESPACE, _REBUILD_OWNER])
        (acquired,) = cursor.fetchone()
    if not acquired:
        raise RebuildAlreadyRunning(
            "Otsinguindeksi täisehitus juba käib; teist korraga ei alustata."
        )
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [_LOCK_NAMESPACE, _REBUILD_OWNER])


def rebuild_is_running() -> bool:
    """Whether some session holds the rebuild lock right now."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
            "AND classid = %s AND objid = %s AND objsubid = 2 AND granted)",
            [_LOCK_NAMESPACE, _REBUILD_OWNER],
        )
        (running,) = cursor.fetchone()
    return bool(running)


#: Called after every committed batch of a full rebuild with the kind and the
#: number of sources in the batch. For tests that need a deterministic moment
#: inside a rebuild; production passes nothing.
BatchHook = Callable[[str, int], None]


def rebuild_all(
    *,
    batch_size: int = BATCH_SIZE,
    clear: bool = True,
    between_batches: BatchHook | None = None,
) -> RebuildResult:
    """Rebuild the whole projection from canonical records, into a new generation.

    **Readers see one complete generation at every moment.** A partially
    rebuilt index is the failure this function exists to prevent: it returns
    confident, silent, incomplete answers, and «vasteid ei leitud» looks the
    same whether the Matter does not exist or the rebuild died before reaching
    it (docs/adr/0013). That used to be guaranteed by emptying and refilling the
    table in one transaction; it is now guaranteed by *generations*. Readers read
    the active generation only (`app.search.generations`), this function fills
    the next one beside it, and the switch is one short transaction at the end.
    A rebuild that fails anywhere before that leaves the active generation
    exactly as it was.

    **Business writes are not held off for the rebuild's duration** (ENG-011).
    The single long transaction held the exclusive side of the refresh gate
    from start to finish, and every save that refreshes search waited for it:
    42–50 seconds at 38,000 rows in the audit, growing with the corpus. Now the
    exclusive side is held for one batch at a time — the time to build
    ``batch_size`` rows — and a write waiting on it waits for that batch, not
    for the rebuild.

    **A write during the rebuild reaches the generation that becomes active.**
    A targeted refresh writes every live generation, the building one included
    (`app.search.child_indexing._generations`). A batch reads canonical data
    under the exclusive side of the gate, so a write whose refresh already ran
    has committed before the batch reads, and a write that refreshes later
    rewrites the batch's row. The generation is created under the exclusive side
    too, so no writer can learn the live set before it exists and commit after.

    Steps, each its own transaction:

    1. discard what an earlier, unfinished rebuild left (`_discard_unfinished`);
    2. create the BUILDING generation;
    3. fill it, batch by batch, every source kind;
    4. activate it: BUILDING becomes ACTIVE and the old ACTIVE becomes RETIRED,
       together, under the exclusive side of the gate;
    5. delete the retired generation's rows, in batches, holding nothing.

    ``clear`` is accepted for callers and no longer changes anything: every
    rebuild is into an empty generation, so rows for sources that no longer
    exist cannot survive one whichever way it is called.
    """
    started = timezone.now()
    clock = _Clock()
    with _sole_rebuild():
        _discard_unfinished()
        generation = _begin_generation(clock)
        try:
            counts = _fill(
                generation.number,
                batch_size=batch_size,
                clock=clock,
                between_batches=between_batches,
            )
            documents = _activate(generation, clock)
        except BaseException as error:
            _fail(generation, error)
            raise
        _discard_generations(keep={generation.number})
    return RebuildResult(
        documents=documents,
        matters=counts["matters"],
        entries=counts["entries"],
        submissions=counts["submissions"],
        fragments=counts["fragments"],
        source_pages=counts["source_pages"],
        engagements=counts["engagements"],
        developments=counts["developments"],
        positions=counts["positions"],
        document_rows=counts["document_rows"],
        seconds=(timezone.now() - started).total_seconds(),
        index_version=INDEX_VERSION,
        generation=generation.number,
        longest_gate_seconds=clock.longest,
        swap_gate_seconds=clock.swap,
        batches=clock.batches,
        retried_batches=clock.retried,
    )


class _Clock:
    """How long each exclusive hold of the refresh gate lasted, for the report."""

    def __init__(self) -> None:
        self.longest = 0.0
        self.swap = 0.0
        self.batches = 0
        self.retried = 0

    @contextlib.contextmanager
    def hold(self, *, swap: bool = False) -> Iterator[None]:
        started = time.monotonic()
        try:
            yield
        finally:
            held = time.monotonic() - started
            self.longest = max(self.longest, held)
            if swap:
                self.swap = held
            else:
                self.batches += 1


def _begin_generation(clock: _Clock) -> Any:
    """Create the BUILDING generation, after every in-flight refresh has committed."""
    from django.db.models import Max

    from app.search.models import SearchGeneration, SearchGenerationState

    with transaction.atomic(), clock.hold():
        _hold_off_refreshes()
        highest = max(
            SearchGeneration.objects.aggregate(top=Max("number"))["top"] or 0,
            _by_generation().aggregate(top=Max("generation"))["top"] or 0,
            INITIAL_GENERATION,
        )
        return SearchGeneration.objects.create(
            number=highest + 1,
            state=SearchGenerationState.BUILDING,
            index_version=INDEX_VERSION,
            started_at=timezone.now(),
        )


def _fill(
    number: int, *, batch_size: int, clock: _Clock, between_batches: BatchHook | None
) -> dict[str, int]:
    """Every source kind, into generation ``number``, one short transaction per batch."""
    from app.search.child_indexing import (
        indexable_developments,
        indexable_documents,
        indexable_engagements,
        indexable_entries,
        indexable_fragments,
        indexable_positions,
        indexable_source_links,
        indexable_submissions,
        refresh_developments,
        refresh_documents,
        refresh_engagements,
        refresh_entries,
        refresh_fragments,
        refresh_positions,
        refresh_source_links,
        refresh_submissions,
    )

    plan = (
        ("matters", SearchSourceKind.MATTER, indexable_matters(), refresh_matters),
        ("entries", SearchSourceKind.ENTRY, indexable_entries(), refresh_entries),
        ("submissions", SearchSourceKind.SUBMISSION, indexable_submissions(), refresh_submissions),
        (
            "fragments",
            SearchSourceKind.DOCUMENT_FRAGMENT,
            indexable_fragments(),
            refresh_fragments,
        ),
        (
            "source_pages",
            SearchSourceKind.LEGACY_SOURCE_PAGE,
            indexable_source_links(),
            refresh_source_links,
        ),
        ("engagements", SearchSourceKind.ENGAGEMENT, indexable_engagements(), refresh_engagements),
        (
            "developments",
            SearchSourceKind.PROCEDURAL_DEVELOPMENT,
            indexable_developments(),
            refresh_developments,
        ),
        ("positions", SearchSourceKind.EXTERNAL_POSITION, indexable_positions(), refresh_positions),
        ("document_rows", SearchSourceKind.DOCUMENT, indexable_documents(), refresh_documents),
    )
    counts: dict[str, int] = {}
    for name, kind, queryset, refresh in plan:
        # Listed after the generation exists: a source committed before this
        # is here, and one committed after it is written into the generation
        # by its own refresh.
        identifiers = list(queryset.order_by("pk").values_list("pk", flat=True))
        total = 0
        for offset in range(0, len(identifiers), batch_size):
            chunk = identifiers[offset : offset + batch_size]
            total += _fill_batch(number, kind, queryset.filter(pk__in=chunk), chunk, refresh, clock)
            if between_batches is not None:
                between_batches(name, len(chunk))
        counts[name] = total
    return counts


#: How many times a batch is tried before the rebuild gives up. Each retry is
#: after a concurrent write got in the batch's way; the active generation is
#: untouched whatever happens, so giving up is safe and the rebuild can be run
#: again.
_BATCH_ATTEMPTS = 8


def _fill_batch(
    number: int, kind: str, queryset: QuerySet, chunk: list[Any], refresh: Any, clock: _Clock
) -> int:
    """One batch, under the exclusive side of the refresh gate.

    Retried, after a short pause, in the two cases where a concurrent business
    transaction gets in its way — and in both it is the batch that yields, never
    the business write:

    * **A source it read was deleted** before the row naming it was inserted:
      the insert fails its foreign key. A hard delete does not refresh the
      projection — the foreign key's cascade removes existing rows — so this is
      the one race the gate does not order. The retry reads the source as gone.
    * **A row lock it needs is held by a writer waiting on the gate**: the
      bounded wait (:func:`_bounded_lock_waits`) runs out, the batch rolls back
      and releases the gate, and the writer finishes.
    """
    for attempt in range(_BATCH_ATTEMPTS):
        try:
            with transaction.atomic(), clock.hold():
                _hold_off_refreshes()
                _bounded_lock_waits()
                # `.all()`: a fresh query each attempt. The refresh evaluates
                # what it is given, and a retry reading the previous attempt's
                # cached rows would insert the deleted source all over again.
                written = refresh(queryset.all(), generations=[number])
                if kind != SearchSourceKind.MATTER:
                    _recompute_vectors(
                        SearchDocument.objects.filter(
                            generation=number, source_kind=kind, source_object_id__in=chunk
                        )
                    )
                return int(written)
        except (IntegrityError, OperationalError) as error:
            retryable = isinstance(error, IntegrityError) or _sqlstate(error) in {
                _LOCK_NOT_AVAILABLE,
                _DEADLOCK_DETECTED,
            }
            if not retryable or attempt == _BATCH_ATTEMPTS - 1:
                raise
            clock.retried += 1
            time.sleep(min(0.05 * 2**attempt, 2.0))
    return 0  # pragma: no cover - the loop returns or raises


def _activate(generation: Any, clock: _Clock) -> int:
    """BUILDING becomes ACTIVE and the old ACTIVE becomes RETIRED, together.

    Under the exclusive side of the gate, so no refresh is between learning the
    live generations and committing; that makes the swap the only moment a
    business write waits for more than one batch, and it is two single-row
    updates long. Readers switch at its commit: each statement evaluates the
    active number once.
    """
    from app.search.models import SearchGeneration, SearchGenerationState

    rows = SearchDocument.objects.filter(generation=generation.number).count()
    now = timezone.now()
    with transaction.atomic(), clock.hold(swap=True):
        _hold_off_refreshes()
        SearchGeneration.objects.filter(state=SearchGenerationState.ACTIVE).update(
            state=SearchGenerationState.RETIRED, finished_at=now
        )
        SearchGeneration.objects.filter(pk=generation.pk).update(
            state=SearchGenerationState.ACTIVE, activated_at=now, rows=rows
        )
    generation.state = SearchGenerationState.ACTIVE
    return rows


def _fail(generation: Any, error: BaseException) -> None:
    """Mark a generation FAILED; the next rebuild deletes its rows. Never raises."""
    from app.search.freshness import describe_failure
    from app.search.models import SearchGeneration, SearchGenerationState

    generation.state = SearchGenerationState.FAILED
    try:
        with transaction.atomic():
            SearchGeneration.objects.filter(pk=generation.pk).update(
                state=SearchGenerationState.FAILED,
                finished_at=timezone.now(),
                last_error=describe_failure(error),
            )
    except Exception:  # pragma: no cover - only reachable with a dead connection
        return


def _discard_unfinished() -> None:
    """A BUILDING generation nobody is building is a crashed rebuild's: fail it.

    Only called while holding the rebuild lock, so no other rebuild can be the
    one filling it.
    """
    from app.search.models import SearchGeneration, SearchGenerationState

    SearchGeneration.objects.filter(state=SearchGenerationState.BUILDING).update(
        state=SearchGenerationState.FAILED, finished_at=timezone.now()
    )
    _discard_generations(keep=set())


#: Rows deleted per statement when a generation is discarded.
DISCARD_BATCH = 5000

#: How many finished generations' records are kept for the history. Their rows
#: are always deleted; this is only the bookkeeping.
KEPT_GENERATION_RECORDS = 20


def discard_dead_generations() -> int:
    """Delete the rows of every generation that is neither active nor building.

    What a rebuild does after its swap, exposed for an operator whose rebuild
    stopped between the swap and this step. Safe at any time: a live
    generation's rows are never touched.
    """
    return _discard_generations(keep=set())


def _by_generation() -> QuerySet[SearchDocument]:
    """Rows as the rebuild looks them up by generation: through the unique index.

    `search_one_row_per_source_and_generation` leads with `generation` and is
    partial on ``source_object_id IS NOT NULL``; a lookup that states the same
    condition can use it. There is deliberately no plain index on
    `generation` (`SearchDocument.Meta`), so without the condition every one
    of these would be a scan of the table.
    """
    return SearchDocument.objects.filter(source_object_id__isnull=False)


def _discard_generations(*, keep: set[int]) -> int:
    """Delete rows outside the live generations (and ``keep``), in batches, holding nothing.

    Retired and failed generations, and rows numbered for generations with no
    record at all — which is what `INITIAL_GENERATION`'s rows become once a
    generation-aware rebuild has run. No gate: a targeted refresh writes only
    live generations, so nothing here can collide with one.
    """
    from app.search.generations import live_generations
    from app.search.models import SearchGeneration, SearchGenerationState

    live = set(live_generations()) | keep
    removed = 0
    present = set(_by_generation().order_by().values_list("generation", flat=True).distinct())
    for number in sorted(present - live):
        while True:
            batch = list(
                _by_generation()
                .filter(generation=number)
                .order_by()
                .values_list("pk", flat=True)[:DISCARD_BATCH]
            )
            if not batch:
                break
            deleted, _ = SearchDocument.objects.filter(pk__in=batch).delete()
            removed += deleted
    # A row with no source id is one the indexer never writes and the
    # integrity check reports («Allika tunnus»); if one sits in a dead
    # generation it goes too, in one statement.
    orphans, _ = (
        SearchDocument.objects.filter(source_object_id__isnull=True)
        .exclude(generation__in=live)
        .delete()
    )
    removed += orphans
    finished = SearchGeneration.objects.filter(
        state__in=[SearchGenerationState.RETIRED, SearchGenerationState.FAILED]
    ).order_by("-number")
    stale = list(finished.values_list("pk", flat=True)[KEPT_GENERATION_RECORDS:])
    if stale:
        SearchGeneration.objects.filter(pk__in=stale).delete()
    return removed
