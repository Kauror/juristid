"""Osakonna töö — what is going on across the lawyers, in three questions.

A third surface, deliberately not a replacement for either of the two that
exist. *Minu töö* answers "what do I have to do today". *Ülevaade* answers "what
is the state of the department's files", and every reader gets it. This answers
a question only one person has, and the 2026-08 redesign states it as three:

* **Mida meeskond teeb** — the Meeskond table, one row per person.
* **Mis on ees** — Eesolev, the whole department's dates in four windows.
* **Mis on tehtud** — Tehtud, what came out of a period the reader chooses.

Above them is *Seis*: six risks rather than six counters. Beside them is a rail
of four states worth a decision, and the year's output.

Four rules run through every function here.

**Authorization before arithmetic.** Every queryset starts from
``visible_to(user)``. A department head sees RESTRICTED content because
``DEPARTMENT_HEAD`` is a role the central authorization already entitles — not
because this module decided so, and nothing here re-implements ``visibility ==
NORMAL or …``. Point the same functions at a specialist and they return the
specialist's authorized world.

**Every number opens the list it counted.** Each figure is defined *as* register
parameters; the count is those parameters run through the register's own filter
pipeline and the link is those parameters as a query string. A count and a
drill-through that disagree is the failure this discipline exists to prevent
(``register_population``, master specification 18.9). Three columns count
history the register cannot express as a current state, and those carry no link
at all — an honest number beats a link to a different list.

**Definitions are imported, never restated.** Overdue, this week, "no next
action", "still drafting" all come from :mod:`app.matters.dashboard` and
:mod:`app.matters.work_items`. Two similar definitions in two files is how two
screens start disagreeing about the same Matter, and the head is precisely the
person who would notice and stop trusting both.

**This is not a staff evaluation.** Every column is a count of observable
states. There is no ranking, no score, no rate, no percentage and no colour
grading of people. Lawyers are listed alphabetically, and that ordering is
load-bearing: sorting the table by any of its numbers would turn an oversight
tool into a leaderboard, which is both forbidden (specification 18.8) and the
fastest way to make the underlying data worth gaming. A count of open matters is
inventory — one of them can be a two-line monitoring note and the next a year of
consultation — so it measures neither effort nor performance and is never
labelled as if it did.

What this page no longer carries, and where it went
---------------------------------------------------

The redesign narrowed the page to the three questions above. Nothing it dropped
lost its path:

* the cross-team *Vajab sekkumist* list is Ülevaade's, and the head reads it
  there (``/ulevaade/?vaade=osakond``);
* *Ülevaatus või ootamine vajab pilku* is ``?too=ulevaatamiseks`` on the
  register, and is in Ülevaade's intervention list;
* per-lawyer *recently received* is ``?vastutaja=…&saabus_alates=…``;
* *Aktiivne teema ilma hetkeseisuta* is ``?hetkeseis=puudub``, and the full
  picture is Statistika → Andmekvaliteet.
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
    short_range,
    weekday_name,
)
from app.intelligence.enums import FactStatus
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
    """A link into Teemad with filters already applied.

    Only parameters the register actually supports today. Every number that
    links to a list is a promise that the list behind it holds exactly those
    rows, and the promise is kept by reusing the register's own query
    parameters rather than inventing a parallel query language beside Stage
    2E.1's (Stage-2F brief 35).
    """
    query = "&".join(f"{key}={value}" for key, value in params.items() if value)
    base = reverse("matters:matter_list")
    return f"{base}?{query}" if query else base


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
    """One number on the risk strip, with the list it opens."""

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


def seis_figures(user: Any, today: date | None = None) -> list[SeisFigure]:
    """The strip, in the head's reading order: what is late, then what is loose.

    Every count runs through the register's own filter pipeline over the
    parameters that *are* its definition, so the number and the list behind it
    are one query rather than two similar ones (`register_population`, master
    specification 18.9).
    """
    today = today or timezone.localdate()
    population = Matter.objects.visible_to(user)

    def count(params: dict[str, Any]) -> int:
        return register_population(user, params, today=today, population=population).count()

    overdue = {**_open_full(), WORK_PARAM: wi.WORK_OVERDUE}
    this_week = {**_open_full(), WORK_PARAM: wi.WORK_DEADLINE_THIS_WEEK}
    unassigned = {**_open_full(), "vastutaja": MISSING}
    unreviewed = _unreviewed_params(today)
    arrived = _arrived_this_week_params(today)

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
        SeisFigure("arrived", count(arrived), "uut sel nädalal", register_url(**arrived)),
        SeisFigure(
            "sent",
            sent_submissions(user, since=today - timedelta(days=SENT_WINDOW_DAYS)).count(),
            f"arvamust välja · {SENT_WINDOW_DAYS} p",
            reverse("submissions:sent"),
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

    The aggregate runs over a re-wrapped, unordered query for the reason
    :func:`_by_owner` gives: `Meta.ordering` and any annotation on the incoming
    query leak into the GROUP BY and split one person's total across rows.
    """
    sent = sent_submissions(user, since=since, until=until)
    grouped = (
        Matter.objects.filter(pk__in=sent.values("matter_id"))
        .order_by()
        .values("owner_id")
        .annotate(total=Count("id"))
    )
    return {row["owner_id"]: row["total"] for row in grouped}


def team_rows(user: Any, today: date | None = None) -> list[TeamRow]:
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
    year_start = date(today.year, 1, 1)

    active = active_matters(user)
    items = wi.work_items(user, today=today)
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
        "sent_year": _sent_by_owner(user, since=year_start),
    }

    owner_ids = {owner_id for owner_id in counts["open"] if owner_id is not None}
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
            url=register_url(**_open_full(), vastutaja=person.pk),
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
        return len(self.items)

    @property
    def matter_count(self) -> int:
        return len({item.matter_id for item in self.items})

    @property
    def preview(self) -> list[wi.WorkItem]:
        return self.items[: self.shown]

    @property
    def rest(self) -> list[wi.WorkItem]:
        return self.items[self.shown :]

    @property
    def remaining(self) -> int:
        return max(0, self.count - self.shown)

    @property
    def range_label(self) -> str:
        return short_range(self.starts, self.ends)

    @property
    def url(self) -> str:
        """The register, narrowed to exactly this window of real deadlines.

        `?too=tahtaeg-vahemik` with the window's own two dates, so the list
        behind «kõik N →» holds the Matters the number counted rather than a
        superset that happens to contain them (app/matters/work_items.py).
        """
        params: dict[str, Any] = {
            **_open_full(),
            "too": wi.WORK_DEADLINE_WINDOW,
            "too_alates": format_estonian_date(self.starts),
        }
        if self.ends is not None:
            params["too_kuni"] = format_estonian_date(self.ends)
        return register_url(**params)


#: How many rows a group shows before the rest go behind «Näita veel». Ten at a
#: time, opened in place: a manager scanning next month should not have to leave
#: the page to see the eleventh date (design handoff, Osakond §3).
UPCOMING_PREVIEW = 5
UPCOMING_BATCH = 10


def upcoming_groups(user: Any, today: date | None = None) -> list[UpcomingGroup]:
    """Today, tomorrow, the rest of next week, and the month after it.

    Four consecutive windows, like Ülevaade's — but cut where a manager plans
    rather than where a lawyer works: the two days that are already decided, the
    week being planned, and the month after it. Only what the department may
    honestly call a deadline (`real_deadlines`): a WAIT's expected date is not a
    commitment anybody made (master specification 18.8).
    """
    today = today or timezone.localdate()
    tomorrow = today + timedelta(days=1)
    next_week_end = wi.end_of_iso_week(today) + timedelta(days=7)
    month_start = next_week_end + timedelta(days=1)
    windows: tuple[tuple[str, str, date, date], ...] = (
        ("tana", "Täna", today, today),
        ("homme", weekday_name(tomorrow), tomorrow, tomorrow),
        ("nadal", "Järgmine nädal", tomorrow + timedelta(days=1), next_week_end),
        ("kuu", "Järgmine kuu", month_start, end_of_month(month_start)),
    )

    items = wi.real_deadlines(wi.work_items(user, today=today))
    groups = []
    for key, label, starts, ends in windows:
        window = [item for item in items if item.when is not None and starts <= item.when <= ends]
        groups.append(
            UpcomingGroup(
                key=key,
                label=label,
                items=window,
                starts=starts,
                ends=ends,
                shown=len(window) if key in ("tana", "homme") else UPCOMING_PREVIEW,
            )
        )
    return groups


# ---------------------------------------------------------------------------
# Tehtud
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PeriodOption:
    key: str
    label: str
    active: bool

    @property
    def query(self) -> str:
        return urlencode({PERIOD_PARAM: self.key})


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


def period_options(period: Period) -> list[PeriodOption]:
    return [
        PeriodOption(key=key, label=label, active=period.key == key) for key, label in PERIOD_LABELS
    ]


@dataclass
class Digest:
    """What the department finished in one period."""

    period: Period
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


def _digest_url(matter_id: Any) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": matter_id})


def build_digest(user: Any, period: Period) -> Digest:
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
        MatterWorkVictory.objects.visible_to(user)
        .filter(
            status=FactStatus.ACTIVE,
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

    return Digest(
        period=period,
        rows=rows,
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


def attention_rail(user: Any, today: date | None = None) -> list[RailRow]:
    """Four states worth a decision, each opening exactly its own list.

    The wording is the handoff's and is not negotiable: «Muutusteta 30 p», never
    «seisma jäänud» — a file nobody has touched for a month is a fact, and the
    other phrase is a judgement about the person carrying it.
    """
    today = today or timezone.localdate()
    population = Matter.objects.visible_to(user)

    def count(params: dict[str, Any]) -> int:
        return register_population(user, params, today=today, population=population).count()

    unassigned = {**_open_full(), "vastutaja": MISSING}
    unreviewed = _unreviewed_params(today)
    quiet = {**_open_full(), WORK_PARAM: wi.WORK_QUIET_30}
    overdue = {**_open_full(), WORK_PARAM: wi.WORK_OVERDUE}

    return [
        RailRow("Vastutajata teemad", count(unassigned), register_url(**unassigned), "warning"),
        RailRow(
            "Uued saabunud, läbi vaatamata",
            count(unreviewed),
            register_url(**unreviewed),
            "warning",
        ),
        RailRow(f"Muutusteta {wi.QUIET_DAYS} p", count(quiet), register_url(**quiet)),
        RailRow("Üle tähtaja", count(overdue), register_url(**overdue), "danger"),
    ]


def reporting_rail(user: Any, today: date | None = None) -> list[RailRow]:
    """What the department has produced this calendar year.

    The calendar year, not the reporting year: this asks what was finished, and
    a 2019 file whose opinion went out in March is March's work
    (app/matters/dashboard.py makes the same distinction).
    """
    today = today or timezone.localdate()
    start = date(today.year, 1, 1)
    closed_params = {"olek": "suletud", "liik": RecordMode.FULL.value, "suletud": str(today.year)}
    return [
        RailRow(
            "Saadetud arvamusi",
            sent_submissions(user, since=start, until=today).count(),
            reverse("submissions:sent"),
        ),
        RailRow(
            "Töövõite kinnitatud",
            MatterWorkVictory.objects.visible_to(user)
            .filter(
                status=FactStatus.ACTIVE,
                confirmed_at__date__gte=start,
                confirmed_at__date__lte=today,
            )
            .count(),
            reverse("intelligence:work_victories"),
        ),
        RailRow(
            "Suletud teemasid",
            register_population(user, closed_params, today=today).count(),
            register_url(**closed_params),
        ),
    ]


# ---------------------------------------------------------------------------
# The whole page
# ---------------------------------------------------------------------------


@dataclass
class DepartmentWork:
    today: date
    people: int = 0
    open_matters: int = 0
    seis: list[SeisFigure] = field(default_factory=list)
    team: list[TeamRow] = field(default_factory=list)
    upcoming: list[UpcomingGroup] = field(default_factory=list)
    digest: Digest | None = None
    periods: list[PeriodOption] = field(default_factory=list)
    attention: list[RailRow] = field(default_factory=list)
    reporting: list[RailRow] = field(default_factory=list)
    previous_week_label: str = ""

    @property
    def has_former_members(self) -> bool:
        return any(row.is_former for row in self.team)

    @property
    def has_upcoming(self) -> bool:
        return any(group.count for group in self.upcoming)


def build_department_work(
    user: Any, today: date | None = None, params: Any = None
) -> DepartmentWork:
    """The whole page, for one authorized reader."""
    today = today or timezone.localdate()
    team = team_rows(user, today)
    week_start, week_end = previous_week(today)
    total = next((row for row in team if row.is_total), None)
    period = period_from(params or {}, today)
    return DepartmentWork(
        today=today,
        # Rows minus the unassigned pile and the total: those are not people.
        people=len([row for row in team if not row.is_unassigned and not row.is_total]),
        open_matters=total.cells[0].value if total is not None else 0,
        seis=seis_figures(user, today),
        team=team,
        upcoming=upcoming_groups(user, today),
        digest=build_digest(user, period),
        periods=period_options(period),
        attention=attention_rail(user, today),
        reporting=reporting_rail(user, today),
        previous_week_label=short_range(week_start, week_end),
    )
