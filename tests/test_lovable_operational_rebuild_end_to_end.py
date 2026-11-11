# ruff: noqa: F811 - pytest fixtures imported from the pilot suite are used as arguments
"""The rebuild in place, end to end (docs/adr/0202 §1–§6).

tests/test_lovable_operational_rebuild.py drives the importer's REBASE alone
(its pilot states seeded directly), and tests/test_lovable_operational_rebuild_sync.py
drives `enroll --supersede` over REBASE records it writes itself. Here neither
half is faked: a six-Teema pilot is applied by the real version-1 importer and
read by one real COMPARE night; a lawyer works on two of its Teemad; a real
rebuild package (REBASE every pilot topic, CREATE three) goes through plan →
apply → verify → `preserved`; the real `lovable_sync enroll --supersede
lovable-pilot` archives the pilot's baselines; and the real nightly run reads a
fake Lovable that returns exactly the package's rows through the real GET-only
`HttpTransport`.

**The order of the release steps matters**, and is pinned below:
`lovable_pilot preserved --before` (the digest file the apply wrote) runs
*before* `lovable_sync enroll`. The enrolment re-seeds every rebased Teema's
synchronisation state — its baseline, which the rebased Teema's local digest
covers — so after it the check reports that state as local work changed.

Every title, name, reference and identity is invented.
"""

from __future__ import annotations

import copy
import datetime as dt
import io
import json
from typing import Any

import pytest
from django.core.management import CommandError, call_command
from django.db import transaction

from app.documents.models import Document
from app.intelligence.models import MatterImportantDate
from app.legacy_import import lovable_pilot, lovable_review, lovable_source, lovable_sync
from app.legacy_import.lovable_pilot import (
    HOLD,
    KEEP_LOCAL,
    NOTHING,
    OPERATIONAL_COHORT,
    PILOT,
    WRITE,
    LovablePilotError,
    apply_plan,
    build_plan,
    plan_digest,
    preserved_digests,
    preserved_document,
    verify_applied,
)
from app.legacy_import.lovable_sync import PILOT_COHORT, TALLINN
from app.legacy_import.lovable_sync_models import (
    LovableSyncBaselineArchive,
    LovableSyncCohort,
    LovableSyncState,
    SyncMode,
    SyncOutcome,
)
from app.legacy_import.models import EvidencePilotRecord
from app.matters.models import Matter
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from app.workflow.services import set_next_action
from tests import factories
from tests.test_lovable_operational_end_to_end import _found, _night
from tests.test_lovable_operational_import import _sid
from tests.test_lovable_operational_rebuild import (
    BACKUP,
    NEW_STEP,
    _change,
    _created_finished,
    _created_ongoing,
    _created_predecessor,
    _pilot_id,
    _rebase,
    _rebuild,
)
from tests.test_lovable_operational_rebuild_sync import _ledger, _state_dump
from tests.test_lovable_pilot import URL, _topic, pilot_on, world  # noqa: F401
from tests.test_lovable_pilot import _apply as _apply_pilot
from tests.test_lovable_pilot import _package as _pilot_package
from tests.test_lovable_sync import NIGHT, FakeLovable, _decisions, _matter, _row

pytestmark = pytest.mark.django_db

DAY = dt.timedelta(days=1)
#: The first night after the rebuild (its snapshot is of 03.11).
AFTER = dt.datetime(2026, 11, 4, 0, 15, tzinfo=TALLINN)

UNCHANGED = "2026_501"  # REBASE, nothing differs
NEW_READING = "2026_502"  # REBASE, a new step reading: WRITE
STAGE_MOVE = "2026_503"  # REBASE, a stage that does not end the work: WRITE
LAWYER_STEP = "2026_504"  # REBASE, Lovable moved the step a lawyer replaced: KEEP_LOCAL
FINISHED = "2026_505"  # REBASE, «jõustunud» in Lovable: HOLD
COMMENCEMENT = "2026_506"  # REBASE, an added commencement: WRITE (and local work)
REBASED = (UNCHANGED, NEW_READING, STAGE_MOVE, LAWYER_STEP, FINISHED, COMMENCEMENT)
CREATED = ("2026_601", "2026_602", "2026_603")  # ongoing, finished, continues under 501
NATIVE, SAME_REF = "2026_7", "2026_515"

LOVABLE_STEP = {**NEW_STEP, "target_date": "2026-12-20", "text": "ootan Valitsuse otsust"}
LOVABLE_STEP["source_text"] = LOVABLE_STEP["text"]
REBUILD_LINK = "https://example.invalid/naidis/rebase"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _full_row(reference: str, topic_id: str, version: int, **values: Any) -> dict[str, Any]:
    """A topic row as the nightly reader returns it: every `TOPIC_COLUMNS` key."""
    row: dict[str, Any] = dict.fromkeys(lovable_source.TOPIC_COLUMNS)
    row.update(_row(reference, valja="", tegevuse_tahtaeg=None))
    row.update(id=topic_id, version=version, updated_at="2026-11-02T10:00:00+00:00")
    row.update(values)
    assert set(row) == set(lovable_source.TOPIC_COLUMNS)
    return row


def _pilot_topic(reference: str, **overrides: Any) -> dict[str, Any]:
    owner = overrides.get("owner_raw", "Mari")
    topic = _topic(reference, **overrides)
    topic["source"]["row"] = _full_row(
        reference,
        _pilot_id(reference),
        3,
        vastutaja=owner,
        # Nothing sent: a sent day would be the Teema's opinion outcome on the
        # first night under source authority (docs/adr/0213 §10).
        valja="",
        jargmiseks="ootan 2. lugemist",
        tegevuse_tahtaeg="2026-11-15",
        onenote_link=URL,
    )
    return topic


def _pilot() -> dict[str, Any]:
    package = _pilot_package(
        *(
            _pilot_topic(r, **({"owner_raw": "Jaan"} if r in (STAGE_MOVE, COMMENCEMENT) else {}))
            for r in REBASED
        )
    )
    package["snapshot"]["highest_reference"] = {"2026": 520}
    return package


def _with_full_row(topic: dict[str, Any], **values: Any) -> dict[str, Any]:
    """The topic, its `source.row` widened to the reader's full row (its own values kept)."""
    source = topic["source"]
    kept = {k: v for k, v in source["row"].items() if k not in ("id", "version")}
    source["row"] = _full_row(
        topic["reference"], str(source["id"]), source["version"], **{**kept, **values}
    )
    return topic


def _rebased(reference: str, changes: list[dict[str, Any]], **row: Any) -> dict[str, Any]:
    owner = "Jaan" if reference in (STAGE_MOVE, COMMENCEMENT) else "Mari"
    topic = _rebase(reference, changes, onenote_url=REBUILD_LINK)
    return _with_full_row(
        topic,
        vastutaja=owner,
        valja="",
        tegevuse_tahtaeg=row.pop("tegevuse_tahtaeg", "2026-11-15"),
        jargmiseks=row.pop("jargmiseks", "ootan 2. lugemist"),
        onenote_link=REBUILD_LINK,
        **row,
    )


def _planned_commencement_row() -> dict[str, Any]:
    return {
        "id": "00000000-0000-0000-0c02-000000000506",
        "topic_id": _pilot_id(COMMENCEMENT),
        "liik": "Eelnõus kavandatud jõustumine",
        "kuupaev": "2027-02-01",
        "tapsustus": "",
        "position": 0,
    }


def _rebuild_package() -> dict[str, Any]:
    commencement = _rebased(
        COMMENCEMENT,
        [
            _change(
                "commencement_add",
                {
                    "key": f"{COMMENCEMENT}-C09",
                    "review": "READY",
                    "target": "MatterImportantDate",
                    "kind": "PLANNED_COMMENCEMENT",
                    "date": "2027-02-01",
                    "date_precision": "EXACT",
                    "note": "",
                },
            )
        ],
    )
    commencement["source"]["commencements"] = [_planned_commencement_row()]
    ongoing = _with_full_row(_created_ongoing(), tegevuse_tahtaeg="2026-11-20", onenote_link=URL)
    return _rebuild(
        ongoing,
        _with_full_row(_created_finished(), vastutaja="", jargmiseks="", onenote_link=URL),
        _with_full_row(_created_predecessor(), vastutaja="", jargmiseks="", onenote_link=URL),
        _rebased(UNCHANGED, []),
        _rebased(
            NEW_READING,
            [_change("next_action", NEW_STEP)],
            jargmiseks=NEW_STEP["text"],
            tegevuse_tahtaeg=NEW_STEP["target_date"],
        ),
        _rebased(STAGE_MOVE, [_change("stage", "consultation")], hetkeseis="kooskõlastusel"),
        _rebased(
            LAWYER_STEP,
            [_change("next_action", LOVABLE_STEP, KEEP_LOCAL)],
            jargmiseks=LOVABLE_STEP["text"],
            tegevuse_tahtaeg=LOVABLE_STEP["target_date"],
        ),
        _rebased(FINISHED, [_change("stage", "in_force", HOLD)], hetkeseis="jõustunud"),
        commencement,
    )


def _lovable_now(fake: FakeLovable, package: dict[str, Any]) -> None:
    """Lovable after the rebuild's snapshot: exactly the package's rows."""
    for topic in package["topics"]:
        fake.rows[str(topic["source"]["id"])] = copy.deepcopy(topic["source"]["row"])
    fake.commencements = [
        copy.deepcopy(c) for t in package["topics"] for c in t["source"].get("commencements") or []
    ]


def _preserved_file(report: Any, path: Any) -> Any:
    """What `apply --preserved-out` writes, from the apply's report."""
    document = preserved_document(
        report.preserved_settled_before,
        report.preserved_references,
        local=report.preserved_local_before,
        declared=report.declared,
        created=report.created_ids,
    )
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def _preserved(path: Any, capsys: Any) -> dict[str, Any]:
    capsys.readouterr()
    try:
        call_command("lovable_pilot", "preserved", "--before", str(path), "--json")
    except CommandError:
        pass
    return json.loads(capsys.readouterr().out)


def _settled() -> dict[str, str]:
    by_pk = preserved_digests(settled=True)
    names = {str(m.pk): m.display_reference for m in Matter.all_objects.all()}
    return {names[pk]: digest for pk, digest in by_pk.items()}


def _enroll_arguments(digest: str) -> list[str]:
    return [
        "lovable_sync",
        "enroll",
        "--cohort",
        OPERATIONAL_COHORT,
        "--package-sha256",
        digest,
        "--expect",
        "9",
        "--reason",
        "Operatiivne register ühest hetktõmmisest",
        "--supersede",
        PILOT_COHORT,
    ]


# ---------------------------------------------------------------------------
# The world, step by step
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def apply_day(monkeypatch):
    monkeypatch.setattr(lovable_pilot, "_today", lambda: dt.date(2026, 11, 3))


@pytest.fixture
def piloted(world, pilot_on, monkeypatch):
    """The pilot by the real v1 importer and one COMPARE night; then a lawyer's
    step on 504 and a Valdkond and a document on 506. Native and same-reference
    Teemad beside them."""
    pilot = _pilot()
    _apply_pilot(pilot)
    monkeypatch.setattr(lovable_sync, "EXPECTED_COHORT", len(REBASED))
    lovable_sync.set_mode(SyncMode.COMPARE, reason="esimesed ööd")
    mari = world["mari"]
    native = factories.MatterFactory(
        reference_year=2026, reference_number=7, owner=mari, title="Oma näidisteema"
    )
    native.policy_areas.add(factories.PolicyAreaFactory())
    factories.NextActionFactory(matter=native)
    same = factories.MatterFactory(
        reference_year=2026, reference_number=515, title="Sama numbriga näidisteema"
    )
    fake = FakeLovable()
    for topic in pilot["topics"]:
        fake.rows[str(topic["source"]["id"])] = copy.deepcopy(topic["source"]["row"])
    fake.rows[_sid(SAME_REF)] = _full_row(SAME_REF, _sid(SAME_REF), 2)
    here: dict[str, Any] = {"source": fake, "world": world, "native": native, "same": same}
    night = _night(here, NIGHT)
    assert night.counts["checked"] == len(REBASED) and not night.decisions.exists()

    lawyer_step = set_next_action(
        matter=_matter(LAWYER_STEP),
        text="Juristi samm",
        kind="DO",
        date_semantics="DEADLINE",
        target_date=dt.date(2026, 12, 1),
        responsible=mari,
        actor=mari,
    )
    area = factories.PolicyAreaFactory()
    _matter(COMMENCEMENT).policy_areas.add(area)
    document = factories.DocumentFactory(matter=_matter(COMMENCEMENT))
    return {**here, "lawyer_step": lawyer_step, "area": area, "document": document}


@pytest.fixture
def rebuilt(piloted, tmp_path):
    """plan → apply, with the digest file `--preserved-out` writes."""
    package = _rebuild_package()
    plan = build_plan(package)
    assert plan.problems == [], plan.problems
    assert (plan.cohort, plan.database_state) == (OPERATIONAL_COHORT, "ready")
    report = apply_plan(
        plan,
        operator_intent=OPERATIONAL_COHORT,
        expect_package_sha256=plan.digest,
        expect_plan_sha256=plan_digest(plan),
        backup_set=BACKUP,
    )
    return {
        **piloted,
        "package": package,
        "plan": plan,
        "digest": plan.digest,
        "report": report,
        "preserved": _preserved_file(report, tmp_path / "preserved.json"),
        "pilot_states": _state_dump(
            list(LovableSyncState.objects.filter(cohort=PILOT_COHORT).values_list("pk", flat=True))
        ),
        "ledger": _ledger(),
    }


@pytest.fixture
def enrolled(rebuilt):
    out = io.StringIO()
    call_command(*_enroll_arguments(rebuilt["digest"]), stdout=out)
    _lovable_now(rebuilt["source"], rebuilt["package"])
    return {**rebuilt, "enrol_output": out.getvalue().strip()}


@pytest.fixture
def first_night(enrolled):
    before = _settled()
    run = _night(enrolled, AFTER)
    return {**enrolled, "first": run, "settled": (before, _settled())}


@pytest.fixture
def second_night(first_night):
    first_night["source"].edit(NEW_READING, tegevuse_tahtaeg="2027-01-10")
    first_night["source"].edit(UNCHANGED, tegevuse_tahtaeg="2027-01-12")
    before = _settled()
    run = _night(first_night, AFTER + DAY)
    return {**first_night, "second": run, "settled": (before, _settled())}


# ---------------------------------------------------------------------------
# Plan, apply, verify, preserved
# ---------------------------------------------------------------------------


def test_the_plan_reads_each_rebase_three_ways(piloted):
    plan = build_plan(_rebuild_package())
    assert plan.problems == [], plan.problems
    resolved = {
        t.reference: [(e["field"], e["action"]) for e in t.resolution]
        for t in plan.topics
        if t.action == "REBASE"
    }
    assert resolved == {
        UNCHANGED: [],
        NEW_READING: [("next_action", WRITE)],
        STAGE_MOVE: [("stage", WRITE)],
        LAWYER_STEP: [("next_action", KEEP_LOCAL)],
        FINISHED: [("stage", HOLD)],
        COMMENCEMENT: [("commencement_add", WRITE)],
    }
    [step] = next(t for t in plan.topics if t.reference == LAWYER_STEP).resolution
    assert (step["b"]["text"], step["c"]["text"]) == ("Juristi samm", "ootan Valitsuse otsust")
    assert step["a"]["text"] == "ootan 2. lugemist, vaata üle 15.11"
    assert NOTHING not in {a for pairs in resolved.values() for _, a in pairs}
    assert next(t for t in plan.topics if t.reference == "2026_603").successor_in_batch == (
        UNCHANGED
    )


def test_the_rebuild_writes_the_plan_and_keeps_local_work(rebuilt, capsys):
    report = rebuilt["report"]
    assert report.verify.ok, report.verify.discrepancies
    assert verify_applied(build_plan(rebuilt["package"])).ok
    # WRITE
    step = NextAction.objects.get(matter=_matter(NEW_READING), status=ActionStatus.OPEN)
    assert (step.text, step.target_date) == (NEW_STEP["text"], dt.date(2026, 12, 15))
    assert _matter(STAGE_MOVE).stage.key == "consultation"
    assert MatterImportantDate.objects.filter(
        matter=_matter(COMMENCEMENT), kind="PLANNED_COMMENCEMENT", date_value=dt.date(2027, 2, 1)
    ).exists()
    # KEEP_LOCAL and HOLD: the lawyer's step, the open Teema at its stage.
    kept = NextAction.objects.get(matter=_matter(LAWYER_STEP), status=ActionStatus.OPEN)
    assert kept.pk == rebuilt["lawyer_step"].pk
    assert _matter(FINISHED).is_open and _matter(FINISHED).stage.key == "parliament"
    # CREATE
    assert _matter("2026_601").is_open
    assert not _matter("2026_602").is_open and _matter("2026_602").closed_at is None
    assert _matter("2026_603").superseded_by_id == _matter(UNCHANGED).pk
    # Local work survived the write on the same Teema.
    assert list(_matter(COMMENCEMENT).policy_areas.all()) == [rebuilt["area"]]
    assert Document.objects.filter(pk=rebuilt["document"].pk).exists()

    # `preserved --before`, after the apply and before the enrolment.
    printed = _preserved(rebuilt["preserved"], capsys)
    assert (printed["checked"], printed["unchanged"], printed["changed"]) == (2, 2, [])
    assert (printed["rebased_checked"], printed["local_unchanged"]) == (6, 6)
    assert printed["local_changed"] == [] and printed["undeclared"] == []
    assert printed["declared_changes_only"] is True
    document = json.loads(rebuilt["preserved"].read_text(encoding="utf-8"))
    assert set(document["references"].values()) == {NATIVE, SAME_REF, *REBASED}
    assert "Juristi samm" not in rebuilt["preserved"].read_text(encoding="utf-8")


def test_preserved_runs_before_the_enrolment_not_after(rebuilt, capsys):
    """The documented order: the enrolment re-seeds each rebased Teema's
    synchronisation state, which its local digest covers."""
    assert _preserved(rebuilt["preserved"], capsys)["local_changed"] == []
    call_command(*_enroll_arguments(rebuilt["digest"]), stdout=io.StringIO())
    after = _preserved(rebuilt["preserved"], capsys)
    assert after["changed"] == [] and after["undeclared"] == []
    assert after["local_changed"] == sorted(REBASED)


# ---------------------------------------------------------------------------
# enroll --supersede lovable-pilot
# ---------------------------------------------------------------------------


def test_enrolment_archives_the_pilot_and_reseeds_the_same_rows(enrolled):
    assert enrolled["enrol_output"] == (
        "enrolled: cohort=lovable-operational expected=9 seeded=9 import_held_fields=3"
        " reseeded=6 superseded=lovable-pilot"
    )
    old = enrolled["pilot_states"]
    archives = {str(a.state_id): a for a in LovableSyncBaselineArchive.objects.all()}
    assert set(archives) == set(old) and len(old) == len(REBASED)
    for pk, before in old.items():
        archive = archives[pk]
        assert (archive.applied, archive.observed) == (before["applied"], before["observed"])
        assert (archive.applied_version, archive.applied_snapshot_sha256) == (
            before["applied_version"],
            before["applied_snapshot_sha256"],
        )
        assert (archive.observed_version, archive.observed_run_id) == (
            before["observed_version"],
            before["observed_run"],
        )
        assert (archive.cohort_before, archive.cohort_after) == (PILOT_COHORT, OPERATIONAL_COHORT)
    states = {s.reference: s for s in LovableSyncState.objects.all()}
    assert set(states) == {*REBASED, *CREATED}
    assert {str(states[r].pk) for r in REBASED} == set(old)  # the same rows
    assert {s.cohort for s in states.values()} == {OPERATIONAL_COHORT}
    assert {states[r].applied_snapshot_sha256 for r in REBASED} == {"c" * 64}
    assert lovable_sync.held_by_import(states[LAWYER_STEP].applied) == {
        "next_action_text",
        "next_action_date",
    }
    assert lovable_sync.held_by_import(states[FINISHED].applied) == {"stage"}
    # The KEEP_LOCAL step's baseline is the pilot's own step (the rebuild writes no
    # NEXT_ACTION record for a lawyer's step), so the lawyer's step keeps reading as
    # Juristid's own and never becomes the baseline (ADR 0202 §6, review of the rebuild).
    pilot_step = EvidencePilotRecord.objects.get(
        pilot=PILOT, matter=states[LAWYER_STEP].matter, record_kind="NEXT_ACTION"
    ).target_id
    target = states[LAWYER_STEP].applied["next_action"]["target"]
    assert target["id"] == str(pilot_step) != str(enrolled["lawyer_step"].pk)
    assert not EvidencePilotRecord.objects.filter(
        pilot=OPERATIONAL_COHORT, matter=states[LAWYER_STEP].matter, record_kind="NEXT_ACTION"
    ).exists()
    pilot = LovableSyncCohort.objects.get(key=PILOT_COHORT)
    assert pilot.retired_at is not None and enrolled["digest"] in pilot.retired_reason
    assert lovable_sync.expected_cohorts() == {OPERATIONAL_COHORT: 9}
    assert _ledger() == enrolled["ledger"]
    assert EvidencePilotRecord.objects.filter(pilot=PILOT).exists()
    assert not LovableSyncState.objects.filter(matter__in=[enrolled["native"], enrolled["same"]])


# ---------------------------------------------------------------------------
# The nights after the rebuild
# ---------------------------------------------------------------------------


def test_the_first_night_reports_only_the_rebuilds_holds(first_night):
    run = first_night["first"]
    assert run.counts["checked"] == 9
    assert set(run.counts["by_cohort"]) == {OPERATIONAL_COHORT}
    # Source authority (docs/adr/0213 §10): what the import held is no longer a
    # person's call. The lawyer's step (KEEP_LOCAL, held at the rebuild) converges
    # on Lovable's sentence, and 505 («jõustunud» in Lovable) closes — both only
    # planned on this COMPARE night, so nothing is written.
    assert _found(run, (*REBASED, *CREATED)) == {
        (LAWYER_STEP, "next_action_text", SyncOutcome.ELIGIBLE, ""),
        (LAWYER_STEP, "next_action_date", SyncOutcome.ELIGIBLE, ""),
        (FINISHED, "stage", SyncOutcome.ELIGIBLE, ""),
    }
    assert (run.counts["eligible_unapplied"], run.counts["conflicts"]) == (2, 0)
    part = run.counts["by_cohort"][OPERATIONAL_COHORT]
    assert (part["import_held"], part["import_held_fields"]) == (0, 0)
    assert part["stopped_active"] == 1  # 505: «jõustunud» in Lovable, open in Juristid
    # The run changed no Teema. A created Teema's synchronisation baseline may
    # settle on its first comparison (docs/adr/0213 §10: unchanged in Lovable and
    # already what Juristid holds); that is bookkeeping, not a business write.
    before, after = first_night["settled"]
    assert len(before) == 11
    assert {k: v for k, v in after.items() if k not in CREATED} == {
        k: v for k, v in before.items() if k not in CREATED
    }
    seen = first_night["requests"][-1]
    assert seen[0] == ("POST", "/auth/v1/token")
    assert {method for method, _ in seen[1:]} == {"GET"}


def test_the_second_night_reports_a_moved_date_without_writing(second_night):
    run = second_night["second"]
    for reference, day in ((NEW_READING, "2027-01-10"), (UNCHANGED, "2027-01-12")):
        date = _decisions(run, reference)["next_action_date"]
        assert (date.outcome, date.source) == (SyncOutcome.ELIGIBLE, day), reference
    assert NextAction.objects.get(
        matter=_matter(NEW_READING), status=ActionStatus.OPEN
    ).target_date == dt.date(2026, 12, 15)
    before, after = second_night["settled"]
    assert after == before


def test_uuendused_shows_created_teemad_entering_and_rebased_ones_compared(second_night):
    reader = factories.UserFactory(display_name="Lugeja Näidis")
    history = lovable_review.nightly_history(reader, now=AFTER + 3 * DAY)
    second, first, pilot_night = history.nights[:3]
    assert pilot_night.comparison == "BASELINE"
    kinds = {t.reference: t.kind for t in first.topics}
    assert {r for r, k in kinds.items() if k == lovable_review.ENROLLED} == set(CREATED)
    assert first.enrolled == 3
    # A rebased Teema never «Lisandus võrdlusesse»: it was in the pilot's reading.
    assert not {r for r, k in kinds.items() if k == lovable_review.ENROLLED} & set(REBASED)
    fields = {(t.reference, f.name): f for t in first.topics for f in t.fields}
    step = fields[(NEW_READING, "next_action_text")]
    assert (step.result, step.note) == (lovable_review.SAME, lovable_review.REBASED_NOTE)
    # Planned under source authority, not written on a COMPARE night.
    assert fields[(FINISHED, "stage")].result == lovable_review.NOT_APPLIED
    assert fields[(LAWYER_STEP, "next_action_text")].result == lovable_review.NOT_APPLIED

    assert {t.reference: t.kind for t in second.topics} == {
        NEW_READING: "CHANGED",
        UNCHANGED: "CHANGED",
    }
    assert second.enrolled == 0
    for topic in second.topics:
        [date] = topic.fields
        assert (date.name, date.result) == ("next_action_date", lovable_review.NOT_APPLIED)


# ---------------------------------------------------------------------------
# Again, and refusals
# ---------------------------------------------------------------------------


def test_the_same_rebuild_and_enrolment_again_change_nothing(first_night):
    counts = (EvidencePilotRecord.objects.count(), NextAction.objects.count())
    settled = _settled()
    plan = build_plan(first_night["package"])
    assert plan.database_state == "applied", plan.problems
    again = apply_plan(
        plan,
        operator_intent=OPERATIONAL_COHORT,
        expect_package_sha256=plan.digest,
        expect_plan_sha256=plan_digest(plan),
        backup_set=BACKUP,
    )
    assert again.already_applied
    out = io.StringIO()
    call_command(*_enroll_arguments(first_night["digest"]), stdout=out)
    assert out.getvalue().startswith("already enrolled: cohort=lovable-operational expected=9")
    assert (EvidencePilotRecord.objects.count(), NextAction.objects.count()) == counts
    assert LovableSyncBaselineArchive.objects.count() == len(REBASED)
    assert _settled() == settled


def test_a_lawyer_save_after_the_plan_refuses_the_apply(piloted):
    package = _rebuild_package()
    plan = build_plan(package)
    approved = plan_digest(plan)
    # The WRITE on 502 relies on Juristid still holding the pilot's step.
    set_next_action(
        matter=_matter(NEW_READING),
        text="Juristi uus samm",
        kind="DO",
        date_semantics="DEADLINE",
        target_date=dt.date(2026, 12, 3),
        responsible=piloted["world"]["jaan"],
        actor=piloted["world"]["jaan"],
    )
    before = preserved_digests()
    with pytest.raises(LovablePilotError, match="changed since it was approved"):
        apply_plan(
            plan,
            operator_intent=OPERATIONAL_COHORT,
            expect_package_sha256=plan.digest,
            expect_plan_sha256=approved,
            backup_set=BACKUP,
        )
    assert preserved_digests() == before
    assert not EvidencePilotRecord.objects.filter(pilot=OPERATIONAL_COHORT).exists()
    assert not Matter.all_objects.filter(reference_number__in=(601, 602, 603)).exists()


def test_a_digest_snapshot_at_the_backup_proves_a_restore(piloted, tmp_path, capsys):
    """`preserved --snapshot-out` at the backup; the rebuild; a restore (here, the
    transaction rolled back); `preserved --before` holds — and without the
    restore it names every rebased Teema."""
    at_backup = tmp_path / "at-backup.json"
    call_command("lovable_pilot", "preserved", "--snapshot-out", str(at_backup), "--json")
    assert json.loads(capsys.readouterr().out)["matters"] == 8

    class Restored(Exception):
        pass

    package = _rebuild_package()
    with pytest.raises(Restored), transaction.atomic():
        plan = build_plan(package)
        apply_plan(
            plan,
            operator_intent=OPERATIONAL_COHORT,
            expect_package_sha256=plan.digest,
            expect_plan_sha256=plan_digest(plan),
            backup_set=BACKUP,
        )
        assert Matter.all_objects.filter(reference_number=601).exists()
        raise Restored
    restored = _preserved(at_backup, capsys)
    assert (restored["checked"], restored["unchanged"], restored["changed"]) == (8, 8, [])

    plan = build_plan(package)
    apply_plan(
        plan,
        operator_intent=OPERATIONAL_COHORT,
        expect_package_sha256=plan.digest,
        expect_plan_sha256=plan_digest(plan),
        backup_set=BACKUP,
    )
    not_restored = _preserved(at_backup, capsys)
    assert set(not_restored["changed"]) == set(REBASED)
