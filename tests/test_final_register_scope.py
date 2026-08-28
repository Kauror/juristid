"""The reviewed current scope, and the production incident that revealed it.

The final cutover was run in production against the approved workbook and
proposed making **2219** Matters current instead of 200 — activating two
thousand rows from 2011–2024. Nothing was applied; the gate caught it.

The cause is worth stating precisely, because the obvious summary is wrong.
`currency_of` retires a row when `HETKESEIS` is one of two terminal labels, and
treats anything else — including a blank — as still live. That default is
correct and deliberate: dropping live work is the harm, showing an extra row is
not. The defect is that the question was asked at all outside the years the
register maintains. The 2011–2022 era contracts have no status column, so those
rows carry no status to read; 2023 and 2024 have one but the department had
stopped keeping it current.

So the fix is a scope, not a change to what blank means. These tests pin both
halves: within the reviewed years the old semantics are untouched, and outside
them the row is retired **by scope**, with a reason that says so rather than
inventing a terminal status the register never wrote.
"""

from __future__ import annotations

import pytest

from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.final_cutover import (
    REVIEWED_SNAPSHOTS,
    Action,
    ReviewReason,
    Rule,
    apply_cutover_plan,
    build_cutover_plan,
    reviewed_snapshot,
    sheet_year,
)
from app.matters.enums import RecordMode
from app.workflow.enums import Disposition
from tests.synthetic_cutover import (
    FINAL_SNAPSHOT,
    IN_2025_LIVE,
    IN_2026_LIVE,
    OUT_2014_WITH_ENTRY,
    OUT_2016_REAL_CLOSURE,
    OUT_2018_BLANK,
    OUT_2019_STILL_OPEN,
    OUT_2023_LIVE,
    OUT_2024_LIVE,
    REVIEWED_YEARS,
    approve_snapshot,
    build_historical_world,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def reviewed(monkeypatch: pytest.MonkeyPatch) -> None:
    approve_snapshot(monkeypatch, sha256=FINAL_SNAPSHOT)


@pytest.fixture
def world():
    return build_historical_world()


def plan():
    return build_cutover_plan(snapshot_sha256=FINAL_SNAPSHOT)


def action_for(built, title: str) -> str:
    matter_id = built.matters[title].pk
    return next(c.action for c in plan().candidates if c.matter.pk == matter_id)


def candidate_for(built, title: str):
    matter_id = built.matters[title].pk
    return next(c for c in plan().candidates if c.matter.pk == matter_id)


# ---------------------------------------------------------------------------
# The premise: these rows really do carry no status
# ---------------------------------------------------------------------------


def test_the_old_sheets_genuinely_have_no_status_to_read(reviewed, world):
    """The fixture's premise, asserted rather than assumed.

    If the 2018 row carried a blank *string* rather than no column at all, the
    tests below would be checking something easier than production.
    """
    for title in (OUT_2018_BLANK, OUT_2019_STILL_OPEN, OUT_2014_WITH_ENTRY):
        assert candidate_for(world, title).observation.status_label == ""
    # And 2023/2024 do have one, which is what makes them the interesting case.
    assert candidate_for(world, OUT_2023_LIVE).observation.status_label != ""
    assert candidate_for(world, OUT_2024_LIVE).observation.status_label != ""


# ---------------------------------------------------------------------------
# The regression itself
# ---------------------------------------------------------------------------


def test_a_2018_row_with_no_status_stays_historical(reviewed, world):
    """The production defect, in one assertion.

    Before the scope existed this row reached `currency_of`, found no terminal
    status, found no continuation, and was classified CURRENT — so an already
    archived 2018 Matter was proposed for ACTIVATE.
    """
    candidate = candidate_for(world, OUT_2018_BLANK)
    assert candidate.currency == RegisterCurrency.RETIRED
    assert candidate.action == Action.ALREADY_RETIRED
    assert candidate.rule == Rule.RETIRED_BY_SCOPE


@pytest.mark.parametrize("title", [OUT_2023_LIVE, OUT_2024_LIVE])
def test_a_recent_out_of_scope_row_stays_historical_even_with_a_live_status(reviewed, world, title):
    """The rule is the reviewed scope, not "years lacking HETKESEIS".

    2023 and 2024 do carry a status and it says the work had not finished. They
    are still outside the years the snapshot was approved for, so they stay
    historical — and if the rule were merely "no column, no currency" these two
    would have become current.
    """
    candidate = candidate_for(world, title)
    assert candidate.currency == RegisterCurrency.RETIRED
    assert candidate.rule == Rule.RETIRED_BY_SCOPE
    assert candidate.action == Action.ALREADY_RETIRED


def test_out_of_scope_retirement_does_not_claim_a_terminal_status(reviewed, world):
    """Retired by scope is a different fact from retired by the register.

    An audit row saying a 2018 Matter ended because its HETKESEIS was terminal
    would be describing a column the workbook does not have.
    """
    already = candidate_for(world, OUT_2018_BLANK)
    assert already.rule == Rule.RETIRED_BY_SCOPE
    assert already.rule != Rule.RETIRED_BY_TERMINAL_STATUS
    assert "HETKESEIS" not in already.reason
    assert already.provenance(FINAL_SNAPSHOT)["rule"] == Rule.RETIRED_BY_SCOPE

    # The row the operation actually changes carries the scope sentence too, so
    # the report an operator reads says why rather than only what.
    retiring = candidate_for(world, OUT_2019_STILL_OPEN)
    assert retiring.action == Action.RETIRE
    assert retiring.rule == Rule.RETIRED_BY_SCOPE
    assert "ulatus" in retiring.reason
    assert "HETKESEIS" not in retiring.reason


# ---------------------------------------------------------------------------
# Within the reviewed years, nothing changed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("title", [IN_2025_LIVE, IN_2026_LIVE])
def test_a_live_row_inside_the_scope_is_current(reviewed, world, title):
    candidate = candidate_for(world, title)
    assert candidate.currency == RegisterCurrency.CURRENT
    assert candidate.rule == Rule.CURRENT
    assert candidate.action in {Action.ACTIVATE, Action.KEEP_CURRENT}


def test_the_scope_is_exactly_the_two_maintained_years(reviewed, world):
    current = {c.observation.sheet for c in plan().current_after}
    assert current == {"2025", "2026"}


# ---------------------------------------------------------------------------
# The protections the scope must not weaken
# ---------------------------------------------------------------------------


def test_an_out_of_scope_matter_that_is_still_current_retires(reviewed, world):
    assert action_for(world, OUT_2019_STILL_OPEN) == Action.RETIRE


def test_native_work_on_an_out_of_scope_matter_still_wins(reviewed, world, specialist):
    """The tie goes to the person, scope or no scope.

    Retiring a historical row is the ordinary answer; retiring one somebody has
    since written on is the failure the guard exists to prevent, and widening
    the retirement rule must not quietly widen past it.
    """
    from app.matters.services import add_entry

    add_entry(matter=world[OUT_2014_WITH_ENTRY], body="<p>Hilisem töö.</p>", author=specialist)

    candidate = candidate_for(world, OUT_2014_WITH_ENTRY)
    assert candidate.action == Action.REVIEW_REQUIRED
    assert candidate.review_reason == ReviewReason.AUTHORED_ENTRIES


def test_a_real_closure_on_an_out_of_scope_matter_is_left_alone(reviewed, world):
    before = world.refresh(OUT_2016_REAL_CLOSURE)
    assert before.disposition == Disposition.MONITORING_STOPPED
    assert before.closed_at is not None

    assert action_for(world, OUT_2016_REAL_CLOSURE) == Action.ALREADY_RETIRED

    apply_cutover_plan(build_cutover_plan(snapshot_sha256=FINAL_SNAPSHOT))
    after = world.refresh(OUT_2016_REAL_CLOSURE)
    assert after.disposition == before.disposition
    assert after.closed_at == before.closed_at
    assert after.record_mode == RecordMode.ARCHIVE


# ---------------------------------------------------------------------------
# The plan still covers the whole snapshot
# ---------------------------------------------------------------------------


def test_out_of_scope_rows_are_classified_not_dropped(reviewed, world):
    """A reconciliation that forgot history would look like a smaller success.

    Production's gate expects the examined count to be the whole snapshot, and
    `CurrentRegisterState` is supposed to describe every real row in it.
    """
    built = plan()
    titles = {c.observation.title for c in built.candidates}
    for title in (
        OUT_2018_BLANK,
        OUT_2019_STILL_OPEN,
        OUT_2023_LIVE,
        OUT_2024_LIVE,
        OUT_2016_REAL_CLOSURE,
        IN_2025_LIVE,
        IN_2026_LIVE,
    ):
        assert title in titles
    assert len(built.candidates) == 8


def test_state_is_rebuilt_for_history_too(reviewed, world):
    apply_cutover_plan(build_cutover_plan(snapshot_sha256=FINAL_SNAPSHOT))

    assert CurrentRegisterState.objects.count() == 8
    out_of_scope = CurrentRegisterState.objects.get(matter=world.matters[OUT_2018_BLANK])
    assert out_of_scope.currency == RegisterCurrency.RETIRED
    # Its own source status, preserved as the source wrote it — blank.
    assert out_of_scope.status_label == ""
    assert CurrentRegisterState.objects.filter(currency=RegisterCurrency.CURRENT).count() == 2


# ---------------------------------------------------------------------------
# The policy itself
# ---------------------------------------------------------------------------


def test_the_approved_snapshot_carries_the_maintained_years():
    """The real digest and the real scope, checked together.

    They are one decision. A digest approved without a scope is what produced
    the incident this file is named after.
    """
    policy = reviewed_snapshot("f38906c255f5ad6a58711ce833dd61da5fad7ce7ffd74fb8d2b057c6e8a58df2")
    assert policy is not None
    assert policy.current_years == frozenset({2025, 2026})


def test_every_reviewed_snapshot_carries_a_scope_and_a_date():
    """The guard that used to be ``len(REVIEWED_SNAPSHOTS) == 1``.

    Counting the list stopped being the right check the moment a second
    workbook was approved, and the count was never what the check was about: it
    was about a digest entering the list without somebody deciding what it
    speaks for. So the property is asserted of every entry instead, which is
    both what the incident actually requires and a guard that keeps working as
    the department approves further snapshots.

    The date is here for the same reason the scope is. A year-less
    ``JÄRGMISEKS`` date is read as the sheet's year only where the sheet year
    and the snapshot year agree, so a snapshot with no date silently turns that
    reading off — a quiet loss of conversions with no error anywhere
    (``register_next_actions.ParseContext``).
    """
    assert REVIEWED_SNAPSHOTS
    for snapshot in REVIEWED_SNAPSHOTS:
        assert snapshot.current_years == frozenset({2025, 2026}), snapshot.label
        assert snapshot.snapshot_date is not None, snapshot.label
        assert len(snapshot.sha256) == 64
        assert snapshot.label.strip()

    digests = [snapshot.sha256 for snapshot in REVIEWED_SNAPSHOTS]
    assert len(set(digests)) == len(digests)


def test_an_unreviewed_digest_has_no_scope_and_activates_nothing(world):
    """No fallback, and in particular not the approved snapshot's scope.

    The analysis still runs — an operator may look at an unknown workbook — and
    it truthfully reports that this snapshot makes nothing current.
    """
    assert reviewed_snapshot("0" * 64) is None

    built = plan()
    assert built.is_reviewed is False
    assert built.current_years == frozenset()
    assert built.current_after == []
    assert built.counts[Action.ACTIVATE] == 0


def test_applying_an_unreviewed_snapshot_is_refused(world):
    from app.legacy_import.final_cutover import UnreviewedSnapshot

    with pytest.raises(UnreviewedSnapshot):
        apply_cutover_plan(build_cutover_plan(snapshot_sha256=FINAL_SNAPSHOT))


def test_the_scope_is_not_reachable_from_the_command_line():
    """Portfolio scope is authority, not convenience.

    An operator who could pass `--years 2014` could turn a decade of history
    into current work without anybody reviewing it.
    """
    from app.legacy_import.management.commands.final_register_cutover import Command

    parser = Command().create_parser("manage.py", "final_register_cutover")
    flags = {action.dest for action in parser._actions}
    assert "years" not in flags
    assert "current_years" not in flags
    assert "scope" not in flags


@pytest.mark.parametrize(
    ("sheet", "expected"),
    [("2025", 2025), ("2011", 2011), ("", None), ("kokku", None), ("2026 ", 2026)],
)
def test_a_sheet_name_resolves_to_a_year_or_to_nothing(sheet, expected):
    """An unreadable sheet name is outside every scope, which is the safe way
    for it to be wrong."""
    assert sheet_year(sheet) == expected
    if expected is None:
        assert expected not in REVIEWED_YEARS
