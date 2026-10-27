"""The Lovable pilot (docs/adr/0191), on a synthetic package.

Every title, name, reference text and link below is invented; the shapes are
the real package's. The tests walk validate → plan → dry-run → apply → verify
and pin the rules the brief set: an append that never touches a Teema already
there, a reference that exists stops the whole apply, a step's kind comes from
the reviewed package and never from its wording, a WAIT or MONITOR is never a
deadline, a historical opinion deadline is source only and never overdue, no
sent opinion, document, follow-up, closure date or closure event is invented,
«üldises korras» carries no date, the OneNote link is kept byte for byte and
shown only as an https link, and a second run writes nothing.
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType, SecurityEventType
from app.audit.models import ChangeEvent
from app.audit.services import record_security_event
from app.documents.models import Document
from app.intelligence.models import MatterEffectiveDate, MatterImportantDate
from app.legacy_import import lovable_pilot
from app.legacy_import.lovable_pilot import (
    LovablePilotError,
    apply_plan,
    build_plan,
    dry_run,
    package_digest,
    plan_digest,
    validate_package,
    verify_applied,
)
from app.legacy_import.models import EvidencePilotRecord, MatterSourceReference
from app.matters.models import Matter, MatterReferenceSequence
from app.submissions.models import Submission
from app.workflow.models import NextAction, OpinionFollowUp
from tests import factories

URL = "https://example.invalid/sites/naidis/_layouts/Doc.aspx?sourcedoc={AAAA}&wd=target(Näidis.one|X/Leht|Y)"


def _row(reference: str, **values: Any) -> dict[str, Any]:
    return {
        "nr": reference,
        "teema": f"Näidisteema {reference}",
        "aasta": int(reference[:4]),
        **values,
    }


def _topic(reference: str, **overrides: Any) -> dict[str, Any]:
    topic = {
        "reference": reference,
        "group": "A",
        "title": f"Näidisteema {reference}",
        "owner_raw": "Mari",
        "status_raw": "Riigikogus",
        "stage": "parliament",
        "lifecycle": "ACTIVE",
        "disposition": "",
        "successor": "",
        "koda_initiative": False,
        "instruments": ["seadus"],
        "received_date": "2026-01-05",
        "addressee_raw": "",
        "next_action": {
            "review": "READY",
            "kind": "WAIT",
            "date_semantics": "REVIEW_ON",
            "target_date": "2026-11-15",
            "date_precision": "EXACT",
            "text": "ootan 2. lugemist, vaata üle 15.11",
            "source_text": "ootan 2. lugemist, vaata üle 15.11",
            "treatment": "SOURCE_NEXT_STEP",
            "reason": "WAIT with a review date",
        },
        "deadline": {"treatment": "SOURCE_ONLY", "deadline": "2026-01-20", "reason": "SENT"},
        "commencements": [],
        "onenote_url": URL,
        "field_decisions": {"jargmiseks": "APPLIED", "valja": "PRESERVED_AS_SOURCE_ONLY"},
        "review_notes": [],
        "source": {
            "id": f"00000000-0000-0000-0000-{int(reference[5:]):012d}",
            "version": 3,
            "updated_at": "2026-10-20T10:00:00+00:00",
            "row": _row(reference, valja="2026-01-20", jargmiseks="ootan 2. lugemist"),
            "excel_source_row": None,
            "history": [
                {"occurred_at": "2026-09-01T08:00:00+00:00", "action": "muutmine", "fields": []}
            ],
            "commencements": [],
            "responsible_changes": [],
        },
    }
    topic.update(overrides)
    return topic


def _drafting() -> dict[str, Any]:
    """Open work with an outstanding request, drafted, and three commencement kinds."""
    return _topic(
        "2026_502",
        owner_raw="Jaan",
        stage="consultation",
        next_action={
            "review": "READY",
            "kind": "DO",
            "date_semantics": "DEADLINE",
            "target_date": "2026-11-12",
            "date_precision": "EXACT",
            "text": "Koostan arvamuse",
            "source_text": "Koostan arvamust.",
            "treatment": "OPINION_DRAFTING",
            "reason": "drafting",
        },
        deadline={"treatment": "OPEN_REQUEST", "deadline": "2026-11-12"},
        commencements=[
            {
                "key": "2026_502-C01",
                "review": "READY",
                "target": "MatterEffectiveDate",
                "kind": "GENERAL_ORDER",
                "date": None,
                "date_precision": None,
                "note": "",
            },
            {
                "key": "2026_502-C02",
                "review": "READY",
                "target": "MatterImportantDate",
                "kind": "ESTIMATED_COMMENCEMENT",
                "date": "2027-01-01",
                "date_precision": "EXACT",
                "note": "§ 1 p 3",
            },
            {
                "key": "2026_502-C03",
                "review": "READY",
                "target": "MatterImportantDate",
                "kind": "PLANNED_COMMENCEMENT",
                "date": "2027-07-01",
                "date_precision": "EXACT",
                "note": "",
            },
        ],
    )


def _package(*topics: dict[str, Any]) -> dict[str, Any]:
    topics = topics or (
        _topic("2026_501"),
        _drafting(),
        _topic(
            "2026_503",
            status_raw="jõustunud",
            stage="in_force",
            lifecycle="INACTIVE",
            disposition="COMPLETED",
            next_action=None,
            commencements=[
                {"key": "2026_503-C01", "review": "SOURCE_ONLY", "reason": "closed Matter"}
            ],
        ),
        _topic(
            "2026_504",
            owner_raw="Endine",
            status_raw="rohkem ei tegele",
            stage="monitoring_stopped",
            lifecycle="CONTINUES",
            disposition="SUPERSEDED",
            successor="2026_501",
            next_action=None,
            onenote_url="",
        ),
    )
    return {
        "format": lovable_pilot.PACKAGE_FORMAT,
        "format_version": lovable_pilot.PACKAGE_FORMAT_VERSION,
        "reviewer": "näidisülevaataja",
        "snapshot": {
            "as_of": "2026-10-26T17:40:16+00:00",
            "snapshot_sha256": "a" * 64,
            "project": "näidis",
            "highest_reference": {"2026": 520},
        },
        "people": {"Mari": "Mari Näidis", "Jaan": "Jaan Näidis"},
        "reserve_through": {"2026": 520},
        "expected_topics": len(topics),
        "topics": list(topics),
    }


@pytest.fixture
def world(db):
    mari = factories.UserFactory(upn="mari@example.invalid", display_name="Mari Näidis")
    jaan = factories.UserFactory(upn="jaan@example.invalid", display_name="Jaan Näidis")
    return {"mari": mari, "jaan": jaan}


@pytest.fixture
def pilot_on(settings):
    settings.LOVABLE_PILOT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    return settings


def _apply(package: dict[str, Any], **overrides: Any):
    plan = build_plan(package)
    arguments = {
        "operator_intent": lovable_pilot.OPERATOR_INTENT,
        "expect_package_sha256": plan.digest,
        "expect_plan_sha256": plan_digest(plan),
        "backup_set": "sets/20261026T160000Z",
        **overrides,
    }
    return apply_plan(plan, **arguments)


def _problems(change) -> list[str]:
    package = _package()
    change(package)
    package["expected_topics"] = len(package["topics"])
    return validate_package(package).problems


# ---------------------------------------------------------------------------
# validate: no database, the reviewed shape only
# ---------------------------------------------------------------------------


def test_the_synthetic_package_validates():
    report = validate_package(_package())
    assert report.problems == []
    assert (report.topics, report.open_topics, report.steps) == (4, 2, 2)
    assert (report.commencements_ready, report.commencements_source_only) == (3, 1)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda p: p["topics"][0]["next_action"].update(kind="WAIT", date_semantics="DEADLINE"),
            "only a DO step has a deadline",
        ),
        (lambda p: p["topics"][0]["next_action"].update(reason=""), "needs the reviewer's reason"),
        (
            lambda p: p["topics"][2].update(next_action=_topic("2026_503")["next_action"]),
            "a finished topic carries no current step",
        ),
        (
            lambda p: p["topics"][1].update(deadline={"treatment": "NONE"}),
            "a drafting step needs the open request",
        ),
        (
            lambda p: p["topics"][0].update(
                deadline={"treatment": "OPEN_REQUEST", "deadline": "2026-11-15"}
            ),
            "an open request is drafted",
        ),
        (
            lambda p: p["topics"][1]["commencements"][0].update(date="2027-01-01"),
            "carries no date; none is invented",
        ),
        (lambda p: p["topics"][1]["commencements"][1].update(date=None), "needs its exact day"),
        (
            lambda p: p["topics"][2]["commencements"][0].update(reason=""),
            "a source-only commencement says why",
        ),
        (
            lambda p: p["topics"][0].update(onenote_url="javascript:alert(1)"),
            "not an https address",
        ),
        (lambda p: p["topics"][0].update(owner_raw="Tundmatu"), "an open file needs one"),
        (lambda p: p["topics"][2].update(disposition=""), "a finished topic needs a disposition"),
        (lambda p: p["topics"].append(copy.deepcopy(p["topics"][0])), "listed twice"),
        (
            lambda p: p["topics"][0]["source"]["row"].update(nr="2026_999"),
            "does not match the reference",
        ),
        (lambda p: p["people"].update(Mari=""), "an open file needs one"),
        (
            lambda p: p["topics"][0]["next_action"].pop("date_semantics"),
            "date_semantics is required",
        ),
        (lambda p: p["topics"][0]["next_action"].pop("kind"), "kind is required"),
        (
            lambda p: p["topics"][0]["next_action"].update(date_semantics="EXPECTED_AROUND"),
            "a WAIT or MONITOR step is a review date",
        ),
        (
            lambda p: p["topics"][1]["commencements"][1].update(key="2026_502-C01"),
            "is used twice",
        ),
        (lambda p: p["topics"][1]["commencements"][1].update(key="x" * 121), "empty or too long"),
        (lambda p: p["topics"][1]["commencements"][2].pop("key"), "needs its key"),
        (lambda p: p["topics"][1].update(koda_initiative=True), "takes no new request"),
        (
            lambda p: (
                p["topics"][1]["next_action"].update(target_date="2026-10-01"),
                p["topics"][1]["deadline"].update(deadline="2026-10-01"),
            ),
            "overdue on arrival",
        ),
        (lambda p: p["topics"][0].update(successor="2026_502"), "only a finished, SUPERSEDED"),
        (lambda p: p["topics"][3].update(successor="2026_504"), "cannot continue under itself"),
        (lambda p: p["topics"][1]["next_action"].update(text="Kirjutan vastuse"), "is drafted"),
        (lambda p: p.update(reserve_through={"2026": 52000}), "highest_reference"),
        (lambda p: p.update(reserve_through={"20x6": 5}), "not a year and a number"),
        (
            lambda p: (
                p["topics"][0].update(reference="2026_0501"),
                p["topics"][0]["source"]["row"].update(nr="2026_0501"),
            ),
            "is not YYYY_N",
        ),
    ],
)
def test_validate_refuses_what_cannot_be_written_honestly(change, message):
    assert any(message in problem for problem in _problems(change)), _problems(change)


def test_the_digest_ignores_layout_but_not_content():
    package = _package()
    again = copy.deepcopy(package)
    assert package_digest(package) == package_digest(again)
    again["topics"][0]["next_action"]["target_date"] = "2026-11-16"
    assert package_digest(package) != package_digest(again)


# ---------------------------------------------------------------------------
# plan: references are free or nothing is written
# ---------------------------------------------------------------------------


def test_the_plan_resolves_people_and_says_ready(world):
    plan = build_plan(_package())
    assert plan.problems == []
    assert plan.database_state == "ready"
    owners = {t.reference: t.owner for t in plan.topics}
    assert owners["2026_501"] == world["mari"] and owners["2026_502"] == world["jaan"]
    assert owners["2026_504"] is None  # a departed owner stays source text, never a person
    assert plan.topics[3].successor_in_batch == "2026_501"


def test_an_existing_reference_stops_the_plan_and_the_apply(world, pilot_on):
    factories.MatterFactory(reference_year=2026, reference_number=502)
    plan = build_plan(_package())
    assert plan.database_state == "conflict"
    assert plan.collisions == ["2026_502"]
    with pytest.raises(LovablePilotError, match="never overwritten"):
        _apply(_package())
    assert Matter.all_objects.count() == 1


def test_two_topics_cannot_continue_under_each_other():
    def loop(p):
        p["topics"][2].update(lifecycle="CONTINUES", disposition="SUPERSEDED", successor="2026_504")
        p["topics"][3].update(successor="2026_503")

    assert any("which continues under it" in problem for problem in _problems(loop))


def test_a_permanently_deleted_reference_is_never_reused(world, pilot_on):
    record_security_event(
        event_type=SecurityEventType.MATTER_DELETED, detail={"reference": "2026_502"}
    )
    plan = build_plan(_package())
    assert plan.database_state == "conflict"
    assert plan.collisions == ["2026_502"]
    with pytest.raises(LovablePilotError, match="never overwritten"):
        _apply(_package())
    assert Matter.all_objects.count() == 0


def test_an_owner_who_may_not_be_given_work_stops_the_plan(world):
    world["mari"].is_staff = True
    world["mari"].save(update_fields=["is_staff"])
    plan = build_plan(_package())
    assert any("may be assigned to" in problem for problem in plan.problems), plan.problems


def test_an_unrelated_existing_matter_does_not_block_and_is_not_touched(world, pilot_on):
    other = factories.MatterFactory(reference_year=2026, reference_number=7)
    before = (other.updated_at, other.title, other.owner_id)
    report = _apply(_package())
    other.refresh_from_db()
    assert (other.updated_at, other.title, other.owner_id) == before
    assert report.matters_before == 1 and report.matters_after == 5
    assert report.fingerprint_before == report.fingerprint_after


# ---------------------------------------------------------------------------
# apply: gated, one transaction, idempotent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "overrides", "message"),
    [
        ("LOVABLE_PILOT_ENABLED", {}, "JURISTID_LOVABLE_PILOT"),
        ("REAL_DATA_ALLOWED", {}, "REAL_DATA_ALLOWED is off"),
        (None, {"operator_intent": "jah"}, "--operator-intent lovable-pilot"),
        (None, {"expect_package_sha256": "0" * 64}, "pass exactly that"),
        (None, {"expect_plan_sha256": " "}, "pass it as --expect-plan-sha256"),
        (None, {"expect_plan_sha256": "0" * 64}, "changed since it was approved"),
        (None, {"backup_set": "eile"}, "Name the verified backup set"),
    ],
)
def test_the_apply_refuses_without_each_authorisation(world, pilot_on, setting, overrides, message):
    if setting:
        setattr(pilot_on, setting, False)
    with pytest.raises(LovablePilotError, match=message):
        _apply(_package(), **overrides)
    assert Matter.all_objects.count() == 0
    assert EvidencePilotRecord.objects.count() == 0


def test_a_reference_taken_after_the_plan_is_refused_under_the_lock(world, pilot_on):
    package = _package()
    plan = build_plan(package)
    factories.MatterFactory(reference_year=2026, reference_number=501)
    with pytest.raises(LovablePilotError):
        apply_plan(
            plan,
            operator_intent=lovable_pilot.OPERATOR_INTENT,
            expect_package_sha256=plan.digest,
            expect_plan_sha256=plan_digest(plan),
            backup_set="sets/20261026T160000Z",
        )
    assert Matter.all_objects.count() == 1


def test_the_apply_writes_what_the_package_says_and_nothing_else(world, pilot_on):
    report = _apply(_package())
    assert report.written == {
        "MATTER": 4,
        "NEXT_ACTION": 2,
        "RESPONSE_REQUEST": 1,
        "MatterEffectiveDate": 1,
        "MatterImportantDate": 2,
        "CONTINUATION": 1,
    }
    by_ref = {m.display_reference: m for m in Matter.all_objects.all()}

    waiting = NextAction.objects.get(matter=by_ref["2026_501"], status="OPEN")
    assert (waiting.kind, waiting.date_semantics, waiting.target_date) == (
        "WAIT",
        "REVIEW_ON",
        dt.date(2026, 11, 15),
    )
    assert waiting.responsible == world["mari"]
    # The historical opinion deadline is source only: no current request, nothing overdue.
    assert by_ref["2026_501"].response_deadline is None

    drafting = by_ref["2026_502"]
    step = NextAction.objects.get(matter=drafting, status="OPEN")
    assert (step.kind, step.date_semantics, step.text) == ("DO", "DEADLINE", "Koostan arvamuse")
    assert drafting.response_deadline == dt.date(2026, 11, 12)
    assert drafting.response_requested_at is not None
    general = MatterEffectiveDate.objects.get(matter=drafting)
    assert (general.kind, general.date_value) == ("GENERAL_ORDER", None)
    assert sorted(
        MatterImportantDate.objects.filter(matter=drafting).values_list("kind", flat=True)
    ) == [
        "ESTIMATED_COMMENCEMENT",
        "PLANNED_COMMENCEMENT",
    ]

    closed = by_ref["2026_503"]
    assert (closed.is_open, closed.record_mode, closed.disposition, closed.closed_at) == (
        False,
        "ARCHIVE",
        "COMPLETED",
        None,
    )
    assert not NextAction.objects.filter(matter=closed).exists()
    assert not MatterEffectiveDate.objects.filter(matter=closed).exists()
    assert not closed.shows_register_archive_notice

    continued = by_ref["2026_504"]
    assert continued.superseded_by == by_ref["2026_501"]
    assert continued.owner is None

    assert not ChangeEvent.objects.filter(event_type=ChangeEventType.MATTER_CLOSED).exists()
    assert not ChangeEvent.objects.filter(matter__in=by_ref.values(), actor__isnull=False).exists()
    assert not Submission.objects.exists() and not Document.objects.exists()
    assert not OpinionFollowUp.objects.exists()
    assert MatterReferenceSequence.objects.get(year=2026).last_number == 520


def test_the_source_reference_keeps_the_source_exactly(world, pilot_on):
    _apply(_package())
    reference = MatterSourceReference.objects.get(matter__reference_number=501)
    assert reference.source_system == "LOVABLE"
    assert reference.onenote_url == URL
    assert reference.source_snapshot_sha256 == "a" * 64
    assert (reference.source_sheet, reference.source_row_number) == ("public.topic:2026", 501)
    assert reference.source_row_raw["lovable"]["id"].endswith("501")
    assert reference.source_row_raw["history"][0]["action"] == "muutmine"
    assert reference.source_row_raw["row"]["valja"] == "2026-01-20"


def test_a_second_run_writes_nothing(world, pilot_on):
    _apply(_package())
    counts = (
        Matter.all_objects.count(),
        EvidencePilotRecord.objects.count(),
        NextAction.objects.count(),
    )
    again = _apply(_package())
    assert again.already_applied
    assert (
        Matter.all_objects.count(),
        EvidencePilotRecord.objects.count(),
        NextAction.objects.count(),
    ) == counts
    assert build_plan(_package()).database_state == "applied"


def test_the_dry_run_verifies_and_leaves_nothing(world, pilot_on):
    report = dry_run(build_plan(_package()))
    assert report.dry_run and report.verify.ok and report.verify.checked == 4
    assert report.written["MATTER"] == 4
    assert Matter.all_objects.count() == 0
    assert EvidencePilotRecord.objects.count() == 0


def test_verify_finds_a_difference(world, pilot_on):
    _apply(_package())
    assert verify_applied(build_plan(_package())).ok
    NextAction.objects.filter(matter__reference_number=501, status="OPEN").update(
        target_date=dt.date(2026, 12, 1)
    )
    report = verify_applied(build_plan(_package()))
    assert any("2026_501: current step" in d for d in report.discrepancies)


# ---------------------------------------------------------------------------
# what a reader sees
# ---------------------------------------------------------------------------


def test_the_archive_notice_names_the_same_pilot_constant():
    # `Matter.shows_register_archive_notice` cannot import the module (a cycle),
    # so it spells the pilot's name; this keeps the two from drifting apart.
    import inspect

    assert f'"{lovable_pilot.PILOT}"' in inspect.getsource(
        Matter.shows_register_archive_notice.fget
    )


def test_the_edit_page_names_lovable_without_a_row_number(world, pilot_on):
    from app.matters.views import _immutable_facts

    _apply(_package())
    facts = dict(_immutable_facts(Matter.objects.get(reference_number=501)))
    assert facts["Algallikas"] == "Lovable"


def test_a_partial_import_is_reported_not_repaired(world, pilot_on):
    _apply(_package())
    EvidencePilotRecord.objects.filter(record_kind="NEXT_ACTION").first().delete()
    plan = build_plan(_package())
    assert plan.database_state == "conflict"
    assert plan.collisions == []
    assert any("partial or removed import" in p for p in plan.problems)


def test_the_documents_tab_links_onenote_and_says_nothing_was_imported(world, pilot_on, client):
    _apply(_package())
    client.force_login(world["mari"])
    matter = Matter.objects.get(reference_number=501)
    page = client.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()
    assert ">OneNote</a>" in page
    assert "Lehe sisu ja manuseid ei ole Juristidesse imporditud" in page
    assert 'rel="noopener noreferrer"' in page

    without = Matter.all_objects.get(reference_number=504)
    other = client.get(
        reverse("matters:matter_documents", kwargs={"pk": without.pk})
    ).content.decode()
    assert ">OneNote</a>" not in other
