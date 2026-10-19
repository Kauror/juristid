"""The evidence pilot (docs/adr/0173), on a synthetic package.

Every title, name, organisation and file below is invented; the shapes are the
real package's. The tests walk the three steps — validate, plan, apply — and
pin the rules the brief set: every file accounted for and re-hashed, nothing
newer than the snapshot, no sent opinion without its real final letter, no
organisation or person invented, a held record never written, an apply that
refuses without each authorisation and writes nothing the second time, and a
provenance row for everything it wrote. The last block covers the reconciliation
round (0173 §6): reviewed organisations, joint letters, rejected records,
planned milestones and a reply that answers the current request.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.audit.models import ChangeEvent
from app.documents.models import Document
from app.intelligence.models import MatterEffectiveDate
from app.legacy_import import evidence_pilot
from app.legacy_import.evidence_pilot import (
    EvidencePilotError,
    apply_plan,
    build_plan,
    load_package,
    package_digest,
    validate_package,
    verify_applied,
)
from app.legacy_import.models import EvidencePilotRecord
from app.legacy_import.source_pages import LegacySourceResourceImport, ResourceImportState
from app.matters.entry_enums import EntryKind
from app.matters.models import (
    Entry,
    Matter,
    MatterEngagement,
    MatterExternalPosition,
    MatterProceduralDevelopment,
    MatterWebsiteOverview,
)
from app.submissions.enums import SentAtPrecision, SubmissionStatus
from app.submissions.models import Submission
from app.workflow.models import NextAction, OpinionFollowUp
from tests import factories

YEAR = 2025
NOTEBOOK = "Näidiskoja õigusloome"


def _asice(inner_name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/vnd.etsi.asic-e+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr(inner_name, b"%PDF-1.4\n% naidisarvamus\n")
        archive.writestr("META-INF/signatures0.xml", b"<XAdESSignatures/>")
    return buffer.getvalue()


PAGES: dict[str, dict[str, Any]] = {
    "p-one": {
        "title": "Näidisseaduse muutmise eelnõu",
        "section": "Näidissektsioon",
        "resources": [
            ("r001", "Ministeeriumi kiri.pdf", b"%PDF-1.4\n% naidiskiri\n"),
            ("r002", "Koja arvamus.asice", _asice("Koja arvamus.pdf")),
            ("r003", "Liikme vastus.pdf", b"%PDF-1.4\n% liikme vastus\n"),
            ("r004", "tühi.docx", b""),
            ("r005", "Kaasatud liikmed.pdf", b"%PDF-1.4\n% nimekiri\n"),
        ],
    },
    "p-two": {
        "title": "Näidismääruse eelnõu",
        "section": "Näidissektsioon",
        "resources": [("r001", "Eelnõu.pdf", b"%PDF-1.4\n% eelnou\n")],
    },
}


def _write_archive(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "archive.json").write_text(json.dumps({"archiveVersion": "desktop/1"}), "utf-8")
    for order, (key, spec) in enumerate(PAGES.items(), start=1):
        page = root / "pages" / key
        capture = page / "captures" / "c1"
        capture.mkdir(parents=True)
        xml = f'<?xml version="1.0"?><one:Page ID="{key}"/>'.encode()
        (capture / "page.source.xml").write_bytes(xml)
        (capture / "page.txt").write_text(spec["title"], encoding="utf-8")
        blocks = [{"type": "TEXT", "sourceOrdinal": 1, "text": spec["title"], "depth": 0}]
        for ordinal, (rkey, name, content) in enumerate(spec["resources"], start=2):
            blocks.append(
                {"type": "FILE_ATTACHMENT", "sourceOrdinal": ordinal, "text": name, "depth": 0}
            )
            holder = page / "resources" / rkey
            (holder / "original").mkdir(parents=True)
            (holder / "original" / name).write_bytes(content)
            (holder / "resource.json").write_text(
                json.dumps(
                    {
                        "archiveResourceKey": rkey,
                        "originalFilename": name,
                        "resourceKind": "FILE_ATTACHMENT",
                        "sourceOrdinal": ordinal,
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "sizeBytes": len(content),
                        "originalPath": f"resources\\{rkey}\\original\\{name}",
                        "inlineOrFile": "file",
                        "downloadStatus": "CAPTURED" if content else "CAPTURED_EMPTY",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        (capture / "blocks.json").write_text(
            json.dumps({"blocks": blocks, "readingOrder": "VISUAL_THEN_XML"}), "utf-8"
        )
        (capture / "links.json").write_text(json.dumps({"links": []}), "utf-8")
        (page / "page.json").write_text(
            json.dumps(
                {
                    "archivePageKey": key,
                    "title": spec["title"],
                    "level": 1,
                    "observedPageOrder": order,
                    "createdDateTime": "2025-01-10T08:00:00Z",
                    "lastModifiedDateTime": "2025-03-20T08:00:00Z",
                    "source": {"pageId": "{" + key + "}", "hyperlink": "onenote:#" + key},
                    "section": {"displayName": spec["section"]},
                    "originalSource": {
                        "notebookName": NOTEBOOK,
                        "sectionName": spec["section"],
                        "pageTitle": spec["title"],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (page / "current.json").write_text(
            json.dumps({"captureId": "c1", "xmlSha256": hashlib.sha256(xml).hexdigest()}), "utf-8"
        )


def _sha(page: str, key: str) -> str:
    content = {k: c for k, _n, c in PAGES[page]["resources"]}[key]
    return hashlib.sha256(content).hexdigest()


def _evidence(*blocks: int) -> dict:
    return {"blocks": list(blocks)}


def _package() -> dict:
    one = f"{YEAR}_5"
    return {
        "format": evidence_pilot.PACKAGE_FORMAT,
        "format_version": evidence_pilot.PACKAGE_FORMAT_VERSION,
        "pilot": "näidispakett",
        "data_class": "SYNTHETIC",
        "snapshot": {"as_of": f"{YEAR}-03-31"},
        "reference_year": YEAR,
        "reserve_through": 9,
        "expected": {"topics": 2, "owners": {"Mari": 1, "Jaan": 1}},
        "organisation_mappings": {
            "Vana Näidisministeerium": {"target": "Näidisministeerium", "status": "PROPOSED"}
        },
        "topics": [
            {
                "reference": one,
                "title": "Näidisseaduse muutmise seaduse eelnõu",
                "alternate_titles": ["Näidisseaduse muutmise eelnõu"],
                "owner": "Mari",
                "legal_instrument": "seadus",
                "stage_label": "Riigikogus",
                "received_date": f"{YEAR}-02-01",
                "response_deadline": f"{YEAR}-04-15",
                "addressee": "Riigikogu näidiskomisjon",
                "next_action": {
                    "text": "Koostan arvamust",
                    "target_date": f"{YEAR}-04-14",
                    "review": "READY",
                    "evidence": {"source": "näidisallikas"},
                },
                "source_page": {"page_key": "p-one"},
                "source_state": {"hetkeseis": "Riigikogus"},
                "records": [
                    {
                        "key": f"{one}-E01",
                        "target": "ProceduralDevelopment",
                        "review": "READY",
                        "date": f"{YEAR}-02-01",
                        "date_precision": "DAY",
                        "title": "Ministeerium saatis eelnõu arvamuse avaldamiseks",
                        "evidence": _evidence(2),
                        "documents": [
                            {
                                "resource_key": "r001",
                                "role": "INCOMING_AUTHORITY",
                                "sha256": _sha("p-one", "r001"),
                            }
                        ],
                    },
                    {
                        "key": f"{one}-E02",
                        "target": "MatterEngagement",
                        "review": "READY",
                        "kind": "EMAIL_CAMPAIGN",
                        "history": True,
                        "date": f"{YEAR}-02",
                        "date_precision": "MONTH",
                        "title": "Näidisliikmed",
                        "evidence": _evidence(6),
                        "documents": [
                            {"resource_key": "r005", "role": "WORKING_DOCUMENT"},
                        ],
                    },
                    {
                        "key": f"{one}-E03",
                        "target": "MatterExternalPosition.RECEIVED",
                        "review": "READY",
                        "date": f"{YEAR}-02-10",
                        "date_precision": "DAY",
                        "source_label": "Näidisettevõte OÜ",
                        "source_is_member": True,
                        "engagement_key": f"{one}-E02",
                        "summary": "Toetab eelnõu.",
                        "evidence": _evidence(4),
                        "documents": [{"resource_key": "r003", "role": "MEMBER_FEEDBACK"}],
                    },
                    {
                        "key": f"{one}-E04",
                        "target": "Submission.SENT",
                        "review": "READY",
                        "date": f"{YEAR}-02-20",
                        "date_precision": "DAY",
                        "title": "Koja arvamus näidisseaduse eelnõu kohta",
                        "recipients": ["Näidisministeerium"],
                        "evidence": _evidence(3),
                        "documents": [
                            {
                                "resource_key": "r002",
                                "role": "KODA_SUBMISSION_FINAL",
                                "final": True,
                            }
                        ],
                    },
                    {
                        "key": f"{one}-E05",
                        "target": "MatterWebsiteOverview.OVERVIEW",
                        "review": "READY",
                        "date": f"{YEAR}-02-05",
                        "date_precision": "DAY",
                        "title": "Mida arvad näidisseadusest?",
                        "url": "https://www.koda.ee/et/naidis",
                        "evidence": {"web": [{"n": 1}]},
                    },
                    {
                        "key": f"{one}-E06",
                        "target": "Entry.NOTE",
                        "review": "READY",
                        "date": f"{YEAR}-03-01",
                        "date_precision": "DAY",
                        "body": "Ettepanekut on osaliselt arvestatud.",
                        "evidence": _evidence(1),
                    },
                    {
                        "key": f"{one}-E07",
                        "target": "MatterEffectiveDate",
                        "review": "READY",
                        "kind": "KNOWN_DATE",
                        "date": f"{YEAR + 1}-05-27",
                        "date_precision": "DAY",
                        "title": "Eeldatav jõustumine",
                        "evidence": {"source": "näidisallikas"},
                    },
                    {
                        "key": f"{one}-E08",
                        "target": "ReviewOnly",
                        "review": "NEEDS_REVIEW",
                        "title": "Võimalik teine arvamus",
                        "evidence": _evidence(1),
                    },
                ],
                "documents_unlinked": [],
                "resources_not_imported": [
                    {"resource_key": "r004", "code": "EMPTY_AT_SOURCE", "reason": "0 baiti"}
                ],
            },
            {
                "reference": f"{YEAR}_6",
                "title": "Näidismääruse eelnõu",
                "owner": "Jaan",
                "legal_instrument": "määrus",
                "stage_label": "kooskõlastusringil",
                "received_date": f"{YEAR}-03-01",
                "response_deadline": f"{YEAR}-03-20",
                "next_action": {
                    "text": "Arvamuse tähtaeg 20.03.",
                    "target_date": f"{YEAR}-03-20",
                    "review": "NEEDS_REVIEW",
                },
                "source_page": {"page_key": "p-two"},
                "records": [],
                "documents_unlinked": [
                    {"resource_key": "r001", "role": "INCOMING_AUTHORITY", "review": "READY"}
                ],
                "resources_not_imported": [],
            },
        ],
    }


def _pinned(package: dict) -> dict:
    """Every file the package accounts for, pinned to its bytes — as a real build does."""
    for topic in package["topics"]:
        page = topic["source_page"]["page_key"]
        docs = [d for r in topic.get("records") or [] for d in r.get("documents") or []]
        docs += list(topic.get("documents_unlinked") or [])
        docs += list(topic.get("resources_not_imported") or [])
        for doc in docs:
            doc.setdefault("sha256", _sha(page, doc["resource_key"]))
    return package


def _write(root: Path, package: dict) -> Path:
    (root / "package.json").write_text(json.dumps(_pinned(package), ensure_ascii=False), "utf-8")
    return root


@pytest.fixture
def package_root(tmp_path: Path) -> Path:
    root = tmp_path / "package"
    _write_archive(root / evidence_pilot.ARCHIVE_DIRECTORY)
    return _write(root, _package())


@pytest.fixture
def world(db):
    mari = factories.UserFactory(upn="mari@example.invalid", display_name="Mari Näidis")
    jaan = factories.UserFactory(upn="jaan@example.invalid", display_name="Jaan Näidis")
    for name in ("Näidisministeerium", "Riigikogu näidiskomisjon"):
        factories.OrganisationFactory(name=name)
    return {"mari": mari, "jaan": jaan}


@pytest.fixture
def pilot_on(settings):
    settings.EVIDENCE_PILOT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


def _apply(root: Path, **overrides):
    plan = build_plan(root)
    arguments = {
        "operator_intent": evidence_pilot.OPERATOR_INTENT,
        "expect_package_sha256": plan.digest,
        "backup_set": "sets/20250331T000000Z",
        **overrides,
    }
    return apply_plan(plan, **arguments)


def _validate(root: Path, change=None) -> list[str]:
    package, archive = load_package(root)
    if change is not None:
        package = copy.deepcopy(package)
        change(package)
    return validate_package(package, archive).problems


# ---------------------------------------------------------------------------
# validate: no database
# ---------------------------------------------------------------------------


def test_the_synthetic_package_validates(package_root):
    package, archive = load_package(package_root)
    report = validate_package(package, archive)
    assert report.problems == []
    assert report.topics == 2
    assert report.records_ready == 7
    assert report.records_held == 1
    assert report.resources_on_pages == report.resources_accounted == 6


def _one(package):
    return package["topics"][0]


def _record(package, suffix):
    return next(r for r in _one(package)["records"] if r["key"].endswith(suffix))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda p: p["topics"].append(copy.deepcopy(p["topics"][1])), "appears twice"),
        (lambda p: p["expected"]["owners"].update({"Mari": 2}), "owner 'Mari'"),
        (
            lambda p: _record(p, "E01")["documents"][0].update({"sha256": "0" * 64}),
            "SHA-256 in the package differs",
        ),
        (lambda p: _one(p).update({"resources_not_imported": []}), "not accounted for: r004"),
        (
            lambda p: _record(p, "E03")["documents"].append(
                {"resource_key": "r001", "role": "OTHER"}
            ),
            "r001 is used by",
        ),
        (lambda p: _record(p, "E06").update({"date": f"{YEAR}-04-02"}), "after the snapshot"),
        (lambda p: _record(p, "E04")["documents"][0].update({"final": False}), "exactly one final"),
        (lambda p: _record(p, "E04").update({"recipients": []}), "at least one recipient"),
        (
            lambda p: _record(p, "E03").update(
                {"target": "MatterExternalPosition.DISCOVERED", "engagement_key": None}
            ),
            "names an organisation",
        ),
        (
            lambda p: _one(p).update(
                {
                    "resources_not_imported": [],
                    "documents_unlinked": [
                        {"resource_key": "r004", "role": "OTHER", "review": "READY"}
                    ],
                }
            ),
            "which has no bytes",
        ),
        (lambda p: _record(p, "E08").update({"review": "READY"}), "never imported"),
        (
            lambda p: _record(p, "E06").update({"date_precision": "MONTH", "date": f"{YEAR}-03"}),
            "exact day",
        ),
        (lambda p: _record(p, "E05").update({"evidence": {}}), "names no evidence"),
        (
            lambda p: (
                _record(p, "E05").update({"documents": [{"resource_key": "r005", "role": "OTHER"}]})
                or _record(p, "E02").update({"documents": []})
            ),
            "an overview carries no files",
        ),
        (lambda p: _one(p).update({"stage_label": "kuskil mujal"}), "not a known label"),
        (
            lambda p: _record(p, "E03").update({"engagement_key": f"{YEAR}_5-E99"}),
            "not a READY round",
        ),
    ],
)
def test_the_package_is_refused_for_each_broken_rule(package_root, change, message):
    problems = _validate(package_root, change)
    assert any(message in problem for problem in problems), problems


def test_every_imported_byte_is_rehashed(package_root):
    original = (
        package_root / evidence_pilot.ARCHIVE_DIRECTORY / "pages/p-one/resources/r003/original"
    )
    (original / "Liikme vastus.pdf").write_bytes(b"%PDF-1.4\n% muudetud\n")
    problems = _validate(package_root)
    assert any("r003 bytes do not hash" in problem for problem in problems)


def test_a_newer_fact_is_held_not_refused(package_root):
    def newer(p):
        _record(p, "E06").update({"date": f"{YEAR}-04-02", "review": "NEEDS_REVIEW"})

    assert _validate(package_root, newer) == []


def test_the_digest_is_the_canonical_json():
    package = _package()
    reordered = json.loads(json.dumps(package, sort_keys=True))
    assert package_digest(package) == package_digest(reordered)
    changed = copy.deepcopy(package)
    changed["topics"][0]["title"] += "."
    assert package_digest(changed) != package_digest(package)


# ---------------------------------------------------------------------------
# plan: reads only
# ---------------------------------------------------------------------------


def test_the_plan_resolves_people_stages_and_organisations(world, package_root):
    plan = build_plan(package_root)
    assert plan.problems == []
    assert plan.database_state == "fresh"
    one, two = plan.topics
    assert one.owner == world["mari"] and two.owner == world["jaan"]
    assert one.stage.key == "parliament" and two.stage.key == "consultation"
    assert [i.key for i in one.instruments]
    assert one.addressee is not None
    assert plan.expected_writes() == {
        "MATTER": 2,
        "NEXT_ACTION": 1,
        "DOCUMENT": 5,
        "ProceduralDevelopment": 1,
        "MatterEngagement": 1,
        "MatterExternalPosition.RECEIVED": 1,
        "Submission.SENT": 1,
        "MatterWebsiteOverview.OVERVIEW": 1,
        "Entry.NOTE": 1,
        "MatterEffectiveDate": 1,
    }


def test_an_organisation_outside_the_catalogue_blocks_the_plan(world, package_root):
    package = _package()
    _record(package, "E04")["recipients"] = ["Vana Näidisministeerium"]
    plan = build_plan(_write(package_root, package))
    assert any("'Vana Näidisministeerium' is not in the catalogue" in p for p in plan.problems)


def test_only_an_approved_mapping_resolves_an_organisation(world, package_root):
    package = _package()
    _record(package, "E04")["recipients"] = ["Vana Näidisministeerium"]
    package["organisation_mappings"]["Vana Näidisministeerium"]["status"] = "APPROVED"
    plan = build_plan(_write(package_root, package))
    assert plan.problems == []


def test_an_unknown_addressee_is_a_warning_and_is_not_recorded(world, package_root):
    package = _package()
    _one(package)["addressee"] = "Näidisamet"
    plan = build_plan(_write(package_root, package))
    assert plan.problems == []
    assert plan.topics[0].addressee is None
    assert any("Näidisamet" in w for w in plan.topics[0].warnings)


def test_an_owner_who_is_not_exactly_one_account_blocks_the_plan(world, package_root):
    factories.UserFactory(upn="mari2@example.invalid", display_name="Mari Teine")
    plan = build_plan(package_root)
    assert any("owner 'Mari' does not resolve" in p for p in plan.problems)


# ---------------------------------------------------------------------------
# apply: gated, one transaction, idempotent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "overrides", "message"),
    [
        ("EVIDENCE_PILOT_ENABLED", {}, "JURISTID_EVIDENCE_PILOT"),
        ("REAL_DATA_ALLOWED", {}, "REAL_DATA_ALLOWED is off"),
        (None, {"operator_intent": "jah"}, "--operator-intent evidence-pilot"),
        (None, {"expect_package_sha256": "0" * 64}, "pass exactly that"),
        (None, {"backup_set": " "}, "Name the verified backup set"),
    ],
)
def test_the_apply_refuses_without_each_authorisation(
    world, package_root, pilot_on, setting, overrides, message
):
    if setting:
        setattr(pilot_on, setting, False)
    with pytest.raises(EvidencePilotError, match=message):
        _apply(package_root, **overrides)
    assert Matter.all_objects.count() == 0
    assert EvidencePilotRecord.objects.count() == 0


def test_the_apply_refuses_a_database_with_other_business_data(world, package_root, pilot_on):
    factories.MatterFactory()
    with pytest.raises(EvidencePilotError, match="this package did not create"):
        _apply(package_root)


def test_the_apply_refuses_a_plan_with_problems(world, package_root, pilot_on):
    package = _package()
    _record(package, "E04")["recipients"] = ["Vana Näidisministeerium"]
    with pytest.raises(EvidencePilotError, match="The plan has problems"):
        _apply(_write(package_root, package))
    assert Matter.all_objects.count() == 0


def _matter(reference: str) -> Matter:
    year, number = reference.split("_")
    return Matter.all_objects.get(reference_year=int(year), reference_number=int(number))


def test_the_apply_writes_the_package_through_the_domain(world, package_root, pilot_on):
    report = _apply(package_root)
    assert not report.already_applied

    one = _matter(f"{YEAR}_5")
    assert one.title == "Näidisseaduse muutmise seaduse eelnõu"
    assert one.alternate_titles == ["Näidisseaduse muutmise eelnõu"]
    assert one.owner == world["mari"]
    assert one.stage is not None and one.stage.key == "parliament"
    assert one.response_deadline == dt.date(YEAR, 4, 15)
    assert one.received_date == dt.date(YEAR, 2, 1)
    created = ChangeEvent.objects.get(matter=one, event_type="MATTER_CREATED")
    assert created.actor is None
    assert created.payload["package_sha256"] == build_plan(package_root).digest

    step = NextAction.objects.get(matter=one)
    assert step.text == "Koostan arvamust" and step.target_date == dt.date(YEAR, 4, 14)
    assert step.responsible == world["mari"]
    # A step held for review is not written.
    assert not NextAction.objects.filter(matter=_matter(f"{YEAR}_6")).exists()

    development = MatterProceduralDevelopment.objects.get(matter=one)
    assert development.occurred_on == dt.date(YEAR, 2, 1)

    engagement = MatterEngagement.objects.get(matter=one)
    assert engagement.lifecycle_tracked is False
    assert engagement.occurred_on == dt.date(YEAR, 2, 1)
    assert engagement.occurred_on_precision == "MONTH"

    position = MatterExternalPosition.objects.get(matter=one)
    assert position.provenance == "RECEIVED"
    assert position.engagement == engagement
    assert position.source_label == "Näidisettevõte OÜ"

    submission = Submission.objects.get(matter=one)
    assert submission.status == SubmissionStatus.SENT
    assert submission.sent_at_precision == SentAtPrecision.DATE
    final = submission.final_version
    assert final is not None
    assert final.document.role == "KODA_SUBMISSION_FINAL"
    assert final.sha256 == _sha("p-one", "r002")
    assert [r.name for r in submission.recipients.all()] == ["Näidisministeerium"]

    overview = MatterWebsiteOverview.objects.get(matter=one)
    assert overview.url == "https://www.koda.ee/et/naidis"
    assert overview.published_on == dt.date(YEAR, 2, 5)

    note = Entry.objects.get(matter=one)
    assert note.kind == EntryKind.NOTE and note.author is None

    commencement = MatterEffectiveDate.objects.get(matter=one)
    assert commencement.date_value == dt.date(YEAR + 1, 5, 27)

    # Every file is the archive's bytes; the empty one is accounted, not stored.
    assert Document.objects.filter(matter=one).count() == 4
    states = dict(
        LegacySourceResourceImport.objects.filter(matter_source_page__matter=one).values_list(
            "resource__resource_key", "state"
        )
    )
    assert states["r004"] == ResourceImportState.SKIPPED
    assert sum(1 for s in states.values() if s == ResourceImportState.IMPORTED) == 4

    # Nothing the pilot wrote has a person behind it, and no check was scheduled.
    assert not ChangeEvent.objects.filter(matter__in=[one], actor__isnull=False).exists()
    assert OpinionFollowUp.objects.count() == 0
    assert report.held == [f"{YEAR}_5-E08", f"{YEAR}_6-NEXT"]


def test_an_earlier_rounds_opinion_does_not_answer_the_current_deadline(
    world, package_root, pilot_on
):
    from app.matters.work_items import response_obligation_of

    _apply(package_root)
    one = _matter(f"{YEAR}_5")
    assert Submission.objects.filter(matter=one, status=SubmissionStatus.SENT).exists()
    assert one.response_requested_at is not None
    assert response_obligation_of(one, world["mari"]).is_outstanding


def test_every_written_record_has_its_provenance_row(world, package_root, pilot_on):
    _apply(package_root)
    plan = build_plan(package_root)
    kinds: dict[str, int] = {}
    for row in EvidencePilotRecord.objects.filter(package_sha256=plan.digest):
        kinds[row.record_kind] = kinds.get(row.record_kind, 0) + 1
    assert kinds == plan.expected_writes()


def test_a_second_apply_writes_nothing(world, package_root, pilot_on):
    _apply(package_root)
    before = (
        Matter.all_objects.count(),
        EvidencePilotRecord.objects.count(),
        Document.objects.count(),
    )
    assert build_plan(package_root).database_state == "applied"
    assert _apply(package_root).already_applied
    after = (
        Matter.all_objects.count(),
        EvidencePilotRecord.objects.count(),
        Document.objects.count(),
    )
    assert before == after


def test_a_different_package_does_not_apply_over_this_one(world, package_root, pilot_on):
    _apply(package_root)
    package = _package()
    package["topics"][1]["title"] = "Näidismääruse eelnõu (muudetud)"
    with pytest.raises(EvidencePilotError, match="this package did not create"):
        _apply(_write(package_root, package))


def test_verify_reads_back_what_was_written(world, package_root, pilot_on):
    _apply(package_root)
    assert verify_applied(build_plan(package_root)).problems == []

    Matter.all_objects.filter(reference_number=5).update(response_deadline=dt.date(YEAR, 4, 1))
    problems = verify_applied(build_plan(package_root)).problems
    assert any("Arvamuse tähtaeg differs" in p for p in problems)


# ---------------------------------------------------------------------------
# the reconciliation round: reviewed organisations, joint letters, rejected
# records, planned milestones, a reply that answers the current request
# ---------------------------------------------------------------------------


def _organisation(name: str, kind: str = "ASSOCIATION", **extra) -> dict:
    return {
        "name": name,
        "organisation_type": kind,
        "status": "APPROVED",
        "evidence": {"blocks": [4], "letterhead": name},
        **extra,
    }


def _discovered(key: str, organisation: str, **extra) -> dict:
    return {
        "key": f"{YEAR}_5-{key}",
        "target": "MatterExternalPosition.DISCOVERED",
        "review": "READY",
        "date": f"{YEAR}-02-12",
        "date_precision": "DAY",
        "organisation": organisation,
        "summary": "Ühispöördumine.",
        "evidence": _evidence(4),
        **extra,
    }


def _joint_letter_package() -> dict:
    """Two signatories of one letter: the file is filed once and supports both."""
    package = _package()
    package["organisations"] = [
        _organisation("Näidisliit", registry_code="80000001", notes="Kirjapäisest."),
        _organisation("Teine Näidisliit"),
    ]
    _record(package, "E03")["documents"] = []
    _one(package)["records"] += [
        _discovered(
            "E09",
            "Näidisliit",
            documents=[{"resource_key": "r003", "role": "EXTERNAL_POSITION"}],
        ),
        _discovered("E10", "Teine Näidisliit", shared_documents_from=f"{YEAR}_5-E09"),
    ]
    return package


def _milestone(date: str, precision: str = "QUARTER") -> dict:
    return {
        "key": f"{YEAR}_5-E11",
        "target": "MatterImportantDate",
        "review": "READY",
        "date": date,
        "date_precision": precision,
        "title": "Valitsus plaanib eelnõu heaks kiita",
        "note": "Üks allikas ütleb «novembris», teine «aasta lõpuks».",
        "source_text": "novembris",
        "evidence": _evidence(1),
    }


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda p: p.update({"organisations": [_organisation("Näidisliit", evidence={})]}),
            "names no evidence for its identity",
        ),
        (
            lambda p: p.update({"organisations": [_organisation("Näidisliit", "KLUBI")]}),
            "type 'KLUBI' is unknown",
        ),
        (
            lambda p: p.update({"organisations": [_organisation("A"), _organisation("A")]}),
            "appears twice",
        ),
        (
            lambda p: p.update(
                {"organisations": [_organisation("Näidisliit", valid_to="2025-13-01")]}
            ),
            "valid_to is not a date",
        ),
        (lambda p: _record(p, "E08").update({"review": "REJECTED"}), "says why (decision_reason)"),
        (lambda p: _one(p).update({"answered_by": f"{YEAR}_5-E03"}), "not a READY sent opinion"),
        (
            lambda p: p["topics"][1].update({"answered_by": f"{YEAR}_5-E04"}),
            "not a READY sent opinion",
        ),
        (
            lambda p: _record(p, "E04").update({"submission_kind": "TELEGRAMM"}),
            "submission kind 'TELEGRAMM' is unknown",
        ),
        (
            lambda p: _one(p)["records"].append(_milestone(f"{YEAR}-Q5")),
            "does not read as QUARTER",
        ),
    ],
)
def test_the_reconciliation_fields_are_refused_when_broken(package_root, change, message):
    problems = _validate(package_root, change)
    assert any(message in problem for problem in problems), problems


def test_a_joint_letters_signatory_shares_the_letter_and_lists_none_of_its_own(package_root):
    def own_files_too(p):
        p.update(_joint_letter_package())
        _record(p, "E10")["documents"] = [{"resource_key": "r005", "role": "OTHER"}]

    problems = _validate(package_root, own_files_too)
    assert any("lists none of its own" in problem for problem in problems), problems

    def shares_a_fileless_record(p):
        p.update(_joint_letter_package())
        _record(p, "E10")["shared_documents_from"] = f"{YEAR}_5-E03"

    problems = _validate(package_root, shares_a_fileless_record)
    assert any("is not a READY position" in problem for problem in problems), problems


def test_a_proposed_organisation_is_not_created_and_blocks_its_record(world, package_root):
    package = _joint_letter_package()
    package["organisations"][1]["status"] = "PROPOSED"
    plan = build_plan(_write(package_root, package))
    assert any("'Teine Näidisliit' is not in the catalogue" in p for p in plan.problems)


def test_an_approved_organisation_is_created_once_with_its_reviewed_identity(
    world, package_root, pilot_on
):
    from app.organisations.models import Organisation

    package_root = _write(package_root, _joint_letter_package())
    plan = build_plan(package_root)
    assert plan.problems == []
    assert plan.expected_writes()["ORGANISATION"] == 2
    report = _apply(package_root)
    assert report.organisations_created == ["Näidisliit", "Teine Näidisliit"]

    liit = Organisation.objects.get(name="Näidisliit")
    assert liit.organisation_type == "ASSOCIATION"
    assert liit.registry_code == "80000001"
    assert liit.notes == "Kirjapäisest."
    row = EvidencePilotRecord.objects.get(record_key="ORG:Näidisliit")
    assert row.record_kind == "ORGANISATION" and row.matter is None
    assert row.target_id == liit.pk and row.evidence["created"] is True
    assert verify_applied(build_plan(package_root)).problems == []


def test_a_catalogue_organisation_is_reused_and_left_as_it_was(world, package_root, pilot_on):
    from app.organisations.models import Organisation

    package = _package()
    package["organisations"] = [
        _organisation("Näidisministeerium", "MINISTRY", notes="Ei tohi üle kirjutada.")
    ]
    _apply(_write(package_root, package))
    ministry = Organisation.objects.get(name="Näidisministeerium")
    assert ministry.notes != "Ei tohi üle kirjutada."
    assert Organisation.objects.filter(name="Näidisministeerium").count() == 1
    row = EvidencePilotRecord.objects.get(record_key="ORG:Näidisministeerium")
    assert row.evidence["created"] is False


def test_the_provenance_row_names_a_matter_unless_it_is_an_organisation(world):
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        EvidencePilotRecord.objects.create(
            pilot=evidence_pilot.PILOT,
            pilot_version=evidence_pilot.PILOT_VERSION,
            package_sha256="0" * 64,
            record_key="X",
            record_kind="MATTER",
            matter=None,
        )


def test_each_signatory_of_a_joint_letter_has_its_position_on_one_filed_letter(
    world, package_root, pilot_on
):
    from app.documents.links import DocumentLink

    _apply(_write(package_root, _joint_letter_package()))
    one = _matter(f"{YEAR}_5")
    positions = {
        p.organisation.name: p
        for p in MatterExternalPosition.objects.filter(matter=one, provenance="DISCOVERED")
    }
    assert set(positions) == {"Näidisliit", "Teine Näidisliit"}
    letter = Document.objects.get(
        matter=one, current_version__original_filename="Liikme vastus.pdf"
    )
    for position in positions.values():
        assert DocumentLink.objects.filter(document=letter, external_position=position).exists()
    assert Document.objects.filter(matter=one).count() == 4


def test_a_rejected_record_is_reported_apart_and_its_files_say_why(world, package_root, pilot_on):
    package = _package()
    _record(package, "E08").update(
        {
            "review": "REJECTED",
            "decision_reason": "Liikme seisukoht, mitte Koja arvamus.",
            "documents": [{"resource_key": "r005", "role": "OTHER"}],
        }
    )
    _record(package, "E02")["documents"] = []
    report = _apply(_write(package_root, package))
    assert report.rejected == [f"{YEAR}_5-E08"]
    assert report.held == [f"{YEAR}_6-NEXT"]
    skipped = LegacySourceResourceImport.objects.get(
        matter_source_page__matter=_matter(f"{YEAR}_5"), resource__resource_key="r005"
    )
    assert skipped.state == ResourceImportState.SKIPPED
    assert skipped.error_code == "RECORD_REJECTED_FOR_IMPORT"
    assert "mitte Koja arvamus" in skipped.error_detail


def test_a_planned_milestone_is_an_important_date_over_its_whole_quarter(
    world, package_root, pilot_on
):
    from app.intelligence.models import MatterImportantDate

    package = _package()
    _one(package)["records"].append(_milestone(f"{YEAR}-Q4"))
    _apply(_write(package_root, package))
    milestone = MatterImportantDate.objects.get(matter=_matter(f"{YEAR}_5"))
    assert (milestone.date_value, milestone.period_end) == (
        dt.date(YEAR, 10, 1),
        dt.date(YEAR, 12, 31),
    )
    assert milestone.date_precision == "QUARTER"
    assert milestone.kind == "OTHER" and milestone.source_text == "novembris"
    # A planned milestone is not a commencement.
    assert MatterEffectiveDate.objects.filter(matter=_matter(f"{YEAR}_5")).count() == 1


def test_a_sent_reply_keeps_its_kind_and_reference(world, package_root, pilot_on):
    package = _package()
    _record(package, "E04").update(
        {"submission_kind": "INFORMAL_WRITTEN_RESPONSE", "reference": "9/99"}
    )
    _apply(_write(package_root, package))
    submission = Submission.objects.get(matter=_matter(f"{YEAR}_5"))
    assert submission.kind == "INFORMAL_WRITTEN_RESPONSE"
    assert submission.reference == "9/99"


def test_a_reply_named_by_answered_by_answers_the_current_request(world, package_root, pilot_on):
    from app.matters.models import MatterResponseDeadline
    from app.matters.work_items import response_obligation_of

    package = _package()
    _one(package)["answered_by"] = f"{YEAR}_5-E04"
    package_root = _write(package_root, package)
    plan = build_plan(package_root)
    assert plan.expected_writes()["DEADLINE_ANSWERED"] == 1
    _apply(package_root)

    one = _matter(f"{YEAR}_5")
    assert one.response_deadline is None
    answered = MatterResponseDeadline.objects.get(matter=one, deadline=dt.date(YEAR, 4, 15))
    assert answered.outcome == "ANSWERED"
    assert answered.submission == Submission.objects.get(matter=one)
    assert not response_obligation_of(one, world["mari"]).is_outstanding
    assert verify_applied(build_plan(package_root)).problems == []
    # The other topic's request is still open.
    assert _matter(f"{YEAR}_6").response_deadline == dt.date(YEAR, 3, 20)


# ---------------------------------------------------------------------------
# the independent review of 2026-10-19: what a later package must not slip in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "message"),
    [
        # B1: an undated note would read as written on the import day.
        (
            lambda p: _record(p, "E06").update({"date": None, "date_precision": None}),
            "needs an exact day",
        ),
        # B2: `published_on` has no precision, so a month would become its 1st.
        (
            lambda p: _record(p, "E05").update({"date": f"{YEAR}-02", "date_precision": "MONTH"}),
            "publication date is an exact day",
        ),
        # B3: two entries on one registry code would be filed as one body.
        (
            lambda p: p.update(
                {
                    "organisations": [
                        _organisation("Uus Näidisamet", "AUTHORITY", registry_code="70000001"),
                        _organisation("Vana Näidisamet", "AUTHORITY", registry_code="70000001"),
                    ]
                }
            ),
            "registry code 70000001 is also",
        ),
        # S1: the digest covers package.json; the package must pin every file.
        (lambda p: _record(p, "E03")["documents"][0].pop("sha256"), "without its SHA-256"),
        (lambda p: _one(p)["resources_not_imported"][0].pop("sha256"), "without its SHA-256"),
        # S2: a past round given a reply-by day would open a wait nobody is in.
        (
            lambda p: _record(p, "E02").update({"feedback_deadline": f"{YEAR}-02-20"}),
            "past round carries no reply-by day",
        ),
        # Refused at validate rather than half-way through the apply.
        (
            lambda p: p.update({"organisations": [_organisation("N" * 120)]}),
            "too long for its provenance key",
        ),
        (
            lambda p: p.update(
                {"organisations": [_organisation("Näidisliit", source_reference="x" * 201)]}
            ),
            "source_reference is longer than 200",
        ),
        (
            lambda p: p.update(
                {
                    "organisations": [
                        _organisation(
                            "Näidisliit", valid_from=f"{YEAR}-05-01", valid_to=f"{YEAR}-01-01"
                        )
                    ]
                }
            ),
            "valid_from is after valid_to",
        ),
    ],
)
def test_the_review_findings_are_refused_at_validate(package_root, change, message):
    problems = _validate(package_root, change)
    assert any(message in problem for problem in problems), problems


def test_a_name_is_never_merged_into_another_by_its_registry_code(world, package_root, pilot_on):
    from app.organisations.models import Organisation

    factories.OrganisationFactory(name="Uus Näidisamet", registry_code="70000001")
    package = _package()
    package["organisations"] = [
        _organisation("Vana Näidisamet", "AUTHORITY", registry_code="70000001")
    ]
    _one(package)["records"].append(
        _discovered(
            "E09",
            "Vana Näidisamet",
            documents=[{"resource_key": "r003", "role": "EXTERNAL_POSITION"}],
        )
    )
    _record(package, "E03")["documents"] = []
    package_root = _write(package_root, package)

    plan = build_plan(package_root)
    assert any(
        "registry code 70000001 already belongs to 'Uus Näidisamet'" in p for p in plan.problems
    )
    with pytest.raises(EvidencePilotError, match="The plan has problems"):
        _apply(package_root)
    assert not Organisation.objects.filter(name="Vana Näidisamet").exists()


def test_a_teema_created_after_the_plan_stops_the_apply(world, package_root, pilot_on):
    plan = build_plan(package_root)
    assert plan.database_state == "fresh"
    factories.MatterFactory()

    with pytest.raises(EvidencePilotError, match="changed after the plan"):
        apply_plan(
            plan,
            operator_intent=evidence_pilot.OPERATOR_INTENT,
            expect_package_sha256=plan.digest,
            backup_set="sets/20250331T000000Z",
        )
    assert Matter.all_objects.count() == 1
    assert EvidencePilotRecord.objects.count() == 0


def test_stored_bytes_must_match_the_reviewed_package_not_only_the_archive(
    world, package_root, pilot_on
):
    # The archive and its resource.json re-exported together with new bytes: the
    # validate catches it because the package pins the old SHA-256.
    original = package_root / evidence_pilot.ARCHIVE_DIRECTORY / "pages/p-one/resources/r003"
    content = b"%PDF-1.4\n% vahetatud\n"
    (original / "original" / "Liikme vastus.pdf").write_bytes(content)
    meta = json.loads((original / "resource.json").read_text("utf-8"))
    meta.update({"sha256": hashlib.sha256(content).hexdigest(), "sizeBytes": len(content)})
    (original / "resource.json").write_text(json.dumps(meta, ensure_ascii=False), "utf-8")

    problems = _validate(package_root)
    assert any("r003 SHA-256 in the package differs" in p for p in problems), problems
