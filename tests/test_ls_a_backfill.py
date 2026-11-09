"""The gated historical backfill (LS-A): proven-absent, non-operational topics only,
created closed through the existing importer, beside the live register.

The machine reader (agent B's `lovable_reading.read_topic`, COMMON interface 1) is
not in this tree: a minimal local fake with the same signature stands in. Every
title, name, reference and identifier below is synthetic.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
from collections.abc import Mapping
from typing import Any

import pytest
from django.core.management import CommandError, call_command

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.legacy_import import lovable_backfill, lovable_pilot
from app.legacy_import.lovable_backfill import BackfillError, build_package
from app.legacy_import.lovable_inventory import Category, build_ledger
from app.legacy_import.lovable_pilot import (
    HISTORY_COHORT,
    LovablePilotError,
    package_digest,
    plan_digest,
    validate_package,
)
from app.legacy_import.models import EvidencePilotRecord, MatterSourceReference
from app.matters.models import Matter
from app.workflow.models import NextAction
from tests.test_ls_a_inventory import (
    FakeLovable,
    enrol_register,
    reading,
    register_teema,
    row_of,
    sid,
    tombstone,
    topic,
)

pytestmark = pytest.mark.django_db

BACKUP = "sets/20261109T060000Z"
READER_VERSION = "fake-reader/1"


def fake_read_topic(
    bundle: dict[str, Any], *, as_of: dt.date, people: Mapping[str, str], historical: bool
) -> dict[str, Any]:
    """COMMON interface 1, minimally: a finished topic read conservatively."""
    assert historical is True
    row = bundle["row"]
    if row.get("teema") == "LOEMATU":
        raise ValueError("the reader cannot read this topic")
    return {
        "reference": row["nr"],
        "action": "CREATE",
        "group": "HISTORY",
        "title": row["teema"],
        "owner_raw": row.get("vastutaja") or "",
        "status_raw": row.get("hetkeseis") or "",
        "stage": "",
        "lifecycle": "INACTIVE",
        "disposition": "COMPLETED",
        "successor": "",
        "koda_initiative": bool(row.get("koja_ettepanek")),
        "instruments": [],
        "received_date": row.get("sisse"),
        "addressee_raw": "",
        "next_action": None,
        "deadline": {"treatment": "NONE"},
        "commencements": [],
        "onenote_url": row.get("onenote_link") or "",
        "field_decisions": {"hetkeseis": "HELD_FOR_REVIEW"},
        "review_notes": ["stage held: no verified mapping in the fake reader"],
        "source": {
            "id": row["id"],
            "version": row.get("version"),
            "updated_at": row.get("updated_at"),
            "row": row,
            "excel_source_row": None,
            "history": [],
            "commencements": bundle.get("commencements") or [],
            "responsible_changes": bundle.get("responsible_changes") or [],
            "stage_links": bundle.get("stage_links") or [],
        },
    }


def active_reader(bundle, *, as_of, people, historical):
    topic_ = fake_read_topic(bundle, as_of=as_of, people=people, historical=historical)
    topic_["lifecycle"], topic_["disposition"] = "ACTIVE", ""
    return topic_


@pytest.fixture(autouse=True)
def _fake_reader_is_this_candidates(monkeypatch):
    """`plan` refuses a package read by another reader version (STALE_READER); in
    this module the fake reader stands in for the candidate's own."""
    monkeypatch.setattr(
        lovable_backfill, "_default_reader", lambda: (fake_read_topic, READER_VERSION)
    )


@pytest.fixture
def pilot_on(settings):
    settings.LOVABLE_PILOT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


@pytest.fixture
def world(db):
    """A live register Teema (operational, exact) beside the historical source."""
    enrol_register()
    fake = FakeLovable(
        [
            topic(1, operational=True, hetkeseis="Riigikogus"),  # EXACT, the register
            topic(2, nr="2019_12", aasta=2019, title="Ajaloolise näidise teema A"),
            topic(3, nr="2020_7", aasta=2020, title="Ajaloolise näidise teema B"),
            topic(4, nr="2021_3", aasta=2021, title="Ajaloolise näidise teema C"),
            topic(5, operational=True, hetkeseis="Riigikogus", title="Uus operatiivne"),
            topic(6, nr="2018_40", aasta=2018, title="Hauakivi näidise teema"),
            topic(7, nr="2017_2", aasta=2017, title="LOEMATU"),
        ]
    )
    register = register_teema(1)
    tombstone(lovable_id=sid(6), reference="2018_40")
    document = reading(fake)
    return {"fake": fake, "register": register, "document": document}


def _selection(world, reader=fake_read_topic):
    ledger = build_ledger(world["document"])
    return ledger, build_package(
        world["document"], ledger, reader=reader, reader_version=READER_VERSION
    )


def _apply(package: dict[str, Any], **overrides: Any):
    plan = lovable_backfill.plan(package)
    arguments = {
        "operator_intent": HISTORY_COHORT,
        "expect_package_sha256": plan.digest,
        "expect_plan_sha256": plan_digest(plan),
        "backup_set": BACKUP,
        **overrides,
    }
    return lovable_backfill.apply(package, **arguments)


# ---------------------------------------------------------------------------
# The importer, before and after the one edit
# ---------------------------------------------------------------------------


def test_the_importer_knows_the_history_cohort_only_as_a_version_2_package():
    package = {
        "format": lovable_pilot.PACKAGE_FORMAT,
        "format_version": lovable_pilot.PACKAGE_FORMAT_VERSION,
        "cohort": HISTORY_COHORT,
        "snapshot": {"as_of": "2026-11-09", "snapshot_sha256": "a" * 64},
        "expected_topics": 0,
        "topics": [],
    }
    problems = validate_package(package).problems
    assert any("format_version 1 is the lovable-pilot cohort's" in p for p in problems)


# ---------------------------------------------------------------------------
# Selection: absent, historical, not on a tombstone — everything else listed
# ---------------------------------------------------------------------------


def test_the_package_holds_only_proven_absent_historical_topics(world):
    ledger, selection = _selection(world)
    assert row_of(ledger, 5).category == Category.ABSENT and row_of(ledger, 5).operational
    assert selection.selected == ["2019_12", "2020_7", "2021_3"]
    reasons = {item["reference"]: item["reason"] for item in selection.left_out}
    assert reasons == {
        "2026_905": "OPERATIONAL",  # left to the nightly new-topic synchronisation
        "2018_40": "TOMBSTONE_HELD",
        "2017_2": "READER_REFUSED",
    }
    assert "2026_901" not in reasons  # EXACT rows are simply not candidates
    package = selection.package
    assert package["cohort"] == HISTORY_COHORT and package["format_version"] == 2
    assert package["expected_topics"] == 3
    assert package["snapshot"]["snapshot_sha256"] == world["document"]["sha256"]
    assert package["selection"]["ledger_sha256"] == ledger.sha256()
    assert all(t["action"] == "CREATE" and t["lifecycle"] != "ACTIVE" for t in package["topics"])
    assert lovable_backfill.validate(package) == []


def test_a_topic_the_reader_makes_active_is_left_out_never_forced(world):
    _ledger, selection = _selection(world, reader=active_reader)
    assert selection.package is None
    assert {i["reason"] for i in selection.left_out} >= {"NOT_HISTORICAL"}


def test_a_ledger_from_another_reading_is_refused(world):
    ledger = build_ledger(world["document"])
    other = copy.deepcopy(world["document"])
    other["sha256"] = "f" * 64
    with pytest.raises(BackfillError, match="not built from this reading"):
        build_package(other, ledger, reader=fake_read_topic)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda p: p.update(cohort="lovable-operational"), "names the lovable-history cohort"),
        (lambda p: p.update(replace=True), "never replaces"),
        (lambda p: p["topics"][0].update(action="REBASE"), "an explicit CREATE"),
        (
            lambda p: p["topics"][0].update(lifecycle="ACTIVE", disposition=""),
            "created closed",
        ),
        (
            lambda p: p["topics"][0]["source"]["row"].update(operatiivne_ulatus=True),
            "nightly synchronisation's",
        ),
        (lambda p: p.update(reserve_through={"2026": 999}), "moves no reference sequence"),
    ],
)
def test_the_backfill_refuses_anything_but_a_historical_create_package(world, change, message):
    _ledger, selection = _selection(world)
    package = copy.deepcopy(selection.package)
    change(package)
    assert any(message in p for p in lovable_backfill.package_problems(package))
    plan = lovable_backfill.plan(package)
    assert any(message in p for p in plan.problems)


# ---------------------------------------------------------------------------
# dry-run, apply, verify — the importer's gates
# ---------------------------------------------------------------------------


def test_the_dry_run_creates_every_topic_then_rolls_back(world, pilot_on):
    _ledger, selection = _selection(world)
    before = Matter.all_objects.count()
    report = lovable_backfill.dry_run(selection.package)
    assert report.dry_run is True
    assert report.written["MATTER"] == 3
    assert report.verify.ok, report.verify.discrepancies
    assert report.fingerprint_before == report.fingerprint_after
    assert Matter.all_objects.count() == before
    assert not EvidencePilotRecord.objects.filter(pilot=HISTORY_COHORT).exists()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"operator_intent": "lovable-register"}, "--operator-intent lovable-history"),
        ({"backup_set": ""}, "Name the verified backup set"),
        ({"expect_package_sha256": "0" * 64}, "pass exactly that as --expect-package-sha256"),
        ({"expect_plan_sha256": "0" * 64}, "plan"),
    ],
)
def test_the_apply_refuses_without_every_gate(world, pilot_on, override, message):
    _ledger, selection = _selection(world)
    before = Matter.all_objects.count()
    with pytest.raises(LovablePilotError, match=message):
        _apply(selection.package, **override)
    assert Matter.all_objects.count() == before


def test_the_apply_refuses_without_the_flags(world, settings):
    settings.LOVABLE_PILOT_ENABLED = False
    _ledger, selection = _selection(world)
    with pytest.raises(LovablePilotError, match="JURISTID_LOVABLE_PILOT"):
        _apply(selection.package)
    settings.LOVABLE_PILOT_ENABLED, settings.REAL_DATA_ALLOWED = True, False
    with pytest.raises(LovablePilotError, match="REAL_DATA_ALLOWED is off"):
        _apply(selection.package)
    assert not Matter.all_objects.filter(evidence_pilot_records__pilot=HISTORY_COHORT).exists()


def test_the_apply_creates_closed_history_and_touches_no_existing_teema(world, pilot_on):
    _ledger, selection = _selection(world)
    register = world["register"]
    register_before = Matter.all_objects.filter(pk=register.pk).values().get()
    preserved = lovable_pilot.preserved_snapshot()
    before = Matter.all_objects.count()

    report = _apply(selection.package)
    assert report.written["MATTER"] == 3 and report.verify.ok
    assert report.fingerprint_before == report.fingerprint_after
    assert Matter.all_objects.count() == before + 3

    created = Matter.objects.filter(
        evidence_pilot_records__pilot=HISTORY_COHORT, evidence_pilot_records__record_kind="MATTER"
    )
    assert sorted(m.display_reference for m in created) == ["2019_12", "2020_7", "2021_3"]
    for matter in created:
        assert matter.is_open is False and matter.record_mode == "ARCHIVE"
        assert matter.closed_at is None and matter.disposition == "COMPLETED"
        assert matter.owner_id is None  # a departed owner stays source text
        assert matter.shows_register_archive_notice is False  # an imported register row
    assert not NextAction.objects.filter(matter__in=created).exists()
    assert not ChangeEvent.objects.filter(
        matter__in=created, event_type=ChangeEventType.MATTER_CLOSED
    ).exists()
    # The Lovable identity travels in the canonical reference, byte for byte.
    ids = set(
        MatterSourceReference.objects.filter(matter__in=created).values_list(
            "source_row_raw__lovable__id", flat=True
        )
    )
    assert ids == {sid(2), sid(3), sid(4)}

    # Nothing that was there before moved.
    assert Matter.all_objects.filter(pk=register.pk).values().get() == register_before
    assert lovable_pilot.check_preserved(preserved).ok
    assert lovable_backfill.verify(selection.package).ok

    # A second apply writes nothing and says so.
    again = _apply(selection.package)
    assert again.already_applied is True
    assert Matter.all_objects.count() == before + 3

    # The next inventory sees them as exact matches, not yet enrolled.
    ledger = build_ledger(world["document"])
    assert {row_of(ledger, n).reason for n in (2, 3, 4)} == {"EXACT_NOT_ENROLLED"}
    assert row_of(ledger, 1).reason == "EXACT_ENROLLED"


def test_a_package_that_names_an_existing_teemas_identity_is_refused(world, pilot_on):
    _ledger, selection = _selection(world)
    package = copy.deepcopy(selection.package)
    package["topics"][0]["source"]["id"] = sid(1)  # the register Teema's identity
    plan = lovable_backfill.plan(package)
    assert any("already in Juristid under their Lovable identity" in p for p in plan.problems)
    with pytest.raises(LovablePilotError, match="The plan has problems"):
        lovable_pilot.dry_run(plan)


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_the_backfill_command_package_and_plan_steps_are_read_only(world, tmp_path, monkeypatch):
    monkeypatch.setattr(
        lovable_backfill, "_default_reader", lambda: (fake_read_topic, READER_VERSION)
    )
    saved = tmp_path / "reading.json"
    saved.write_text(json.dumps(world["document"], ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "backfill"
    before = Matter.all_objects.count()
    call_command(
        "lovable_inventory", "backfill", "--step", "package", "--document", str(saved),
        "--out", str(out),
    )  # fmt: skip
    package = json.loads((out / "package.json").read_text(encoding="utf-8"))
    report = json.loads((out / "backfill-report.json").read_text(encoding="utf-8"))
    assert report["selected"] == 3 and report["package_sha256"] == package_digest(package)
    call_command("lovable_inventory", "backfill", "--step", "validate", "--package", str(out))
    call_command("lovable_inventory", "backfill", "--step", "plan", "--package", str(out))
    assert Matter.all_objects.count() == before
    with pytest.raises(CommandError, match="--operator-intent lovable-history"):
        call_command(
            "lovable_inventory", "backfill", "--step", "apply", "--package", str(out),
            "--operator-intent", "lovable-pilot",
        )  # fmt: skip


# ---------------------------------------------------------------------------
# Decisions and archive Teemad (review P2)
# ---------------------------------------------------------------------------


def test_decided_topics_stay_out_and_the_package_binds_the_decisions(world, pilot_on):
    from app.legacy_import.lovable_inventory_models import InventoryDecisionKind
    from tests.test_ls_a_inventory import decide

    decide(2, InventoryDecisionKind.EXCLUDE, reason="ei ole registriteema")
    decide(3, InventoryDecisionKind.LINK, matter=world["register"])
    ledger, selection = _selection(world)
    reasons = {item["reference"]: item["reason"] for item in selection.left_out}
    assert reasons["2019_12"] == "DECIDED_EXCLUDE" and reasons["2020_7"] == "DECIDED_LINK"
    assert selection.selected == ["2021_3"]
    package = selection.package
    assert package["selection"]["decisions_sha256"] == ledger.decisions_sha256
    assert len(package["selection"]["decisions"]) == 2
    assert lovable_backfill.plan(package).problems == []

    # A later decision: this package is refused, and the next one differs.
    decide(4, InventoryDecisionKind.EXCLUDE)
    plan = lovable_backfill.plan(package)
    assert any(lovable_backfill.DECISIONS_CHANGED in p for p in plan.problems)
    with pytest.raises(LovablePilotError, match="The plan has problems"):
        lovable_pilot.dry_run(plan)
    _ledger, again = _selection(world)
    assert again.package is None  # 2021_3 is now excluded too
    assert {i["reason"] for i in again.left_out} >= {"DECIDED_EXCLUDE", "DECIDED_LINK"}


def test_a_package_without_bound_decisions_is_refused(world):
    _ledger, selection = _selection(world)
    package = copy.deepcopy(selection.package)
    package["selection"].pop("decisions_sha256")
    assert any(
        lovable_backfill.DECISIONS_CHANGED in p for p in lovable_backfill.plan(package).problems
    )


def test_an_archive_teema_with_a_lookalike_title_is_never_duplicated_by_the_backfill(world):
    from tests.test_ls_a_inventory import unreferenced_teema

    unreferenced_teema("Ajaloolise näidise teema B", era="2020")
    _ledger, selection = _selection(world)
    assert "2020_7" not in selection.selected
    assert selection.selected == ["2019_12", "2021_3"]
