"""The rebuild of the Lovable-derived Teemad from one snapshot, in place
(docs/adr/0202 §1–§5), on synthetic data.

A six-Teema pilot world — one step a lawyer replaced, one Teema with a Valdkond,
a document and a renamed title — is brought to a newer snapshot by one package:
CREATE topics (ongoing, finished, a predecessor continuing under a rebased
Teema) and REBASE entries that WRITE where Juristid still holds what the pilot
wrote, keep a lawyer's step (KEEP_LOCAL), take a source title over a lawyer's
(TAKE_SOURCE), hold a stage that would close the Teema (HOLD), and leave an
unchanged Teema alone. Every title, name, reference and identity is invented.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
from typing import Any

import pytest
from django.core.management import CommandError, call_command

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.documents.models import Document
from app.intelligence.models import MatterImportantDate
from app.legacy_import import lovable_pilot, lovable_sync
from app.legacy_import.lovable_pilot import (
    HOLD,
    KEEP_LOCAL,
    NOTHING,
    OPERATIONAL_COHORT,
    PILOT,
    TAKE_SOURCE,
    WRITE,
    LovablePilotError,
    apply_plan,
    build_plan,
    derived_values,
    local_digests,
    plan_digest,
    preserved_digests,
    validate_package,
    verify_applied,
)
from app.legacy_import.models import EvidencePilotRecord, ImportBatch, MatterSourceReference
from app.matters.models import Matter
from app.matters.services import set_matter_title
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from app.workflow.services import set_next_action
from tests import factories
from tests.source_ownership_support import lock_lifted_for_the_engine_tests  # noqa: F401 - autouse
from tests.test_lovable_operational_import import _active, _sid
from tests.test_lovable_pilot import _package as _pilot_package
from tests.test_lovable_pilot import _topic

AS_OF = "2026-11-03T05:00:00+00:00"
BACKUP = "sets/20261103T120000Z"
PILOT_PLAN_BACKUP = "sets/20261026T160000Z"


def _pilot_id(reference: str) -> str:
    """The identity `tests.test_lovable_pilot._topic` gives a pilot topic."""
    return f"00000000-0000-0000-0000-{int(reference[5:]):012d}"


@pytest.fixture(autouse=True)
def apply_day(monkeypatch):
    day = {"today": dt.date(2026, 11, 3)}
    monkeypatch.setattr(lovable_pilot, "_today", lambda: day["today"])
    return day


@pytest.fixture
def pilot_on(settings):
    settings.LOVABLE_PILOT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


def _pilot_world_package() -> dict[str, Any]:
    """The 0191 synthetic pilot, plus two more open Teemad (505, 506)."""
    package = _pilot_package()
    extra = [
        _topic("2026_505"),
        _topic("2026_506", owner_raw="Jaan", stage="consultation"),
    ]
    package["topics"] += extra
    package["expected_topics"] = len(package["topics"])
    return package


@pytest.fixture
def world(db, pilot_on):
    mari = factories.UserFactory(upn="mari@example.invalid", display_name="Mari Näidis")
    jaan = factories.UserFactory(upn="jaan@example.invalid", display_name="Jaan Näidis")
    kati = factories.UserFactory(upn="kati@example.invalid", display_name="Kati Näidis")
    pilot = _pilot_world_package()
    plan = build_plan(pilot)
    assert plan.problems == [], plan.problems
    apply_plan(
        plan,
        operator_intent=PILOT,
        expect_package_sha256=plan.digest,
        expect_plan_sha256=plan_digest(plan),
        backup_set=PILOT_PLAN_BACKUP,
    )
    for reference in MatterSourceReference.objects.filter(source_system="LOVABLE"):
        lovable_sync.seed_state(reference)
    by_ref = {m.display_reference: m for m in Matter.all_objects.all()}
    # A lawyer replaced 505's step; somebody worked on 506.
    set_next_action(
        matter=by_ref["2026_505"],
        text="Juristi samm",
        kind="DO",
        date_semantics="DEADLINE",
        target_date=dt.date(2026, 12, 1),
        responsible=mari,
        actor=mari,
    )
    area = factories.PolicyAreaFactory()
    by_ref["2026_506"].policy_areas.add(area)
    document = factories.DocumentFactory(matter=by_ref["2026_506"])
    set_matter_title(matter=by_ref["2026_506"], value="Juristi pealkiri", actor=jaan)
    return {
        "mari": mari,
        "jaan": jaan,
        "kati": kati,
        "by_ref": by_ref,
        "area": area,
        "document": document,
    }


def _rebase(reference: str, changes: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    topic = {
        "action": "REBASE",
        "reference": reference,
        "title": extra.pop("title", f"Näidisteema {reference}"),
        "source": {
            "id": _pilot_id(reference),
            "version": 7,
            "updated_at": "2026-11-02T10:00:00+00:00",
            "row": {
                "id": _pilot_id(reference),
                "nr": reference,
                "aasta": 2026,
                "teema": f"Näidisteema {reference}",
                "hetkeseis": "Riigikogus",
                "jargmiseks": "ootan",
                "version": 7,
            },
            "commencements": [],
            "responsible_changes": [],
            "stage_links": [],
        },
        "onenote_url": "https://example.invalid/naidis/rebase",
        "field_decisions": {},
        "review_notes": [],
        "changes": changes,
    }
    # What a reviewer keeps or holds is held for review on its Lovable column.
    for change in changes:
        if change.get("decision") in {KEEP_LOCAL, HOLD} and change["field"] in (
            lovable_pilot.REBASE_HELD_COLUMNS
        ):
            column = lovable_pilot.REBASE_HELD_COLUMNS[change["field"]][0]
            topic["field_decisions"][column] = "HELD_FOR_REVIEW"
            topic["review_notes"].append({"field": column, "reason": change["reason"]})
    topic.update(extra)
    return topic


def _change(field: str, reading: Any, decision: str | None = None) -> dict[str, Any]:
    change = {"field": field, "reading": reading, "reason": f"reviewed {field}"}
    if decision:
        change["decision"] = decision
    return change


NEW_STEP = {
    "review": "READY",
    "kind": "WAIT",
    "date_semantics": "REVIEW_ON",
    "target_date": "2026-12-15",
    "date_precision": "EXACT",
    "text": "ootan Riigikogu otsust",
    "source_text": "ootan Riigikogu otsust",
    "treatment": "SOURCE_NEXT_STEP",
    "reason": "WAIT with a review date",
}


def _created_ongoing() -> dict[str, Any]:
    topic = _active("2026_601")
    topic["next_action"]["target_date"] = "2026-11-20"
    return topic


def _created_finished() -> dict[str, Any]:
    topic = _active(
        "2026_602",
        status_raw="jõustunud",
        stage="in_force",
        lifecycle="INACTIVE",
        disposition="COMPLETED",
        next_action=None,
        owner_raw="",
    )
    topic["source"]["row"]["hetkeseis"] = "jõustunud"
    return topic


def _created_predecessor() -> dict[str, Any]:
    """A finished topic continuing under the rebased 2026_501 (by its identity)."""
    topic = _active(
        "2026_603",
        status_raw="rohkem ei tegele",
        stage="monitoring_stopped",
        lifecycle="CONTINUES",
        disposition="SUPERSEDED",
        successor="2026_501",
        next_action=None,
        owner_raw="",
    )
    topic["source"]["row"]["hetkeseis"] = "rohkem ei tegele"
    topic["source"]["successor_id"] = _pilot_id("2026_501")
    return topic


def _rebuild(*topics: dict[str, Any]) -> dict[str, Any]:
    topics = topics or (
        _created_ongoing(),
        _created_finished(),
        _created_predecessor(),
        # WRITE: Juristid still holds what the pilot wrote.
        _rebase(
            "2026_501",
            [
                _change("title", "Näidisteema 2026_501 (uus)"),
                _change("stage", "consultation"),
                _change("open_request", {"deadline": "2026-11-25"}),
            ],
            title="Näidisteema 2026_501 (uus)",
        ),
        # Unchanged since the pilot: nothing to write.
        _rebase("2026_502", []),
        # HOLD: a stage that would close the Teema is never inferred.
        _rebase("2026_503", [_change("stage", "monitoring_stopped", HOLD)]),
        # KEEP_LOCAL: a lawyer replaced the step; Lovable changed it too.
        _rebase("2026_505", [_change("next_action", NEW_STEP, KEEP_LOCAL)]),
        # TAKE_SOURCE over a lawyer's title; a new step and a commencement WRITE.
        _rebase(
            "2026_506",
            [
                _change("title", "Lovable'i uus pealkiri", TAKE_SOURCE),
                _change("next_action", NEW_STEP),
                _change(
                    "commencement_add",
                    {
                        "key": "2026_506-C09",
                        "review": "READY",
                        "target": "MatterImportantDate",
                        "kind": "PLANNED_COMMENCEMENT",
                        "date": "2027-02-01",
                        "date_precision": "EXACT",
                        "note": "",
                    },
                ),
            ],
            title="Lovable'i uus pealkiri",
        ),
    )
    return {
        "format": lovable_pilot.PACKAGE_FORMAT,
        "format_version": lovable_pilot.ACTIVE_FORMAT_VERSION,
        "cohort": OPERATIONAL_COHORT,
        "reviewer": "näidisülevaataja",
        "snapshot": {
            "as_of": AS_OF,
            "snapshot_sha256": "c" * 64,
            "project": "näidis",
            "highest_reference": {"2026": 640},
        },
        "people": {"Mari": "Mari Näidis", "Jaan": "Jaan Näidis", "Kati": "Kati Näidis"},
        "reserve_through": {"2026": 640},
        "expected_topics": len(topics),
        "topics": list(topics),
    }


def _apply(package: dict[str, Any], **overrides: Any):
    plan = build_plan(package)
    arguments = {
        "operator_intent": OPERATIONAL_COHORT,
        "expect_package_sha256": plan.digest,
        "expect_plan_sha256": plan_digest(plan),
        "backup_set": BACKUP,
        **overrides,
    }
    return apply_plan(plan, **arguments)


def _actions(plan) -> dict[str, list[tuple[str, str]]]:
    return {
        t.reference: [(e["field"], e["action"]) for e in t.resolution]
        for t in plan.topics
        if t.action == "REBASE"
    }


# ---------------------------------------------------------------------------
# The three-way plan
# ---------------------------------------------------------------------------


def test_the_rebuild_validates():
    assert validate_package(_rebuild()).problems == []


def test_the_plan_resolves_every_change_three_ways(world):
    plan = build_plan(_rebuild())
    assert plan.problems == [], plan.problems
    assert plan.database_state == "ready"
    assert _actions(plan) == {
        "2026_501": [("title", WRITE), ("stage", WRITE), ("open_request", WRITE)],
        "2026_502": [],
        "2026_503": [("stage", HOLD)],
        "2026_505": [("next_action", KEEP_LOCAL)],
        "2026_506": [
            ("title", TAKE_SOURCE),
            ("next_action", WRITE),
            ("commencement_add", WRITE),
        ],
    }
    by_ref = {t.reference: t for t in plan.topics}
    # A is what the pilot wrote, B what Juristid holds now.
    title_506 = by_ref["2026_506"].resolution[0]
    assert (title_506["a"], title_506["b"]) == ("Näidisteema 2026_506", "Juristi pealkiri")
    stage_501 = by_ref["2026_501"].resolution[1]
    assert (stage_501["a"], stage_501["b"], stage_501["c"]) == (
        "parliament",
        "parliament",
        "consultation",
    )
    step_505 = by_ref["2026_505"].resolution[0]
    assert step_505["a"]["text"] == "ootan 2. lugemist, vaata üle 15.11"
    assert step_505["b"]["text"] == "Juristi samm"
    assert plan.topics[2].successor_in_batch == "2026_501"
    assert plan.expected_writes() == {
        "MATTER": 3,
        "REBASE": 5,
        "TITLE": 2,
        "STAGE": 1,
        "RESPONSE_REQUEST": 1,
        # 601's, and each rebased Teema whose step is the rebuild's: 501 and 502 keep the
        # pilot's untouched step, 506 gets a new one; 505's is the lawyer's own.
        "NEXT_ACTION": 4,
        "MatterImportantDate": 1,
        "CONTINUATION": 1,
    }


def test_a_conflict_without_a_decision_is_a_plan_problem(world):
    package = _rebuild()
    package["topics"][6]["changes"][0].pop("decision")
    plan = build_plan(package)
    assert (
        "2026_505: next_action was changed in Juristid and in Lovable; the package needs a "
        "decision (KEEP_LOCAL, TAKE_SOURCE or HOLD)." in plan.problems
    )
    assert not any("Juristi samm" in p or "Näidisteema" in p for p in plan.problems)


def test_a_source_value_equal_to_the_pilots_is_nothing(world):
    package = _rebuild(_rebase("2026_501", [_change("title", "Näidisteema 2026_501")]))
    plan = build_plan(package)
    assert plan.problems == []
    assert _actions(plan) == {"2026_501": [("title", NOTHING)]}


def test_keep_local_and_hold_override_a_write(world):
    package = _rebuild(
        _rebase(
            "2026_501",
            [_change("title", "Uus", KEEP_LOCAL), _change("stage", "consultation", HOLD)],
            title="Uus",
        )
    )
    assert _actions(build_plan(package)) == {"2026_501": [("title", KEEP_LOCAL), ("stage", HOLD)]}


def test_a_write_to_a_closed_teema_is_a_plan_problem(world):
    package = _rebuild(_rebase("2026_503", [_change("stage", "consultation")]))
    plan = build_plan(package)
    assert any("2026_503: stage cannot be written on a closed Teema" in p for p in plan.problems)


# ---------------------------------------------------------------------------
# The apply
# ---------------------------------------------------------------------------


def test_the_rebuild_writes_the_plan_and_nothing_else(world):
    by_ref = world["by_ref"]
    rebased_ids = [
        by_ref[r].pk for r in ("2026_501", "2026_502", "2026_503", "2026_505", "2026_506")
    ]
    untouched_before = preserved_digests()
    steps_before = {
        r: derived_values(by_ref[r])["next_action_id"] for r in ("2026_501", "2026_502", "2026_505")
    }

    report = _apply(_rebuild())
    assert report.verify.ok, report.verify.discrepancies
    m = {ref: Matter.all_objects.get(pk=matter.pk) for ref, matter in by_ref.items()}

    # WRITE
    assert m["2026_501"].title == "Näidisteema 2026_501 (uus)"
    assert m["2026_501"].stage.key == "consultation"
    assert m["2026_501"].response_deadline == dt.date(2026, 11, 25)
    # unchanged, HOLD, KEEP_LOCAL
    assert derived_values(m["2026_502"])["next_action_id"] == steps_before["2026_502"]
    assert m["2026_503"].stage.key == "in_force" and not m["2026_503"].is_open
    assert NextAction.objects.get(matter=m["2026_505"], status=ActionStatus.OPEN).text == (
        "Juristi samm"
    )
    # TAKE_SOURCE, a new step superseding the old, an added commencement
    assert m["2026_506"].title == "Lovable'i uus pealkiri"
    step = NextAction.objects.get(matter=m["2026_506"], status=ActionStatus.OPEN)
    assert (step.text, step.kind, step.target_date) == (
        "ootan Riigikogu otsust",
        "WAIT",
        dt.date(2026, 12, 15),
    )
    assert NextAction.objects.filter(matter=m["2026_506"]).count() == 2  # superseded, kept
    assert MatterImportantDate.objects.filter(
        matter=m["2026_506"], kind="PLANNED_COMMENCEMENT", date_value=dt.date(2027, 2, 1)
    ).exists()
    # The writes say who wrote them: no person, the rebuild's provenance.
    renamed = ChangeEvent.objects.filter(
        matter=m["2026_506"], event_type=ChangeEventType.MATTER_TITLE_CHANGED
    ).latest("occurred_at")
    assert renamed.actor is None
    assert renamed.payload["source"] == "LOVABLE_REBUILD"
    assert renamed.payload["cohort"] == OPERATIONAL_COHORT

    # The new predecessor continues under the rebased Teema; only its row changed.
    created = Matter.all_objects.get(reference_number=603)
    assert created.superseded_by_id == m["2026_501"].pk
    assert m["2026_501"].superseded_by_id is None
    assert Matter.all_objects.get(reference_number=602).is_open is False

    # Every rebased Teema has the snapshot's source reference beside the pilot's.
    for ref in ("2026_501", "2026_502", "2026_503", "2026_505", "2026_506"):
        sources = MatterSourceReference.objects.filter(matter=m[ref], source_system="LOVABLE")
        assert sources.count() == 2
        assert sources.filter(source_snapshot_sha256="c" * 64).get().onenote_url == (
            "https://example.invalid/naidis/rebase"
        )
    # Records: REBASE per Teema, one per write, a NEXT_ACTION for each open step.
    records = EvidencePilotRecord.objects.filter(pilot=OPERATIONAL_COHORT)
    next_records = {
        r.matter.display_reference: str(r.target_id)
        for r in records.filter(record_kind="NEXT_ACTION")
    }
    assert "2026_505" not in next_records  # the lawyer's step stays local
    assert next_records["2026_501"] == steps_before["2026_501"]
    assert next_records["2026_506"] == str(step.pk)
    assert "2026_503" not in next_records
    assert records.filter(record_kind="REBASE").count() == 5

    # Local work survived, everything outside the package is byte for byte the same.
    assert local_digests(report.local_specs, drop_ids=[created.pk]) == report.local_before
    assert list(m["2026_506"].policy_areas.all()) == [world["area"]]
    assert Document.objects.filter(pk=world["document"].pk, matter=m["2026_506"]).exists()
    after = preserved_digests()
    outside = {pk for pk in untouched_before if pk not in {str(i) for i in rebased_ids}}
    assert {pk: after[pk] for pk in outside} == {pk: untouched_before[pk] for pk in outside}
    # The pilot's own records stay.
    assert EvidencePilotRecord.objects.filter(pilot=PILOT).count() > 0
    assert ImportBatch.objects.count() == 2


def test_verify_reads_back_and_a_second_apply_writes_nothing(world):
    _apply(_rebuild())
    plan = build_plan(_rebuild())
    assert plan.database_state == "applied", plan.problems
    assert verify_applied(plan).ok
    counts = (EvidencePilotRecord.objects.count(), NextAction.objects.count())
    again = _apply(_rebuild())
    assert again.already_applied
    assert (EvidencePilotRecord.objects.count(), NextAction.objects.count()) == counts


def test_verify_sees_a_rebased_value_moved_since(world):
    _apply(_rebuild())
    Matter.all_objects.filter(reference_number=501).update(title="Keegi muutis")
    report = verify_applied(build_plan(_rebuild()))
    assert "2026_501: title is not what the plan left (WRITE)." in report.discrepancies


def test_a_lawyer_save_after_the_plan_refuses_the_apply(world):
    package = _rebuild()
    plan = build_plan(package)
    approved = plan_digest(plan)
    set_matter_title(
        matter=world["by_ref"]["2026_501"], value="Juristi muudatus", actor=world["mari"]
    )
    with pytest.raises(LovablePilotError, match="changed since it was approved"):
        apply_plan(
            plan,
            operator_intent=OPERATIONAL_COHORT,
            expect_package_sha256=plan.digest,
            expect_plan_sha256=approved,
            backup_set=BACKUP,
        )
    assert not EvidencePilotRecord.objects.filter(pilot=OPERATIONAL_COHORT).exists()
    assert not Matter.all_objects.filter(reference_number=601).exists()


def test_an_interrupted_rebuild_leaves_nothing_and_runs_again(world, monkeypatch):
    from app.matters import services

    before = preserved_digests()
    original = services.set_matter_title

    def fails(**kwargs: Any) -> Any:
        raise RuntimeError("katkestus")

    monkeypatch.setattr(services, "set_matter_title", fails)
    with pytest.raises(RuntimeError, match="katkestus"):
        _apply(_rebuild())
    assert preserved_digests() == before
    assert not EvidencePilotRecord.objects.filter(pilot=OPERATIONAL_COHORT).exists()
    monkeypatch.setattr(services, "set_matter_title", original)
    assert _apply(_rebuild()).verify.ok


def test_a_change_to_local_work_inside_the_rebuild_refuses_it(world, monkeypatch):
    from app.matters import services

    original = services.set_matter_title
    teema = world["by_ref"]["2026_506"]

    def and_touch_local_work(**kwargs: Any) -> Any:
        teema.policy_areas.clear()  # a Valdkond is Juristid-only, never the import's
        return original(**kwargs)

    monkeypatch.setattr(services, "set_matter_title", and_touch_local_work)
    with pytest.raises(LovablePilotError, match="local work changed: 2026_506"):
        _apply(_rebuild())
    assert list(teema.policy_areas.all()) == [world["area"]]


def test_an_undeclared_lovable_value_change_refuses_it(world, monkeypatch):
    from app.matters import services

    original = services.set_matter_title
    target = world["by_ref"]["2026_502"]

    def and_rename_another(**kwargs: Any) -> Any:
        Matter.all_objects.filter(pk=target.pk).update(title="vahepeal muudetud")
        return original(**kwargs)

    monkeypatch.setattr(services, "set_matter_title", and_rename_another)
    with pytest.raises(LovablePilotError, match="undeclared changes: 2026_502"):
        _apply(_rebuild())


def test_preserved_after_the_fact_reports_local_and_declared(world, tmp_path, capsys):
    package = _rebuild()
    path = tmp_path / "package.json"
    path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    plan = build_plan(package)
    out = tmp_path / "preserved.json"
    call_command(
        "lovable_pilot",
        "apply",
        "--package",
        str(path),
        "--expect-package-sha256",
        plan.digest,
        "--expect-plan-sha256",
        plan_digest(plan),
        "--operator-intent",
        OPERATIONAL_COHORT,
        "--backup-set",
        BACKUP,
        "--preserved-out",
        str(out),
        "--json",
    )
    capsys.readouterr()
    document = json.loads(out.read_text(encoding="utf-8"))
    assert len(document["local"]) == 5 and len(document["declared"]) == 5
    assert "Juristi pealkiri" not in out.read_text(encoding="utf-8")

    call_command("lovable_pilot", "preserved", "--before", str(out), "--json")
    printed = json.loads(capsys.readouterr().out)
    assert (printed["local_unchanged"], printed["declared_changes_only"]) == (5, True)

    world["by_ref"]["2026_506"].policy_areas.clear()
    Matter.all_objects.filter(reference_number=501).update(title="hiljem muudetud")
    with pytest.raises(CommandError, match="2 pre-existing Teema"):
        call_command("lovable_pilot", "preserved", "--before", str(out), "--json")
    printed = json.loads(capsys.readouterr().out)
    assert printed["local_changed"] == ["2026_506"]
    assert printed["undeclared"] == ["2026_501"]
    assert printed["declared_changes_only"] is False


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def _problems(package: dict[str, Any]) -> list[str]:
    package["expected_topics"] = len(package["topics"])
    return validate_package(package).problems


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_change("stage", "monitoring_stopped"), "would close the Teema"),
        (_change("stage", "in_force", KEEP_LOCAL), "would close the Teema"),
        (_change("valja", "2026-10-01"), "'valja' is not a field a REBASE may change"),
        (_change("next_action", None), "no readable step is HOLD"),
        (_change("open_request", None), "no request is HOLD"),
        (_change("owner", "Tundmatu"), "is not in the reviewed people map"),
        (_change("title", "x", "OVERWRITE"), "is not KEEP_LOCAL, TAKE_SOURCE or HOLD"),
        ({"field": "title", "reading": "x"}, "the reviewer's reason is required"),
        (_change("open_request", {"deadline": "2026-10-01"}), "before the snapshot is history"),
    ],
)
def test_a_rebase_change_that_cannot_be_read_honestly_is_refused(change, message):
    problems = _problems(_rebuild(_rebase("2026_501", [change])))
    assert any(message in p for p in problems), problems


def test_a_terminal_stage_held_is_accepted():
    assert _problems(_rebuild(_rebase("2026_501", [_change("stage", "in_force", HOLD)]))) == []


def test_a_rebase_never_carries_a_continuation_or_a_create_reading():
    topic = _rebase("2026_501", [], successor="2026_601")
    assert any("a REBASE carries no successor" in p for p in _problems(_rebuild(topic)))


def test_a_rebase_row_carries_topic_columns_only():
    topic = _rebase("2026_501", [])
    topic["source"]["row"]["markmed"] = "x"
    problems = _problems(_rebuild(topic))
    assert any("source.row carries markmed, which is not a topic column" in p for p in problems)


def test_a_pilot_teema_listed_twice_is_refused():
    problems = _problems(_rebuild(_rebase("2026_501", []), _rebase("2026_501", [])))
    assert "2026_501: listed twice." in problems


def test_a_rebase_of_a_teema_the_pilot_did_not_create_is_refused(world):
    native = factories.MatterFactory(reference_year=2026, reference_number=7)
    topic = _rebase("2026_7", [])
    topic["source"]["id"] = topic["source"]["row"]["id"] = _sid("2026_7")
    plan = build_plan(_rebuild(topic))
    assert (
        f"{native.display_reference}: not a Teema the pilot (lovable-pilot) created; no REBASE."
        in (plan.problems)
    )


def test_a_rebase_naming_another_identity_is_refused(world):
    topic = _rebase("2026_501", [])
    topic["source"]["id"] = _pilot_id("2026_502")
    topic["source"]["row"]["id"] = _pilot_id("2026_502")
    plan = build_plan(_rebuild(topic))
    assert any("2026_501: its pilot source reference does not record" in p for p in plan.problems)


def test_a_rebase_of_a_free_reference_is_refused(world):
    topic = _rebase("2026_630", [])
    plan = build_plan(_rebuild(topic))
    assert "2026_630: a REBASE names a pilot Teema; Juristid holds none at 2026_630." in (
        plan.problems
    )


def test_a_create_for_an_identity_a_teema_records_is_refused(world):
    topic = _active("2026_630")
    topic["source"]["id"] = _pilot_id("2026_502")
    plan = build_plan(_rebuild(topic))
    assert plan.identity_collisions == ["2026_630 (Juristid 2026_502)"]


def test_a_rebase_of_only_some_pilot_teemad_is_allowed(world):
    package = _rebuild(_rebase("2026_502", []))
    assert build_plan(package).problems == []
    assert _apply(package).verify.ok


def test_a_rebase_write_dated_before_today_is_a_plan_problem(world, apply_day):
    apply_day["today"] = dt.date(2026, 11, 30)
    package = _rebuild(_rebase("2026_501", [_change("open_request", {"deadline": "2026-11-25"})]))
    plan = build_plan(package)
    assert any("2026_501: open_request 2026-11-25 is before today" in p for p in plan.problems)


def test_a_version_1_package_rebases_nothing():
    package = _pilot_package()
    package["topics"][0]["action"] = "CREATE"
    assert any("a topic action is a version-2 reading" in p for p in _problems(package))


def test_copying_the_package_does_not_change_its_plan(world):
    package = _rebuild()
    assert plan_digest(build_plan(package)) == plan_digest(build_plan(copy.deepcopy(package)))


# ---------------------------------------------------------------------------
# The contract with the synchronisation
# ---------------------------------------------------------------------------


def test_a_kept_or_held_change_is_held_for_review_on_its_column():
    topic = _rebase("2026_505", [_change("next_action", NEW_STEP, KEEP_LOCAL)])
    topic["field_decisions"] = {}
    topic["review_notes"] = []
    problems = _problems(_rebuild(topic))
    assert any(
        "KEEP_LOCAL keeps Juristid's value; field_decisions marks jargmiseks or "
        "tegevuse_tahtaeg HELD_FOR_REVIEW." in p
        for p in problems
    ), problems


def test_a_kept_step_may_omit_its_reading(world):
    change = {"field": "next_action", "decision": KEEP_LOCAL, "reason": "the lawyer's step"}
    package = _rebuild(_rebase("2026_505", [change]))
    assert _problems(copy.deepcopy(package)) == []
    plan = build_plan(package)
    assert plan.problems == []
    assert _actions(plan) == {"2026_505": [("next_action", KEEP_LOCAL)]}
    assert _apply(package).verify.ok


def test_a_title_reading_is_the_entrys_title():
    topic = _rebase("2026_501", [_change("title", "Üks pealkiri")], title="Teine pealkiri")
    assert any("is the entry's reviewed title" in p for p in _problems(_rebuild(topic)))


def test_the_records_name_what_each_write_wrote(world):
    package = _rebuild(
        _rebase(
            "2026_502",
            [_change("owner", "Kati", TAKE_SOURCE), _change("stage", "parliament")],
        ),
        _rebase(
            "2026_501",
            [_change("title", "Näidisteema 2026_501 (uus)")],
            title="Näidisteema 2026_501 (uus)",
        ),
    )
    _apply(package)
    records = EvidencePilotRecord.objects.filter(pilot=OPERATIONAL_COHORT)
    owner = records.get(record_kind="OWNER")
    assert (owner.target_model, str(owner.target_id)) == ("accounts.User", str(world["kati"].pk))
    stage = records.get(record_kind="STAGE")
    assert stage.target_model == "matters.Matter"
    assert (stage.evidence["from"], stage.evidence["to"]) == ("consultation", "parliament")
    title = records.get(record_kind="TITLE")
    assert title.target_model == "matters.Matter"
    for rebase in records.filter(record_kind="REBASE"):
        source = MatterSourceReference.objects.get(pk=rebase.target_id)
        assert rebase.target_model == "legacy_import.MatterSourceReference"
        assert source.source_snapshot_sha256 == "c" * 64
        assert source.matter_id == rebase.matter_id
    renamed = MatterSourceReference.objects.get(
        pk=records.get(record_kind="REBASE", record_key="2026_501").target_id
    )
    assert renamed.source_title == Matter.objects.get(pk=title.target_id).title


def test_a_snapshot_of_the_digests_proves_a_restore(world, tmp_path, capsys):
    out = tmp_path / "at-backup.json"
    call_command("lovable_pilot", "preserved", "--snapshot-out", str(out), "--json")
    printed = json.loads(capsys.readouterr().out)
    assert printed["matters"] == Matter.all_objects.count()
    call_command("lovable_pilot", "preserved", "--before", str(out), "--json")
    assert json.loads(capsys.readouterr().out)["changed"] == []
    world["by_ref"]["2026_506"].policy_areas.clear()
    with pytest.raises(CommandError, match="1 pre-existing Teema"):
        call_command("lovable_pilot", "preserved", "--before", str(out), "--json")
    assert json.loads(capsys.readouterr().out)["changed"] == ["2026_506"]


def test_snapshot_out_and_before_are_one_or_the_other(tmp_path):
    with pytest.raises(CommandError, match="not both"):
        call_command(
            "lovable_pilot",
            "preserved",
            "--before",
            str(tmp_path / "a.json"),
            "--snapshot-out",
            str(tmp_path / "b.json"),
        )


# ---------------------------------------------------------------------------
# Review fixes: A from the pilot's provenance, step identity, owner effects,
# local digest per field, locks
# ---------------------------------------------------------------------------


def _open_step(matter: Matter) -> NextAction:
    return NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)


def _state(matter: Matter):
    from app.legacy_import.lovable_sync_models import LovableSyncState

    return LovableSyncState.objects.get(matter=matter)


def _needs_decision(package: dict[str, Any], reference: str, field: str) -> None:
    plan = build_plan(package)
    wanted = (
        f"{reference}: {field} was changed in Juristid and in Lovable; the package needs a "
        "decision (KEEP_LOCAL, TAKE_SOURCE or HOLD)."
    )
    assert wanted in plan.problems, plan.problems


def test_a_title_resolved_to_the_lawyers_is_not_the_pilots(world):
    """`resolve_field` moves the baseline's target to the lawyer's title; A stays
    the pilot's own, so the rebuild needs a decision rather than overwriting it."""
    teema = world["by_ref"]["2026_501"]
    set_matter_title(matter=teema, value="Juristi nimi", actor=world["mari"])
    state = _state(teema)
    state.applied["title"]["target"] = "Juristi nimi"
    state.save(update_fields=["applied"])
    package = _rebuild(_rebase("2026_501", [_change("title", "Uus")], title="Uus"))
    _needs_decision(package, "2026_501", "title")


def test_a_step_converged_on_a_night_is_not_the_pilots(world):
    """ALREADY_CONVERGED moves the baseline's step to the lawyer's; a later source
    step must not replace the lawyer's without a decision."""
    teema = world["by_ref"]["2026_501"]
    step = set_next_action(
        matter=teema,
        text="ootan, Lovable'iga sama",
        kind="WAIT",
        date_semantics="REVIEW_ON",
        target_date=dt.date(2026, 11, 30),
        responsible=world["mari"],
        actor=world["mari"],
    )
    state = _state(teema)
    state.applied["next_action"]["target"] = lovable_sync._action_dict(step)
    state.save(update_fields=["applied"])
    _needs_decision(
        _rebuild(_rebase("2026_501", [_change("next_action", NEW_STEP)])), "2026_501", "next_action"
    )


def test_a_reassigned_step_with_the_same_words_is_not_the_pilots(world):
    teema = world["by_ref"]["2026_501"]
    NextAction.objects.filter(pk=_open_step(teema).pk).update(responsible=world["kati"])
    _needs_decision(
        _rebuild(_rebase("2026_501", [_change("next_action", NEW_STEP)])), "2026_501", "next_action"
    )


def test_a_restricted_step_is_not_the_pilots(world):
    teema = world["by_ref"]["2026_501"]
    NextAction.objects.filter(pk=_open_step(teema).pk).update(visibility_override="RESTRICTED")
    _needs_decision(
        _rebuild(_rebase("2026_501", [_change("next_action", NEW_STEP)])), "2026_501", "next_action"
    )


def test_a_step_reviewed_in_place_is_not_the_pilots(world):
    from app.workflow.services import acknowledge_review

    teema = world["by_ref"]["2026_501"]
    # Looked at, moved, and moved back: the same words and day, edited in place.
    for day in (dt.date(2026, 11, 20), dt.date(2026, 11, 15)):
        acknowledge_review(action=_open_step(teema), actor=world["mari"], next_review_date=day)
    assert _open_step(teema).target_date == dt.date(2026, 11, 15)
    _needs_decision(
        _rebuild(_rebase("2026_501", [_change("next_action", NEW_STEP)])), "2026_501", "next_action"
    )


def test_an_untouched_pilot_step_is_still_written(world):
    plan = build_plan(_rebuild(_rebase("2026_501", [_change("next_action", NEW_STEP)])))
    assert plan.problems == []
    assert _actions(plan) == {"2026_501": [("next_action", WRITE)]}


def test_an_owner_is_written_only_on_take_source(world):
    package = _rebuild(_rebase("2026_502", [_change("owner", "Kati")]))
    plan = build_plan(package)
    assert any(
        "2026_502: an owner change moves its steps and notices" in p for p in plan.problems
    ), plan.problems


def test_an_owner_taken_from_the_source_moves_its_steps_as_declared(world):
    teema = world["by_ref"]["2026_502"]
    package = _rebuild(_rebase("2026_502", [_change("owner", "Kati", TAKE_SOURCE)]))
    report = _apply(package)
    assert report.verify.ok
    assert Matter.objects.get(pk=teema.pk).owner == world["kati"]
    assert _open_step(teema).responsible == world["kati"]


def _touch_during_the_rebuild(monkeypatch, effect) -> None:
    from app.matters import services

    original = services.set_matter_title

    def and_then(**kwargs: Any) -> Any:
        effect()
        return original(**kwargs)

    monkeypatch.setattr(services, "set_matter_title", and_then)


def test_a_stage_period_of_a_teema_whose_stage_is_not_written_stays_covered(world, monkeypatch):
    from app.matters.models import MatterStageEpisode

    teema = world["by_ref"]["2026_505"]
    _touch_during_the_rebuild(
        monkeypatch,
        lambda: MatterStageEpisode.objects.filter(matter=teema).update(
            updated_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        ),
    )
    with pytest.raises(LovablePilotError, match="local work changed: 2026_505"):
        _apply(_rebuild())


def test_a_superseded_step_of_a_teema_whose_step_is_kept_stays_covered(world, monkeypatch):
    teema = world["by_ref"]["2026_505"]
    old = NextAction.objects.filter(matter=teema).exclude(status=ActionStatus.OPEN).get()
    _touch_during_the_rebuild(
        monkeypatch, lambda: NextAction.objects.filter(pk=old.pk).update(text="muudetud")
    )
    with pytest.raises(LovablePilotError, match="local work changed: 2026_505"):
        _apply(_rebuild())


def test_a_planned_step_beside_a_written_one_stays_covered(world, monkeypatch):
    teema = world["by_ref"]["2026_506"]
    planned = factories.NextActionFactory(
        matter=teema, status=ActionStatus.PLANNED, target_date=dt.date(2027, 1, 10)
    )
    _touch_during_the_rebuild(
        monkeypatch, lambda: NextAction.objects.filter(pk=planned.pk).update(text="muudetud")
    )
    with pytest.raises(LovablePilotError, match="local work changed: 2026_506"):
        _apply(_rebuild())


def test_a_lawyer_save_slipping_past_the_plan_is_refused_before_writing(world, monkeypatch):
    original = lovable_pilot._hold_and_replan
    teema = world["by_ref"]["2026_501"]

    def replan_then_a_save(plan, expect):
        current = original(plan, expect)
        Matter.all_objects.filter(pk=teema.pk).update(title="Juristi salvestus")
        return current

    monkeypatch.setattr(lovable_pilot, "_hold_and_replan", replan_then_a_save)
    with pytest.raises(LovablePilotError, match="2026_501 changed since it was approved"):
        _apply(_rebuild())
    # Everything rolled back, the save included (it ran inside the test's transaction).
    assert Matter.all_objects.get(pk=teema.pk).title == "Näidisteema 2026_501"
    assert not EvidencePilotRecord.objects.filter(pilot=OPERATIONAL_COHORT).exists()


def test_the_rebase_teemad_are_locked_in_order_before_the_table(world):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as queries:
        _apply(_rebuild())
    sql = [q["sql"] for q in queries.captured_queries]
    row_lock = next(
        i
        for i, q in enumerate(sql)
        if 'FROM "matters_matter"' in q and "FOR NO KEY UPDATE" in q and "ORDER BY" in q
    )
    table_lock = next(i for i, q in enumerate(sql) if 'LOCK TABLE "matters_matter"' in q)
    sequence_lock = next(
        i for i, q in enumerate(sql) if "matterreferencesequence" in q and "FOR UPDATE" in q
    )
    assert sequence_lock < row_lock < table_lock


def test_the_apply_waits_for_no_synchronisation_run(world):
    import psycopg
    from django.db import connection

    params = connection.get_connection_params()
    other = psycopg.connect(**{k: v for k, v in params.items() if k != "cursor_factory"})
    try:
        other.execute(
            "SELECT pg_advisory_lock(%s, %s)", [lovable_sync.LOCK_NAMESPACE, lovable_sync.LOCK_KEY]
        )
        with pytest.raises(LovablePilotError, match="a synchronisation run is in progress"):
            _apply(_rebuild())
        with pytest.raises(LovablePilotError, match="a synchronisation run is in progress"):
            lovable_pilot.dry_run(build_plan(_rebuild()))
    finally:
        other.close()
    assert not EvidencePilotRecord.objects.filter(pilot=OPERATIONAL_COHORT).exists()


def test_a_title_reading_is_the_entrys_title_exactly():
    topic = _rebase(
        "2026_501",
        [_change("title", "Näidisteema 2026_501 (uus).")],
        title="Näidisteema 2026_501 (uus)",
    )
    assert any("is the entry's reviewed title" in p for p in _problems(_rebuild(topic)))


def test_an_open_request_on_a_chamber_initiative_is_a_plan_problem(world):
    teema = world["by_ref"]["2026_501"]
    Matter.all_objects.filter(pk=teema.pk).update(track="KODA_INITIATIVE")
    package = _rebuild(_rebase("2026_501", [_change("open_request", {"deadline": "2026-11-25"})]))
    plan = build_plan(package)
    assert any("2026_501: a Chamber initiative takes no new request" in p for p in plan.problems), (
        plan.problems
    )


def test_a_lawyers_step_with_no_listed_change_gets_no_record(world):
    _apply(_rebuild(_rebase("2026_505", [])))
    assert not EvidencePilotRecord.objects.filter(
        pilot=OPERATIONAL_COHORT, record_kind="NEXT_ACTION"
    ).exists()
