# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""The live synchronisation engine (docs/adr/0213), on synthetic data.

The world is a small `lovable-register` cohort built the way production's was:
a synthetic package applied through `lovable_pilot`, its provenance relabelled to
the register, enrolled by the replacement's enrolment. Lovable is a fake
transport serving invented rows. The 09.11 cases are reproduced by their shape:

* 2026_238 — the next step changes from preparing the opinion to waiting for a
  Commission proposal → REVIEW, nothing applied, pending every night;
* 2026_232 — the next step changes to waiting for the government's approval →
  REVIEW;
* 2026_253 — the opinion step is replaced by a later review and the deadline
  cleared → REVIEW, the request and its history untouched;
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
    topic = _topic(reference, **{"deadline": {"treatment": "NONE"}, **overrides})
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
        "2026_238": _row("2026_238", jargmiseks="Koostan arvamust.", arvamuse_tahtaeg="2099-03-01"),
        "2026_232": _row(
            "2026_232",
            jargmiseks="Saadan arvamuse ministeeriumile",
            tegevuse_tahtaeg="2099-02-01",
        ),
        "2026_253": _row("2026_253", jargmiseks="Koostan arvamust.", arvamuse_tahtaeg="2099-03-15"),
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
    automatic = [name for name, p in FIELD_POLICY.items() if p.policy == lovable_sync.AUTO]
    conditional = {name for name, p in FIELD_POLICY.items() if p.policy == lovable_sync.CONDITIONAL}
    assert automatic == list(AUTO_FIELDS)
    assert conditional == {*CONDITIONAL_FIELDS, NEW_TOPIC}
    for name in ("next_action_text", "sent", "sent_date", "addressee", "koda_initiative"):
        assert FIELD_POLICY[name].policy == lovable_sync.REVIEW
    assert FIELD_POLICY[CONTINUATION].policy == lovable_sync.REVIEW
    assert all(
        p.explanation and p.policy in lovable_sync.POLICY_CLASSES for p in FIELD_POLICY.values()
    )


# ---------------------------------------------------------------------------
# The 09.11 cases
# ---------------------------------------------------------------------------


def test_238_preparation_to_waiting_for_a_commission_proposal_is_held_and_persists(register):
    source = register["source"]
    source.edit(
        "2026_238",
        jargmiseks="Ootan Euroopa Komisjoni ettepanekut (avaldatakse 15.12.2026)",
        tegevuse_tahtaeg="2099-12-15",
    )
    step_before = list(
        NextAction.objects.filter(matter=_matter("2026_238")).values_list("pk", "status")
    )
    events = ChangeEvent.objects.filter(matter=_matter("2026_238")).count()
    first = _run(register, at=_at(0))
    assert first.status == SyncRunStatus.SUCCEEDED, first.error
    found = _decisions(first, "2026_238")
    assert (found["next_action_text"].outcome, found["next_action_text"].reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.TEXT_CHANGED,
    )
    assert found["next_action_date"].outcome == SyncOutcome.REVIEW_REQUIRED
    assert (
        list(NextAction.objects.filter(matter=_matter("2026_238")).values_list("pk", "status"))
        == step_before
    )
    assert ChangeEvent.objects.filter(matter=_matter("2026_238")).count() == events
    assert not Submission.objects.exists()

    second = _run(register, at=_at(24))
    assert _decisions(second, "2026_238")["next_action_text"].outcome == SyncOutcome.REVIEW_REQUIRED
    pending = {
        (p.reference, p.field): p for p in lovable_sync.pending_differences() if p.kind == "FIELD"
    }
    held = pending[("2026_238", "next_action_text")]
    assert held.since == first.started_at
    assert held.baseline == "Koostan arvamust."
    assert held.source.startswith("Ootan Euroopa Komisjoni")


def test_232_opinion_action_to_waiting_for_government_approval_is_held(register):
    register["source"].edit("2026_232", jargmiseks="Ootan Vabariigi Valitsuse heakskiitu")
    run = _run(register, at=_at(0))
    found = _decisions(run, "2026_232")
    assert found["next_action_text"].outcome == SyncOutcome.REVIEW_REQUIRED
    step = NextAction.objects.get(matter=_matter("2026_232"), status=ActionStatus.OPEN)
    assert step.text == "Saadan arvamuse ministeeriumile" and step.kind == "DO"
    assert not run.decisions.filter(outcome=SyncOutcome.APPLIED).exists()


def test_253_a_later_review_replacing_the_opinion_action_keeps_the_request_history(register):
    matter = _matter("2026_253")
    history = MatterResponseDeadline.objects.filter(matter=matter).count()
    register["source"].edit(
        "2026_253",
        jargmiseks="Vaatan üle pärast kooskõlastusringi",
        tegevuse_tahtaeg="2099-04-01",
        arvamuse_tahtaeg=None,
    )
    run = _run(register, at=_at(0))
    found = _decisions(run, "2026_253")
    assert found["next_action_text"].outcome == SyncOutcome.REVIEW_REQUIRED
    assert (found["opinion_deadline"].outcome, found["opinion_deadline"].reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.REQUEST_CLEARED,
    )
    matter.refresh_from_db()
    assert matter.response_deadline == dt.date(2099, 3, 15)
    assert MatterResponseDeadline.objects.filter(matter=matter).count() == history
    step = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert step.text == OPINION_PREPARATION_TEXT


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
    assert free_text.is_open and not NextAction.objects.filter(matter=free_text).exists()
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
    held = LovableSyncState.objects.get(matter=free_text).applied
    assert held["next_action_text"].get("import_held") is True
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


def test_a_conflict_overwrites_nothing(register):
    from app.matters.services import set_matter_title

    set_matter_title(matter=_matter("2026_240"), value="Juristi pealkiri", actor=None)
    register["source"].edit("2026_240", teema="Lovable'i uus pealkiri")
    run = _run(register, at=_at(0))
    assert _decisions(run, "2026_240")["title"].outcome == SyncOutcome.CONFLICT
    assert _matter("2026_240").title == "Juristi pealkiri"


def test_a_local_change_is_kept_and_a_matching_one_converges(register):
    from app.matters.services import change_stage
    from app.workflow.models import StageVocabulary

    matter = _matter("2026_240")
    change_stage(matter=matter, stage=StageVocabulary.objects.get(key="government"), actor=None)
    run = _run(register, at=_at(0))
    assert "stage" not in _decisions(run, "2026_240")  # the source did not move
    register["source"].edit("2026_240", hetkeseis="valitsuses")
    run = _run(register, at=_at(1))
    decision = _decisions(run, "2026_240")["stage"]
    assert decision.outcome == SyncOutcome.ALREADY_CONVERGED
    state = LovableSyncState.objects.get(matter=matter)
    assert state.applied["stage"] == {"source": "valitsuses", "target": "government"}


def test_a_stage_that_does_not_end_the_work_is_applied(register):
    register["source"].edit("2026_240", hetkeseis="valitsuses")
    run = _run(register, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert _matter("2026_240").stage.key == "government"
    applied = run.decisions.get(state__reference="2026_240", field="stage")
    assert applied.outcome == SyncOutcome.APPLIED and applied.idempotency_key


def test_a_terminal_stage_is_review_and_never_closes(register):
    register["source"].edit("2026_240", hetkeseis="jõustunud")
    run = _run(register, at=_at(0))
    decision = _decisions(run, "2026_240")["stage"]
    assert (decision.outcome, decision.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.STAGE_ENDS_WORK,
    )
    matter = _matter("2026_240")
    assert matter.is_open and matter.stage.key == "parliament"


def test_an_unmapped_stage_is_review(register):
    register["source"].edit("2026_240", hetkeseis="Riigikogus menetluses")
    run = _run(register, at=_at(0))
    assert _decisions(run, "2026_240")["stage"].reason == lovable_sync.HELD


def test_a_new_opinion_deadline_opens_a_request_only_where_none_is_open(register):
    register["source"].edit("2026_240", arvamuse_tahtaeg="2099-06-01")
    register["source"].edit("2026_238", arvamuse_tahtaeg="2099-06-01")
    run = _run(register, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert _matter("2026_240").response_deadline == dt.date(2099, 6, 1)
    held = _decisions(run, "2026_238")["opinion_deadline"]
    assert (held.outcome, held.reason) == (SyncOutcome.REVIEW_REQUIRED, lovable_sync.REQUEST_OPEN)
    assert _matter("2026_238").response_deadline == dt.date(2099, 3, 1)


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ({"valja": "ei saatnud"}, "REQUEST_SENT_IN_SOURCE"),
        ({"valja_kuupaev": "2026-11-01"}, "REQUEST_SENT_IN_SOURCE"),
        ({"koja_ettepanek": True}, "REQUEST_ON_INITIATIVE"),
    ],
)
def test_a_new_deadline_with_anything_sent_or_on_an_initiative_is_review(register, values, reason):
    register["source"].edit("2026_240", arvamuse_tahtaeg="2099-06-01", **values)
    run = _run(register, at=_at(0))
    decision = _decisions(run, "2026_240")["opinion_deadline"]
    assert (decision.outcome, decision.reason) == (SyncOutcome.REVIEW_REQUIRED, reason)
    assert _matter("2026_240").response_deadline is None
    assert not Submission.objects.exists()


def test_a_past_opinion_deadline_is_never_an_obligation_or_a_send(register):
    register["source"].edit("2026_240", arvamuse_tahtaeg="2026-10-01")
    run = _run(register, at=_at(0))
    decision = _decisions(run, "2026_240")["opinion_deadline"]
    assert (decision.outcome, decision.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.DATE_PAST,
    )
    assert _matter("2026_240").response_deadline is None
    assert not Submission.objects.exists()


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
    assert (decision.outcome, decision.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.HELD,
    )
    assert dates.count() == 1


def test_a_new_continuation_is_review_and_closes_nothing(register):
    source = register["source"]
    _run(register, at=_at(0))  # the continuation's baseline starts here
    source.links.append({"prev_topic_id": _id("2026_240"), "next_topic_id": _id("2026_232")})
    source.edit("2026_240")
    run = _run(register, at=_at(1))
    decision = _decisions(run, "2026_240")[CONTINUATION]
    assert (decision.outcome, decision.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.CONTINUATION_CHANGED,
    )
    matter = _matter("2026_240")
    assert matter.is_open and matter.superseded_by_id is None


def test_future_dated_narrative_is_not_a_past_event(register):
    register["source"].edit(
        "2026_232",
        jargmiseks="Arvamus saadeti 20.12.2026; ootame vastust",
        valja="",
    )
    events = ChangeEvent.objects.count()
    run = _run(register, at=_at(0))
    assert _decisions(run, "2026_232")["next_action_text"].outcome == SyncOutcome.REVIEW_REQUIRED
    assert ChangeEvent.objects.count() == events
    assert not Submission.objects.exists()


def test_a_closed_teema_takes_no_change_and_a_vanished_one_is_not_deleted(register):
    register["source"].edit("2026_239", teema="Uus pealkiri", hetkeseis="valitsuses")
    run = _run(register, at=_at(0))
    found = _decisions(run, "2026_239")
    assert found["title"].reason == lovable_sync.MATTER_CLOSED
    assert found["stage"].reason == lovable_sync.MATTER_CLOSED
    del register["source"].rows[_id("2026_239")]
    gone = _run(register, at=_at(1))
    assert _decisions(gone, "2026_239")["*"].outcome == SyncOutcome.SOURCE_MISSING
    matter = _matter("2026_239")
    assert matter.deleted_at is None and matter.title == "Näidisteema 2026_239"


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
    register["source"].edit("2026_232", jargmiseks="Ootan Vabariigi Valitsuse heakskiitu")
    _run(register, at=_at(0))
    assert ("2026_232", "next_action_text") in {
        (p.reference, p.field) for p in lovable_sync.pending_differences()
    }
    admin = factories.UserFactory(display_name="Haldur Näidis")
    decision = lovable_sync.resolve_field(
        reference="2026_232",
        field_name="next_action_text",
        note="Jääb Juristide tegevus",
        actor=admin,
    )
    assert decision.actor == admin and decision.reason.startswith(lovable_sync.RESOLVED_PREFIX)
    assert ("2026_232", "next_action_text") not in {
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
    lovable_sync.set_mode(SyncMode.COMPARE, reason="test")
    _run(register, at=_at(0))
    counts = (
        LovableSyncRun.objects.count(),
        LovableSyncDecision.objects.count(),
        ChangeEvent.objects.count(),
    )
    applied = {s.pk: json.dumps(s.applied, sort_keys=True) for s in LovableSyncState.objects.all()}
    result = lovable_sync.replay(today=_at(0).date())
    assert ("next_action_text", SyncOutcome.REVIEW_REQUIRED, lovable_sync.TEXT_CHANGED) in [
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
    monkeypatch.setattr(lovable_sync, "pending_sync_request", lambda: True)
    assert lovable_sync.due_on_demand() == "DUE APPLY"
    lovable_sync.set_mode(SyncMode.OFF, reason="test")
    assert lovable_sync.due_on_demand() == "DUE OFF"
    monkeypatch.setenv(lovable_sync.HOST_BACKUP_SET_ENV, "sets/20261110T000000Z")
    assert lovable_sync.host_backup_set() == "sets/20261110T000000Z"


# ---------------------------------------------------------------------------
# The historical cohort
# ---------------------------------------------------------------------------


def test_a_historical_cohort_is_enrollable_and_its_teemad_only_ever_review(register):
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
    assert {found["title"].reason, found["stage"].reason} == {lovable_sync.MATTER_CLOSED}
    assert _decisions(run, "2026_102")["*"].outcome == SyncOutcome.SOURCE_MISSING
    gone = _matter("2026_102")
    assert gone.deleted_at is None and not gone.is_open
    assert _matter("2026_101").title == "Näidisteema 2026_101"
    assert run.counts["by_cohort"][lovable_sync.HISTORY_COHORT]["checked"] == 2
    # A historical topic is not operational: never a new-topic candidate.
    assert run.counts["new_topics_seen"] == 0
