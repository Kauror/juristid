"""Osakond's read model — what is going on across the lawyers, and where it stands.

The department page's populations live here. It served *Osakonna töö* when that
was a surface of its own; since the merge it and :mod:`app.matters.overview`
are the two read models one page composes, and the composition is
:mod:`app.matters.department` (docs/adr/0049).

* **Seis** — six risks rather than six counters, each opening exactly its rows.
* **Mida meeskond teeb** — the Meeskond table, one row per person. Head only.
* **Mis on ees** — Eesolev, the department's real deadlines in five windows.
* **Mis on tehtud** — Tehtud, what came out of a period the reader chooses,
  narrowable by row kind. Head only.
* the *Uued teemad* and *Aruandlus* rail blocks.

Four rules run through every function here.

**Authorization before arithmetic.** Every queryset starts from
``visible_to(user)``. A department head sees RESTRICTED content because
``DEPARTMENT_HEAD`` is a role the central authorization already entitles — not
because this module decided so, and nothing here re-implements ``visibility ==
NORMAL or …``. Point the same functions at a specialist and they return the
specialist's authorized world. Whether the *manager* sections are built at all
is a separate question, decided by the caller from the authenticated role.

**Every number opens the list it counted.** Each figure is defined *as* register
parameters; the count is those parameters run through the register's own filter
pipeline and the link is those parameters as a query string. A count and a
drill-through that disagree is the failure this discipline exists to prevent
(``register_population``, master specification 18.9). Three columns count
history the register cannot express as a current state, and those carry no link
at all — an honest number beats a link to a different list.

**Definitions are imported, never restated.** Overdue, this week, "no next
action", "still drafting", "a real deadline" all come from
:mod:`app.matters.dashboard` and :mod:`app.matters.work_items`. Two similar
definitions in two files is how two screens start disagreeing about the same
Matter, and the head is precisely the person who would notice and stop trusting
both. It is also why one business fact has one definition *within* this module:
the team table's year column and Aruandlus's «Saadetud arvamusi» read one
population over one window, and the equality is asserted.

**This is not a staff evaluation.** Every column is a count of observable
states. There is no ranking, no score, no rate, no percentage and no colour
grading of people. Lawyers are listed alphabetically, and that ordering is
load-bearing: sorting the table by any of its numbers would turn an oversight
tool into a leaderboard, which is both forbidden (specification 18.8) and the
fastest way to make the underlying data worth gaming. A count of open matters is
inventory — one of them can be a two-line monitoring note and the next a year of
consultation — so it measures neither effort nor performance and is never
labelled as if it did.

What the page no longer carries, and where it went
--------------------------------------------------

Nothing that was dropped lost its path:

* *Ülevaatus või ootamine vajab pilku* is ``?too=ulevaatamiseks`` on the
  register, and its rows are in the intervention list on the same page;
* per-lawyer *recently received* is ``?vastutaja=…&saabus_alates=…``;
* *Aktiivne teema ilma hetkeseisuta* is ``?hetkeseis=puudub``, and the full
  picture is Statistika → Andmekvaliteet;
* the *Vajab sekkumist* rail block restated four counts the main column already
  lists as rows, and *Koormus* asked the question the Meeskond table answers
  with more of the answer (docs/adr/0049).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlencode

from django.db.models import Count, Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from app.accounts.enums import UserRole
from app.accounts.models import User
from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.audit.visibility import scope_change_events
from app.core.dates import (
    end_of_month,
    format_estonian_date,
    parse_flexible_date,
    short_day_month,
    short_range,
    weekday_name,
)
from app.intelligence.enums import WorkVictoryStatus
from app.intelligence.models import MatterWorkVictory
from app.matters import work_items as wi
from app.matters.dashboard import (
    active_matters,
    drafting_matters,
    without_next_action,
)
from app.matters.enums import RecordMode
from app.matters.models import Entry, Matter
from app.matters.register_filters import (
    OPINION_DRAFTING,
    RESULTS_ANCHOR,
    WORK_PARAM,
    register_population,
)
from app.matters.selectors import MISSING
from app.matters.timeline import TIMELINE_EVENT_TYPES
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission

#: How far back "recently arrived" reaches. A fortnight, because a department
#: review is a fortnightly conversation.
INCOMING_WINDOW_DAYS = 14

#: Roles whose holders do casework and therefore belong in the team table even
#: with nothing open. A READER reads and an ADMINISTRATOR administers; neither
#: carries files, and listing them with a row of dashes would suggest they
#: should.
#:
#: Deliberately *not* `app.accounts.selectors.DEPARTMENT_WORK_ROLES`, despite
#: naming the same two roles. This is a report population, not a chooser: the
#: query below unions it with everybody who currently owns something, so a
#: departed colleague — or a technical account that was handed a file years ago
#: — keeps their row and their open work stays visible. The assignment rule is
#: stricter on purpose (it also refuses `is_staff` and `is_superuser`), and
#: adopting it here would take live work off the page that finds it
#: (docs/adr/0036 §"What this does not change").
CASEWORK_ROLES: tuple[str, ...] = (
    UserRole.SPECIALIST.value,
    UserRole.DEPARTMENT_HEAD.value,
)


def register_url(**params: Any) -> str:
    """A link into Teemad with filters already applied, landing on the rows.

    Only parameters the register actually supports today. Every number that
    links to a list is a promise that the list behind it holds exactly those
    rows, and the promise is kept by reusing the register's own query
    parameters rather than inventing a parallel query language beside Stage
    2E.1's (Stage-2F brief 35).
    """
    query = "&".join(f"{key}={value}" for key, value in params.items() if value)
    base = reverse("matters:matter_list")
    return f"{base}?{query}{RESULTS_ANCHOR}" if query else f"{base}{RESULTS_ANCHOR}"


def _open_full() -> dict[str, Any]:
    """The register filters that mean "open FULL", as the list understands them."""
    return {"olek": "avatud", "liik": RecordMode.FULL.value}


def _by_owner(queryset: QuerySet[Matter]) -> dict[Any, int]:
    """Count one authorized population per owner, in one query.

    The aggregate deliberately runs over a **re-wrapped** query rather than
    over the population directly, because the populations arriving here are not
    all the same shape. Some carry an ``Exists`` annotation; some have had
    ``.distinct()`` applied by ``visible_to``; all carry Matter's
    ``Meta.ordering``. Each of those leaks into a ``values().annotate()``
    aggregate in its own way — an annotation and an ordering column both end up
    in the GROUP BY, which splits one person's total across several rows, and a
    result that is silently split still looks like a number.

    Restricting an unannotated, unordered query to the authorized primary keys
    gives a GROUP BY of exactly ``owner_id`` whatever came in. Authorization is
    not weakened: the inner query is the authorized one, and the outer can only
    see rows it returned.
    """
    grouped = (
        Matter.objects.filter(pk__in=queryset.values("pk"))
        .order_by()
        .values("owner_id")
        .annotate(total=Count("id"))
    )
    return {row["owner_id"]: row["total"] for row in grouped}


# ---------------------------------------------------------------------------
# Seis — the manager's risk strip
#
# Not general counters. Six states somebody can act on this morning, each one a
# link to exactly the rows it counted (design handoff, Osakond §1).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeisFigure:
    """One number on the risk strip, with the list it opens.

    ``url`` is empty for a figure the application cannot open exactly. That is
    rare and deliberate: an honest number beats a link to a different list, and
    it is the same treatment the team table's three historical columns get
    (`_column_url`, master specification 18.9).
    """

    key: str
    value: int
    caption: str
    url: str
    #: `danger`, `warning` or empty. Emphasis only: the caption beside every
    #: figure says what it is, so nothing here is carried by colour alone.
    tone: str = ""


#: How far back "recently arrived, nobody has looked" reaches.
UNREVIEWED_WINDOW_DAYS = INCOMING_WINDOW_DAYS

#: The trailing window on the sent-opinions figure.
SENT_WINDOW_DAYS = 7


def _unreviewed_params(today: date) -> dict[str, Any]:
    """«Uued saabunud, läbi vaatamata», as register parameters.

    There is no *triaged* flag on a Matter and this deliberately does not invent
    one. What the register can say, and what a department head actually means by
    the phrase, is: it arrived in the last fortnight, nobody has taken it, and
    nobody has said what happens to it. Three stored facts, no new column, and a
    link that opens exactly those rows.

    The word is «läbi vaatamata». Never «triaaž» (design handoff, wording).
    """
    return {
        **_open_full(),
        "vastutaja": MISSING,
        "tegevus": MISSING,
        "saabus_alates": format_estonian_date(today - timedelta(days=UNREVIEWED_WINDOW_DAYS)),
        "saabus_kuni": format_estonian_date(today),
    }


def _arrived_this_week_params(today: date) -> dict[str, Any]:
    return {
        **_open_full(),
        "saabus_alates": format_estonian_date(wi.start_of_iso_week(today)),
        "saabus_kuni": format_estonian_date(today),
    }


def _no_action_params() -> dict[str, Any]:
    """«Järgmise tegevuseta», as register parameters.

    The register's own `?tegevus=puudub`, which resolves through
    `selectors.filter_by_next_action` to the same reader-scoped population
    `app.matters.dashboard.without_next_action` describes — an open FULL Matter
    carrying no open instruction. Expressed as parameters rather than as a
    queryset so the figure counts and the link opens one query, like every
    other figure on the strip.
    """
    return {**_open_full(), "tegevus": MISSING}


def sent_submissions(user: Any, *, since: date, until: date | None = None) -> QuerySet[Submission]:
    """Opinions Koda actually sent inside a window, for this reader.

    ``Submission.objects.visible_to`` rather than a filter on the Matter: a
    submission carries its own visibility override and may be stricter than the
    file it belongs to (app/submissions/models.py).

    Compared on the **local** date. `sent_at` is a timestamp and Estonia is two
    or three hours ahead of UTC, so a letter sent at half past midnight belongs
    to the day the sender was living in.
    """
    queryset = Submission.objects.visible_to(user).filter(
        status=SubmissionStatus.SENT, sent_at__isnull=False
    )
    queryset = queryset.filter(sent_at__date__gte=since)
    if until is not None:
        queryset = queryset.filter(sent_at__date__lte=until)
    return queryset


def seis_figures(
    user: Any, today: date | None = None, *, items: list[wi.WorkItem] | None = None
) -> list[SeisFigure]:
    """The strip, in the head's reading order: what is late, then what is loose.

    Every count runs through the register's own filter pipeline over the
    parameters that *are* its definition, so the number and the list behind it
    are one query rather than two similar ones (`register_population`, master
    specification 18.9).

    ``items`` is the page's single unnarrowed read of the work model, offered so
    that the two `?too=` figures here do not each read it again. It changes no
    number: it is the same list `register_population` would otherwise fetch for
    the same reader on the same day.
    """
    today = today or timezone.localdate()
    population = Matter.objects.visible_to(user)

    def count(params: dict[str, Any]) -> int:
        return register_population(
            user, params, today=today, population=population, shared_items=items
        ).count()

    overdue = {**_open_full(), WORK_PARAM: wi.WORK_OVERDUE}
    this_week = {**_open_full(), WORK_PARAM: wi.WORK_DEADLINE_THIS_WEEK}
    unassigned = {**_open_full(), "vastutaja": MISSING}
    unreviewed = _unreviewed_params(today)
    no_action = _no_action_params()

    return [
        SeisFigure("overdue", count(overdue), "üle tähtaja", register_url(**overdue), "danger"),
        SeisFigure("week", count(this_week), "tähtaeg sel nädalal", register_url(**this_week)),
        SeisFigure(
            "unassigned", count(unassigned), "vastutajata", register_url(**unassigned), "warning"
        ),
        SeisFigure(
            "unreviewed",
            count(unreviewed),
            "uut läbi vaatamata",
            register_url(**unreviewed),
            "warning",
        ),
        # «Uut sel nädalal» stood here. It is arrival, not risk — a file that
        # came in on Tuesday is on the strip whether or not anything is wrong
        # with it — and it is a row of *Uued teemad* in the rail, where the rest
        # of the arrival picture already is. What replaced it is the state a
        # head can actually act on: an open file nobody has said what happens to
        # (design handoff C §3.2).
        SeisFigure(
            "no_action",
            count(no_action),
            "järgmise tegevuseta",
            register_url(**no_action),
            "warning",
        ),
        # No link. The count is a seven-day window and the Arvamused workspace
        # filters by year and month, so the only destination available lists
        # more opinions than the number beside it — which is precisely the
        # count-and-list disagreement this strip exists to make impossible.
        # It linked there before the merge and the parity sweep caught it the
        # first time both pages' figures were on one strip
        # (`e2e/test_kpi_navigation.py`, DS-24).
        SeisFigure(
            "sent",
            sent_submissions(user, since=today - timedelta(days=SENT_WINDOW_DAYS)).count(),
            f"arvamust välja · {SENT_WINDOW_DAYS} p",
            "",
        ),
    ]


# ---------------------------------------------------------------------------
# Meeskond
#
# One row per person, and the same refusal the module opens with: this is
# inventory and attention, never workload and never a ranking. Alphabetical, and
# that ordering is load-bearing.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatCell:
    """One number in the team table."""

    value: int
    #: Empty where the register cannot express this column. A number with no
    #: link is honest; a link to a list that does not match it is not.
    url: str = ""
    tone: str = ""
    #: First column of a group, and therefore the one that draws the hairline
    #: separating "right now" from "last week" from "this year".
    sep: bool = False
    #: What this number is, in words, for a reader who cannot see the column
    #: heading above it. The grid is a grid rather than a `<table>` — its rows
    #: are links — so no header is *associated* with any cell, and each one has
    #: to name itself. It carries the group as well, because two columns are
    #: both called `ARVAMUSI VÄLJA` and only the group tells them apart.
    label: str = ""

    @property
    def is_zero(self) -> bool:
        return self.value == 0


@dataclass(frozen=True)
class TeamRow:
    """One person's row, or the unassigned row, or the total."""

    key: str
    name: str
    initials: str
    cells: tuple[StatCell, ...]
    is_self: bool = False
    is_former: bool = False
    is_unassigned: bool = False
    is_total: bool = False
    url: str = ""


#: The columns, in the order they are read, with the group each belongs to and
#: whether it opens that group.
#: `now` is the current portfolio, `week` is what moved in the previous week and
#: `year` is the running total. The headings above them say which is which,
#: because a number about last week beside a number about right now is two
#: different questions in one row (design handoff, Osakond §2).
TEAM_COLUMNS: tuple[tuple[str, str, str, bool], ...] = (
    ("open", "AVATUD", "now", False),
    ("overdue", "ÜLE TÄHTAJA", "now", False),
    ("week", "TÄHTAEG SEL NÄD", "now", False),
    ("no_action", "TEGEVUSETA", "now", False),
    ("drafting", "ARVAMUS KOOSTAMISEL", "now", False),
    ("changed", "TEEMADES MUUDATUSI", "week", True),
    ("sent_week", "ARVAMUSI VÄLJA", "week", False),
    ("sent_year", "ARVAMUSI VÄLJA", "year", True),
)

#: Which columns are emphasised, and how. The values are the stylesheet's own
#: modifier suffixes rather than words the template maps: a template that
#: translated "danger" into "bad" would be a second naming of one idea, and the
#: contract test that checks every class in a template has a rule cannot see
#: through such a mapping (static/css/ux.css, tests/test_ui_contract.py).
#:
#: Nothing is emphasised by colour alone: the column heading above the number
#: says what it counts.
_COLUMN_TONE: dict[str, str] = {"overdue": "bad", "week": "warn"}

#: Events that mean somebody worked on a file. `ENTRY_ADDED` is added to the
#: timeline's own list because an entry is the commonest change of all and the
#: timeline renders it from the `Entry` rather than from the event.
_ACTIVITY_EVENT_TYPES: tuple[str, ...] = (
    *TIMELINE_EVENT_TYPES,
    ChangeEventType.ENTRY_ADDED,
)


def previous_week(today: date) -> tuple[date, date]:
    """Monday to Sunday of the week before the one ``today`` falls in."""
    start = wi.start_of_iso_week(today) - timedelta(days=7)
    return start, start + timedelta(days=6)


def reporting_year(today: date) -> tuple[date, date]:
    """The calendar year, both ends, as the one window the year columns share.

    The calendar year and not a reporting year of its own: this asks what was
    finished, and a 2019 file whose opinion went out in March is March's work
    (app/matters/dashboard.py makes the same distinction).

    Both ends, so that the window is exactly `sent_at__year=<year>` — which is
    what the destination list filters on when a reader follows «Saadetud
    arvamusi». A window ending today would count one thing and open another.
    """
    return date(today.year, 1, 1), date(today.year, 12, 31)


def _matters_changed_by_owner(user: Any, start: date, end: date) -> dict[Any, int]:
    """How many of each person's files something happened to, in one window.

    Distinct **Matters**, not events: a file somebody wrote three notes on moved
    once as far as this column is concerned. Scoped through
    `scope_change_events`, because a change event about a restricted child may
    be stricter than the Matter it hangs off (AUTH-003).

    Grouped by the Matter's owner, like every other column here. This table
    answers "who is carrying what", so a change on a colleague's file counts for
    whoever carries it — the alternative would be a second, actor-based table
    beside an owner-based one, reading almost the same and disagreeing.
    """
    events = scope_change_events(
        ChangeEvent.objects.filter(
            event_type__in=_ACTIVITY_EVENT_TYPES,
            occurred_at__date__gte=start,
            occurred_at__date__lte=end,
        ),
        user,
    )
    grouped = (
        Matter.objects.filter(pk__in=events.values("matter_id"))
        .filter(pk__in=Matter.objects.visible_to(user).values("pk"))
        .order_by()
        .values("owner_id")
        .annotate(total=Count("id"))
    )
    return {row["owner_id"]: row["total"] for row in grouped}


def _sent_by_owner(user: Any, *, since: date, until: date | None = None) -> dict[Any, int]:
    """Opinions sent in a window, grouped by whose file they went out on.

    **Opinions**, not files carrying one. It counted Matters, which is a
    different number the moment a file produces two opinions in a year — and
    the same population is printed twice on the department page, as the team
    table's `ARVAMUSI VÄLJA` total and as Aruandlus's `Saadetud arvamusi`. Two
    counts of one business fact that disagree is exactly what the reconciliation
    test now forbids (design handoff C §4, brief §19).

    The aggregate runs over a re-wrapped, unordered query for the reason
    :func:`_by_owner` gives: `Meta.ordering` and any annotation on the incoming
    query leak into the GROUP BY and split one total across rows.
    """
    sent = sent_submissions(user, since=since, until=until)
    grouped = (
        Submission.objects.filter(pk__in=sent.values("pk"))
        .order_by()
        .values("matter__owner_id")
        .annotate(total=Count("id"))
    )
    return {row["matter__owner_id"]: row["total"] for row in grouped}


def team_rows(
    user: Any, today: date | None = None, *, items: list[wi.WorkItem] | None = None
) -> list[TeamRow]:
    """Every caseworker, the unassigned pile, and the total that reconciles.

    Nine grouped queries plus one for the people — not one per person per
    column. The real department is small enough that the naive shape would work
    and still be wrong: a query count that grows with the number of colleagues is
    a page that degrades exactly when somebody is hired.

    The total row is computed as the sum of the rows above it rather than as a
    tenth set of queries, so the two cannot disagree — and the same figures
    appear on the Seis strip, which is asserted rather than assumed
    (tests/test_ux_pass.py).
    """
    today = today or timezone.localdate()
    week_start, week_end = previous_week(today)
    year_start, year_end = reporting_year(today)

    active = active_matters(user)
    # The page's own read when it has one — the same list, for the same reader
    # on the same day, which is why the Seis strip and this table are obliged to
    # agree and now cannot even in principle disagree by reading twice.
    items = items if items is not None else wi.work_items(user, today=today)
    counts = {
        "open": _by_owner(active),
        "overdue": _by_owner(
            active.filter(
                pk__in=wi.work_population_ids(user, wi.WORK_OVERDUE, today=today, items=items)
            )
        ),
        "week": _by_owner(
            active.filter(
                pk__in=wi.work_population_ids(
                    user, wi.WORK_DEADLINE_THIS_WEEK, today=today, items=items
                )
            )
        ),
        "no_action": _by_owner(without_next_action(user)),
        "drafting": _by_owner(drafting_matters(user)),
        "changed": _matters_changed_by_owner(user, week_start, week_end),
        "sent_week": _sent_by_owner(user, since=week_start, until=week_end),
        # The whole calendar year, both ends, so this column and Aruandlus's
        # «Saadetud arvamusi» are one window as well as one population. Bounded
        # at today they would have disagreed about a letter somebody dated
        # forward, which is the kind of one-row difference nobody can explain
        # afterwards (`reporting_year`).
        "sent_year": _sent_by_owner(user, since=year_start, until=year_end),
    }

    # Everybody who appears in *any* column, not only in the open-work one. A
    # colleague who sent an opinion in March and carries nothing today still
    # owns that opinion, and dropping their row would drop it from the Kokku
    # line that Aruandlus is asserted against.
    owner_ids = {
        owner_id for column in counts.values() for owner_id in column if owner_id is not None
    }
    people = User.objects.filter(
        Q(is_active=True, role__in=CASEWORK_ROLES) | Q(pk__in=owner_ids)
    ).order_by("display_name")

    def label_of(column_label: str, group: str) -> str:
        if group == "week":
            return f"{column_label.capitalize()} · eelmine nädal"
        if group == "year":
            return f"{column_label.capitalize()} · {today.year}"
        return column_label.capitalize()

    def cell(column: str, sep: bool, label: str, owner_id: Any) -> StatCell:
        value = counts[column].get(owner_id, 0)
        return StatCell(
            value=value,
            url=_column_url(column, owner_id),
            tone=_COLUMN_TONE.get(column, "") if value else "",
            sep=sep,
            label=label,
        )

    rows = [
        TeamRow(
            key=str(person.pk),
            name=person.display_name,
            initials=person.initials,
            cells=tuple(
                cell(column, sep, label_of(label, group), person.pk)
                for column, label, group, sep in TEAM_COLUMNS
            ),
            is_self=person.pk == getattr(user, "pk", None),
            is_former=not person.is_active,
            # The person's desk, not the register filtered by owner. A register
            # row answers "what is this Matter"; the question a head clicks a
            # name to ask is "what is on this person's desk", and that is a
            # different page (design handoff, Minu asjad §A). The register is
            # still one click further on, from that page's own footer link.
            #
            # One's own row goes to `Minu asjad` rather than to one's own
            # person page. They render identically — `person_work` resolves
            # `is_self` — but the address a person keeps for their own desk is
            # the short one.
            url=(
                reverse("matters:my_work")
                if person.pk == getattr(user, "pk", None)
                else reverse("matters:person_work", kwargs={"pk": person.pk})
            ),
        )
        for person in people
    ]

    # Work nobody carries. Its own row rather than an omission: it is on nobody's
    # personal list by definition, which is exactly why it has to be on this one.
    rows.append(
        TeamRow(
            key="vastutajata",
            name="Vastutajata",
            initials="!",
            cells=tuple(
                cell(column, sep, label_of(label, group), None)
                for column, label, group, sep in TEAM_COLUMNS
            ),
            is_unassigned=True,
            url=register_url(**_open_full(), vastutaja=MISSING),
        )
    )

    rows.append(
        TeamRow(
            key="kokku",
            name="Kokku",
            initials="",
            cells=tuple(
                StatCell(
                    value=sum(row.cells[index].value for row in rows),
                    tone=_COLUMN_TONE.get(column, "")
                    if sum(row.cells[index].value for row in rows)
                    else "",
                    sep=sep,
                    label=f"Kokku — {label_of(label, group).lower()}",
                )
                for index, (column, label, group, sep) in enumerate(TEAM_COLUMNS)
            ),
            is_total=True,
        )
    )
    return rows


def _column_url(column: str, owner_id: Any) -> str:
    """The list one cell opens, or nothing where the register cannot say it.

    Three columns count history — what changed last week, what went out last
    week, what has gone out this year — and the register lists *Matters* by
    their current state. A link there would open a list that does not match the
    number above it, which is worse than no link at all (Stage-2F brief 35).
    """
    owner = owner_id if owner_id is not None else MISSING
    base = {**_open_full(), "vastutaja": owner}
    if column == "open":
        return register_url(**base)
    if column == "overdue":
        return register_url(**base, too=wi.WORK_OVERDUE)
    if column == "week":
        return register_url(**base, too=wi.WORK_DEADLINE_THIS_WEEK)
    if column == "no_action":
        return register_url(**base, tegevus=MISSING)
    if column == "drafting":
        return register_url(**base, arvamus=OPINION_DRAFTING)
    return ""


# ---------------------------------------------------------------------------
# Eesolev
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UpcomingGroup:
    """One window of the department's deadlines, and the list it opens."""

    key: str
    label: str
    items: list[wi.WorkItem]
    starts: date
    ends: date | None
    shown: int

    @property
    def count(self) -> int:
        """Deadlines in the window, which is what *Eesolev* discloses.

        Two dated obligations on one file are two rows, so this is the number
        the window's own «kõik N» control states: it opens N rows, and a count
        of files would be describing a different population from the one it
        reveals. `matter_count` is the register's answer to the same window and
        stays beside it rather than replacing it.
        """
        return len(self.items)

    @property
    def matter_count(self) -> int:
        """Files in the window, which is what the register would list.

        Not on the panel any more — the per-window control stopped being a link
        to the register when the window became a disclosure — but still the
        honest count for `url`, and the pair the window's own tests hold apart.
        """
        return len({item.matter_id for item in self.items})

    @property
    def preview(self) -> list[wi.WorkItem]:
        """The head of the window, and no longer what *Eesolev* renders.

        The panel showed `preview` and put `rest` behind a «Näita veel N»
        disclosure inside the group. The group is itself a disclosure now and
        owns every row, so the template reads `items`: opening «kõik N» that
        revealed some of N would be the same lie the old count was.
        """
        return self.items[: self.shown]

    @property
    def rest(self) -> list[wi.WorkItem]:
        """The tail `preview` leaves. Unrendered, for the same reason."""
        return self.items[self.shown :]

    @property
    def remaining(self) -> int:
        return max(0, self.count - self.shown)

    @property
    def range_label(self) -> str:
        """``30.08–06.09``, or ``alates 01.10`` where the window has no end.

        Only the last window is open-ended. `short_range` returns nothing at all
        for a half-open interval — correctly, since it states a range — so the
        one group that has a first day and no last day states that instead of
        printing a blank where every group above it prints its dates
        (design handoff C, frame C).
        """
        if self.ends is None:
            return f"alates {short_day_month(self.starts)}"
        return short_range(self.starts, self.ends)

    @property
    def is_far(self) -> bool:
        """The open-ended window. It is headed differently, and nothing else."""
        return self.ends is None

    @property
    def is_empty_window(self) -> bool:
        """True where the interval holds no days at all.

        *Ülejäänud kuu* is one such window whenever next week already runs to or
        past the end of the month: the rest of the month would begin on Monday
        and have ended on Sunday. It holds nothing by construction, and the
        panel omits it rather than printing a heading over an interval read
        backwards.
        """
        return self.ends is not None and self.starts > self.ends

    @property
    def url(self) -> str:
        """The register, narrowed to exactly this window of real deadlines.

        `?too=tahtaeg-vahemik` with the window's own two dates, so what the
        register lists for a window is exactly that window rather than a
        superset that happens to contain it (app/matters/work_items.py).

        No longer rendered: the window's own control opens the rows in place
        instead of leaving the page. Kept because it is the one expression of a
        window's boundaries the register can be *asked*, and the partition is
        asserted through it — `register_population` answering `matter_count` for
        every window is a check no comparison rewritten in a test could be
        (tests/test_deadline_grouping.py).
        """
        params: dict[str, Any] = {
            **_open_full(),
            "too": wi.WORK_DEADLINE_WINDOW,
            "too_alates": format_estonian_date(self.starts),
        }
        if self.ends is not None:
            params["too_kuni"] = format_estonian_date(self.ends)
        return register_url(**params)


#: Where `preview` cuts a group, for the two windows that carry a cut at all.
#: *Eesolev* does not render the cut any more — every window is one disclosure
#: and opening it shows the whole window — so this now describes the read model
#: alone. Five is what the three-window panel of docs/adr/0046 also showed; that
#: panel and its own constant are gone, and this is the only one left.
UPCOMING_PREVIEW = 5

#: The windows are cut here rather than where they are rendered, because two of
#: the five boundaries are the kind that look obvious and are not: *järgmine
#: nädal* ends at the end of the *next* ISO week and not seven days from
#: tomorrow, and *ülejäänud kuu* ends at the end of whatever month that Sunday
#: falls in — which can be the month after this one.
UPCOMING_WINDOWS: tuple[str, ...] = ("tana", "homme", "nadal", "kuu", "kaugemal")


def upcoming_windows(today: date) -> tuple[tuple[str, str, date, date | None], ...]:
    """The five consecutive windows *Eesolev* holds: key, heading, first, last.

    Consecutive and exhaustive by construction. Each begins the day after the
    previous one ends and the last has no end, so a future real deadline lands
    in exactly one of them — never in two, and never in none. That is asserted
    across awkward calendars rather than assumed (`tests/test_department_page.py`).

    Two boundaries carry the whole of the difficulty:

    * *Järgmine nädal* runs from the day after tomorrow to the end of the **next
      ISO week**, so on a Friday it is nine days and on a Sunday it is seven.
      The heading says «järgmine nädal» and the interval has to mean it.
    * *Ülejäänud kuu* is what is left of the month that Sunday falls in. When
      next week already runs past the month's end that window is empty by
      construction — it would start after it ended — and `is_empty_window` says
      so rather than the panel printing a heading over a backwards interval.

    *Kaugemal* then starts the day after the later of those two, which is what
    keeps the five touching in both cases.
    """
    tomorrow = today + timedelta(days=1)
    next_week_start = tomorrow + timedelta(days=1)
    next_week_end = wi.end_of_iso_week(today) + timedelta(days=7)
    month_start = next_week_end + timedelta(days=1)
    # Never before the week's own end, so *Kaugemal* cannot begin inside a
    # window the reader has already been shown.
    month_end = max(end_of_month(month_start), next_week_end)
    return (
        ("tana", "Täna", today, today),
        ("homme", weekday_name(tomorrow), tomorrow, tomorrow),
        ("nadal", "Järgmine nädal", next_week_start, next_week_end),
        ("kuu", "Ülejäänud kuu", month_start, month_end),
        ("kaugemal", "Kaugemal", month_end + timedelta(days=1), None),
    )


#: Where each window's `preview` cuts. `None` means "all of them", and the panel
#: renders all of them either way: *Eesolev*'s windows are disclosures now and
#: none of them slices.
_UPCOMING_SHOWN: dict[str, int | None] = {
    "tana": None,
    "homme": None,
    "nadal": None,
    "kuu": UPCOMING_PREVIEW,
    "kaugemal": UPCOMING_PREVIEW,
}


def upcoming_groups(
    user: Any, today: date | None = None, *, items: list[wi.WorkItem] | None = None
) -> list[UpcomingGroup]:
    """Every real deadline ahead, in the five windows :func:`upcoming_windows` cuts.

    One read of the work model, one partition. Not five queries: five windows
    assembled independently is five chances for a date to appear twice or in
    none, and the partition is the property this panel is asserted on.

    Only what the department may honestly call a deadline (`real_deadlines`): a
    WAIT's expected date and a MONITOR's review date are commitments nobody
    made, and they stay in *Vajab sekkumist*, where they read as "look at this
    again" (master specification 18.8).

    *Kaugemal* is a real list now rather than the one-line summary it was. A
    deadline in November was on no screen anywhere until November, and every
    window states the full population it holds (design handoff C §3.3).
    """
    today = today or timezone.localdate()
    # ``items`` lets a page that has already read the work model avoid reading
    # it again; omitting it reads the same model with the same authorization.
    # The department page holds one read and passes it to every panel that
    # partitions it, which is what keeps the query count a property of the page
    # rather than of how many panels happen to sit on it (`work_population_ids`
    # takes the same argument for the same reason).
    deadlines = wi.real_deadlines(items if items is not None else wi.work_items(user, today=today))
    groups = []
    for key, label, starts, ends in upcoming_windows(today):
        # Through the read model's own window selector rather than a comparison
        # written here, so the rows a window discloses, the count on its heading
        # and the `?too=tahtaeg-vahemik` register query for the same window are
        # one definition (app/matters/work_items.py).
        window = wi.work_population_items(
            deadlines, wi.WORK_DEADLINE_WINDOW, today, window=(starts, ends)
        )
        shown = _UPCOMING_SHOWN[key]
        groups.append(
            UpcomingGroup(
                key=key,
                label=label,
                items=window,
                starts=starts,
                ends=ends,
                shown=len(window) if shown is None else shown,
            )
        )
    return groups


# ---------------------------------------------------------------------------
# Tehtud
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PeriodOption:
    """One choice in the period control, and the URL that selects it.

    ``query`` carries the row-kind filter across, because the two controls sit
    on one section and neither may throw the other away: a reader who narrowed
    Tehtud to opinions and then asked for the quarter meant both.
    """

    key: str
    label: str
    active: bool
    query: str


@dataclass(frozen=True)
class DigestRow:
    """One thing the department finished, in the selected period."""

    when: date
    kind: str
    kind_label: str
    text: str
    initials: str
    actor_name: str
    url: str


#: The period control, and the query parameter it lives in. In the URL like
#: every other choice in this product, so a period somebody picked survives a
#: refresh and can be pasted to a colleague.
PERIOD_PARAM = "periood"
PERIOD_START_PARAM = "alates"
PERIOD_END_PARAM = "kuni"

PERIOD_7 = "7"
PERIOD_30 = "30"
PERIOD_QUARTER = "kvartal"
PERIOD_YEAR = "aasta"
PERIOD_CUSTOM = "vahemik"

PERIOD_LABELS: tuple[tuple[str, str], ...] = (
    (PERIOD_7, "7 päeva"),
    (PERIOD_30, "30 päeva"),
    (PERIOD_QUARTER, "Kvartal"),
    (PERIOD_YEAR, "Aasta"),
)

#: How many digest rows are shown before the rest go behind a disclosure.
DIGEST_PREVIEW = 6
#: The ceiling on what the disclosure opens. A period nobody bounded is still a
#: page somebody has to render.
DIGEST_LIMIT = 200

#: The row-kind filter, and the query parameter it lives in. In the URL beside
#: the period, so a reader who narrowed *Tehtud* to opinions can send that view
#: to somebody else.
#:
#: It narrows the **rows**. The summary line above them stays the whole selected
#: period, because that line answers "what did this period produce" and the
#: filter answers "show me one kind of it" — two questions, and a summary that
#: moved with the filter would leave the page unable to answer the first
#: (design handoff C §9.2, brief §15B).
KIND_PARAM = "liik"
KIND_ALL = "koik"

#: The visible choices, mapped onto the `DigestRow.kind` values the rows already
#: carry. The mapping exists so the URL can read in Estonian without renaming
#: anything stored: `kind` is the digest's own vocabulary and is not rewritten
#: to make a query string look tidy (brief §15C).
DIGEST_KINDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (KIND_ALL, "Kõik", ("sent", "win", "closed", "entry")),
    ("arvamused", "Arvamused", ("sent",)),
    ("toovoidud", "Töövõidud", ("win",)),
    ("suletud", "Suletud teemad", ("closed",)),
    ("sissekanded", "Sissekanded", ("entry",)),
)


#: The scope parameter, carried by every link the section builds so that
#: changing the period or the row kind cannot silently drop the reader into
#: another scope. Its value is `app.matters.overview.SCOPE_DEPARTMENT`; naming
#: it here rather than importing it keeps this module free of a circular import,
#: and the two are asserted equal (`tests/test_department_page.py`).
SCOPE_PARAM = "vaade"
SCOPE_DEPARTMENT = "osakond"


def _page_params(
    *,
    period_key: str,
    kind: str,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, str]:
    """The URL state of the Tehtud section, as one dictionary.

    Written once because two controls build links out of it and each has to
    preserve what the other chose. A custom range carries its two dates; a named
    period does not, so a reader leaving *Vali periood…* is not left with two
    stale parameters the page then has to decide whether to believe.
    """
    params = {SCOPE_PARAM: SCOPE_DEPARTMENT, PERIOD_PARAM: period_key}
    if period_key == PERIOD_CUSTOM and start is not None and end is not None:
        params[PERIOD_START_PARAM] = format_estonian_date(start)
        params[PERIOD_END_PARAM] = format_estonian_date(end)
    params[KIND_PARAM] = kind
    return params


def kind_from(params: Any) -> str:
    """Which row kind the URL asks for, or all of them.

    An unrecognised value means all, never nothing. A hand-edited or truncated
    URL must not render a convincing empty page that reads as "the department
    did nothing this month".
    """
    value = (params.get(KIND_PARAM) or "").strip()
    return value if value in {key for key, _, _ in DIGEST_KINDS} else KIND_ALL


@dataclass(frozen=True)
class Period:
    """The window Tehtud is reporting on, and how it was chosen."""

    key: str
    start: date
    end: date

    @property
    def is_custom(self) -> bool:
        return self.key == PERIOD_CUSTOM

    @property
    def label(self) -> str:
        return dict(PERIOD_LABELS).get(self.key, short_range(self.start, self.end))


def period_from(params: Any, today: date | None = None) -> Period:
    """Which window the reader asked for, or the default fortnight-equivalent.

    An unreadable custom range falls back to the default rather than to nothing:
    a hand-edited URL should show something honest, and the control redisplays
    the dates so the reader can see what was refused.
    """
    today = today or timezone.localdate()
    key = (params.get(PERIOD_PARAM) or PERIOD_7).strip()
    if key == PERIOD_CUSTOM:
        start = parse_flexible_date((params.get(PERIOD_START_PARAM) or "").strip())
        end = parse_flexible_date((params.get(PERIOD_END_PARAM) or "").strip())
        if start is not None and end is not None and start <= end:
            return Period(PERIOD_CUSTOM, start, end)
        key = PERIOD_7
    if key == PERIOD_30:
        return Period(PERIOD_30, today - timedelta(days=30), today)
    if key == PERIOD_QUARTER:
        return Period(PERIOD_QUARTER, today - timedelta(days=91), today)
    if key == PERIOD_YEAR:
        return Period(PERIOD_YEAR, date(today.year, 1, 1), today)
    return Period(PERIOD_7, today - timedelta(days=7), today)


def period_options(period: Period, kind: str = KIND_ALL) -> list[PeriodOption]:
    return [
        PeriodOption(
            key=key,
            label=label,
            active=period.key == key,
            query=urlencode(_page_params(period_key=key, kind=kind)),
        )
        for key, label in PERIOD_LABELS
    ]


@dataclass(frozen=True)
class KindOption:
    """One choice in the row-kind filter, and the URL that selects it.

    ``query`` carries the period across — including a custom range's two dates —
    so narrowing to opinions does not silently reset the window the reader chose.
    """

    key: str
    label: str
    active: bool
    query: str


@dataclass
class Digest:
    """What the department finished in one period.

    ``rows`` is what the kind filter left; the four counts describe the **whole**
    period whatever that filter is. Both are on the object rather than derived
    in the template, so the rule that the summary does not move is one line of
    code and one assertion rather than a convention.
    """

    period: Period
    kind: str = KIND_ALL
    rows: list[DigestRow] = field(default_factory=list)
    sent: int = 0
    closed: int = 0
    victories: int = 0
    entries: int = 0

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def preview(self) -> list[DigestRow]:
        return self.rows[:DIGEST_PREVIEW]

    @property
    def rest(self) -> list[DigestRow]:
        return self.rows[DIGEST_PREVIEW:]

    @property
    def kinds(self) -> list[KindOption]:
        return [
            KindOption(
                key=key,
                label=label,
                active=key == self.kind,
                query=urlencode(
                    _page_params(
                        period_key=self.period.key,
                        kind=key,
                        start=self.period.start,
                        end=self.period.end,
                    )
                ),
            )
            for key, label, _ in DIGEST_KINDS
        ]


def _digest_url(matter_id: Any) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": matter_id})


def build_digest(user: Any, period: Period, kind: str = KIND_ALL) -> Digest:
    """Four kinds of finished work, newest first, over one window.

    Read from the canonical records rather than from the audit stream: a sent
    opinion is a `Submission`, a closed Matter is a Matter with a closure date,
    a work victory is a confirmed `MatterWorkVictory`, and an entry is an
    `Entry`. Each carries its own visibility, and each is scoped by it.

    The summary line counts the same four populations the rows come from, so the
    sentence above the list and the list under it cannot disagree.
    """
    sent = list(
        sent_submissions(user, since=period.start, until=period.end)
        .select_related("matter", "sent_by")
        .order_by("-sent_at")[:DIGEST_LIMIT]
    )
    closed = list(
        Matter.objects.visible_to(user)
        .filter(
            is_open=False,
            record_mode=RecordMode.FULL,
            closed_at__date__gte=period.start,
            closed_at__date__lte=period.end,
        )
        .select_related("owner")
        .order_by("-closed_at")[:DIGEST_LIMIT]
    )
    victories = list(
        # `WorkVictoryStatus.CONFIRMED`, which is what this field stores.
        # `FactStatus.ACTIVE` stood here and belongs to a different fact's
        # vocabulary, so the filter matched nothing and the row read «0
        # töövõitu» however many the department had confirmed
        # (app/intelligence/enums.py, brief §23).
        MatterWorkVictory.objects.visible_to(user)
        .filter(
            status=WorkVictoryStatus.CONFIRMED,
            confirmed_at__date__gte=period.start,
            confirmed_at__date__lte=period.end,
        )
        .select_related("matter", "confirmed_by")
        .order_by("-confirmed_at")[:DIGEST_LIMIT]
    )
    entries = list(
        Entry.objects.visible_to(user)
        .filter(occurred_at__date__gte=period.start, occurred_at__date__lte=period.end)
        .select_related("matter", "author")
        .order_by("-occurred_at")[:DIGEST_LIMIT]
    )

    def person(actor: Any) -> tuple[str, str]:
        return (actor.initials, actor.display_name) if actor is not None else ("··", "")

    rows: list[DigestRow] = []
    for submission in sent:
        initials, name = person(submission.sent_by or submission.matter.owner)
        rows.append(
            DigestRow(
                when=timezone.localtime(submission.sent_at).date(),
                kind="sent",
                kind_label="Arvamus välja",
                text=submission.matter.title,
                initials=initials,
                actor_name=name,
                url=_digest_url(submission.matter_id),
            )
        )
    for victory in victories:
        initials, name = person(victory.confirmed_by or victory.matter.owner)
        rows.append(
            DigestRow(
                when=timezone.localtime(victory.confirmed_at).date(),
                kind="win",
                kind_label="Töövõit",
                text=victory.title,
                initials=initials,
                actor_name=name,
                url=_digest_url(victory.matter_id),
            )
        )
    for matter in closed:
        initials, name = person(matter.owner)
        rows.append(
            DigestRow(
                when=timezone.localtime(matter.closed_at).date(),
                kind="closed",
                kind_label="Teema suletud",
                text=matter.title,
                initials=initials,
                actor_name=name,
                url=_digest_url(matter.pk),
            )
        )
    for entry in entries:
        initials, name = person(entry.author)
        rows.append(
            DigestRow(
                when=timezone.localtime(entry.occurred_at).date(),
                kind="entry",
                kind_label="Sissekanne",
                text=entry.matter.title,
                initials=initials,
                actor_name=name,
                url=_digest_url(entry.matter_id),
            )
        )

    # The three outcomes first, then the entries: a period is read for what came
    # out of it, and a day's notes under a sent opinion is the order somebody
    # actually scans. Newest first inside that.
    order = {"sent": 0, "win": 0, "closed": 0, "entry": 1}
    rows.sort(key=lambda row: (order[row.kind], -row.when.toordinal()))

    wanted = {key: kinds for key, _, kinds in DIGEST_KINDS}[kind]
    return Digest(
        period=period,
        kind=kind,
        rows=[row for row in rows if row.kind in wanted],
        # Counted before the filter, and deliberately: the four numbers are the
        # period's output, not the visible list's length.
        sent=len(sent),
        closed=len(closed),
        victories=len(victories),
        entries=len(entries),
    )


# ---------------------------------------------------------------------------
# The right rail
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RailRow:
    label: str
    count: int
    url: str
    tone: str = ""


def incoming_rail(user: Any, today: date | None = None) -> list[RailRow]:
    """*Uued teemad* — what has arrived, and what has been left alone.

    Three rows the department page used to spread across two surfaces: the Seis
    strip's «uut sel nädalal», Ülevaade's *Uued teemad* rail and this module's
    own *Vajab sekkumist* rail all counted from these same three definitions
    (design handoff C §3.4).

    Nothing is redefined here. Each row is the register parameters that *are*
    its definition, counted through the register's own filter pipeline and
    linked as the same parameters, so the number and the list behind it are one
    query (`register_population`, master specification 18.9).

    The wording is the handoff's and is not negotiable: «Muutusteta 30 p», never
    «seisma jäänud» — a file nobody has touched for a month is a fact, and the
    other phrase is a judgement about the person carrying it.
    """
    today = today or timezone.localdate()
    population = Matter.objects.visible_to(user)

    def count(params: dict[str, Any]) -> int:
        return register_population(user, params, today=today, population=population).count()

    arrived = _arrived_this_week_params(today)
    unreviewed = _unreviewed_params(today)
    quiet = {**_open_full(), WORK_PARAM: wi.WORK_QUIET_30}

    return [
        RailRow("Uut sel nädalal", count(arrived), register_url(**arrived)),
        RailRow("Uut läbi vaatamata", count(unreviewed), register_url(**unreviewed), "warning"),
        RailRow(f"Muutusteta {wi.QUIET_DAYS} p", count(quiet), register_url(**quiet)),
    ]


def reporting_rail(user: Any, today: date | None = None) -> list[RailRow]:
    """What the department has produced this calendar year.

    The calendar year, not the reporting year: this asks what was finished, and
    a 2019 file whose opinion went out in March is March's work
    (app/matters/dashboard.py makes the same distinction).

    Every row carries its year into its link. The label said 2026 and two of the
    three links opened every year there had ever been, which is a count that
    cannot be checked — and one of them counted a status this field never
    stores, so it read zero whatever the year held (brief §19, §23).

    «Saadetud arvamusi» is the same population as the team table's `ARVAMUSI
    VÄLJA · <aasta>` Kokku cell, from the same window, and that equality is
    asserted rather than assumed (`tests/test_department_page.py`).
    """
    today = today or timezone.localdate()
    year = today.year
    start, end = reporting_year(today)
    closed_params = {"olek": "suletud", "liik": RecordMode.FULL.value, "suletud": str(year)}
    return [
        RailRow(
            "Saadetud arvamusi",
            sent_submissions(user, since=start, until=end).count(),
            f"{reverse('submissions:sent')}?aasta={year}",
        ),
        RailRow(
            "Töövõite kinnitatud",
            # The business period, which is what the destination list filters
            # `?aasta=` on — not `confirmed_at`, which is when somebody got
            # round to recording it. A count and a list that read two different
            # dates is the failure this page exists to avoid (ADR 0043).
            MatterWorkVictory.objects.visible_to(user)
            .filter(status=WorkVictoryStatus.CONFIRMED, period_date__year=year)
            .count(),
            # `?aasta=` only. The destination has no state filter any more:
            # a Töövõit is a Töövõit there, and a `?staatus=` this link still
            # carried would name a parameter nothing reads.
            f"{reverse('intelligence:work_victories')}?aasta={year}",
        ),
        RailRow(
            "Suletud teemasid",
            register_population(user, closed_params, today=today).count(),
            register_url(**closed_params),
        ),
    ]
