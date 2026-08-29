"""Minu asjad — one person's desk, whoever is looking at it.

The page has exactly one organising question, and it is *when do I need to care
about this again*. That has one answer per piece of work and it is a date,
whatever the mode: a ministry's reply expected on Thursday and an opinion due on
Thursday are both Thursday's problem. So there is one timeline, and the mode
chip beside each date says what the date means rather than which column it
belongs in.

Three consequences shape everything below.

**No dated work is exiled to a rail.** The earlier design put future WAIT and
MONITOR in separate blocks on the right, which made a lawyer read two lists and
merge them in their head — the merge this page exists to do for them. The rail
now holds only what genuinely has no place in time: a Matter that has gone quiet
because nobody set a next step, and an action nobody has dated.

**One page, two modes.** `subject` is whose desk this is; `user` is who is
looking at it. They are different arguments and they do different things:
`responsible=subject` selects the work, `visible_to(user)` decides what of it
may be seen. A department head reading a colleague's desk therefore sees that
colleague's queue through their own entitlement, never through the colleague's
— and a personal scratchpad is not part of this read model at all
(`app/matters/person_work.py`).

**Nothing here is anybody else's work.** Every population is filtered to the
subject before it is counted, and the responsibility rules differ by source on
purpose: a NextAction belongs to its ``responsible``, an ``Oluline tähtaeg`` to
the Matter's current owner (app/matters/work_items.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from django.urls import reverse
from django.utils import timezone

from app.matters import work_items as wi
from app.matters.activity import BASIS_LABELS, activity_of, annotate_last_activity
from app.matters.models import Entry, Matter
from app.workflow.enums import ESTONIAN_MONTHS

#: How far the *Hiljem* band reaches when nobody has chosen otherwise: the end
#: of the month two months out. On 24 August that is 31 October, which is the
#: window the design's range control names — far enough to plan an autumn
#: consultation round, near enough that the band stays readable.
DEFAULT_HORIZON_MONTHS = 2

#: How many months the range control offers beyond the default.
HORIZON_CHOICES = 4

#: The query parameter the range lives in. In the URL rather than the session,
#: so a refresh, the back button and a pasted link all show the same page.
HORIZON_PARAM = "kuni"
HORIZON_ALL = "koik"

#: Caps. The heading above each list states the honest total either way.
RAIL_LIMIT = 8
ENTRY_LIMIT = 6

#: How many portfolio rows are on screen before the rest go behind
#: «Näita veel N ▾». The rest are the same list, sliced.
PORTFOLIO_VISIBLE = 6

#: How many changed Matters the rail names.
CHANGE_LIMIT = 5

#: What «Hiljuti muutunud» means, and what «Muutusteta 30 p» means. Both are
#: measured against the same activity fact the register's *Viimane tegevus*
#: column shows, so a row cannot be recent here and stale there.
RECENT_DAYS = 7
QUIET_DAYS = wi.QUIET_DAYS


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _shift_months(anchor: date, months: int) -> tuple[int, int]:
    index = anchor.year * 12 + (anchor.month - 1) + months
    return index // 12, index % 12 + 1


@dataclass(frozen=True)
class HorizonOption:
    key: str
    label: str
    active: bool

    @property
    def query(self) -> str:
        return f"{HORIZON_PARAM}={self.key}"


@dataclass(frozen=True)
class Horizon:
    """How far ahead the timeline looks, and what the control calls it."""

    key: str
    label: str
    until: date | None

    @property
    def is_all(self) -> bool:
        return self.until is None


def default_horizon(today: date) -> Horizon:
    year, month = _shift_months(today, DEFAULT_HORIZON_MONTHS)
    return Horizon(
        key=f"{year}-{month:02d}",
        label=f"Kuni {ESTONIAN_MONTHS[month - 1]}",
        until=_month_end(year, month),
    )


def horizon_from(value: str | None, today: date) -> Horizon:
    """The window a query parameter asks for, or the default.

    Anything unrecognised falls back rather than raising or emptying the list: a
    mistyped URL should show the page, not a convincing empty timeline.
    """
    if value == HORIZON_ALL:
        return Horizon(key=HORIZON_ALL, label="Kõik tähtajad", until=None)
    if value:
        try:
            year, month = (int(part) for part in value.split("-", 1))
            if 1 <= month <= 12 and 2000 <= year <= 2100:
                return Horizon(
                    key=f"{year}-{month:02d}",
                    label=f"Kuni {ESTONIAN_MONTHS[month - 1]}",
                    until=_month_end(year, month),
                )
        except (ValueError, TypeError):
            pass
    return default_horizon(today)


def horizon_options(today: date, selected: Horizon) -> list[HorizonOption]:
    options: list[HorizonOption] = []
    for offset in range(HORIZON_CHOICES):
        year, month = _shift_months(today, offset)
        key = f"{year}-{month:02d}"
        options.append(
            HorizonOption(
                key=key,
                label=f"Kuni {ESTONIAN_MONTHS[month - 1]}",
                active=key == selected.key,
            )
        )
    options.append(HorizonOption(key=HORIZON_ALL, label="Kõik tähtajad", active=selected.is_all))
    return options


# ---------------------------------------------------------------------------
# The rail
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuietMatter:
    """An open Matter of this person's that nobody has given a next step.

    ``silent_days`` is measured rather than described, because "44 p vaikust" is
    what makes the difference between a file that is merely undated and one that
    has been forgotten.
    """

    matter: Matter
    silent_days: int | None

    @property
    def silence(self) -> str:
        if self.silent_days is None:
            return "viimane tegevus teadmata"
        return f"viimane tegevus {self.silent_days} p tagasi"


def quiet_matters(
    user: Any, subject: Any, today: date, limit: int = RAIL_LIMIT
) -> tuple[list[QuietMatter], int]:
    """The subject's open Matters with no active NextAction, quietest first.

    ``annotate_last_activity`` rather than reading each Matter's timeline: a
    rail block showing "viimane tegevus 44 p tagasi" for eight rows must not
    fetch eight two-hundred-entry chronologies to do it (§27).
    """
    queryset = annotate_last_activity(
        wi.matters_without_action(user, owner=subject).select_related("stage", "owner"), user
    )
    total = queryset.count()
    rows: list[QuietMatter] = []
    for matter in queryset.order_by("updated_at")[:limit]:
        # `activity_of` reads the annotations and never queries, which is what
        # makes eight rows eight reads of memory rather than eight timelines.
        activity = activity_of(matter)
        rows.append(
            QuietMatter(
                matter=matter,
                silent_days=(today - activity.occurred_on).days if activity else None,
            )
        )
    return rows, total


def undated_items(
    user: Any, subject: Any, limit: int = RAIL_LIMIT
) -> tuple[list[wi.WorkItem], int]:
    """The subject's open actions carrying no date at all — TEEN, OOTAN and JÄLGIN together.

    Not split into separate Ootan and Jälgin blocks. "No idea when" is one
    condition whatever the mode, and three short lists of two rows each is three
    headings for six facts.
    """
    queryset = wi.undated_actions(user, responsible=subject)
    total = queryset.count()
    today = timezone.localdate()
    rows = [wi.action_item(action, today) for action in queryset.order_by("matter__title")[:limit]]
    return rows, total


def recent_entries(user: Any, subject: Any, limit: int = ENTRY_LIMIT) -> list[Entry]:
    """What this person wrote lately. A way back, not a second timeline."""
    return list(
        Entry.objects.visible_to(user)
        .filter(author=subject)
        .select_related("matter")
        .chronological()[:limit]
    )


# ---------------------------------------------------------------------------
# Aktiivsed teemad — the portfolio
#
# One row per open Matter, which is the other half of the question the bands
# answer. The bands say *when*; this says *what is on the desk at all*, including
# the files that carry no date and would otherwise only appear in a rail block.
# ---------------------------------------------------------------------------

#: The query parameter the segmentation lives in, and its values.
VIEW_PARAM = "vaade"
VIEW_ALL = "koik"
VIEW_ATTENTION = "sekkumist"
VIEW_NO_ACTION = "jargmiseta"
VIEW_CHANGED = "muutunud"

#: The chips, in reading order, with the words `01-EHITUSJUHIS` §4 settles.
#: «Vajab sekkumist», never «Vajab tähelepanu».
VIEW_LABELS: tuple[tuple[str, str], ...] = (
    (VIEW_ALL, "Kõik"),
    (VIEW_ATTENTION, "Vajab sekkumist"),
    (VIEW_NO_ACTION, "Ilma järgmiseta"),
    (VIEW_CHANGED, "Hiljuti muutunud"),
)

VIEW_TONES: dict[str, str] = {VIEW_NO_ACTION: "warn"}


def view_from(value: str | None) -> str:
    """Which segmentation a query parameter asks for, or all of them."""
    keys = {key for key, _ in VIEW_LABELS}
    return value if value in keys else VIEW_ALL


@dataclass(frozen=True)
class PortfolioRow:
    """One open Matter on this desk: where it stands, what is next, when it moved."""

    matter: Matter
    #: The earliest open dated action, if there is one. An `Oluline tähtaeg` is
    #: deliberately not eligible: it is a milestone, not somebody's next step.
    action: wi.WorkItem | None
    #: An open action with no date at all, when that is all there is.
    undated: wi.WorkItem | None
    last_activity: date | None
    today: date

    @property
    def has_action(self) -> bool:
        return self.action is not None or self.undated is not None

    @property
    def next_action(self) -> wi.WorkItem | None:
        return self.action or self.undated

    @property
    def stage_label(self) -> str:
        stage = self.matter.stage
        return stage.label_et if stage is not None else ""

    @property
    def silent_days(self) -> int | None:
        if self.last_activity is None:
            return None
        return (self.today - self.last_activity).days

    @property
    def needs_attention(self) -> bool:
        """Late, or a review that has come round. Never a future WAIT."""
        return self.action is not None and (self.action.is_overdue or self.action.is_review_ripe)

    @property
    def changed_recently(self) -> bool:
        days = self.silent_days
        return days is not None and days <= RECENT_DAYS


@dataclass(frozen=True)
class PortfolioChip:
    key: str
    label: str
    count: int
    active: bool
    tone: str = ""

    @property
    def query(self) -> str:
        return f"{VIEW_PARAM}={self.key}"


@dataclass(frozen=True)
class Portfolio:
    """The subject's open Matters, segmented, with the register link out."""

    view: str
    chips: list[PortfolioChip]
    rows: list[PortfolioRow]
    #: Every open Matter on this desk, before the chip narrowed it. The rail
    #: reads it so a second query for the same population is never made.
    all_rows: list[PortfolioRow]
    total: int
    register_url: str
    #: How many rows are on screen; the rest are the same list, sliced.
    visible: int = PORTFOLIO_VISIBLE

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def preview(self) -> list[PortfolioRow]:
        return self.rows[: self.visible]

    @property
    def rest(self) -> list[PortfolioRow]:
        return self.rows[self.visible :]

    @property
    def remaining(self) -> int:
        return len(self.rest)


def _earliest_actions(items: list[wi.WorkItem]) -> dict[Any, wi.WorkItem]:
    """The earliest open dated action per Matter, from the list already read.

    `items` is `sort_items` order — oldest first — so the first one seen for a
    Matter is the earliest. Built from the same read the bands come from rather
    than queried again, which is what stops a row saying one thing and the
    timeline above it another.
    """
    earliest: dict[Any, wi.WorkItem] = {}
    for item in items:
        if not item.is_action:
            continue
        earliest.setdefault(item.matter_id, item)
    return earliest


def build_portfolio(
    user: Any,
    subject: Any,
    *,
    today: date,
    items: list[wi.WorkItem],
    view: str = VIEW_ALL,
) -> Portfolio:
    """Every open Matter this person holds, in one query plus the work already read."""
    queryset = annotate_last_activity(
        wi.open_matters(user).filter(owner=subject).select_related("stage", "owner"), user
    )
    dated = _earliest_actions(items)
    undated: dict[Any, wi.WorkItem] = {}
    for action in wi.undated_actions(user, responsible=subject).order_by("created_at"):
        undated.setdefault(action.matter_id, wi.action_item(action, today))

    rows = [
        PortfolioRow(
            matter=matter,
            action=dated.get(matter.pk),
            undated=undated.get(matter.pk),
            last_activity=(activity.occurred_on if (activity := activity_of(matter)) else None),
            today=today,
        )
        for matter in queryset
    ]

    predicates = {
        VIEW_ALL: lambda row: True,
        VIEW_ATTENTION: lambda row: row.needs_attention,
        VIEW_NO_ACTION: lambda row: not row.has_action,
        VIEW_CHANGED: lambda row: row.changed_recently,
    }
    chips = [
        PortfolioChip(
            key=key,
            label=label,
            count=sum(1 for row in rows if predicates[key](row)),
            active=key == view,
            tone=VIEW_TONES.get(key, ""),
        )
        for key, label in VIEW_LABELS
    ]

    selected = [row for row in rows if predicates[view](row)]
    # Attention first, then the oldest silence: what is late or unanswered is
    # what a desk is opened for.
    selected.sort(
        key=lambda row: (
            not row.needs_attention,
            row.silent_days is None,
            -(row.silent_days or 0),
            row.matter.title,
        )
    )

    base = reverse("matters:matter_list")
    owner = getattr(subject, "pk", "")
    register = f"{base}?olek=avatud&liik=FULL&vastutaja={owner}" if owner else base

    return Portfolio(
        view=view,
        chips=chips,
        rows=selected,
        all_rows=rows,
        total=len(rows),
        register_url=register,
        visible=PORTFOLIO_VISIBLE,
    )


# ---------------------------------------------------------------------------
# Viimati muudetud
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangeRow:
    """One Matter that moved, and what kind of fact moved it."""

    matter: Matter
    occurred_on: date
    label: str


def recent_changes(user: Any, subject: Any, limit: int = CHANGE_LIMIT) -> list[ChangeRow]:
    """The subject's Matters that moved most recently, newest first.

    Built from the existing activity precedence rather than from an audit log:
    `annotate_last_activity` already resolves the latest fact on a Matter and
    says which kind of fact it was, and `BASIS_LABELS` already names those kinds
    in Estonian. A second chronology would be a second thing to keep true
    (app/matters/activity.py, design handoff 03 §3).
    """
    queryset = annotate_last_activity(
        wi.open_matters(user).filter(owner=subject).select_related("stage", "owner"), user
    )
    rows: list[ChangeRow] = []
    for matter in queryset:
        activity = activity_of(matter)
        if activity is None:
            continue
        rows.append(
            ChangeRow(
                matter=matter,
                occurred_on=activity.occurred_on,
                label=BASIS_LABELS.get(activity.basis, ""),
            )
        )
    rows.sort(key=lambda row: (row.occurred_on, row.matter.title), reverse=True)
    return rows[:limit]


# ---------------------------------------------------------------------------
# The whole page
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeisFigure:
    """One number on the strip, with the list it opens.

    Zeros are never rendered — the template drops them — because "0 üle
    tähtaja" is a warning about nothing, and a page that cries wolf every
    morning is a page people stop reading (01 §3.7).
    """

    key: str
    value: int
    caption: str
    url: str
    tone: str = ""


@dataclass(frozen=True)
class StatRow:
    """One figure in the statistics foldout.

    ``url`` is empty wherever no list in this product holds exactly this
    population. A number with no link is honest; a link to a list that does not
    match the number above it is not — the same rule the Osakonna töö team
    table already applies to its three history columns
    (app/matters/department_dashboard.py, `_column_url`).
    """

    label: str
    value: int
    url: str = ""


@dataclass(frozen=True)
class PersonStats:
    """This month and this year, counted, never rated.

    No ranking, no percentage, no comparison with a colleague and no colour
    grading of a person. These are counts of observable work state, which is
    the same refusal Osakonna töö opens with (01 §3.3).
    """

    month_label: str
    year_label: str
    month: list[StatRow]
    year: list[StatRow]


def person_stats(user: Any, subject: Any, today: date) -> PersonStats:
    """The foldout's figures, from the selectors that already define them."""
    from app.matters.department_dashboard import sent_submissions

    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    owned = Matter.objects.visible_to(user).filter(owner=subject)

    def sent(since: date) -> int:
        return sent_submissions(user, since=since).filter(matter__owner=subject).count()

    def closed(since: date) -> int:
        return owned.filter(is_open=False, closed_at__date__gte=since).count()

    entries = (
        Entry.objects.visible_to(user)
        .filter(author=subject, occurred_at__date__gte=month_start)
        .count()
    )
    return PersonStats(
        month_label="SEL KUUL",
        year_label="SEL AASTAL",
        month=[
            StatRow("arvamusi välja", sent(month_start)),
            StatRow("sissekandeid", entries),
            StatRow("lõpetatud teemasid", closed(month_start)),
        ],
        year=[
            StatRow("arvamusi välja", sent(year_start)),
            StatRow("lõpetatud teemasid", closed(year_start)),
        ],
    )


@dataclass(frozen=True)
class QuickRow:
    """One line of the manager's Kiirvaade: a count and the list behind it."""

    label: str
    value: int
    url: str
    tone: str = ""

    @property
    def css_class(self) -> str:
        """The whole class name, built here rather than concatenated in the
        template. A template writing `is-{{ tone }}` leaves the literal `is-`
        in the markup, which the stylesheet contract reads as a class nobody
        ever defined (tests/test_ui_contract.py)."""
        return f"is-{self.tone}" if self.tone else ""


@dataclass
class MyWork:
    subject: Any
    is_self: bool
    today: date
    horizon: Horizon
    horizons: list[HorizonOption] = field(default_factory=list)
    bands: list[wi.WorkBand] = field(default_factory=list)
    seis: list[SeisFigure] = field(default_factory=list)
    portfolio: Portfolio | None = None
    quiet: list[QuietMatter] = field(default_factory=list)
    quiet_total: int = 0
    undated: list[wi.WorkItem] = field(default_factory=list)
    undated_total: int = 0
    changes: list[ChangeRow] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    quick: list[QuickRow] = field(default_factory=list)
    stats: PersonStats | None = None
    open_matters: int = 0
    overdue: int = 0
    week: int = 0
    beyond_horizon: int = 0

    @property
    def has_work(self) -> bool:
        return bool(self.bands)

    @property
    def total(self) -> int:
        """Every dated item the timeline is showing.

        Counted off the bands rather than recomputed, so the figure and the
        list it describes cannot disagree — and an item is in exactly one
        band, so nothing is counted twice.
        """
        return sum(band.count for band in self.bands)

    @property
    def last_entry(self) -> Entry | None:
        return self.entries[0] if self.entries else None


def _work_url(subject: Any, population: str) -> str:
    """The register list behind one work figure, for exactly this person.

    `?too=` is resolved by `work_items.work_population_ids` and `?too_vastutaja=`
    narrows it to one person, so the number here and the rows there are one
    selector rather than two similar ones (app/matters/register_filters.py).
    """
    owner = getattr(subject, "pk", "")
    return (
        f"{reverse('matters:matter_list')}?olek=avatud&liik=FULL"
        f"&too={population}&too_vastutaja={owner}"
    )


def _register_url(subject: Any, **extra: str) -> str:
    owner = getattr(subject, "pk", "")
    query = "&".join(f"{key}={value}" for key, value in extra.items())
    base = f"{reverse('matters:matter_list')}?olek=avatud&liik=FULL&vastutaja={owner}"
    return f"{base}&{query}" if query else base


def build_my_work(
    user: Any,
    today: date | None = None,
    horizon: Horizon | None = None,
    *,
    subject: Any = None,
    view: str = VIEW_ALL,
) -> MyWork:
    """Assemble the page from one read of the shared work model.

    ``subject`` defaults to ``user``, which is the ordinary case: my own desk.
    Passing somebody else selects *their* work and leaves the authorization
    scope alone — ``visible_to(user)`` still decides what may be seen, so a
    manager reading a colleague's desk sees it through their own entitlement.

    The strip's counts are taken from the same list the bands are built from, so
    the summary and the timeline cannot disagree — and ``N üle tähtaja`` counts
    only what is genuinely late: overdue DO deadlines and the ``Oluline
    tähtaeg`` on Matters this person owns. A passed review date is never in it.
    """
    today = today or timezone.localdate()
    horizon = horizon or default_horizon(today)
    subject = subject if subject is not None else user
    is_self = getattr(subject, "pk", None) == getattr(user, "pk", object())

    # Unbounded, because the strip has to count what falls beyond the window in
    # order to offer "Näita kaugemaid tähtaegu" honestly.
    everything = wi.work_items(user, today=today, responsible=subject)
    week_end = wi.end_of_iso_week(today)
    bands = wi.band_items(everything, today, week_end=week_end, horizon=horizon.until)

    banded = {item.object_id for band in bands for item in band.items}
    beyond = sum(1 for item in everything if item.object_id not in banded)

    quiet, quiet_total = quiet_matters(user, subject, today)
    undated, undated_total = undated_items(user, subject)
    portfolio = build_portfolio(user, subject, today=today, items=everything, view=view)

    def population(key: str) -> int:
        """How many Matters one named work population holds for this person.

        `work_population_ids` is the same selector `?too=` resolves in the
        register, given the work model already read here. So the number on the
        strip and the rows behind the link it carries are one query, not two
        similar ones (master specification 18.9).
        """
        return len(
            wi.work_population_ids(user, key, today=today, items=everything, responsible=subject)
        )

    open_matters = portfolio.total
    overdue = population(wi.WORK_OVERDUE)
    week = population(wi.WORK_DEADLINE_THIS_WEEK)

    seis = [
        SeisFigure("open", open_matters, "avatud teemat", _register_url(subject)),
        SeisFigure(
            "overdue", overdue, "üle tähtaja", _work_url(subject, wi.WORK_OVERDUE), "danger"
        ),
        SeisFigure(
            "week",
            week,
            "tähtaeg sel nädalal",
            _work_url(subject, wi.WORK_DEADLINE_THIS_WEEK),
            "warning",
        ),
        SeisFigure(
            "no_action",
            quiet_total,
            "järgmise tegevuseta",
            _register_url(subject, tegevus="puudub"),
            "warning",
        ),
    ]

    quick: list[QuickRow] = []
    if not is_self:
        # The manager's Kiirvaade. Four counts, each one a link into the same
        # register population the number came from; no scratchpad and nothing
        # that reads as a judgement of a colleague.
        quick = [
            QuickRow(
                "Vajab sekkumist",
                population(wi.WORK_NEEDS_ATTENTION),
                _work_url(subject, wi.WORK_NEEDS_ATTENTION),
                "bad",
            ),
            QuickRow(
                "Ilma järgmiseta",
                quiet_total,
                _register_url(subject, tegevus="puudub"),
                "warn",
            ),
            QuickRow(
                "Tähtaeg 30 p jooksul",
                population(wi.WORK_DEADLINE_30_DAYS),
                _work_url(subject, wi.WORK_DEADLINE_30_DAYS),
            ),
            QuickRow(
                "Muutusteta 30 p",
                population(wi.WORK_QUIET_30),
                _work_url(subject, wi.WORK_QUIET_30),
            ),
        ]

    return MyWork(
        subject=subject,
        is_self=is_self,
        today=today,
        horizon=horizon,
        horizons=horizon_options(today, horizon),
        bands=bands,
        seis=seis,
        portfolio=portfolio,
        quiet=quiet,
        quiet_total=quiet_total,
        undated=undated,
        undated_total=undated_total,
        changes=recent_changes(user, subject),
        entries=recent_entries(user, subject),
        quick=quick,
        stats=person_stats(user, subject, today),
        open_matters=open_matters,
        overdue=overdue,
        week=week,
        beyond_horizon=beyond,
    )
