"""Refreshing current work from a newer reviewed snapshot.

The parser is tested one sentence at a time in
``test_register_next_action_parser_20.py``. What is tested here is everything
the parser is not allowed to decide: whose work the register may move, what a
second snapshot does to the first one's output, and the four source facts that
have to survive the trip from a spreadsheet cell to a page a lawyer reads.

Two of those are the same mistake in different clothes and both are asserted
directly, because both were made once already somewhere in this codebase:

* a blank is not a zero, and
* "the cell holds something" is not "the cell holds a date".

Every title, name, institution and sentence below is invented. No Koda row
appears in a fixture (master specification 5.3, 23.5).
"""

from __future__ import annotations

import datetime as dt
import hashlib

import pytest
from django.utils import timezone

from app.audit.models import ChangeEvent
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.final_cutover import build_cutover_plan, rebuild_current_state
from app.legacy_import.models import OutreachChannel, RegisterEngagementImport
from app.legacy_import.next_action_enrichment import Outcome, action_ownership
from app.legacy_import.register_outreach import mapping_digest, read_campaigns, read_mapping
from app.legacy_import.register_refresh import (
    CATALOGUE_COMMAND,
    PlanChanged,
    SnapshotNotCatalogued,
    UnreviewedSnapshot,
    apply_refresh_plan,
    build_refresh_plan,
    catalogue_state,
    protected_rows,
    require_catalogued,
    summary,
)
from app.legacy_import.register_semantics import AddresseeCardinality, OpinionSentState
from app.matters.enums import RecordMode
from app.matters.models import Matter, MatterEngagement
from app.matters.services import create_imported_matter
from app.submissions.models import Submission
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import cancel_next_action, complete_next_action, set_next_action
from tests.synthetic_cutover import SNAPSHOT_DATE, approve_snapshot
from tests.synthetic_portfolio import (
    CURRENT_YEAR,
    MINISTRY_NAME,
    add_source_reference,
    build_people,
    build_register,
    snapshot_for,
)

pytestmark = pytest.mark.django_db

SNAPSHOT = snapshot_for("refresh-28-08")
OLDER_SNAPSHOT = snapshot_for("refresh-21-08")

#: Invented instructions, each one shaped like a rule under test.
REVIEW_SEPTEMBER = "Vaatan 07.09 sünteetilise eelnõu seisu üle."
REVIEW_OCTOBER = "Vaatan 07.10 sünteetilise eelnõu seisu üle."
WAIT_AND_REVIEW = "ootan valitsusele saatmist, vaata üle 15.09"
UNREADABLE = "Menetlus jätkub kuidagi"

LIVE_STATUS = "kooskõlastusringil"

#: The page's label for the register's own feedback observation, in full. The
#: engagement composer carries a placeholder that begins with the same two
#: words, so a shorter needle would match a page that rendered no counts.
FEEDBACK_LABEL = "Liikmete tagasiside · registri vaatlus"

#: The pilot window the command pins, restated here so the gate tests do not
#: depend on importing a management command.
WINDOW = (dt.date(2026, 1, 1), dt.date(2026, 8, 28))


def _campaign_row(
    *,
    template: str,
    url: str = "https://example.invalid/templates/aaaa-1111/html/",
    due: str = "2026-03-05 10:00:00",
) -> dict[str, str]:
    """One export row, with only the five columns that may be read."""
    return {
        "Section name": "Mida arvad sünteetilise pakendiseaduse muudatustest?",
        "Template name": template,
        "Template preview": url,
        "Due at": due,
        "Enqueues": "789",
    }


@pytest.fixture
def world(monkeypatch):
    """A minimal maintained register: people, a ministry, and one snapshot."""
    approve_snapshot(monkeypatch, sha256=SNAPSHOT)
    people = build_people()
    register = build_register(people)
    register.snapshot = SNAPSHOT
    return register


def add_matter(
    register,
    *,
    title: str,
    reference: int,
    year: int = CURRENT_YEAR,
    snapshot: str = SNAPSHOT,
    record_mode: str = RecordMode.FULL,
    **cells,
) -> Matter:
    """One imported Matter with one register row behind it."""
    matter = create_imported_matter(
        title=title,
        reference_year=year,
        reference_number=reference,
        record_mode=record_mode,
    )
    add_source_reference(
        register,
        matter,
        year=year,
        snapshot=snapshot,
        status_cell=cells.pop("status_cell", LIVE_STATUS),
        **cells,
    )
    return matter


def restate(register, matter: Matter, *, year: int = CURRENT_YEAR, **cells):
    """What a newer workbook does to a Matter that already has a row.

    It adds one, and it does not remove the old one. Source references are
    immutable evidence — ``CurrentRegisterState`` even PROTECTs them, so an
    attempt to delete one raises — and the reconciliation resolves a Matter to
    the *last* row the snapshot names it on. Modelling a refresh as a deletion
    would test a shape the architecture forbids.
    """
    return add_source_reference(
        register,
        matter,
        year=year,
        snapshot=SNAPSHOT,
        status_cell=cells.pop("status_cell", LIVE_STATUS),
        **cells,
    )


def state_for(matter: Matter) -> CurrentRegisterState:
    return CurrentRegisterState.objects.get(matter=matter)


def refresh(**kwargs):
    return build_refresh_plan(snapshot_sha256=SNAPSHOT, today=SNAPSHOT_DATE, **kwargs)


def outcome_for(plan, matter: Matter) -> str:
    return next(
        proposal.outcome
        for proposal in plan.next_actions.proposals
        if proposal.matter_id == matter.pk
    )


# ---------------------------------------------------------------------------
# The two feedback counts
# ---------------------------------------------------------------------------


def test_a_blank_feedback_cell_is_not_a_zero(world):
    """The distinction the whole feature turns on.

    A blank means nobody wrote the number down. A written zero means somebody
    measured and the answer was none. Stored as one value, no later run could
    ever tell them apart again — and the page would report 124 measurements as
    gaps, or 19 gaps as measured zeros.
    """
    measured = add_matter(
        world,
        title="Sünteetiline pakendieelnõu",
        reference=1,
        feedback_requested_cell="220",
        feedback_responded_cell="0",
    )
    unrecorded = add_matter(world, title="Sünteetiline energiaeelnõu", reference=2)

    rebuild_current_state(build_cutover_plan(snapshot_sha256=SNAPSHOT))

    assert state_for(measured).member_feedback_requested == 220
    assert state_for(measured).member_feedback_responded == 0
    assert state_for(unrecorded).member_feedback_requested is None
    assert state_for(unrecorded).member_feedback_responded is None


@pytest.mark.parametrize("year", [2025, 2026])
def test_both_maintained_sheets_keep_the_blank_zero_distinction(world, year):
    """The columns exist on both maintained sheets and mean the same thing."""
    measured = add_matter(
        world,
        title=f"Sünteetiline {year} eelnõu, mõõdetud",
        reference=10 + year,
        year=year,
        feedback_responded_cell="0",
    )
    blank = add_matter(
        world, title=f"Sünteetiline {year} eelnõu, kirjeta", reference=20 + year, year=year
    )

    rebuild_current_state(build_cutover_plan(snapshot_sha256=SNAPSHOT))

    assert state_for(measured).member_feedback_responded == 0
    assert state_for(blank).member_feedback_responded is None


def test_rebuilding_preserves_the_exact_counts(world):
    """Derived, so it must survive being thrown away and rebuilt."""
    matter = add_matter(
        world,
        title="Sünteetiline jäätmeeelnõu",
        reference=3,
        feedback_requested_cell="273",
        feedback_responded_cell="5",
    )

    for _ in range(2):
        rebuild_current_state(build_cutover_plan(snapshot_sha256=SNAPSHOT))

    assert state_for(matter).member_feedback_requested == 273
    assert state_for(matter).member_feedback_responded == 5


def test_a_newer_snapshot_moves_the_counts_and_can_empty_them(world, monkeypatch):
    """A refresh is a refresh: the newer workbook replaces the interpretation.

    Including downward, and including to nothing. If a later snapshot no longer
    records the number, the derived row must say *not recorded* rather than keep
    a figure the current source does not contain.
    """
    matter = add_matter(
        world, title="Sünteetiline kliimaeelnõu", reference=4, feedback_requested_cell="100"
    )
    rebuild_current_state(build_cutover_plan(snapshot_sha256=SNAPSHOT))
    assert state_for(matter).member_feedback_requested == 100

    restate(world, matter, feedback_requested_cell="0")
    rebuild_current_state(build_cutover_plan(snapshot_sha256=SNAPSHOT))
    assert state_for(matter).member_feedback_requested == 0

    restate(world, matter)
    rebuild_current_state(build_cutover_plan(snapshot_sha256=SNAPSHOT))
    assert state_for(matter).member_feedback_requested is None


def test_the_report_counts_populated_zero_and_blank_separately(world):
    add_matter(world, title="Sünteetiline A", reference=5, feedback_requested_cell="220")
    add_matter(world, title="Sünteetiline B", reference=6, feedback_requested_cell="0")
    add_matter(world, title="Sünteetiline C", reference=7)

    figures = summary(refresh())

    assert figures["member_feedback_requested"] == {
        "populated": 1,
        "explicit_zero": 1,
        "blank": 1,
    }


def test_nothing_divides_one_count_by_the_other(world):
    """No response rate exists, in the report or on the model.

    The register's own contract says the columns are not subsets of one
    another, and the real data holds rows where more members answered than were
    asked directly. A rate computed from them would be wrong and would look
    authoritative.
    """
    add_matter(
        world,
        title="Sünteetiline D",
        reference=8,
        feedback_requested_cell="4",
        feedback_responded_cell="9",
    )
    figures = summary(refresh())

    assert "rate" not in str(figures)
    assert not hasattr(CurrentRegisterState, "member_feedback_rate")


# ---------------------------------------------------------------------------
# VÄLJA
# ---------------------------------------------------------------------------


def test_ei_saatnud_is_not_rendered_as_a_sent_date(world):
    """A recorded decision, not an unreadable date and not an unfinished draft.

    ``opinion_sent_recorded`` is true — something *is* written — and that is
    what the drafting queryset asks. What the page needs is the third answer:
    the register says Koda did not send one.
    """
    matter = add_matter(
        world, title="Sünteetiline varjendieelnõu", reference=9, opinion_sent_cell="ei saatnud"
    )
    rebuild_current_state(build_cutover_plan(snapshot_sha256=SNAPSHOT))

    state = state_for(matter)
    assert state.opinion_sent_state == OpinionSentState.NOT_SENT
    assert state.opinion_not_sent is True
    assert state.opinion_sent_date is None
    assert state.opinion_sent_recorded is True


@pytest.mark.parametrize(
    ("cell", "expected", "recorded"),
    [
        ("10.03.2026", OpinionSentState.DATE, True),
        ("ei saatnud", OpinionSentState.NOT_SENT, True),
        ("saadetud koos teisega", OpinionSentState.RECORDED_OTHER, True),
        ("", OpinionSentState.BLANK, False),
    ],
)
def test_valja_is_read_as_four_answers(world, cell, expected, recorded):
    matter = add_matter(
        world, title=f"Sünteetiline eelnõu {expected}", reference=100, opinion_sent_cell=cell
    )
    rebuild_current_state(build_cutover_plan(snapshot_sha256=SNAPSHOT))

    state = state_for(matter)
    assert state.opinion_sent_state == expected
    assert state.opinion_sent_recorded is recorded


def test_a_valja_date_never_creates_a_submission(world):
    """DATA-001, restated where somebody would be tempted to break it.

    A sent opinion's canonical record needs immutable final evidence. A
    spreadsheet cell is not evidence, and a Submission created from one would be
    indistinguishable afterwards from a real one.
    """
    add_matter(
        world, title="Sünteetiline maksueelnõu", reference=11, opinion_sent_cell="10.03.2026"
    )
    plan = refresh()

    assert summary(plan)["submissions_created_from_valja"] == 0
    before = Submission.objects.count()
    apply_refresh_plan(plan, expect_plan_sha256=plan.digest)
    assert Submission.objects.count() == before


# ---------------------------------------------------------------------------
# KELLELE
# ---------------------------------------------------------------------------


def test_a_multi_addressee_cell_is_not_reduced_to_the_first_organisation(world):
    """Three ministries stay three, and the canonical field is left alone.

    Taking the first would record — with no trace of the choice — that Koda
    wrote to one body when it wrote to three.
    """
    matter = add_matter(
        world,
        title="Sünteetiline riigikaitse-eelnõu",
        reference=12,
        addressee_cell=f"{MINISTRY_NAME}, Näidisamet, Näidiskomisjon",
    )
    before = matter.addressee_organisation_id

    plan = refresh()
    apply_refresh_plan(plan, expect_plan_sha256=plan.digest)

    state = state_for(matter)
    assert state.addressee_cardinality == AddresseeCardinality.MULTIPLE
    assert state.addressees == (MINISTRY_NAME, "Näidisamet", "Näidiskomisjon")
    matter.refresh_from_db()
    assert matter.addressee_organisation_id == before


def test_a_ministry_name_containing_ja_is_one_organisation(world):
    """The trap the separator set exists to avoid.

    Estonian ministries are called *X- ja Y-ministeerium*. Splitting on ``ja``
    reads almost every single addressee in the real register as a pair and
    invents an organisation for each one.
    """
    matter = add_matter(
        world,
        title="Sünteetiline majanduseelnõu",
        reference=13,
        addressee_cell="Majandus- ja Näidisministeerium",
    )
    rebuild_current_state(build_cutover_plan(snapshot_sha256=SNAPSHOT))

    state = state_for(matter)
    assert state.addressee_cardinality == AddresseeCardinality.SINGLE
    assert state.addressees == ("Majandus- ja Näidisministeerium",)


def test_a_single_resolvable_addressee_does_update_the_canonical_field(world):
    """The other side of the rule, so it is visibly not a blanket refusal."""
    matter = add_matter(
        world, title="Sünteetiline energiaeelnõu II", reference=14, addressee_cell=MINISTRY_NAME
    )
    assert matter.addressee_organisation_id is None

    plan = refresh()
    apply_refresh_plan(plan, expect_plan_sha256=plan.digest)

    matter.refresh_from_db()
    assert matter.addressee_organisation is not None
    assert matter.addressee_organisation.name == MINISTRY_NAME


def test_an_unreadable_organisation_cannot_erase_a_known_one(world):
    """*Do not touch* and *set to nothing* are different instructions.

    A field the source cannot settle is absent from the refresh rather than
    present as ``None``, so a cell nobody can resolve leaves what somebody
    entered exactly where it was.
    """
    matter = add_matter(
        world, title="Sünteetiline tolliseadus", reference=15, addressee_cell=MINISTRY_NAME
    )
    plan = refresh()
    apply_refresh_plan(plan, expect_plan_sha256=plan.digest)
    matter.refresh_from_db()
    known = matter.addressee_organisation_id
    assert known is not None

    restate(
        world,
        matter,
        addressee_cell="Tundmatu asutus, mida keegi ei tunne",
    )
    later = refresh()
    apply_refresh_plan(later, expect_plan_sha256=later.digest)

    matter.refresh_from_db()
    assert matter.addressee_organisation_id == known


def test_an_unresolvable_owner_cannot_erase_a_known_one(world):
    """The same rule for VASTUTAJA, and the report says so instead."""
    people = world.people
    matter = add_matter(
        world, title="Sünteetiline tööseadus", reference=16, owner_cell=people.sandra.display_name
    )
    plan = refresh()
    apply_refresh_plan(plan, expect_plan_sha256=plan.digest)
    matter.refresh_from_db()
    assert matter.owner_id == people.sandra.pk

    restate(world, matter, owner_cell="Näidis")
    later = refresh()

    assert [item["value"] for item in summary(later)["unresolved_owners"]] == ["Näidis"]
    apply_refresh_plan(later, expect_plan_sha256=later.digest)
    matter.refresh_from_db()
    assert matter.owner_id == people.sandra.pk


def test_a_multi_person_owner_cell_names_nobody(world):
    """One person is never chosen out of a cell naming two.

    Assigning the file to whichever name came first would attribute somebody's
    work to a colleague, which is the failure the resolver refuses outright.
    """
    people = world.people
    add_matter(
        world,
        title="Sünteetiline kaubandusseadus",
        reference=17,
        owner_cell=f"{people.sandra.display_name}, {people.martin.display_name}",
    )
    figures = summary(refresh())

    assert figures["unresolved_owners"]
    assert figures["field_changes"]["owner"] == 0


# ---------------------------------------------------------------------------
# Whose next action is it
# ---------------------------------------------------------------------------


def test_an_untouched_imported_action_may_be_refreshed(world):
    """Case 19B, and the reason the operation is repeatable at all.

    Nobody has touched what the register wrote, and the newer workbook says
    something different. The action moves, and the one it replaces stays in the
    history as a superseded row.
    """
    matter = add_matter(
        world, title="Sünteetiline ehitusseadus", reference=18, next_action_cell=REVIEW_SEPTEMBER
    )
    first = refresh()
    apply_refresh_plan(first, expect_plan_sha256=first.digest)

    action = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert action.target_date == dt.date(2026, 9, 7)

    restate(
        world,
        matter,
        next_action_cell=REVIEW_OCTOBER,
    )
    later = refresh()
    assert outcome_for(later, matter) == Outcome.REFRESH_IMPORTED

    apply_refresh_plan(later, expect_plan_sha256=later.digest)
    moved = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert moved.target_date == dt.date(2026, 10, 7)
    assert NextAction.objects.filter(matter=matter, status=ActionStatus.SUPERSEDED).count() == 1


def test_an_identical_reading_writes_nothing(world):
    """Re-run safety, and it is a comparison rather than a flag.

    Same snapshot, same database, applied twice: no canonical change, no second
    action, no second audit event.
    """
    matter = add_matter(
        world, title="Sünteetiline veeseadus", reference=19, next_action_cell=REVIEW_SEPTEMBER
    )
    first = refresh()
    apply_refresh_plan(first, expect_plan_sha256=first.digest)
    action = NextAction.objects.get(matter=matter)

    second = refresh()
    assert outcome_for(second, matter) == Outcome.IMPORTED_UP_TO_DATE
    assert second.next_actions.writing == ()

    result = apply_refresh_plan(second, expect_plan_sha256=second.digest)
    assert result.actions_created == 0
    assert result.actions_refreshed == 0
    assert result.actions_withdrawn == 0
    assert NextAction.objects.filter(matter=matter).count() == 1
    assert NextAction.objects.get(pk=action.pk).updated_at == action.updated_at


def test_a_person_who_completed_an_imported_action_wins(world):
    """Case 19C. Completing it is a decision, whoever wrote it originally."""
    matter = add_matter(
        world, title="Sünteetiline metsaseadus", reference=20, next_action_cell=REVIEW_SEPTEMBER
    )
    first = refresh()
    apply_refresh_plan(first, expect_plan_sha256=first.digest)

    action = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    complete_next_action(action=action, actor=world.people.sandra)

    restate(
        world,
        matter,
        next_action_cell=REVIEW_OCTOBER,
    )
    later = refresh()

    assert outcome_for(later, matter) == Outcome.HUMAN_WINS
    apply_refresh_plan(later, expect_plan_sha256=later.digest)
    assert NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).count() == 0


def test_a_person_who_replaced_an_imported_action_wins(world):
    """Superseding by hand is a decision too, and the register does not undo it."""
    matter = add_matter(
        world, title="Sünteetiline kalandusseadus", reference=21, next_action_cell=REVIEW_SEPTEMBER
    )
    first = refresh()
    apply_refresh_plan(first, expect_plan_sha256=first.digest)

    set_next_action(
        matter=matter,
        text="Inimese enda otsus",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=dt.date(2027, 1, 1),
        actor=world.people.sandra,
    )

    later = refresh()
    assert outcome_for(later, matter) == Outcome.HUMAN_WINS

    apply_refresh_plan(later, expect_plan_sha256=later.digest)
    kept = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert kept.text == "Inimese enda otsus"


def test_a_hand_made_action_is_never_touched(world):
    """The original protection, unchanged: no provenance, no refresh."""
    matter = add_matter(
        world, title="Sünteetiline sadamaseadus", reference=22, next_action_cell=REVIEW_SEPTEMBER
    )
    set_next_action(
        matter=matter,
        text="Käsitsi kirjutatud samm",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=dt.date(2027, 3, 1),
        actor=world.people.martin,
    )

    plan = refresh()
    assert outcome_for(plan, matter) == Outcome.HUMAN_WINS

    apply_refresh_plan(plan, expect_plan_sha256=plan.digest)
    assert NextAction.objects.filter(matter=matter).count() == 1


def test_an_emptied_instruction_withdraws_the_action_it_produced(world):
    """Case 19D. A machine instruction with no source behind it is withdrawn.

    Leaving it would put a date on somebody's list that nothing in the register
    supports and nothing could ever take off again.
    """
    matter = add_matter(
        world, title="Sünteetiline haldusseadus", reference=23, next_action_cell=REVIEW_SEPTEMBER
    )
    first = refresh()
    apply_refresh_plan(first, expect_plan_sha256=first.digest)
    assert NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).exists()

    restate(world, matter)
    later = refresh()
    assert outcome_for(later, matter) == Outcome.REMOVE_STALE_IMPORTED

    result = apply_refresh_plan(later, expect_plan_sha256=later.digest)
    assert result.actions_withdrawn == 1
    assert not NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).exists()
    assert NextAction.objects.filter(matter=matter, status=ActionStatus.CANCELLED).count() == 1


def test_an_instruction_that_stops_being_readable_withdraws_it_too(world):
    matter = add_matter(
        world, title="Sünteetiline ringlusseadus", reference=24, next_action_cell=REVIEW_SEPTEMBER
    )
    first = refresh()
    apply_refresh_plan(first, expect_plan_sha256=first.digest)

    restate(world, matter, next_action_cell=UNREADABLE)
    later = refresh()

    assert outcome_for(later, matter) == Outcome.REMOVE_STALE_IMPORTED


def test_a_withdrawal_is_not_mistaken_for_a_persons_decision(world):
    """The cancellation this operation performs must stay its own.

    ``cancel_next_action`` with no actor and machine provenance leaves the
    Matter refreshable; a later snapshot that speaks again may still speak. A
    status-based ownership test would have read the withdrawal as human work and
    frozen the file permanently.
    """
    matter = add_matter(
        world, title="Sünteetiline postiseadus", reference=25, next_action_cell=REVIEW_SEPTEMBER
    )
    first = refresh()
    apply_refresh_plan(first, expect_plan_sha256=first.digest)

    restate(world, matter)
    second = refresh()
    apply_refresh_plan(second, expect_plan_sha256=second.digest)

    ownership = action_ownership([matter.pk])[matter.pk]
    assert ownership.human_touched is False

    restate(
        world,
        matter,
        next_action_cell=REVIEW_OCTOBER,
    )
    third = refresh()
    assert outcome_for(third, matter) == Outcome.AUTO


def test_a_person_who_cancelled_an_imported_action_wins(world):
    """The same shape with an actor, and the opposite answer."""
    matter = add_matter(
        world, title="Sünteetiline raudteeseadus", reference=26, next_action_cell=REVIEW_SEPTEMBER
    )
    first = refresh()
    apply_refresh_plan(first, expect_plan_sha256=first.digest)

    action = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    cancel_next_action(action=action, actor=world.people.sandra, reason="Ei ole enam asjakohane")

    restate(
        world,
        matter,
        next_action_cell=REVIEW_OCTOBER,
    )
    later = refresh()

    assert outcome_for(later, matter) == Outcome.HUMAN_WINS


def test_the_wait_and_review_pair_becomes_one_action(world):
    """End to end, because the parser alone cannot prove it reaches the file."""
    matter = add_matter(
        world, title="Sünteetiline pensioniseadus", reference=27, next_action_cell=WAIT_AND_REVIEW
    )
    plan = refresh()
    apply_refresh_plan(plan, expect_plan_sha256=plan.digest)

    action = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert action.kind == ActionKind.WAIT
    assert action.date_semantics == DateSemantics.REVIEW_ON
    assert action.target_date == dt.date(2026, 9, 15)
    assert action.source_text == WAIT_AND_REVIEW


def test_the_register_sentence_is_never_rewritten(world):
    """Two claims, and the first stays true whatever happens to the second."""
    matter = add_matter(
        world, title="Sünteetiline apteegiseadus", reference=28, next_action_cell=REVIEW_SEPTEMBER
    )
    plan = refresh()
    apply_refresh_plan(plan, expect_plan_sha256=plan.digest)

    assert state_for(matter).next_action_text == REVIEW_SEPTEMBER


# ---------------------------------------------------------------------------
# The plan, the digest and the gate
# ---------------------------------------------------------------------------


def test_planning_writes_nothing(world):
    add_matter(
        world, title="Sünteetiline ravimiseadus", reference=29, next_action_cell=REVIEW_SEPTEMBER
    )
    plan = refresh()
    summary(plan)
    protected_rows(plan)

    assert CurrentRegisterState.objects.count() == 0
    assert NextAction.objects.count() == 0
    # The projection exists and is unsaved: that is what makes a complete
    # report available before anything has been written.
    assert plan.projected
    assert not CurrentRegisterState.objects.filter(
        pk__in=[row.pk for row in plan.projected]
    ).exists()


def test_the_digest_is_deterministic_and_moves_with_the_work(world):
    add_matter(
        world, title="Sünteetiline turvaseadus", reference=30, next_action_cell=REVIEW_SEPTEMBER
    )
    assert refresh().digest == refresh().digest

    before = refresh().digest
    add_matter(
        world, title="Sünteetiline lennuseadus", reference=31, next_action_cell=REVIEW_OCTOBER
    )
    assert refresh().digest != before


def test_applying_the_wrong_digest_writes_nothing(world):
    add_matter(
        world,
        title="Sünteetiline kindlustusseadus",
        reference=32,
        next_action_cell=REVIEW_SEPTEMBER,
    )
    plan = refresh()

    with pytest.raises(PlanChanged):
        apply_refresh_plan(plan, expect_plan_sha256="0" * 64)

    assert NextAction.objects.count() == 0
    assert CurrentRegisterState.objects.count() == 0


def test_a_database_that_moved_after_the_plan_refuses_the_apply(world):
    """The plan is a photograph, and the apply re-takes it before writing."""
    add_matter(
        world, title="Sünteetiline pangaseadus", reference=33, next_action_cell=REVIEW_SEPTEMBER
    )
    plan = refresh()
    digest = plan.digest

    add_matter(
        world, title="Sünteetiline väärtpaberiseadus", reference=34, next_action_cell=REVIEW_OCTOBER
    )

    with pytest.raises(PlanChanged):
        apply_refresh_plan(plan, expect_plan_sha256=digest)
    assert NextAction.objects.count() == 0


def test_an_unreviewed_snapshot_cannot_be_applied(world, monkeypatch):
    add_matter(world, title="Sünteetiline riigihankeseadus", reference=35)

    approve_snapshot(monkeypatch, sha256=OLDER_SNAPSHOT)
    unreviewed = build_refresh_plan(snapshot_sha256=SNAPSHOT, today=SNAPSHOT_DATE)

    assert unreviewed.is_reviewed is False
    with pytest.raises(UnreviewedSnapshot):
        apply_refresh_plan(unreviewed, expect_plan_sha256=unreviewed.digest)


def test_the_report_carries_no_register_prose(world):
    """A report is a file somebody may e-mail. The instruction is not in it."""
    add_matter(
        world,
        title="Sünteetiline salajane eelnõu",
        reference=36,
        next_action_cell=REVIEW_SEPTEMBER,
        status_cell=LIVE_STATUS,
    )
    rendered = str(summary(refresh()))

    assert REVIEW_SEPTEMBER not in rendered
    assert "Sünteetiline salajane eelnõu" not in rendered

    for row in protected_rows(refresh()):
        assert REVIEW_SEPTEMBER not in str(row)
        assert "Sünteetiline salajane eelnõu" not in str(row)


def test_the_2025_sheet_keeps_its_yearless_dates_out_of_the_work_queue(world):
    """The stricter rule, proved where it actually matters.

    The same sentence on the two maintained sheets: read on 2026, refused on
    2025. Not because 2025 is old, but because the sheet and the snapshot
    disagree and nothing else settles the year.
    """
    current = add_matter(
        world,
        title="Sünteetiline 2026 eelnõu",
        reference=37,
        year=2026,
        next_action_cell=REVIEW_SEPTEMBER,
    )
    carried = add_matter(
        world,
        title="Sünteetiline 2025 eelnõu",
        reference=38,
        year=2025,
        next_action_cell=REVIEW_SEPTEMBER,
    )
    plan = refresh()

    assert outcome_for(plan, current) == Outcome.AUTO
    assert outcome_for(plan, carried) == Outcome.REVIEW_REQUIRED


def test_the_scope_stays_the_two_maintained_years(world):
    """2024 and earlier are history, and this operation does not move that."""
    figures = summary(refresh())
    assert figures["current_scope_years"] == [2025, 2026]


# ---------------------------------------------------------------------------
# The Matter page
# ---------------------------------------------------------------------------


def signed_in_page(client, user, matter) -> str:
    from django.urls import reverse

    client.force_login(user)
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def test_the_page_shows_both_counts_and_says_which_is_which(world, client):
    """The line the product owner asked for, in the register's own terms.

    Labelled as the register's observation rather than as live analytics: a
    campaign's recipient count is a different population and belongs on the
    campaign row.
    """
    matter = add_matter(
        world,
        title="Sünteetiline kaasamise eelnõu",
        reference=40,
        feedback_requested_cell="273",
        feedback_responded_cell="5",
    )
    plan = refresh()
    apply_refresh_plan(plan, expect_plan_sha256=plan.digest)

    body = signed_in_page(client, world.people.sandra, matter)

    # The full label, not just its first two words: the engagement composer's
    # own placeholder text reads "Näiteks: Liikmete tagasiside küsimine", and a
    # looser assertion would pass on a page that renders nothing at all.
    assert FEEDBACK_LABEL in body
    assert "otse küsitud 273" in body
    assert "vastas 5" in body


def test_a_measured_zero_reads_as_zero_and_a_blank_reads_as_teadmata(world, client):
    """The rendering failure a template filter would have produced silently.

    ``{{ value|default:"—" }}`` renders a measured zero as a missing one,
    because ``0`` is falsy. That is the whole reason the two values are decided
    in Python before the template sees them.
    """
    measured = add_matter(
        world,
        title="Sünteetiline nullvastuse eelnõu",
        reference=41,
        feedback_requested_cell="220",
        feedback_responded_cell="0",
    )
    partial = add_matter(
        world,
        title="Sünteetiline poolik eelnõu",
        reference=42,
        feedback_requested_cell="336",
    )
    plan = refresh()
    apply_refresh_plan(plan, expect_plan_sha256=plan.digest)

    body = signed_in_page(client, world.people.sandra, measured)
    assert "otse küsitud 220" in body
    assert "vastas 0" in body
    assert "teadmata" not in body

    body = signed_in_page(client, world.people.sandra, partial)
    assert "otse küsitud 336" in body
    assert "vastas teadmata" in body


def test_a_matter_with_no_counts_shows_no_feedback_line(world, client):
    """Absence is not a zero either, and it is not a row saying so."""
    matter = add_matter(world, title="Sünteetiline vaikne eelnõu", reference=43)
    plan = refresh()
    apply_refresh_plan(plan, expect_plan_sha256=plan.digest)

    body = signed_in_page(client, world.people.sandra, matter)
    assert FEEDBACK_LABEL not in body


def test_the_page_says_ei_saatnud_in_words(world, client):
    matter = add_matter(
        world,
        title="Sünteetiline saatmata eelnõu",
        reference=44,
        opinion_sent_cell="ei saatnud",
    )
    plan = refresh()
    apply_refresh_plan(plan, expect_plan_sha256=plan.digest)

    body = signed_in_page(client, world.people.sandra, matter)
    assert "ei saatnud" in body


def test_the_page_lists_every_addressee_the_register_named(world, client):
    matter = add_matter(
        world,
        title="Sünteetiline mitme adressaadiga eelnõu",
        reference=45,
        addressee_cell=f"{MINISTRY_NAME}, Näidisamet",
    )
    plan = refresh()
    apply_refresh_plan(plan, expect_plan_sha256=plan.digest)

    body = signed_in_page(client, world.people.sandra, matter)
    assert "Adressaadid registris" in body
    assert MINISTRY_NAME in body
    assert "Näidisamet" in body


def test_a_continuation_reference_can_be_followed(world, client):
    """Followable when this database holds the Matter, plain text when not.

    No canonical relationship is created either way; the reference is read at
    render time from the derived state, exactly as the register wrote it.
    """
    successor = add_matter(world, title="Sünteetiline järgnev eelnõu", reference=46)
    predecessor = add_matter(
        world,
        title="Sünteetiline eelnev eelnõu",
        reference=47,
        next_action_cell=f"Jätkub teema {successor.display_reference} all.",
    )
    plan = refresh()
    apply_refresh_plan(plan, expect_plan_sha256=plan.digest)

    body = signed_in_page(client, world.people.sandra, predecessor)
    assert "Registri järgi jätkub" in body
    assert str(successor.pk) in body


def test_the_structured_action_reaches_the_page(world, client):
    """End to end: the register sentence becomes work a lawyer can see."""
    matter = add_matter(
        world,
        title="Sünteetiline järgmiseks eelnõu",
        reference=48,
        next_action_cell=WAIT_AND_REVIEW,
    )
    plan = refresh()
    apply_refresh_plan(plan, expect_plan_sha256=plan.digest)

    body = signed_in_page(client, world.people.sandra, matter)
    assert WAIT_AND_REVIEW in body


# ---------------------------------------------------------------------------
# Query shape
# ---------------------------------------------------------------------------


def test_ownership_costs_two_queries_however_many_matters(world, django_assert_num_queries):
    """A batch operation, so the precedence question is asked once for the set.

    Two queries: the actions, and the audit events about them. Not one per
    Matter — the register carries four hundred rows and a per-row lookup is how
    a five-second command becomes a five-minute one (brief 38).
    """
    matters = [
        add_matter(
            world,
            title=f"Sünteetiline mahueelnõu {index}",
            reference=200 + index,
            next_action_cell=REVIEW_SEPTEMBER,
        )
        for index in range(6)
    ]
    plan = refresh()
    apply_refresh_plan(plan, expect_plan_sha256=plan.digest)

    with django_assert_num_queries(2):
        ownership = action_ownership([matter.pk for matter in matters])

    assert len(ownership) == 6
    assert all(not own.human_touched for own in ownership.values())


def test_ownership_asks_nothing_when_there_is_nothing_to_ask(world, django_assert_num_queries):
    """An empty set is not a query, and a set with no actions is one.

    The second is the ordinary case on a first run: every Matter is new, so the
    events query has nothing to look for and is not made.
    """
    with django_assert_num_queries(0):
        assert action_ownership([]) == {}

    matter = add_matter(world, title="Sünteetiline uus eelnõu", reference=210)
    with django_assert_num_queries(1):
        assert action_ownership([matter.pk]) == {}


def test_a_row_the_newer_workbook_dropped_does_not_block_the_apply(world):
    """A Matter the previous workbook named and this one does not.

    Its derived row legitimately keeps the older digest, so the table carries
    two. The standalone enrichment command fails closed on that — correctly,
    because it cannot tell which workbook it would be speaking for. Inside a
    refresh the question is already settled by the plan digest, so the rows are
    read back for *this* snapshot and passed in, and a workbook is not made
    unusable for having lost a line.
    """
    kept = add_matter(
        world, title="Sünteetiline alles eelnõu", reference=60, next_action_cell=REVIEW_SEPTEMBER
    )
    dropped = add_matter(world, title="Sünteetiline kadunud eelnõu", reference=61)

    # A state row from a workbook this refresh does not mention.
    CurrentRegisterState.objects.create(
        matter=dropped,
        source_reference=dropped.source_references.first(),
        source_snapshot_sha256=OLDER_SNAPSHOT,
        source_sheet="2026",
        source_row_number=999,
        currency=RegisterCurrency.CURRENT,
        observed_at=timezone.now(),
    )

    plan = refresh()
    result = apply_refresh_plan(plan, expect_plan_sha256=plan.digest)

    assert result.actions_created == 1
    assert NextAction.objects.filter(matter=kept, status=ActionStatus.OPEN).exists()
    assert (
        CurrentRegisterState.objects.values_list("source_snapshot_sha256", flat=True)
        .distinct()
        .count()
        == 1
    )


# ---------------------------------------------------------------------------
# The gates an apply has to pass
# ---------------------------------------------------------------------------


def test_a_reviewed_workbook_nobody_catalogued_is_refused(world):
    """A reviewed digest is not evidence that anybody imported the rows.

    The reconciliation reads ``MatterSourceReference`` and nothing else, so a
    forgotten catalogue step used to produce a plan reporting zero changes
    everywhere — which reads as "the newer workbook changes nothing" and is the
    one wrong answer an operator would believe, because it is the answer they
    were hoping for.
    """
    # Nothing catalogued for this snapshot at all.
    with pytest.raises(SnapshotNotCatalogued) as refusal:
        require_catalogued(SNAPSHOT)

    message = str(refusal.value)
    assert "not catalogued" in message
    assert CATALOGUE_COMMAND in message

    state = catalogue_state(SNAPSHOT)
    assert state.is_catalogued is False
    assert state.references == 0


def test_a_padding_row_is_not_a_catalogue(world):
    """A pre-numbered row with no title never became work.

    The importer refuses to create a Matter from one, so a snapshot catalogued
    as nothing but padding has nothing to reconcile — and counting references
    rather than *rows* would have called that catalogued.

    Built as a padding row rather than made into one: a database trigger holds
    imported source values immutable, which is the right answer and means a
    fixture has to write the shape it wants at creation.
    """
    add_matter(world, title="Sünteetiline reaalne rida", reference=70, title_cell="")

    state = catalogue_state(SNAPSHOT)
    assert state.references == 1
    assert state.real_rows == 0

    with pytest.raises(SnapshotNotCatalogued):
        require_catalogued(SNAPSHOT)


def test_a_catalogued_snapshot_passes_the_prerequisite(world):
    add_matter(world, title="Sünteetiline kataloogitud rida", reference=71)

    state = require_catalogued(SNAPSHOT)
    assert state.is_catalogued is True
    assert state.real_rows == 1


def test_the_apply_refuses_a_snapshot_that_was_never_catalogued(world, monkeypatch):
    """The gate is on the apply as well as the command.

    A caller reaching the service directly must meet the same prerequisite; a
    check that lived only in the management command would be advice rather than
    a rule.
    """
    matter = add_matter(world, title="Sünteetiline rida", reference=72)
    plan = refresh()

    # The catalogue is withdrawn between planning and applying. Nothing has been
    # written yet, so no derived row protects the evidence and it can go.
    matter.source_references.all().delete()

    with pytest.raises(SnapshotNotCatalogued):
        apply_refresh_plan(plan, expect_plan_sha256=plan.digest)


def test_a_campaign_export_that_moved_between_plan_and_apply_is_refused(world):
    """The campaign set is a hard pin, not a reported figure.

    The export decides which candidates an operator reviewed, so applying
    against a different set is approving one plan and performing another — and
    the mapping digest cannot catch it, because a reviewed mapping is a list of
    links and says nothing about the candidates it was chosen from.
    """
    add_matter(
        world,
        title="Sünteetiline kampaania eelnõu",
        reference=73,
        owner_cell="Sandra",
        received_cell="16.02.2026",
        deadline_cell="10.03.2026",
    )

    first = read_campaigns(
        [_campaign_row(template="pakendid 05.03.26 Sandra")], since=WINDOW[0], until=WINDOW[1]
    )[0]
    planned = build_refresh_plan(
        snapshot_sha256=SNAPSHOT,
        today=SNAPSHOT_DATE,
        campaigns=first,
        campaign_window=WINDOW,
    )
    approved = planned.digest

    # The operator re-exports and a further campaign has appeared in the window.
    second = read_campaigns(
        [
            _campaign_row(template="pakendid 05.03.26 Sandra"),
            _campaign_row(
                template="varjendid 02.03.26 Sandra",
                url="https://example.invalid/templates/cccc-3333/html/",
                due="2026-03-02 09:00:00",
            ),
        ],
        since=WINDOW[0],
        until=WINDOW[1],
    )[0]
    later = build_refresh_plan(
        snapshot_sha256=SNAPSHOT,
        today=SNAPSHOT_DATE,
        campaigns=second,
        campaign_window=WINDOW,
    )

    assert later.digest != approved
    with pytest.raises(PlanChanged):
        apply_refresh_plan(later, expect_plan_sha256=approved)
    assert NextAction.objects.count() == 0


def test_a_plan_approved_with_campaigns_cannot_be_applied_without_them(world):
    """And the reverse. "Planned with campaigns" is a different plan."""
    add_matter(
        world,
        title="Sünteetiline kampaania eelnõu II",
        reference=74,
        owner_cell="Sandra",
        received_cell="16.02.2026",
        deadline_cell="10.03.2026",
    )
    campaigns = read_campaigns(
        [_campaign_row(template="pakendid 05.03.26 Sandra")], since=WINDOW[0], until=WINDOW[1]
    )[0]

    with_campaigns = build_refresh_plan(
        snapshot_sha256=SNAPSHOT,
        today=SNAPSHOT_DATE,
        campaigns=campaigns,
        campaign_window=WINDOW,
    )
    without = build_refresh_plan(snapshot_sha256=SNAPSHOT, today=SNAPSHOT_DATE)

    assert with_campaigns.digest != without.digest
    assert with_campaigns.campaign_set_sha256
    assert without.campaign_set_sha256 == ""

    with pytest.raises(PlanChanged):
        apply_refresh_plan(without, expect_plan_sha256=with_campaigns.digest)


def test_a_reviewed_mapping_cannot_stand_in_for_the_plan_digest(world):
    """The mapping authorises links; it does not authorise the refresh.

    Supplying a perfectly valid mapping alongside a plan digest that no longer
    describes the database must still refuse everything, engagements included.
    """
    matter = add_matter(world, title="Sünteetiline vastefaili eelnõu", reference=75)
    plan = refresh()

    links = read_mapping(
        [
            {
                "reference": matter.display_reference,
                "channel": OutreachChannel.EMAIL_CAMPAIGN,
                "source_key": "https://example.invalid/templates/dddd-4444/html/",
                "title": "Sünteetiline kiri",
            }
        ]
    )

    with pytest.raises(PlanChanged):
        apply_refresh_plan(
            plan,
            expect_plan_sha256="0" * 64,
            links=links,
            expect_mapping_sha256=mapping_digest(links),
        )

    assert MatterEngagement.objects.count() == 0
    assert RegisterEngagementImport.objects.count() == 0


def test_a_second_identical_apply_changes_nothing_including_engagements(world):
    """Whole-refresh idempotency, engagements and audit rows included."""
    matter = add_matter(
        world,
        title="Sünteetiline korduse eelnõu",
        reference=76,
        next_action_cell=REVIEW_SEPTEMBER,
    )
    links = read_mapping(
        [
            {
                "reference": matter.display_reference,
                "channel": OutreachChannel.EMAIL_CAMPAIGN,
                "source_key": "https://example.invalid/templates/eeee-5555/html/",
                "title": "Sünteetiline kiri liikmetele",
                "occurred_on": "2026-03-05",
            }
        ]
    )
    digest = mapping_digest(links)

    first = refresh()
    apply_refresh_plan(
        first, expect_plan_sha256=first.digest, links=links, expect_mapping_sha256=digest
    )

    def census():
        return (
            NextAction.objects.count(),
            MatterEngagement.objects.count(),
            RegisterEngagementImport.objects.count(),
            ChangeEvent.objects.count(),
            CurrentRegisterState.objects.count(),
        )

    after_first = census()

    second = refresh()
    result = apply_refresh_plan(
        second, expect_plan_sha256=second.digest, links=links, expect_mapping_sha256=digest
    )

    assert result.actions_created == 0
    assert result.actions_refreshed == 0
    assert result.actions_withdrawn == 0
    assert result.engagements_created == 0
    assert result.engagements_updated == 0
    assert census() == after_first


def test_the_command_refuses_an_uncatalogued_workbook(world, tmp_path, monkeypatch):
    """The gate reaches the operator, not just the service.

    A refusal only a caller of ``apply_refresh_plan`` could hit would be advice:
    the person who forgets the catalogue step is the person running the command,
    and what they see is a report. This asserts they see an error instead.
    """
    from django.core.management import CommandError, call_command

    from app.legacy_import.final_cutover import ReviewedSnapshot

    workbook = tmp_path / "synthetic-register.xlsx"
    workbook.write_bytes(b"not a workbook, and never opened: only hashed")
    digest = hashlib.sha256(workbook.read_bytes()).hexdigest()

    monkeypatch.setattr(
        "app.legacy_import.final_cutover.REVIEWED_SNAPSHOTS",
        (
            ReviewedSnapshot(
                sha256=digest,
                label="sünteetiline kataloogimata",
                current_years=frozenset({2025, 2026}),
                snapshot_date=SNAPSHOT_DATE,
            ),
        ),
    )

    with pytest.raises(CommandError) as refusal:
        call_command("refresh_current_register", "plan", "--workbook", str(workbook))

    message = str(refusal.value)
    assert "not catalogued" in message
    assert CATALOGUE_COMMAND in message


def test_the_command_renders_a_full_report_with_campaigns(world, tmp_path, monkeypatch, capsys):
    """The operator's own code path, end to end, including the outreach block.

    The report reads keys out of the summary dictionaries by name, and a renamed
    key there is a ``KeyError`` nothing else would catch — the service tests call
    ``summary()`` and never the printer. This runs the command the way an
    operator does and asserts the two campaign digests are told apart on screen.
    """
    from django.core.management import call_command

    from app.legacy_import.final_cutover import ReviewedSnapshot

    add_matter(
        world,
        title="Sünteetiline aruande eelnõu",
        reference=80,
        owner_cell="Sandra",
        received_cell="16.02.2026",
        deadline_cell="10.03.2026",
        next_action_cell=REVIEW_SEPTEMBER,
        feedback_requested_cell="220",
        feedback_responded_cell="0",
    )

    workbook = tmp_path / "synthetic-register.xlsx"
    workbook.write_bytes(b"hashed, never opened: the catalogue is what is read")
    digest = hashlib.sha256(workbook.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "app.legacy_import.final_cutover.REVIEWED_SNAPSHOTS",
        (
            ReviewedSnapshot(
                sha256=digest,
                label="sünteetiline aruanne",
                current_years=frozenset({2025, 2026}),
                snapshot_date=SNAPSHOT_DATE,
            ),
        ),
    )
    # The catalogue this refresh reads back carries the workbook's own digest.
    matter = Matter.objects.get(reference_number=80)
    add_source_reference(
        world,
        matter,
        snapshot=digest,
        status_cell=LIVE_STATUS,
        owner_cell="Sandra",
        received_cell="16.02.2026",
        deadline_cell="10.03.2026",
        next_action_cell=REVIEW_SEPTEMBER,
    )

    export = tmp_path / "campaigns.csv"
    export.write_text(
        "\n".join(
            [
                '"Section name";"Template name";"Template preview";"Due at";"Enqueues"',
                ";".join(
                    [
                        '"Mida arvad sünteetilise pakendiseaduse muudatustest?"',
                        '"pakendid 05.03.26 Sandra"',
                        '"https://example.invalid/templates/aaaa-1111/html/"',
                        '"2026-03-05 10:00:00"',
                        '"789"',
                    ]
                ),
            ]
        ),
        encoding="utf-8",
    )

    call_command(
        "refresh_current_register",
        "plan",
        "--workbook",
        str(workbook),
        "--campaigns",
        str(export),
        "--today",
        SNAPSHOT_DATE.isoformat(),
    )

    printed = capsys.readouterr().out
    assert "Catalogue" in printed
    assert "campaign set (pinned in the plan digest)" in printed
    assert "campaign file (evidence only)" in printed
    assert "Plan digest" in printed
    assert "Nothing was written" in printed
    # And it really wrote nothing.
    assert CurrentRegisterState.objects.count() == 0
    assert NextAction.objects.count() == 0
