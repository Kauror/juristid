"""The historical backfill closes topics only for reasons the source states (LS2-A).

Round 1's reader closed every historical topic with «Koda lõpetas jälgimise»
(MONITORING_STOPPED) and read «ei saatnud» as «Seisukohta ei kujundatud»
(NO_POSITION_FORMED). Neither is in the source: being outside Lovable's
operational scope says nothing about why the work ended, and not sending an
opinion is not proof that no position was formed. Reader version 2 records a
closure reason only from Lovable's own status or its structured stage link, and
otherwise closes the historical Teema ARCHIVE with a blank disposition, which
`matters_closure_fields_consistent` allows for an archive row.

These tests use the real machine reader end to end — reader, package, plan,
dry-run, apply, verify — on synthetic topics served by a fake transport. Every
title, name, reference and identifier is invented.
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
from app.legacy_import import lovable_backfill, lovable_pilot, lovable_reading
from app.legacy_import.lovable_backfill import build_package
from app.legacy_import.lovable_inventory import Category, build_ledger, coverage_markdown
from app.legacy_import.lovable_pilot import (
    HISTORY_COHORT,
    OPERATIONAL_COHORT,
    REGISTER_COHORT,
    plan_digest,
    validate_package,
)
from app.legacy_import.lovable_reading import (
    CLOSURE_BY_STATUS,
    READER_VERSION,
    ReadingRefused,
    closure_reading,
    package_for,
    read_topic,
)
from app.legacy_import.models import EvidencePilotRecord, MatterSourceReference
from app.legacy_import.source_pages import MatterSourcePage, SourceMatchMethod
from app.matters.models import Matter
from app.matters.work_status import INACTIVE
from app.workflow.enums import DIRECT_CLOSURE_DISPOSITIONS, Disposition
from app.workflow.models import NextAction
from tests import factories
from tests.test_ls_a_inventory import (
    FakeLovable,
    archive_page,
    enrol_register,
    onenote_link,
    reading,
    register_teema,
    row_of,
    sid,
    tombstone,
    topic,
)
from tests.test_ls_b_reading import bundle

AS_OF = dt.date(2026, 11, 9)
BACKUP = "sets/20261109T060000Z"


def _historical(**values: Any) -> dict[str, Any]:
    return read_topic(
        bundle("2019_701", operatiivne_ulatus=False, aasta=2019, **values),
        as_of=AS_OF,
        people={},
        historical=True,
    )


def _package(topic_: dict[str, Any], cohort: str) -> dict[str, Any]:
    return package_for([topic_], as_of=AS_OF, snapshot_sha256="b" * 64, people={}, cohort=cohort)


# ---------------------------------------------------------------------------
# The reader: a reason only where the source states one
# ---------------------------------------------------------------------------


def test_the_reader_version_names_the_new_reading():
    assert READER_VERSION == "lovable-reading/2"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("jõustunud", ("INACTIVE", "COMPLETED", "SOURCE_STATUS")),
        ("Jõustunud", ("INACTIVE", "COMPLETED", "SOURCE_STATUS")),
        ("rohkem ei tegele", ("CONCLUDED", "MONITORING_STOPPED", "SOURCE_STATUS")),
        ("Rohkem  ei tegele ", ("CONCLUDED", "MONITORING_STOPPED", "SOURCE_STATUS")),
        ("rohkem pole tegevusi plaanis", ("CONCLUDED", "MONITORING_STOPPED", "SOURCE_STATUS")),
        # Not a stated reason: blank, never a guess.
        ("lõpetatud", ("INACTIVE", "", "UNKNOWN")),
        ("joustunud", ("INACTIVE", "", "UNKNOWN")),  # no diacritic folding
        ("Riigikogus", ("INACTIVE", "", "UNKNOWN")),
        ("ootan jõustumist", ("INACTIVE", "", "UNKNOWN")),
        ("muu", ("INACTIVE", "", "UNKNOWN")),
        ("", ("INACTIVE", "", "UNKNOWN")),
    ],
)
def test_a_closure_reason_is_read_only_from_the_sources_own_status(status, expected):
    assert closure_reading(bundle(hetkeseis=status)) == expected


def test_the_closure_table_holds_only_reasons_the_product_already_reads_that_way():
    from app.workflow.stage_flow import TERMINAL_DISPOSITIONS
    from app.workflow.vocabulary import RAW_LABEL_TO_DISPOSITION

    for label, (_lifecycle, disposition) in CLOSURE_BY_STATUS.items():
        stage = lovable_reading.STAGE_MAPPING.get(label)
        if stage is not None:
            assert TERMINAL_DISPOSITIONS[stage] == disposition, label
        else:
            assert RAW_LABEL_TO_DISPOSITION[label] == disposition, label
    assert "NO_POSITION_FORMED" not in {d for _l, d in CLOSURE_BY_STATUS.values()}


def test_a_historical_topic_outside_the_operational_scope_gets_no_invented_reason():
    topic_ = _historical(hetkeseis="Riigikogus")
    assert (topic_["lifecycle"], topic_["disposition"]) == ("INACTIVE", "")
    assert topic_["closure_basis"] == "UNKNOWN"
    assert any(n.startswith("CLOSURE_REASON_UNKNOWN") for n in topic_["review_notes"])
    assert topic_["stage"] == "parliament"  # the available source stage is kept
    assert validate_package(_package(topic_, HISTORY_COHORT)).problems == []
    assert lovable_backfill.history_problems(topic_) == []


@pytest.mark.parametrize("cohort", [OPERATIONAL_COHORT, REGISTER_COHORT])
def test_a_blank_disposition_is_only_the_historical_cohorts(cohort):
    topic_ = _historical(hetkeseis="Riigikogus")
    package = _package(topic_, cohort)
    if cohort == REGISTER_COHORT:
        package["replace"] = True
    problems = validate_package(package).problems
    assert any("a finished topic needs a disposition, not ''" in p for p in problems)


@pytest.mark.parametrize("status", ["Riigikogus", "idee", "lõpetatud", ""])
def test_ei_saatnud_is_never_a_closure_reason_on_a_historical_topic(status):
    topic_ = _historical(hetkeseis=status, valja="ei saatnud", arvamuse_tahtaeg="2019-03-01")
    assert topic_["disposition"] == "" and topic_["disposition"] != "NO_POSITION_FORMED"
    # The source's statement is kept, as the source's.
    assert topic_["deadline"] == {
        "treatment": "SOURCE_ONLY",
        "deadline": "2019-03-01",
        "reason": "NOT_SENT",
    }
    assert topic_["field_decisions"]["valja"] == "PRESERVED_AS_SOURCE_ONLY"
    assert topic_["source"]["row"]["valja"] == "ei saatnud"
    assert any(n.startswith("NOT_SENT_RECORDED") for n in topic_["review_notes"])
    assert validate_package(_package(topic_, HISTORY_COHORT)).problems == []


def test_ei_saatnud_beside_koda_s_own_stop_reads_the_stop():
    topic_ = _historical(hetkeseis="rohkem ei tegele", valja="ei saatnud")
    assert (topic_["lifecycle"], topic_["disposition"]) == ("CONCLUDED", "MONITORING_STOPPED")
    assert topic_["closure_basis"] == "SOURCE_STATUS"


def test_a_package_claiming_no_position_formed_for_ei_saatnud_is_refused():
    topic_ = _historical(hetkeseis="Riigikogus", valja="ei saatnud")
    topic_.update(lifecycle="CONCLUDED", disposition="NO_POSITION_FORMED")
    problems = validate_package(_package(topic_, HISTORY_COHORT)).problems
    assert any("«ei saatnud» is not a closure reason" in p for p in problems)
    assert any(
        "is not what the source states" in p for p in lovable_backfill.history_problems(topic_)
    )


@pytest.mark.parametrize(
    ("status", "claimed"),
    [
        ("Riigikogus", ("INACTIVE", "MONITORING_STOPPED")),  # round 1's invention
        ("Riigikogus", ("INACTIVE", "COMPLETED")),
        ("jõustunud", ("INACTIVE", "")),  # a stated reason is never dropped either
        ("rohkem ei tegele", ("INACTIVE", "OTHER")),
    ],
)
def test_the_backfill_rereads_the_closure_from_the_topics_own_source(status, claimed):
    topic_ = _historical(hetkeseis=status)
    topic_["lifecycle"], topic_["disposition"] = claimed
    assert any(
        "is not what the source states" in p for p in lovable_backfill.history_problems(topic_)
    )


def test_a_historical_continuation_is_superseded_and_names_its_successor_only_when_known():
    me, nxt = sid(1), sid(2)
    raw = bundle("2019_701", operatiivne_ulatus=False, aasta=2019, id=me, hetkeseis="idee")
    raw["stage_links"] = [{"prev": me, "next": nxt}]
    unnamed = read_topic(raw, as_of=AS_OF, people={}, historical=True)
    assert (unnamed["lifecycle"], unnamed["disposition"]) == ("CONTINUES", "SUPERSEDED")
    assert unnamed["successor"] == "" and unnamed["closure_basis"] == "CONTINUATION"
    named = read_topic(
        {**raw, "references": {nxt: "2019_702"}}, as_of=AS_OF, people={}, historical=True
    )
    assert named["successor"] == "2019_702" and named["source"]["successor_id"] == nxt
    assert lovable_backfill.history_problems(named) == []


def test_a_finished_operational_topic_with_no_stated_reason_is_held_for_a_person():
    # `activity` folds diacritics, so «Joustunud» is FINISHED there; the closure
    # table does not, so no reason is read and nothing is created.
    with pytest.raises(ReadingRefused) as refused:
        read_topic(bundle(hetkeseis="Joustunud"), as_of=AS_OF, people={}, historical=False)
    assert refused.value.code == "CLOSURE_REASON_UNKNOWN"


@pytest.mark.parametrize(
    "values",
    [
        {"arvamuse_tahtaeg": "2027-01-15"},  # in the future
        {"arvamuse_tahtaeg": "2019-01-15"},  # passed, nothing sent
        {"arvamuse_tahtaeg": "2019-01-15", "valja": "ei saatnud"},
        {"arvamuse_tahtaeg": "2019-01-15", "valja": "saadetud", "valja_kuupaev": "2019-01-10"},
        {"arvamuse_tahtaeg": "2027-01-15", "koja_ettepanek": True},
        {"arvamuse_tahtaeg": "2027-01-15", "hetkeseis": "jõustunud"},
        {
            "arvamuse_tahtaeg": "2027-01-15",
            "jargmiseks": "Koostan arvamuse",
            "tegevuse_tahtaeg": "2027-01-10",
        },
    ],
)
def test_a_historical_opinion_deadline_is_never_a_current_obligation(values):
    topic_ = _historical(**values)
    assert topic_["deadline"]["treatment"] == "SOURCE_ONLY"
    assert topic_["next_action"] is None
    assert lovable_backfill.history_problems(topic_) == []
    assert validate_package(_package(topic_, HISTORY_COHORT)).problems == []


def test_lopeta_teema_still_offers_only_real_reasons():
    # The blank is the importer's, for history only: a lawyer's closure is unchanged.
    assert "" not in DIRECT_CLOSURE_DISPOSITIONS
    assert set(DIRECT_CLOSURE_DISPOSITIONS) <= set(Disposition.values)


# ---------------------------------------------------------------------------
# The backfill end to end, with the real reader
# ---------------------------------------------------------------------------


@pytest.fixture
def pilot_on(settings):
    settings.LOVABLE_PILOT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example"  # the MCP reader's links
    return settings


def _h(n: int, nr: str, **values: Any) -> dict[str, Any]:
    values.setdefault("title", f"Ajalooline näidisteema {n}")
    return topic(n, nr=nr, aasta=int(nr[:4]), **values)


@pytest.fixture
def history(db):
    """Every historical case beside a live register, its evidence and its tombstones."""
    enrol_register()
    fake = FakeLovable(
        [
            topic(1, operational=True, hetkeseis="Riigikogus", title="Registri teema"),
            # Selected: one per closure basis, «ei saatnud», deadlines, a continuation.
            _h(
                2,
                "2019_2",
                hetkeseis="Riigikogus",
                valja="ei saatnud",
                arvamuse_tahtaeg="2019-02-01",
            ),
            _h(3, "2019_3", hetkeseis="jõustunud"),
            _h(4, "2019_4", hetkeseis="rohkem ei tegele", valja="ei saatnud"),
            _h(5, "2020_5", hetkeseis="kooskõlastusringil"),
            _h(
                6,
                "2020_6",
                hetkeseis="idee",
                arvamuse_tahtaeg="2027-02-01",
                jargmiseks="Koostan arvamuse",
                tegevuse_tahtaeg="2027-01-20",
            ),
            _h(7, "2018_7", hetkeseis="lõpetatud", valja="ei saatnud"),
            # Held identities: a replacement tombstone's UUID; a released reference.
            _h(8, "2018_8", hetkeseis="jõustunud"),
            _h(9, "2018_9", hetkeseis="jõustunud"),
            # Ambiguous: an archive page, a shared reference, a reference-only match.
            _h(10, "2017_10", onenote_link=onenote_link(10)),
            _h(11, "2017_11", title="Kaks korda"),
            _h(12, "2017_11", title="Kaks korda, teine"),
            _h(13, "2016_13"),
        ]
    )
    fake.link(5, 6)  # 2020_5 continues as 2020_6
    register = register_teema(1)
    # Existing evidence on the register Teema: a OneNote page link.
    page = archive_page(1)
    MatterSourcePage.objects.create(
        matter=register, source_page=page, match_method=SourceMatchMethod.MANUAL
    )
    tombstone(lovable_id=sid(8))
    released = tombstone()
    record_security_event(
        event_type=SecurityEventType.REFERENCE_RELEASED,
        subject=released,
        detail={"reference": "2018_9"},
    )
    archive_page(10)
    holder = factories.MatterFactory(reference_year=2016, reference_number=13)
    return {
        "fake": fake,
        "register": register,
        "holder": holder,
        "document": reading(fake),
    }


def _select(history_: dict[str, Any]):
    ledger = build_ledger(history_["document"])
    return ledger, build_package(
        history_["document"],
        ledger,
        reader=read_topic,
        reader_version=READER_VERSION,
    )


def _apply(package: dict[str, Any]):
    plan = lovable_backfill.plan(package)
    return lovable_backfill.apply(
        package,
        operator_intent=HISTORY_COHORT,
        expect_package_sha256=plan.digest,
        expect_plan_sha256=plan_digest(plan),
        backup_set=BACKUP,
    )


def _state(qs) -> list[dict[str, Any]]:
    return sorted(qs.values(), key=lambda r: str(r["id"]))


def test_the_package_selects_only_free_history_and_lists_the_ambiguous_for_a_person(history):
    ledger, selection = _select(history)
    assert selection.selected == ["2018_7", "2019_2", "2019_3", "2019_4", "2020_5", "2020_6"]
    left = {i["reference"]: i["reason"] for i in selection.left_out}
    assert left["2018_8"] == left["2018_9"] == "TOMBSTONE_HELD"
    # Never forced into a match: each ambiguous row is in the review list, with why.
    review = {(i["reference"], i["category"]): i for i in selection.review}
    assert set(review) == {
        ("2017_10", Category.ARCHIVE_UNLINKED),
        ("2017_11", Category.POSSIBLE_DUPLICATE),
        ("2016_13", Category.LINK_ENRICHMENT),
    }
    assert review[("2017_10", Category.ARCHIVE_UNLINKED)]["archive_pages"]
    assert len([i for i in selection.review if i["reference"] == "2017_11"]) == 2
    assert review[("2016_13", Category.LINK_ENRICHMENT)]["reason"] == "SAME_REFERENCE_NO_IDENTITY"
    report = selection.report()
    assert report["review_by_category"] == {
        "ARCHIVE_UNLINKED": 1,
        "LINK_ENRICHMENT": 1,
        "POSSIBLE_DUPLICATE": 2,
    }
    # The owner's before/after view of what would be created.
    assert report["distribution"]["disposition"] == {
        "": 3,
        "COMPLETED": 1,
        "MONITORING_STOPPED": 1,
        "SUPERSEDED": 1,
    }
    assert report["distribution"]["closure_basis"] == {
        "CONTINUATION": 1,
        "SOURCE_STATUS": 2,
        "UNKNOWN": 3,
    }
    assert report["distribution"]["deadline_treatment"] == {"NONE": 4, "SOURCE_ONLY": 2}
    assert "NO_POSITION_FORMED" not in report["distribution"]["disposition"]
    # The coverage report says the same to a person.
    markdown = coverage_markdown(ledger)
    section = markdown.split("## Needs a person")[1].split("## Ledger")[0]
    assert "Undecided rows: 4" in section
    for reference in ("2017_10", "2017_11", "2016_13"):
        assert f"| {reference} |" in section
    assert "2019_2" not in section


def test_the_backfill_applies_closed_history_with_only_stated_reasons(history, pilot_on, client):
    _ledger, selection = _select(history)
    package = selection.package
    assert lovable_backfill.validate(package) == []
    register, holder = history["register"], history["holder"]
    untouched = Matter.all_objects.exclude(evidence_pilot_records__pilot=HISTORY_COHORT)
    before = _state(untouched)
    evidence_before = (
        _state(MatterSourcePage.objects.all()),
        _state(EvidencePilotRecord.objects.all()),
        _state(MatterSourceReference.objects.all()),
    )
    preserved = lovable_pilot.preserved_snapshot()

    dry = lovable_backfill.dry_run(package)
    assert dry.verify.ok, dry.verify.discrepancies
    report = _apply(package)
    assert report.verify.ok and report.written["MATTER"] == 6

    created = {
        m.display_reference: m
        for m in Matter.objects.filter(
            evidence_pilot_records__pilot=HISTORY_COHORT,
            evidence_pilot_records__record_kind="MATTER",
        )
    }
    assert {ref: m.disposition for ref, m in created.items()} == {
        "2018_7": "",  # «lõpetatud» + «ei saatnud»: no stated reason
        "2019_2": "",  # a live stage + «ei saatnud»: not NO_POSITION_FORMED
        "2019_3": "COMPLETED",
        "2019_4": "MONITORING_STOPPED",
        "2020_5": "SUPERSEDED",
        "2020_6": "",
    }
    for matter in created.values():
        assert matter.is_open is False and matter.record_mode == "ARCHIVE"
        assert matter.closed_at is None and matter.closed_by_id is None
        assert matter.response_deadline is None and matter.response_requested_at is None
    assert created["2020_5"].superseded_by_id == created["2020_6"].pk
    assert not NextAction.objects.filter(matter__in=created.values()).exists()
    assert not ChangeEvent.objects.filter(
        matter__in=created.values(), event_type=ChangeEventType.MATTER_CLOSED
    ).exists()

    # A blank reason reads as no reason anywhere: «Mitteaktiivne», never a closure claim.
    blank = created["2019_2"]
    assert blank.work_status == INACTIVE and blank.closure_reason_label == ""
    viewer = factories.UserFactory()
    from app.matters import work_items
    from app.mcp_server import readers

    assert (
        not work_items.outstanding_response_deadlines(viewer)
        .filter(pk__in=[m.pk for m in created.values()])
        .exists()
    )
    snapshot = readers.matter_snapshot(viewer, str(blank.pk))
    assert snapshot["disposition"] is None and snapshot["is_open"] is False
    assert snapshot["work_status"]["key"] == "INACTIVE"
    assert snapshot["opinion_deadline"] is None
    client.force_login(viewer)
    page = client.get(reverse("matters:matter_detail", kwargs={"pk": blank.pk}))
    assert page.status_code == 200
    text = page.content.decode()
    assert "Teema on suletud." in text
    for invented in ("Koda lõpetas jälgimise", "Koda ei tegele edasi", "Seisukohta ei kujundatud"):
        assert invented not in text

    # Provenance: identity, reference, the source's status and «ei saatnud», the reading.
    source = MatterSourceReference.objects.get(matter=blank, source_system="LOVABLE")
    raw = source.source_row_raw
    assert raw["lovable"]["id"] == sid(2)
    assert raw["row"]["nr"] == "2019_2" and raw["row"]["hetkeseis"] == "Riigikogus"
    assert raw["row"]["valja"] == "ei saatnud" and raw["row"]["arvamuse_tahtaeg"] == "2019-02-01"
    assert any(n.startswith("CLOSURE_REASON_UNKNOWN") for n in raw["review_notes"])
    assert any(n.startswith("NOT_SENT_RECORDED") for n in raw["review_notes"])
    continued = MatterSourceReference.objects.get(matter=created["2020_5"], source_system="LOVABLE")
    assert continued.source_row_raw["stage_links"] == [{"prev": sid(5), "next": sid(6)}]

    # Nothing that was there before moved: Teemad, tombstones, evidence.
    assert _state(untouched) == before
    assert (
        _state(MatterSourcePage.objects.all()),
        _state(EvidencePilotRecord.objects.exclude(pilot=HISTORY_COHORT)),
        _state(MatterSourceReference.objects.exclude(matter__in=created.values())),
    ) == evidence_before
    assert lovable_pilot.check_preserved(preserved).ok
    assert register.pk not in {m.pk for m in created.values()} and holder.pk not in {
        m.pk for m in created.values()
    }
    assert lovable_backfill.verify(package).ok


def test_reapplying_or_rebuilding_from_the_same_reading_changes_nothing(history, pilot_on):
    _ledger, selection = _select(history)
    first = _apply(selection.package)
    assert first.written["MATTER"] == 6
    count = Matter.all_objects.count()
    records = EvidencePilotRecord.objects.count()

    again = _apply(selection.package)
    assert again.already_applied is True
    assert (Matter.all_objects.count(), EvidencePilotRecord.objects.count()) == (count, records)

    # A fresh package from the same reading creates nothing either: every topic it
    # selected is now an EXACT match, so none is selected and nothing duplicates.
    ledger, rebuilt = _select(history)
    assert rebuilt.package is None and rebuilt.selected == []
    assert {row_of(ledger, n).category for n in (2, 3, 4, 5, 6, 7)} == {Category.EXACT}
    assert Matter.all_objects.count() == count


def test_a_package_read_by_another_reader_version_is_refused(history, pilot_on):
    _ledger, selection = _select(history)
    stale = copy.deepcopy(selection.package)
    stale["mapping_version"] = "lovable-reading/1"
    for topic_ in stale["topics"]:
        topic_["reading"]["reader_version"] = "lovable-reading/1"
    assert any(lovable_backfill.STALE_READER in p for p in lovable_backfill.validate(stale))
    plan = lovable_backfill.plan(stale)
    assert any(lovable_backfill.STALE_READER in p for p in plan.problems)
    before = Matter.all_objects.count()
    with pytest.raises(lovable_pilot.LovablePilotError, match="The plan has problems"):
        lovable_pilot.dry_run(plan)
    assert Matter.all_objects.count() == before
    # One topic stamped by another version than its package is refused too.
    mixed = copy.deepcopy(selection.package)
    mixed["topics"][0]["reading"]["reader_version"] = "lovable-reading/1"
    assert any("read by lovable-reading/1" in p for p in lovable_backfill.validate(mixed))


# ---------------------------------------------------------------------------
# The stage mapping: every Lovable label is a reviewed label of its stage
# ---------------------------------------------------------------------------


def test_every_stage_mapping_is_a_reviewed_label_of_its_own_stage():
    """Each of the 14 labels is, word for word (case aside), a label the reviewed
    vocabulary gave that very stage in one of its versions — never a neighbour."""
    from app.workflow.reference_stages import (
        MONITORING_STOPPED_STAGE,
        REFERENCE_STAGES_V1,
        REWORDED_STAGE_LABELS_V2,
    )

    labels: dict[str, set[str]] = {}
    for stage in (*REFERENCE_STAGES_V1, MONITORING_STOPPED_STAGE):
        labels.setdefault(stage.key, set()).add(stage.label_et.casefold())
    for key, label in REWORDED_STAGE_LABELS_V2.items():
        labels[key].add(label.casefold())
    assert len(lovable_reading.STAGE_MAPPING) == 14
    for source_label, key in lovable_reading.STAGE_MAPPING.items():
        assert source_label.casefold() in labels[key], (source_label, key)
    # The stages that end the work are exactly the product's terminal ones.
    from app.workflow.stage_flow import TERMINAL_STAGE_KEYS

    assert lovable_reading.TERMINAL_STAGES == TERMINAL_STAGE_KEYS


def test_stage_links_that_loop_are_kept_unlinked_and_never_block_the_package(db):
    enrol_register()
    fake = FakeLovable(
        [
            _h(21, "2015_21", hetkeseis="idee"),
            _h(22, "2015_22", hetkeseis="idee"),
            _h(23, "2015_23", hetkeseis="idee"),
            _h(24, "2015_24", hetkeseis="idee"),
        ]
    )
    fake.link(21, 22)
    fake.link(22, 21)  # a loop in the source
    fake.link(23, 24)  # an ordinary chain beside it
    _ledger, selection = _select({"document": reading(fake)})
    by_reference = {t["reference"]: t for t in selection.package["topics"]}
    for looped in ("2015_21", "2015_22"):
        topic_ = by_reference[looped]
        assert topic_["disposition"] == "SUPERSEDED" and topic_["successor"] == ""
        assert any(n.startswith("CONTINUATION_NOT_LINKED") for n in topic_["review_notes"])
    assert by_reference["2015_23"]["successor"] == "2015_24"
    assert by_reference["2015_24"]["disposition"] == ""
    assert lovable_backfill.validate(selection.package) == []
