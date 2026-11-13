# ruff: noqa: F811 - the fixtures imported from fixtures.py are used as arguments
"""Lifecycle, opinions, commencements, unknown values and breakers.

LC-O1, LC-C1, LC-C2, LC-D1, LC-U1 and LC-B1 (audit §D.2), plus the current
behaviour behind two open owner decisions (TL-02, LS-05), against the approved
contract (`JURISTID_LOVABLE_AUTHORITATIVE_APPROVAL.md` §1, §4, §5).
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.audit.models import ChangeEvent
from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync_models import SyncOutcome, SyncRunStatus
from tests.lovable_contract import oracle
from tests.lovable_contract.fake import LovableRefused
from tests.lovable_contract.fixtures import pilot_on, world  # noqa: F401 - fixtures
from tests.lovable_contract.world import (
    AVA,
    NIGHT,
    TALLINN,
    UKU,
    build_world,
    decisions,
    lovable_row,
    ref,
    source_id,
)

pytestmark = pytest.mark.django_db


def _parity(world, *references):
    return oracle.differences(world.lovable, today=world.today, references=references or None)


# ---------------------------------------------------------------------------
# LC-O1 — the opinion outcome is one identity
# ---------------------------------------------------------------------------


def test_an_opinion_outcome_moves_as_one_chain_and_blank_is_never_sent(world):
    """LC-O1: sent → another day → «ei saatnud» → blank, over four runs.

    Contract: approval 11.11 §1 «a sent date or «ei saatnud» ends Lovable's
    request; a blank … never does; no second consultation is ever created;
    no document … is invented». One current statement per Teema; each change
    supersedes the previous one (the history stays readable), never a second,
    parallel outcome; blank leaves no statement and never «sent»."""
    from app.legacy_import.lovable_sync_models import LovableOpinionOutcome
    from app.matters.models import MatterResponseDeadline
    from app.submissions.models import Submission

    reference = ref(1)
    matter = world.matter(reference)
    steps = [
        {"valja": "2026-11-02", "valja_kuupaev": "2026-11-02"},
        {"valja": "2026-11-05", "valja_kuupaev": "2026-11-05"},
        {"valja": "ei saatnud", "valja_kuupaev": None},
        {"valja": "", "valja_kuupaev": None},
    ]
    wanted = [("SENT", dt.date(2026, 11, 2)), ("SENT", dt.date(2026, 11, 5)), ("NOT_SENT", None)]
    for n, values in enumerate(steps):
        world.lovable.edit(reference, **values)
        run = world.run()
        assert run.status == SyncRunStatus.SUCCEEDED, run.error
        assert _parity(world, reference) == []
        rows = list(LovableOpinionOutcome.objects.filter(matter=matter))
        heads = [r for r in rows if _no_successor(r)]
        assert len(heads) == 1, n  # one current statement
        # A single chain: every row but the first names the one before it.
        assert sum(1 for r in rows if r.previous_id is None) == 1
        current = heads[0]
        if n < len(wanted):
            assert (current.outcome, current.sent_on) == wanted[n]
        else:
            assert (current.outcome, current.sent_on) == ("CLEARED", None)
    assert not Submission.objects.filter(matter=matter).exists()
    assert not MatterResponseDeadline.objects.filter(matter=matter).exists()


def _no_successor(row) -> bool:
    from app.legacy_import.lovable_sync_models import LovableOpinionOutcome

    return not LovableOpinionOutcome.objects.filter(previous=row).exists()


# ---------------------------------------------------------------------------
# LC-C1 — closure and reopening
# ---------------------------------------------------------------------------


def test_lovable_closes_with_its_own_reason_and_reopens_at_its_stage(db, pilot_on):
    """LC-C1: «jõustunud» closes COMPLETED, «rohkem ei tegele» MONITORING_STOPPED,
    a closed Teema Lovable reopens is open at the mapped stage; the next run
    writes nothing.

    Contract: approval 11.11 §1 lifecycle «open / closed, reason,
    continuation»; §2 write set «Closed: … «jõustunud»»; §1 «A Teema a lawyer
    closed is reopened; a Teema a lawyer reopened is closed again» (local
    lifecycle edits: LC-F2)."""
    world = build_world(3, closed=(3,))
    world.lovable.edit(ref(1), hetkeseis="jõustunud", jargmiseks="", tegevuse_tahtaeg=None)
    world.lovable.edit(ref(2), hetkeseis="rohkem ei tegele")
    world.lovable.edit(
        ref(3),
        hetkeseis="Riigikogus",
        jargmiseks="Ootan 3. lugemist",
        tegevuse_tahtaeg="2099-05-01",
    )
    run = world.run()
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert _parity(world) == []
    one, two, three = (world.matter(ref(n)) for n in (1, 2, 3))
    assert (one.is_open, one.disposition, one.stage.key) == (False, "COMPLETED", "in_force")
    assert (two.is_open, two.disposition) == (False, "MONITORING_STOPPED")
    assert (three.is_open, three.stage.key) == (True, "parliament")
    events = ChangeEvent.objects.count()
    assert not world.run().decisions.filter(outcome=SyncOutcome.APPLIED).exists()
    assert ChangeEvent.objects.count() == events


def test_tl02_a_source_closure_with_juristid_only_work_is_held_not_cancelled(world):
    """LC-C3 / TL-02 (owner decision 12.11.2026, flipped from the pinned r54 behaviour).

    Regression: TL-02. A closure that comes from Lovable while the Teema holds
    Juristid-only work («Kodulehe ülevaade», a lawyer's planned Tegevus) is
    *held*: the Teema stays open, nothing is cancelled, and the oracle reads the
    Teema as HELD — a difference with a recorded hold, never MATCH and never an
    unexplained parity error. When the lawyer finishes the work, the next night
    closes it with Lovable's reason and parity is clean.

    Contract: owner decision 12.11.2026 points 1–3 and 8 (hold, preserve, make
    the discrepancy visible, idempotent); approval 11.11 §1 lifecycle."""
    from app.matters.models import MatterWebsiteOverview
    from app.matters.services import plan_website_overview
    from app.workflow.models import NextAction
    from app.workflow.services import add_planned_action, cancel_next_action

    matter = world.matter(ref(1))
    overview = plan_website_overview(matter=matter, actor=world.person(AVA))
    planned = add_planned_action(
        matter=matter, text="Helistan ministeeriumi", target_date=dt.date(2099, 4, 1)
    )
    world.lovable.edit(ref(1), hetkeseis="jõustunud", jargmiseks="", tegevuse_tahtaeg=None)
    run = world.run()
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert world.matter(ref(1)).is_open
    assert MatterWebsiteOverview.objects.get(pk=overview.pk).status == "PLANNED"
    assert NextAction.objects.get(pk=planned.pk).status == "PLANNED"
    stage = decisions(run, ref(1))["stage"]
    assert (stage.outcome, stage.reason) == (SyncOutcome.REVIEW_REQUIRED, "LOCAL_WORK_AT_CLOSURE")
    assert run.counts["held_local_work"] == 1
    held = {c.field: c.verdict for c in _parity(world, ref(1))}
    assert held["lifecycle"] == oracle.HELD
    assert set(held.values()) == {oracle.HELD}
    # The other Teemad are untouched by it.
    assert _parity(world, ref(2), ref(3)) == []

    # The lawyer finishes the work; the next night closes with Lovable's reason.
    from app.matters.services import cancel_website_overview

    cancel_website_overview(overview=overview, actor=world.person(AVA))
    cancel_next_action(action=planned, actor=world.person(AVA), reason="Pole vaja")
    later = world.run()
    assert later.status == SyncRunStatus.SUCCEEDED, later.error
    one = world.matter(ref(1))
    assert (one.is_open, one.disposition) == (False, "COMPLETED")
    assert later.counts["held_local_work"] == 0
    assert _parity(world) == []


# ---------------------------------------------------------------------------
# LC-C2 — continuations
# ---------------------------------------------------------------------------


def test_a_continuation_to_an_existing_teema_closes_it_under_its_successor(world):
    """LC-C2 (existing successor): one run closes A SUPERSEDED under B.

    Contract: approval 11.11 §2 «2026's 140 continued by 259» (write set:
    closed, continuation); lifecycle includes «continuation»."""
    world.lovable.link(ref(1), ref(2))
    run = world.run()
    assert decisions(run, ref(1))[lovable_sync.CONTINUATION].outcome == SyncOutcome.APPLIED
    a = world.matter(ref(1))
    assert (a.is_open, a.disposition, a.superseded_by_id) == (
        False,
        "SUPERSEDED",
        world.matter(ref(2)).pk,
    )
    assert _parity(world) == []


def _new_successor(world, number: int) -> str:
    reference = ref(number)
    world.lovable.put(source_id(reference), lovable_row(reference), version=1)
    world.lovable.link(ref(1), reference)
    return reference


def test_st007_a_same_run_successor_closes_the_predecessor_in_one_run(world):
    """LC-C2: A→B with B new in Lovable: one run creates B and closes A under it,
    and the next run writes nothing.

    Contract: approval 11.11 §1 «Everything Lovable owns mirrors Lovable after
    every successful sync» — the continuation included."""
    successor = _new_successor(world, 50)
    world.run()
    a = world.matter(ref(1))
    assert (a.is_open, a.superseded_by_id) == (False, world.matter(successor).pk)
    assert _parity(world) == []
    assert not world.run().decisions.filter(outcome=SyncOutcome.APPLIED).exists()


def test_an_ambiguous_continuation_is_held_never_guessed(world):
    """LC-C2 (A→B and A→C): held, A stays open, nothing guessed.

    Contract: approval 11.11 §1 «… It is never guessed» (the fail-closed rule
    the approval states for the step type, applied to the lifecycle)."""
    world.lovable.link(ref(1), ref(2))
    world.lovable.link(ref(1), ref(3))
    run = world.run()
    assert decisions(run, ref(1))["stage"].reason == lovable_sync.SUCCESSOR_AMBIGUOUS
    a = world.matter(ref(1))
    assert (a.is_open, a.superseded_by_id) == (True, None)


def test_a_continuation_cycle_is_held_never_guessed(world):
    """LC-C2 (A→B→A): neither Teema is closed under the other; the night is
    not a failure.

    Contract: audit §D.2 LC-C2 «Ambiguous/cycle: held, never guessed»; approval
    11.11 §1 «It is never guessed». Observed at `f3eabea7`: run 1 closes A
    SUPERSEDED under B and fails B («not applied (DomainError)»), status FAILED;
    run 2 fails B again."""
    world.lovable.link(ref(1), ref(2))
    world.lovable.link(ref(2), ref(1))
    run = world.run()
    assert run.status in (SyncRunStatus.SUCCEEDED, SyncRunStatus.REVIEW_ONLY), run.error
    one, two = world.matter(ref(1)), world.matter(ref(2))
    assert (one.superseded_by_id, two.superseded_by_id) == (None, None)
    assert one.is_open and two.is_open
    assert decisions(run, ref(1))["stage"].reason == lovable_sync.CONTINUATION_CYCLE


# ---------------------------------------------------------------------------
# LC-D1 — commencements and their provenance
# ---------------------------------------------------------------------------

FIXED = "Kindla kuupäevaga jõustumine"
ESTIMATED = "Eeldatava kuupäevaga jõustumine"
GENERAL = "Üldises korras"


def test_commencements_mirror_lovable_by_kind_and_stage_and_removals_are_kept(db, pilot_on):
    """LC-D1: a fixed day is the law's day only at an adopted stage, a plan
    elsewhere; an estimate stays an estimate; «Üldises korras» has no day; a
    § note is part of the value; a date Lovable no longer states is cancelled
    and kept.

    Contract: approval 11.11 §1 «Commencement dates: the dates that came from
    Lovable mirror Lovable as a set; dates Lovable no longer states are
    cancelled and kept in the history; § notes are part of the value»."""
    from app.intelligence.models import MatterImportantDate

    world = build_world(2)
    world.lovable.edit(ref(2), hetkeseis="jõustumise ootel")
    for number in (1, 2):
        world.lovable.set_commencements(
            ref(number),
            [
                {"liik": FIXED, "kuupaev": "2099-07-01", "tapsustus": "§ 1"},
                {"liik": ESTIMATED, "kuupaev": "2099-09-01"},
                {"liik": GENERAL},
            ],
        )
    run = world.run()
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert _parity(world) == []
    kinds = {
        n: sorted(k for _m, k, _d, _n in oracle.observed(world.matter(ref(n)))["commencements"])
        for n in (1, 2)
    }
    assert kinds[1] == ["ESTIMATED_COMMENCEMENT", "GENERAL_ORDER", "PLANNED_COMMENCEMENT"]
    assert kinds[2] == ["ESTIMATED_COMMENCEMENT", "GENERAL_ORDER", "KNOWN_DATE"]

    # Lovable drops the estimate and rewords the § note.
    world.lovable.set_commencements(
        ref(1),
        [
            {"liik": FIXED, "kuupaev": "2099-07-01", "tapsustus": "§ 1 lg 2"},
            {"liik": GENERAL},
        ],
    )
    world.run()
    assert _parity(world, ref(1)) == []
    estimate = MatterImportantDate.objects.get(
        matter=world.matter(ref(1)), kind="ESTIMATED_COMMENCEMENT"
    )
    assert estimate.status == "CANCELLED"  # kept, never deleted


def test_each_commencement_the_sync_writes_names_lovable_and_the_run(world):
    """LC-D1 (provenance): every commencement event of the run names the
    synchronisation and the run (source = Lovable, run key)."""
    world.lovable.set_commencements(ref(1), [{"liik": ESTIMATED, "kuupaev": "2099-09-01"}])
    run = world.run()
    events = world.events_of(run).filter(matter=world.matter(ref(1)))
    assert events.exists()
    for event in events:
        payload = event.payload or {}
        nested = payload.get("provenance") or {}
        assert (payload.get("sync_run") or nested.get("sync_run")) == str(run.pk), event.event_type


def test_lv02_teadmata_beside_a_readable_date_holds_the_whole_set_today(world):
    """LV-02 (owner decision pending) — current behaviour, pinned: one
    «Teadmata» row holds every commencement of the Teema (nothing written).

    Alternatives (audit §C.1 LV-02): skip «Teadmata» (it asserts no date) and
    reconcile the readable rows, or hold only that row.
    `test_lv02_…_readable_date_is_written` is the contract once decided."""
    world.lovable.set_commencements(
        ref(1), [{"liik": "Teadmata"}, {"liik": FIXED, "kuupaev": "2099-07-01"}]
    )
    run = world.run()
    decision = decisions(run, ref(1))["commencements"]
    assert (decision.outcome, decision.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.COMMENCEMENT_UNREADABLE,
    )
    assert oracle.observed(world.matter(ref(1)))["commencements"] == []


@pytest.mark.xfail(strict=True, reason="LV-02: pending owner decision («Teadmata» blocks the set)")
def test_lv02_teadmata_beside_a_readable_date_the_readable_date_is_written(world):
    """LC-D1 / LV-02: «Teadmata» states no date; the fixed day beside it is
    written (the audit's recommended reading)."""
    world.lovable.set_commencements(
        ref(1), [{"liik": "Teadmata"}, {"liik": FIXED, "kuupaev": "2099-07-01"}]
    )
    world.run()
    assert oracle.observed(world.matter(ref(1)))["commencements"] == [
        ("MatterImportantDate", "PLANNED_COMMENCEMENT", "2099-07-01", "")
    ]


def test_a_commencement_on_a_closed_teema_is_not_representable_and_writes_nothing(db, pilot_on):
    """LC-D1 (closed Teema): Juristid's date services refuse a closed file; the
    run still succeeds and writes nothing there.

    Contract: approval 11.11 §4 «Commencement on a closed Teema … Accepted»."""
    world = build_world(1, closed=(1,))
    world.lovable.set_commencements(ref(1), [{"liik": FIXED, "kuupaev": "2099-07-01"}])
    run = world.run()
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert oracle.observed(world.matter(ref(1)))["commencements"] == []
    assert not run.decisions.filter(state__reference=ref(1), outcome=SyncOutcome.APPLIED).exists()


# ---------------------------------------------------------------------------
# LC-U1 — unknown classifications fail closed, the rest still syncs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "field", "reason"),
    [
        ({"hetkeseis": "Menetluses kuskil"}, "stage", "STAGE_UNMAPPED"),
        ({"oigusakt": "Muu"}, "legal_act", "INSTRUMENT_UNMAPPED"),
        (
            {"onenote_link": "onenote:///Naidis/Leht.one#Uus"},
            "onenote_link",
            "LINK_NOT_HTTPS",
        ),
        (
            {"onenote_link": "http://example.invalid/sites/naidis/Leht"},
            "onenote_link",
            "LINK_NOT_HTTPS",
        ),
    ],
    ids=["unknown stage", "legal act Muu (LV-01)", "onenote: link (LV-03b)", "http: link"],
)
def test_an_unknown_value_is_held_and_the_rest_of_the_teema_still_syncs(
    world, values, field, reason
):
    """LC-U1: a value outside Juristid's decided vocabulary is held with a
    named reason (fail closed), visible as a review item, and the same Teema's
    other changes are still written.

    Contract: approval 11.11 §1 «It is never guessed»; audit §D.2 LC-U1."""
    world.lovable.edit(ref(1), teema="Kontraktiteema 2099_1 (uus)", **values)
    before = oracle.observed(world.matter(ref(1)))
    run = world.run()
    found = decisions(run, ref(1))
    assert (found[field].outcome, found[field].reason) == (SyncOutcome.REVIEW_REQUIRED, reason)
    after = oracle.observed(world.matter(ref(1)))
    assert after["title"] == "Kontraktiteema 2099_1 (uus)"
    key = {"stage": "stage", "legal_act": "legal_act", "onenote_link": "onenote_link"}[field]
    assert after[key] == before[key]
    assert run.counts["review"] >= 1


def test_lovable_itself_refuses_an_unknown_commencement_kind(world):
    """LC-U1 (a new commencement kind): Lovable's `save_topic` refuses it
    (`c_liigid`), so the engine never sees one; the fake keeps that rule."""
    with pytest.raises(LovableRefused, match="Tundmatu"):
        world.lovable.set_commencements(ref(1), [{"liik": "Osaliselt", "kuupaev": "2099-01-01"}])
    with pytest.raises(LovableRefused, match="kuupäev puudub"):
        world.lovable.set_commencements(ref(1), [{"liik": FIXED}])


# ---------------------------------------------------------------------------
# LC-B1 — circuit breakers
# ---------------------------------------------------------------------------


def test_the_nightly_limits_are_the_approved_ones():
    """LC-B1: «Lovable's own changes: up to 20 Teemad a night; restores of local
    edits: up to 100 Teemad a night» (approval 11.11 §5)."""
    assert (lovable_sync.MAX_CHANGED_MATTERS, lovable_sync.MAX_RESTORED_MATTERS) == (20, 100)


def test_a_night_over_the_source_limit_stops_visibly_and_writes_nothing(db, pilot_on):
    """LC-B1 (source changes, at the real limit): 21 Teemad changed in Lovable
    → the night stops as review-only and writes none of them; a supervised run
    with a raised limit (`--max-changed`) writes them all.

    Contract: approval 11.11 §5 «a night over either limit stops visibly
    without writing that part»; §5.3.2 the one-run override."""
    world = build_world(21)
    for number in range(1, 22):
        world.lovable.edit(ref(number), teema=f"Kontraktiteema {ref(number)} (Lovable)")
    stopped = world.run(trigger="SCHEDULED")
    assert (stopped.status, stopped.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.BREAKER)
    assert stopped.counts["changed_source"] == 21
    assert len(_parity(world)) == 21
    assert not stopped.decisions.filter(outcome=SyncOutcome.APPLIED).exists()
    supervised = world.run(max_changed=21)
    assert supervised.status == SyncRunStatus.SUCCEEDED, supervised.error
    assert _parity(world) == []


def test_a_night_at_exactly_the_source_limit_writes_everything(db, pilot_on):
    """LC-B1 (the boundary): 20 Teemad changed in Lovable is within «up to 20
    Teemad a night» — the night writes them all.

    Contract: approval 11.11 §5 «Lovable's own changes: up to 20 Teemad a night»."""
    world = build_world(20)
    for number in range(1, 21):
        world.lovable.edit(ref(number), teema=f"Kontraktiteema {ref(number)} (Lovable)")
    run = world.run(trigger="SCHEDULED")
    assert run.status == SyncRunStatus.SUCCEEDED, (run.status, run.error)
    assert run.counts["changed_source"] == 20
    assert _parity(world) == []


def test_over_the_restore_limit_restores_wait_and_source_changes_are_written(db, pilot_on):
    """LC-B1 (restores): over the restore limit every restore-only Teema waits,
    while Lovable's own change is written; a Teema with both is applied whole
    (no partial Teema).

    Contract: approval 11.11 §5 «a night over either limit stops visibly
    without writing that part»; r54 P2-4. The limit is lowered with the
    run's own override (`--max-restored`), as the approval's rehearsal did
    («the restore limit lowered on purpose to 3»)."""
    from app.matters.services import set_matter_title
    from tests.source_ownership_support import divergence_from_before_the_lock

    world = build_world(4)
    lawyer = world.person(UKU)
    # Divergences from before the TL-01 lock (`tests.source_ownership_support`).
    with divergence_from_before_the_lock():
        for number in (1, 2, 3):
            set_matter_title(matter=world.matter(ref(number)), value="Juristi oma", actor=lawyer)
    world.lovable.edit(ref(3), oigusakt="ELi määrus")  # 3 has a source change and a restore
    world.lovable.edit(ref(4), teema="Kontraktiteema 2099_4 (Lovable)")
    run = world.run(max_restored=1)
    assert (run.status, run.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.RESTORE_BREAKER)
    assert world.matter(ref(4)).title == "Kontraktiteema 2099_4 (Lovable)"
    assert _parity(world, ref(3)) == []  # applied whole: its restore too
    assert [world.matter(ref(n)).title for n in (1, 2)] == ["Juristi oma", "Juristi oma"]
    after = world.run(max_restored=2)
    assert after.status == SyncRunStatus.SUCCEEDED, after.error
    assert _parity(world) == []


def test_a_zero_source_limit_writes_nothing(world):
    """LC-B1 / LS-06: a limit of 0 means none, as `max_restored=0` already does."""
    world.lovable.edit(ref(1), teema="Kontraktiteema 2099_1 (Lovable)")
    before = world.matter(ref(1)).title
    run = world.run(max_changed=0)
    assert (run.status, run.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.BREAKER)
    assert world.matter(ref(1)).title == before


# ---------------------------------------------------------------------------
# LS-05 — an overdue drafting step (owner question), current behaviour
# ---------------------------------------------------------------------------


def test_ls05_an_overdue_drafting_step_with_nothing_sent_is_ended_today(world):
    """LS-05 (owner question) — current behaviour, pinned, not endorsed.

    Lovable asks for an opinion by a day and nothing is sent. The day after the
    deadline the drafting step is ended by the synchronisation, exactly when
    it is most urgent. Alternative (audit §C.2 LS-05): keep it until Lovable
    says sent or «ei saatnud». Flip this test when the owner answers."""
    from app.workflow.enums import ActionStatus
    from app.workflow.models import NextAction

    world.lovable.edit(ref(1), arvamuse_tahtaeg="2026-11-13", jargmiseks="", tegevuse_tahtaeg=None)
    world.run()
    matter = world.matter(ref(1))
    drafting = NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).first()
    assert drafting is not None and drafting.kind == "DO"
    later = dt.datetime(2026, 11, 14, 0, 15, tzinfo=TALLINN)
    assert later > NIGHT
    world.run(at=later)
    assert not NextAction.objects.filter(pk=drafting.pk, status=ActionStatus.OPEN).exists()


# ---------------------------------------------------------------------------
# The step kinds end to end, and the request rule
# ---------------------------------------------------------------------------


def test_each_step_kind_is_read_from_lovables_first_word(world):
    """The current step is Lovable's exact sentence and day; its kind comes from
    the first word: «Jälgin» → MONITOR, an unclear sentence → «Liik määramata»
    (SOURCE), never guessed. A plural form is compared for text and day only
    (`oracle.KNOWN_DIVERGENCES`).

    Contract: approval 11.11 §1 «Step type is worked out from Lovable's current
    text by one fixed rule (the first word; if unclear, «Liik määramata»). It is
    never guessed»."""
    world.lovable.edit(ref(1), jargmiseks="Jälgin eelnõu menetlust", tegevuse_tahtaeg="2099-04-01")
    world.lovable.edit(ref(2), jargmiseks="Jõustumisel.", tegevuse_tahtaeg="2099-04-02")
    world.lovable.edit(ref(3), jargmiseks="Ootame ministeeriumi vastust", tegevuse_tahtaeg=None)
    run = world.run()
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert _parity(world) == []
    kinds = {n: oracle.observed(world.matter(ref(n)))["step"]["kind"] for n in (1, 2)}
    assert kinds == {1: "MONITOR", 2: "SOURCE"}
    compared = {
        c.field: c for c in oracle.compare(world.lovable, today=world.today, references=[ref(3)])
    }
    assert "kind" not in compared["step"].lovable and compared["step"].verdict == oracle.MATCH


def test_a_blank_or_a_passed_deadline_never_ends_lovables_request(world):
    """The request Lovable states stays open while Lovable says nothing was sent
    — the next nights, and after its day has passed.

    Contract: approval 11.11 §1 «a sent date or «ei saatnud» ends Lovable's
    request; a blank or a passed deadline never does»."""
    world.lovable.edit(ref(1), arvamuse_tahtaeg="2026-11-20")
    world.run()
    assert world.matter(ref(1)).response_deadline == dt.date(2026, 11, 20)
    world.run()  # nothing sent: blank `valja`
    assert world.matter(ref(1)).response_deadline == dt.date(2026, 11, 20)
    world.run(at=dt.datetime(2026, 11, 25, 0, 15, tzinfo=TALLINN))  # the day has passed
    assert world.matter(ref(1)).response_deadline == dt.date(2026, 11, 20)
    world.lovable.edit(ref(1), valja="ei saatnud")
    world.run(at=dt.datetime(2026, 11, 26, 0, 15, tzinfo=TALLINN))
    assert world.matter(ref(1)).response_deadline is None
