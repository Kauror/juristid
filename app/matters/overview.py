"""Ülevaade — where the department stands, in three scopes behind one shell.

Minu töö answers *what do I do*. This answers *where is the department losing
time* — for the head, for whoever covers a holiday, and for the person who has
to write the annual report. Three scopes, one page:

``?vaade=osakond``   where is time being lost right now
``?vaade=tiim``      how is each person's week going
``?vaade=valdkonniti`` where does Koda intervene, and what is nobody watching

The scope lives in the URL so a view can be linked, bookmarked and quoted in a
bug report. There is no client-side tab machinery: three links, one view, one
template.

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

from app.accounts.enums import UserRole
from app.accounts.models import User
from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.matters import work_items as wi
from app.matters.activity import activity_of, annotate_last_activity
from app.matters.models import Entry, Matter, MatterEngagement
from app.matters.register_filters import register_population
from app.matters.selectors import MISSING
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.taxonomy.models import PolicyArea
from app.taxonomy.vocabulary import selectable_policy_areas

# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------

SCOPE_PARAM = "vaade"
SCOPE_DEPARTMENT = "osakond"
SCOPE_TEAM = "tiim"
SCOPE_AREAS = "valdkonniti"

SCOPES: tuple[tuple[str, str], ...] = (
    (SCOPE_DEPARTMENT, "Kogu osakond"),
    (SCOPE_TEAM, "Minu tiim"),
    (SCOPE_AREAS, "Valdkonniti"),
)


def scope_from(value: str | None) -> str:
    """The scope a URL asks for, or the department. Unknown falls back."""
    keys = {key for key, _ in SCOPES}
    return value if value in keys else SCOPE_DEPARTMENT


#: The area table's sort keys. In the URL for the same reason the scope is.
SORT_PARAM = "jarjesta"

#: Render the areas that carry no open work as rows too. The area table's own
#: footer link, and the only honest destination for a number that counts areas.
SHOW_EMPTY_AREAS_PARAM = "tuhjad"
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

#: Caps. The number above each list is the honest total regardless.
INTERVENTION_PREVIEW = 6
INTERVENTION_LIMIT = 60
DEADLINE_PREVIEW = 4
DEADLINE_LIMIT = 40
FEED_LIMIT = 12
RAIL_LIMIT = 6
AREA_MATTER_PREVIEW = 4

#: How far the *tähtaega N päeva jooksul* figure looks. A fortnight is the unit
#: a department review actually plans in, and it is the horizon the rest of the
#: product already uses.
DEADLINE_HORIZON_DAYS = 14

#: Roles whose holders carry files and therefore belong in a people list even
#: with nothing open. A READER reads and an ADMINISTRATOR administers.
CASEWORK_ROLES: tuple[str, ...] = (UserRole.SPECIALIST.value, UserRole.DEPARTMENT_HEAD.value)

#: "in August" — the inessive, written out rather than derived.
#:
#: Estonian does not add one suffix to every month: *mais* drops nothing,
#: *märtsis* adds two letters, and *septembris* loses the vowel before the
#: stem's last consonant. A rule guessed from three examples produces
#: *augusts* and *septemberis*, which is exactly the kind of small wrongness
#: that makes a page read as machine-written. The month itself is derived
#: from the date; only its spelling is a table (§16.1).
ESTONIAN_MONTHS_IN: tuple[str, ...] = (
    "jaanuaris",
    "veebruaris",
    "märtsis",
    "aprillis",
    "mais",
    "juunis",
    "juulis",
    "augustis",
    "septembris",
    "oktoobris",
    "novembris",
    "detsembris",
)


#: The register's results region. Every link from this page carries it, so
#: arriving from a number lands on the rows rather than on the filter panel the
#: reader then has to scroll past to find out whether anything came back.
RESULTS_ANCHOR = "#tulemused"

#: The two figures on this page that count something the register does not list
#: — people, and policy areas. Each one opens the list of exactly those, which
#: is on this page.
PEOPLE_ANCHOR = "#inimesed"
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


def _populations(user: Any, pop: Populations | None) -> Populations:
    return pop if pop is not None else Populations.for_user(user)


# ---------------------------------------------------------------------------
# The Seis strip
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Figure:
    """One number on the Seis strip, and the list it opens.

    ``url`` is never empty. A figure a reader cannot follow is a dead end, and a
    dead end teaches people to stop trusting the number beside it.
    """

    key: str
    count: int
    label: str
    url: str
    tone: str = ""


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

#: The query parameter that narrows *Vajab sekkumist* to one kind of trouble.
#:
#: It exists so the Seis strip can keep a promise the register cannot. "12 üle
#: tähtaja" counts late *work* — a DO deadline and an Oluline tähtaeg alike —
#: and the register can only filter Matters by their open action, so a link
#: there would open a list shorter than the number above it. A list shorter than
#: its own count reads as a bug in the count, which is exactly the trust this
#: page cannot afford to spend (Ulevaade brief 21).
#:
#: Read-only, and it narrows a list this page already renders. It is not a
#: second register.
INTERVENTION_PARAM = "sekkumine"

#: What each value of that parameter selects, as the reasons it holds.
INTERVENTION_ALL = "koik"

INTERVENTION_FILTERS: dict[str, tuple[str, ...]] = {
    "hilinenud": (REASON_OVERDUE, REASON_IMPORTANT),
    "sammuta": (REASON_NO_ACTION,),
    "vastutajata": (REASON_OWNERLESS,),
    "ulevaatamiseks": (REASON_RIPE,),
    # Every reason, uncapped. It exists because the list's own "Näita kõiki N"
    # link had nowhere honest to go: it carried `sekkumine=hilinenud`, so a
    # footer promising all forty rows opened the nine that were late
    # (Ülevaade QA §3).
    INTERVENTION_ALL: (
        REASON_OVERDUE,
        REASON_IMPORTANT,
        REASON_NO_ACTION,
        REASON_OWNERLESS,
        REASON_RIPE,
    ),
}

#: How each filtered list describes itself above the rows.
INTERVENTION_LABELS: dict[str, str] = {
    "hilinenud": "üle tähtaja",
    "sammuta": "järgmise tegevuseta",
    "vastutajata": "vastutajata",
    "ulevaatamiseks": "ülevaatamiseks küpsed",
    INTERVENTION_ALL: "kõik põhjused",
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
                    meaning=f"{item.action_kind_label.upper()} AL {_short(item.when)}",
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
                value="sammuta",
                meaning=f"{(today - since).days} P VAIKUST"
                if since
                else "VIIMANE TEGEVUS TEADMATA",
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
# Tähtajad
# ---------------------------------------------------------------------------


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
    #: The `?too=` value that reproduces this window on the register.
    population: str

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
    def matter_count(self) -> int:
        return len({item.matter_id for item in self.items})

    @property
    def url(self) -> str:
        return _teemad(**_OPEN_FULL, too=self.population)


#: Kept as a name here because three test modules and two callers read it from
#: this module; the definition itself lives with the read model, so the register
#: filters on the same predicate the table renders.
real_deadlines = wi.real_deadlines


def deadline_groups(items: list[wi.WorkItem], today: date) -> list[DeadlineGroup]:
    this_week = wi.work_population_items(items, wi.WORK_DEADLINE_THIS_WEEK, today)
    next_week = wi.work_population_items(items, wi.WORK_DEADLINE_NEXT_WEEK, today)
    return [
        DeadlineGroup(
            "sel_nadalal",
            "Sel nädalal",
            this_week,
            len(this_week),
            wi.WORK_DEADLINE_THIS_WEEK,
        ),
        DeadlineGroup(
            "jargmisel",
            "Järgmisel",
            next_week,
            DEADLINE_PREVIEW,
            wi.WORK_DEADLINE_NEXT_WEEK,
        ),
    ]


# ---------------------------------------------------------------------------
# Viimane tegevus
# ---------------------------------------------------------------------------

FEED_ALL = "koik"
FEED_ENTRIES = "sissekanded"
FEED_SUBMISSIONS = "arvamused"
FEED_STATUS = "staatus"

FEED_FILTERS: tuple[tuple[str, str], ...] = (
    (FEED_ALL, "Kõik"),
    (FEED_ENTRIES, "Sissekanded"),
    (FEED_SUBMISSIONS, "Arvamused"),
    (FEED_STATUS, "Staatuse muutused"),
)

FEED_PARAM = "voog"

#: The events a person would recognise as something a colleague did. Field-level
#: corrections are deliberately absent: a feed that reports a deadline moved by
#: a day is an audit log, and an audit log is not read.
_STATUS_EVENTS: tuple[str, ...] = (
    ChangeEventType.MATTER_STAGE_CHANGED,
    ChangeEventType.MATTER_ASSIGNED,
    ChangeEventType.MATTER_CLOSED,
    ChangeEventType.MATTER_REOPENED,
    ChangeEventType.MATTER_CREATED,
)

_EVENT_VERBS: dict[str, str] = {
    ChangeEventType.MATTER_STAGE_CHANGED: "muutis hetkeseisu",
    ChangeEventType.MATTER_ASSIGNED: "määras vastutaja",
    ChangeEventType.MATTER_CLOSED: "sulges teema",
    ChangeEventType.MATTER_REOPENED: "avas teema uuesti",
    ChangeEventType.MATTER_CREATED: "avas teema",
}


@dataclass(frozen=True)
class FeedItem:
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
    def url(self) -> str:
        return (
            reverse("matters:matter_detail", kwargs={"pk": self.matter.pk})
            if self.matter is not None
            else ""
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
                verb="lisas sissekande",
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
                verb="esitas arvamuse",
                matter=submission.matter,
            )
            for submission in sent
        ]

    if kind in (FEED_ALL, FEED_STATUS):
        events = (
            ChangeEvent.objects.filter(
                event_type__in=_STATUS_EVENTS,
                occurred_at__date__gte=since,
                matter__in=Matter.objects.visible_to(user).values("pk"),
            )
            .select_related("matter", "actor")
            .order_by("-occurred_at")[:FEED_LIMIT]
        )
        items += [
            FeedItem(
                when=event.occurred_at,
                actor=event.actor,
                verb=_EVENT_VERBS.get(event.event_type, "muutis teemat"),
                matter=event.matter,
            )
            for event in events
        ]

    return sorted(items, key=lambda item: item.when, reverse=True)[:FEED_LIMIT]


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersonLoad:
    """One colleague's inventory and attention counts. Never a ranking.

    ``overdue`` counts **Matters**, not late rows. Two missed deadlines on one
    file are one file to open, and the register — which is where the number
    leads — lists Matters. Counting rows here and listing Matters there is how a
    "3 üle tähtaja" link opens two rows (Ülevaade QA §3).
    """

    user: Any
    open_count: int
    overdue: int
    week: int
    no_action: int
    items: list[wi.WorkItem] = field(default_factory=list)
    later: int = 0

    @property
    def name(self) -> str:
        return self.user.display_name

    @property
    def initials(self) -> str:
        return self.user.initials

    @property
    def is_clear(self) -> bool:
        return self.overdue == 0

    @property
    def url(self) -> str:
        return _teemad(**_OPEN_FULL, vastutaja=self.user.pk)

    @property
    def overdue_url(self) -> str:
        # `too_vastutaja`, not `vastutaja`: this counts the late work this
        # person is *responsible* for, which is a different question from which
        # files they own — and the row prints the two side by side precisely
        # because they are different (master specification 18.1).
        return _teemad(**_OPEN_FULL, too=wi.WORK_OVERDUE, too_vastutaja=self.user.pk)

    @property
    def no_action_url(self) -> str:
        return _teemad(**_OPEN_FULL, vastutaja=self.user.pk, tegevus=MISSING)


def _people(user: Any, owner_ids: set[Any]) -> list[Any]:
    return list(
        User.objects.filter(
            Q(is_active=True, role__in=CASEWORK_ROLES) | Q(pk__in=owner_ids)
        ).order_by("display_name")
    )


def _count_by_owner(queryset: QuerySet[Matter]) -> dict[Any, int]:
    """One authorized population per owner, in one query.

    The aggregate runs over a re-wrapped query rather than over the population
    directly: the populations arriving here carry annotations, orderings and a
    ``.distinct()`` from ``visible_to``, and each of those leaks into a
    ``values().annotate()`` GROUP BY in its own way — splitting one person's
    total across several rows that still look like numbers. Restricting an
    unannotated query to the authorized primary keys gives a GROUP BY of exactly
    ``owner_id`` whatever came in, and the inner query is still the authorized
    one.
    """
    grouped = (
        Matter.objects.filter(pk__in=queryset.values("pk"))
        .order_by()
        .values("owner_id")
        .annotate(total=Count("id"))
    )
    return {row["owner_id"]: row["total"] for row in grouped}


def person_loads(
    user: Any,
    today: date,
    items: list[wi.WorkItem],
    *,
    with_week: bool = False,
    pop: Populations | None = None,
) -> list[PersonLoad]:
    """Per-person counts, with the responsibility rules kept apart on purpose.

    Open Matters and the no-next-action count are **ownership**: they describe
    a portfolio. The dated work is **responsibility**: a NextAction belongs to
    whoever must do it, and an ``Oluline tähtaeg`` to the Matter's current
    owner. These are genuinely different questions and collapsing them into one
    "workload" figure would answer neither (§18.1).
    """
    people = _populations(user, pop)
    open_by_owner = _count_by_owner(people.open_matters)
    quiet_by_owner = _count_by_owner(people.quiet)
    week_end = wi.end_of_iso_week(today)

    per_person: dict[Any, list[wi.WorkItem]] = {}
    for item in items:
        if item.responsible is not None:
            per_person.setdefault(item.responsible.pk, []).append(item)

    loads: list[PersonLoad] = []
    for person in _people(user, {key for key in open_by_owner if key is not None}):
        mine = per_person.get(person.pk, [])
        week = wi.week_items(mine, today, week_end)
        overdue = wi.overdue_items(mine)
        ripe = wi.review_ripe_items(mine)
        shown = wi.sort_items(overdue + ripe + week) if with_week else []
        loads.append(
            PersonLoad(
                user=person,
                open_count=open_by_owner.get(person.pk, 0),
                overdue=len({item.matter_id for item in overdue}),
                week=len(week),
                no_action=quiet_by_owner.get(person.pk, 0),
                items=shown,
                later=max(0, len(mine) - len(shown)),
            )
        )
    return loads


def unassigned_count(user: Any, pop: Populations | None = None) -> int:
    return _populations(user, pop).ownerless.count()


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


def attach_area_matters(
    user: Any, rows: list[AreaRow], limit: int = 1, pop: Populations | None = None
) -> None:
    """Load the compact Matter lines for the rows that open on arrival.

    Only the rows that are actually expanded. Fetching every area's Matters so
    that a caret *could* open instantly is a page that reads the whole register
    to render a table of counts (§27).
    """
    people = _populations(user, pop)
    for row in rows[:limit]:
        object.__setattr__(
            row,
            "matters",
            list(
                people.open_matters.filter(policy_areas__key=row.key)
                .select_related("stage", "owner")
                .order_by("response_deadline")[:AREA_MATTER_PREVIEW]
            ),
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


def reporting_counts(user: Any, today: date, pop: Populations | None = None) -> list[CountRow]:
    """Current-year canonical counts, each from the selector its own list uses.

    *Esitatud arvamusi* is SENT ``Submission`` rows. The 767 historical archive
    files are evidence of past correspondence, not canonical sent opinions, and
    counting them here would inflate the department's year by an order of
    magnitude (ADR 0021).

    Each row now carries its year into its link, which is the half that was
    missing: the label said 2026 and the link opened every year there was. The
    closed row is counted *through* the register's own filter pipeline, the way
    the deadline figure is; the sent row narrows the submission population this
    page already resolved, and ``tests/test_overview_drilldowns.py`` asserts it
    against what the destination actually shows.
    """
    people = _populations(user, pop)
    year = today.year
    closed_params = {"olek": "suletud", "suletud": str(year)}
    return [
        CountRow(
            # The year is in the link as well as in the label. It was in the
            # label alone, so "Esitatud arvamusi 2026" opened every opinion the
            # department had ever sent (Ülevaade QA §3).
            label=f"Esitatud arvamusi {year}",
            count=people.submissions.filter(
                status=SubmissionStatus.SENT, sent_at__year=year
            ).count(),
            url=f"{reverse('submissions:sent')}?aasta={year}",
        ),
        CountRow(
            # No destination: this product has no list of Kaasamine records
            # outside the Matter that carries them, and a link to something
            # adjacent is worse than no link at all.
            label=f"Kaasamisi {year}",
            count=MatterEngagement.objects.visible_to(user).filter(occurred_on__year=year).count(),
        ),
        CountRow(
            label=f"Suletud teemasid {year}",
            # Counted *through* the register's own filter pipeline, the way the
            # deadline figure is, so the count and the list are one query rather
            # than two that resemble each other. `?liik=` is deliberately absent
            # from both halves: an archive row closed this year is still one of
            # this year's completions.
            count=register_population(user, closed_params, today=today).count(),
            url=_teemad(**closed_params),
        ),
    ]


def new_matters(user: Any, today: date, pop: Populations | None = None) -> list[CountRow]:
    people = _populations(user, pop)
    week_start = today - timedelta(days=today.weekday())
    return [
        CountRow(
            label="Jaotamata",
            count=unassigned_count(user, people),
            url=_teemad(**_OPEN_FULL, vastutaja=MISSING),
            tone="warning",
        ),
        CountRow(
            label="Uusi sellel nädalal",
            count=people.open_matters.filter(received_date__gte=week_start).count(),
            url=_teemad(**_OPEN_FULL, saabus_alates=format_estonian_date(week_start)),
        ),
    ]


# ---------------------------------------------------------------------------
# The whole page
# ---------------------------------------------------------------------------


@dataclass
class Overview:
    scope: str
    today: date
    figures: list[Figure] = field(default_factory=list)
    interventions: list[InterventionRow] = field(default_factory=list)
    intervention_total: int = 0
    #: Distinct Matters behind the whole list, uncapped. A different number from
    #: `intervention_total`, and deliberately: one Matter can be late *and*
    #: unowned, which is two rows and one file to open.
    intervention_matters: int = 0
    intervention_url: str = ""
    intervention_filter: str = ""
    deadlines: list[DeadlineGroup] = field(default_factory=list)
    feed: list[FeedItem] = field(default_factory=list)
    feed_filter: str = FEED_ALL
    people: list[PersonLoad] = field(default_factory=list)
    areas: list[AreaRow] = field(default_factory=list)
    empty_areas: int = 0
    show_empty_areas: bool = False
    area_total: int = 0
    sort: str = SORT_OPEN
    loads: list[PersonLoad] = field(default_factory=list)
    unassigned: int = 0
    area_rail: list[CountRow] = field(default_factory=list)
    unowned_areas: list[AreaRow] = field(default_factory=list)
    organisations: list[CountRow] = field(default_factory=list)
    reporting: list[CountRow] = field(default_factory=list)
    incoming: list[CountRow] = field(default_factory=list)
    team_activity: list[CountRow] = field(default_factory=list)

    @property
    def is_department(self) -> bool:
        return self.scope == SCOPE_DEPARTMENT

    @property
    def is_team(self) -> bool:
        return self.scope == SCOPE_TEAM

    @property
    def is_areas(self) -> bool:
        return self.scope == SCOPE_AREAS

    @property
    def intervention_preview(self) -> list[InterventionRow]:
        # A filtered list is what the reader asked for, so it is shown whole
        # rather than trimmed to six: they arrived from a number and the
        # rows have to add up to it.
        if self.intervention_filter:
            return self.interventions
        return self.interventions[:INTERVENTION_PREVIEW]

    @property
    def intervention_label(self) -> str:
        return INTERVENTION_LABELS.get(self.intervention_filter, "")

    @property
    def intervention_remaining(self) -> int:
        if self.intervention_filter:
            return 0
        return max(0, self.intervention_total - INTERVENTION_PREVIEW)


def drafting_count(user: Any, pop: Populations | None = None) -> int:
    """Canonical opinions being written now, counted by the list's own selector."""
    from app.submissions import workspace

    people = _populations(user, pop)
    return workspace.drafting(user, people.submissions).count()


def drafting_url() -> str:
    """The Arvamused workspace, showing exactly what :func:`drafting_count` counted."""
    from app.submissions import workspace

    return f"{reverse('submissions:sent')}?{workspace.DRAFTING_QUERY}"


def _department_figures(
    user: Any, today: date, items: list[wi.WorkItem], pop: Populations | None = None
) -> list[Figure]:
    horizon = today + timedelta(days=DEADLINE_HORIZON_DAYS)
    people = _populations(user, pop)
    month = ESTONIAN_MONTHS_IN[today.month - 1]
    deadline_params = {
        **_OPEN_FULL,
        "tahtaeg_alates": format_estonian_date(today),
        "tahtaeg_kuni": format_estonian_date(horizon),
    }
    sent = people.submissions.filter(
        status=SubmissionStatus.SENT, sent_at__year=today.year, sent_at__month=today.month
    ).count()
    overdue_ids = wi.work_population_ids(user, wi.WORK_OVERDUE, today=today, items=items)
    return [
        Figure(
            "open",
            people.open_matters.count(),
            "avatud teemat",
            _teemad(**_OPEN_FULL),
        ),
        Figure(
            "overdue",
            # Matters, because Matters is what the list holds. Two missed
            # deadlines on one file are one file to open, and the register
            # cannot list a row twice to make a work-item count come out.
            len(overdue_ids),
            "teemat üle tähtaja",
            # The register, through `?too=` — the read model's own population,
            # resolved by the read model's own function. It used to narrow this
            # page's intervention list instead, because `?tegevus=` cannot
            # express a passed `Oluline tähtaeg`; `?too=` can, so the figure now
            # opens the register like every figure beside it (Ülevaade QA §3).
            _teemad(**_OPEN_FULL, too=wi.WORK_OVERDUE),
            tone="danger",
        ),
        Figure(
            "no_action",
            people.quiet.count(),
            "järgmise tegevuseta",
            _teemad(**_OPEN_FULL, tegevus=MISSING),
            tone="warning",
        ),
        Figure(
            "deadlines",
            # Counted *through* the register's own filter pipeline rather than
            # beside it, so the figure and the list it opens are one query. The
            # wording says which deadline this is: the department watches other
            # dates too, and `Tähtajad` below shows them.
            register_population(user, deadline_params, today=today).count(),
            f"arvamuse tähtaega {DEADLINE_HORIZON_DAYS} päeva jooksul",
            _teemad(**deadline_params),
        ),
        Figure(
            "drafting",
            # Canonical Submissions in DRAFT — opinions somebody is writing now.
            # Not the register's VÄLJA column, which records what the *Excel*
            # era knew about a sent date, and not the historical archive, whose
            # 767 letters are evidence of past correspondence rather than work
            # in progress (ADR 0021, ADR 0028).
            #
            # Counted by the same selector the destination lists with, so the
            # figure and the Arvamused workspace cannot disagree.
            drafting_count(user, people),
            "arvamust koostamisel",
            drafting_url(),
            tone="warning",
        ),
        Figure(
            "submissions",
            sent,
            f"esitatud arvamust {month}",
            f"{reverse('submissions:sent')}?aasta={today.year}&kuu={today.month}",
        ),
    ]


def build_overview(
    user: Any,
    *,
    scope: str = SCOPE_DEPARTMENT,
    today: date | None = None,
    sort: str = SORT_OPEN,
    feed_filter: str = FEED_ALL,
    intervention_filter: str = "",
    show_empty_areas: bool = False,
) -> Overview:
    """Assemble one scope. The shell is the same for all three."""
    today = today or timezone.localdate()
    scope = scope_from(scope)
    if intervention_filter not in INTERVENTION_FILTERS:
        # An unrecognised value shows the whole list rather than an empty one:
        # a mistyped URL must not look like an answer.
        intervention_filter = ""
    page = Overview(
        scope=scope,
        today=today,
        sort=sort,
        feed_filter=feed_filter,
        intervention_filter=intervention_filter,
    )
    people = Populations.for_user(user)

    # One read of the shared model, reused by every figure, list and per-person
    # count on the page. The alternative is the same query five times with five
    # slightly different filters, which is how a strip stops agreeing with the
    # list under it.
    items = wi.work_items(user, today=today)

    if scope == SCOPE_DEPARTMENT:
        page.figures = _department_figures(user, today, items, people)
        rows = intervention_rows(user, today, items, pop=people)
        if intervention_filter:
            wanted = INTERVENTION_FILTERS[intervention_filter]
            rows = [row for row in rows if row.reason in wanted]
        page.interventions = rows
        page.intervention_total = len(rows)
        page.intervention_matters = len(
            wi.work_population_ids(
                user,
                wi.WORK_NEEDS_ATTENTION,
                today=today,
                items=items,
                quiet=people.quiet,
                ownerless=people.ownerless,
            )
        )
        page.intervention_url = _teemad(**_OPEN_FULL, too=wi.WORK_NEEDS_ATTENTION)
        page.deadlines = deadline_groups(items, today)
        page.feed = activity_feed(user, today, feed_filter)
        page.loads = person_loads(user, today, items, pop=people)
        page.unassigned = unassigned_count(user, people)
        page.area_rail = [
            CountRow(label=row.name, count=row.open_count, url=row.url)
            for row in area_rows(user, today, items, pop=people)[0][:5]
        ]
        page.incoming = new_matters(user, today, people)
        page.reporting = reporting_counts(user, today, people)
        return page

    if scope == SCOPE_TEAM:
        page.people = person_loads(user, today, items, with_week=True, pop=people)
        page.loads = page.people
        page.unassigned = unassigned_count(user, people)
        week_end = wi.end_of_iso_week(today)
        deadlines = real_deadlines(items)
        page.figures = [
            # The one figure on this page that does not count Matters, so its
            # destination is the list of people below rather than the register:
            # following it must land on exactly the N this number claims, and
            # this page *is* that list (Ülevaade QA §3).
            Figure(
                "people",
                len(page.people),
                "inimest",
                f"?{SCOPE_PARAM}={SCOPE_TEAM}{PEOPLE_ANCHOR}",
            ),
            # The department's open Matters, not the sum of the rows below.
            # Summing per-person counts silently drops every unowned file — the
            # ones the rail lists as *Vastutajata* — so the strip disagreed with
            # the register link beside it by exactly that many.
            Figure(
                "open",
                people.open_matters.count(),
                "avatud teemat",
                _teemad(**_OPEN_FULL),
            ),
            Figure(
                "overdue",
                len(wi.work_population_ids(user, wi.WORK_OVERDUE, today=today, items=items)),
                "teemat üle tähtaja",
                _teemad(**_OPEN_FULL, too=wi.WORK_OVERDUE),
                tone="danger",
            ),
            Figure(
                "no_action",
                people.quiet.count(),
                "järgmise tegevuseta",
                _teemad(**_OPEN_FULL, tegevus=MISSING),
                tone="warning",
            ),
        ]
        page.deadlines = deadline_groups(items, today)
        page.incoming = new_matters(user, today, people)
        page.team_activity = [
            CountRow(
                label="Sissekandeid sel nädalal",
                count=Entry.objects.visible_to(user)
                .filter(occurred_at__date__gte=week_end - timedelta(days=6))
                .count(),
            ),
            CountRow(
                label=f"Esitatud arvamusi {ESTONIAN_MONTHS_IN[today.month - 1]}",
                count=people.submissions.filter(
                    status=SubmissionStatus.SENT,
                    sent_at__year=today.year,
                    sent_at__month=today.month,
                ).count(),
            ),
            CountRow(label="Tähtaegu sel nädalal", count=len(wi.week_items(deadlines, today))),
        ]
        return page

    areas, empty = area_rows(
        user, today, items, sort=sort, include_empty=show_empty_areas, pop=people
    )
    attach_area_matters(user, areas, pop=people)
    page.areas = areas
    page.empty_areas = empty
    page.show_empty_areas = show_empty_areas
    page.area_total = len(areas) if show_empty_areas else len(areas) + empty
    unowned = [row for row in areas if row.is_unowned]
    page.unowned_areas = unowned
    page.figures = [
        Figure("open", people.open_matters.count(), "avatud teemat", _teemad(**_OPEN_FULL)),
        Figure(
            "unowned",
            len(unowned),
            "valdkonda vastutajata",
            # This counts *areas*, so it opens the list of those areas — the
            # rail block beside the table — and not the register. It linked to
            # every ownerless Matter, which is a different population with a
            # different number: three areas, eleven files (Ülevaade QA §3).
            f"?{SCOPE_PARAM}={SCOPE_AREAS}{UNOWNED_ANCHOR}",
            tone="warning",
        ),
    ]
    page.organisations = organisation_ranking(user, pop=people)
    page.reporting = reporting_counts(user, today, people)
    return page
