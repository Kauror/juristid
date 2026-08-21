"""Owner resolution, and putting the answer back on Matters that lost it.

Two layers, tested separately because they fail differently. The resolver is a
pure decision about one string and a directory of people; the backfill is a
decision about one Matter given every source row behind it. The resolver being
right does not make the backfill safe, and the interesting bugs are in the
second: overwriting somebody's manual assignment, or picking a side when two
source rows disagree.
"""

from __future__ import annotations

import pytest

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.legacy_import.owner_backfill import (
    Outcome,
    apply_backfill_plan,
    build_backfill_plan,
    summary,
)
from app.legacy_import.parser import SOURCE_SYSTEM
from app.legacy_import.resolution import (
    METHOD_EXACT,
    METHOD_GIVEN_NAME,
    METHOD_MAPPING,
    KnownPeople,
    MappingFileError,
    MappingTables,
    resolve_owner,
)
from app.matters.models import Matter
from tests import factories
from tests.synthetic_portfolio import (
    CONFLICTED_OWNER,
    HISTORICAL,
    OWNED_CANDIDATE,
    SHARED_OWNER,
    UNASSIGNED,
    UNKNOWN_OWNER,
    build_portfolio,
)

pytestmark = pytest.mark.django_db


def people(*users) -> KnownPeople:
    return KnownPeople.of(list(users))


def resolve(value: str, *users, mappings: MappingTables | None = None):
    return resolve_owner(value, mappings or MappingTables.empty(), people(*users))


# =========================================================================
# The resolution hierarchy
# =========================================================================


def test_the_whole_display_name_resolves() -> None:
    alex = factories.UserFactory(display_name="Alex Example")
    resolution = resolve("Alex Example", alex)
    assert resolution.value == alex
    assert resolution.method == METHOD_EXACT


def test_the_whole_display_name_resolves_through_casing_and_spacing() -> None:
    """Normalisation may change spelling. It may not change identity."""
    alex = factories.UserFactory(display_name="Alex Example")
    assert resolve("  alex   EXAMPLE ", alex).value == alex


def test_a_lone_given_name_resolves_when_only_one_person_carries_it() -> None:
    """The bug this stage exists to fix.

    The register writes a first name; an account holds a full one. Comparing
    the two for equality meant the commonest shape in the column matched
    nothing at all.
    """
    alex = factories.UserFactory(display_name="Alex Example")
    maria = factories.UserFactory(display_name="Maria Example")
    resolution = resolve("Alex", alex, maria)
    assert resolution.value == alex
    assert resolution.method == METHOD_GIVEN_NAME


def test_two_people_whose_names_begin_alike_are_ambiguous() -> None:
    first = factories.UserFactory(display_name="Alex Example")
    second = factories.UserFactory(display_name="Alex Sample")
    resolution = resolve("Alex", first, second)
    assert resolution.value is None
    assert resolution.needs_mapping


def test_a_departed_colleague_still_resolves_in_history() -> None:
    """Historical ownership may legitimately name somebody who has left."""
    former = factories.UserFactory(display_name="Kadri Endine", is_active=False)
    resolution = resolve("Kadri", former)
    assert resolution.value == former
    assert resolution.method == METHOD_GIVEN_NAME


def test_a_departed_duplicate_makes_a_present_colleague_ambiguous() -> None:
    """Ambiguity is judged against everyone, not only the active half.

    Looking at active users alone would turn an unsafe match into a confident
    one, and quietly hand a departed colleague's decade of files to whoever
    happens to share their first name.
    """
    current = factories.UserFactory(display_name="Kadri Praegune")
    former = factories.UserFactory(display_name="Kadri Endine", is_active=False)
    assert resolve("Kadri", current, former).value is None


@pytest.mark.parametrize(
    "cell", ["Alex, Maria", "Alex / Maria", "Alex; Maria", "Alex ja Maria", "Alex & Maria"]
)
def test_a_cell_naming_two_people_is_never_resolved_to_one(cell: str) -> None:
    alex = factories.UserFactory(display_name="Alex Example")
    maria = factories.UserFactory(display_name="Maria Example")
    resolution = resolve(cell, alex, maria)
    assert resolution.value is None, "shared responsibility is not one person's work"
    assert resolution.needs_mapping


def test_an_unknown_name_resolves_to_nothing_and_creates_nobody() -> None:
    alex = factories.UserFactory(display_name="Alex Example")
    before = Matter.objects.count()
    assert resolve("Keegi Tundmatu", alex).value is None
    assert Matter.objects.count() == before


def test_a_reviewed_mapping_outranks_everything_including_a_shared_cell() -> None:
    alex = factories.UserFactory(display_name="Alex Example", upn="alex@example.invalid")
    maria = factories.UserFactory(display_name="Maria Example")
    tables = MappingTables(owners={"alex, maria": "alex@example.invalid"})
    resolution = resolve("Alex, Maria", alex, maria, mappings=tables)
    assert resolution.value == alex
    assert resolution.method == METHOD_MAPPING


def test_a_mapping_at_a_user_who_does_not_exist_fails_loudly() -> None:
    """A typo must fail, or it looks like the mapping simply had no effect."""
    alex = factories.UserFactory(display_name="Alex Example")
    tables = MappingTables(owners={"alex": "nobody@example.invalid"})
    with pytest.raises(MappingFileError):
        resolve("Alex", alex, mappings=tables)


def test_a_full_name_that_matches_nobody_is_not_decomposed_into_a_given_name() -> None:
    """``Sandra Teistmoodi`` is a different person, not a longer way of writing Sandra.

    The sharp case, and the reason this is worth its own test: the *first
    token* names somebody real. A resolver that fell back to given-name
    matching whenever the whole name missed would hand this row to Sandra
    Näidis, and the audit would record a confident `given_name` match with
    nothing to say it had guessed. A name with a space in it is a full name.
    """
    sandra = factories.UserFactory(display_name="Sandra Näidis")
    resolution = resolve("Sandra Teistmoodi", sandra)
    assert resolution.value is None
    assert resolution.needs_mapping


def test_an_inactive_person_is_not_offered_as_a_current_owner() -> None:
    """The other half of the departed-colleague rule.

    Resolvable in history, absent from the choices. Stage 2F is what makes
    this load-bearing: until the resolver started naming inactive users, no
    departed colleague could become an owner at all, so nothing tested that
    they stay out of the controls for handing out *new* work.

    Asserted against ``active_users()`` — the selector every owner control is
    built from — rather than against one form, because the rule belongs to
    this stage and which widget renders it belongs to another.
    """
    from app.matters.forms import active_users

    former = factories.UserFactory(display_name="Kadri Endine", is_active=False)
    current = factories.UserFactory(display_name="Ireen Näidis")

    offered = set(active_users())
    assert current in offered
    assert former not in offered
    # And still resolvable as a historical owner, which is the whole point.
    assert resolve("Kadri", former).value == former


# =========================================================================
# The backfill
# =========================================================================


def test_the_dry_run_classifies_every_matter_exactly_once() -> None:
    build_portfolio()
    plan = build_backfill_plan()
    assert sum(plan.counts.values()) == len(plan.plans)


def test_a_first_name_in_the_source_becomes_an_owner() -> None:
    portfolio = build_portfolio()
    assert portfolio.matter(OWNED_CANDIDATE).owner is None

    apply_backfill_plan(build_backfill_plan())

    assert portfolio.matter(OWNED_CANDIDATE).owner == portfolio.people.sandra


def test_a_blank_source_owner_leaves_the_matter_unassigned() -> None:
    portfolio = build_portfolio()
    apply_backfill_plan(build_backfill_plan())
    assert portfolio.matter(UNASSIGNED).owner is None


def test_a_shared_source_cell_assigns_nobody() -> None:
    portfolio = build_portfolio()
    plan = build_backfill_plan()
    apply_backfill_plan(plan)

    assert portfolio.matter(SHARED_OWNER).owner is None
    outcomes = {p.matter.title: p.outcome for p in plan.plans}
    assert outcomes[SHARED_OWNER] == Outcome.MULTI_PERSON


def test_an_unknown_source_name_assigns_nobody() -> None:
    portfolio = build_portfolio()
    plan = build_backfill_plan()
    apply_backfill_plan(plan)

    assert portfolio.matter(UNKNOWN_OWNER).owner is None
    outcomes = {p.matter.title: p.outcome for p in plan.plans}
    assert outcomes[UNKNOWN_OWNER] == Outcome.UNKNOWN_OWNER_VALUE


def test_two_source_rows_naming_two_people_assign_nobody() -> None:
    """Disagreement is not a tie to break.

    Taking the later row would be an inference dressed as a fact, and the
    department would have no way of telling which Matters were guessed at.
    """
    portfolio = build_portfolio()
    plan = build_backfill_plan()
    apply_backfill_plan(plan)

    assert portfolio.matter(CONFLICTED_OWNER).owner is None
    outcomes = {p.matter.title: p.outcome for p in plan.plans}
    assert outcomes[CONFLICTED_OWNER] == Outcome.CONFLICTING_SOURCES


def test_a_departed_colleague_is_restored_as_the_owner_of_their_archive() -> None:
    portfolio = build_portfolio()
    apply_backfill_plan(build_backfill_plan())
    assert portfolio.matter(HISTORICAL).owner == portfolio.people.former


def test_an_owner_set_by_a_person_is_never_overwritten() -> None:
    portfolio = build_portfolio()
    matter = portfolio.matter(OWNED_CANDIDATE)
    Matter.objects.filter(pk=matter.pk).update(owner=portfolio.people.martin)

    plan = build_backfill_plan()
    outcomes = {p.matter.title: p.outcome for p in plan.plans}
    assert outcomes[OWNED_CANDIDATE] == Outcome.ALREADY_OWNED

    apply_backfill_plan(plan)
    assert portfolio.matter(OWNED_CANDIDATE).owner == portfolio.people.martin


def test_running_it_twice_changes_nothing_the_second_time() -> None:
    build_portfolio()
    first = apply_backfill_plan(build_backfill_plan())
    assert first.assigned > 0

    second = apply_backfill_plan(build_backfill_plan())
    assert second.assigned == 0


def test_every_assignment_records_how_it_was_reached() -> None:
    """A reviewer months later must be able to tell an inference from a fact."""
    portfolio = build_portfolio()
    apply_backfill_plan(build_backfill_plan())

    event = ChangeEvent.objects.filter(
        matter=portfolio.matter(OWNED_CANDIDATE),
        event_type=ChangeEventType.MATTER_ASSIGNED,
    ).latest("created_at")

    assert event.payload["operation"] == "backfill_legacy_owners"
    assert event.payload["resolution_method"] == METHOD_GIVEN_NAME
    assert event.payload["source_eras"] == ["2026"]
    assert event.payload["source"], "the deciding source row must be identifiable"


def test_the_audit_payload_carries_no_source_cell_text() -> None:
    """Provenance points at the evidence; it does not copy it.

    The raw owner cell is register content and stays on the source reference,
    which is the table designed to hold it. Change events are read far more
    widely.
    """
    portfolio = build_portfolio()
    apply_backfill_plan(build_backfill_plan())

    event = ChangeEvent.objects.filter(
        matter=portfolio.matter(OWNED_CANDIDATE),
        event_type=ChangeEventType.MATTER_ASSIGNED,
    ).latest("created_at")
    assert "Sandra" not in str(event.payload.get("source", ""))


def test_the_summary_is_aggregate_only() -> None:
    """Safe to paste into a message or attach to a review."""
    build_portfolio()
    figures = summary(build_backfill_plan())

    rendered = str(figures)
    for forbidden in (OWNED_CANDIDATE, SHARED_OWNER, "Sandra", "Kadri"):
        assert forbidden not in rendered
    assert figures["would_update"] >= 1


def test_planning_the_backfill_writes_nothing() -> None:
    """``--dry-run`` is a promise, and the planner is what keeps it.

    Every other test here builds a plan and applies it in the same breath,
    which would not notice a planner that assigned as it went.
    """
    portfolio = build_portfolio()

    plan = build_backfill_plan()

    assert any(p.assigns for p in plan.plans), "the fixture must offer something to assign"
    assert portfolio.matter(OWNED_CANDIDATE).owner is None
    assert portfolio.matter(HISTORICAL).owner is None


def test_the_owner_column_is_read_through_the_era_contract_not_a_fixed_letter() -> None:
    """A value under the wrong letter for its era must resolve to nothing.

    ``VASTUTAJA`` is column H on the current sheet and the contract is what
    says so. Code that reached for a hard-coded letter — or that scanned the
    row for anything resembling a name — would find this and assign it.
    """
    matter = factories.MatterFactory(owner=None)
    factories.MatterSourceReferenceFactory(
        matter=matter,
        source_system=SOURCE_SYSTEM,
        source_sheet="2026",
        source_era="2026",
        source_row_raw={"ZZ": "Sandra"},
    )
    factories.UserFactory(display_name="Sandra Näidis")

    apply_backfill_plan(build_backfill_plan())

    matter.refresh_from_db()
    assert matter.owner is None


def test_a_matter_with_no_provenance_is_left_alone() -> None:
    """No source reference, no opinion. The backfill reads provenance only."""
    factories.UserFactory(display_name="Sandra Näidis")
    native = factories.MatterFactory(owner=None)

    plan = build_backfill_plan()
    apply_backfill_plan(plan)

    native.refresh_from_db()
    assert native.owner is None
    assert native.pk not in {p.matter.pk for p in plan.plans}
