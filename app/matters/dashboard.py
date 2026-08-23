"""Ülevaade — what is happening across the visible portfolio.

Distinct from Minu töö, which answers "what do *I* have to do". This answers
"what is the state of the department's files", and the difference matters: a
department head opening the same page as a lawyer should see the same shape of
truth, scoped to what each may read.

Two rules run through every function here.

**Authorization before arithmetic.** Every queryset starts from
``Matter.objects.visible_to(user)`` and is filtered, grouped, counted and sliced
only after that. A restricted Matter the reader may not see contributes nothing
to a total, a bar, an owner tally or an attention row. Hiding it at render time
would leave it inside the counts, which is the disclosure that is easy to miss
because nothing on screen looks wrong.

**Nothing here is a performance metric.** Counts by owner are *inventory* — how
many open files sit with someone — and are never labelled workload, throughput
or productivity. The specification forbids presenting a count of open matters as
if it measured effort (18.8), and a dashboard that ranks colleagues is a
dashboard people learn to game.

Nothing is invented either: no severity score, no predicted risk, no
"most productive", no response rate. Every number is a count of rows that exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from django.db.models import Exists, F, OuterRef, QuerySet
from django.urls import reverse
from django.utils import timezone

from app.core.authorization import scoped_count
from app.legacy_import.current_state import RegisterCurrency
from app.matters.enums import RecordMode
from app.matters.models import Matter
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from app.workflow.models import NextAction

#: The summary cards look this far ahead. Fixed: the card is a KPI, not a view
#: of the table below it, and making the whole page depend on one query
#: parameter would be a lot of machinery for a number nobody filters.
DEADLINE_HORIZON_DAYS = 7

#: The deadline table's default. A fortnight is the planning unit a department
#: review actually uses, and it is what the table looked at before the period
#: selector existed — so the page opens on the same rows it always did.
UPCOMING_HORIZON_DAYS = 14

#: Caps. A dashboard that renders four hundred rows is a dashboard nobody reads,
#: and the counts above each list are the honest total either way.
ATTENTION_LIMIT = 40
UPCOMING_LIMIT = 40
RECENT_INCOMING_LIMIT = 10


#: The query parameter the deadline period lives in. In the URL rather than in
#: the session, so a refresh, the back button and a link pasted into a chat all
#: show the same page — a period held in a cookie is a page that cannot be
#: shared and cannot be reproduced from a bug report.
DEADLINE_WINDOW_PARAM = "tahtajad"


@dataclass(frozen=True)
class DeadlineWindow:
    """How far ahead the deadline table looks.

    Every window starts at *today* and none of them reach backwards. A date
    that has already passed is not "upcoming", and a WAIT or MONITOR review
    date that has arrived is not late — both are already answered, in their own
    words, by :func:`overdue_actions` and :func:`reviews_due` above.
    """

    key: str
    label: str
    days: int | None
    #: True when the window begins *after* ``days`` instead of ending at it.
    beyond: bool = False

    def bounds(self, today: date) -> tuple[date, date | None]:
        """``(earliest, latest)``. ``latest`` of ``None`` means no upper bound."""
        if self.days is None:
            return today, None
        if self.beyond:
            # "later than thirty days" — the day after the thirty-day window's
            # last day, so the two never overlap and never leave a date in
            # neither.
            return today + timedelta(days=self.days + 1), None
        return today, today + timedelta(days=self.days)


DEADLINE_WINDOWS: tuple[DeadlineWindow, ...] = (
    DeadlineWindow(key="7", label="7 päeva", days=7),
    DeadlineWindow(key="14", label="14 päeva", days=UPCOMING_HORIZON_DAYS),
    DeadlineWindow(key="30", label="30 päeva", days=30),
    DeadlineWindow(key="30plus", label="30+ päeva", days=30, beyond=True),
    DeadlineWindow(key="koik", label="Kõik", days=None),
)

DEFAULT_DEADLINE_WINDOW = DEADLINE_WINDOWS[1]


def deadline_window(key: str | None) -> DeadlineWindow:
    """The window a query parameter asks for, or the default.

    Anything unrecognised falls back rather than raising or emptying the table:
    a mistyped URL should show the page, not a stack trace and not a convincing
    empty list somebody reads as "no deadlines".
    """
    for window in DEADLINE_WINDOWS:
        if window.key == key:
            return window
    return DEFAULT_DEADLINE_WINDOW


@dataclass(frozen=True)
class WindowOption:
    """One choice in the period control, with the link that selects it."""

    key: str
    label: str
    query: str
    active: bool


def window_options(selected: DeadlineWindow) -> list[WindowOption]:
    return [
        WindowOption(
            key=window.key,
            label=window.label,
            query=f"{DEADLINE_WINDOW_PARAM}={window.key}",
            active=window.key == selected.key,
        )
        for window in DEADLINE_WINDOWS
    ]


def _teemad(**params: Any) -> str:
    """A link into the register with filters already applied.

    Every number on this page is a promise that a list exists behind it. Reusing
    the register's own query parameters keeps that promise honest: the count and
    the list come from the same filters rather than two similar definitions.
    """
    query = "&".join(f"{key}={value}" for key, value in params.items() if value)
    return f"{reverse('matters:matter_list')}?{query}" if query else reverse("matters:matter_list")


def active_matters(user: Any) -> QuerySet[Matter]:
    """Open FULL Matters the reader may see.

    ARCHIVE rows are excluded throughout the dashboard. They are historical
    evidence rather than live work, and counting a decade of imported register
    rows as "active" would make every number meaningless.
    """
    return Matter.objects.visible_to(user).filter(is_open=True, record_mode=RecordMode.FULL)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SummaryCard:
    key: str
    label: str
    count: int
    url: str
    note: str = ""


def overdue_actions(user: Any, today: date) -> QuerySet[NextAction]:
    """Genuinely late work, and nothing else.

    Public rather than private because the department-head dashboard counts the
    same thing, and a second definition of "overdue" written next door is how
    two pages start disagreeing about the same Matter (Stage-2F brief 32).

    Only DO + DEADLINE can be overdue. A WAIT whose review date has passed is
    due for a look, not missed — describing an ordinary dependency on a ministry
    as a failure is what makes a work queue stop being believed
    (master specification 18.8).
    """
    return (
        NextAction.objects.visible_to(user)
        .filter(
            status=ActionStatus.OPEN,
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date__lt=today,
        )
        .select_related("matter", "matter__owner", "matter__stage", "responsible")
    )


def reviews_due(user: Any, today: date) -> QuerySet[NextAction]:
    """WAIT and MONITOR whose review date has arrived. Never called overdue."""
    return (
        NextAction.objects.visible_to(user)
        .filter(
            status=ActionStatus.OPEN,
            kind__in=(ActionKind.WAIT, ActionKind.MONITOR),
            target_date__isnull=False,
            target_date__lte=today,
        )
        .select_related("matter", "matter__owner", "matter__stage", "responsible")
    )


def without_next_action(user: Any) -> QuerySet[Matter]:
    has_open = NextAction.objects.filter(matter=OuterRef("pk"), status=ActionStatus.OPEN)
    return active_matters(user).annotate(has_action=Exists(has_open)).filter(has_action=False)


def summary_cards(user: Any, today: date | None = None) -> list[SummaryCard]:
    today = today or timezone.localdate()
    horizon = today + timedelta(days=DEADLINE_HORIZON_DAYS)
    active = active_matters(user)

    return [
        SummaryCard(
            key="active",
            label="Aktiivsed teemad",
            count=active.count(),
            url=_teemad(olek="avatud", liik=RecordMode.FULL),
        ),
        SummaryCard(
            key="deadlines",
            label=f"Tähtajad {DEADLINE_HORIZON_DAYS} päeva jooksul",
            count=active.filter(
                response_deadline__gte=today, response_deadline__lte=horizon
            ).count(),
            url=_teemad(olek="avatud", liik=RecordMode.FULL),
            note="Arvamuse tähtaeg",
        ),
        SummaryCard(
            key="overdue",
            label="Tähtaeg möödas",
            count=overdue_actions(user, today).count(),
            url=reverse("matters:my_work"),
            note="Ainult tähtajaga tegevused",
        ),
        SummaryCard(
            key="drafting",
            label="Arvamusi koostamisel",
            count=drafting_matters(user).count(),
            url=_teemad(olek="avatud", liik=RecordMode.FULL),
            note="Registris puudub VÄLJA märge",
        ),
        SummaryCard(
            key="no_action",
            label="Järgmine tegevus puudub",
            count=without_next_action(user).count(),
            url=_teemad(olek="avatud", liik=RecordMode.FULL),
        ),
        SummaryCard(
            key="unassigned",
            label="Vastutajata",
            count=active.filter(owner__isnull=True).count(),
            url=reverse("matters:inbox"),
        ),
    ]


# ---------------------------------------------------------------------------
# Attention
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttentionRow:
    """One thing somebody can actually do something about.

    ``order`` is a fixed rank per reason, not a computed severity. An invented
    score would be a number nobody can check and everybody would argue with.
    """

    order: int
    reason: str
    matter: Matter
    owner_name: str
    when: date | None
    date_label: str
    url: str


_UNASSIGNED = "Vastutaja puudub"
_NO_ACTION = "Järgmine tegevus puudub"
_OVERDUE = "Tegevuse tähtaeg möödas"
_NO_SUBMISSION = "Arvamuse tähtaeg möödas, arvamust ei ole saadetud"
_REVIEW_DUE = "Ülevaatuse aeg on käes"


def _stage_label(matter: Matter) -> str:
    stage = matter.stage
    return stage.label_et if stage is not None else "—"


def _owner_name(matter: Matter) -> str:
    """The colleague's short name, because these tables are read at a glance.

    ``get_short_name`` rather than a first-token split written here: the User
    model already owns what a person is called informally, and a second copy of
    that rule is a second place for it to drift.

    Only for a resolved account. The register's own ``VASTUTAJA`` text is a
    different thing and is never shortened — see :func:`source_responsibility`,
    which names a colleague who has no account here at all.
    """
    owner = matter.owner
    return owner.get_short_name() if owner is not None else "Vastutajata"


def attention_rows(user: Any, today: date | None = None) -> list[AttentionRow]:
    """Actionable states only, ordered by how late each one actually is.

    Reuses the same definitions the rest of the product uses rather than
    restating them: a second definition of "overdue" that drifts from the first
    is how two screens start disagreeing about the same Matter.
    """
    today = today or timezone.localdate()
    rows: list[AttentionRow] = []

    for action in overdue_actions(user, today).order_by("target_date")[:ATTENTION_LIMIT]:
        rows.append(
            AttentionRow(
                order=1,
                reason=_OVERDUE,
                matter=action.matter,
                owner_name=_owner_name(action.matter),
                when=action.target_date,
                date_label="Tähtaeg",
                url=reverse("matters:matter_detail", kwargs={"pk": action.matter_id}),
            )
        )

    sent = Submission.objects.filter(matter=OuterRef("pk"), status=SubmissionStatus.SENT)
    missed = (
        active_matters(user)
        .filter(response_deadline__lt=today)
        .annotate(has_sent=Exists(sent))
        .filter(has_sent=False)
        .select_related("owner", "stage")
        .order_by("response_deadline")
    )
    for missed_matter in missed[:ATTENTION_LIMIT]:
        rows.append(
            AttentionRow(
                order=2,
                reason=_NO_SUBMISSION,
                matter=missed_matter,
                owner_name=_owner_name(missed_matter),
                when=missed_matter.response_deadline,
                date_label="Arvamuse tähtaeg",
                url=reverse("matters:matter_detail", kwargs={"pk": missed_matter.pk}),
            )
        )

    for action in reviews_due(user, today).order_by("target_date")[:ATTENTION_LIMIT]:
        rows.append(
            AttentionRow(
                order=3,
                reason=_REVIEW_DUE,
                matter=action.matter,
                owner_name=_owner_name(action.matter),
                when=action.target_date,
                date_label=action.date_label or "Ülevaatus",
                url=reverse("matters:matter_detail", kwargs={"pk": action.matter_id}),
            )
        )

    unassigned = active_matters(user).filter(owner__isnull=True).select_related("stage")
    for matter in unassigned.order_by("-created_at")[:ATTENTION_LIMIT]:
        rows.append(
            AttentionRow(
                order=4,
                reason=_UNASSIGNED,
                matter=matter,
                owner_name=_owner_name(matter),
                when=matter.received_date,
                date_label="Saabus",
                url=reverse("matters:matter_detail", kwargs={"pk": matter.pk}),
            )
        )

    without_action = without_next_action(user).select_related("owner", "stage")
    for quiet in without_action.order_by("-updated_at")[:ATTENTION_LIMIT]:
        rows.append(
            AttentionRow(
                order=5,
                reason=_NO_ACTION,
                matter=quiet,
                owner_name=_owner_name(quiet),
                when=quiet.response_deadline,
                date_label="Arvamuse tähtaeg" if quiet.response_deadline else "",
                url=reverse("matters:matter_detail", kwargs={"pk": quiet.pk}),
            )
        )

    # Reason rank first, then genuinely oldest first inside a reason. Rows with
    # no date sort last rather than pretending to be urgent.
    return sorted(rows, key=lambda row: (row.order, row.when or date.max))[:ATTENTION_LIMIT]


# ---------------------------------------------------------------------------
# Upcoming
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UpcomingRow:
    when: date
    matter: Matter
    owner_name: str
    stage_label: str
    meaning: str
    url: str


#: What a date on this page actually means. Four different things share a
#: column, and collapsing them into one word called "deadline" is precisely the
#: conflation the register suffered from and this product exists to undo.
MEANING_RESPONSE = "Arvamuse tähtaeg"
MEANING_ACTION = "Tegevuse tähtaeg"
MEANING_REVIEW = "Ülevaatuse kuupäev"
MEANING_EXPECTED = "Oodatav aeg"

_SEMANTICS_MEANING = {
    DateSemantics.DEADLINE.value: MEANING_ACTION,
    DateSemantics.REVIEW_ON.value: MEANING_REVIEW,
    DateSemantics.EXPECTED_AROUND.value: MEANING_EXPECTED,
}


@dataclass(frozen=True)
class UpcomingResult:
    """The rows a period holds, and how many there really are.

    Two numbers rather than one, because they legitimately differ: the table
    renders at most :data:`UPCOMING_LIMIT` rows and the heading states the full
    count. Deriving the heading from the rendered list instead would make the
    number quietly stop at forty and read as the truth.
    """

    rows: list[UpcomingRow]
    total: int
    window: DeadlineWindow


def _upcoming_sources(user: Any, today: date, window: DeadlineWindow) -> tuple[Any, Any]:
    """The two things this table calls a deadline, filtered by the same window.

    A Matter's own ``response_deadline`` and an open ``NextAction``'s target
    date are different facts with different meanings, and both belong here — so
    a period that applied to one of them would silently hide half of what the
    department is looking for.
    """
    earliest, latest = window.bounds(today)

    matters = (
        active_matters(user)
        .filter(response_deadline__gte=earliest)
        .select_related("owner", "stage")
    )
    actions = (
        NextAction.objects.visible_to(user)
        .filter(
            status=ActionStatus.OPEN,
            target_date__gte=earliest,
            matter__is_open=True,
            matter__record_mode=RecordMode.FULL,
        )
        .select_related("matter", "matter__owner", "matter__stage")
    )
    if latest is not None:
        matters = matters.filter(response_deadline__lte=latest)
        actions = actions.filter(target_date__lte=latest)
    return matters, actions


def upcoming_rows(
    user: Any, today: date | None = None, window: DeadlineWindow | None = None
) -> UpcomingResult:
    today = today or timezone.localdate()
    window = window or DEFAULT_DEADLINE_WINDOW
    matters, actions = _upcoming_sources(user, today, window)
    rows: list[UpcomingRow] = []

    for matter in matters.order_by("response_deadline")[:UPCOMING_LIMIT]:
        if matter.response_deadline is None:  # pragma: no cover - excluded by the filter
            continue
        rows.append(
            UpcomingRow(
                when=matter.response_deadline,
                matter=matter,
                owner_name=_owner_name(matter),
                stage_label=_stage_label(matter),
                meaning=MEANING_RESPONSE,
                url=reverse("matters:matter_detail", kwargs={"pk": matter.pk}),
            )
        )

    for action in actions.order_by("target_date")[:UPCOMING_LIMIT]:
        if action.target_date is None:  # pragma: no cover - excluded by the filter
            continue
        rows.append(
            UpcomingRow(
                when=action.target_date,
                matter=action.matter,
                owner_name=_owner_name(action.matter),
                stage_label=_stage_label(action.matter),
                # The semantic label travels with the row. A review date and a
                # response deadline are not the same obligation, and one column
                # called "tähtaeg" is exactly the conflation this product
                # exists to undo.
                meaning=_SEMANTICS_MEANING.get(action.date_semantics, MEANING_ACTION),
                url=reverse("matters:matter_detail", kwargs={"pk": action.matter_id}),
            )
        )

    # Counted on the querysets, not on the capped list above. Both have been
    # through the visibility predicate, and `apply` ends in `.distinct()`, so a
    # plain `.count()` is the scoped total (app/core/authorization.py).
    return UpcomingResult(
        rows=sorted(rows, key=lambda row: (row.when, row.matter.title))[:UPCOMING_LIMIT],
        total=matters.count() + actions.count(),
        window=window,
    )


# ---------------------------------------------------------------------------
# Team and stage distribution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CountRow:
    label: str
    count: int
    url: str


def owner_inventory(user: Any) -> list[CountRow]:
    """Open FULL Matters per owner. **Inventory, never workload.**

    A count of open files says nothing about effort: one may be a two-line
    monitoring note and the next a year of consultation. Naming this workload or
    productivity would invite exactly the comparison the specification forbids
    (18.8), and would make the number worth gaming.
    """
    grouped = (
        active_matters(user)
        .values("owner_id", "owner__display_name")
        # `scoped_count`, not `Count("id")`. `active_matters` has been through
        # the visibility predicate, which joins the collaborators many-to-many
        # for everybody except the department head — so a plain count here gives
        # a shared file one tally per collaborator, and these bars stop summing
        # to the *Aktiivsed teemad* card beside them (app/core/authorization.py).
        .annotate(total=scoped_count())
        .order_by("-total", "owner__display_name")
    )

    rows = [
        CountRow(
            label=entry["owner__display_name"],
            count=entry["total"],
            # `liik` as well, exactly as the summary cards carry it. Every
            # number on this page counts `active_matters`, which is open *FULL*
            # records — so a link without the record-mode filter opens a list
            # that also holds open archive rows, and is longer than the bar it
            # came from (the one thing `_teemad` exists to prevent).
            url=_teemad(olek="avatud", liik=RecordMode.FULL, vastutaja=entry["owner_id"]),
        )
        for entry in grouped
        if entry["owner_id"] is not None
    ]

    unassigned = next((entry["total"] for entry in grouped if entry["owner_id"] is None), 0)
    if unassigned:
        rows.append(CountRow(label="Vastutajata", count=unassigned, url=reverse("matters:inbox")))
    return rows


def drafting_matters(user: Any) -> QuerySet[Matter]:
    """Current work whose opinion has not been recorded as sent.

    ``Arvamusi koostamisel``. Both halves are required and they come from
    different places on purpose.

    The lifecycle half is canonical: ``active_matters`` — open FULL records this
    reader may see. The source half is the register's ``VÄLJA`` column, held on
    the derived ``CurrentRegisterState`` row. Leading with the canonical half is
    what makes the number self-correcting: a lawyer who closes a Matter today
    drops out of this count on the next page load, without anybody re-running
    the cutover, because the derived table is only ever consulted about the one
    fact it is authoritative for.

    ``VÄLJA`` is not ``Submission.sent_at`` and this is not a count of
    submissions. It answers a narrower question — has the drafting step been
    recorded as finished — and a Matter can legitimately have a send date while
    its proceeding runs on for months (ADR 0021).

    The source half asks whether the register *wrote* anything in ``VÄLJA``, not
    whether what it wrote parses as a date. Those differ on fourteen current
    Matters in the approved snapshot, and reading the parsed date's nullability
    reported all fourteen as unfinished work.
    """
    return active_matters(user).filter(
        current_register_state__currency=RegisterCurrency.CURRENT,
        current_register_state__opinion_sent_recorded=False,
    )


def drafting_by_responsibility(user: Any) -> list[CountRow]:
    """Who the register names on each Matter still being drafted.

    Grouped by the register's own ``VASTUTAJA`` text rather than by the resolved
    account, for the reason :func:`source_responsibility` gives.
    """
    return _by_source_responsibility(drafting_matters(user), unassigned_label="Vastutajata")


def source_responsibility(user: Any) -> list[CountRow]:
    """Named responsibility across current work, as the register states it.

    **Source responsibility, not workload and not a ranking.** The same refusal
    as :func:`owner_inventory`, which this sits beside: a count of files says
    nothing about effort, and the specification forbids presenting one as if it
    did (18.8).

    Grouped by the raw register name rather than by ``Matter.owner``, and that
    is the whole reason this function exists separately. Two current Matters
    name a colleague who has no account here. Grouping by the resolved owner
    would file them under *Määramata*, which discards the one thing the register
    is certain about; inventing an account to hold them would be worse. So the
    register's own word is what is counted, and whether it resolves to a login
    is a separate question this page does not ask (Stage-2F owner resolver).
    """
    return _by_source_responsibility(active_matters(user), unassigned_label="Vastutajata")


def _by_source_responsibility(queryset: Any, *, unassigned_label: str) -> list[CountRow]:
    """One row per name the register gives, largest first, blanks last.

    ``scoped_count`` because the queryset has been through the visibility
    predicate and its collaborators join (app/core/authorization.py).
    """
    grouped = (
        queryset.values("current_register_state__owner_raw")
        .annotate(total=scoped_count())
        .order_by("-total", "current_register_state__owner_raw")
    )
    named: list[CountRow] = []
    unassigned = 0
    for entry in grouped:
        name = (entry["current_register_state__owner_raw"] or "").strip()
        if not name:
            unassigned += entry["total"]
            continue
        named.append(
            CountRow(
                label=name,
                count=entry["total"],
                # No drill-through. The register filters on the *resolved* owner
                # and this counts the source name, so a link would open a list
                # that disagrees with the number above it — the one thing every
                # other link on this page is built to avoid.
                url="",
            )
        )
    if unassigned:
        named.append(CountRow(label=unassigned_label, count=unassigned, url=""))
    return named


def stage_distribution(user: Any) -> list[CountRow]:
    """Where the visible active files stand in the external process.

    Stage only. A disposition says why Koda stopped working on something, which
    is a different question and does not belong on a bar beside procedural
    stages (specification 3.4).
    """
    grouped = (
        active_matters(user)
        .filter(stage__isnull=False)
        .values("stage__key", "stage__label_et", "stage__sort_order")
        .annotate(total=scoped_count())
        .order_by("stage__sort_order")
    )
    return [
        CountRow(
            label=entry["stage__label_et"],
            count=entry["total"],
            url=_teemad(olek="avatud", liik=RecordMode.FULL, hetkeseis=entry["stage__key"]),
        )
        for entry in grouped
    ]


# ---------------------------------------------------------------------------
# Recent incoming
# ---------------------------------------------------------------------------


def recent_incoming(user: Any) -> QuerySet[Matter]:
    """What has arrived lately, newest first.

    Ordered by the date the material was *received* rather than created, so a
    Matter entered late still appears where the department expects it. Rows with
    no received date fall to the end rather than to the top.
    """
    return (
        Matter.objects.visible_to(user)
        .filter(record_mode=RecordMode.FULL)
        .select_related("owner", "source_organisation", "stage")
        .order_by(F("received_date").desc(nulls_last=True), "-created_at")[:RECENT_INCOMING_LIMIT]
    )


# ---------------------------------------------------------------------------
# The whole page
# ---------------------------------------------------------------------------


def _empty_upcoming() -> UpcomingResult:
    return UpcomingResult(rows=[], total=0, window=DEFAULT_DEADLINE_WINDOW)


@dataclass
class Dashboard:
    cards: list[SummaryCard] = field(default_factory=list)
    attention: list[AttentionRow] = field(default_factory=list)
    upcoming: UpcomingResult = field(default_factory=_empty_upcoming)
    windows: list[WindowOption] = field(default_factory=list)
    responsibility: list[CountRow] = field(default_factory=list)
    drafting_responsibility: list[CountRow] = field(default_factory=list)
    stages: list[CountRow] = field(default_factory=list)
    incoming: list[Matter] = field(default_factory=list)

    @property
    def attention_count(self) -> int:
        return len(self.attention)


def build_dashboard(
    user: Any, today: date | None = None, window: DeadlineWindow | None = None
) -> Dashboard:
    """Assemble the page.

    The responsibility rail reads :func:`source_responsibility`, not
    :func:`owner_inventory`. The dashboard's responsibility breakdown groups by
    the register's own name rather than by the resolved account, because the
    register names a colleague who has no account here and the resolved grouping
    would file that work under *Määramata* — discarding the one thing the
    register is certain about (docs/adr/0021). The resolved inventory remains
    available and tested; the register's own filters are where it drills
    through, so it is not duplicated here beside numbers it would disagree with.

    ``window`` reaches only the deadline table. The summary cards keep their own
    fixed seven-day horizon: they are the page's headline figures rather than a
    view of the list below them, and a card that moved with a filter would stop
    meaning the same thing from one visit to the next.
    """
    today = today or timezone.localdate()
    window = window or DEFAULT_DEADLINE_WINDOW
    return Dashboard(
        cards=summary_cards(user, today),
        attention=attention_rows(user, today),
        upcoming=upcoming_rows(user, today, window),
        windows=window_options(window),
        responsibility=source_responsibility(user),
        drafting_responsibility=drafting_by_responsibility(user),
        stages=stage_distribution(user),
        incoming=list(recent_incoming(user)),
    )
