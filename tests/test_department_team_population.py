"""Who the Meeskond table names, and where everybody else's work is counted.

A named row in Osakond → Meeskond is a claim about a person: *this colleague is
a current member of the department*. It used to be a claim about a file. The
population was «active caseworkers, unioned with everybody appearing in any
column», so owning one Matter was enough to be listed beside the lawyers — a
colleague from another unit, a technical account, a departed specialist. The
page that exists to say who carries the department's work said they did
(docs/adr/0036, amendment of 2026-09-11).

What is asserted here
---------------------

**Membership comes from one rule.** The named rows are
`app.accounts.selectors.department_workers()` — the same definition the persona
list and every assignment control read. The rule itself has its own suite in
`tests/test_department_workers.py`; what these cases prove is that this table
reads it and adds nothing of its own.

**No amount of ownership converts anybody.** One Matter, five, overdue ones,
opinions sent this year: every case below hands an outsider real work and then
asserts they are still not a colleague.

**The work does not disappear with the name.** Removing a row must not remove a
Matter from the department's oversight, so everything owned outside the
department is aggregated into one anonymous `Väljaspool osakonda` row and
`Kokku` still reconciles column by column.

**And the aggregate names nobody.** It is a bucket, deliberately: the Matters
keep their real owners on the register and on their own pages, and this table
simply must not present those owners as departmental colleagues.

Every assertion is about a *role* or a *flag* on a fixture, never about a
display name. The reported defect was somebody in particular appearing on the
page; the fix is a rule, and a suite written against the name would go on
passing the day it is somebody else.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone

from app.accounts.enums import UserRole
from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.matters import department_dashboard as dd
from app.matters.department import build_department
from app.matters.models import Matter
from app.submissions.enums import SubmissionStatus
from app.workflow.enums import ActionKind, DateSemantics
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

PAGE = "/osakond/"

#: Every way of not being a current department worker, as the account's own
#: attributes. Named by the property rather than by a person, because the rule
#: is the property — `department_workers()` refuses each of these for its own
#: stated reason, and this table inherits all of them rather than restating any.
OUTSIDE_ACCOUNTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("a reader", {"role": UserRole.READER}),
    ("an administrator", {"role": UserRole.ADMINISTRATOR, "is_staff": True}),
    ("an administrator with no technical grant", {"role": UserRole.ADMINISTRATOR}),
    ("a technical account carrying a work role", {"role": UserRole.SPECIALIST, "is_staff": True}),
    ("a superuser carrying a work role", {"role": UserRole.DEPARTMENT_HEAD, "is_superuser": True}),
    ("a departed specialist", {"role": UserRole.SPECIALIST, "is_active": False}),
)


# ---------------------------------------------------------------------------
# Reading the table
# ---------------------------------------------------------------------------


def rows_for(viewer: Any, today: date | None = None) -> list[dd.TeamRow]:
    return dd.team_rows(viewer, today or timezone.localdate())


def named(rows: list[dd.TeamRow]) -> list[dd.TeamRow]:
    """The rows that claim somebody is a member of the department."""
    return [
        row
        for row in rows
        if not row.is_total and not row.is_unassigned and not row.is_outside_department
    ]


def names(rows: list[dd.TeamRow]) -> set[str]:
    return {row.name for row in named(rows)}


def outside_row(rows: list[dd.TeamRow]) -> dd.TeamRow | None:
    return next((row for row in rows if row.is_outside_department), None)


def unassigned_row(rows: list[dd.TeamRow]) -> dd.TeamRow:
    return next(row for row in rows if row.is_unassigned)


def total_row(rows: list[dd.TeamRow]) -> dd.TeamRow:
    return next(row for row in rows if row.is_total)


def index_of(column: str) -> int:
    return next(
        position
        for position, (key, _label, _group, _sep) in enumerate(dd.TEAM_COLUMNS)
        if key == column
    )


def value(row: dd.TeamRow | None, column: str) -> int:
    return 0 if row is None else row.cells[index_of(column)].value


def meeskond_markup(client: Any, viewer: Any) -> str:
    """Only the Meeskond section of the rendered page.

    Scoped deliberately. A name may legitimately appear elsewhere on a page this
    reader is authorized to see — in a list of Matters, say — and asserting its
    absence from the whole document would be asserting something this change
    never claimed.
    """
    client.force_login(viewer)
    body = client.get(PAGE).content.decode()
    match = re.search(r'<section[^>]*aria-label="Meeskond".*?</section>', body, re.S)
    assert match is not None, "the Meeskond section did not render at all"
    return match.group()


# ---------------------------------------------------------------------------
# Building work
# ---------------------------------------------------------------------------


def open_matter(owner: Any, **kwargs: Any) -> Matter:
    return factories.MatterFactory(owner=owner, **kwargs)


def deadline_on(matter: Matter, when: date, actor: Any) -> None:
    set_next_action(
        matter=matter,
        text="Esita arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=when,
        actor=actor,
    )


def at_ten(day: date) -> datetime:
    return timezone.make_aware(datetime(day.year, day.month, day.day, 10, 0))


def touched_on(matter: Matter, when: date, actor: Any) -> None:
    """One Matter-level change event, on a day of the caller's choosing.

    Matter-level rather than a child's: `scope_change_events` passes those
    through on the Matter's own visibility, so this exercises the column rather
    than the child-visibility rule that has a suite of its own.
    """
    ChangeEvent.objects.create(
        matter=matter,
        actor=actor,
        event_type=ChangeEventType.MATTER_STAGE_CHANGED,
        occurred_at=at_ten(when),
        summary="Menetlusetapp muutus",
    )


def drafting(matter: Matter) -> None:
    """Mark one Matter as a current register row whose VÄLJA is still blank."""
    from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
    from app.legacy_import.models import MatterSourceReference

    digest = hashlib.sha256(b"osakond-meeskond-population").hexdigest()
    reference = MatterSourceReference.objects.create(
        matter=matter,
        source_system="EXCEL_REGISTER",
        source_file_name="Naidisregister.xlsx",
        source_snapshot_sha256=digest,
        source_sheet="2026",
        source_row_number=matter.reference_number,
        source_row_raw={"VASTUTAJA": ""},
        source_title=matter.title,
        source_era="2026",
    )
    CurrentRegisterState.objects.create(
        matter=matter,
        source_reference=reference,
        source_snapshot_sha256=digest,
        source_sheet="2026",
        source_row_number=matter.reference_number,
        currency=RegisterCurrency.CURRENT,
        status_label="Kooskolastusringil",
        opinion_sent_recorded=False,
        owner_raw="",
        owner_resolved=False,
        observed_at=timezone.now(),
    )


@pytest.fixture
def send_opinion(capture_evidence):
    """A SENT Submission with the immutable evidence the database insists on."""

    def send(matter: Matter, *, when: datetime, title: str = "Naidisarvamus") -> Any:
        version = capture_evidence(matter, b"%PDF-1.4 synthetic", f"{title}.pdf", "application/pdf")
        return factories.SubmissionFactory(
            matter=matter,
            title=title,
            status=SubmissionStatus.SENT,
            sent_at=when,
            final_version=version,
        )

    return send


@pytest.fixture
def outsider(db):
    """An active account with a real role that is not a department work role.

    The reported defect exactly: somebody who is not a lawyer, owns one open
    Matter, and was listed as a member of the legal team because of it.
    """
    return factories.ReaderFactory(display_name="Liisi Lugeja")


@pytest.fixture
def midweek() -> date:
    """A Wednesday, so «this week» and «last week» are never a day apart.

    A real weekday rather than `localdate()`: the world below places work in
    this week and in the previous one, and read on a Sunday those two windows
    slide into each other. Every date in the fixture is derived from this one.
    """
    today = timezone.localdate()
    return today - timedelta(days=today.weekday()) + timedelta(days=2)


# =========================================================================
# §25 A — the current team
# =========================================================================


def test_an_active_specialist_is_a_named_row(department_head, specialist) -> None:
    assert specialist.display_name in names(rows_for(department_head))


def test_an_active_department_head_is_a_named_row(department_head) -> None:
    assert department_head.display_name in names(rows_for(department_head))


def test_a_current_worker_carrying_nothing_still_has_a_row(department_head) -> None:
    """The table is the roster as well as the workload.

    Membership is not derived from activity: a colleague who happens to be
    between files this week is still a colleague, and a row of dashes is a
    truthful thing to print about them.
    """
    idle = factories.UserFactory(display_name="Aadam Vabajurist")
    assert not Matter.objects.filter(owner=idle).exists()

    row = next(row for row in rows_for(department_head) if row.name == idle.display_name)
    assert [cell.value for cell in row.cells] == [0] * len(dd.TEAM_COLUMNS)


def test_the_named_rows_are_exactly_the_canonical_department_workers(
    department_head, specialist, outsider
) -> None:
    """One definition, read here rather than restated here."""
    from app.accounts.selectors import department_workers

    open_matter(outsider)
    assert names(rows_for(department_head)) == {
        person.display_name for person in department_workers()
    }


def test_the_named_rows_stay_alphabetical_and_the_exceptions_come_last(
    department_head, specialist, outsider
) -> None:
    """Ordering is the guard against a leaderboard, and the bucket respects it:
    the people first, then the two rows that are not people, then Kokku."""
    open_matter(outsider)
    factories.UserFactory(display_name="Ülle Näidis")
    factories.UserFactory(display_name="Aadu Näidis")

    rows = rows_for(department_head)
    assert [row.name for row in named(rows)] == sorted(row.name for row in named(rows))
    assert [row.name for row in rows][-3:] == [
        dd.OUTSIDE_DEPARTMENT_NAME,
        "Vastutajata",
        "Kokku",
    ]


# =========================================================================
# §25 B/C — everybody else
# =========================================================================


@pytest.mark.parametrize(
    ("label", "attributes"), OUTSIDE_ACCOUNTS, ids=[label for label, _ in OUTSIDE_ACCOUNTS]
)
def test_owning_work_does_not_make_somebody_a_team_member(
    department_head, specialist, label: str, attributes: dict[str, Any]
) -> None:
    person = factories.UserFactory(display_name=f"Naidis {label}", **attributes)
    open_matter(person)

    rows = rows_for(department_head)
    assert person.display_name not in names(rows), label
    assert value(outside_row(rows), "open") == 1, label


def test_owning_a_great_deal_of_work_does_not_make_somebody_a_team_member(
    department_head, specialist, outsider, midweek
) -> None:
    """Volume is not membership either, and neither is urgency."""
    for _ in range(4):
        open_matter(outsider)
    late = open_matter(outsider)
    deadline_on(late, midweek - timedelta(days=10), specialist)

    rows = rows_for(department_head, today=midweek)
    assert outsider.display_name not in names(rows)
    assert value(outside_row(rows), "open") == 5
    assert value(outside_row(rows), "overdue") == 1


def test_a_departed_specialists_live_work_moves_to_the_bucket_not_off_the_page(
    department_head, specialist
) -> None:
    """Removing the name must not remove the Matter from oversight."""
    former = factories.UserFactory(display_name="Kadri Endine", is_active=False)
    open_matter(former)

    rows = rows_for(department_head)
    assert former.display_name not in names(rows)
    assert value(outside_row(rows), "open") == 1
    assert value(total_row(rows), "open") == 1


def test_a_departed_colleague_is_not_labelled_as_a_former_colleague(
    client, department_head, specialist
) -> None:
    """No name on the roster, and therefore no badge beside one either."""
    former = factories.UserFactory(display_name="Kadri Endine", is_active=False)
    open_matter(former)

    markup = meeskond_markup(client, department_head)
    assert former.display_name not in markup
    assert "endine" not in markup.casefold()


# =========================================================================
# §23 — the viewer is not automatically a colleague
# =========================================================================


def test_the_viewer_gets_no_row_merely_for_being_the_viewer(
    department_head, specialist, outsider
) -> None:
    """The reported regression, from the reader's own side.

    `is_self` marks a colleague's own row. It does not create one.
    """
    open_matter(outsider)
    rows = rows_for(outsider)

    assert outsider.display_name not in names(rows)
    assert not any(row.is_self for row in rows)


def test_a_worker_viewing_the_page_still_gets_their_own_row_marked(
    department_head, specialist
) -> None:
    rows = rows_for(specialist)
    own = next(row for row in named(rows) if row.name == specialist.display_name)
    assert own.is_self
    assert own.url == reverse("matters:my_work")


# =========================================================================
# §25 D — the aggregate row
# =========================================================================


def test_several_outside_owners_aggregate_into_one_row(department_head, specialist) -> None:
    first = factories.ReaderFactory(display_name="Esimene Valine")
    second = factories.AdministratorFactory(display_name="Teine Valine")
    third = factories.UserFactory(display_name="Kolmas Valine", is_active=False)
    for person in (first, second, third):
        open_matter(person)

    rows = rows_for(department_head)
    assert len([row for row in rows if row.is_outside_department]) == 1
    assert value(outside_row(rows), "open") == 3


def test_the_row_is_absent_when_no_work_sits_outside_the_department(
    department_head, specialist
) -> None:
    """A row of dashes under the team would read as a population that exists and
    is idle. The honest rendering of "nothing sits outside" is no row."""
    open_matter(specialist)
    factories.MatterFactory(owner=None)
    factories.ReaderFactory(display_name="Keegi Valine")

    rows = rows_for(department_head)
    assert outside_row(rows) is None
    assert not any(row.name == dd.OUTSIDE_DEPARTMENT_NAME for row in rows)


def test_the_row_is_the_agreed_words(department_head, specialist, outsider) -> None:
    open_matter(outsider)
    row = outside_row(rows_for(department_head))
    assert row is not None
    assert row.name == "Väljaspool osakonda"


def test_the_row_carries_no_person_and_nowhere_to_go(department_head, specialist, outsider) -> None:
    """A bucket, not a desk.

    No link rather than a wrong one: the register cannot express «owned by
    anybody who is not in the department», so every address it could be given
    would open a different set of Matters (brief §17).
    """
    open_matter(outsider)
    row = outside_row(rows_for(department_head))
    assert row is not None

    assert row.initials == ""
    assert row.url == ""
    assert not row.is_self
    assert not row.is_unassigned
    assert not row.is_total
    assert all(cell.url == "" for cell in row.cells)


def test_no_outside_owner_is_named_anywhere_in_the_team_table(
    client, department_head, specialist, send_opinion
) -> None:
    """Not in text, not in a tooltip, not in a link, not in a hidden label.

    The whole markup of the section is the assertion, because this leak does not
    have to be visible to be a disclosure.
    """
    reader = factories.ReaderFactory(display_name="Liisi Lugeja")
    admin = factories.AdministratorFactory(display_name="Andres Administraator")
    former = factories.UserFactory(display_name="Kadri Endine", is_active=False)
    for person in (reader, admin, former):
        send_opinion(open_matter(person), when=timezone.now() - timedelta(days=1))

    markup = meeskond_markup(client, department_head)

    # One row, located by the name cell rather than by the string: the marker
    # beside it carries the same words as its tooltip, which is two occurrences
    # of one row.
    rendered = re.findall(
        rf'class="uxteam__name[^"]*">\s*{re.escape(dd.OUTSIDE_DEPARTMENT_NAME)}', markup
    )
    assert len(rendered) == 1

    for person in (reader, admin, former):
        assert person.display_name not in markup
        assert str(person.pk) not in markup
        # Initials are a name compressed, and two letters beside a row of
        # numbers still say who is carrying them.
        assert f">{person.initials}<" not in markup


# =========================================================================
# §25 E — and the unassigned pile is a different thing
# =========================================================================


def test_work_with_no_owner_stays_its_own_row(department_head, specialist, outsider) -> None:
    factories.MatterFactory(owner=None)
    open_matter(outsider)

    rows = rows_for(department_head)
    assert value(unassigned_row(rows), "open") == 1
    assert value(outside_row(rows), "open") == 1


def test_an_owner_outside_the_department_is_never_counted_as_unassigned(
    department_head, specialist, outsider
) -> None:
    """Two states, two rows. «Nobody has this» and «somebody has this and they
    are not one of us» are different things to do something about."""
    open_matter(outsider)

    rows = rows_for(department_head)
    assert value(unassigned_row(rows), "open") == 0
    assert value(outside_row(rows), "open") == 1


# =========================================================================
# §25 F/G — every column, and the line that adds them up
# =========================================================================


@pytest.fixture
def every_column(specialist, outsider, send_opinion, midweek) -> date:
    """One Matter for each column, twice: a colleague's and an outsider's.

    Every column the table computes, so that a column added to `TEAM_COLUMNS`
    with no home for outside owners fails here rather than quietly filing
    somebody else's work under a colleague's name.
    """
    week_start = midweek - timedelta(days=midweek.weekday())
    last_week = week_start - timedelta(days=4)

    for owner in (specialist, outsider):
        deadline_on(open_matter(owner), midweek - timedelta(days=7), specialist)
        deadline_on(open_matter(owner), midweek + timedelta(days=1), specialist)
        # No instruction at all — the `no_action` column, which every Matter
        # without a NextAction is in. It is open as well, which is the point.
        open_matter(owner)
        drafting(open_matter(owner))
        touched_on(open_matter(owner), last_week, specialist)
        send_opinion(open_matter(owner), when=at_ten(last_week))

    # And one Matter nobody carries, so the third bucket is never empty either.
    factories.MatterFactory(owner=None)
    return midweek


def test_every_column_puts_outside_work_in_the_bucket(department_head, every_column) -> None:
    rows = rows_for(department_head, today=every_column)
    outside = outside_row(rows)
    assert outside is not None

    for column, _label, _group, _sep in dd.TEAM_COLUMNS:
        assert value(outside, column) >= 1, column


def test_every_column_is_counted_in_exactly_one_row(department_head, every_column) -> None:
    """The partition, column by column.

    `Kokku` is the sum of everything above it, and the named rows, the bucket
    and the unassigned pile are the only things above it — so a Matter counted
    twice or dropped altogether shows up here as an arithmetic failure rather
    than as a number nobody can explain a month later.
    """
    rows = rows_for(department_head, today=every_column)
    total = total_row(rows)

    for position, (column, _label, _group, _sep) in enumerate(dd.TEAM_COLUMNS):
        parts = sum(row.cells[position].value for row in rows if not row.is_total)
        assert total.cells[position].value == parts, column


def test_the_total_population_is_what_it_was_before_the_rows_were_regrouped(
    department_head, every_column
) -> None:
    """Regrouping is not filtering.

    Every column's Kokku must equal the figure computed straight from the
    authorized population, whoever happens to own the rows in it.
    """
    from app.matters.dashboard import active_matters

    total = total_row(rows_for(department_head, today=every_column))

    assert total.cells[index_of("open")].value == active_matters(department_head).count()
    start, end = dd.reporting_year(every_column)
    assert (
        total.cells[index_of("sent_year")].value
        == dd.sent_submissions(department_head, since=start, until=end).count()
    )


def test_the_bucket_holds_exactly_the_difference(department_head, every_column) -> None:
    """Outside == total − colleagues − unassigned, for every column.

    Stated as its own assertion rather than inferred from the one above: this is
    the property that breaks if the bucket is ever computed from a *second*
    population — a wider or narrower read than the columns themselves — which is
    exactly how an aggregate row starts disagreeing with the table it sits in.
    """
    rows = rows_for(department_head, today=every_column)

    for position, (column, _label, _group, _sep) in enumerate(dd.TEAM_COLUMNS):
        colleagues = sum(row.cells[position].value for row in named(rows))
        nobody = unassigned_row(rows).cells[position].value
        expected = total_row(rows).cells[position].value - colleagues - nobody
        assert value(outside_row(rows), column) == expected, column


# =========================================================================
# §26 — the reconciliations the page already owed
# =========================================================================


def test_aruandlus_and_the_team_total_still_count_the_same_opinions(
    department_head, every_column
) -> None:
    """Regrouping the rows must not move the number the two surfaces share."""
    page = build_department(department_head, is_head=True, today=every_column)
    total = total_row(page.team)
    reporting = {row.label: row.count for row in page.reporting}

    assert total.cells[index_of("sent_year")].value == reporting["Saadetud arvamusi"]


def test_the_risk_strip_and_the_team_total_still_agree(department_head, every_column) -> None:
    page = build_department(department_head, is_head=True, today=every_column)
    total = total_row(page.team)
    figures = {figure.key: figure.value for figure in page.seis}

    assert total.cells[index_of("overdue")].value == figures["overdue"]
    assert total.cells[index_of("week")].value == figures["week"]
    assert total.cells[index_of("no_action")].value == figures["no_action"]
    assert total.cells[index_of("open")].value == page.open_matters
    assert unassigned_row(page.team).cells[index_of("open")].value == figures["unassigned"]


# =========================================================================
# §19 — authorization before arithmetic
# =========================================================================


def test_a_matter_the_reader_may_not_see_changes_nothing_about_the_bucket(
    reader, specialist
) -> None:
    """The bucket is computed over the viewer's own authorized world.

    A restricted Matter owned outside the department must not decide whether the
    row appears, what it counts, or what Kokku says — for a reader who is not
    entitled to it. Both lawyer roles read RESTRICTED work by role (ADR 0042),
    so the reader who genuinely cannot is a `READER`.
    """
    stranger = factories.ReaderFactory(display_name="Valine Lugeja")
    before = rows_for(reader)

    factories.MatterFactory(owner=stranger, visibility=Visibility.RESTRICTED)
    after = rows_for(reader)

    assert outside_row(before) is None
    assert outside_row(after) is None
    assert value(total_row(after), "open") == value(total_row(before), "open")


def test_the_entitled_reader_does_see_it(department_head) -> None:
    """The other half of the same rule: a head is entitled to RESTRICTED work,
    so for them the same Matter is in the bucket and in Kokku."""
    stranger = factories.ReaderFactory(display_name="Valine Lugeja")
    factories.MatterFactory(owner=stranger, visibility=Visibility.RESTRICTED)

    rows = rows_for(department_head)
    assert value(outside_row(rows), "open") == 1
    assert value(total_row(rows), "open") == 1


# =========================================================================
# §21 — and none of it costs a query per person
# =========================================================================


def test_the_bucket_costs_no_query_per_outside_owner(
    django_assert_max_num_queries, department_head, specialist
) -> None:
    """The same ceiling the team table already had.

    The bucket is the columns' own grouped dictionaries summed in Python over
    their non-worker keys, so twelve people in it cost exactly what nought in it
    costs. A per-owner implementation would be a query each, and would look
    perfectly healthy in a fixture holding one.
    """
    for index in range(12):
        open_matter(factories.ReaderFactory(display_name=f"Valine {index}"))

    with django_assert_max_num_queries(14):
        list(rows_for(department_head))
