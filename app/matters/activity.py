"""What actually last happened on a Matter, as opposed to when a row was written.

The register list has a *Viimane tegevus* column and it renders
``Matter.updated_at``. For a Matter somebody is working on today that is close
enough to true. For the two thousand imported ones it is simply wrong: their
``updated_at`` is the moment the 2026 cutover touched the row. A file whose last
real activity was a OneNote page edited in 2018 reads as though a lawyer had
been on it this morning, and a column that says that about most of the register
is worse than no column.

The fix is not to rewrite the timestamp. ``updated_at``, ``created_at`` and
``closed_at`` are records of what happened to the *row*, they are correct, and
other things depend on them. What was missing is a second, derived answer to a
different question — *when did work last happen on this file* — computed from
facts that are about the work.

What counts as activity
-----------------------
A recorded closure. An authored entry. A submission that went out. A next action
a person set or ended. A dated `Kaasamine` engagement. The date the matter
arrived. And, for imported records, the OneNote page's own created and modified
timestamps, which are source metadata captured from the archive rather than
anything this system did.

What does not
-------------
``ImportBatch.started_at``, ``CurrentRegisterState.observed_at``, search-index
refreshes, ``MatterEngagement.created_at`` on an engagement whose own date is
unknown, and — for anything imported — ``Matter.updated_at``. Those are
processing and data-entry times. Calling one of them activity is the exact mistake this module
exists to correct, and there is a regression test for the 2026-import case.

Two rules that look similar and are not
---------------------------------------
**The latest fact wins.** Not the most canonical one. A OneNote page modified in
2019 on a Matter closed in 2021 last saw activity in 2021; a Matter closed in
2019 whose related page was still being edited in 2021 last saw activity in
2021. A fixed source-priority list would answer one of those two wrongly
(brief 56).

**Precedence breaks ties only.** When two facts fall on the same day the more
canonical one names the basis, so the answer is stable across runs. It can never
make an older date win.

One of the nine facts may not be a day
--------------------------------------
Since docs/adr/0082 a `Kaasamine` may be recorded to a month, a quarter or a
year, and what is stored is then the period's **anchor** — its first day. This
module orders on that anchor, which is exactly what an anchor is for, and
:attr:`MatterActivityFact.date_precision` carries what the anchor means so that
no surface prints `01.10.2026` for a consultation somebody remembers as
*oktoobris*. :attr:`MatterActivityFact.display_date` is the only supported way
to render the date, and it is byte-identical to the old
``{{ fact.occurred_on|date:"j.n.Y" }}`` for every exact one.

A next action counts only when a person set or ended it
-------------------------------------------------------
``NextAction.created_at`` is business activity when a lawyer chose the next step
and an import timestamp when a machine did. The distinguishing fact is already
stored: ``created_by``. Without this rule the ``JÄRGMISEKS`` enrichment would
stamp today's date onto every Matter it touched and call it activity — the same
error as ``updated_at``, arriving through a different column (brief 55, 64).

Reading it costs no queries per row
-----------------------------------
:func:`annotate_last_activity` puts every fact on the queryset as a subquery
annotation and :func:`activity_of` reads attributes. A helper that queried per
Matter would be fine on a Matter page and would put six queries on every row of
a hundred-row register (brief 65).

Not wired to the template yet
-----------------------------
Where it is wired
-----------------
:func:`annotate_last_activity` is applied inside
``app.matters.selectors.matter_list_queryset``, which every surface rendering
``matters/partials/matter_table.html`` already comes through, and the partial
reads the fact through the ``last_activity`` template filter. That placement is
deliberate: :func:`activity_of` refuses to answer without the annotations, so a
surface that forgot them would raise rather than quietly show a wrong date —
and putting the annotation at the one shared chokepoint means forgetting is not
possible.

``Matter.updated_at`` is still what ``?jarjestus=updated`` sorts by, and that
option is labelled *Viimati muudetud* rather than *Viimane tegevus* so the two
are not read as the same fact.

Sorting the column itself
-------------------------
``?jarjestus=viimane_uusim`` and ``?jarjestus=viimane_vanim`` order on
:func:`annotate_activity_date`, which is this module's own rule stated in SQL —
the same nine facts, the same maximum, the same refusal to fall back to
``updated_at`` for an imported row. A sort that read ``updated_at`` while the
column rendered this would put the rows in an order the dates on screen
contradict, which is the failure the column exists to prevent, arriving through
the heading instead of through the cell (docs/adr/0071).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from django.db.models import (
    Case,
    DateField,
    IntegerField,
    Max,
    OuterRef,
    QuerySet,
    Subquery,
    When,
)
from django.db.models.functions import Coalesce, Greatest, TruncDate
from django.utils import timezone

from app.core.authorization import apply as apply_scope
from app.core.authorization import child_visibility_q, scope_for_user
from app.legacy_import.source_pages import MatterSourcePage, SourceRelationshipKind
from app.matters.enums import MatterOrigin
from app.matters.models import Entry, Matter, MatterEngagement
from app.submissions.models import Submission
from app.workflow.dates import format_at_precision, is_approximate
from app.workflow.enums import DatePrecision
from app.workflow.models import NextAction


class ActivityBasis:
    """Why the date exists. Exposed so a future UI can say so, and so a
    surprising value is debuggable without reading this module."""

    CLOSURE = "CLOSURE"
    SUBMISSION = "SUBMISSION"
    ENTRY = "ENTRY"
    NEXT_ACTION = "NEXT_ACTION"
    ENGAGEMENT = "ENGAGEMENT"
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
    ActivityBasis.RECEIVED: "Saabus",
    ActivityBasis.ONENOTE_MODIFIED: "OneNote'i lehte muudetud",
    ActivityBasis.ONENOTE_CREATED: "OneNote'i leht loodud",
    ActivityBasis.NATIVE_RECORD: "Kirjet muudetud",
}

#: Tie-break order, most canonical first. Used **only** when two facts fall on
#: the same day; it can never select an earlier date over a later one.
BASIS_PRECEDENCE: tuple[str, ...] = (
    ActivityBasis.CLOSURE,
    ActivityBasis.SUBMISSION,
    ActivityBasis.ENTRY,
    ActivityBasis.NEXT_ACTION,
    ActivityBasis.ENGAGEMENT,
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
)


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
    #: `EXACT` for eight of the nine bases, and not as a default standing in
    #: for an unknown answer: a closure, a sent submission, an entry, a next
    #: action, an arrival date and a OneNote timestamp each happened on a
    #: particular day. `Kaasamine` is the one fact here that may be recorded to
    #: a month, a quarter or a year (docs/adr/0082), and when it is, the date
    #: above is the period's **anchor** — a place in the ordering, never a day
    #: to print. :attr:`display_date` is what a surface renders.
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
        always printed; `09.26` for a month, the two-number shape the work
        surfaces already use for exactly this (`WorkItem.compact_month`); and
        the period spelled out for a quarter or a year, because *IV kvartal
        2026* has no two-number form a reader would arrive at unaided.

        Never the anchor as a day. `01.10` under a heading that means "when
        something last happened" is a claim about the first of October.
        """
        if self.date_precision == DatePrecision.MONTH:
            return f"{self.occurred_on.month:02d}.{self.occurred_on.year % 100:02d}"
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


def annotate_last_activity(queryset: QuerySet[Matter], user: Any) -> QuerySet[Matter]:
    """Attach every activity fact to the queryset as a subquery annotation.

    Scoped through each model's ``visible_to``, not the raw tables. A restricted
    entry on an ordinary Matter would otherwise announce its existence through
    the date column to somebody who cannot open it — the same leak the materials
    filter is careful about (``app.matters.selectors.filter_by_materials``).

    Source pages have no visibility of their own and are reached through the
    Matter, which the reader already sees, so they need no scope.

    The scope is resolved **once**. ``visible_to`` calls ``scope_for_user``,
    which asks the database whether this person holds a break-glass grant, so
    four calls to it here meant four identical lookups for one page — and this
    function runs several times on a surface that builds more than one
    population. Same predicate, same rule, computed once (Agent-F brief 31).
    """
    scope = scope_for_user(user)

    def scoped(model: Any) -> QuerySet[Any]:
        return apply_scope(model._default_manager.all(), child_visibility_q(scope))

    people_actions = scoped(NextAction)
    pages = MatterSourcePage.objects.filter(relationship_kind__in=CHRONOLOGY_RELATIONSHIPS)
    return queryset.annotate(
        activity_entry_at=_latest(scoped(Entry), "occurred_at"),
        # Any submission that carries a send date. A submission later withdrawn
        # or superseded was still genuinely sent on that day, and the withdrawal
        # does not un-happen the work.
        activity_submission_at=_latest(scoped(Submission).filter(sent_at__isnull=False), "sent_at"),
        activity_action_created_at=_latest(
            people_actions.filter(created_by__isnull=False), "created_at"
        ),
        activity_action_ended_at=_latest(people_actions.filter(ended_by__isnull=False), "ended_at"),
        activity_page_modified_at=_latest(pages, "source_page__source_modified_at"),
        activity_page_created_at=_latest(pages, "source_page__source_created_at"),
        # Only a *dated* engagement. `created_at` is deliberately not offered:
        # somebody recording a 2019 consultation today would otherwise move the
        # file's last activity to today, which is the import-timestamp mistake
        # this module exists to remove, arriving by a different door
        # (Agent-F brief 29).
        activity_engagement_on=_latest(
            scoped(MatterEngagement).filter(occurred_on__isnull=False),
            "occurred_on",
        ),
        # How exactly that date is known. A `Kaasamine` may be recorded to a
        # month, a quarter or a year (docs/adr/0082), and the date above is
        # then the period's anchor — correct to order on and wrong to print.
        #
        # The row this reads is the same row the maximum came from: order by
        # `occurred_on` descending and take the first. `_latest` cannot answer
        # this, because `Max` over one column says nothing about a second.
        #
        # **Exact wins a tie.** Two engagements anchored on the same day where
        # one names the day and the other names the month are both true, and
        # the day is the more informative of the two — so `_exact_first` sorts
        # `EXACT`/`INFERRED` ahead. `-id` after it makes the choice
        # deterministic where even that is equal, which is what stops the
        # column flickering between two spellings of the same date across
        # identical requests.
        activity_engagement_precision=Subquery(
            scoped(MatterEngagement)
            .filter(matter=OuterRef("pk"), occurred_on__isnull=False)
            .annotate(
                _exact_first=Case(
                    When(
                        occurred_on_precision__in=(
                            DatePrecision.EXACT,
                            DatePrecision.INFERRED,
                        ),
                        then=0,
                    ),
                    default=1,
                    output_field=IntegerField(),
                )
            )
            .order_by("-occurred_on", "_exact_first", "-id")
            .values("occurred_on_precision")[:1]
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
            # The precision travels with the date, and only from the one basis
            # that has one. Eight of the nine are days by construction; an
            # `ENGAGEMENT` may be a period, and `activity_engagement_precision`
            # is the precision of the very row whose date won. Where a closure
            # or an entry ties with an approximate engagement, precedence picks
            # the exact fact and the date stays a day — correctly: something
            # really did happen on it.
            precision = DatePrecision.EXACT.value
            if basis == ActivityBasis.ENGAGEMENT:
                precision = (
                    getattr(matter, "activity_engagement_precision", "")
                    or DatePrecision.EXACT.value
                )
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
    the page boundary has already been drawn. Both readings offer the same nine
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
    * It sorts an approximate `Kaasamine` on its anchor, which is what
      :func:`activity_of` compares too, so the two readings still agree row by
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


def activity_for_matter(matter: Matter, user: Any) -> MatterActivityFact | None:
    """The single-object convenience, for a Matter page rather than a list.

    One query, not six: it re-reads the same Matter through the same annotated
    queryset. Never call this in a loop — that is what
    :func:`annotate_last_activity` is for.
    """
    annotated = annotate_last_activity(Matter.objects.filter(pk=matter.pk), user).first()
    return activity_of(annotated) if annotated is not None else None
