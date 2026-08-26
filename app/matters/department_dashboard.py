"""Osakonna töö — what is currently going on across the lawyers.

A third surface, deliberately not a replacement for either of the two that
exist. *Minu töö* answers "what do I have to do today". *Ülevaade* answers
"what is the state of the department's files", and every reader gets it.
This answers a question only one person has: **who is carrying what, and where
should I look first.**

The difference from Ülevaade is the by-lawyer table. Everything else on the
page — the attention list, the upcoming dates, the definition of "active" and
of "overdue" — is imported from :mod:`app.matters.dashboard` rather than
restated here. Two similar definitions of overdue in two files is how two
screens start disagreeing about the same Matter, and the head is precisely the
person who would notice and stop trusting both.

Two rules run through every function.

**Authorization before arithmetic.** Every queryset starts from
``Matter.objects.visible_to(user)``. A department head sees RESTRICTED content
because ``DEPARTMENT_HEAD`` is a role the central authorization already
entitles — not because this module decided so, and nothing here re-implements
``visibility == NORMAL or …``. Point the same functions at a specialist and
they return the specialist's authorized world.

**This is not a staff evaluation.** Every column is a count of observable
states: how many files sit with somebody, how many carry a late instruction,
how many have none. There is no ranking, no score, no rate, no percentage and
no colour grading of people. Lawyers are listed alphabetically, and that
ordering is load-bearing: sorting the table by any of its numbers would turn an
oversight tool into a leaderboard, which is both forbidden (specification 18.8)
and the fastest way to make the underlying data worth gaming.

A count of open matters is inventory. One of them can be a two-line monitoring
note and the next a year of consultation, so it measures neither effort nor
performance and is never labelled as if it did.
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
from app.core.dates import format_estonian_date
from app.matters.dashboard import (
    UPCOMING_HORIZON_DAYS,
    AttentionRow,
    SummaryCard,
    UpcomingRow,
    active_matters,
    attention_rows,
    overdue_actions,
    reviews_due,
    upcoming_rows,
    without_next_action,
)
from app.matters.enums import RecordMode
from app.matters.models import Matter
from app.matters.selectors import MISSING

#: The summary cards and the matrix look this far ahead for a response
#: deadline. The same seven days Ülevaade uses, imported as a number rather
#: than re-chosen, so the two pages cannot drift apart by a week.
DEADLINE_HORIZON_DAYS = 7

#: How far back "recently arrived" reaches. A fortnight, matching the upcoming
#: horizon, because a department review is a fortnightly conversation.
INCOMING_WINDOW_DAYS = UPCOMING_HORIZON_DAYS

#: Caps on the lists. The count above each list is the honest total either way.
UNASSIGNED_LIMIT = 25
INCOMING_LIMIT = 15

#: Roles whose holders do casework and therefore belong in the team table even
#: with nothing open. A READER reads and an ADMINISTRATOR administers; neither
#: carries files, and listing them with a row of zeroes would suggest they
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


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def summary_cards(user: Any, today: date | None = None) -> list[SummaryCard]:
    """The department-wide operational totals, in the head's reading order.

    Reuses ``SummaryCard`` and the same underlying selectors as Ülevaade. The
    cards differ in wording and order, not in what they count.

    ``Ülevaatus/ootamine`` is counted separately from ``Tegevuse tähtaeg
    möödas`` and never added to it. Only a DO with a DEADLINE can be late;
    waiting on a ministry past a review date means somebody should look, not
    that anybody failed, and a page that conflates the two teaches its reader
    to ignore both (specification 18.8).
    """
    today = today or timezone.localdate()
    horizon = today + timedelta(days=DEADLINE_HORIZON_DAYS)
    active = active_matters(user)

    return [
        SummaryCard(
            key="active",
            label="Aktiivsed teemad",
            count=active.count(),
            url=register_url(**_open_full()),
        ),
        SummaryCard(
            key="unassigned",
            label="Vastutajata",
            count=active.filter(owner__isnull=True).count(),
            url=register_url(**_open_full(), vastutaja=MISSING),
        ),
        SummaryCard(
            key="deadlines",
            label=f"Arvamuse tähtaeg {DEADLINE_HORIZON_DAYS} päeva jooksul",
            count=active.filter(
                response_deadline__gte=today, response_deadline__lte=horizon
            ).count(),
            # The range itself, not a sort and an apology. The register has read
            # `?tahtaeg_alates=`/`?tahtaeg_kuni=` since Stage 2E.1; the card was
            # written before that and kept telling the reader it could not
            # (app/matters/register_filters.py).
            url=register_url(
                **_open_full(),
                tahtaeg_alates=format_estonian_date(today),
                tahtaeg_kuni=format_estonian_date(horizon),
            ),
        ),
        SummaryCard(
            key="overdue",
            label="Tegevuse tähtaeg möödas",
            count=overdue_actions(user, today).count(),
            url=register_url(**_open_full(), tegevus="hilinenud"),
            note="Tegevused, mille tähtaeg on käes olnud",
        ),
        SummaryCard(
            key="no_action",
            label="Järgmiseks puudub",
            count=without_next_action(user).count(),
            url=register_url(**_open_full(), tegevus=MISSING),
        ),
        SummaryCard(
            key="review_due",
            label="Ülevaatus või ootamine vajab pilku",
            count=reviews_due(user, today).count(),
            # The register filters WAIT and MONITOR review dates separately and
            # this counts both, so the link opens the whole active list rather
            # than a filter that would show fewer rows than the number above it.
            url=register_url(**_open_full()),
            note="Ei ole hilinemine; loend on laiem kui arv",
        ),
    ]


# ---------------------------------------------------------------------------
# The lawyer matrix
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatrixCell:
    """One number in the team table, with the list it opens."""

    count: int
    url: str
    #: True where the register cannot yet express the filter this counted, so
    #: the link opens the lawyer's whole active list instead. Always a
    #: *superset*, never a subset — a destination holding fewer rows than the
    #: number above it reads as a bug in the count. Rendered as a hint, so
    #: nobody takes the link for a promise it cannot keep (Stage-2F brief 35).
    approximate: bool = False


@dataclass(frozen=True)
class LawyerRow:
    """One lawyer's current portfolio, as counts of observable states.

    Inventory and attention, never workload. Nothing here is summed into a
    single figure, because a single figure invites the comparison the whole
    page is arranged to avoid.
    """

    user: User
    active: MatrixCell
    deadlines_soon: MatrixCell
    overdue: MatrixCell
    no_action: MatrixCell
    review_due: MatrixCell
    recently_received: MatrixCell
    #: A former colleague who still owns live work. Surfaced rather than
    #: hidden: dropping the row would take the Matter off this page entirely,
    #: and an active file owned by somebody who has left is exactly the thing
    #: a department head needs to see (Stage-2F brief 34).
    is_former_member: bool = False

    @property
    def display_name(self) -> str:
        return self.user.display_name

    @property
    def cells(self) -> tuple[MatrixCell, ...]:
        """The row's six counts, in the order the table's headers declare them.

        Built here rather than in the template because Django's ``for`` cannot
        iterate a literal tuple, and six near-identical hand-written cells is
        six chances for one of them to drift out of step with its header.
        """
        return (
            self.active,
            self.deadlines_soon,
            self.overdue,
            self.no_action,
            self.review_due,
            self.recently_received,
        )


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


def lawyer_matrix(user: Any, today: date | None = None) -> list[LawyerRow]:
    """Every current caseworker, alphabetically, with six counts each.

    Six grouped queries plus one for the people — not one query per lawyer per
    metric. The real department is small enough that the naive shape would work
    and still be wrong: a query count that grows with the number of colleagues
    is a page that degrades exactly when somebody is hired (Stage-2F brief 47).

    Ordering is alphabetical by display name and nothing else. Sorting by any
    column would rank colleagues by a number that measures inventory rather
    than effort, which is forbidden and would also be believed.
    """
    today = today or timezone.localdate()
    horizon = today + timedelta(days=DEADLINE_HORIZON_DAYS)
    since = today - timedelta(days=INCOMING_WINDOW_DAYS)

    active = active_matters(user)
    counts = {
        "active": _by_owner(active),
        "deadlines": _by_owner(
            active.filter(response_deadline__gte=today, response_deadline__lte=horizon)
        ),
        "overdue": _by_owner(
            active.filter(pk__in=overdue_actions(user, today).values("matter_id"))
        ),
        "no_action": _by_owner(without_next_action(user)),
        "review_due": _by_owner(active.filter(pk__in=reviews_due(user, today).values("matter_id"))),
        "incoming": _by_owner(active.filter(received_date__gte=since, received_date__lte=today)),
    }

    owner_ids = {owner_id for owner_id in counts["active"] if owner_id is not None}
    people = User.objects.filter(
        Q(is_active=True, role__in=CASEWORK_ROLES) | Q(pk__in=owner_ids)
    ).order_by("display_name")

    rows: list[LawyerRow] = []
    for person in people:
        base = {**_open_full(), "vastutaja": person.pk}
        rows.append(
            LawyerRow(
                user=person,
                active=MatrixCell(counts["active"].get(person.pk, 0), register_url(**base)),
                deadlines_soon=MatrixCell(
                    counts["deadlines"].get(person.pk, 0),
                    register_url(**base, jarjestus="deadline"),
                    approximate=True,
                ),
                overdue=MatrixCell(
                    counts["overdue"].get(person.pk, 0),
                    register_url(**base, tegevus="hilinenud"),
                ),
                no_action=MatrixCell(
                    counts["no_action"].get(person.pk, 0),
                    register_url(**base, tegevus=MISSING),
                ),
                review_due=MatrixCell(
                    counts["review_due"].get(person.pk, 0),
                    register_url(**base),
                    approximate=True,
                ),
                recently_received=MatrixCell(
                    counts["incoming"].get(person.pk, 0),
                    register_url(**base),
                    approximate=True,
                ),
                is_former_member=not person.is_active,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Unassigned, incoming and data quality
# ---------------------------------------------------------------------------


def unassigned_matters(user: Any) -> QuerySet[Matter]:
    """Live work with nobody's name on it.

    Open FULL only. A decade of ownerless archive rows is a historical fact
    about a spreadsheet, not a queue anybody can act on, and mixing the two
    would bury the handful of files that genuinely need assigning today.
    """
    return (
        active_matters(user)
        .filter(owner__isnull=True)
        .select_related("stage")
        .prefetch_related("source_organisations")
        .order_by("response_deadline", "-received_date", "-created_at")
    )


def recent_incoming(user: Any, today: date | None = None) -> QuerySet[Matter]:
    """Current work that arrived lately, across the team.

    Ordered and filtered by ``received_date`` — when the material reached Koda
    — never by ``created_at``. For an imported Matter ``created_at`` is the
    moment a migration script ran, and putting a 2019 file at the top of
    "recently arrived" because it was imported on Tuesday would be an import
    artefact presented as a business fact (Stage-2F brief 38).
    """
    today = today or timezone.localdate()
    since = today - timedelta(days=INCOMING_WINDOW_DAYS)
    return (
        active_matters(user)
        .filter(received_date__gte=since, received_date__lte=today)
        .select_related("owner", "stage")
        .prefetch_related("source_organisations")
        .order_by("-received_date", "title")[:INCOMING_LIMIT]
    )


@dataclass(frozen=True)
class QualitySignal:
    label: str
    count: int
    url: str
    help_text: str


def quality_signals(user: Any) -> list[QualitySignal]:
    """Small operational triage. Not a second Andmekvaliteet tab.

    Each line is a field the *current* workflow expects and this Matter has
    not got. Archive rows are excluded throughout, which is the whole point: a
    2014 register row with no stage is complete and correct history, and
    calling it a defect would put thousands of non-problems in front of
    somebody looking for the four that matter (Stage-2F brief 40).
    """
    active = active_matters(user)
    return [
        QualitySignal(
            label="Aktiivne teema ilma vastutajata",
            count=active.filter(owner__isnull=True).count(),
            url=register_url(**_open_full(), vastutaja=MISSING),
            help_text="Praegune töökorraldus eeldab vastutajat.",
        ),
        QualitySignal(
            label="Aktiivne teema ilma järgmise tegevuseta",
            count=without_next_action(user).count(),
            url=register_url(**_open_full(), tegevus=MISSING),
            help_text="Järgmiseks puudub — teema ei ole kellegi töölaual nähtav.",
        ),
        QualitySignal(
            label="Aktiivne teema ilma hetkeseisuta",
            count=active.filter(stage__isnull=True).count(),
            url=register_url(**_open_full(), hetkeseis=MISSING),
            help_text="Menetlusetapp on aktiivsel teemal ootuspärane.",
        ),
    ]


# ---------------------------------------------------------------------------
# The whole page
# ---------------------------------------------------------------------------


@dataclass
class DepartmentWork:
    cards: list[SummaryCard] = field(default_factory=list)
    lawyers: list[LawyerRow] = field(default_factory=list)
    attention: list[AttentionRow] = field(default_factory=list)
    upcoming: list[UpcomingRow] = field(default_factory=list)
    unassigned: list[Matter] = field(default_factory=list)
    unassigned_total: int = 0
    incoming: list[Matter] = field(default_factory=list)
    quality: list[QualitySignal] = field(default_factory=list)

    @property
    def has_former_members(self) -> bool:
        return any(row.is_former_member for row in self.lawyers)


def build_department_work(user: Any, today: date | None = None) -> DepartmentWork:
    """The whole page, for one authorized reader."""
    today = today or timezone.localdate()
    unassigned = unassigned_matters(user)
    return DepartmentWork(
        cards=summary_cards(user, today),
        lawyers=lawyer_matrix(user, today),
        # Imported wholesale. The head's cross-team attention list is the same
        # five truthful states Ülevaade already defines, over a wider
        # authorized population — not a second, similar list beside it.
        attention=attention_rows(user, today),
        # `.rows`, and the default fortnight. Ülevaade's period control belongs
        # to Ülevaade; this page keeps the fixed horizon its heading and its
        # empty state describe.
        upcoming=upcoming_rows(user, today).rows,
        unassigned=list(unassigned[:UNASSIGNED_LIMIT]),
        unassigned_total=unassigned.count(),
        incoming=list(recent_incoming(user, today)),
        quality=quality_signals(user),
    )
