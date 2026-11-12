# ruff: noqa: F811 - the fixtures imported from fixtures.py are used as arguments
"""Topic identity, tombstones, the excluded population and Juristid-only data.

LC-I1, LC-T1, LC-P1, LC-J1 and LC-J2 (audit §D.2), against the approved
contract (`JURISTID_LOVABLE_AUTHORITATIVE_APPROVAL.md` §1 «Never touched»).
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync_models import (
    LovableSyncState,
    SyncOutcome,
    SyncRunStatus,
)
from app.matters.models import Matter
from tests import factories
from tests.lovable_contract import oracle
from tests.lovable_contract.fake import LovableRefused
from tests.lovable_contract.fixtures import pilot_on, world  # noqa: F401 - fixtures
from tests.lovable_contract.world import (
    AVA,
    UKU,
    build_world,
    decisions,
    import_outside_cohort,
    lovable_row,
    ref,
    row_hashes,
    source_id,
)

pytestmark = pytest.mark.django_db


def _lovable_matters() -> int:
    return Matter.all_objects.filter(reference_year=2099).count()


# ---------------------------------------------------------------------------
# LC-I1 — topic identity
# ---------------------------------------------------------------------------


def test_lovable_itself_never_renumbers_a_topic(world):
    """LC-I1: `nr`, `aasta` and `id` are immutable in Lovable (`topic_guard`);
    the fake keeps that rule, so a renumbering can only be a broken reading."""
    with pytest.raises(LovableRefused, match="immutable"):
        world.lovable.edit(ref(1), nr=ref(99))


def test_a_reading_that_renumbers_a_topic_is_refused_and_repoints_nothing(world):
    """LC-I1 (impossible renumbering): a reading in which a synchronised id
    carries another `nr` re-points no Teema; it is held with a stable reason.

    Contract: audit §D.2 LC-I1 «The guard refuses: no Matter is re-pointed; a
    technical hold with a stable reason»; approval §1 (identity is Lovable's
    topic)."""
    row = world.lovable.rows[source_id(ref(1))]
    row["nr"], row["version"] = ref(99), row["version"] + 1  # a corrupt reading
    before = world.matter(ref(1)).pk
    run = world.run()
    assert decisions(run, ref(1))["*"].outcome == SyncOutcome.IDENTIFIER_CHANGED
    state = LovableSyncState.objects.get(source_id=source_id(ref(1)))
    assert (state.matter_id, state.reference) == (before, ref(1))
    assert not Matter.all_objects.filter(reference_year=2099, reference_number=99).exists()


def test_a_second_lovable_topic_with_a_taken_number_is_never_created(world):
    """LC-I1 (two Lovable rows with one `nr`): the newcomer is held with a
    stable reason, nothing is created, the existing Teema is untouched."""
    from app.legacy_import.lovable_sync_models import LovableSyncNewTopic

    other = "00000000-0000-0000-2099-000000000777"
    world.lovable.put(other, lovable_row(ref(1), teema="Teine sama numbriga"), version=1)
    before = _lovable_matters()
    run = world.run()
    assert run.counts["new_topics_created"] == 0
    held = LovableSyncNewTopic.objects.get(source_id=other)
    assert (held.status, held.reason) == ("HELD", lovable_sync.NEW_REFERENCE_TAKEN)
    assert _lovable_matters() == before
    assert world.matter(ref(1)).title == f"Kontraktiteema {ref(1)}"


# ---------------------------------------------------------------------------
# LC-T1 — tombstones
# ---------------------------------------------------------------------------


def test_a_topic_lovable_soft_deletes_is_kept_never_recreated_and_relinked_on_restore(world):
    """LC-T1: Lovable soft-deletes a synchronised topic → Juristid keeps the
    Teema and reports it missing; later runs neither delete nor recreate it;
    Lovable restores the same id → the same Teema follows it again, no duplicate.

    Contract: approval 11.11 §1 «Never touched: … the audit trail»; audit §D.2
    LC-T1 («does not delete the Matter … never recreated … relinked, not
    duplicated»)."""
    teema = world.matter(ref(1))
    count = _lovable_matters()
    world.lovable.soft_delete(ref(1))
    for _night in range(2):
        run = world.run()
        assert decisions(run, ref(1))["*"].outcome == SyncOutcome.SOURCE_MISSING
        fresh = world.matter(ref(1))
        assert (fresh.pk, fresh.deleted_at, fresh.title) == (teema.pk, None, teema.title)
        assert _lovable_matters() == count
        assert run.counts["new_topics_created"] == 0
    world.lovable.restore(ref(1))
    world.lovable.edit(ref(1), teema="Taastatud Lovable'is")
    run = world.run()
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert world.matter(ref(1)).pk == teema.pk
    assert world.matter(ref(1)).title == "Taastatud Lovable'is"
    assert _lovable_matters() == count
    assert oracle.differences(world.lovable, today=world.today) == []


# ---------------------------------------------------------------------------
# LC-P1 — the excluded population, as a rule
# ---------------------------------------------------------------------------


def test_the_excluded_population_is_counted_and_never_written(db, pilot_on):
    """LC-P1: Lovable's whole population = the synchronised cohort + the topics
    outside it; every excluded topic's Juristid Teema is byte-identical after
    APPLY; one more excluded topic grows the outside count by one and the write
    set by nothing.

    The excluded classes, restated as a rule (the real 154-row list stays in
    `_worklogs`, private): 3 *archive, unlinked* (Lovable's historical topics,
    a Juristid archive Teema with no link to them), 4 *possible duplicates*
    (Lovable ids Juristid holds outside every cohort) and 2 *tombstones* (a
    cohort Teema a person deleted in Juristid).

    Contract: approval 11.11 §1 «Covered: both registers …»; §2 «0 failures»;
    audit §D.2 LC-P1."""
    from app.matters.deletion import delete_matter
    from app.matters.services import create_matter

    world = build_world(6)
    for number in range(201, 204):  # archive, unlinked
        reference = ref(number)
        world.lovable.put(
            source_id(reference), lovable_row(reference, operatiivne_ulatus=False), version=2
        )
        create_matter(title=f"Arhiiviteema {reference}", reference_year=1999)
    import_outside_cohort(world, [211, 212, 213, 214])  # possible duplicates
    for number in (5, 6):  # tombstones
        delete_matter(matter=world.matter(ref(number)), actor=None)
    excluded = [
        m
        for m in Matter.all_objects.all()
        if not LovableSyncState.objects.filter(matter=m, matter__deleted_at__isnull=True).exists()
    ]
    before = row_hashes(excluded)
    for number in range(1, 5):
        world.lovable.edit(ref(number), teema=f"Kontraktiteema {ref(number)} (Lovable)")
    run = world.run()
    assert run.status in (SyncRunStatus.SUCCEEDED, SyncRunStatus.REVIEW_ONLY), run.error
    population = len(world.lovable.rows)
    # 4 live cohort Teemad; outside: 3 archive + 4 duplicates + 2 tombstones
    # (a deleted cohort Teema is retired from the cohort, ADR 0211).
    assert (run.counts["checked"], run.counts["outside_cohort"], run.counts["retired"]) == (4, 9, 2)
    assert run.counts["checked"] + run.counts["outside_cohort"] == population
    assert row_hashes(excluded) == before
    assert run.counts["new_topics_created"] == 0
    from app.legacy_import.lovable_sync_models import LovableSyncNewTopic

    holds = {str(t.source_id): (t.status, t.reason) for t in LovableSyncNewTopic.objects.all()}
    for number in (211, 212, 213, 214):
        assert holds[source_id(ref(number))] == ("HELD", lovable_sync.NEW_UUID_LIVE)
    for number in (5, 6):
        assert holds[source_id(ref(number))] == ("HELD", lovable_sync.NEW_UUID_TOMBSTONE)
    assert (
        oracle.differences(world.lovable, today=world.today, references=world.references[:4]) == []
    )

    # One more archive topic in Lovable: outside +1, the write set unchanged.
    extra = ref(204)
    world.lovable.put(source_id(extra), lovable_row(extra, operatiivne_ulatus=False), version=2)
    again = world.run()
    assert again.counts["outside_cohort"] == run.counts["outside_cohort"] + 1
    assert not again.decisions.filter(outcome=SyncOutcome.APPLIED).exists()
    assert row_hashes(excluded) == before


# ---------------------------------------------------------------------------
# LC-J1, LC-J2 — Juristid-only data
# ---------------------------------------------------------------------------


def _juristid_only(world, reference: str) -> list:
    """What a lawyer adds that Lovable never owns: a note, a document, a draft
    opinion, an own important date and an own commencement estimate."""
    from app.intelligence.services import add_important_date

    matter = world.matter(reference)
    lawyer = world.person(UKU)
    day = dt.date(2099, 4, 1)
    return [
        factories.EntryFactory(matter=matter, author=lawyer),
        factories.DocumentFactory(matter=matter),
        factories.SubmissionFactory(matter=matter),
        add_important_date(
            matter=matter, title="Oma tähtaeg", date_value=day, period_end=day, actor=lawyer
        ),
    ]


@pytest.mark.parametrize("scenario", ["source change", "restore", "closure", "tombstone"])
def test_juristid_only_records_are_byte_identical_after_any_run(db, pilot_on, scenario):
    """LC-J1: notes, documents, submissions and a person's own dates on every
    synchronised Teema are byte-identical after a source change, a restore, a
    closure from Lovable and a Lovable tombstone.

    Contract: approval 11.11 §1 «Never touched: Juristid-only notes, documents,
    submissions, evidence, OneNote imports, a person's own important or
    commencement dates, and the audit trail» (the r54 production check D, made
    a test). Planned Juristid-only work is TL-02 (`test_lc_lifecycle.py`)."""
    from app.matters.services import set_matter_title

    world = build_world(2)
    records = [r for reference in world.references for r in _juristid_only(world, reference)]
    before = row_hashes(records)
    if scenario == "source change":
        world.lovable.edit(ref(1), teema="Uus pealkiri Lovableist", oigusakt="ELi määrus")
    elif scenario == "restore":
        set_matter_title(matter=world.matter(ref(1)), value="Oma", actor=world.person(AVA))
    elif scenario == "closure":
        world.lovable.edit(ref(1), hetkeseis="jõustunud", jargmiseks="", tegevuse_tahtaeg=None)
    else:
        world.lovable.soft_delete(ref(1))
    run = world.run()
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert row_hashes(records) == before
    world.run()
    assert row_hashes(records) == before


def test_a_cancellation_from_lovable_never_cancels_a_persons_own_commencement(world):
    """LC-J2: Lovable stops stating a commencement a lawyer also recorded by
    hand: only the Lovable-sourced record is cancelled; the person's own stays.

    Contract: approval 11.11 §1 «dates Lovable no longer states are cancelled
    … a person's own dates are never touched»."""
    from app.intelligence.models import MatterImportantDate
    from app.intelligence.services import add_important_date

    world.lovable.set_commencements(
        ref(1), [{"liik": "Kindla kuupäevaga jõustumine", "kuupaev": "2099-07-01"}]
    )
    world.run()
    day = dt.date(2099, 7, 1)
    own = add_important_date(
        matter=world.matter(ref(1)),
        title="Jõustumine (minu märge)",
        date_value=day,
        period_end=day,
        kind="PLANNED_COMMENCEMENT",
        actor=world.person(AVA),
    )
    world.lovable.set_commencements(ref(1), [])
    run = world.run()
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    rows = MatterImportantDate.objects.filter(
        matter=world.matter(ref(1)), kind="PLANNED_COMMENCEMENT"
    )
    assert {(r.pk == own.pk, r.status) for r in rows} == {(True, "ACTIVE"), (False, "CANCELLED")}
    assert oracle.differences(world.lovable, today=world.today, exclude_dates=[own.pk]) == []
