"""Substantive work on a Matter: what counts, and when it happened (docs/adr/0134).

**This module owns one question: did real work happen on this file, and when?**
It is the only definition of that. Three surfaces aggregate it, differently:

* **«Viimane tegevus»** (the register column and its sort) — the *latest*
  qualifying activity: :func:`annotate_last_activity` + :func:`activity_of`, and
  :func:`annotate_activity_date` for the ORDER BY;
* **«Muutusteta 30 p»** (`work_items.quiet_matters`, Minu asjad, Osakond's rail,
  Ülevaade) — the *age* of that latest activity;
* **Osakond «Teemades muudatusi · eelmine nädal»** — whether a Matter had *at
  least one* qualifying activity in a calendar window:
  :func:`work_activity_between`. Not «its latest activity falls in the window»:
  a file worked on last week and again today was worked on last week.

The shared thing is the vocabulary — which records count, on which date, for
which reader — not one aggregate expression.

What counts as work
-------------------
Each on its **own business date**, never a technical timestamp standing in for
an unknown one:

* a recorded closure — ``Matter.closed_at``;
* a sent submission — ``Submission.sent_at``;
* an authored entry — ``Entry.occurred_at``;
* a next action a **person** set or ended — ``NextAction.created_at`` /
  ``ended_at`` where ``created_by`` / ``ended_by`` is set;
* a dated, begun `Kaasamine` — ``MatterEngagement.occurred_on`` at its precision;
* a `+ Märge` recording something done — ``MatterProceduralDevelopment.occurred_on``
  at its precision, when that date was not after the day it was recorded
  (docs/adr/0124 §2: past or today is the record of something done; a day ahead
  is information or a plan, and the calendar reaching it later proves nothing);
* a published `Ülevaade / uudis` — ``MatterWebsiteOverview.published_on`` when
  known (docs/adr/0085);
* a dated `Väline seisukoht` — ``MatterExternalPosition.stated_on`` at its
  precision (docs/adr/0084): material somebody else stated, which a lawyer put on
  the file — not a Koja `Submission`, and labelled as its own basis;
* the date the Matter arrived — ``Matter.received_date``;
* for imported records, the OneNote page's own created and modified timestamps,
  which are source metadata captured from the archive.

And for a Matter created in this system with none of the above, its
``updated_at`` as a last resort for «Viimane tegevus» only (:func:`activity_of`'s
``NATIVE_RECORD``). That is a fallback for printing something true, not a
qualifying fact, so it decides no window.

What does not
-------------
A record with no business date of its own (an undated `Kaasamine`, `Märge` or
`Väline seisukoht`; a published `Ülevaade` whose day nobody wrote down; a
planned or cancelled one): it stays on the file and in `Teema käik`, and moves
no date here. Administrative writes: a title or metadata edit, an owner or
collaborator change, a standalone `Hetkeseis` transition (it records when
Juristid was told, not when the procedure moved — `MatterStageEpisode`), a
standalone document upload (it files bytes; the act it evidences is its own
record). Machine writes: ``ImportBatch.started_at``,
``CurrentRegisterState.observed_at``, search-index refreshes, enrichment, a next
action an importer set, and — for anything imported — ``Matter.updated_at``.
A removed record does not exist here at all (docs/adr/0102).

**Should a new record type affect «Viimane tegevus», «Muutusteta 30 p» or
Osakond's count?** Only if it is accepted as substantive work on the Matter *and*
carries a defensible business date of its own. Then it is added here — to
:class:`_WorkSources`, :func:`annotate_last_activity`, :func:`activity_of`,
:func:`annotate_activity_date` and :func:`work_activity_between` — and nowhere
else.

Four questions that are not this one
------------------------------------
* **When was this row written?** ``Matter.updated_at`` — record mutation, sorted
  as «Viimati muudetud» (``?jarjestus=updated``), never as «Viimane tegevus».
* **What belongs in the history?** `Teema käik` — ``timeline.TIMELINE_EVENT_TYPES``
  and the projected records. It shows a `Hetkeseis` transition and a document
  arriving, which are history and not work; Osakond used to borrow that list,
  which is the drift this module replaced (RULE-02).
* **What did the audit log record?** `ChangeEvent` — every write, by whom, when
  it was written. Planning and administrative events live there by design.
* **How much work is in which state?** Statistika «Tegevus»
  (`app.reporting.selectors.activity`) — open, overdue and due-for-review steps
  and entry volumes over a reporting window.

Rules that keep it honest
-------------------------
**The latest fact wins**, not the most canonical one: a OneNote page modified in
2019 on a Matter closed in 2021 last saw activity in 2021, and the reverse
(brief 56). **Precedence breaks ties only** — when two facts fall on the same day
the more canonical one names the basis; it can never make an older date win.

**A period is not a day.** A `Kaasamine`, a `Märge` or a `Väline seisukoht` may
be recorded to a month, a quarter or a year (docs/adr/0082, 0121, 0122); what is
stored is the period's anchor. «Viimane tegevus» orders on the anchor and
:attr:`MatterActivityFact.date_precision` carries what it means, so no surface
prints `01.10.2026` for *oktoobris*. A window bounded in days never holds a
period (`dates.period_in_window`, docs/adr/0122 §2), so an approximate record
counts for no week.

**Scoped before arithmetic.** Every source is read through the visibility its
own ``visible_to`` applies — :func:`_readable` is that rule with the scope
resolved once per call rather than once per source — so a record restricted
below a visible Matter moves no date, no sort position, no quiet state and no
count for a reader who may not see it.

**No queries per row.** :func:`annotate_last_activity` is one correlated
subquery per fact for the whole page and :func:`activity_of` reads attributes;
:func:`work_activity_between` is one ``EXISTS`` per source. Both are applied by
the surfaces that need them — the register through
``selectors.matter_list_queryset``, which every row of
``matters/partials/matter_table.html`` comes through, so a forgotten annotation
raises rather than printing a wrong date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from django.db.models import (
    Case,
    CharField,
    DateField,
    Exists,
    IntegerField,
    Max,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    When,
)
from django.db.models.functions import Coalesce, Greatest, TruncDate
from django.utils import timezone

from app.core.authorization import apply as apply_scope
from app.core.authorization import child_scope_q, scope_for_user
from app.core.dates import start_of_local_day
from app.legacy_import.source_pages import MatterSourcePage, SourceRelationshipKind
from app.matters.enums import MatterOrigin, WebsiteOverviewStatus
from app.matters.models import (
    Entry,
    Matter,
    MatterEngagement,
    MatterExternalPosition,
    MatterProceduralDevelopment,
    MatterWebsiteOverview,
)
from app.submissions.models import Submission
from app.workflow.dates import format_at_precision, is_approximate
from app.workflow.enums import DatePrecision
from app.workflow.lateness import APPROXIMATE_PRECISIONS
from app.workflow.models import NextAction


class ActivityBasis:
    """Why the date exists. Exposed so a future UI can say so, and so a
    surprising value is debuggable without reading this module."""

    CLOSURE = "CLOSURE"
    SUBMISSION = "SUBMISSION"
    ENTRY = "ENTRY"
    NEXT_ACTION = "NEXT_ACTION"
    ENGAGEMENT = "ENGAGEMENT"
    DEVELOPMENT = "DEVELOPMENT"
    WEBSITE_OVERVIEW = "WEBSITE_OVERVIEW"
    EXTERNAL_POSITION = "EXTERNAL_POSITION"
    RECEIVED = "RECEIVED"
    ONENOTE_MODIFIED = "ONENOTE_MODIFIED"
    ONENOTE_CREATED = "ONENOTE_CREATED"
    NATIVE_RECORD = "NATIVE_RECORD"


#: How a basis is described to a reader, in Estonian.
BASIS_LABELS: dict[str, str] = {
    ActivityBasis.CLOSURE: "Teema suleti",
    ActivityBasis.SUBMISSION: "Arvamus saadetud",
    ActivityBasis.ENTRY: "Sissekanne",
    ActivityBasis.NEXT_ACTION: "Järgmiseks muudetud",
    ActivityBasis.ENGAGEMENT: "Kaasamine",
    ActivityBasis.DEVELOPMENT: "Märge",
    ActivityBasis.WEBSITE_OVERVIEW: "Ülevaade avaldatud",
    ActivityBasis.EXTERNAL_POSITION: "Väline seisukoht",
    ActivityBasis.RECEIVED: "Saabus",
    ActivityBasis.ONENOTE_MODIFIED: "OneNote'i lehte muudetud",
    ActivityBasis.ONENOTE_CREATED: "OneNote'i leht loodud",
    ActivityBasis.NATIVE_RECORD: "Kirjet muudetud",
}

#: Tie-break order, most canonical first. Used **only** when two facts fall on
#: the same day; it can never select an earlier date over a later one. The three
#: bases that may be a period (`ENGAGEMENT`, `DEVELOPMENT`, `EXTERNAL_POSITION`)
#: rank below every basis that is always a day, so a day that ties with a period
#: names the day: something really did happen on it.
BASIS_PRECEDENCE: tuple[str, ...] = (
    ActivityBasis.CLOSURE,
    ActivityBasis.SUBMISSION,
    ActivityBasis.ENTRY,
    ActivityBasis.WEBSITE_OVERVIEW,
    ActivityBasis.NEXT_ACTION,
    ActivityBasis.ENGAGEMENT,
    ActivityBasis.DEVELOPMENT,
    ActivityBasis.EXTERNAL_POSITION,
    ActivityBasis.RECEIVED,
    ActivityBasis.ONENOTE_MODIFIED,
    ActivityBasis.ONENOTE_CREATED,
    ActivityBasis.NATIVE_RECORD,
)

#: Which linked pages may speak for the Matter's chronology. The same two as the
#: PolicyArea enrichment, and for the same reason: a ``RELATED`` page can
#: legitimately carry later work, a ``BACKGROUND`` page is material *about* the
#: subject and its edit date says nothing about this file (brief 59).
CHRONOLOGY_RELATIONSHIPS: tuple[str, ...] = (
    SourceRelationshipKind.PRIMARY.value,
    SourceRelationshipKind.RELATED.value,
)

#: The annotation names. Prefixed, because they land on ``Matter`` beside real
#: field names and a collision would be silent.
ANNOTATIONS: tuple[str, ...] = (
    "activity_entry_at",
    "activity_submission_at",
    "activity_action_created_at",
    "activity_action_ended_at",
    "activity_page_modified_at",
    "activity_page_created_at",
    "activity_engagement_on",
    "activity_engagement_precision",
    "activity_development_on",
    "activity_development_precision",
    "activity_overview_on",
    "activity_position_on",
    "activity_position_precision",
)

#: Which annotation carries the precision of each basis that may be a period.
_PRECISION_OF: dict[str, str] = {
    ActivityBasis.ENGAGEMENT: "activity_engagement_precision",
    ActivityBasis.DEVELOPMENT: "activity_development_precision",
    ActivityBasis.EXTERNAL_POSITION: "activity_position_precision",
}


@dataclass(frozen=True)
class MatterActivityFact:
    """One answer to "when did work last happen here", with its reason.

    Read-only and derived. There is no model, no column and no migration behind
    it: this is an interpretation of facts already stored, in the same sense
    ``CurrentRegisterState`` is an interpretation of source rows.
    """

    occurred_on: date
    basis: str
    #: How exactly :attr:`occurred_on` is known.
    #:
    #: `EXACT` for every basis that is always a day — a closure, a sent
    #: submission, an entry, a next action, a published overview, an arrival
    #: date, a OneNote timestamp — and not as a default standing in for an
    #: unknown answer. A `Kaasamine`, a `Märge` and a `Väline seisukoht` may be
    #: recorded to a month, a quarter or a year (docs/adr/0082, 0121), and then
    #: the date above is the period's **anchor** — a place in the ordering, never
    #: a day to print. :attr:`display_date` is what a surface renders.
    date_precision: str = DatePrecision.EXACT.value

    @property
    def label(self) -> str:
        return BASIS_LABELS.get(self.basis, "")

    @property
    def display_date(self) -> str:
        """*Viimane tegevus*, written the way it is actually known.

        Identical to the old `{{ fact.occurred_on|date:"j.n.Y" }}` for every
        exact date — `format_estonian_date` is that format — and *oktoober
        2026* where the engagement behind it was recorded to a month
        (docs/adr/0079 §3).
        """
        return format_at_precision(self.occurred_on, self.date_precision)

    @property
    def is_approximate(self) -> bool:
        return is_approximate(self.date_precision)

    @property
    def compact_display(self) -> str:
        """The same fact for a surface that has no room for a year.

        `12.5` for a day, which is what *Viimati muudetud* on Minu töö has
        always printed, and the period spelled out for a month, a quarter or a
        year — «oktoober 2025», not `10.25`, which two numbers under a column of
        days read as a day (docs/adr/0120, UQ-09).

        Never the anchor as a day. `01.10` under a heading that means "when
        something last happened" is a claim about the first of October.
        """
        if self.is_approximate:
            return self.display_date
        return f"{self.occurred_on.day}.{self.occurred_on.month}"

    @property
    def is_source_derived(self) -> bool:
        """Whether the date came from archived source metadata rather than
        from something this system recorded."""
        return self.basis in (ActivityBasis.ONENOTE_MODIFIED, ActivityBasis.ONENOTE_CREATED)


def _latest(queryset: QuerySet[Any], field: str) -> Subquery:
    """``Max(field)`` for the Matters in the outer query, one subquery total.

    ``order_by()`` is not decoration. Every model here declares ``Meta.ordering``
    and Django puts an ordering column into the ``GROUP BY`` of an aggregating
    subquery, which turns one row per Matter into one row per ordering value —
    a wrong maximum, silently.
    """
    return Subquery(
        queryset.filter(matter=OuterRef("pk"))
        .order_by()
        .values("matter")
        .annotate(latest=Max(field))
        .values("latest")[:1]
    )


def _latest_where_any(queryset: QuerySet[Any], field: str) -> Case:
    """:func:`_latest` for a source only a few Matters have, asked only of those.

    A published `Ülevaade` is on a handful of files. Its table is small enough
    that PostgreSQL's stock costs prefer scanning all of it to an index probe,
    and it did so once per Matter, twice (select list and sort key): ~100 ms
    of «Viimane tegevus» ordering on a 5,016-Matter clone, for a date most
    Matters lack. The ``IN`` is one hashed scan per query; the correlated
    maximum then runs only where it can find something.
    """
    return Case(
        When(pk__in=queryset.values("matter"), then=_latest(queryset, field)),
        default=None,
        output_field=DateField(),
    )


@dataclass(frozen=True)
class _WorkSources:
    """Each record type that can be substantive work, read for one reader.

    Every queryset is already reader-scoped (:func:`_readable`) and already
    limited to the rows that count — a person's step, a dated begun round, a
    `Märge` that recorded something done, a published overview with its day —
    so the latest-activity annotations and the window question read one
    definition and cannot drift apart. Each record's business date is named
    beside it in :func:`annotate_last_activity` and :func:`work_activity_between`.
    """

    entries: QuerySet[Any]
    submissions: QuerySet[Any]
    actions_set: QuerySet[Any]
    actions_ended: QuerySet[Any]
    engagements: QuerySet[Any]
    developments: QuerySet[Any]
    overviews: QuerySet[Any]
    positions: QuerySet[Any]
    pages: QuerySet[Any]


def _readable(model: Any, scope: Any) -> QuerySet[Any]:
    """``model.objects.visible_to(user)``, with ``scope`` resolved once by the caller.

    The same two rules every child model's ``visible_to`` states: the child
    visibility predicate (`app.core.authorization`), and — for a record a lawyer
    can take off the file — no removed row (docs/adr/0102). Resolved here rather
    than by calling each ``visible_to`` because that asks ``scope_for_user``,
    and with it the break-glass lookup, once per source on every page
    (Agent-F brief 31). ``tests/test_work_activity.py`` holds each source to its
    model's own ``visible_to`` through the hidden-record cases.
    """
    scoped = apply_scope(model._default_manager.all(), child_scope_q(model, scope))
    if any(field.name == "removed_at" for field in model._meta.get_fields()):
        scoped = scoped.filter(removed_at__isnull=True)
    return scoped


def _work_sources(user: Any, today: date | None = None) -> _WorkSources:
    day = today or timezone.localdate()
    scope = scope_for_user(user)
    actions = _readable(NextAction, scope)
    return _WorkSources(
        entries=_readable(Entry, scope),
        # Any submission that carries a send date. A submission later withdrawn
        # or superseded was still genuinely sent on that day, and the withdrawal
        # does not un-happen the work.
        submissions=_readable(Submission, scope).filter(sent_at__isnull=False),
        # A step counts only when a person set or ended it: `created_by` is what
        # tells a lawyer's choice from an importer's timestamp (brief 55, 64).
        actions_set=actions.filter(created_by__isnull=False),
        actions_ended=actions.filter(ended_by__isnull=False),
        # Only a *dated* engagement, and only one that has begun. `created_at` is
        # deliberately not offered: somebody recording a 2019 consultation today
        # would otherwise move the file's last activity to today (Agent-F brief
        # 29). A round may be dated ahead since docs/adr/0121 §3 — planned, not
        # held — and a period's anchor is its first day, so `occurred_on <=
        # today` is «its period has begun».
        engagements=_readable(MatterEngagement, scope).filter(
            occurred_on__isnull=False, occurred_on__lte=day
        ),
        # **A `Märge` that recorded something done**: dated, and not dated after
        # the day it was recorded (docs/adr/0124 §2–§3: past or today is the
        # record of something done; a day ahead is information or a plan). The
        # anchor is a period's first day, so `occurred_on <= recorded day` is
        # exactly `not dates.period_starts_after(occurred_on, precision,
        # day=recorded day)`. Judged against the recording day and not against
        # today, so a future `Märge` never becomes proof of work because the
        # calendar caught up with it; a step it set counts through the step.
        # A correction does not reclassify it (docs/adr/0124 §4).
        developments=_readable(MatterProceduralDevelopment, scope).filter(
            occurred_on__isnull=False, occurred_on__lte=TruncDate("created_at")
        ),
        # Published, with the day it was published. A page published on a day
        # nobody wrote down is a real record with no business date, and a
        # planned or cancelled one is no publication (docs/adr/0085, 0089 §8).
        overviews=_readable(MatterWebsiteOverview, scope).filter(
            status=WebsiteOverviewStatus.PUBLISHED,
            published_on__isnull=False,
            published_on__lte=day,
        ),
        # Dated, on the day the other party stated it. Never `created_at`.
        positions=_readable(MatterExternalPosition, scope).filter(
            stated_on__isnull=False, stated_on__lte=day
        ),
        # Source pages have no visibility of their own and are reached through
        # the Matter, which the reader already sees, so they need no scope. A
        # `BACKGROUND` page is material about the subject, not this file's work.
        pages=MatterSourcePage.objects.filter(relationship_kind__in=CHRONOLOGY_RELATIONSHIPS),
    )


def _latest_precision(queryset: QuerySet[Any], date_field: str, precision_field: str) -> Case:
    """The precision of the very row :func:`_latest` took its date from.

    `_latest` cannot answer this: ``Max`` over one column says nothing about a
    second. Ordered by the date descending, then **exact first** — two records
    anchored on one day, one naming the day and one the month, are both true
    and the day is the more informative — then ``-id`` so identical requests
    never flicker between two spellings of the same date.

    Asked only of Matters with a row that is not ``EXACT``; elsewhere the answer
    is ``EXACT`` and the ``NULL`` the guard leaves says so (:func:`activity_of`).
    Few rows are anything else, and the ordered lookup — through the reader's
    scope, once for every Matter on an activity-sorted page — was a third of
    what the three sources added to «Viimane tegevus» ordering for a reader.
    """
    latest = Subquery(
        queryset.filter(matter=OuterRef("pk"))
        .annotate(
            _exact_first=Case(
                When(
                    **{f"{precision_field}__in": (DatePrecision.EXACT, DatePrecision.INFERRED)},
                    then=0,
                ),
                default=1,
                output_field=IntegerField(),
            )
        )
        .order_by(f"-{date_field}", "_exact_first", "-id")
        .values(precision_field)[:1]
    )
    not_exact = queryset.exclude(**{precision_field: DatePrecision.EXACT}).values("matter")
    return Case(When(pk__in=not_exact, then=latest), default=None, output_field=CharField())


def annotate_last_activity(queryset: QuerySet[Matter], user: Any) -> QuerySet[Matter]:
    """Attach the latest of every work fact to the queryset as a subquery annotation.

    One correlated subquery per fact for the whole page, each over a source
    :func:`_work_sources` has already scoped to this reader and limited to what
    counts, so a restricted record announces nothing through the date column
    to somebody who cannot open it.
    """
    sources = _work_sources(user)
    return queryset.annotate(
        activity_entry_at=_latest(sources.entries, "occurred_at"),
        activity_submission_at=_latest(sources.submissions, "sent_at"),
        activity_action_created_at=_latest(sources.actions_set, "created_at"),
        activity_action_ended_at=_latest(sources.actions_ended, "ended_at"),
        activity_page_modified_at=_latest(sources.pages, "source_page__source_modified_at"),
        activity_page_created_at=_latest(sources.pages, "source_page__source_created_at"),
        activity_engagement_on=_latest(sources.engagements, "occurred_on"),
        activity_engagement_precision=_latest_precision(
            sources.engagements, "occurred_on", "occurred_on_precision"
        ),
        activity_development_on=_latest(sources.developments, "occurred_on"),
        activity_development_precision=_latest_precision(
            sources.developments, "occurred_on", "occurred_on_precision"
        ),
        activity_overview_on=_latest_where_any(sources.overviews, "published_on"),
        activity_position_on=_latest(sources.positions, "stated_on"),
        activity_position_precision=_latest_precision(
            sources.positions, "stated_on", "stated_on_precision"
        ),
    )


def _as_date(value: date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    return value


def activity_of(matter: Matter) -> MatterActivityFact | None:
    """The latest known activity, or ``None`` when nothing is known.

    Reads annotations and columns only — it never queries — so it is safe to
    call once per row. Call :func:`annotate_last_activity` on the queryset
    first; without it this raises rather than quietly issuing six queries per
    Matter and looking fine in development.

    ``None`` is a real answer. An archive row with no dates at all has no known
    activity, and printing today, or the import date, or a dash that looks like
    a date would each be an invention.
    """
    missing = [name for name in ANNOTATIONS if not hasattr(matter, name)]
    if missing:
        raise ValueError(
            "activity_of needs annotate_last_activity on the queryset; "
            f"missing {', '.join(missing)}."
        )

    candidates: list[tuple[date, str]] = []

    def offer(value: date | datetime | None, basis: str) -> None:
        moment = _as_date(value)
        if moment is not None:
            candidates.append((moment, basis))

    # A recorded closure is a real recorded fact. Nothing here invents one for
    # an archive row that has none (brief 57, 69).
    offer(matter.closed_at, ActivityBasis.CLOSURE)
    offer(matter.activity_submission_at, ActivityBasis.SUBMISSION)  # type: ignore[attr-defined]
    offer(matter.activity_entry_at, ActivityBasis.ENTRY)  # type: ignore[attr-defined]
    offer(matter.activity_action_created_at, ActivityBasis.NEXT_ACTION)  # type: ignore[attr-defined]
    offer(matter.activity_action_ended_at, ActivityBasis.NEXT_ACTION)  # type: ignore[attr-defined]
    # Asking members what they think is real work on the file, so a dated
    # engagement competes on its date like everything else here. It gets no
    # priority: an Entry written after it still wins (brief 30).
    offer(matter.activity_engagement_on, ActivityBasis.ENGAGEMENT)  # type: ignore[attr-defined]
    # A `+ Märge` that recorded something done, a published overview and an
    # external position, each on its own business date (docs/adr/0134).
    offer(matter.activity_development_on, ActivityBasis.DEVELOPMENT)  # type: ignore[attr-defined]
    offer(matter.activity_overview_on, ActivityBasis.WEBSITE_OVERVIEW)  # type: ignore[attr-defined]
    offer(matter.activity_position_on, ActivityBasis.EXTERNAL_POSITION)  # type: ignore[attr-defined]
    # A real business date, and a legitimate fallback — but never the answer
    # when something later is known, which the maximum below guarantees
    # (brief 61).
    offer(matter.received_date, ActivityBasis.RECEIVED)
    # Source metadata from the archive, not import time. `modified` records the
    # last known edit on the archived page and is therefore the better of the
    # two; `created` is the fallback. Both are offered and the later one wins,
    # which reduces to exactly that preference on a coherent page and does the
    # sane thing on an incoherent one (brief 58, 60).
    offer(matter.activity_page_modified_at, ActivityBasis.ONENOTE_MODIFIED)  # type: ignore[attr-defined]
    offer(matter.activity_page_created_at, ActivityBasis.ONENOTE_CREATED)  # type: ignore[attr-defined]

    if not candidates and matter.origin == MatterOrigin.NATIVE:
        # Last resort, and only here. For a Matter created in this system the
        # system *is* the authoritative record, so "the row was last touched
        # then" is the best available statement about the work. For an imported
        # row it is a statement about the importer (brief 62, 64).
        offer(matter.updated_at, ActivityBasis.NATIVE_RECORD)

    if not candidates:
        return None

    latest = max(moment for moment, _ in candidates)
    bases = {basis for moment, basis in candidates if moment == latest}
    for basis in BASIS_PRECEDENCE:
        if basis in bases:
            # The precision travels with the date, and only from a basis that
            # may be a period; its annotation is the precision of the very row
            # whose date won. Where a day ties with an approximate record,
            # precedence picks the day and the date stays a day — correctly:
            # something really did happen on it.
            precision = DatePrecision.EXACT.value
            if basis in _PRECISION_OF:
                precision = getattr(matter, _PRECISION_OF[basis], "") or DatePrecision.EXACT.value
            return MatterActivityFact(occurred_on=latest, basis=basis, date_precision=precision)
    return None


#: The annotation :func:`annotate_activity_date` writes, and the name
#: ``?jarjestus=viimane_uusim`` orders on. Prefixed like :data:`ANNOTATIONS`,
#: and for the same reason.
ACTIVITY_DATE = "last_activity_on"


def annotate_activity_date(queryset: QuerySet[Matter]) -> QuerySet[Matter]:
    """The date :func:`activity_of` would return, computed by the database.

    The SQL twin of the ``max(candidates)`` above, so *Viimane tegevus* can be
    sorted on without reading a hundred rows into Python and sorting them after
    the page boundary has already been drawn. Both readings offer the same
    facts and both take the latest; ``tests/test_register_interactive_columns.py``
    holds them against each other row by row.

    Three things this does **not** do, each of them the whole point of
    :mod:`app.matters.activity`:

    * It never falls back to ``updated_at`` for an imported row. The ``Case``
      below is reached only when nothing else is known *and* the record was
      created here, which is exactly :func:`activity_of`'s last-resort branch —
      for an imported row that timestamp is a statement about the importer.
    * It never invents a date. All-NULL in means NULL out, and NULL renders an
      em dash and sorts last in both directions.
    * It sorts an approximate `Kaasamine`, `Märge` or `Väline seisukoht` on its
      anchor, which is what :func:`activity_of` compares too, so the two readings still agree row by
      row. The *spelling* of the date is not this function's business — it
      orders, it does not render (docs/adr/0079 §2).
    * It widens nothing. Every value it reads is either a Matter column this
      reader already sees or one of :data:`ANNOTATIONS`, which
      :func:`annotate_last_activity` has already scoped to this reader.

    ``Greatest`` is PostgreSQL's ``GREATEST``, which **ignores NULLs and is NULL
    only when every argument is** — the behaviour ``max()`` has over the
    non-``None`` candidates above. Django's own documentation warns that MySQL,
    SQLite and Oracle return NULL if any argument is; this application runs on
    PostgreSQL and on nothing else (config/settings.py, docker-compose.yml).

    ``TruncDate`` reads the active timezone, which is what
    :func:`_as_date`'s ``timezone.localtime`` does, so a submission sent at
    01:30 Tallinn time lands on the same day in both readings.
    """
    return queryset.annotate(
        **{
            ACTIVITY_DATE: Coalesce(
                Greatest(
                    TruncDate("closed_at"),
                    TruncDate("activity_submission_at"),
                    TruncDate("activity_entry_at"),
                    TruncDate("activity_action_created_at"),
                    TruncDate("activity_action_ended_at"),
                    "activity_engagement_on",
                    "activity_development_on",
                    "activity_overview_on",
                    "activity_position_on",
                    "received_date",
                    TruncDate("activity_page_modified_at"),
                    TruncDate("activity_page_created_at"),
                    output_field=DateField(),
                ),
                Case(
                    When(origin=MatterOrigin.NATIVE, then=TruncDate("updated_at")),
                    default=None,
                    output_field=DateField(),
                ),
                output_field=DateField(),
            )
        }
    )


def work_activity_between(
    queryset: QuerySet[Matter], user: Any, start: date, end: date
) -> QuerySet[Matter]:
    """The Matters in ``queryset`` with **at least one** qualifying work activity
    on a local day from ``start`` to ``end``, for this reader.

    Osakond's «Teemades muudatusi · eelmine nädal» (docs/adr/0134). Existence,
    not the latest activity: a Matter worked on last week and again today was
    worked on last week, and «its latest activity falls in the window» would say
    it was not.

    The same sources :func:`annotate_last_activity` reads, each on the same
    business date, through one ``EXISTS`` per source:

    * moments (an entry, a send, a person's step set or ended, a OneNote page)
      as a half-open range from the first moment of ``start`` to the first
      moment after ``end`` — the same local days, and a range their indexes can
      answer (`app.core.dates.start_of_local_day`);
    * dated records at **day** precision only. A window bounded in days never
      holds a period — `dates.period_in_window`, docs/adr/0122 §2 — so a round,
      a `Märge` or a position known only to a month counts for no week;
    * the Matter's own closure and arrival date, on the Matter row.

    No ``NATIVE_RECORD`` fallback: that prints something true when nothing else
    is known, and is not a qualifying fact.
    """
    sources = _work_sources(user)
    since, until = start_of_local_day(start), start_of_local_day(end + timedelta(days=1))
    day_precision = ~Q(occurred_on_precision__in=APPROXIMATE_PRECISIONS)

    def happened(rows: QuerySet[Any], **window: Any) -> Exists:
        return Exists(rows.filter(matter=OuterRef("pk"), **window))

    return queryset.filter(
        Q(closed_at__gte=since, closed_at__lt=until)
        | Q(received_date__gte=start, received_date__lte=end)
        | happened(sources.entries, occurred_at__gte=since, occurred_at__lt=until)
        | happened(sources.submissions, sent_at__gte=since, sent_at__lt=until)
        | happened(sources.actions_set, created_at__gte=since, created_at__lt=until)
        | happened(sources.actions_ended, ended_at__gte=since, ended_at__lt=until)
        | happened(
            sources.engagements.filter(day_precision),
            occurred_on__gte=start,
            occurred_on__lte=end,
        )
        | happened(
            sources.developments.filter(day_precision),
            occurred_on__gte=start,
            occurred_on__lte=end,
        )
        | happened(sources.overviews, published_on__gte=start, published_on__lte=end)
        | happened(
            sources.positions.filter(~Q(stated_on_precision__in=APPROXIMATE_PRECISIONS)),
            stated_on__gte=start,
            stated_on__lte=end,
        )
        | happened(
            sources.pages,
            source_page__source_modified_at__gte=since,
            source_page__source_modified_at__lt=until,
        )
        | happened(
            sources.pages,
            source_page__source_created_at__gte=since,
            source_page__source_created_at__lt=until,
        )
    )


def activity_for_matter(matter: Matter, user: Any) -> MatterActivityFact | None:
    """The single-object convenience, for a Matter page rather than a list.

    One query, not six: it re-reads the same Matter through the same annotated
    queryset. Never call this in a loop — that is what
    :func:`annotate_last_activity` is for.
    """
    annotated = annotate_last_activity(Matter.objects.filter(pk=matter.pk), user).first()
    return activity_of(annotated) if annotated is not None else None
