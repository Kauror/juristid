"""One read of the shared work model, and one answer per denominator.

PERF-02 and PERF-03. Both modules that build `/osakond/` documented the same
invariant in prose — *"One read of the shared work model, reused by the
intervention list and by the Matter count beside it. The alternative is the same
query twice with two slightly different filters, which is how a heading stops
agreeing with the list under it."* — while the page performed five. The
Statistika pages asked *"out of how many"* once per metric, and got the same
number back nine times over one table.

Neither was a wrong number. Both were the same number bought repeatedly, which
is why nothing caught them: a page can only get slower this way, never wronger.
So the tests here are about **shape**, not about totals.

Three properties, and the reason each is stated the way it is.

**Constancy, not a constant.** `test_the_department_reads_the_work_model_once`
asserts `== 1`, because one is the number the docstrings claim and any other
number means a caller re-read it. The budgets in `tests/test_scope_query_cost.py`
carry the absolute ceilings.

**Sharing must not move a number.** The parameter that makes this cheap is an
opportunity to hand a filter the wrong population, and the failure would be
silent — a heading disagreeing with its list, which is exactly what the shared
read exists to prevent. Every figure is therefore asserted equal with and
without the shared list.

**A memo must not outlive the filter that produced it.** `ReportingContext` is
the filter state. A denominator remembered on it is unreachable from a different
filter *by construction*, and the test proves the construction rather than
trusting it.
"""

from __future__ import annotations

import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.matters import department_dashboard as dd
from app.matters import overview as ov
from app.matters import register_filters as rf
from app.matters import work_items as wi

pytestmark = pytest.mark.django_db

PAGE = "matters:department"


@pytest.fixture
def today():
    return datetime.date(2026, 9, 2)


@pytest.fixture
def counting(monkeypatch):
    """Count entries into `work_items`, wherever they are made from."""
    calls: list[int] = []
    original = wi.work_items

    def spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(wi, "work_items", spy)
    return calls


# ---------------------------------------------------------------------------
# PERF-03 — the page reads the work model once
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scope", [ov.SCOPE_DEPARTMENT, ov.SCOPE_AREAS])
def test_the_department_reads_the_work_model_once(client, department_head, counting, scope):
    """The number both modules' docstrings already claim.

    Five reads for the department scope and three for Valdkonniti, each several
    queries over `matters_matter`, `workflow_nextaction` and
    `intelligence_matterimportantdate`, for one answer that cannot change inside
    a request.
    """
    client.force_login(department_head)

    response = client.get(reverse(PAGE) + f"?vaade={scope}")

    assert response.status_code == 200
    assert len(counting) == 1, (
        f"{scope} read the work model {len(counting)} times; every consumer on this "
        "page wants the same list for the same reader on the same day"
    )


def test_a_specialist_pays_the_same_once(client, specialist, counting):
    """Not a property of being a head. The team table is head-only, so a
    specialist never reached that read — the three register-filter reads were
    everybody's."""
    client.force_login(specialist)

    assert client.get(reverse(PAGE)).status_code == 200
    assert len(counting) == 1


def test_the_quiet_population_never_reads_the_work_model(department_head, today, counting):
    """`?too=muutusteta-30` is a Matter-level state with no dated obligation.

    It was reading the whole work model and discarding it on the next line —
    three queries and a full row materialisation for a list nothing consulted.
    Asserted for every caller in the product, not only for this page.
    """
    wi.work_population_ids(department_head, wi.WORK_QUIET_30, today=today)

    assert counting == []


# ---------------------------------------------------------------------------
# Sharing the read must not move a number
# ---------------------------------------------------------------------------


def _seeded(owner, today):
    from app.workflow.enums import ActionKind, DateSemantics
    from app.workflow.services import set_next_action
    from tests import factories

    for index, offset in enumerate((-3, -1, 1, 4)):
        matter = factories.MatterFactory(
            owner=owner, title=f"Jagatud {index}", reference_year=2026, reference_number=700 + index
        )
        set_next_action(
            matter=matter,
            text=f"Tegevus {index}",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=today + datetime.timedelta(days=offset),
            actor=owner,
        )
    factories.MatterFactory(title="Vastutajata", reference_year=2026, reference_number=799)


def test_the_shared_list_changes_no_figure(department_head, specialist, today):
    """The strip, read both ways, figure by figure.

    The parameter exists to save a query. If it can also change a count, it is
    the defect the shared read was introduced to prevent, wearing the costume of
    the fix.
    """
    _seeded(specialist, today)
    items = wi.work_items(department_head, today=today)

    shared = dd.seis_figures(department_head, today, items=items)
    alone = dd.seis_figures(department_head, today)

    assert [(f.key, f.value, f.url) for f in shared] == [(f.key, f.value, f.url) for f in alone]


def test_the_shared_list_changes_no_team_row(department_head, specialist, today):
    _seeded(specialist, today)
    items = wi.work_items(department_head, today=today)

    shared = dd.team_rows(department_head, today, items=items)
    alone = dd.team_rows(department_head, today)

    assert [(row.key, [cell.value for cell in row.cells]) for row in shared] == [
        (row.key, [cell.value for cell in row.cells]) for row in alone
    ]


def test_the_shared_list_changes_no_area_row(department_head, specialist, today):
    _seeded(specialist, today)
    items = wi.work_items(department_head, today=today)

    shared = ov.build_overview(department_head, today=today, items=items)
    alone = ov.build_overview(department_head, today=today)

    assert [(row.key, row.open_count) for row in shared.areas] == [
        (row.key, row.open_count) for row in alone.areas
    ]
    assert shared.open_matters == alone.open_matters


@pytest.mark.parametrize(
    "key",
    [wi.WORK_OVERDUE, wi.WORK_DEADLINE_THIS_WEEK, wi.WORK_RIPE, wi.WORK_NEEDS_ATTENTION],
)
def test_a_shared_list_and_a_fresh_one_select_the_same_matters(
    department_head, specialist, today, key
):
    """Through the register's own pipeline, which is what the figures count."""
    _seeded(specialist, today)
    items = wi.work_items(department_head, today=today)
    params = {"olek": "avatud", "liik": "FULL", "too": key}

    shared = rf.register_population(department_head, params, today=today, shared_items=items)
    alone = rf.register_population(department_head, params, today=today)

    assert set(shared.values_list("pk", flat=True)) == set(alone.values_list("pk", flat=True))


def test_the_shared_list_never_survives_the_responsible_narrowing(
    department_head, specialist, other_specialist, today
):
    """The one place where handing over the page's list would be wrong.

    `filter_by_work_state` builds a *narrowed* list when `?too_vastutaja=` is
    present, and that list — not the page's — is what the population must be
    resolved from. The two are one rename apart, and no figure on `/osakond/`
    sets `too_vastutaja`, so nothing on that page would notice if they were
    swapped. This does.
    """
    from app.workflow.enums import ActionKind, DateSemantics
    from app.workflow.services import set_next_action
    from tests import factories

    mine = factories.MatterFactory(
        owner=specialist, title="Minu hilinenud", reference_year=2026, reference_number=801
    )
    theirs = factories.MatterFactory(
        owner=other_specialist, title="Nende hilinenud", reference_year=2026, reference_number=802
    )
    for matter, who in ((mine, specialist), (theirs, other_specialist)):
        set_next_action(
            matter=matter,
            text="Esitan arvamuse",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=today - datetime.timedelta(days=2),
            responsible=who,
            actor=who,
        )

    items = wi.work_items(department_head, today=today)
    params = {
        "olek": "avatud",
        "liik": "FULL",
        "too": wi.WORK_OVERDUE,
        "too_vastutaja": str(specialist.pk),
    }

    listed = rf.register_population(department_head, params, today=today, shared_items=items)

    assert set(listed.values_list("title", flat=True)) == {"Minu hilinenud"}, (
        "the shared list reached the population instead of the narrowed one, so "
        "one person's page is reporting a colleague's late work"
    )


# ---------------------------------------------------------------------------
# PERF-02 — a denominator is remembered, and cannot outlive its filter
# ---------------------------------------------------------------------------


def test_a_shared_answer_is_computed_once(reporting_context, department_head):
    context = reporting_context(department_head)
    calls: list[int] = []

    def compute() -> int:
        calls.append(1)
        return 7

    assert context.shared("probe", compute) == 7
    assert context.shared("probe", compute) == 7
    assert len(calls) == 1


def test_a_different_filter_cannot_see_the_answer(reporting_context, department_head):
    """The memo is on the filter state, so a different filter is a different
    object. Asserted rather than assumed, because the field being `init=False`
    is the only thing that makes it true — with `init=True`, `replace` hands the
    new state the old state's dict by reference."""
    context = reporting_context(department_head)
    context.shared("probe", lambda: 1)

    moved = context.with_period(context.period)

    assert moved.shared("probe", lambda: 2) == 2


def test_two_facts_that_are_equal_today_do_not_share_a_key(reporting_context, department_head):
    """Occurrences and distinct binaries are 767 apiece in production and are
    different questions. A key derived from a function name would merge them,
    and nothing would notice until a letter was filed twice."""
    context = reporting_context(department_head)

    assert context.shared("historical.resource_occurrences", lambda: 10) == 10
    assert context.shared("historical.unique_binary_contents", lambda: 9) == 9


def test_the_denominators_are_read_once_per_page(client, department_head):
    """The property the finding is about, on the real page.

    Counted by SQL string rather than by shape: the nine reads were byte
    identical, which is what makes 'the same question asked nine times' the
    right description and 'nine similar questions' the wrong one.
    """
    client.force_login(department_head)

    with CaptureQueriesContext(connection) as captured:
        assert client.get("/statistika/andmekvaliteet/").status_code == 200

    statements = [query["sql"] for query in captured.captured_queries]
    repeated = {sql: statements.count(sql) for sql in set(statements) if statements.count(sql) > 3}

    assert not repeated, "a statement ran more than three times on one page: " + "; ".join(
        f"x{count}" for count in repeated.values()
    )
