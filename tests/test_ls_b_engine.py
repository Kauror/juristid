# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""The live synchronisation engine (docs/adr/0213), on synthetic data.

The world is a small `lovable-register` cohort built the way production's was:
a synthetic package applied through `lovable_pilot`, its provenance relabelled to
the register, enrolled by the replacement's enrolment. Lovable is a fake
transport serving invented rows. The 09.11 cases are reproduced by their shape,
under source authority since 11.11 (docs/adr/0213 §10; the full reference set is
`tests/test_r4_authority.py`):

* 2026_238 — the next step changes from preparing the opinion to waiting for a
  Commission proposal → the WAIT replaces the drafting step, which stays in the
  history;
* 2026_232 — the next step changes to waiting for the government's approval →
  replaced;
* 2026_253 — the opinion step is replaced by a later review and the deadline
  cleared → the step replaced, the source's own request withdrawn, its history kept;
* 2026_254–257 — new topics: planned only in COMPARE, created once in APPLY,
  none on a rerun; the one that cannot be read safely is held.

Every title, name and link is invented.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import urllib.parse
from typing import Any

import pytest
from django.db import IntegrityError

from app.audit.models import ChangeEvent
from app.legacy_import import lovable_source, lovable_sync
from app.legacy_import.lovable_pilot import PILOT, REGISTER_COHORT, build_plan
from app.legacy_import.lovable_sync import (
    AUTO_FIELDS,
    CONDITIONAL_FIELDS,
    CONTINUATION,
    FIELD_POLICY,
    FIELDS,
    NEW_TOPIC,
    SyncRefused,
)
from app.legacy_import.lovable_sync_models import (
    LovableSyncDecision,
    LovableSyncMembership,
    LovableSyncNewTopic,
    LovableSyncRun,
    LovableSyncState,
    MembershipBasis,
    NewTopicStatus,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)
from app.legacy_import.models import EvidencePilotRecord, MatterSourceReference
from app.matters.models import Matter, MatterResponseDeadline
from app.submissions.models import Submission
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from app.workflow.services import OPINION_PREPARATION_TEXT
from tests import factories
from tests.test_lovable_pilot import _apply, _package, _topic, pilot_on, world  # noqa: F401
from tests.test_lovable_sync import (
    FakeLovable,
    _credentials,
    _decisions,
    _jwt,
    _matter,
    _Response,
    _run,
)

pytestmark = pytest.mark.django_db

TALLINN = lovable_sync.TALLINN
NIGHT = dt.datetime(2026, 11, 10, 0, 15, tzinfo=TALLINN)
HOUR = dt.timedelta(hours=1)
URL = "https://example.invalid/sites/naidis/_layouts/Doc.aspx?sourcedoc={DDDD}"


def _id(reference: str) -> str:
    return f"00000000-0000-0000-0000-{int(reference.split('_')[1]):012d}"


def _row(reference: str, **values: Any) -> dict[str, Any]:
    return {
        "nr": reference,
        "aasta": int(reference[:4]),
        "teema": f"Näidisteema {reference}",
        "vastutaja": "Mari",
        "uus_vastutaja": "",
        "hetkeseis": "Riigikogus",
        "jargmiseks": "",
        "tegevuse_tahtaeg": None,
        "arvamuse_tahtaeg": None,
        "valja": "",
        "valja_kuupaev": None,
        "kellele": "",
        "koja_ettepanek": False,
        "oigusakt": "",
        "sisse": "2026-01-05",
        "onenote_link": URL,
        "operatiivne_ulatus": True,
        **values,
    }


def _drafting(day: str) -> dict[str, Any]:
    return {
        "review": "READY",
        "kind": "DO",
        "date_semantics": "DEADLINE",
        "target_date": day,
        "date_precision": "EXACT",
        "text": OPINION_PREPARATION_TEXT,
        "source_text": "Koostan arvamust.",
        "treatment": "OPINION_DRAFTING",
        "reason": "drafting",
    }


def _step(kind: str, text: str, day: str) -> dict[str, Any]:
    return {
        "review": "READY",
        "kind": kind,
        "date_semantics": "DEADLINE" if kind == "DO" else "REVIEW_ON",
        "target_date": day,
        "date_precision": "EXACT",
        "text": text,
        "source_text": text,
        "treatment": "SOURCE_NEXT_STEP",
        "reason": kind,
    }


def _register_topic(reference: str, row: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    topic = _topic(
        reference, **{"deadline": {"treatment": "NONE"}, "onenote_url": URL, **overrides}
    )
    topic["source"]["id"] = _id(reference)
    topic["source"]["row"] = row
    return topic


class Source(FakeLovable):
    """`FakeLovable` with stage links, as `lovable_source` reads them."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self.selects: list[str] = []

    def select(
        self, table: str, columns: Any, *, filters: dict[str, str], order: str
    ) -> list[dict[str, Any]]:
        self.selects.append(table)
        if table == "topic_stage_link":
            column, value = next(iter(filters.items()))
            wanted = set(value[4:-1].split(","))
            return [dict(r) for r in self.links if r[column] in wanted]
        return super().select(table, columns, filters=filters, order=order)


@pytest.fixture
def register(world, pilot_on):
    """Five register Teemad (238, 232, 253, 240 open; 239 closed), enrolled."""
    rows = {
        "2026_238": _row("2026_238", jargmiseks="Koostan arvamuse", arvamuse_tahtaeg="2099-03-01"),
        "2026_232": _row(
            "2026_232",
            jargmiseks="Saadan arvamuse ministeeriumile",
            tegevuse_tahtaeg="2099-02-01",
        ),
        "2026_253": _row("2026_253", jargmiseks="Koostan arvamuse", arvamuse_tahtaeg="2099-03-15"),
        "2026_240": _row("2026_240", jargmiseks="ootan 2. lugemist", tegevuse_tahtaeg="2099-01-15"),
        "2026_239": _row("2026_239", hetkeseis="jõustunud"),
    }
    topics = [
        _register_topic(
            "2026_238",
            rows["2026_238"],
            next_action=_drafting("2099-03-01"),
            deadline={"treatment": "OPEN_REQUEST", "deadline": "2099-03-01"},
        ),
        _register_topic(
            "2026_232",
            rows["2026_232"],
            next_action=_step("DO", "Saadan arvamuse ministeeriumile", "2099-02-01"),
        ),
        _register_topic(
            "2026_253",
            rows["2026_253"],
            next_action=_drafting("2099-03-15"),
            deadline={"treatment": "OPEN_REQUEST", "deadline": "2099-03-15"},
        ),
        _register_topic(
            "2026_240",
            rows["2026_240"],
            next_action=_step("WAIT", "ootan 2. lugemist", "2099-01-15"),
        ),
        _register_topic(
            "2026_239",
            rows["2026_239"],
            status_raw="jõustunud",
            stage="in_force",
            lifecycle="INACTIVE",
            disposition="COMPLETED",
            next_action=None,
        ),
    ]
    package = _package(*topics)
    package["snapshot"]["highest_reference"] = {"2026": 260}
    package["reserve_through"] = {"2026": 260}
    digest = build_plan(package).digest
    _apply(package)
    EvidencePilotRecord.objects.filter(package_sha256=digest).update(pilot=REGISTER_COHORT)
    lovable_sync.enroll(
        cohort=REGISTER_COHORT,
        package_sha256=digest,
        expect=len(topics),
        reason="Lovable'i register (testmaailm)",
        replaces=[PILOT],
    )
    lovable_sync.set_mode(SyncMode.APPLY, reason="test")
    source = Source()
    for topic in topics:
        source.put(topic["source"]["id"], copy.deepcopy(topic["source"]["row"]), version=3)
    return {"source": source, "world": world, "topics": topics, "digest": digest}


def _new_topics(source: Source) -> None:
    """2026_254–257 appear in Lovable (05.11): an open request (254), a free-text
    next step (255), a finished topic (256) and an owner with no account (257)."""
    source.put(_id("2026_254"), _row("2026_254", arvamuse_tahtaeg="2099-05-01"), version=1)
    source.put(
        _id("2026_255"),
        _row("2026_255", jargmiseks="ootan Komisjoni ettepanekut", tegevuse_tahtaeg="2099-04-01"),
        version=1,
    )
    source.put(_id("2026_256"), _row("2026_256", hetkeseis="jõustunud"), version=1)
    source.put(_id("2026_257"), _row("2026_257", vastutaja="Tundmatu Isik"), version=1)


def _at(n: int) -> dt.datetime:
    return NIGHT + n * HOUR


# ---------------------------------------------------------------------------
# The field policy
# ---------------------------------------------------------------------------


def test_the_policy_names_every_reconciled_field_with_an_estonian_reason():
    assert set(FIELDS) | {CONTINUATION, NEW_TOPIC, "source_missing"} <= set(FIELD_POLICY)
    authoritative = [
        name for name, p in FIELD_POLICY.items() if p.policy == lovable_sync.AUTHORITATIVE
    ]
    assert set(authoritative) == {*lovable_sync.AUTHORITATIVE_FIELDS, CONTINUATION}
    assert FIELD_POLICY[NEW_TOPIC].policy == lovable_sync.CONDITIONAL
    assert FIELD_POLICY["operational_scope"].policy == lovable_sync.NOT_REPRESENTABLE
    assert all(
        p.explanation and p.policy in lovable_sync.POLICY_CLASSES for p in FIELD_POLICY.values()
    )
    # The archive path's own classes are unchanged (docs/adr/0213 §4).
    assert set(AUTO_FIELDS) == {"title", "owner", "next_action_date", "onenote_link"}
    assert set(CONDITIONAL_FIELDS) == {"stage", "opinion_deadline", "commencements"}


# ---------------------------------------------------------------------------
# The 09.11 cases
# ---------------------------------------------------------------------------


def test_238_preparation_to_waiting_for_a_commission_proposal_replaces_the_step(register):
    source = register["source"]
    source.edit(
        "2026_238",
        jargmiseks="Ootan Euroopa Komisjoni ettepanekut (avaldatakse 15.12.2026)",
        tegevuse_tahtaeg="2099-12-15",
    )
    drafting = NextAction.objects.get(matter=_matter("2026_238"), status=ActionStatus.OPEN)
    first = _run(register, at=_at(0))
    assert first.status == SyncRunStatus.SUCCEEDED, first.error
    found = _decisions(first, "2026_238")
    assert found["next_action_text"].outcome == SyncOutcome.APPLIED
    assert found["next_action_date"].outcome == SyncOutcome.APPLIED
    step = NextAction.objects.get(matter=_matter("2026_238"), status=ActionStatus.OPEN)
    assert (step.kind, step.date_semantics, step.target_date) == (
        "WAIT",
        "REVIEW_ON",
        dt.date(2099, 12, 15),
    )
    drafting.refresh_from_db()
    assert (drafting.status, drafting.replaced_by_id) == (ActionStatus.SUPERSEDED, step.pk)
    assert not Submission.objects.exists()

    second = _run(register, at=_at(24))
    assert "next_action_text" not in _decisions(second, "2026_238")
    assert not [p for p in lovable_sync.pending_differences() if p.reference == "2026_238"]


def test_232_opinion_action_to_waiting_for_government_approval_is_replaced(register):
    register["source"].edit("2026_232", jargmiseks="Ootan Vabariigi Valitsuse heakskiitu")
    run = _run(register, at=_at(0))
    found = _decisions(run, "2026_232")
    assert found["next_action_text"].outcome == SyncOutcome.APPLIED
    step = NextAction.objects.get(matter=_matter("2026_232"), status=ActionStatus.OPEN)
    assert (step.text, step.kind) == ("Ootan Vabariigi Valitsuse heakskiitu", "WAIT")
    assert NextAction.objects.get(
        matter=_matter("2026_232"), text="Saadan arvamuse ministeeriumile"
    ).status == (ActionStatus.SUPERSEDED)


def test_253_a_later_review_replacing_the_opinion_action_keeps_the_request_history(register):
    from app.matters.enums import ResponseDeadlineOutcome

    matter = _matter("2026_253")
    register["source"].edit(
        "2026_253",
        jargmiseks="Vaatan üle pärast kooskõlastusringi",
        tegevuse_tahtaeg="2099-04-01",
        arvamuse_tahtaeg=None,
    )
    run = _run(register, at=_at(0))
    found = _decisions(run, "2026_253")
    assert found["next_action_text"].outcome == SyncOutcome.APPLIED
    assert found["opinion_deadline"].outcome == SyncOutcome.APPLIED
    matter.refresh_from_db()
    # The source's own request is withdrawn, and stays in the history as such.
    assert matter.response_deadline is None
    ended = MatterResponseDeadline.objects.get(matter=matter)
    assert (ended.deadline, ended.outcome) == (
        dt.date(2099, 3, 15),
        ResponseDeadlineOutcome.CANCELLED,
    )
    step = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert (step.kind, step.target_date) == ("MONITOR", dt.date(2099, 4, 1))
    assert NextAction.objects.get(matter=matter, text=OPINION_PREPARATION_TEXT).status == (
        ActionStatus.SUPERSEDED
    )


def test_new_topics_are_planned_only_in_compare(register):
    lovable_sync.set_mode(SyncMode.COMPARE, reason="test")
    _new_topics(register["source"])
    matters = Matter.all_objects.count()
    run = _run(register, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    c = run.counts
    assert (c["new_topics_seen"], c["new_topics_planned"], c["new_topics_held"]) == (4, 3, 1)
    assert c["new_topics_created"] == 0
    assert Matter.all_objects.count() == matters
    ledger = {r.reference: r for r in LovableSyncNewTopic.objects.all()}
    assert {ref: r.status for ref, r in ledger.items()} == {
        "2026_254": NewTopicStatus.PLANNED,
        "2026_255": NewTopicStatus.PLANNED,
        "2026_256": NewTopicStatus.PLANNED,
        "2026_257": NewTopicStatus.HELD,
    }
    assert ledger["2026_257"].reason == f"{lovable_sync.NEW_UNREADABLE}:OWNER_UNRESOLVED"
    # The stored reading carries the new topics' own consistent reading.
    assert set(run.snapshot.document["new_topics"]["cohort"]) == {
        _id(f"2026_25{n}") for n in (4, 5, 6, 7)
    }


def test_new_topics_are_created_once_in_apply_and_none_on_a_rerun(register):
    _new_topics(register["source"])
    run = _run(register, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert (run.counts["new_topics_created"], run.counts["new_topics_held"]) == (3, 1)

    open_request = _matter("2026_254")
    assert open_request.is_open and open_request.response_deadline == dt.date(2099, 5, 1)
    step = NextAction.objects.get(matter=open_request, status=ActionStatus.OPEN)
    assert (step.kind, step.text, step.target_date) == (
        "DO",
        OPINION_PREPARATION_TEXT,
        dt.date(2099, 5, 1),
    )
    free_text = _matter("2026_255")
    # Created minimal by the reader, then mirrored the same night (docs/adr/0213 §10).
    step = NextAction.objects.get(matter=free_text, status=ActionStatus.OPEN)
    assert (step.kind, step.text, step.target_date) == (
        "WAIT",
        "ootan Komisjoni ettepanekut",
        dt.date(2099, 4, 1),
    )
    finished = _matter("2026_256")
    assert (finished.is_open, finished.record_mode, finished.closed_at) == (False, "ARCHIVE", None)
    assert not Matter.all_objects.filter(reference_year=2026, reference_number=257).exists()

    for reference in ("2026_254", "2026_255", "2026_256"):
        matter = _matter(reference)
        state = LovableSyncState.objects.get(matter=matter)
        assert (state.cohort, str(state.source_id)) == (REGISTER_COHORT, _id(reference))
        assert LovableSyncMembership.objects.get(matter=matter).basis == MembershipBasis.NEW_TOPIC
        source = MatterSourceReference.objects.get(matter=matter)
        assert source.source_parser_version.endswith("+machine")
        assert source.reviewed_by == ""
    mirrored = LovableSyncState.objects.get(matter=free_text).applied
    assert mirrored["next_action_text"].get("import_held") is None
    assert mirrored["next_action_text"][lovable_sync.MIRRORED] is True
    assert lovable_sync.expected_cohorts() == {REGISTER_COHORT: 8}
    assert (
        LovableSyncDecision.objects.filter(field=NEW_TOPIC, outcome=SyncOutcome.APPLIED).count()
        == 3
    )

    matters = Matter.all_objects.count()
    again = _run(register, at=_at(1))
    assert again.status == SyncRunStatus.SUCCEEDED, again.error
    assert again.counts["new_topics_created"] == 0
    assert (again.counts["new_topics_seen"], again.counts["new_topics_held"]) == (1, 1)
    assert Matter.all_objects.count() == matters
    assert again.counts["checked"] == 8
    # The created Teemad are compared from now on, unchanged.
    for reference in ("2026_254", "2026_256"):
        assert _decisions(again, reference) == {}
    assert LovableSyncNewTopic.objects.get(reference="2026_257").status == NewTopicStatus.HELD


def test_a_second_creation_of_one_topic_is_refused_by_the_database(register):
    _new_topics(register["source"])
    _run(register, at=_at(0))
    state = LovableSyncState.objects.get(matter=_matter("2026_254"))
    with pytest.raises(IntegrityError):
        LovableSyncDecision.objects.create(
            run=LovableSyncRun.objects.first(),
            state=state,
            field=NEW_TOPIC,
            outcome=SyncOutcome.APPLIED,
            idempotency_key=lovable_sync.new_topic_key(_id("2026_254")),
        )


def test_the_new_topic_breaker_creates_none_and_says_so(register, monkeypatch):
    monkeypatch.setattr(lovable_sync, "MAX_NEW_TOPICS", 2)
    _new_topics(register["source"])
    register["source"].edit("2026_240", teema="Näidisteema 2026_240 parandatud")
    matters = Matter.all_objects.count()
    run = _run(register, at=_at(0))
    assert (run.status, run.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.NEW_BREAKER)
    assert Matter.all_objects.count() == matters
    assert run.counts["new_topics_planned"] == 3
    # The field breakers are separate: the safe title change is still applied.
    assert _matter("2026_240").title == "Näidisteema 2026_240 parandatud"


def test_a_tombstone_holding_the_uuid_is_review_never_recreated(register):
    from app.matters.deletion import delete_matter

    _new_topics(register["source"])
    _run(register, at=_at(0))
    created = _matter("2026_254")
    delete_matter(matter=created, actor=None)
    # Deleted in Juristid; Lovable still has it: never re-created.
    run = _run(register, at=_at(1))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    row = LovableSyncNewTopic.objects.get(source_id=_id("2026_254"))
    assert (row.status, row.reason) == (NewTopicStatus.HELD, lovable_sync.NEW_UUID_TOMBSTONE)
    assert row.created_matter_id == created.pk
    assert run.counts["new_topic_reasons"].get(lovable_sync.NEW_UUID_TOMBSTONE) == 1
    assert Matter.all_objects.filter(reference_year=2026, reference_number=254).count() == 1
    # The joined member left with its deletion; the size check still holds.
    assert lovable_sync.expected_cohorts() == {REGISTER_COHORT: 7}


def test_a_source_reference_on_a_tombstone_holds_the_uuid(register):
    from django.utils import timezone

    lovable_sync.set_mode(SyncMode.COMPARE, reason="test")
    _new_topics(register["source"])
    old = factories.MatterFactory()
    MatterSourceReference.objects.create(
        matter=old,
        source_system="LOVABLE",
        source_file_name="public.topic",
        source_row_raw={"lovable": {"id": _id("2026_255")}},
        source_title="vana",
    )
    Matter.all_objects.filter(pk=old.pk).update(deleted_at=timezone.now())
    run = _run(register, at=_at(0))
    row = LovableSyncNewTopic.objects.get(source_id=_id("2026_255"))
    assert (row.status, row.reason) == (NewTopicStatus.HELD, lovable_sync.NEW_UUID_TOMBSTONE)
    assert run.counts["new_topics_held"] == 2


def test_a_resolved_new_topic_is_never_created(register):
    lovable_sync.set_mode(SyncMode.COMPARE, reason="test")
    _new_topics(register["source"])
    _run(register, at=_at(0))
    admin = factories.UserFactory(display_name="Haldur Näidis")
    with pytest.raises(SyncRefused) as refused:
        lovable_sync.resolve_new_topic(source_id=_id("2026_255"), note="  ")
    assert refused.value.code == "NO_NOTE"
    row = lovable_sync.resolve_new_topic(
        source_id=_id("2026_255"), note="Loon käsitsi", actor=admin
    )
    assert (row.status, row.decided_by) == (NewTopicStatus.RESOLVED, admin)
    lovable_sync.set_mode(SyncMode.APPLY, reason="test")
    run = _run(register, at=_at(1))
    assert run.counts["new_topics_resolved"] == 1
    assert not Matter.all_objects.filter(reference_year=2026, reference_number=255).exists()
    assert (
        LovableSyncNewTopic.objects.get(source_id=_id("2026_255")).status == NewTopicStatus.RESOLVED
    )


# ---------------------------------------------------------------------------
# A / B / C
# ---------------------------------------------------------------------------


def test_a_source_change_overwrites_a_local_one(register):
    from app.matters.services import set_matter_title

    set_matter_title(matter=_matter("2026_240"), value="Juristi pealkiri", actor=None)
    register["source"].edit("2026_240", teema="Lovable'i uus pealkiri")
    run = _run(register, at=_at(0))
    assert _decisions(run, "2026_240")["title"].outcome == SyncOutcome.APPLIED
    assert _matter("2026_240").title == "Lovable'i uus pealkiri"


def test_a_local_change_is_restored_and_a_matching_one_converges(register):
    from app.matters.services import change_stage
    from app.workflow.models import StageVocabulary

    matter = _matter("2026_240")
    change_stage(matter=matter, stage=StageVocabulary.objects.get(key="government"), actor=None)
    run = _run(register, at=_at(0))
    # Lovable did not move: its stage is put back (owner decision 11.11).
    stage = _decisions(run, "2026_240")["stage"]
    assert (stage.outcome, stage.reason) == (SyncOutcome.APPLIED, lovable_sync.RESTORED)
    assert _matter("2026_240").stage.key == "parliament"
    change_stage(
        matter=_matter("2026_240"),
        stage=StageVocabulary.objects.get(key="government"),
        actor=None,
    )
    register["source"].edit("2026_240", hetkeseis="valitsuses")
    run = _run(register, at=_at(1))
    decision = _decisions(run, "2026_240")["stage"]
    assert decision.outcome == SyncOutcome.ALREADY_CONVERGED
    state = LovableSyncState.objects.get(matter=matter)
    assert state.applied["stage"]["source"] == "valitsuses"
    assert state.applied["stage"]["target"]["stage"] == "government"


def test_a_stage_that_does_not_end_the_work_is_applied(register):
    register["source"].edit("2026_240", hetkeseis="valitsuses")
    run = _run(register, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert _matter("2026_240").stage.key == "government"
    applied = run.decisions.get(state__reference="2026_240", field="stage")
    assert applied.outcome == SyncOutcome.APPLIED and applied.idempotency_key


def test_a_terminal_stage_closes_with_the_sources_reason(register):
    register["source"].edit("2026_240", hetkeseis="jõustunud")
    run = _run(register, at=_at(0))
    assert _decisions(run, "2026_240")["stage"].outcome == SyncOutcome.APPLIED
    matter = _matter("2026_240")
    assert (matter.is_open, matter.stage.key, matter.disposition) == (
        False,
        "in_force",
        "COMPLETED",
    )
    # The open step ended with the closure: cancelled, never completed.
    assert not NextAction.objects.filter(matter=matter, status=ActionStatus.COMPLETED).exists()


def test_an_unmapped_stage_is_review(register):
    register["source"].edit("2026_240", hetkeseis="Riigikogus menetluses")
    run = _run(register, at=_at(0))
    assert _decisions(run, "2026_240")["stage"].reason == lovable_sync.STAGE_UNMAPPED


def test_a_new_opinion_deadline_sets_or_moves_the_one_request(register):
    from app.matters.enums import ResponseDeadlineOutcome

    register["source"].edit("2026_240", arvamuse_tahtaeg="2099-06-01")
    register["source"].edit("2026_238", arvamuse_tahtaeg="2099-06-01")
    run = _run(register, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert _matter("2026_240").response_deadline == dt.date(2099, 6, 1)
    assert _decisions(run, "2026_238")["opinion_deadline"].outcome == SyncOutcome.APPLIED
    matter = _matter("2026_238")
    assert matter.response_deadline == dt.date(2099, 6, 1)
    # The same request, moved — never a second consultation.
    moved = MatterResponseDeadline.objects.get(matter=matter)
    assert moved.outcome == ResponseDeadlineOutcome.MOVED


@pytest.mark.parametrize(
    ("values", "request_open"),
    [
        ({"valja": "ei saatnud"}, False),
        ({"valja_kuupaev": "2026-11-01"}, False),
        ({"koja_ettepanek": True}, True),
    ],
)
def test_a_new_deadline_with_anything_sent_opens_no_request(register, values, request_open):
    register["source"].edit("2026_240", arvamuse_tahtaeg="2099-06-01", **values)
    run = _run(register, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    # Sent or «ei saatnud»: nothing is owed now. A Chamber initiative keeps its
    # own deadline (docs/adr/0151 amendment).
    assert (_matter("2026_240").response_deadline is not None) == request_open
    assert not Submission.objects.exists()


def test_a_past_opinion_deadline_is_never_an_obligation_or_a_send(register):
    from app.legacy_import.lovable_sync_models import LovableOpinionOutcome

    register["source"].edit("2026_240", arvamuse_tahtaeg="2026-10-01")
    run = _run(register, at=_at(0))
    decision = _decisions(run, "2026_240")["opinion_deadline"]
    assert decision.outcome == SyncOutcome.ALREADY_CONVERGED
    assert _matter("2026_240").response_deadline is None
    assert not Submission.objects.exists()
    assert not LovableOpinionOutcome.objects.exists()


def test_an_added_estimate_stays_an_estimate_and_never_becomes_work(register):
    from app.intelligence.models import MatterImportantDate
    from app.matters.work_items import important_deadlines

    source = register["source"]
    source.commencements.append(
        {
            "id": "c1",
            "topic_id": _id("2026_240"),
            "liik": "Eeldatava kuupäevaga",
            "kuupaev": "2099-07-01",
            "tapsustus": "",
            "position": 1,
        }
    )
    source.edit("2026_240")
    run = _run(register, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    dates = MatterImportantDate.objects.filter(matter=_matter("2026_240"))
    assert list(dates.values_list("kind", "date_value")) == [
        ("ESTIMATED_COMMENCEMENT", dt.date(2099, 7, 1))
    ]
    mari = register["world"]["mari"]
    assert not [
        i for i in important_deadlines(mari) if getattr(i, "kind", "").endswith("COMMENCEMENT")
    ]
    source.commencements.clear()
    source.edit("2026_240")
    removed = _run(register, at=_at(1))
    decision = _decisions(removed, "2026_240")["commencements"]
    # Removed in Lovable: cancelled through its service, kept in the history.
    assert decision.outcome == SyncOutcome.APPLIED
    assert dates.count() == 1 and dates.get().status == "CANCELLED"


def test_a_new_continuation_closes_the_predecessor_under_its_successor(register):
    source = register["source"]
    _run(register, at=_at(0))  # the continuation's baseline starts here
    source.links.append({"prev_topic_id": _id("2026_240"), "next_topic_id": _id("2026_232")})
    source.edit("2026_240")
    run = _run(register, at=_at(1))
    assert _decisions(run, "2026_240")[CONTINUATION].outcome == SyncOutcome.APPLIED
    matter = _matter("2026_240")
    assert (matter.is_open, matter.disposition, matter.superseded_by_id) == (
        False,
        "SUPERSEDED",
        _matter("2026_232").pk,
    )


def test_future_dated_narrative_is_not_a_past_event(register):
    from app.legacy_import.lovable_sync_models import LovableOpinionOutcome

    register["source"].edit(
        "2026_232",
        jargmiseks="Arvamus saadeti 20.12.2026; ootame vastust",
        valja="",
    )
    run = _run(register, at=_at(0))
    # The sentence becomes the step, verbatim and unclassified; nothing is sent.
    assert _decisions(run, "2026_232")["next_action_text"].outcome == SyncOutcome.APPLIED
    step = NextAction.objects.get(matter=_matter("2026_232"), status=ActionStatus.OPEN)
    assert step.kind == "SOURCE"
    assert not Submission.objects.exists()
    assert not LovableOpinionOutcome.objects.exists()


def test_lovable_reopening_a_closed_teema_reopens_it_and_a_vanished_one_is_not_deleted(register):
    register["source"].edit("2026_239", teema="Uus pealkiri", hetkeseis="valitsuses")
    run = _run(register, at=_at(0))
    found = _decisions(run, "2026_239")
    assert found["title"].outcome == SyncOutcome.APPLIED
    assert found["stage"].outcome == SyncOutcome.APPLIED
    reopened = _matter("2026_239")
    assert (reopened.is_open, reopened.stage.key) == (True, "government")
    del register["source"].rows[_id("2026_239")]
    gone = _run(register, at=_at(1))
    assert _decisions(gone, "2026_239")["*"].outcome == SyncOutcome.SOURCE_MISSING
    matter = _matter("2026_239")
    assert matter.deleted_at is None and matter.title == "Uus pealkiri"


# ---------------------------------------------------------------------------
# Failure, idempotency, the source connection, the manual run
# ---------------------------------------------------------------------------


def test_a_failed_bookkeeping_keeps_every_baseline_and_is_retryable(register, monkeypatch):
    from app.matters.services import change_stage
    from app.workflow.models import StageVocabulary

    matter = _matter("2026_240")
    change_stage(matter=matter, stage=StageVocabulary.objects.get(key="government"), actor=None)
    register["source"].edit("2026_240", hetkeseis="valitsuses")
    before = LovableSyncState.objects.get(matter=matter).applied

    def broken(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(lovable_sync, "_observe_index", broken)
    run = _run(register, at=_at(0))
    assert run.status == SyncRunStatus.FAILED
    assert run.error.startswith("INTERNAL") and "stage=record" in run.error
    assert LovableSyncState.objects.get(matter=matter).applied == before
    assert not run.decisions.exists()
    monkeypatch.undo()
    retry = _run(register, at=_at(1))
    assert retry.status == SyncRunStatus.SUCCEEDED, retry.error
    assert LovableSyncState.objects.get(matter=matter).applied["stage"]["source"] == "valitsuses"


def test_the_same_reading_twice_writes_once(register):
    register["source"].edit("2026_240", hetkeseis="valitsuses", teema="Uus nimi")
    _run(register, at=_at(0))
    events = ChangeEvent.objects.count()
    again = _run(register, at=_at(1))
    assert not again.decisions.filter(outcome=SyncOutcome.APPLIED).exists()
    assert ChangeEvent.objects.count() == events


def test_a_manual_run_reads_the_source_fresh(register):
    _run(register, at=_at(0))
    source = register["source"]
    source.selects.clear()
    source.edit("2026_240", teema="Pärast eelmist lugemist")
    run = _run(register, at=_at(1))
    assert run.trigger == SyncTrigger.MANUAL and "topic" in source.selects
    assert _matter("2026_240").title == "Pärast eelmist lugemist"


def test_the_whole_run_over_http_is_get_only(register, tmp_path):
    """The engine through the real transport: every request a GET (and the one
    password sign-in POST), the new topics' reading included."""
    _new_topics(register["source"])
    fake = register["source"]
    methods: list[str] = []

    def opener(request: Any, timeout: float) -> Any:
        methods.append(request.get_method())
        if "/auth/v1/token" in request.full_url:
            return _Response({"access_token": _jwt("authenticated")})
        parts = urllib.parse.urlsplit(request.full_url)
        table = parts.path.rsplit("/", 1)[1]
        query = dict(urllib.parse.parse_qsl(parts.query))
        columns = query.pop("select").split(",")
        order = query.pop("order")
        offset = int(query.pop("offset"))
        query.pop("limit")
        rows = fake.select(table, columns, filters=query, order=order)
        return _Response(rows[offset:] if offset == 0 else [])

    transport = lovable_source.HttpTransport(
        lovable_source.load_config(_credentials(tmp_path)), opener=opener
    )
    run = lovable_sync.run_sync(
        trigger=SyncTrigger.MANUAL,
        transport=transport,
        backup_set=(_at(0) - dt.timedelta(minutes=5))
        .astimezone(dt.UTC)
        .strftime("sets/%Y%m%dT%H%M%SZ"),
        now=lambda: _at(0),
        pause=lambda _s: None,
    )
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert run.counts["new_topics_created"] == 3
    assert set(methods) == {"GET", "POST"} and methods.count("POST") == 1
    with pytest.raises(lovable_source.SourceError) as refused:
        transport._request("PATCH", "/rest/v1/topic", headers={})
    assert refused.value.code == "NOT_READ_ONLY"


# ---------------------------------------------------------------------------
# Decisions, pending differences, the replay, the on-demand gate
# ---------------------------------------------------------------------------


def test_an_administrators_decision_names_the_actor_and_clears_the_difference(register):
    register["source"].edit("2026_232", vastutaja="Tundmatu Isik")
    _run(register, at=_at(0))
    assert ("2026_232", "owner") in {
        (p.reference, p.field) for p in lovable_sync.pending_differences()
    }
    admin = factories.UserFactory(display_name="Haldur Näidis")
    decision = lovable_sync.resolve_field(
        reference="2026_232",
        field_name="owner",
        note="Jääb Juristide vastutaja",
        actor=admin,
    )
    assert decision.actor == admin and decision.reason.startswith(lovable_sync.RESOLVED_PREFIX)
    assert ("2026_232", "owner") not in {
        (p.reference, p.field) for p in lovable_sync.pending_differences()
    }


def test_a_user_sees_new_topics_counted_never_named(register):
    lovable_sync.set_mode(SyncMode.COMPARE, reason="test")
    _new_topics(register["source"])
    _run(register, at=_at(0))
    server = [p for p in lovable_sync.pending_differences() if p.kind == "NEW_TOPIC"]
    assert {p.reference for p in server} == {"2026_254", "2026_255", "2026_256", "2026_257"}
    reader = factories.UserFactory()
    shown = [
        p for p in lovable_sync.pending_differences(visible_to=reader) if p.kind == "NEW_TOPIC"
    ]
    assert len(shown) == 4 and {p.reference for p in shown} == {""}
    assert {p.source_id for p in shown} == {""}


def test_the_replay_writes_nothing(register):
    register["source"].edit("2026_238", jargmiseks="Ootan Euroopa Komisjoni ettepanekut")
    lovable_sync.set_mode(SyncMode.COMPARE, reason="test")  # nothing written: it stays eligible
    _run(register, at=_at(0))
    counts = (
        LovableSyncRun.objects.count(),
        LovableSyncDecision.objects.count(),
        ChangeEvent.objects.count(),
    )
    applied = {s.pk: json.dumps(s.applied, sort_keys=True) for s in LovableSyncState.objects.all()}
    result = lovable_sync.replay(today=_at(0).date())
    assert ("next_action_text", SyncOutcome.ELIGIBLE, "") in [
        tuple(row) for row in result["teemad"]["2026_238"]
    ]
    assert counts == (
        LovableSyncRun.objects.count(),
        LovableSyncDecision.objects.count(),
        ChangeEvent.objects.count(),
    )
    assert applied == {
        s.pk: json.dumps(s.applied, sort_keys=True) for s in LovableSyncState.objects.all()
    }
    # The database is writable again afterwards: the read-only setting went with it.
    factories.UserFactory()


def test_the_on_demand_gate_answers_only_for_a_waiting_request(register, monkeypatch):
    assert lovable_sync.due_on_demand() == "NOT_DUE NO_REQUEST"
    monkeypatch.setattr(lovable_sync, "pending_sync_request", lambda lane="lovable": True)
    assert lovable_sync.due_on_demand() == "DUE APPLY"
    lovable_sync.set_mode(SyncMode.OFF, reason="test")
    assert lovable_sync.due_on_demand() == "DUE OFF"
    monkeypatch.setenv(lovable_sync.HOST_BACKUP_SET_ENV, "sets/20261110T000000Z")
    assert lovable_sync.host_backup_set() == "sets/20261110T000000Z"


# ---------------------------------------------------------------------------
# The historical cohort
# ---------------------------------------------------------------------------


def test_a_historical_cohort_is_enrollable_and_follows_lovable_closed(register):
    rows = {
        ref: _row(ref, hetkeseis="jõustunud", operatiivne_ulatus=False)
        for ref in ("2026_101", "2026_102")
    }
    topics = [
        _register_topic(
            ref,
            row,
            status_raw="jõustunud",
            stage="in_force",
            lifecycle="INACTIVE",
            disposition="COMPLETED",
            next_action=None,
        )
        for ref, row in rows.items()
    ]
    package = _package(*topics)
    package["snapshot"]["highest_reference"] = {"2026": 260}
    package["reserve_through"] = {"2026": 260}
    digest = build_plan(package).digest
    _apply(package)
    EvidencePilotRecord.objects.filter(package_sha256=digest).update(
        pilot=lovable_sync.HISTORY_COHORT
    )
    done = lovable_sync.enroll(
        cohort=lovable_sync.HISTORY_COHORT,
        package_sha256=digest,
        expect=2,
        reason="Ajalugu (testmaailm)",
    )
    assert done.seeded == 2
    source = register["source"]
    for topic in topics:
        source.put(topic["source"]["id"], copy.deepcopy(topic["source"]["row"]), version=3)
    source.edit("2026_101", teema="Ajaloolise teema uus pealkiri", hetkeseis="valitsuses")
    del source.rows[_id("2026_102")]
    run = _run(register, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    found = _decisions(run, "2026_101")
    # The historical register follows Lovable too (owner decision 11.11): the
    # title is written; a reopening outside the operational scope is held.
    assert found["title"].outcome == SyncOutcome.APPLIED
    assert (found["stage"].outcome, found["stage"].reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.HISTORY_REOPENED_OUTSIDE_SCOPE,
    )
    assert _decisions(run, "2026_102")["*"].outcome == SyncOutcome.SOURCE_MISSING
    gone = _matter("2026_102")
    assert gone.deleted_at is None and not gone.is_open
    assert _matter("2026_101").title == "Ajaloolise teema uus pealkiri"
    assert not _matter("2026_101").is_open
    assert run.counts["by_cohort"][lovable_sync.HISTORY_COHORT]["checked"] == 2
    # A historical topic is not operational: never a new-topic candidate.
    assert run.counts["new_topics_seen"] == 0


# ---------------------------------------------------------------------------
# Review F (2026-11-09): B = A on the conditional rules, CHECK after creation
# ---------------------------------------------------------------------------


def test_a_locally_ended_request_is_never_a_second_consultation(register):
    """Answered or declined in Juristid while Lovable's `valja` is still blank: no
    new request episode — a visible technical hold until Lovable records it."""
    from app.matters.response_deadlines import resolve_response_deadline

    matter = _matter("2026_253")
    resolve_response_deadline(matter=matter, outcome="NOT_ANSWERING", actor=None)
    history = MatterResponseDeadline.objects.filter(matter=matter).count()
    register["source"].edit("2026_253", arvamuse_tahtaeg="2099-04-15")
    run = _run(register, at=_at(0))
    decision = _decisions(run, "2026_253")["opinion_deadline"]
    assert (decision.outcome, decision.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.ANSWERED_IN_JURISTID,
    )
    matter.refresh_from_db()
    assert matter.response_deadline is None
    assert MatterResponseDeadline.objects.filter(matter=matter).count() == history


def test_a_request_a_lawyer_opened_is_moved_never_a_second_request(register):
    from app.matters.response_deadlines import request_response_deadline

    matter = _matter("2026_240")
    request_response_deadline(matter=matter, deadline=dt.date(2099, 8, 1), actor=None)
    register["source"].edit("2026_240", arvamuse_tahtaeg="2099-06-01")
    run = _run(register, at=_at(0))
    assert _decisions(run, "2026_240")["opinion_deadline"].outcome == SyncOutcome.APPLIED
    matter.refresh_from_db()
    assert matter.response_deadline == dt.date(2099, 6, 1)
    assert MatterResponseDeadline.objects.filter(matter=matter, outcome="MOVED").count() == 1


def test_a_commencement_cancelled_in_juristid_does_not_block_an_added_one(register):
    from app.intelligence.enums import FactStatus
    from app.intelligence.models import MatterImportantDate

    source = register["source"]
    first = {
        "id": "c1",
        "topic_id": _id("2026_240"),
        "liik": "Eeldatava kuupäevaga",
        "kuupaev": "2099-07-01",
        "tapsustus": "",
        "position": 1,
    }
    source.commencements.append(first)
    source.edit("2026_240")
    _run(register, at=_at(0))
    MatterImportantDate.objects.filter(matter=_matter("2026_240")).update(
        status=FactStatus.CANCELLED
    )
    source.commencements.append({**first, "id": "c2", "kuupaev": "2099-09-01", "position": 2})
    source.edit("2026_240")
    run = _run(register, at=_at(1))
    # Source authority: the added commencement is added; the cancelled one stays cancelled.
    assert _decisions(run, "2026_240")["commencements"].outcome == SyncOutcome.APPLIED
    assert MatterImportantDate.objects.filter(
        matter=_matter("2026_240"), date_value=dt.date(2099, 9, 1)
    ).exists()
    assert MatterImportantDate.objects.filter(
        matter=_matter("2026_240"), status=FactStatus.CANCELLED
    ).exists()


def test_a_check_after_a_night_that_created_teemad_reads_them(register):
    _new_topics(register["source"])
    night = _run(register, at=_at(0))
    assert night.counts["new_topics_created"] == 3, night.error
    check = lovable_sync.run_sync(trigger=SyncTrigger.CHECK, now=lambda: _at(1))
    assert check.status == SyncRunStatus.SUCCEEDED, check.error
    assert check.counts["missing"] == 0 and check.counts["checked"] == 8


def test_the_onenote_lane_answers_only_for_its_own_request(register, monkeypatch):
    seen: list[str] = []

    def waiting(lane: str = "lovable") -> bool:
        seen.append(lane)
        return lane == "onenote"

    monkeypatch.setattr(lovable_sync, "pending_sync_request", waiting)
    assert lovable_sync.due_on_demand("onenote") == "DUE ONENOTE"
    assert lovable_sync.due_on_demand("lovable") == "NOT_DUE NO_REQUEST"
    assert seen == ["onenote", "lovable"]
