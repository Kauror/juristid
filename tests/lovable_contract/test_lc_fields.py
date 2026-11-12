# ruff: noqa: F811 - the fixtures imported from fixtures.py are used as arguments
"""Source-owned fields: parity, restoration, COMPARE, idempotency, attribution, read-only.

LC-F1, LC-F2, LC-F3, LC-B2, LC-A1 and LC-R1 (audit §D.2), against the business
contract the owner approved on 11.11.2026 (`JURISTID_LOVABLE_AUTHORITATIVE_APPROVAL.md`
§1, docs/adr/0213). The oracle (`oracle.py`) decides what «matches»; the
engine's own reason codes appear only as observed values.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.audit.models import ChangeEvent
from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync_models import SyncMode, SyncOutcome, SyncRunStatus
from tests.lovable_contract import oracle
from tests.lovable_contract.fixtures import pilot_on, world  # noqa: F401 - fixtures
from tests.lovable_contract.world import (
    BASELINE,
    BOOKKEEPING_TABLES,
    LINK,
    UKU,
    build_world,
    changed_tables,
    decisions,
    ref,
    table_hashes,
)

pytestmark = pytest.mark.django_db

NEW_LINK = LINK.replace("{CCCC}", "{DDDD}")

#: One Lovable-owned field changed per Teema (LC-F1), 2099_1..13; 2099_12 is
#: 2099_11's successor and itself unchanged.
SOURCE_CHANGES = {
    1: {"teema": "Kontraktiteema 2099_1 (Lovable'i parandus)"},
    2: {"uus_vastutaja": UKU},
    3: {"hetkeseis": "valitsuses"},
    4: {"oigusakt": "ELi direktiiv"},
    5: {"valja": "2026-11-02", "valja_kuupaev": "2026-11-02", "kellele": "Näidisministeerium"},
    6: {"arvamuse_tahtaeg": "2099-03-01"},
    7: {"jargmiseks": "Saadan märkused ministeeriumile", "tegevuse_tahtaeg": "2099-02-01"},
    8: {"koja_ettepanek": True},
    9: {"onenote_link": NEW_LINK},
    13: {"sisse": "2026-02-02"},
    14: {"valja": "ei saatnud"},
}


def _source_world():
    world = build_world(15)
    for number, values in SOURCE_CHANGES.items():
        world.lovable.edit(ref(number), **values)
    world.lovable.set_commencements(
        ref(10),
        [
            {
                "liik": "Eeldatava kuupäevaga jõustumine",
                "kuupaev": "2099-07-01",
                "tapsustus": "§ 2",
            },
            {"liik": "Üldises korras"},
        ],
    )
    world.lovable.link(ref(11), ref(12))
    return world


def test_every_source_owned_field_changed_in_lovable_reads_lovables_value(db, pilot_on):
    """LC-F1 + LC-B2: after one APPLY run every Lovable-owned field matches Lovable;
    the next run writes nothing.

    Contract: approval 11.11 §1 «Everything Lovable owns mirrors Lovable after
    every successful sync … A change in Lovable is written through the normal
    domain services»; §2 «Runs 2 and 3: 0 changes»."""
    world = _source_world()
    run = world.run()
    assert (run.status, run.verified) == (SyncRunStatus.SUCCEEDED, True), run.error
    found = oracle.differences(world.lovable, today=world.today)
    assert found == []
    compared = oracle.compare(world.lovable, today=world.today)
    changed = {(c.reference, c.field) for c in compared if c.verdict == oracle.MATCH}
    for reference, field in [
        (ref(1), "title"),
        (ref(2), "owner"),
        (ref(3), "stage"),
        (ref(4), "legal_act"),
        (ref(5), "opinion_outcome"),
        (ref(6), "opinion_request"),
        (ref(7), "step"),
        (ref(8), "koda"),
        (ref(9), "onenote_link"),
        (ref(10), "commencements"),
        (ref(11), "lifecycle"),
        (ref(13), "received"),
        (ref(14), "opinion_outcome"),
    ]:
        assert (reference, field) in changed, (reference, field)

    events = ChangeEvent.objects.count()
    again = world.run()
    assert again.status == SyncRunStatus.SUCCEEDED, again.error
    assert not again.decisions.filter(outcome=SyncOutcome.APPLIED).exists()
    assert ChangeEvent.objects.count() == events
    assert oracle.differences(world.lovable, today=world.today) == []
    assert world.lovable.writes == []


def _local_edits(world):
    """A lawyer edits one Lovable-owned field per Teema in Juristid; Lovable is unchanged.

    Since TL-01 a person is refused these edits in APPLY, so the divergence is
    one from before the lock (`tests.source_ownership_support`): the restore
    rule is what is under test here, not the lock."""
    from app.matters.services import (
        assign_matter,
        change_stage,
        change_track,
        close_matter,
        set_legal_instruments,
        set_matter_dates,
        set_matter_title,
    )
    from app.workflow.enums import Disposition, Track
    from app.workflow.models import StageVocabulary
    from app.workflow.services import set_next_action
    from tests.source_ownership_support import divergence_from_before_the_lock

    lawyer = world.person(UKU)
    with divergence_from_before_the_lock():
        set_matter_title(matter=world.matter(ref(1)), value="Juristi oma pealkiri", actor=lawyer)
        assign_matter(matter=world.matter(ref(2)), owner=lawyer, actor=lawyer)
        change_stage(
            matter=world.matter(ref(3)),
            stage=StageVocabulary.objects.get(key="government"),
            actor=lawyer,
        )
        set_legal_instruments(
            matter=world.matter(ref(4)),
            legal_instruments=[
                world.matter(ref(4))
                ._meta.get_field("legal_instruments")
                .related_model._default_manager.get(key="maarus")
            ],
            actor=lawyer,
        )
        set_next_action(matter=world.matter(ref(5)), text="Helistan ministeeriumi", actor=lawyer)
        change_track(matter=world.matter(ref(6)), track=Track.KODA_INITIATIVE, actor=lawyer)
        set_matter_dates(
            matter=world.matter(ref(7)), received_date=dt.date(2026, 3, 3), actor=lawyer
        )
        close_matter(
            matter=world.matter(ref(8)),
            disposition=Disposition.OTHER,
            actor=lawyer,
            follow_ups_confirmed=True,
        )
    return lawyer


RESTORED_FIELDS = {
    1: "title",
    2: "owner",
    3: "stage",
    4: "legal_act",
    5: "next_action_text",
    6: "koda_initiative",
    7: "received",
    8: "stage",
}


def test_a_local_edit_of_a_lovable_owned_field_is_put_back(db, pilot_on):
    """LC-F2 + LC-B2: with Lovable unchanged, APPLY restores each locally edited
    field, keeps the Juristid value it replaced in the decision, and writes
    nothing on the next run.

    Contract: approval 11.11 §1 «A local edit in Juristid, while Lovable is
    unchanged, is put back the next night. It is audited as «Lovable'i väärtus
    taastati». A step is replaced, never marked done. A Teema a lawyer closed is
    reopened»."""
    world = build_world(8)
    _local_edits(world)
    assert oracle.differences(world.lovable, today=world.today)
    run = world.run()
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert oracle.differences(world.lovable, today=world.today) == []
    for number, field in RESTORED_FIELDS.items():
        decision = decisions(run, ref(number))[field]
        assert decision.outcome == SyncOutcome.APPLIED, (number, field)
        assert decision.reason == lovable_sync.RESTORED, (number, field, decision.reason)
        # Audited: an event of this run on the Teema, by no person.
        events = world.events_of(run).filter(matter=world.matter(ref(number)))
        assert events.exists() and not events.filter(actor__isnull=False).exists(), number
    # The old value stays readable in the audit trail (approval §1 «audited with
    # the old value, the new value»).
    title = world.events_of(run).get(matter=world.matter(ref(1)), event_type="MATTER_TITLE_CHANGED")
    assert title.payload["previous"] == "Juristi oma pealkiri"
    from app.workflow.enums import ActionStatus
    from app.workflow.models import NextAction

    local = NextAction.objects.get(matter=world.matter(ref(5)), text="Helistan ministeeriumi")
    assert local.status == ActionStatus.SUPERSEDED
    assert world.matter(ref(8)).is_open

    events = ChangeEvent.objects.count()
    again = world.run()
    assert not again.decisions.filter(outcome=SyncOutcome.APPLIED).exists()
    assert ChangeEvent.objects.count() == events


def test_compare_writes_no_business_data_only_its_own_bookkeeping(db, pilot_on):
    """LC-F3: in COMPARE, local edits *and* Lovable's own changes are recorded
    per field and nothing else is written: every business table is
    byte-identical — the synchronisation state's baseline included, because it
    decides restore against source change and so which breaker applies — and
    only the bookkeeping tables (`world.BOOKKEEPING_TABLES`) change.

    Contract: approval 11.11 §5 «keeps the sync in COMPARE»; §6 «set the sync to
    COMPARE, which stops all further writes immediately»."""
    world = build_world(10)
    _local_edits(world)
    world.lovable.edit(ref(9), teema="Kontraktiteema 2099_9 (Lovable)", hetkeseis="valitsuses")
    world.lovable.edit(ref(10), valja="2026-11-02", valja_kuupaev="2026-11-02")
    juristid_before = oracle.compare(world.lovable, today=world.today)
    before = table_hashes()
    assert BASELINE in before
    run = world.run(mode=SyncMode.COMPARE)
    assert run.status in (SyncRunStatus.SUCCEEDED, SyncRunStatus.REVIEW_ONLY), run.error
    changed = changed_tables(before, table_hashes())
    assert changed <= BOOKKEEPING_TABLES, sorted(changed - BOOKKEEPING_TABLES)
    assert oracle.compare(world.lovable, today=world.today) == juristid_before
    for number, field in RESTORED_FIELDS.items():
        decision = decisions(run, ref(number))[field]
        assert decision.outcome == SyncOutcome.ELIGIBLE, (number, field, decision.outcome)
        assert decision.reason == lovable_sync.RESTORED
    for number, field in ((9, "title"), (9, "stage"), (10, "sent")):
        decision = decisions(run, ref(number))[field]
        assert decision.outcome == SyncOutcome.ELIGIBLE, (number, field, decision.outcome)
        assert decision.reason != lovable_sync.RESTORED


def _provenance(event):
    payload = event.payload or {}
    nested = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
    return (
        payload.get("provenance_source") or nested.get("provenance_source"),
        payload.get("sync_run") or nested.get("sync_run"),
    )


@pytest.mark.parametrize("scenario", ["source", "restore"])
def test_no_sync_write_names_a_lawyer(db, pilot_on, scenario):
    """LC-A1 (actor): every write of a source change or a restore is the
    synchronisation's own — no audit event of the run names a person.

    Contract: approval 11.11 §1 «audited with the old value, the new value, the
    Lovable topic and the run»; audit §D.2 LC-A1 «no event names a lawyer»."""
    if scenario == "source":
        world = _source_world()
    else:
        world = build_world(8)
        _local_edits(world)
    run = world.run()
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    new = list(world.events_of(run))
    assert new, "the run wrote no audit event"
    assert [e.event_type for e in new if e.actor_id is not None] == []


@pytest.mark.parametrize("scenario", ["source", "restore"])
def test_every_sync_write_names_its_run_and_source(db, pilot_on, scenario):
    """LC-A1 (provenance): every audit event a run writes names the
    synchronisation (`LOVABLE_SYNC`) and the run.

    Contract: approval 11.11 §1 «audited with … the Lovable topic and the run»."""
    if scenario == "source":
        world = _source_world()
    else:
        world = build_world(8)
        _local_edits(world)
    run = world.run()
    new = list(world.events_of(run))
    assert new
    missing = [
        e.event_type for e in new if _provenance(e) != (lovable_sync.PROVENANCE_SOURCE, str(run.pk))
    ]
    assert missing == []


def test_the_synchronisation_only_ever_reads_lovable(world):
    """LC-R1 (end to end): across baseline, APPLY and COMPARE runs the source
    saw only reads, and asking the transport for anything else is refused.

    Contract: approval 11.11 §2 «It read Lovable through GET requests only»."""
    world.lovable.edit(ref(1), teema="Uus pealkiri Näidis")
    world.run()
    world.run(mode=SyncMode.COMPARE)
    assert world.lovable.calls and {method for method, _ in world.lovable.calls} == {"GET"}
    assert {table for _, table in world.lovable.calls} <= {
        "topic",
        "topic_commencement",
        "topic_responsible_change",
        "topic_stage_link",
    }
    with pytest.raises(AttributeError, match="read-only"):
        world.lovable.save_topic  # noqa: B018 - the access itself is the probe
    assert world.lovable.writes == [("SAVE_TOPIC", "?")]


def test_the_oracle_never_reports_parity_over_a_teema_it_could_not_compare(world):
    """P2-1: a Teema whose synchronisation state is dropped, whose Teema is
    deleted, or that was asked for and does not exist is `NOT_COMPARED` — and
    `differences` reports it, so parity can never be vacuous."""
    from app.legacy_import.lovable_sync_models import LovableSyncState
    from app.matters.deletion import delete_matter

    assert oracle.differences(world.lovable, today=world.today) == []
    LovableSyncState.objects.filter(reference=ref(1)).delete()
    delete_matter(matter=world.matter(ref(2)), actor=None)
    found = {
        (c.reference, c.verdict, c.lovable)
        for c in oracle.differences(
            world.lovable, today=world.today, references=[ref(1), ref(2), ref(3), ref(9)]
        )
    }
    assert found == {
        (ref(1), oracle.NOT_COMPARED, "no synchronisation state"),
        (ref(2), oracle.NOT_COMPARED, "the Teema was deleted"),
        (ref(9), oracle.NOT_COMPARED, "no Lovable row"),
    }
    # The default population (no references) still reports both lost Teemad.
    default = {c.reference for c in oracle.differences(world.lovable, today=world.today)}
    assert default == {ref(1), ref(2)}
