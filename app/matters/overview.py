"""The department page's other read model — the areas scope, and the intervention list.

This was *Ülevaade*, a page of its own. Since ADR 0049 it and
:mod:`app.matters.department_dashboard` are the two read models one page
composes at ``/osakond/``, and the composition is :mod:`app.matters.department`.
What survives here is what that page still asks of it:

``?vaade=valdkonniti``  where does Koda intervene, and what is nobody watching —
                        the whole scope, table, sort control and rail
``intervention_rows``   *Vajab sekkumist*, the main column's hero list
``area_rows``           the *Valdkonnad* rail

There was a third scope, ``?vaade=tiim`` — *Minu tiim*, the same department
grouped by person. It is retired (docs/adr/0039). It never had a population of
its own: this product has no team-membership model, so it showed every colleague
the reader was entitled to see. An old ``?vaade=tiim`` link resolves to the
department the way every unrecognised scope does.

The scope lives in the URL so a view can be linked, bookmarked and quoted in a
bug report. There is no client-side tab machinery: two links, one view, one
template.

`build_overview` builds the areas scope and nothing else. Its department branch
— the figures, the per-person loads, the reporting rows, the feed — went with
the page it fed, so what is left here is a read model with one caller rather
than two implementations of a department page. The one deliberate exception is
the three-window Tähtajad model further down, which is fenced rather than
removed and is recorded as DS-26.

Three rules run through the module.

**One definition of overdue.** Everything dated comes from
:mod:`app.matters.work_items`, the same read model Minu töö renders. A second
idea of *late* written next door is how a department head ends up looking at two
screens that disagree about the same Matter.

**Authorization before arithmetic.** Every population begins at
``visible_to(user)``. A restricted Matter contributes to the totals of a reader
entitled to see it and is invisible — count and title alike — to everybody else.
Nothing here widens a scope because the page happens to aggregate colleagues,
and the department head is entitled by role in
:mod:`app.core.authorization`, not by a special case written here.

**Nothing is a performance metric.** A count of open files is inventory: one can
be a two-line monitoring note and the next a year of consultation. There is no
ranking of people, no score and no rate (master specification 18.8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from django.db.models import Count, Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.authorization import apply as apply_scope
from app.core.authorization import child_visibility_q, matter_visibility_q, scope_for_user
from app.core.dates import end_of_month, format_estonian_date
from app.core.dates import short_range as format_short_range
from app.intelligence.models import (
    MatterEffectiveDate,
    MatterImportantDate,
    MatterWorkVictory,
)
from app.matters import work_items as wi
from app.matters.activity import activity_of, annotate_last_activity
from app.matters.models import Entry, Matter, MatterEngagement
from app.matters.register_filters import RESULTS_ANCHOR
from app.matters.selectors import MISSING
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.taxonomy.models import PolicyArea
from app.taxonomy.vocabulary import selectable_policy_areas
from app.workflow.models import NextAction

# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------

SCOPE_PARAM = "vaade"
SCOPE_DEPARTMENT = "osakond"
SCOPE_AREAS = "valdkonniti"

SCOPES: tuple[tuple[str, str], ...] = (
    (SCOPE_DEPARTMENT, "Kogu osakond"),
    (SCOPE_AREAS, "Valdkonniti"),
)


def scope_from(value: str | None) -> str:
    """The scope a URL asks for, or the department. Unknown falls back.

    ``?vaade=tiim`` is one of the unknown ones now, which is the whole of the
    compatibility story for a retired scope: an old bookmark opens the surviving
    overview rather than 404ing, and there is no second implementation behind it
    to keep alive (docs/adr/0039).
    """
    keys = {key for key, _ in SCOPES}
    return value if value in keys else SCOPE_DEPARTMENT


#: Render the areas that carry no open work as rows too. The area table's own
#: footer link, and the only honest destination for a number that counts areas.
SHOW_EMPTY_AREAS_PARAM = "tuhjad"
#: How the area table is ordered. Read by `app.matters.department_views`,
#: which is why it outlived the department scope that also used it.
SORT_PARAM = "jarjesta"
SORT_OPEN = "avatud"
SORT_OVERDUE = "hilinenud"
SORT_NO_ACTION = "tegevuseta"
SORT_NAME = "nimi"

SORT_OPTIONS: tuple[tuple[str, str], ...] = (
    (SORT_OPEN, "avatud teemate arv"),
    (SORT_OVERDUE, "üle tähtaja"),
    (SORT_NO_ACTION, "järgmise tegevuseta"),
    (SORT_NAME, "nimi"),
)

INTERVENTION_LIMIT = 60
FEED_LIMIT = 12
RAIL_LIMIT = 6
AREA_MATTER_PREVIEW = 4


#: The one figure on this page that counts something the register does not list
#: — policy areas. It opens the list of exactly those, which is on this page.
#:
#: There was a second, ``#inimesed``, for *N inimest* on the retired Minu tiim.
#: The Koormus rail still lists people; it carries no figure claiming a total,
#: so it needs no landing point (docs/adr/0033, docs/adr/0039).
UNOWNED_ANCHOR = "#vastutajata-valdkonnad"


def _teemad(**params: Any) -> str:
    """A link into the register with filters already applied.

    Every figure on this page is a promise that a list exists behind it, and the
    promise is kept by reusing the register's own query parameters rather than
    inventing a parallel query language beside them.

    The fragment is part of the promise. A filtered register still opens on its
    search box and its narrowing panel, and a reader who arrived from "12 üle
    tähtaja" wants the twelve rows — so the link names the results region and
    the register focuses it (templates/matters/matter_list.html).
    """
    query = "&".join(f"{key}={value}" for key, value in params.items() if value not in (None, ""))
    base = reverse("matters:matter_list")
    return f"{base}?{query}{RESULTS_ANCHOR}" if query else f"{base}{RESULTS_ANCHOR}"


_OPEN_FULL = {"olek": "avatud", "liik": "FULL"}


@dataclass(frozen=True)
class Populations:
    """The four authorized querysets this page asks about, resolved once.

    Not an optimisation for its own sake. ``visible_to`` resolves the reader's
    scope every time it is called, and resolving a scope asks the database
    whether this person holds a break-glass grant — so a page that calls it
    twenty times pays for twenty identical lookups before it has counted
    anything. Building the populations once and narrowing them afterwards keeps
    the query count a property of the page rather than of how many figures
    happen to sit on it (app/matters/activity.py makes the same move for the
    same reason).

    Narrowing with ``.filter()`` does not re-resolve the scope, which is what
    makes this safe: every derived population is still the authorized one.
    """

    user: Any
    open_matters: QuerySet[Matter]
    quiet: QuerySet[Matter]
    ownerless: QuerySet[Matter]
    submissions: QuerySet[Submission]

    @classmethod
    def for_user(cls, user: Any) -> Populations:
        open_matters = wi.open_matters(user)
        return cls(
            user=user,
            open_matters=open_matters,
            # Through the read model rather than a condition written here, so
            # this and the register's `?tegevus=puudub` are one definition. They
            # were two, and they disagreed about a Matter whose only open action
            # is restricted below it: reader-blind here, reader-scoped there.
            quiet=wi.matters_without_action(user),
            ownerless=open_matters.filter(owner__isnull=True),
            submissions=Submission.objects.visible_to(user),
        )

    def entries(self) -> QuerySet[Entry]:
        """The reader's `Sissekanded` — a method, where the rest are fields.

        Deliberately not resolved with them. Only Kogu osakond counts entries,
        and every `visible_to` costs a break-glass lookup, so a field here would
        charge Valdkonniti for a population it never reads. Called once, by the
        one row that shows it (docs/adr/0039).
        """
        return Entry.objects.visible_to(self.user)


def _populations(user: Any, pop: Populations | None) -> Populations:
    return pop if pop is not None else Populations.for_user(user)


# ---------------------------------------------------------------------------
# The Seis strip
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Vajab sekkumist
# ---------------------------------------------------------------------------

REASON_OVERDUE = "overdue"
REASON_IMPORTANT = "important"
REASON_RIPE = "ripe"
REASON_NO_ACTION = "no_action"
REASON_OWNERLESS = "ownerless"

#: Reading order, and nothing else. A fixed rank per reason rather than an
#: invented severity score: a computed number nobody can check is a number
#: everybody argues with.
_REASON_RANK = {
    REASON_OVERDUE: 1,
    REASON_IMPORTANT: 1,
    REASON_NO_ACTION: 2,
    REASON_OWNERLESS: 3,
    REASON_RIPE: 4,
}

_REASON_TONE = {
    REASON_OVERDUE: "danger",
    REASON_IMPORTANT: "danger",
    REASON_NO_ACTION: "warning",
    REASON_OWNERLESS: "warning",
    REASON_RIPE: "warning",
}


@dataclass(frozen=True)
class InterventionRow:
    """One thing somebody can actually do something about."""

    reason: str
    value: str
    meaning: str
    matter: Matter
    detail: str
    owner: Any | None
    sort_on: date

    @property
    def tone(self) -> str:
        return _REASON_TONE[self.reason]

    @property
    def rank(self) -> int:
        return _REASON_RANK[self.reason]

    @property
    def url(self) -> str:
        return reverse("matters:matter_detail", kwargs={"pk": self.matter.pk})

    @property
    def owner_name(self) -> str:
        return self.owner.get_short_name() if self.owner is not None else ""

    @property
    def stage_label(self) -> str:
        stage = self.matter.stage
        return stage.label_et if stage is not None else ""

    @property
    def is_ownerless(self) -> bool:
        return self.reason == REASON_OWNERLESS


def intervention_url() -> str:
    """The register, holding exactly the Matters the intervention list covers.

    Named rather than written where it is read, because two surfaces print a
    count of that population and link to it — and the link is the parameters
    that *are* the definition, resolved by the read model's own selector
    (`WORK_NEEDS_ATTENTION`, app/matters/work_items.py).
    """
    return _teemad(**_OPEN_FULL, too=wi.WORK_NEEDS_ATTENTION)


def _short(value: date | None) -> str:
    return f"{value.day:02d}.{value.month:02d}" if value else ""


def intervention_rows(
    user: Any,
    today: date,
    items: list[wi.WorkItem],
    *,
    pop: Populations | None = None,
) -> list[InterventionRow]:
    """Every kind of trouble in one list, ordered by how much of it there is.

    The mix is deliberate. A department head does not think "show me overdue
    deadlines, then show me stalled files": they think "what is going wrong",
    and four separate short lists is four places to look for one answer.
    """
    people = _populations(user, pop)
    rows: list[InterventionRow] = []

    for item in items:
        if item.is_overdue:
            rows.append(
                InterventionRow(
                    reason=REASON_IMPORTANT if not item.is_action else REASON_OVERDUE,
                    value=f"{item.days_late} p üle",
                    # Compact, because the cell is 96px and the meaning must be
                    # readable in full: a truncated meaning is a bare date.
                    meaning=f"{item.meaning} {_short(item.period_end or item.when)}",
                    matter=item.matter,
                    detail=item.text,
                    owner=item.responsible,
                    sort_on=item.period_end or item.when or today,
                )
            )
        elif item.is_review_ripe:
            rows.append(
                InterventionRow(
                    reason=REASON_RIPE,
                    value="üle vaadata",
                    # The *date's* meaning — VAATAN ÜLE, OODATAV AEG — and
                    # the day it fell on. Never the stored action kind: TEEN /
                    # OOTAN / JÄLGIN is not a category this product asks a
                    # reader to hold (ADR 0054).
                    meaning=f"{item.meaning} {_short(item.when)}",
                    matter=item.matter,
                    detail=item.text,
                    owner=item.responsible,
                    sort_on=item.when or today,
                )
            )

    quiet = annotate_last_activity(
        people.quiet.filter(owner__isnull=False).select_related("owner", "stage"),
        people.user,
    ).order_by("updated_at")[:INTERVENTION_LIMIT]
    for matter in quiet:
        # `activity_of` reads the annotations and never queries, so a capped
        # list costs one query rather than one per row. It returns None where
        # nothing is known, and a row that says so is better than one that
        # invents a number.
        activity = activity_of(matter)
        since = activity.occurred_on if activity else None
        rows.append(
            InterventionRow(
                reason=REASON_NO_ACTION,
                # What is wrong with the file, in the words somebody would use
                # about it. "sammuta" is this module's vocabulary, not the
                # department's, and it read as an abbreviation of something
                # (human QA §10).
                value="tähtaeg puudub",
                # Deliberately blank. The row used to print "202 P VAIKUST"
                # beside the reason, and a number nobody can act on is noise on
                # the one list that exists to be acted on: the file has no next
                # step whether the silence is two months old or seven, and the
                # thing to do about it is the same (human QA §11).
                #
                # `since` is still computed and still decides `sort_on`, so the
                # quietest file is still the one at the top. What changed is
                # what the row says, not which rows there are or in what order.
                meaning="",
                matter=matter,
                detail="järgmine samm määramata",
                owner=matter.owner,
                sort_on=since or matter.updated_at.date(),
            )
        )

    # Never silently omitted: an unowned Matter is on nobody's list by
    # definition, which is exactly why it has to be on this one.
    for matter in people.ownerless.select_related("stage").order_by("-created_at")[
        :INTERVENTION_LIMIT
    ]:
        deadline = matter.response_deadline
        rows.append(
            InterventionRow(
                reason=REASON_OWNERLESS,
                value="vastutajata",
                meaning=f"SAABUS {_short(matter.received_date)}"
                if matter.received_date
                else "SAABUMISE AEG TEADMATA",
                matter=matter,
                detail=f"arvamuse tähtaeg {format_estonian_date(deadline)}"
                if deadline
                else "arvamuse tähtaeg määramata",
                owner=None,
                sort_on=matter.received_date or matter.created_at.date(),
            )
        )

    return sorted(rows, key=lambda row: (row.rank, row.sort_on))[:INTERVENTION_LIMIT]


# ---------------------------------------------------------------------------
# Viimased muudatused
# ---------------------------------------------------------------------------

FEED_ALL = "koik"
FEED_ENTRIES = "sissekanded"
FEED_SUBMISSIONS = "arvamused"
FEED_STATUS = "staatus"

#: The bucket labels and the parameter that selects one. Kept with the rest of
#: the fenced feed (DS-25): no page renders them, but «Teema muudatused» is a
#: vocabulary correction — the bucket widened past status changes and the old
#: name stopped being true — and the value `staatus` is in bookmarks people
#: pasted to each other. Both are asserted in `tests/test_identifier_free_ui.py`
#: and would stop being checked at all if they went before the decision does.
FEED_FILTERS: tuple[tuple[str, str], ...] = (
    (FEED_ALL, "Kõik"),
    (FEED_ENTRIES, "Sissekanded"),
    (FEED_SUBMISSIONS, "Arvamused"),
    (FEED_STATUS, "Teema muudatused"),
)

FEED_PARAM = "voog"

# ---------------------------------------------------------------------------
# What counts as a change to the department's work
#
# `ChangeEvent` is the audit history and holds far more than this. The feed is a
# *curated projection* of it, and the curation is the point: a section that
# reported every stored row would report source-field refreshes and cutover
# normalisations beside a colleague closing a file, and a reader who has to skip
# rows stops reading them all (§10, §18).
#
# Two rules decide membership. **Would a colleague recognise this as something a
# person did to the work?** — which excludes import, cutover and infrastructure
# rows whatever their event type. And **can it be scoped safely?** — which is
# what the split below is really about.
# ---------------------------------------------------------------------------

#: Events about the Matter itself. `ChangeEvent.matter` is the whole subject, so
#: Matter-level visibility is the whole answer.
_MATTER_EVENTS: tuple[str, ...] = (
    ChangeEventType.MATTER_CREATED,
    ChangeEventType.MATTER_TITLE_CHANGED,
    ChangeEventType.MATTER_STAGE_CHANGED,
    ChangeEventType.MATTER_ASSIGNED,
    ChangeEventType.MATTER_CLOSED,
    ChangeEventType.MATTER_REOPENED,
)

#: Events about a *child* record — a Järgmiseks, an Oluline tähtaeg, a
#: Jõustumine, a Kaasamine, a Töövõit.
#:
#: These may not be filtered by their Matter alone. Every one of these models is
#: a ``VisibilityInheritingModel``: it carries a ``visibility_override`` that can
#: restrict the record *beyond* its Matter, so a colleague may hold an ordinary
#: Matter and still not be allowed to know that a particular deadline on it
#: exists. A row selected on ``matter__in=visible`` would announce exactly that.
#:
#: Each family is therefore selected through its own ``visible_to`` queryset,
#: matched on ``ChangeEvent.object_id`` — every service in this list records the
#: child as ``obj=``, so the id is there to match on. The Matter filter still
#: applies on top; this narrows, it never widens.
#:
#: This is not the AUTH-003 hardening and does not touch it. It is the ordinary
#: obligation on a *new* query: build the population inside the boundary rather
#: than resolve names outside it (§13, §14).
_CHILD_EVENT_FAMILIES: tuple[tuple[tuple[str, ...], Any], ...] = (
    (
        (
            ChangeEventType.NEXT_ACTION_SET,
            ChangeEventType.NEXT_ACTION_COMPLETED,
            ChangeEventType.NEXT_ACTION_REVIEWED,
            ChangeEventType.NEXT_ACTION_CANCELLED,
        ),
        NextAction,
    ),
    (
        (
            ChangeEventType.IMPORTANT_DATE_ADDED,
            ChangeEventType.IMPORTANT_DATE_CHANGED,
            ChangeEventType.IMPORTANT_DATE_CANCELLED,
        ),
        MatterImportantDate,
    ),
    (
        (
            ChangeEventType.EFFECTIVE_DATE_ADDED,
            ChangeEventType.EFFECTIVE_DATE_CHANGED,
            ChangeEventType.EFFECTIVE_DATE_CANCELLED,
        ),
        MatterEffectiveDate,
    ),
    (
        (
            ChangeEventType.ENGAGEMENT_ADDED,
            ChangeEventType.ENGAGEMENT_CHANGED,
        ),
        MatterEngagement,
    ),
    (
        (
            ChangeEventType.WORK_VICTORY_PROPOSED,
            ChangeEventType.WORK_VICTORY_CONFIRMED,
            ChangeEventType.WORK_VICTORY_REJECTED,
        ),
        MatterWorkVictory,
    ),
)

#: The whole user-facing vocabulary of *Viimased muudatused*, in one place.
#:
#: Every row reads "<inimene> <tegu> · <teema>", and the middle word is chosen
#: here rather than in a template. A template that branched on
#: ``ChangeEventType`` would have to know the enum, and the second template that
#: rendered the feed would word the same event differently (§27).
#:
#: Estonian past tense, third person, with the actor as the subject: these are
#: sentences about colleagues, not event names. Nothing raw — no
#: ``MATTER_STAGE_CHANGED``, no model or field names — ever reaches the page.
_EVENT_VERBS: dict[str, str] = {
    # The Matter itself
    ChangeEventType.MATTER_CREATED: "avas teema",
    # A rename earns a line where the other field edits do not. The enum says
    # why: the title is what everybody navigates and cites by, so a rename is
    # the one change most likely to make a colleague think they are looking at a
    # different file.
    ChangeEventType.MATTER_TITLE_CHANGED: "muutis teema pealkirja",
    ChangeEventType.MATTER_STAGE_CHANGED: "muutis hetkeseisu",
    ChangeEventType.MATTER_ASSIGNED: "määras vastutaja",
    ChangeEventType.MATTER_CLOSED: "sulges teema",
    ChangeEventType.MATTER_REOPENED: "avas teema uuesti",
    # Järgmiseks
    ChangeEventType.NEXT_ACTION_SET: "määras järgmise tegevuse",
    ChangeEventType.NEXT_ACTION_COMPLETED: "lõpetas järgmise tegevuse",
    ChangeEventType.NEXT_ACTION_REVIEWED: "vaatas järgmise tegevuse üle",
    ChangeEventType.NEXT_ACTION_CANCELLED: "tühistas järgmise tegevuse",
    # Olulised tähtajad
    ChangeEventType.IMPORTANT_DATE_ADDED: "lisas olulise tähtaja",
    ChangeEventType.IMPORTANT_DATE_CHANGED: "muutis olulist tähtaega",
    ChangeEventType.IMPORTANT_DATE_CANCELLED: "tühistas olulise tähtaja",
    # Jõustumised
    ChangeEventType.EFFECTIVE_DATE_ADDED: "lisas jõustumise",
    ChangeEventType.EFFECTIVE_DATE_CHANGED: "muutis jõustumist",
    ChangeEventType.EFFECTIVE_DATE_CANCELLED: "tühistas jõustumise",
    # Kaasamine
    ChangeEventType.ENGAGEMENT_ADDED: "lisas kaasamise",
    ChangeEventType.ENGAGEMENT_CHANGED: "muutis kaasamist",
    # Töövõidud
    ChangeEventType.WORK_VICTORY_PROPOSED: "esitas töövõidu kandidaadi",
    ChangeEventType.WORK_VICTORY_CONFIRMED: "kinnitas töövõidu",
    ChangeEventType.WORK_VICTORY_REJECTED: "märkis, et töövõit ei realiseerunud",
}

#: The last resort, and unreachable while the tuples above are whitelists. It
#: exists so that a future event type added to one of them without a word here
#: reads as a vague sentence rather than as ``MATTER_SOMETHING_CHANGED``. A test
#: asserts no rendered row ever reaches it.
_EVENT_VERB_FALLBACK = "muutis teemat"

#: The two sources that are not ChangeEvents. Here rather than inline in
#: `activity_feed` so the vocabulary can be read — and reviewed — as one list.
_ENTRY_VERB = "lisas sissekande"
_SUBMISSION_VERB = "esitas arvamuse"


@dataclass(frozen=True)
class FeedItem:
    """One line of *Viimased muudatused*: who did what, in which topic.

    Everything a row renders is a property here rather than a lookup the
    template performs on ``matter``. That is not tidiness. The section answers
    "kes muutis mida ja millise teema juures", and the topic is named by its
    **title** — the row used to print ``2026_303``, which is how this system
    files the record and not how anybody refers to it (human QA §16).

    A title says more than a reference does, so where the title comes from
    matters. It comes from a Matter that is already inside the reader's
    authorized population: every source in :func:`activity_feed` is filtered
    through ``visible_to`` or through ``Matter.objects.visible_to`` before a
    ``FeedItem`` is built, and nothing here resolves a title by primary key
    afterwards. A Matter the reader may not open produces no row at all, so
    there is no row whose title could leak (§21).
    """

    when: Any
    actor: Any | None
    verb: str
    matter: Matter | None

    @property
    def actor_name(self) -> str:
        return self.actor.display_name if self.actor is not None else "Süsteem"

    @property
    def initials(self) -> str:
        return self.actor.initials if self.actor is not None else "—"

    @property
    def matter_title(self) -> str:
        """The topic as a person names it. Never the technical reference."""
        return self.matter.title if self.matter is not None else ""

    @property
    def is_restricted(self) -> bool:
        return self.matter is not None and self.matter.is_restricted

    @property
    def url(self) -> str:
        return (
            reverse("matters:matter_detail", kwargs={"pk": self.matter.pk})
            if self.matter is not None
            else ""
        )


def _authorized_change_events(user: Any, since: date) -> QuerySet[ChangeEvent]:
    """The curated change rows this reader may see, as one query.

    Two conditions, and a row needs both.

    The **Matter** filter is the outer one and applies to everything: no row is
    offered about a file the reader cannot open. On its own it would be enough
    for `_MATTER_EVENTS`, whose whole subject is the Matter.

    The **child** filter narrows the rest. A Järgmiseks, an Oluline tähtaeg, a
    Jõustumine, a Kaasamine and a Töövõit each carry their own
    ``visibility_override``, so "the reader may open this Matter" does not
    answer "the reader may know this record exists". Each family is matched
    against its own ``visible_to`` queryset on ``object_id`` — the population is
    built inside the authorization boundary rather than filtered after the fact,
    which is the difference between a scoped feed and a leak with a title on it.

    An event type in neither group is not selected at all. Authorization
    whitelists, and so does curation: a future ``ChangeEventType`` appears here
    only when somebody adds it deliberately, rather than the day it is defined.
    """
    # Resolved **once**, then reused for all six populations.
    #
    # `visible_to` calls `scope_for_user`, and `scope_for_user` asks the database
    # whether this person holds a break-glass grant. Six calls to it here is six
    # identical lookups on one page render — the same avoidable cost
    # `annotate_last_activity` documents, arriving through a different door, and
    # it is what pushed this page past its query ceiling
    # (`tests/test_multiple_senders.py`, ADR 0033).
    scope = scope_for_user(user)

    def scoped(model: Any) -> Any:
        return apply_scope(model._default_manager.all(), child_visibility_q(scope))

    visible_matters = apply_scope(Matter.objects.all(), matter_visibility_q(scope)).values("pk")

    eligible = Q(event_type__in=_MATTER_EVENTS)
    for event_types, model in _CHILD_EVENT_FAMILIES:
        eligible |= Q(
            event_type__in=event_types,
            object_id__in=scoped(model).values("pk"),
        )

    return (
        ChangeEvent.objects.filter(
            eligible,
            occurred_at__date__gte=since,
            matter__in=visible_matters,
        )
        .select_related("matter", "actor")
        .order_by("-occurred_at")[:FEED_LIMIT]
    )


def activity_feed(user: Any, today: date, kind: str = FEED_ALL) -> list[FeedItem]:
    """The department's last month as one-liners, newest first.

    Three sources, each capped in the database rather than in Python: a page
    showing twelve lines must not drag a month of the department's traffic
    through the process to find them (§27).
    """
    since = today - timedelta(days=30)
    items: list[FeedItem] = []

    if kind in (FEED_ALL, FEED_ENTRIES):
        entries = (
            Entry.objects.visible_to(user)
            .filter(occurred_at__date__gte=since)
            .select_related("matter", "author")
            .chronological()[:FEED_LIMIT]
        )
        items += [
            FeedItem(
                when=entry.occurred_at,
                actor=entry.author,
                verb=_ENTRY_VERB,
                matter=entry.matter,
            )
            for entry in entries
        ]

    if kind in (FEED_ALL, FEED_SUBMISSIONS):
        sent = (
            Submission.objects.visible_to(user)
            # `sent_at__date__gte`, not `sent_at__gte`. `since` is a date, and
            # Django compares a date against a DateTimeField by widening it to
            # midnight *naive* — which raises a RuntimeWarning under
            # USE_TZ and quietly means "midnight UTC" rather than midnight here.
            # The two Entry/ChangeEvent branches beside this one already use the
            # `__date` lookup; this one did not (Ülevaade QA §5).
            .filter(
                status=SubmissionStatus.SENT,
                sent_at__isnull=False,
                sent_at__date__gte=since,
            )
            .select_related("matter", "sent_by")
            .order_by("-sent_at")[:FEED_LIMIT]
        )
        items += [
            FeedItem(
                when=submission.sent_at,
                actor=getattr(submission, "sent_by", None),
                verb=_SUBMISSION_VERB,
                matter=submission.matter,
            )
            for submission in sent
        ]

    if kind in (FEED_ALL, FEED_STATUS):
        items += [
            FeedItem(
                when=event.occurred_at,
                actor=event.actor,
                verb=_EVENT_VERBS.get(event.event_type, _EVENT_VERB_FALLBACK),
                matter=event.matter,
            )
            for event in _authorized_change_events(user, since)
        ]

    return sorted(items, key=lambda item: item.when, reverse=True)[:FEED_LIMIT]


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Valdkonnad
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AreaRow:
    """One policy area's current activity.

    ``is_legacy`` marks a retired category that still carries open work. Those
    Matters must not vanish from a statistic because the vocabulary moved on,
    and they are never remapped to a current area either — that would be an
    invention nobody reviewed (§19.1).
    """

    key: str
    name: str
    is_legacy: bool
    open_count: int
    overdue: int
    no_action: int
    owners: list[Any]
    matters: list[Matter] = field(default_factory=list)

    @property
    def owner_count(self) -> int:
        return len(self.owners)

    @property
    def is_unowned(self) -> bool:
        """Nobody at all owns work here — not "one file among ten is unassigned".

        The distinction is the whole point of the figure on the strip: an area
        with five owned Matters and one unassigned one is being watched
        (§19.3, §19.6).
        """
        return self.open_count > 0 and not self.owners

    @property
    def url(self) -> str:
        return _teemad(**_OPEN_FULL, valdkond=self.key)

    @property
    def overdue_url(self) -> str:
        # The read model's own population, not `?tegevus=hilinenud`: the column
        # counts a passed `Oluline tähtaeg` too, and that carries no open action
        # for the register to filter on.
        return _teemad(**_OPEN_FULL, valdkond=self.key, too=wi.WORK_OVERDUE)

    @property
    def no_action_url(self) -> str:
        return _teemad(**_OPEN_FULL, valdkond=self.key, tegevus=MISSING)

    @property
    def unassigned_url(self) -> str:
        return _teemad(**_OPEN_FULL, valdkond=self.key, vastutaja=MISSING)


def _initials(display_name: str) -> str:
    """Mirrors ``User.initials`` for a name read off a ``values()`` projection."""
    parts = [part for part in (display_name or "").split() if part]
    if len(parts) >= 2:
        return (parts[0][:1] + parts[-1][:1]).upper()
    if parts:
        return parts[0][:2].upper()
    return "—"


def _area_counts(queryset: QuerySet[Matter]) -> dict[str, int]:
    grouped = (
        Matter.objects.filter(pk__in=queryset.values("pk"))
        .order_by()
        .values("policy_areas__key")
        .annotate(total=Count("id", distinct=True))
    )
    return {row["policy_areas__key"]: row["total"] for row in grouped if row["policy_areas__key"]}


def area_rows(
    user: Any,
    today: date,
    items: list[wi.WorkItem],
    *,
    sort: str = SORT_OPEN,
    include_empty: bool = False,
    pop: Populations | None = None,
) -> tuple[list[AreaRow], int]:
    """One row per area that carries work, plus how many carry none.

    The vocabulary is the governed one — every active Valdkond, read from
    :class:`~app.taxonomy.models.PolicyArea` rather than restated here — and any
    retired area still holding an open Matter is added to it. A hard-coded list
    beside a governed one is a list that drifts, and dropping the retired
    categories would quietly delete live work from the statistic (§19.1).
    """
    people = _populations(user, pop)
    open_matters = people.open_matters
    open_counts = _area_counts(open_matters)
    quiet_counts = _area_counts(people.quiet)

    overdue_ids = [item.matter_id for item in items if item.is_overdue]
    overdue_counts = _area_counts(open_matters.filter(pk__in=overdue_ids))

    owners: dict[str, list[Any]] = {}
    owner_rows = (
        open_matters.filter(owner__isnull=False)
        .values("policy_areas__key", "owner_id", "owner__display_name")
        .distinct()
    )
    for row in owner_rows:
        key = row["policy_areas__key"]
        if not key:
            continue
        owners.setdefault(key, []).append(
            {
                "pk": row["owner_id"],
                "name": row["owner__display_name"],
                # The same two letters the avatar carries everywhere else. The
                # User model owns this rule; a template slicing one character
                # off a first name is a second rule that drifts from it.
                "initials": _initials(row["owner__display_name"]),
            }
        )

    # The governed vocabulary, from the one function that defines it. The rows
    # are re-sorted below by whichever column the reader chose, so what is taken
    # from here is the *set*: which areas are current, and therefore which of
    # the rows below carry the "varasem" flag (app/taxonomy/vocabulary.py).
    active = {area.key: area for area in selectable_policy_areas()}
    legacy_keys = {key for key in open_counts if key not in active}
    legacy = {area.key: area for area in PolicyArea.objects.filter(key__in=legacy_keys)}

    rows: list[AreaRow] = []
    for key, area in {**active, **legacy}.items():
        rows.append(
            AreaRow(
                key=key,
                name=area.name_et,
                is_legacy=key in legacy_keys,
                open_count=open_counts.get(key, 0),
                overdue=overdue_counts.get(key, 0),
                no_action=quiet_counts.get(key, 0),
                owners=sorted(owners.get(key, []), key=lambda entry: entry["name"]),
            )
        )

    empty = sum(1 for row in rows if row.open_count == 0)
    # An area with nothing open is normally counted in the footer rather than
    # rendered as a blank line — a screenful of empty rows is a page that looks
    # like a data problem. `?tuhjad=1` is that footer's destination: it promised
    # "all N areas" and opened the register, which lists Matters and not areas
    # at all (Ülevaade QA §3).
    active_rows = rows if include_empty else [row for row in rows if row.open_count > 0]

    keys = {
        SORT_OPEN: lambda row: (-row.open_count, row.name),
        SORT_OVERDUE: lambda row: (-row.overdue, -row.open_count, row.name),
        SORT_NO_ACTION: lambda row: (-row.no_action, -row.open_count, row.name),
        SORT_NAME: lambda row: (row.name,),
    }
    active_rows.sort(key=keys.get(sort, keys[SORT_OPEN]))
    return active_rows, empty


@dataclass(frozen=True)
class AreaMatterLine:
    """One Matter under an expanded area row, with lateness already decided.

    The flag is computed here rather than compared in the template for the same
    reason :class:`wi.WorkItem` carries its own ``today``: a template's ``today``
    is whatever the view happened to put in the context, so the same question
    asked from two pages could quietly get two answers.

    ``is_overdue`` is the **operational** answer, not "the date is past". The
    line prints the Matter's own *Arvamuse tähtaeg* and marks it *üle*, so it
    has to mean what every other late figure in the product means: a deadline
    with no SENT Submission, no ``VÄLJA`` completion mark and no open
    ``Järgmiseks`` (docs/adr/0050, docs/adr/0059).

    It used to read ``response_deadline < today`` plainly, and that is how a
    count and the list behind it come apart: the number in the row above is
    taken from the shared work model, so a caret opened onto rows marked *üle*
    that the figure had not counted. The flag is now read from the **same list**
    the count is — no second query, no second definition (§18.9).
    """

    matter: Any
    deadline: date | None
    is_overdue: bool


def attach_area_matters(
    user: Any,
    rows: list[AreaRow],
    today: date,
    limit: int = 1,
    pop: Populations | None = None,
    items: list[wi.WorkItem] | None = None,
) -> None:
    """Load the compact Matter lines for the rows that open on arrival.

    Only the rows that are actually expanded. Fetching every area's Matters so
    that a caret *could* open instantly is a page that reads the whole register
    to render a table of counts (§27).

    ``items`` is the shared work model the caller has already read — the same
    list :func:`area_rows` counts its *Üle tähtaja* column from. It is passed in
    rather than re-read so that the caret's rows and the number above them are
    literally the same set, and it is the response-deadline half of it that
    decides ``is_overdue``, because that is the date these lines print. Omitting
    it reads the model again with the same authorization, so a caller that has
    not got one is not forced to invent it.
    """
    people = _populations(user, pop)
    shared = items if items is not None else wi.work_items(user, today=today)
    # The response-deadline rows only. A Matter can be late on a DO deadline it
    # has nothing to do with, and marking *this* date üle for that reason would
    # be a third meaning of the word on one page.
    overdue_deadlines = {
        item.matter_id
        for item in shared
        if item.source_type == wi.SOURCE_RESPONSE_DEADLINE and item.is_overdue
    }
    for row in rows[:limit]:
        object.__setattr__(
            row,
            "matters",
            [
                AreaMatterLine(
                    matter=matter,
                    deadline=matter.response_deadline,
                    is_overdue=matter.pk in overdue_deadlines,
                )
                for matter in people.open_matters.filter(policy_areas__key=row.key)
                .select_related("stage", "owner")
                .order_by("response_deadline")[:AREA_MATTER_PREVIEW]
            ],
        )


# ---------------------------------------------------------------------------
# Rail blocks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CountRow:
    label: str
    count: int
    url: str = ""
    note: str = ""
    tone: str = ""


def organisation_ranking(
    user: Any, limit: int = RAIL_LIMIT, pop: Populations | None = None
) -> list[CountRow]:
    """Who Koda deals with most, by canonical organisation.

    Counted on the ``source_organisations`` relation, which resolves to the
    master record — a text alias is never a second institution here, because
    the register's own names were mapped onto canonical rows during the import.
    """
    grouped = (
        _populations(user, pop)
        .open_matters.filter(source_organisations__isnull=False)
        .values("source_organisations__id", "source_organisations__name")
        .annotate(total=Count("id", distinct=True))
        .order_by("-total", "source_organisations__name")[:limit]
    )
    return [
        CountRow(
            label=row["source_organisations__name"],
            count=row["total"],
            url=_teemad(**_OPEN_FULL, saatja=row["source_organisations__id"]),
        )
        for row in grouped
    ]


# ---------------------------------------------------------------------------
# The whole page
# ---------------------------------------------------------------------------


@dataclass
class Overview:
    """The Valdkonniti scope, as one rendered object.

    It carried both scopes until the department half of `build_overview` was
    retired: the fields the department page filled — its Seis figures, the
    intervention list, the deadline windows, Koormus, the feed, Aruandlus — are
    `app.matters.department.Department`'s now, read from
    `app.matters.department_dashboard`. Keeping a second set here meant two
    objects able to answer the same question differently (docs/adr/0049).
    """

    scope: str
    today: date
    #: What the header prints. An integer rather than a one-item list of
    #: `Figure`: the area scope has no Seis strip of its own — the merged page's
    #: strip is `dd.seis_figures` — so the only thing left to carry was a count.
    open_matters: int = 0
    areas: list[AreaRow] = field(default_factory=list)
    empty_areas: int = 0
    show_empty_areas: bool = False
    area_total: int = 0
    sort: str = SORT_OPEN
    unowned_areas: list[AreaRow] = field(default_factory=list)
    organisations: list[CountRow] = field(default_factory=list)

    @property
    def is_areas(self) -> bool:
        return self.scope == SCOPE_AREAS


def build_overview(
    user: Any,
    *,
    scope: str = SCOPE_AREAS,
    today: date | None = None,
    sort: str = SORT_OPEN,
    show_empty_areas: bool = False,
    items: list[wi.WorkItem] | None = None,
) -> Overview:
    """Assemble the Valdkonniti scope.

    It assembled two. The department branch stopped being routed when ADR 0049
    merged the two pages, and stayed behind it — a whole second read model for a
    page that no longer existed, beside the one that replaced it. It is gone;
    `app.matters.department` composes the live page from
    `app.matters.department_dashboard` and from what remains here.

    `scope` survives because `Overview.is_areas` is what the rail template asks,
    and because an old `?vaade=` in somebody's bookmark still has to resolve to
    something rather than raise.
    """
    today = today or timezone.localdate()
    page = Overview(scope=scope_from(scope), today=today, sort=sort)
    people = Populations.for_user(user)

    # One read of the shared work model, reused by the table and the rail —
    # the composing page's own read when it has one, since it is the same list
    # for the same reader on the same day.
    items = items if items is not None else wi.work_items(user, today=today)

    areas, empty = area_rows(
        user, today, items, sort=sort, include_empty=show_empty_areas, pop=people
    )
    attach_area_matters(user, areas, today, pop=people, items=items)
    page.areas = areas
    page.empty_areas = empty
    page.show_empty_areas = show_empty_areas
    page.area_total = len(areas) if show_empty_areas else len(areas) + empty
    page.unowned_areas = [row for row in areas if row.is_unowned]
    page.open_matters = people.open_matters.count()
    page.organisations = organisation_ranking(user, pop=people)
    return page


# ---------------------------------------------------------------------------
# Tähtajad — the three-window panel, kept until its coverage is settled
# ---------------------------------------------------------------------------
#
# Retired, not dead. The merged page cuts five windows
# (`department_dashboard.upcoming_windows`, ADR 0049 §5) and nothing routes to
# these three any more.
#
# They stay for the same reason `activity_feed` stays: removing them means
# deciding that `tests/test_deadline_grouping.py` — twenty tests about which
# window a deadline lands in, when a group is omitted, when «Näita veel»
# appears, and that a WAIT is never a deadline — is fully covered by the live
# panel's own tests. That overlap is substantial and it is *not* provably
# complete, and a coverage judgement made at the end of a large removal is the
# kind that quietly loses an invariant.
#
# So: its own round, with the comparison done properly. Until then the read
# model is here and tested, and no page reads it.

#: How many rows *Ülejäänud kuu* shows before the rest go behind «Näita veel».
#: Five, the same as Osakond's *Eesolev* (`UPCOMING_PREVIEW`): the two panels
#: answer the same question at different scopes and a reader who learns one
#: should not have to relearn the other.
DEADLINE_PREVIEW = 5

DEADLINE_LIMIT = 40

#: Kept as a name here because three test modules and two callers read it from
#: this module; the definition itself lives with the read model, so the register
#: filters on the same predicate the table renders.
real_deadlines = wi.real_deadlines


@dataclass(frozen=True)
class DeadlineGroup:
    """One deadline window, and the register list that holds exactly it.

    ``count`` is work items — two deadlines on one Matter are two lines in the
    table — and ``matter_count`` is what the register would list. They are
    printed as the different numbers they are, because the group's link opens a
    list of *Matters* and a "Näita ülejäänud 3" above a list of two is the
    failure this page exists to avoid.
    """

    key: str
    label: str
    items: list[wi.WorkItem]
    shown: int
    #: The interval the header states, both ends inclusive. The far group has no
    #: end and prints no range: it is everything after the last real window.
    starts: date
    ends: date | None = None
    #: Rendered as one pointer line instead of a list. Only the far group.
    is_far: bool = False

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def preview(self) -> list[wi.WorkItem]:
        return self.items[: self.shown]

    @property
    def remaining(self) -> int:
        return max(0, self.count - self.shown)

    @property
    def rest(self) -> list[wi.WorkItem]:
        """What the preview did not show, capped.

        Behind a disclosure rather than dropped. A window that quietly ended at
        four rows is how a deadline stopped being anywhere at all, which is the
        failure the far window exists to fix — and it would be an odd fix that
        reintroduced it inside the two near ones (`DEADLINE_LIMIT`).
        """
        return self.items[self.shown : DEADLINE_LIMIT]

    @property
    def matter_count(self) -> int:
        return len({item.matter_id for item in self.items})

    @property
    def is_empty_window(self) -> bool:
        """True where the interval has no days in it at all.

        *Ülejäänud kuu* is one such window whenever this week runs to or past
        the end of the month: the rest of the month would start on Monday and
        have ended on Sunday. It holds nothing by construction — a window is
        asked for both ends at once — and the panel omits it rather than
        printing a heading over an interval read backwards.
        """
        return self.ends is not None and self.starts > self.ends

    @property
    def range_label(self) -> str:
        """``27.08–31.08``. Empty where there is no closed interval to state."""
        if self.is_empty_window:
            return ""
        return format_short_range(self.starts, self.ends)

    @property
    def first(self) -> wi.WorkItem | None:
        """The nearest item. The far group prints this one date and a count."""
        return self.items[0] if self.items else None

    @property
    def beyond_first(self) -> int:
        """How many more there are behind the one date the far group prints."""
        return max(0, self.matter_count - 1)

    @property
    def url(self) -> str:
        """The register, narrowed to exactly this window of real deadlines.

        `?too=tahtaeg-vahemik` with the group's own two dates rather than a
        named population, for the reason Osakond's *Eesolev* does the same: the
        windows here move with the weekday and with the length of the month, so
        a fixed name could only ever approximate them — and «kõik 14 →» over a
        list of eleven is the failure this page exists to avoid. One selector
        answers both the count and the list (`work_population_ids`,
        app/matters/work_items.py).
        """
        params: dict[str, Any] = {
            **_OPEN_FULL,
            "too": wi.WORK_DEADLINE_WINDOW,
            "too_alates": format_estonian_date(self.starts),
        }
        if self.ends is not None:
            params["too_kuni"] = format_estonian_date(self.ends)
        return _teemad(**params)


def deadline_windows(today: date) -> tuple[tuple[str, str, date, date | None], ...]:
    """The panel's three consecutive windows: key, heading, first day, last day.

    *Sel nädalal* is the calendar week — Monday to Sunday, not the seven days
    from here — because "this week" is a thing a department says to each other
    on Wednesday and still means the same by on Friday. A rolling window moves
    under the reader every morning, and a date that was on the list yesterday
    is on a different screen today for no reason they can see.

    *Ülejäänud kuu* is what is left of the calendar month after that Sunday.
    That is the planning horizon somebody actually has: "what else is coming
    before the month turns over" is a question with an answer, where "the next
    thirty days" ends in the middle of a week nobody chose.

    *Kaugemal* is everything after, as one line. It exists because the panel
    used to end at next week, so a deadline five weeks out was on no screen
    anywhere until it became next week's problem (design handoff 1a).

    The three are consecutive and the last is open-ended, so a dated commitment
    lands in exactly one of them. Two boundary cases are the reason this is one
    function rather than three expressions written where they are read:

    * The week can start in the **previous** month — Monday 31.08 with today on
      Wednesday 02.09. *Sel nädalal* holds 31.08 all the same, and *Ülejäänud
      kuu* still starts on 07.09: the cut between them is the week's end, not
      the month's start, so nothing is counted twice and nothing falls out.
    * The week can run to or past the **end** of the month — Monday 28.09 with
      Sunday on 04.10. The rest of the month would then begin after it ended,
      so it is returned as the empty interval it is and the panel omits it;
      *Kaugemal* starts the day after the *week* rather than the day after the
      month, which is what keeps the three windows touching.
    """
    week_start = wi.start_of_iso_week(today)
    week_end = wi.end_of_iso_week(today)
    rest_start = week_end + timedelta(days=1)
    # Never before the week's own end, so the far window cannot start inside a
    # window the reader has already been shown.
    rest_end = max(end_of_month(today), week_end)
    return (
        ("sel_nadalal", "Sel nädalal", week_start, week_end),
        ("ulejaanud_kuu", "Ülejäänud kuu", rest_start, rest_end),
        ("kaugemal", "Kaugemal", rest_end + timedelta(days=1), None),
    )


#: How many rows each window shows before it stops, by key. The whole of this
#: week, because this week is what somebody is working in; a preview of the rest
#: of the month, because they are planning rather than doing. The far window
#: shows one line whatever it holds.
_DEADLINE_SHOWN: dict[str, int | None] = {
    "sel_nadalal": None,
    "ulejaanud_kuu": DEADLINE_PREVIEW,
    "kaugemal": 1,
}


def deadline_groups(items: list[wi.WorkItem], today: date) -> list[DeadlineGroup]:
    """Every dated commitment ahead, in the windows :func:`deadline_windows` cuts.

    Only what the department may honestly call a deadline: a DO deadline or an
    *Oluline tähtaeg*. A WAIT's expected date and a MONITOR's review date are
    commitments nobody made and stay in the intervention list, where they read
    as "look at this again" (`wi.real_deadlines`, master specification 18.8).

    The far group is one line: the next date, and how many more sit behind it in
    the register. A list would be a plan nobody can act on today; a number with
    nothing to open would be a figure nobody can check.
    """
    groups = []
    for key, label, starts, ends in deadline_windows(today):
        window = wi.work_population_items(
            items, wi.WORK_DEADLINE_WINDOW, today, window=(starts, ends)
        )
        shown = _DEADLINE_SHOWN[key]
        groups.append(
            DeadlineGroup(
                key,
                label,
                window,
                len(window) if shown is None else shown,
                starts=starts,
                ends=ends,
                is_far=key == "kaugemal",
            )
        )
    return groups
