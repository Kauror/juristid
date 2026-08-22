"""``Arvamusi koostamisel`` counts VÄLJA marks, not parseable dates.

Production P2 finished with a correct portfolio — 200 current Matters — and a
dashboard that said 29 Matters were still being drafted where the cutover said
15. The fourteen it disagreed about all have a ``VÄLJA`` cell holding something
the date parser cannot read.

The rule was never in doubt. `has_send_date` says presence decides, ADR 0021
says the date's value is source metadata that "nothing in the current-portfolio
decision reads". What happened is that three layers each answered the question
their own way: the cutover asked the raw cell, while the derived table and the
dashboard asked whether the *parsed* date was null — which is a different
question that happens to agree on most rows.

So these tests are mostly about agreement. The last one is the point: three
surfaces, one answer.
"""

from __future__ import annotations

import pytest

from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.final_cutover import apply_cutover_plan, build_cutover_plan
from app.legacy_import.register_semantics import has_send_date
from app.matters import dashboard
from tests.synthetic_cutover import (
    CURRENT_DRAFTING,
    CURRENT_SENT,
    CURRENT_SENT_UNPARSEABLE,
    FINAL_SNAPSHOT,
    RETIRING_IN_FORCE,
    UNPARSEABLE_SEND_CELL,
    approve_snapshot,
    build_world,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def applied(monkeypatch: pytest.MonkeyPatch):
    approve_snapshot(monkeypatch, sha256=FINAL_SNAPSHOT)
    world = build_world()
    apply_cutover_plan(build_cutover_plan(snapshot_sha256=FINAL_SNAPSHOT))
    return world


def state(world, title: str) -> CurrentRegisterState:
    return CurrentRegisterState.objects.get(matter=world.matters[title])


def titles(queryset) -> set[str]:
    return set(queryset.values_list("title", flat=True))


# ---------------------------------------------------------------------------
# The premise
# ---------------------------------------------------------------------------


def test_the_fixture_cell_really_is_a_mark_that_is_not_a_date():
    """Asserted, not assumed: the whole test file rests on this cell's shape."""
    from app.legacy_import.dates import parse_date

    assert has_send_date(UNPARSEABLE_SEND_CELL) is True
    assert parse_date(UNPARSEABLE_SEND_CELL, raw=UNPARSEABLE_SEND_CELL).value is None


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------


def test_a_nonblank_unparseable_send_cell_is_not_drafting(applied):
    """The production defect, in one row.

    The register said the opinion went out. It said so in words, so the parsed
    date is null — and every layer that asked the parsed date called this
    unfinished work.
    """
    row = state(applied, CURRENT_SENT_UNPARSEABLE)
    assert row.currency == RegisterCurrency.CURRENT
    assert row.opinion_sent_recorded is True
    assert row.opinion_sent_date is None, "the fixture's cell is deliberately not a date"
    assert row.is_drafting is False

    assert row not in CurrentRegisterState.objects.drafting()
    assert CURRENT_SENT_UNPARSEABLE not in titles(dashboard.drafting_matters(applied.people.head))


def test_recorded_without_a_parsed_date_is_a_valid_state(applied):
    """Not a contradiction to be repaired — a data-quality fact to be kept."""
    row = state(applied, CURRENT_SENT_UNPARSEABLE)
    assert (row.opinion_sent_recorded, row.opinion_sent_date) == (True, None)


# ---------------------------------------------------------------------------
# The three ordinary shapes
# ---------------------------------------------------------------------------


def test_a_blank_send_cell_is_drafting(applied):
    row = state(applied, CURRENT_DRAFTING)
    assert row.opinion_sent_recorded is False
    assert row.opinion_sent_date is None
    assert row.is_drafting is True
    assert CURRENT_DRAFTING in titles(dashboard.drafting_matters(applied.people.head))


def test_a_real_date_is_recorded_and_parsed(applied):
    row = state(applied, CURRENT_SENT)
    assert row.opinion_sent_recorded is True
    assert row.opinion_sent_date is not None
    assert row.is_drafting is False
    assert CURRENT_SENT not in titles(dashboard.drafting_matters(applied.people.head))


def test_a_matter_that_is_not_current_is_never_drafting(applied):
    """Presence alone is not the test; the Matter has to be current work.

    `RETIRING_IN_FORCE` has a terminal status and no send date — every half of
    the drafting predicate except the one that matters.
    """
    row = state(applied, RETIRING_IN_FORCE)
    assert row.currency != RegisterCurrency.CURRENT
    assert row.opinion_sent_recorded is False
    assert row.is_drafting is False
    assert RETIRING_IN_FORCE not in titles(dashboard.drafting_matters(applied.people.head))


# ---------------------------------------------------------------------------
# The invariant that matters most
# ---------------------------------------------------------------------------


def test_the_cutover_the_derived_table_and_the_dashboard_agree(applied):
    """Three surfaces, one answer.

    This is the assertion the production defect would have failed: the plan said
    15, the table said 29 and the dashboard repeated the table.
    """
    plan = build_cutover_plan(snapshot_sha256=FINAL_SNAPSHOT)
    from_plan = {c.matter.pk for c in plan.drafting_after}
    from_state = set(CurrentRegisterState.objects.drafting().values_list("matter_id", flat=True))
    from_dashboard = set(
        dashboard.drafting_matters(applied.people.head).values_list("pk", flat=True)
    )

    assert from_plan == from_state == from_dashboard
    assert from_plan, "the world must contain at least one drafting Matter"


def test_the_parsed_date_would_have_disagreed(applied):
    """Pins what the fix changed, so the test cannot pass for another reason.

    The old predicate is still expressible; it simply answers a different
    question, and the difference is exactly the unparseable row.
    """
    old_population = set(
        CurrentRegisterState.objects.current()
        .filter(opinion_sent_date__isnull=True)
        .values_list("matter_id", flat=True)
    )
    new_population = set(
        CurrentRegisterState.objects.drafting().values_list("matter_id", flat=True)
    )

    assert new_population < old_population
    assert old_population - new_population == {applied.matters[CURRENT_SENT_UNPARSEABLE].pk}


# ---------------------------------------------------------------------------
# Responsibility, authorization, rebuild
# ---------------------------------------------------------------------------


def test_drafting_responsibility_counts_only_blank_cells(applied):
    """The breakdown follows the population, so it inherits the correction.

    In production it read 13/8/7/1 against an expected 7/5/2/1 for the same
    reason this world would: the unparseable row carries a responsibility too.
    """
    rows = dashboard.drafting_by_responsibility(applied.people.head)
    total = sum(row.count for row in rows)
    assert total == dashboard.drafting_matters(applied.people.head).count()

    drafting = titles(dashboard.drafting_matters(applied.people.head))
    assert CURRENT_SENT_UNPARSEABLE not in drafting


def test_the_count_is_still_authorized_before_it_is_counted(applied, specialist):
    """A hidden Matter must not appear in somebody else's total.

    The fix changes which column the filter reads and nothing about where
    authorization happens; this is here so that stays true.
    """
    from app.core.enums import Visibility
    from app.matters.models import Matter

    visible = dashboard.drafting_matters(applied.people.head)
    assert visible.count() >= 1

    Matter.objects.filter(pk__in=[m.pk for m in visible]).update(visibility=Visibility.RESTRICTED)
    assert dashboard.drafting_matters(specialist).count() == 0


def test_rebuilding_the_derived_state_changes_nothing(applied):
    """Idempotent, and canonical rows are not its business."""
    from app.matters.models import Matter

    before_rows = dict(
        CurrentRegisterState.objects.values_list("matter_id", "opinion_sent_recorded")
    )
    before_drafting = set(
        CurrentRegisterState.objects.drafting().values_list("matter_id", flat=True)
    )
    before_matters = dict(
        Matter.objects.values_list("pk", "record_mode"),
    )

    apply_cutover_plan(build_cutover_plan(snapshot_sha256=FINAL_SNAPSHOT))

    assert (
        dict(CurrentRegisterState.objects.values_list("matter_id", "opinion_sent_recorded"))
        == before_rows
    )
    assert (
        set(CurrentRegisterState.objects.drafting().values_list("matter_id", flat=True))
        == before_drafting
    )
    assert dict(Matter.objects.values_list("pk", "record_mode")) == before_matters
