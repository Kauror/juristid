"""The source-authoritative replacement (docs/adr/0203), on synthetic data.

A synthetic world — the 0191 pilot (with a continuation), an operational
cohort, an evidence-pilot Teema with a stored document, a submission, an entry
and an engagement, a native Teema holding a reference the register's own topic
needs, an already deleted tombstone and a colleague's own settings — is
replaced by one package: every live Teema retired and its reference released,
the register created, everything outside the business dataset untouched and
the evidence bytes left on disk. Every title, name, reference and identity is
invented.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest
from django.core.management import CommandError, call_command

from app.audit.models import ChangeEvent, SecurityAuditEvent
from app.documents.services import evidence_storage
from app.legacy_import import lovable_pilot, lovable_sync
from app.legacy_import.lovable_pilot import (
    PILOT,
    REGISTER_COHORT,
    LovablePilotError,
    apply_plan,
    build_plan,
    dry_run,
    outside_digests,
    plan_digest,
    validate_package,
    verify_applied,
)
from app.legacy_import.models import EvidencePilotRecord, MatterSourceReference
from app.matters import deletion
from app.matters.enums import EngagementKind
from app.matters.models import Matter
from app.matters.services import add_engagement, add_entry, set_matter_title
from tests import factories
from tests.test_lovable_operational_import import _active
from tests.test_lovable_operational_import import _active_package as _operational_package
from tests.test_lovable_pilot import _package as _pilot_package

BACKUP = "sets/20261103T180000Z"


@pytest.fixture(autouse=True)
def apply_day(monkeypatch):
    monkeypatch.setattr(lovable_pilot, "_today", lambda: dt.date(2026, 11, 3))


@pytest.fixture
def pilot_on(settings):
    settings.LOVABLE_PILOT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


def _import(package: dict[str, Any]) -> None:
    plan = build_plan(package)
    assert plan.problems == [], plan.problems
    apply_plan(
        plan,
        operator_intent=lovable_pilot.package_cohort(package),
        expect_package_sha256=plan.digest,
        expect_plan_sha256=plan_digest(plan),
        backup_set="sets/20261026T160000Z",
    )


@pytest.fixture
def world(db, pilot_on, monkeypatch, capture_evidence, pdf_bytes):
    """The dataset before the replacement."""
    monkeypatch.setattr(lovable_pilot, "_today", lambda: dt.date(2026, 10, 27))
    mari = factories.UserFactory(upn="mari@example.invalid", display_name="Mari Näidis")
    jaan = factories.UserFactory(upn="jaan@example.invalid", display_name="Jaan Näidis")
    colleague = factories.UserFactory(upn="kolleeg@example.invalid", display_name="Kolleeg")
    area = factories.PolicyAreaFactory()
    _import(_pilot_package())  # 2026_501..504, 504 continuing under 501
    _import(_operational_package())  # 2026_601, 2026_602, 2025_88
    for reference in MatterSourceReference.objects.filter(source_system="LOVABLE"):
        lovable_sync.seed_state(reference)
    monkeypatch.setattr(lovable_pilot, "_today", lambda: dt.date(2026, 11, 3))

    evidence = factories.MatterFactory(reference_year=2026, reference_number=300, owner=mari)
    evidence.policy_areas.add(area)
    EvidencePilotRecord.objects.create(
        pilot="evidence-pilot",
        pilot_version="1",
        package_sha256="e" * 64,
        record_key="2026_300",
        record_kind="MATTER",
        matter=evidence,
    )
    version = capture_evidence(evidence, pdf_bytes, "eelnou.pdf", "application/pdf")
    factories.SubmissionFactory(matter=evidence)
    add_entry(matter=evidence, body="Sünteetiline märge", author=mari)
    add_engagement(matter=evidence, kind=EngagementKind.SURVEY, title="Näidiskaasamine", actor=mari)
    native = factories.MatterFactory(reference_year=2026, reference_number=7, owner=jaan)
    gone = factories.MatterFactory(reference_year=2026, reference_number=8)
    deletion.delete_matter(matter=gone, actor=None, expected_digest=None)
    return {
        "mari": mari,
        "jaan": jaan,
        "colleague": colleague,
        "area": area,
        "evidence": evidence,
        "evidence_key": version.storage_key,
        "native": native,
        "gone": gone,
    }


def _register_topic(reference: str, **overrides: Any) -> dict[str, Any]:
    topic = _active(reference, **overrides)
    topic["action"] = "CREATE"
    year, number = reference.split("_")
    topic["source"]["id"] = f"00000000-0000-4000-{year}-{int(number):012d}"
    if topic["next_action"]:
        topic["next_action"]["target_date"] = "2026-11-20"
    return topic


def _replacement(*topics: dict[str, Any]) -> dict[str, Any]:
    finished = _register_topic(
        "2026_702",
        status_raw="jõustunud",
        stage="in_force",
        lifecycle="INACTIVE",
        disposition="COMPLETED",
        next_action=None,
        owner_raw="",
    )
    finished["source"]["row"]["hetkeseis"] = "jõustunud"
    predecessor = _register_topic(
        "2026_703",
        status_raw="rohkem ei tegele",
        stage="monitoring_stopped",
        lifecycle="CONTINUES",
        disposition="SUPERSEDED",
        successor="2026_701",
        next_action=None,
        owner_raw="",
    )
    predecessor["source"]["row"]["hetkeseis"] = "rohkem ei tegele"
    predecessor["source"]["successor_id"] = "00000000-0000-4000-2026-000000000701"
    topics = topics or (
        _register_topic("2026_7"),  # the native Teema's reference: Lovable's own topic
        _register_topic("2026_701", owner_raw="Jaan"),
        finished,
        predecessor,
    )
    return {
        "format": lovable_pilot.PACKAGE_FORMAT,
        "format_version": lovable_pilot.ACTIVE_FORMAT_VERSION,
        "cohort": REGISTER_COHORT,
        "replace": True,
        "reviewer": "näidisülevaataja",
        "snapshot": {
            "as_of": "2026-11-03T05:00:00+00:00",
            "snapshot_sha256": "f" * 64,
            "project": "näidis",
            "highest_reference": {"2026": 720},
        },
        "people": {"Mari": "Mari Näidis", "Jaan": "Jaan Näidis"},
        "reserve_through": {"2026": 720},
        "expected_topics": len(topics),
        "topics": list(topics),
    }


def _apply(package: dict[str, Any], **overrides: Any):
    plan = build_plan(package)
    return apply_plan(
        plan,
        **{
            "operator_intent": REGISTER_COHORT,
            "expect_package_sha256": plan.digest,
            "expect_plan_sha256": plan_digest(plan),
            "backup_set": BACKUP,
            **overrides,
        },
    )


# ---------------------------------------------------------------------------
# validate and plan
# ---------------------------------------------------------------------------


def test_the_replacement_validates():
    assert validate_package(_replacement()).problems == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda p: p.pop("replace"), "a replacement (replace: true) is exactly"),
        (lambda p: p.update(cohort="lovable-operational"), "a replacement (replace: true)"),
        (lambda p: p.update(replace="jah"), "replace is true or absent"),
        (lambda p: p["topics"][0].pop("action"), "a replacement creates every topic"),
        (lambda p: p["topics"][0].update(action="REBASE"), "a replacement creates every topic"),
        (
            lambda p: p["topics"][1]["source"].update(id=p["topics"][0]["source"]["id"]),
            "its Lovable identity is also 2026_7's",
        ),
        (lambda p: p["topics"][0]["source"].pop("id"), "source.id and source.row"),
    ],
)
def test_a_replacement_package_is_refused_unless_it_says_so(change, message):
    package = _replacement()
    change(package)
    problems = validate_package(package).problems
    assert any(message in p for p in problems), problems


def test_the_plan_retires_every_live_teema(world):
    plan = build_plan(_replacement())
    assert plan.problems == [], plan.problems
    assert plan.database_state == "ready"
    references = sorted(r["reference"] for r in plan.retirement)
    assert references == sorted(
        [
            *("2026_501", "2026_502", "2026_503", "2026_504"),
            *("2026_601", "2026_602", "2025_88"),
            *("2026_300", "2026_7"),
        ]
    )
    assert plan.released == sorted(references)
    counts = lovable_pilot.replacement_counts(plan)
    assert counts["by_origin"] == {
        "evidence-pilot": 1,
        "lovable-operational": 3,
        "lovable-pilot": 4,
        "other": 1,
    }
    assert (counts["retired"], counts["released"], counts["created"], counts["blockers"]) == (
        9,
        9,
        4,
        0,
    )
    # The tombstone is left alone; its reference stays taken.
    assert str(world["gone"].pk) not in {r["pk"] for r in plan.retirement}


def test_a_released_reference_is_free_and_a_tombstones_is_not(world):
    plan = build_plan(_replacement(_register_topic("2026_8")))
    assert plan.collisions == ["2026_8"]


def test_a_legal_hold_blocks_the_plan(world):
    from app.documents.models import Document

    Document.objects.filter(matter=world["evidence"]).update(legal_hold=True)
    plan = build_plan(_replacement())
    assert "2026_300: cannot be retired (LEGAL_HOLD); nothing is replaced." in plan.problems
    with pytest.raises(LovablePilotError, match="The plan has problems"):
        _apply(_replacement())
    assert Matter.objects.count() == 9


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def test_the_replacement_leaves_exactly_the_register(world):
    user_pks = sorted(str(u.pk) for u in type(world["mari"]).objects.all())
    events_before = set(SecurityAuditEvent.objects.values_list("pk", flat=True))
    changes_before = set(ChangeEvent.objects.values_list("pk", flat=True))
    pilot_records = set(
        EvidencePilotRecord.objects.filter(pilot=PILOT).values_list("pk", flat=True)
    )
    report = _apply(_replacement())
    assert report.verify.ok, report.verify.discrepancies
    assert report.retired == {
        "evidence-pilot": 1,
        "lovable-operational": 3,
        "lovable-pilot": 4,
        "other": 1,
    }
    live = Matter.objects.all()
    assert sorted(m.display_reference for m in live) == [
        "2026_7",
        "2026_701",
        "2026_702",
        "2026_703",
    ]
    # The formerly colliding reference is Lovable's own topic now.
    new_7 = Matter.objects.get(reference_number=7)
    assert new_7.pk != world["native"].pk
    assert Matter.objects.get(reference_number=703).superseded_by.reference_number == 701

    # Tombstones: no reference, both events.
    native = Matter.all_objects.get(pk=world["native"].pk)
    assert native.deleted_at is not None
    assert (native.reference_year, native.reference_number) == (None, None)
    released = SecurityAuditEvent.objects.get(event_type="REFERENCE_RELEASED", subject_id=native.pk)
    assert released.actor is None
    assert released.detail["reference"] == "2026_7"
    assert released.detail["package_sha256"] == build_plan(_replacement()).digest
    deleted = SecurityAuditEvent.objects.get(event_type="MATTER_DELETED", subject_id=native.pk)
    assert deleted.detail["reference"] == "2026_7"
    assert deleted.detail["provenance"]["source"] == "LOVABLE_REPLACEMENT"
    # The earlier tombstone is untouched and keeps its reference.
    gone = Matter.all_objects.get(pk=world["gone"].pk)
    assert gone.display_reference == "2026_8"

    # Evidence bytes stay on disk as orphans.
    assert evidence_storage().exists(world["evidence_key"])
    # Outside the dataset: people, the pilots' records, every event.
    assert sorted(str(u.pk) for u in type(world["mari"]).objects.all()) == user_pks
    assert events_before <= set(SecurityAuditEvent.objects.values_list("pk", flat=True))
    assert changes_before <= set(ChangeEvent.objects.values_list("pk", flat=True))
    assert pilot_records <= set(EvidencePilotRecord.objects.values_list("pk", flat=True))
    assert world["area"].__class__.objects.filter(pk=world["area"].pk).exists()
    # Each live Teema: one register reference with its Lovable identity.
    for matter in live:
        source = MatterSourceReference.objects.get(matter=matter, source_system="LOVABLE")
        assert source.source_parser_version.startswith("lovable-register/")


def test_a_second_run_says_applied(world):
    _apply(_replacement())
    plan = build_plan(_replacement())
    assert plan.database_state == "applied", plan.problems
    assert verify_applied(plan).ok
    count = Matter.all_objects.count()
    assert _apply(_replacement()).already_applied
    assert Matter.all_objects.count() == count


def test_a_lawyer_save_between_plan_and_apply_refuses(world):
    plan = build_plan(_replacement())
    approved = plan_digest(plan)
    set_matter_title(matter=world["native"], value="Juristi muudatus", actor=world["jaan"])
    with pytest.raises(LovablePilotError, match="changed since it was approved"):
        apply_plan(
            plan,
            operator_intent=REGISTER_COHORT,
            expect_package_sha256=plan.digest,
            expect_plan_sha256=approved,
            backup_set=BACKUP,
        )
    assert Matter.objects.count() == 9


def test_the_dry_run_verifies_and_leaves_nothing(world):
    before = outside_digests(None)
    count = Matter.objects.count()
    report = dry_run(build_plan(_replacement()))
    assert report.dry_run and report.verify.ok, report.verify.discrepancies
    assert sum(report.retired.values()) == 9
    assert Matter.objects.count() == count
    assert Matter.all_objects.get(pk=world["native"].pk).display_reference == "2026_7"
    assert outside_digests(None) == before
    assert evidence_storage().exists(world["evidence_key"])


def test_an_interrupted_replacement_leaves_nothing(world, monkeypatch):
    def fails(self, topic_plan):
        raise RuntimeError("katkestus")

    count = Matter.objects.count()
    monkeypatch.setattr(lovable_pilot._Writer, "write_topic", fails)
    with pytest.raises(RuntimeError, match="katkestus"):
        _apply(_replacement())
    assert Matter.objects.count() == count
    assert not SecurityAuditEvent.objects.filter(event_type="REFERENCE_RELEASED").exists()
    assert build_plan(_replacement()).database_state == "ready"


def test_the_replacement_waits_for_no_synchronisation_run(world):
    import psycopg
    from django.db import connection

    params = connection.get_connection_params()
    other = psycopg.connect(**{k: v for k, v in params.items() if k != "cursor_factory"})
    try:
        other.execute(
            "SELECT pg_advisory_lock(%s, %s)", [lovable_sync.LOCK_NAMESPACE, lovable_sync.LOCK_KEY]
        )
        with pytest.raises(LovablePilotError, match="a synchronisation run is in progress"):
            _apply(_replacement())
    finally:
        other.close()


def test_the_operator_types_the_register_intent(world):
    with pytest.raises(LovablePilotError, match="--operator-intent lovable-register"):
        _apply(_replacement(), operator_intent="lovable-operational")


# ---------------------------------------------------------------------------
# the command and the after-the-fact check
# ---------------------------------------------------------------------------


def test_the_command_runs_the_replacement_and_checks_what_stayed(world, tmp_path, capsys):
    package = _replacement()
    path = tmp_path / "package.json"
    path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CommandError, match="runs only as `lovable_pilot replace --step apply`"):
        call_command("lovable_pilot", "apply", "--package", str(path))
    call_command("lovable_pilot", "replace", "--package", str(path), "--json")
    planned = json.loads(capsys.readouterr().out)
    assert planned["replace"]["retired"] == 9 and planned["replace"]["created"] == 4
    out = tmp_path / "preserved.json"
    call_command(
        "lovable_pilot",
        "replace",
        "--step",
        "apply",
        "--package",
        str(path),
        "--expect-package-sha256",
        planned["package_sha256"],
        "--expect-plan-sha256",
        planned["plan_sha256"],
        "--operator-intent",
        REGISTER_COHORT,
        "--backup-set",
        BACKUP,
        "--preserved-out",
        str(out),
        "--json",
    )
    applied = json.loads(capsys.readouterr().out)
    assert (applied["retired"], applied["released"], applied["created"]) == (9, 9, 4)
    call_command("lovable_pilot", "preserved", "--before", str(out), "--json")
    checked = json.loads(capsys.readouterr().out)
    assert checked["outside_changed"] == [] and checked["outside_checked"] > 10
    # A colleague's sign-in moves nothing that is compared afterwards.
    type(world["colleague"]).objects.filter(pk=world["colleague"].pk).update(
        last_login=dt.datetime(2026, 11, 4, tzinfo=dt.UTC)
    )
    call_command("lovable_pilot", "preserved", "--before", str(out), "--json")
    capsys.readouterr()
    # A changed organisation is caught.
    factories.OrganisationFactory()
    with pytest.raises(CommandError, match="pre-existing Teema"):
        call_command("lovable_pilot", "preserved", "--before", str(out), "--json")
    assert json.loads(capsys.readouterr().out)["outside_changed"] == ["organisations.Organisation"]


# ---------------------------------------------------------------------------
# the product's own deletion is unchanged
# ---------------------------------------------------------------------------


def test_the_products_deletion_still_removes_bytes_and_keeps_references_taken(
    db, capture_evidence, pdf_bytes, django_capture_on_commit_callbacks
):
    head = factories.DepartmentHeadFactory()
    doomed = factories.MatterFactory(reference_year=2026, reference_number=900)
    version = capture_evidence(doomed, pdf_bytes, "eelnou.pdf", "application/pdf")
    with django_capture_on_commit_callbacks(execute=True):
        deletion.permanently_delete_matter(
            matter=doomed,
            actor=head,
            confirmation=deletion.plan_matter_deletion(doomed).digest,
        )
    assert not evidence_storage().exists(version.storage_key)
    tomb = Matter.all_objects.get(pk=doomed.pk)
    assert tomb.display_reference == "2026_900"
    event = SecurityAuditEvent.objects.get(event_type="MATTER_DELETED", subject_id=doomed.pk)
    assert "provenance" not in event.detail
    assert not EvidencePilotRecord.objects.filter(matter=doomed).exists()


def test_the_system_door_refuses_without_the_replacements_provenance(db):
    from tests.refusals import refused

    matter = factories.MatterFactory()
    with refused(deletion.DELETION_REFUSED):
        deletion.retire_for_source_replacement(
            matter=matter, provenance={"source": "OTHER"}, expected_digest="x"
        )
    with refused(deletion.DELETION_REFUSED):
        deletion.delete_matter(matter=matter, retain=frozenset({"documents.Document"}))
    assert Matter.objects.filter(pk=matter.pk).exists()
