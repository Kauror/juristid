"""The active Lovable register as a second cohort (docs/adr/0202 §1, §3–§5).

A synthetic version-2 package — every title, name, reference and identity below
is invented — walks the importer's second cohort: the cohort is what the
records say and what the operator types; a version-1 package still hashes,
plans and writes exactly as before; «ei saatnud» on a live stage stays open and
sends nothing; a step the reviewer could not read is held, not guessed; a
continuation is written only from Lovable's structured stage link inside the
package; every Teema already in Juristid — native or the pilot's — is digested
before and after and must not change; and source drift is read GET-only.
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
from app.intelligence.models import MatterImportantDate
from app.legacy_import import lovable_pilot, lovable_source
from app.legacy_import.integrity import canonical_sha256
from app.legacy_import.lovable_pilot import (
    OPERATIONAL_COHORT,
    PILOT,
    LovablePilotError,
    apply_plan,
    build_plan,
    package_digest,
    plan_digest,
    preserved_digests,
    source_drift,
    validate_package,
    verify_applied,
)
from app.legacy_import.models import EvidencePilotRecord, ImportBatch, MatterSourceReference
from app.matters.models import Matter, MatterReferenceSequence
from app.submissions.models import Submission
from app.workflow.models import NextAction
from tests import factories
from tests.test_lovable_pilot import _package as _pilot_package
from tests.test_lovable_pilot import _topic

#: `package_digest` of the version-1 synthetic package in tests/test_lovable_pilot.py,
#: computed on main before the importer learnt about cohorts. A version-1 package
#: must keep its identity: the pilot's approvals and nightly baselines name it.
V1_PACKAGE_SHA256 = "3f4e9d048ae1ae6dc85e42a3233a5d6a9716441e9e1c34fd935c1be21e2e95c3"
AS_OF = "2026-10-26T17:40:16+00:00"
BACKUP = "sets/20261103T060000Z"


def _sid(reference: str) -> str:
    year, number = reference.split("_")
    return f"00000000-0000-0000-{year}-{int(number):012d}"


def _active(reference: str, **overrides: Any) -> dict[str, Any]:
    """An ongoing topic as a version-2 package states it."""
    topic = _topic(reference)
    topic["source"]["id"] = _sid(reference)
    topic["source"]["row"] = {
        "nr": reference,
        "teema": f"Näidisteema {reference}",
        "aasta": int(reference[:4]),
        "hetkeseis": "Riigikogus",
        "valja": "",
        "valja_kuupaev": None,
        "jargmiseks": "ootan 2. lugemist",
        "tegevuse_tahtaeg": topic["next_action"]["target_date"],
    }
    topic["deadline"] = {"treatment": "NONE"}
    topic["field_decisions"] = {"jargmiseks": "APPLIED"}
    topic.update(overrides)
    return topic


def _not_sent() -> dict[str, Any]:
    """«ei saatnud» on a live stage, the step unreadable, a past send day in the row."""
    topic = _active(
        "2026_602",
        owner_raw="Jaan",
        stage="consultation",
        status_raw="kooskõlastusel",
        next_action=None,
        deadline={"treatment": "SOURCE_ONLY", "deadline": "2026-09-15"},
        field_decisions={
            "valja": "HELD_FOR_REVIEW",
            "jargmiseks": "HELD_FOR_REVIEW",
            "arvamuse_tahtaeg": "HELD_FOR_REVIEW",
        },
        review_notes=[
            {"field": "valja", "reason": "«ei saatnud» on a live stage"},
            {"field": "jargmiseks", "reason": "no kind can be read"},
        ],
    )
    topic["source"]["row"].update(
        hetkeseis="kooskõlastusel",
        valja="ei saatnud",
        valja_kuupaev="2026-09-01",
        arvamuse_tahtaeg="2026-09-15",
        jargmiseks="vt OneNote",
    )
    return topic


def _past_review() -> dict[str, Any]:
    """A WAIT whose review day has come round, and an estimated commencement."""
    topic = _active("2026_601")
    topic["next_action"].update(
        target_date="2026-10-01", text="ootan, vaata üle", source_text="ootan, vaata üle"
    )
    topic["commencements"] = [
        {
            "key": "2026_601-C01",
            "review": "READY",
            "target": "MatterImportantDate",
            "kind": "ESTIMATED_COMMENCEMENT",
            "date": "2027-03-01",
            "date_precision": "EXACT",
            "note": "",
        }
    ]
    return topic


def _older_year() -> dict[str, Any]:
    """A 2025 topic with a different owner and an outstanding opinion request."""
    return _active(
        "2025_88",
        owner_raw="Jaan",
        stage="consultation",
        next_action={
            "review": "READY",
            "kind": "DO",
            "date_semantics": "DEADLINE",
            "target_date": "2026-11-20",
            "date_precision": "EXACT",
            "text": "Koostan arvamuse",
            "source_text": "Koostan arvamust.",
            "treatment": "OPINION_DRAFTING",
            "reason": "drafting",
        },
        deadline={"treatment": "OPEN_REQUEST", "deadline": "2026-11-20"},
    )


def _active_package(*topics: dict[str, Any]) -> dict[str, Any]:
    topics = topics or (_past_review(), _not_sent(), _older_year())
    return {
        "format": lovable_pilot.PACKAGE_FORMAT,
        "format_version": lovable_pilot.ACTIVE_FORMAT_VERSION,
        "cohort": OPERATIONAL_COHORT,
        "reviewer": "näidisülevaataja",
        "snapshot": {
            "as_of": AS_OF,
            "snapshot_sha256": "b" * 64,
            "project": "näidis",
            "highest_reference": {"2026": 640, "2025": 90},
        },
        "people": {"Mari": "Mari Näidis", "Jaan": "Jaan Näidis"},
        "reserve_through": {"2026": 640, "2025": 90},
        "expected_topics": len(topics),
        "topics": list(topics),
    }


def _apply(package: dict[str, Any], **overrides: Any):
    plan = build_plan(package)
    arguments = {
        "operator_intent": lovable_pilot.package_cohort(package),
        "expect_package_sha256": plan.digest,
        "expect_plan_sha256": plan_digest(plan),
        "backup_set": BACKUP,
        **overrides,
    }
    return apply_plan(plan, **arguments)


def _created_payload(matter: Matter) -> dict[str, Any]:
    return ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.MATTER_CREATED).payload


def _problems(package: dict[str, Any]) -> list[str]:
    package["expected_topics"] = len(package["topics"])
    return validate_package(package).problems


def _refused(package: dict[str, Any], message: str) -> None:
    problems = _problems(package)
    assert any(message in problem for problem in problems), problems


@pytest.fixture
def world(db):
    mari = factories.UserFactory(upn="mari@example.invalid", display_name="Mari Näidis")
    jaan = factories.UserFactory(upn="jaan@example.invalid", display_name="Jaan Näidis")
    return {"mari": mari, "jaan": jaan}


@pytest.fixture(autouse=True)
def apply_day(monkeypatch):
    """The plan's "today", pinned the day after the snapshot: every date here is
    fixed, so no test turns red when the calendar passes one of them."""
    day = {"today": dt.date(2026, 10, 27)}
    monkeypatch.setattr(lovable_pilot, "_today", lambda: day["today"])
    return day


@pytest.fixture
def pilot_on(settings):
    settings.LOVABLE_PILOT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


@pytest.fixture
def existing(world, pilot_on):
    """A native Teema with a step, a date and a Valdkond, and the pilot's cohort."""
    area = factories.PolicyAreaFactory()
    native = factories.MatterFactory(reference_year=2026, reference_number=7, owner=world["mari"])
    native.policy_areas.add(area)
    factories.NextActionFactory(matter=native)
    factories.ImportantDateFactory(matter=native)
    _apply(_pilot_package())
    return {"native": native, "area": area}


# ---------------------------------------------------------------------------
# Version 1 is unchanged
# ---------------------------------------------------------------------------


def test_a_version_1_package_keeps_its_digest_and_cohort():
    package = _pilot_package()
    assert package_digest(package) == V1_PACKAGE_SHA256
    assert "cohort" not in package
    assert lovable_pilot.package_cohort(package) == PILOT
    assert validate_package(package).problems == []


def test_a_version_1_plan_digest_is_the_one_it_always_was(world):
    plan = build_plan(_pilot_package())
    assert plan.cohort == PILOT
    # The formula as it stood before cohorts: nothing was added to it.
    assert plan_digest(plan) == canonical_sha256(
        {
            "version": 1,
            "package_sha256": V1_PACKAGE_SHA256,
            "database_state": "ready",
            "collisions": [],
            "topics": [
                {
                    "reference": t.reference,
                    "owner": str(t.owner.pk) if t.owner else "",
                    "stage": t.stage.key if t.stage else "",
                    "instruments": sorted(i.key for i in t.instruments),
                    "addressee": str(t.addressee.pk) if t.addressee else "",
                    "successor": t.successor_in_batch,
                }
                for t in plan.topics
            ],
            "reserve_through": {"2026": 520},
        }
    )


def test_a_version_1_package_writes_the_pilots_records_exactly(world, pilot_on):
    _apply(_pilot_package())
    assert set(EvidencePilotRecord.objects.values_list("pilot", "pilot_version")) == {
        (PILOT, "1.0")
    }
    matter = Matter.objects.get(reference_number=501)
    created = _created_payload(matter)
    assert {k: created[k] for k in ("source", "pilot", "pilot_version", "package_sha256")} == {
        "source": "LOVABLE_PILOT",
        "pilot": PILOT,
        "pilot_version": "1.0",
        "package_sha256": V1_PACKAGE_SHA256,
    }
    assert "cohort" not in created and "format_version" not in created
    batch = ImportBatch.objects.get()
    assert (batch.importer_version, batch.contract_version) == (
        "lovable-pilot/1.0",
        "juristid.lovable-pilot/1",
    )
    assert batch.notes.startswith("lovable-pilot: 4 teemat") and "(docs/adr/0191)." in batch.notes
    source = MatterSourceReference.objects.get(matter=matter)
    assert source.source_parser_version == "lovable-pilot/1.0+reviewed"
    assert verify_applied(build_plan(_pilot_package())).ok


def test_a_version_1_package_still_types_the_pilot_intent(world, pilot_on):
    with pytest.raises(LovablePilotError, match="--operator-intent lovable-pilot"):
        _apply(_pilot_package(), operator_intent=OPERATIONAL_COHORT)


def test_a_version_1_row_is_not_held_to_the_version_2_exclusions():
    package = _pilot_package()
    package["topics"][0]["source"]["row"]["markmed"] = "näidismärkmed"
    assert _problems(package) == []


# ---------------------------------------------------------------------------
# Version 2: the cohort
# ---------------------------------------------------------------------------


def test_the_active_package_validates():
    report = validate_package(_active_package())
    assert report.problems == []
    assert (report.topics, report.open_topics, report.steps) == (3, 3, 2)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda p: p.pop("cohort"), "is the lovable-operational cohort's"),
        (lambda p: p.update(cohort=PILOT), "is the lovable-operational cohort's"),
        (lambda p: p.update(cohort="lovable-muu"), "cohort 'lovable-muu' is unknown"),
        (lambda p: p.update(format_version=3), "format_version 3 is not supported"),
        (lambda p: p.update(format_version=1), "is the lovable-pilot cohort's"),
    ],
)
def test_the_cohort_and_the_format_version_belong_together(change, message):
    package = _active_package()
    change(package)
    _refused(package, message)


def test_the_active_cohort_writes_its_own_records(world, pilot_on):
    with pytest.raises(LovablePilotError, match="--operator-intent lovable-operational"):
        _apply(_active_package(), operator_intent=PILOT)
    assert Matter.all_objects.count() == 0

    report = _apply(_active_package())
    assert report.cohort == OPERATIONAL_COHORT
    assert set(EvidencePilotRecord.objects.values_list("pilot", "pilot_version")) == {
        (OPERATIONAL_COHORT, "2.0")
    }
    digest = package_digest(_active_package())
    marked = EvidencePilotRecord.objects.filter(
        pilot=OPERATIONAL_COHORT, package_sha256=digest, record_kind="MATTER"
    )
    assert sorted(r.record_key for r in marked) == ["2025_88", "2026_601", "2026_602"]
    created = _created_payload(Matter.objects.get(reference_year=2026, reference_number=601))
    assert (created["source"], created["pilot"], created["cohort"]) == (
        "LOVABLE_PILOT",
        OPERATIONAL_COHORT,
        OPERATIONAL_COHORT,
    )
    assert created["format_version"] == 2
    batch = ImportBatch.objects.get()
    assert batch.importer_version == "lovable-operational/2.0"
    assert batch.contract_version == "juristid.lovable-pilot/2"
    assert verify_applied(build_plan(_active_package())).ok
    # The pilot's own records are not this cohort's and vice versa.
    assert build_plan(_active_package()).database_state == "applied"
    assert not EvidencePilotRecord.objects.filter(pilot=PILOT).exists()


def test_multiple_years_and_owners_in_one_package(world, pilot_on):
    _apply(_active_package())
    by_ref = {m.display_reference: m for m in Matter.all_objects.all()}
    assert by_ref["2025_88"].owner == world["jaan"]
    assert by_ref["2026_601"].owner == world["mari"]
    assert by_ref["2026_602"].owner == world["jaan"]
    assert MatterReferenceSequence.objects.get(year=2025).last_number == 90
    assert MatterReferenceSequence.objects.get(year=2026).last_number == 640
    assert by_ref["2025_88"].response_deadline == dt.date(2026, 11, 20)


def test_a_second_apply_writes_nothing(world, pilot_on):
    _apply(_active_package())
    counts = (
        Matter.all_objects.count(),
        EvidencePilotRecord.objects.count(),
        NextAction.objects.count(),
        ChangeEvent.objects.count(),
    )
    again = _apply(_active_package())
    assert again.already_applied
    assert (
        Matter.all_objects.count(),
        EvidencePilotRecord.objects.count(),
        NextAction.objects.count(),
        ChangeEvent.objects.count(),
    ) == counts


def test_a_reference_a_native_teema_holds_blocks_the_plan(world, pilot_on):
    factories.MatterFactory(reference_year=2026, reference_number=602)
    plan = build_plan(_active_package())
    assert plan.database_state == "conflict"
    assert plan.collisions == ["2026_602"]
    with pytest.raises(LovablePilotError, match="never overwritten"):
        _apply(_active_package())
    assert Matter.all_objects.count() == 1


def test_a_reference_the_pilot_holds_blocks_the_active_plan(world, pilot_on):
    _apply(_pilot_package())
    clash = _active_package(_active("2026_501"))
    clash["snapshot"]["highest_reference"] = {"2026": 640}
    clash["reserve_through"] = {"2026": 640}
    plan = build_plan(clash)
    assert plan.collisions == ["2026_501"]


# ---------------------------------------------------------------------------
# Nothing existing changes, proven per Teema
# ---------------------------------------------------------------------------


def test_every_existing_teema_is_unchanged_byte_for_byte(existing):
    before = preserved_digests()
    assert len(before) == 5  # the native Teema and the pilot's four
    report = _apply(_active_package())
    assert report.preserved_before == before
    after = preserved_digests()
    assert {pk: after[pk] for pk in before} == before
    created = set(after) - set(before)
    assert len(created) == 3
    native = existing["native"]
    native.refresh_from_db()
    assert list(native.policy_areas.all()) == [existing["area"]]


def test_the_digest_sees_a_change_the_fingerprint_does_not(existing):
    before, fingerprint = preserved_digests(), lovable_pilot.existing_fingerprint()
    NextAction.objects.filter(matter=existing["native"]).update(text="muudetud")
    after = preserved_digests()
    assert after[str(existing["native"].pk)] != before[str(existing["native"].pk)]
    assert lovable_pilot.existing_fingerprint() == fingerprint
    others = {pk for pk in before if pk != str(existing["native"].pk)}
    assert all(after[pk] == before[pk] for pk in others)


def test_the_dry_run_digests_every_existing_teema_and_leaves_nothing(existing):
    before = preserved_digests()
    report = lovable_pilot.dry_run(build_plan(_active_package()))
    assert report.dry_run and report.verify.ok and report.cohort == OPERATIONAL_COHORT
    assert report.preserved_before == before
    assert preserved_digests() == before
    assert not EvidencePilotRecord.objects.filter(pilot=OPERATIONAL_COHORT).exists()


def test_a_change_to_an_existing_teema_inside_the_import_rolls_it_back(existing, monkeypatch):
    from app.workflow import services

    original = services.set_next_action
    native = existing["native"]
    text = NextAction.objects.get(matter=native).text
    matters, records = Matter.all_objects.count(), EvidencePilotRecord.objects.count()

    def and_touch_another(**kwargs: Any) -> Any:
        NextAction.objects.filter(matter=native).update(text="muudetud impordi ajal")
        return original(**kwargs)

    monkeypatch.setattr(services, "set_next_action", and_touch_another)
    with pytest.raises(LovablePilotError, match="changed during the import") as refused:
        _apply(_active_package())
    assert native.display_reference in str(refused.value)
    assert native.title not in str(refused.value)
    assert NextAction.objects.get(matter=native).text == text
    assert (Matter.all_objects.count(), EvidencePilotRecord.objects.count()) == (
        matters,
        records,
    )


def test_an_interrupted_import_leaves_nothing_and_runs_again(world, pilot_on, monkeypatch):
    from app.intelligence import services

    original = services.add_important_date

    def fails(**kwargs: Any) -> Any:
        raise RuntimeError("katkestus")

    monkeypatch.setattr(services, "add_important_date", fails)
    with pytest.raises(RuntimeError, match="katkestus"):
        _apply(_active_package())
    assert Matter.all_objects.count() == 0
    assert EvidencePilotRecord.objects.count() == 0
    assert ImportBatch.objects.count() == 0
    assert build_plan(_active_package()).database_state == "ready"

    monkeypatch.setattr(services, "add_important_date", original)
    report = _apply(_active_package())
    assert report.written["MATTER"] == 3
    assert verify_applied(build_plan(_active_package())).ok


def test_apply_writes_the_digests_and_preserved_checks_them(existing, tmp_path, capsys):
    package = _active_package()
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
    )
    assert "cohort: lovable-operational" in capsys.readouterr().out
    document = json.loads(out.read_text(encoding="utf-8"))
    assert len(document["matters"]) == 5
    assert "2026_7" in document["references"].values()
    assert existing["native"].title not in out.read_text(encoding="utf-8")

    call_command("lovable_pilot", "preserved", "--before", str(out))
    assert "unchanged: 5" in capsys.readouterr().out

    NextAction.objects.filter(matter=existing["native"]).update(text="muudetud hiljem")
    with pytest.raises(CommandError, match="1 pre-existing Teema"):
        call_command("lovable_pilot", "preserved", "--before", str(out))
    printed = capsys.readouterr().out
    assert "2026_7" in printed and existing["native"].title not in printed

    tampered = copy.deepcopy(document)
    tampered["matters"][next(iter(tampered["matters"]))] = "0" * 64
    out.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(CommandError, match="does not match its content"):
        call_command("lovable_pilot", "preserved", "--before", str(out))


# ---------------------------------------------------------------------------
# Version 2: the reading (docs/adr/0202 §3)
# ---------------------------------------------------------------------------


def test_not_sent_on_a_live_stage_is_an_open_teema_with_nothing_sent(world, pilot_on):
    _apply(_active_package())
    matter = Matter.objects.get(reference_year=2026, reference_number=602)
    assert matter.is_open and matter.disposition == "" and matter.closed_at is None
    assert matter.stage.key == "consultation"
    assert matter.response_deadline is None
    # The step could not be read: no current step, the source kept.
    assert not NextAction.objects.filter(matter=matter).exists()
    assert not Submission.objects.exists()
    assert not ChangeEvent.objects.filter(event_type=ChangeEventType.MATTER_CLOSED).exists()
    raw = MatterSourceReference.objects.get(matter=matter).source_row_raw
    assert raw["row"]["valja"] == "ei saatnud"
    assert raw["row"]["valja_kuupaev"] == "2026-09-01"
    assert raw["field_decisions"]["valja"] == "HELD_FOR_REVIEW"
    assert raw["review_notes"][0]["field"] == "valja"


def test_not_sent_on_a_live_stage_never_concludes_the_topic():
    topic = _not_sent()
    topic.update(lifecycle="INACTIVE", disposition="NO_POSITION_FORMED", owner_raw="")
    _refused(_active_package(topic), "«ei saatnud» on a live stage concludes nothing")


def test_not_sent_on_a_live_stage_is_held_for_review():
    topic = _not_sent()
    topic["field_decisions"]["valja"] = "PRESERVED_AS_SOURCE_ONLY"
    _refused(_active_package(topic), "is held for review (field_decisions.valja)")


def test_not_sent_on_a_finished_stage_may_close():
    topic = _not_sent()
    topic.update(
        lifecycle="INACTIVE",
        disposition="NO_POSITION_FORMED",
        status_raw="rohkem ei tegele",
        stage="monitoring_stopped",
    )
    topic["source"]["row"]["hetkeseis"] = "rohkem ei tegele"
    assert _problems(_active_package(topic)) == []


def test_a_held_field_needs_the_reviewers_reason():
    topic = _not_sent()
    topic["review_notes"] = []
    _refused(_active_package(topic), "HELD_FOR_REVIEW without the reviewer's reason")


def test_a_do_deadline_already_past_is_refused():
    topic = _older_year()
    topic["next_action"].update(treatment="SOURCE_NEXT_STEP", text="Saadan kirja")
    topic["next_action"]["target_date"] = "2026-10-01"
    topic["deadline"] = {"treatment": "NONE"}
    _refused(_active_package(topic), "overdue on arrival")


def test_an_open_request_before_the_snapshot_is_refused():
    topic = _older_year()
    topic["next_action"]["target_date"] = "2026-10-20"
    topic["deadline"]["deadline"] = "2026-10-20"
    _refused(_active_package(topic), "an open request whose day is before the snapshot")


def test_a_review_day_that_has_come_round_is_written_and_not_overdue(world, pilot_on):
    _apply(_active_package())
    step = NextAction.objects.get(matter__reference_number=601, status="OPEN")
    assert (step.kind, step.date_semantics, step.target_date) == (
        "WAIT",
        "REVIEW_ON",
        dt.date(2026, 10, 1),
    )
    assert not step.is_overdue(dt.date(2026, 11, 3))
    assert not NextAction.objects.overdue(dt.date(2026, 11, 3)).filter(pk=step.pk).exists()


def test_an_estimated_commencement_stays_an_estimate(world, pilot_on):
    _apply(_active_package())
    dates = MatterImportantDate.objects.filter(matter__reference_number=601)
    assert [(d.kind, d.date_value) for d in dates] == [
        ("ESTIMATED_COMMENCEMENT", dt.date(2027, 3, 1))
    ]


@pytest.mark.parametrize(
    "column", ["markmed", "mitteametlik_arvamus", "raw_hyperlink", "allikas", "vastutaja_user_id"]
)
def test_a_version_2_row_carries_no_excluded_column(column):
    package = _active_package()
    package["topics"][0]["source"]["row"][column] = "x"
    _refused(package, f"source.row.{column} is carried")


def test_a_version_2_row_carries_no_by_column():
    package = _active_package()
    package["topics"][0]["source"]["row"]["created_by"] = "x"
    _refused(package, "source.row.created_by is carried")


# ---------------------------------------------------------------------------
# Continuations
# ---------------------------------------------------------------------------


def _predecessor(reference: str, successor: str, successor_id: str | None) -> dict[str, Any]:
    topic = _active(
        reference,
        owner_raw="Endine",
        status_raw="rohkem ei tegele",
        stage="monitoring_stopped",
        lifecycle="CONTINUES",
        disposition="SUPERSEDED",
        successor=successor,
        next_action=None,
        onenote_url="",
    )
    topic["source"]["row"]["hetkeseis"] = "rohkem ei tegele"
    if successor_id is not None:
        topic["source"]["successor_id"] = successor_id
    return topic


def test_a_continuation_inside_the_package_is_linked_by_its_identity(world, pilot_on):
    package = _active_package(
        _past_review(), _predecessor("2026_610", "2026_601", _sid("2026_601"))
    )
    plan = build_plan(package)
    assert plan.problems == []
    assert plan.topics[1].successor_in_batch == "2026_601"
    _apply(package)
    predecessor = Matter.all_objects.get(reference_number=610)
    assert predecessor.superseded_by == Matter.objects.get(reference_number=601)
    assert verify_applied(build_plan(package)).ok


def test_a_continuation_whose_identity_differs_is_not_linked(world, pilot_on):
    other = "00000000-0000-0000-2026-999999999999"
    package = _active_package(_past_review(), _predecessor("2026_610", "2026_601", other))
    plan = build_plan(package)
    assert plan.topics[1].successor_in_batch == ""
    assert any("not the topic the stage link names" in w for w in plan.topics[1].warnings)
    _apply(package)
    assert Matter.all_objects.get(reference_number=610).superseded_by is None


def test_a_continuation_outside_the_package_touches_nothing(existing):
    native = existing["native"]
    package = _active_package(_past_review(), _predecessor("2026_610", "2026_7", _sid("2026_7")))
    plan = build_plan(package)
    assert plan.problems == [] and plan.topics[1].successor_in_batch == ""
    assert any("does not hold" in w for w in plan.topics[1].warnings)
    before = preserved_digests()
    _apply(package)
    assert Matter.all_objects.get(reference_number=610).superseded_by is None
    after = preserved_digests()
    assert after[str(native.pk)] == before[str(native.pk)]
    assert not Matter.all_objects.filter(superseded_by=native).exists()


def test_a_version_2_continuation_needs_the_structured_link():
    package = _active_package(_past_review(), _predecessor("2026_610", "2026_601", None))
    _refused(package, "a continuation needs source.successor_id")


def test_a_three_topic_cycle_is_refused():
    package = _active_package(
        _predecessor("2026_611", "2026_612", _sid("2026_612")),
        _predecessor("2026_612", "2026_613", _sid("2026_613")),
        _predecessor("2026_613", "2026_611", _sid("2026_611")),
    )
    problems = _problems(package)
    for reference in ("2026_611", "2026_612", "2026_613"):
        assert f"{reference}: its continuations lead back to it (a cycle)." in problems


def test_a_three_topic_cycle_is_refused_in_version_1_too():
    package = _pilot_package()
    extra = copy.deepcopy(package["topics"][3])
    extra.update(reference="2026_505", successor="2026_503")
    extra["source"]["row"]["nr"] = "2026_505"
    package["topics"][2].update(
        lifecycle="CONTINUES", disposition="SUPERSEDED", successor="2026_504"
    )
    package["topics"][3].update(successor="2026_505")
    package["topics"].append(extra)
    problems = _problems(package)
    assert "2026_503: its continuations lead back to it (a cycle)." in problems


# ---------------------------------------------------------------------------
# Source drift (docs/adr/0202 §5)
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, body: Any) -> None:
        self.raw = json.dumps(body).encode()

    def read(self, _n: int) -> bytes:
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _jwt() -> str:
    import base64

    body = base64.urlsafe_b64encode(json.dumps({"role": "authenticated"}).encode())
    return f"x.{body.decode().rstrip('=')}.y"


def _credentials(tmp_path) -> Any:
    path = tmp_path / "lovable-sync.env"
    path.write_text(
        "\n".join(
            [
                "LOVABLE_SYNC_URL=https://naidis.supabase.co",
                "LOVABLE_SYNC_PUBLISHABLE_KEY=sb_publishable_naidis",
                "LOVABLE_SYNC_EMAIL=sync@example.invalid",
                "LOVABLE_SYNC_PASSWORD=naidis-salasona",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _lovable_now() -> tuple[list[str], Any]:
    """Lovable after the snapshot: 2026_601 edited, 2026_602 renumbered, 2025_88 gone."""
    seen: list[str] = []
    rows = [
        {"id": _sid("2026_601"), "nr": "2026_601", "version": 4, "updated_at": "2026-11-01"},
        {"id": _sid("2026_602"), "nr": "2026_699", "version": 3, "updated_at": "2026-10-20"},
    ]

    def opener(request, timeout):
        seen.append(f"{request.get_method()} {request.full_url}")
        if "/auth/v1/token" in request.full_url:
            return _Response({"access_token": _jwt()})
        return _Response(rows)

    return seen, opener


def test_source_drift_reports_edits_renumbering_and_missing_topics_get_only(tmp_path):
    seen, opener = _lovable_now()
    transport = lovable_source.HttpTransport(
        lovable_source.load_config(_credentials(tmp_path)), opener=opener
    )
    drift = source_drift(_active_package(), transport)
    assert [(d["reference"], d["kinds"]) for d in drift] == [
        ("2025_88", ["MISSING"]),
        ("2026_601", ["VERSION"]),
        ("2026_602", ["RENUMBERED"]),
    ]
    reads = [line for line in seen if "/auth/v1/token" not in line]
    assert reads and all(line.startswith("GET ") for line in reads)
    assert [line for line in seen if not line.startswith("GET ")] == [
        "POST https://naidis.supabase.co/auth/v1/token?grant_type=password"
    ]
    assert all(
        "/rest/v1/topic?" in line and "id%2Cnr%2Cversion%2Cupdated_at" in line for line in reads
    )


def test_the_source_drift_command_prints_references_and_counts(tmp_path, monkeypatch, capsys):
    seen, opener = _lovable_now()
    real = lovable_source.HttpTransport
    monkeypatch.setattr(lovable_source, "HttpTransport", lambda config: real(config, opener=opener))
    path = tmp_path / "package.json"
    path.write_text(json.dumps(_active_package(), ensure_ascii=False), encoding="utf-8")
    call_command(
        "lovable_pilot",
        "source-drift",
        "--package",
        str(path),
        "--config",
        str(_credentials(tmp_path)),
        "--json",
    )
    printed = json.loads(capsys.readouterr().out)
    assert printed["cohort"] == OPERATIONAL_COHORT
    assert (printed["topics"], printed["drifted"]) == (3, 3)
    assert printed["changed_version"] == ["2026_601"]
    assert printed["renumbered"] == ["2026_602"]
    assert printed["missing"] == ["2025_88"]
    assert "Näidisteema" not in json.dumps(printed)
    assert all(line.startswith("GET ") for line in seen if "/auth/v1/token" not in line)


# ---------------------------------------------------------------------------
# Review fixes: identity, held fields, coverage, sends, apply day
# ---------------------------------------------------------------------------


def test_a_topic_listed_twice_under_two_references_is_refused():
    twin = _active("2026_620")
    twin["source"]["id"] = _sid("2026_601")
    _refused(_active_package(_past_review(), twin), "its Lovable identity is also 2026_601's")


def test_a_pilot_topic_renumbered_in_the_source_is_refused_at_plan(world, pilot_on):
    _apply(_pilot_package())
    pilot_id = MatterSourceReference.objects.get(matter__reference_number=501).source_row_raw[
        "lovable"
    ]["id"]
    renumbered = _active("2026_630")
    renumbered["source"]["id"] = pilot_id
    plan = build_plan(_active_package(renumbered))
    assert plan.database_state == "conflict"
    assert plan.identity_collisions == ["2026_630 (Juristid 2026_501)"]
    assert any("under their Lovable identity" in p for p in plan.problems)
    assert not any("Näidisteema" in p for p in plan.problems)
    with pytest.raises(LovablePilotError, match="never imported twice"):
        _apply(_active_package(renumbered))


def test_an_identity_held_by_a_deleted_teema_is_refused(world, pilot_on):
    _apply(_pilot_package())
    held = Matter.all_objects.get(reference_number=502)
    pilot_id = MatterSourceReference.objects.get(matter=held).source_row_raw["lovable"]["id"]
    Matter.all_objects.filter(pk=held.pk).update(
        deleted_at=dt.datetime(2026, 10, 30, tzinfo=dt.UTC)
    )
    renumbered = _active("2026_631")
    renumbered["source"]["id"] = pilot_id
    assert build_plan(_active_package(renumbered)).identity_collisions == [
        "2026_631 (Juristid 2026_502)"
    ]


def test_an_identity_held_by_a_synchronisation_state_is_refused(world, pilot_on):
    from app.legacy_import.lovable_sync_models import LovableSyncState

    native = factories.MatterFactory(reference_year=2026, reference_number=8)
    LovableSyncState.objects.create(matter=native, source_id=_sid("2026_601"), reference="2026_8")
    plan = build_plan(_active_package())
    assert plan.identity_collisions == ["2026_601 (Juristid 2026_8)"]


def test_an_applied_package_is_not_its_own_identity_collision(world, pilot_on):
    _apply(_active_package())
    plan = build_plan(_active_package())
    assert plan.database_state == "applied" and plan.identity_collisions == []


def test_the_held_columns_are_one_set_with_the_synchronisation():
    from app.legacy_import import lovable_sync

    assert lovable_sync.IMPORT_HELD_COLUMNS is lovable_pilot.HELD_FIELD_COLUMNS
    assert lovable_pilot.HELD_FIELD_COLUMNS["uus_vastutaja"] == "owner"


def test_a_held_field_nobody_would_hold_is_refused():
    topic = _not_sent()
    topic["field_decisions"]["markus"] = "HELD_FOR_REVIEW"
    _refused(_active_package(topic), "field(s) markus cannot be HELD_FOR_REVIEW")


@pytest.mark.parametrize(
    ("values", "sent"),
    [
        ({"valja": "2026-10-01"}, True),
        ({"valja": "saadetud"}, True),
        ({"valja_kuupaev": "2026-10-01"}, True),
        ({"valja": "Ei saatnud."}, False),
        ({"valja": ""}, False),
    ],
)
def test_an_open_request_the_source_answered_is_refused(values, sent):
    topic = _older_year()
    topic["source"]["row"].update(values)
    problems = _problems(_active_package(topic))
    assert any("the source records a send" in p for p in problems) is sent, problems


@pytest.mark.parametrize("wording", ["Ei saatnud.", "  ei   SAATNUD ", "«ei saatnud»"])
def test_not_sent_is_read_whatever_its_case_spacing_or_punctuation(wording):
    topic = _not_sent()
    topic["source"]["row"]["valja"] = wording
    topic["field_decisions"]["valja"] = "PRESERVED_AS_SOURCE_ONLY"
    _refused(_active_package(topic), "is held for review (field_decisions.valja)")


def test_a_past_opinion_deadline_with_nothing_sent_is_held():
    topic = _not_sent()
    topic["source"]["row"].update(valja_kuupaev=None)
    topic["field_decisions"].pop("arvamuse_tahtaeg")
    _refused(_active_package(topic), "(field_decisions.arvamuse_tahtaeg)")
    topic["field_decisions"]["arvamuse_tahtaeg"] = "HELD_FOR_REVIEW"
    assert _problems(_active_package(topic)) == []


def test_a_past_opinion_deadline_after_a_send_is_history():
    topic = _active("2026_630")
    topic["source"]["row"].update(arvamuse_tahtaeg="2026-09-15", valja="2026-09-10")
    assert _problems(_active_package(topic)) == []


def test_lopetatud_stays_terminal():
    topic = _not_sent()
    topic.update(
        lifecycle="INACTIVE",
        disposition="COMPLETED",
        status_raw="Lõpetatud",
        stage="",
        next_action=None,
    )
    topic["source"]["row"]["hetkeseis"] = "Lõpetatud"
    assert _problems(_active_package(topic)) == []


@pytest.mark.parametrize(
    ("where", "change"),
    [
        ("excel_source_row", lambda s: s.update(excel_source_row={"markmed": "x"})),
        (
            "history.fields",
            lambda s: s.update(
                history=[{"occurred_at": "2026-09-01", "action": "muutmine", "fields": ["markmed"]}]
            ),
        ),
        (
            "history",
            lambda s: s.update(history=[{"occurred_at": "2026-09-01", "changed_by": "x"}]),
        ),
        (
            "responsible_changes",
            lambda s: s.update(responsible_changes=[{"id": 1, "allikas": "x"}]),
        ),
    ],
)
def test_no_excluded_column_anywhere_in_the_source_copy(where, change):
    package = _active_package()
    change(package["topics"][0]["source"])
    _refused(package, f"source.{where}.")


def test_responsible_changes_as_the_package_carries_them_pass():
    package = _active_package()
    package["topics"][0]["source"]["responsible_changes"] = [
        {
            "id": 1,
            "topic_id": _sid("2026_601"),
            "vana_vastutaja": "Mari",
            "uus_vastutaja": "Jaan",
            "alates": "2026-09-01",
        }
    ]
    assert _problems(package) == []


@pytest.mark.parametrize("today", [dt.date(2026, 11, 21), dt.date(2026, 12, 1)])
def test_a_deadline_passed_by_the_apply_day_is_a_plan_problem(world, pilot_on, apply_day, today):
    apply_day["today"] = today
    plan = build_plan(_active_package())
    assert "2025_88: the step's deadline 2026-11-20 is before today; it would be overdue on " in (
        " ".join(plan.problems)
    )
    assert any("the open request's day 2026-11-20 is before today" in p for p in plan.problems)
    with pytest.raises(LovablePilotError, match="would be overdue on arrival"):
        _apply(_active_package())


def test_a_deadline_on_the_apply_day_itself_is_current(world, pilot_on, apply_day):
    apply_day["today"] = dt.date(2026, 11, 20)
    assert build_plan(_active_package()).problems == []


def test_a_version_1_plan_ignores_the_apply_day(world, apply_day):
    apply_day["today"] = dt.date(2027, 1, 1)
    assert build_plan(_pilot_package()).problems == []


def test_a_new_teema_continuing_under_an_existing_one_is_caught(existing):
    native = existing["native"]
    before = preserved_digests()
    factories.MatterFactory(reference_year=2026, reference_number=9, superseded_by=native)
    after = preserved_digests()
    assert after[str(native.pk)] != before[str(native.pk)]


def test_the_digest_covers_relations_from_either_side(existing, world):
    from app.related_materials.models import MatterRelation

    native = existing["native"]
    pilot_teema = Matter.objects.get(reference_number=501)
    before = preserved_digests()
    first, second = sorted([native, pilot_teema], key=lambda m: str(m.pk))
    MatterRelation.objects.create(
        matter_a=first,
        matter_b=second,
        linked_by=world["mari"],
        linked_at=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
    )
    after = preserved_digests()
    assert after[str(native.pk)] != before[str(native.pk)]
    assert after[str(pilot_teema.pk)] != before[str(pilot_teema.pk)]


def test_preserved_after_the_fact_ignores_nightly_observations(existing, tmp_path):
    from app.legacy_import.lovable_sync_models import LovableSyncState

    native = existing["native"]
    state = LovableSyncState.objects.create(
        matter=native, source_id=_sid("2026_699"), reference="2026_7", applied={"a": 1}
    )
    report = _apply(_active_package())
    document = lovable_pilot.preserved_document(
        report.preserved_settled_before, report.preserved_references
    )
    LovableSyncState.objects.filter(pk=state.pk).update(
        observed={"b": 2}, observed_version=9, updated_at=dt.datetime(2026, 11, 2, tzinfo=dt.UTC)
    )
    assert lovable_pilot.check_preserved(document).ok
    # Inside the transaction the same change is a difference.
    assert preserved_digests()[str(native.pk)] != report.preserved_before[str(native.pk)]
    LovableSyncState.objects.filter(pk=state.pk).update(applied={"a": 2})
    result = lovable_pilot.check_preserved(document)
    assert result.changed == ["2026_7"]


def test_apply_refuses_an_unwritable_digest_path_before_writing(world, pilot_on, tmp_path):
    package = _active_package()
    path = tmp_path / "package.json"
    path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    plan = build_plan(package)
    with pytest.raises(CommandError, match="nothing was applied"):
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
            str(tmp_path / "puudub" / "preserved.json"),
        )
    assert Matter.all_objects.count() == 0
