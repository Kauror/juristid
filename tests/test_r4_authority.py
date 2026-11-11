# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""Lovable is authoritative (docs/adr/0213 §10, owner brief 2026-11-11), on synthetic data.

The nine reference topics of 11.11 are reproduced by their shape — the source's
stage, next step, action day, opinion deadline and sent status — in a small
`lovable-register` cohort built the way production's was (a synthetic package
applied through `lovable_pilot`, enrolled as the register). Lovable is a fake
transport serving invented rows. A request or a drafting step that the import
wrote lies in 2099, because the importer refuses one in the past; the sent days
and action days are the reference days, read against a run on 12.11.2026.

Every title, name and link is invented.
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

import pytest

from app.audit.models import ChangeEvent
from app.legacy_import import lovable_sync
from app.legacy_import.lovable_pilot import PILOT, REGISTER_COHORT, build_plan
from app.legacy_import.lovable_reading import step_kind, step_reading
from app.legacy_import.lovable_sync import CONTINUATION, FIELD_POLICY
from app.legacy_import.lovable_sync_labels import LOVABLE_REASONS
from app.legacy_import.lovable_sync_models import (
    DOCUMENT_NOT_YET_AVAILABLE,
    LovableOpinionOutcome,
    LovableSyncDecision,
    LovableSyncState,
    OpinionOutcome,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
    current_opinion_outcomes,
)
from app.legacy_import.models import EvidencePilotRecord
from app.matters.enums import ResponseDeadlineOutcome
from app.matters.models import Matter, MatterResponseDeadline
from app.submissions.models import Submission
from app.workflow.enums import ActionKind, ActionStatus, Disposition, Track
from app.workflow.models import NextAction
from tests.test_lovable_pilot import _apply, _package, pilot_on, world  # noqa: F401
from tests.test_lovable_sync import _decisions, _matter, _run
from tests.test_ls_b_engine import Source, _drafting, _id, _register_topic, _row, _step

pytestmark = pytest.mark.django_db

TALLINN = lovable_sync.TALLINN
#: The night after the reference reading (11.11 04:04Z).
NIGHT = dt.datetime(2026, 11, 12, 0, 15, tzinfo=TALLINN)
HOUR = dt.timedelta(hours=1)
REQUEST = "2099-11-04"  # the import's open request / drafting day (see the docstring)


def _at(n: int) -> dt.datetime:
    return NIGHT + n * HOUR


def _held_step() -> dict[str, str]:
    return {
        "jargmiseks": "HELD_FOR_REVIEW",
        "tegevuse_tahtaeg": "HELD_FOR_REVIEW",
        "valja": "PRESERVED_AS_SOURCE_ONLY",
    }


def _drafting_topic(reference: str, status: str, stage: str) -> dict[str, Any]:
    row = _row(
        reference, hetkeseis=status, jargmiseks="Koostan arvamust.", arvamuse_tahtaeg=REQUEST
    )
    return _register_topic(
        reference,
        row,
        status_raw=status,
        stage=stage,
        next_action=_drafting(REQUEST),
        deadline={"treatment": "OPEN_REQUEST", "deadline": REQUEST},
    )


@pytest.fixture
def reference(world, pilot_on):
    """The reference topics as Juristid held them on 11.11, before tonight's run."""
    topics = [
        # Open in Juristid, «Riigikogus»; the source step was held at import.
        _register_topic(
            "2025_166",
            _row("2025_166", jargmiseks="Jõustumisel.", tegevuse_tahtaeg="2026-11-01"),
            next_action=None,
            field_decisions=_held_step(),
        ),
        _register_topic(
            "2026_150",
            _row("2026_150", jargmiseks="Ootan jõustumist", tegevuse_tahtaeg="2099-10-26"),
            next_action=_step("WAIT", "Ootan jõustumist", "2099-10-26"),
        ),
        _register_topic(
            "2026_140",
            _row("2026_140", jargmiseks="Ootan II lugemist", tegevuse_tahtaeg="2099-06-01"),
            next_action=_step("WAIT", "Ootan II lugemist", "2099-06-01"),
        ),
        _drafting_topic("2026_216", "ELi menetluses", "eu_procedure"),
        _drafting_topic("2026_225", "Riigikogus", "parliament"),
        _drafting_topic("2026_232", "kooskõlastusringil", "consultation"),
        _drafting_topic("2026_238", "Eesti seisukoht koostamisel", "estonian_eu_position"),
        _drafting_topic("2026_253", "Riigikogus", "parliament"),
    ]
    package = _package(*topics)
    package["snapshot"]["highest_reference"] = {"2025": 200, "2026": 260}
    package["reserve_through"] = {"2025": 200, "2026": 260}
    digest = build_plan(package).digest
    _apply(package)
    EvidencePilotRecord.objects.filter(package_sha256=digest).update(pilot=REGISTER_COHORT)
    lovable_sync.enroll(
        cohort=REGISTER_COHORT,
        package_sha256=digest,
        expect=len(topics),
        reason="Lovable'i register (r4 testmaailm)",
        replaces=[PILOT],
    )
    lovable_sync.set_mode(SyncMode.APPLY, reason="test")
    source = Source()
    for topic in topics:
        source.put(topic["source"]["id"], copy.deepcopy(topic["source"]["row"]), version=3)
    return {"source": source, "world": world, "topics": topics}


def _lovable_now(source: Source, *, with_259: bool = True) -> None:
    """What Lovable said at 11.11 04:04Z (BRIEF.md, reference data)."""
    source.edit(
        "2025_166",
        hetkeseis="jõustunud",
        jargmiseks="Jõustumisel.",
        tegevuse_tahtaeg="2026-11-01",
        arvamuse_tahtaeg="2025-09-26",
        valja="2025-10-20",
        valja_kuupaev="2025-10-20",
    )
    source.edit(
        "2026_150",
        hetkeseis="jõustunud",
        jargmiseks="Jõustub 26.10.2026",
        tegevuse_tahtaeg="2026-10-26",
        arvamuse_tahtaeg="2026-06-17",
        valja="2026-06-17",
        valja_kuupaev="2026-06-17",
    )
    source.edit(
        "2026_140",
        hetkeseis="rohkem ei tegele",
        jargmiseks="Jätkub teemana 2026_259.",
        tegevuse_tahtaeg=None,
        arvamuse_tahtaeg="2026-06-02",
        valja="2026-06-03",
        valja_kuupaev="2026-06-03",
    )
    source.edit(
        "2026_216",
        jargmiseks="Jälgin, kuidas teema liigub edasi EL tasemel",
        tegevuse_tahtaeg=None,
        valja="ei saatnud",
    )
    source.edit(
        "2026_225",
        jargmiseks="Ootan II lugemist Riigikogus, vaata teema üle 10.11.2026",
        tegevuse_tahtaeg="2026-11-10",
        valja="2026-11-04",
        valja_kuupaev="2026-11-04",
        koja_ettepanek=True,
    )
    source.edit(
        "2026_232",
        jargmiseks="Ootan, et valitsus kiidaks heaks. Kooskõlastusring lõppes 04.11.",
        tegevuse_tahtaeg="2026-11-27",
        valja="ei saatnud",
    )
    source.edit(
        "2026_238",
        jargmiseks=(
            "Ootan Euroopa Komisjoni õigusakti ettepanekut. Konsultatsioon lõppes 10.11.2026."
        ),
        tegevuse_tahtaeg="2027-01-20",
        valja="ei saatnud",
    )
    source.edit(
        "2026_253",
        jargmiseks="Vaata, mis on eelnõust edasi saanud. Riigikogu ootas ettepanekuid 09.11.",
        tegevuse_tahtaeg="2026-11-23",
        valja="ei saatnud",
    )
    source.links.append({"prev_topic_id": _id("2026_140"), "next_topic_id": _id("2026_259")})
    if with_259:
        source.put(
            _id("2026_259"),
            _row(
                "2026_259",
                jargmiseks="Jälgin eelnõu menetlust. Arvamuse saatsin 10.11.",
                tegevuse_tahtaeg="2026-11-24",
                arvamuse_tahtaeg="2026-11-10",
                valja="2026-11-10",
                valja_kuupaev="2026-11-10",
            ),
            version=1,
        )


def _open_step(reference: str) -> NextAction | None:
    return NextAction.objects.filter(matter=_matter(reference), status=ActionStatus.OPEN).first()


def _outcome(reference: str) -> LovableOpinionOutcome | None:
    matter = _matter(reference)
    return current_opinion_outcomes([matter.pk]).get(matter.pk)


# ---------------------------------------------------------------------------
# The step rule (no database)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Koostan arvamust.", "DO"),
        ("Kirjutan Koja seisukoha", "DO"),
        ("Jälgin, kuidas teema liigub edasi EL tasemel", "MONITOR"),
        ("Ootan II lugemist Riigikogus, vaata teema üle 10.11.2026", "WAIT"),
        ("Ootan, et valitsus kiidaks heaks. Kooskõlastusring lõppes 04.11.", "WAIT"),
        ("Vaata, mis on eelnõust edasi saanud. Riigikogu ootas ettepanekuid 09.11.", "MONITOR"),
        ("Jälgin eelnõu menetlust. Arvamuse saatsin 10.11.", "MONITOR"),
        ("«Ootan» ministeeriumi vastust", "WAIT"),
        # No kind verb first, or two kinds: the sentence is kept, no kind invented.
        ("Jõustumisel.", "SOURCE"),
        ("Jätkub teemana 2026_259.", "SOURCE"),
        ("Koostan arvamuse ja ootan ministeeriumi vastust", "SOURCE"),
        ("Oodatavasti tuleb eelnõu kevadel", "SOURCE"),
        ("Vaatasin 06.08 üle", "SOURCE"),
        ("", "SOURCE"),
    ],
)
def test_the_step_kind_is_read_only_from_a_sentence_initial_verb(text, kind):
    assert step_kind(text) == kind


def test_a_step_reading_keeps_the_sentence_and_the_source_day():
    assert step_reading("  Ootan   II lugemist ", "2026-11-10") == {
        "text": "Ootan II lugemist",
        "kind": "WAIT",
        "date_semantics": "REVIEW_ON",
        "target_date": "2026-11-10",
        "date_precision": "EXACT",
    }
    assert step_reading("Koostan arvamuse", None)["date_semantics"] == "DEADLINE"
    assert step_reading("Jõustumisel.", "2026-11-01")["date_semantics"] == "REVIEW_ON"
    assert step_reading("", "2026-11-01") is None


def test_every_hold_code_is_technical_and_has_estonian_words():
    for code in lovable_sync.TECHNICAL_HOLDS:
        assert LOVABLE_REASONS.get(code), code
    for name in lovable_sync.AUTHORITATIVE_FIELDS:
        assert FIELD_POLICY[name].policy == lovable_sync.AUTHORITATIVE
    assert FIELD_POLICY[CONTINUATION].policy == lovable_sync.AUTHORITATIVE
    assert FIELD_POLICY["operational_scope"].policy == lovable_sync.NOT_REPRESENTABLE


# ---------------------------------------------------------------------------
# The nine reference topics
# ---------------------------------------------------------------------------


def test_the_reference_night_reproduces_lovable_and_holds_only_the_missing_successor(reference):
    source = reference["source"]
    _lovable_now(source)
    steps_before = {
        ref: list(NextAction.objects.filter(matter=_matter(ref)).values_list("pk", flat=True))
        for ref in ("2026_150", "2026_216", "2026_225")
    }
    run = _run(reference, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert run.counts["new_topics_created"] == 1

    # 2025_166 «jõustunud»: closed COMPLETED («Jõustus või töö lõppes»), sent 20.10.2025.
    m166 = _matter("2025_166")
    assert (m166.is_open, m166.disposition, m166.stage.key) == (
        False,
        Disposition.COMPLETED,
        "in_force",
    )
    closed = ChangeEvent.objects.get(matter=m166, event_type="MATTER_CLOSED")
    assert closed.payload["provenance"]["provenance_source"] == "LOVABLE_SYNC"
    assert closed.payload["provenance"]["sync_run"] == str(run.pk)
    assert closed.actor is None and _open_step("2025_166") is None
    assert (_outcome("2025_166").outcome, _outcome("2025_166").sent_on) == (
        OpinionOutcome.SENT,
        dt.date(2025, 10, 20),
    )

    # 2026_150 «jõustunud»: closed; its WAIT step cancelled with the closure, not completed.
    m150 = _matter("2026_150")
    assert (m150.is_open, m150.disposition) == (False, Disposition.COMPLETED)
    old = NextAction.objects.get(pk=steps_before["2026_150"][0])
    assert old.status == ActionStatus.CANCELLED
    assert not NextAction.objects.filter(matter=m150, status=ActionStatus.COMPLETED).exists()

    # 2026_140 → 2026_259: the successor was not in Juristid when 140 was read.
    found = _decisions(run, "2026_140")
    assert (found["stage"].outcome, found["stage"].reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.SUCCESSOR_MISSING,
    )
    assert found[CONTINUATION].reason == lovable_sync.SUCCESSOR_MISSING
    assert found["next_action_text"].reason == lovable_sync.LIFECYCLE_HELD
    assert _matter("2026_140").is_open

    # 2026_216 «ei saatnud» + «Jälgin …»: a MONITOR step with no day; the drafting
    # step superseded (kept in the history, never completed); the request ends
    # «otsustati mitte vastata».
    step = _open_step("2026_216")
    assert (step.kind, step.text, step.target_date, step.date_semantics) == (
        ActionKind.MONITOR,
        "Jälgin, kuidas teema liigub edasi EL tasemel",
        None,
        "REVIEW_ON",
    )
    drafting = NextAction.objects.get(pk=steps_before["2026_216"][0])
    assert (drafting.status, drafting.replaced_by_id) == (ActionStatus.SUPERSEDED, step.pk)
    assert _outcome("2026_216").outcome == OpinionOutcome.NOT_SENT
    m216 = _matter("2026_216")
    assert m216.response_deadline is None
    ended = MatterResponseDeadline.objects.get(matter=m216)
    assert ended.outcome == ResponseDeadlineOutcome.NOT_ANSWERING

    # 2026_225: WAIT on 10.11, sent 04.11 as a Chamber's own proposal, no document.
    step = _open_step("2026_225")
    assert (step.kind, step.target_date, step.date_semantics) == (
        ActionKind.WAIT,
        dt.date(2026, 11, 10),
        "REVIEW_ON",
    )
    m225 = _matter("2026_225")
    assert m225.track == Track.KODA_INITIATIVE
    sent = _outcome("2026_225")
    assert (sent.outcome, sent.sent_on, sent.koda_initiative) == (
        OpinionOutcome.SENT,
        dt.date(2026, 11, 4),
        True,
    )
    assert sent.document_available is False
    answered = MatterResponseDeadline.objects.get(matter=m225)
    assert answered.outcome == ResponseDeadlineOutcome.ANSWERED
    assert DOCUMENT_NOT_YET_AVAILABLE in answered.note and answered.submission_id is None
    assert not Submission.objects.exists()

    # 2026_232 / 2026_238 WAIT, 2026_253 MONITOR, each on Lovable's day.
    expected = {
        "2026_232": (ActionKind.WAIT, dt.date(2026, 11, 27)),
        "2026_238": (ActionKind.WAIT, dt.date(2027, 1, 20)),
        "2026_253": (ActionKind.MONITOR, dt.date(2026, 11, 23)),
    }
    for ref, (kind, day) in expected.items():
        step = _open_step(ref)
        assert (step.kind, step.target_date) == (kind, day), ref
        assert _outcome(ref).outcome == OpinionOutcome.NOT_SENT, ref
        assert NextAction.objects.filter(
            matter=_matter(ref), status=ActionStatus.SUPERSEDED, text="Koostan arvamuse"
        ).exists()

    # 2026_259, created tonight, converged the same night: MONITOR on 24.11, sent 10.11.
    m259 = _matter("2026_259")
    assert m259.is_open and m259.response_deadline is None
    step = _open_step("2026_259")
    assert (step.kind, step.text, step.target_date) == (
        ActionKind.MONITOR,
        "Jälgin eelnõu menetlust. Arvamuse saatsin 10.11.",
        dt.date(2026, 11, 24),
    )
    assert (_outcome("2026_259").outcome, _outcome("2026_259").sent_on) == (
        OpinionOutcome.SENT,
        dt.date(2026, 11, 10),
    )

    # Only the missing successor is pending, with its technical code.
    pending = {
        (p.reference, p.field): p.reason
        for p in lovable_sync.pending_differences()
        if p.kind == "FIELD"
    }
    assert set(pending.values()) <= {lovable_sync.SUCCESSOR_MISSING, lovable_sync.LIFECYCLE_HELD}
    assert {ref for ref, _f in pending} == {"2026_140"}

    # The next night: the successor exists, so 2026_140 closes as continuing under it.
    again = _run(reference, at=_at(24))
    assert again.status == SyncRunStatus.SUCCEEDED, again.error
    m140 = _matter("2026_140")
    assert (m140.is_open, m140.disposition, m140.superseded_by_id) == (
        False,
        Disposition.SUPERSEDED,
        m259.pk,
    )
    assert m140.stage.key == "monitoring_stopped"
    assert "2026_259" in ChangeEvent.objects.get(matter=m140, event_type="MATTER_CLOSED").summary
    assert _open_step("2026_140") is None
    assert _outcome("2026_140").sent_on == dt.date(2026, 6, 3)
    assert not [p for p in lovable_sync.pending_differences() if p.kind == "FIELD"]

    # And a third night writes nothing at all.
    events = ChangeEvent.objects.count()
    outcomes = LovableOpinionOutcome.objects.count()
    third = _run(reference, at=_at(48))
    assert third.status == SyncRunStatus.SUCCEEDED, third.error
    assert third.counts["updated"] == 0
    assert not third.decisions.filter(outcome=SyncOutcome.APPLIED).exists()
    assert ChangeEvent.objects.count() == events
    assert LovableOpinionOutcome.objects.count() == outcomes


def test_every_write_is_audited_with_its_run_and_source(reference):
    _lovable_now(reference["source"])
    run = _run(reference, at=_at(0))
    applied = LovableSyncDecision.objects.filter(run=run, outcome=SyncOutcome.APPLIED)
    assert applied.exists()
    assert all(d.idempotency_key for d in applied)
    m225 = _matter("2026_225")
    set_event = ChangeEvent.objects.filter(matter=m225, event_type="NEXT_ACTION_SET").latest(
        "occurred_at"
    )
    provenance = set_event.payload["provenance"]
    assert provenance["sync_run"] == str(run.pk)
    assert provenance["kind_basis"] == "lovable-step/1"
    assert provenance["source_reference"] == "2026_225"
    assert set_event.payload["replaced"]  # the drafting step it supersedes
    row = LovableOpinionOutcome.objects.get(matter=m225)
    assert (row.run_id, str(row.source_id), row.source_valja) == (
        run.pk,
        _id("2026_225"),
        "2026-11-04",
    )


def test_a_created_topic_from_before_converges_on_its_next_run(reference, monkeypatch):
    _lovable_now(reference["source"])
    monkeypatch.setattr(lovable_sync, "CONVERGE_NEW_TOPICS", False)
    _run(reference, at=_at(0))
    assert _open_step("2026_259") is None and _outcome("2026_259") is None
    monkeypatch.setattr(lovable_sync, "CONVERGE_NEW_TOPICS", True)
    run = _run(reference, at=_at(24))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert _open_step("2026_259").kind == ActionKind.MONITOR
    assert _outcome("2026_259").sent_on == dt.date(2026, 11, 10)


# ---------------------------------------------------------------------------
# Local divergence, idempotency, blank values
# ---------------------------------------------------------------------------


def test_a_source_change_overwrites_a_local_divergence(reference):
    from app.matters.services import set_matter_title
    from app.workflow.services import set_next_action

    m232 = _matter("2026_232")
    set_matter_title(matter=m232, value="Juristi oma pealkiri", actor=None)
    set_next_action(matter=m232, text="Helistan ministeeriumi", actor=None)
    reference["source"].edit(
        "2026_232",
        teema="Lovable'i uus pealkiri",
        jargmiseks="Ootan valitsuse heakskiitu",
        tegevuse_tahtaeg="2026-11-27",
    )
    run = _run(reference, at=_at(0))
    found = _decisions(run, "2026_232")
    assert found["title"].outcome == SyncOutcome.APPLIED
    assert found["next_action_text"].outcome == SyncOutcome.APPLIED
    m232.refresh_from_db()
    assert m232.title == "Lovable'i uus pealkiri"
    step = _open_step("2026_232")
    assert (step.text, step.kind) == ("Ootan valitsuse heakskiitu", ActionKind.WAIT)
    # The lawyer's step stays in the history.
    assert NextAction.objects.get(matter=m232, text="Helistan ministeeriumi").status == (
        ActionStatus.SUPERSEDED
    )


def test_a_local_value_is_kept_while_the_source_is_unchanged(reference):
    from app.matters.services import set_matter_title

    _run(reference, at=_at(0))
    set_matter_title(matter=_matter("2026_253"), value="Juristi oma pealkiri", actor=None)
    run = _run(reference, at=_at(1))
    assert _decisions(run, "2026_253")["title"].outcome == SyncOutcome.LOCAL_TEST_CHANGE
    assert _matter("2026_253").title == "Juristi oma pealkiri"
    # Kept, not waiting for anybody.
    assert not [p for p in lovable_sync.pending_differences() if p.reference == "2026_253"]


def test_an_unclassified_sentence_keeps_its_words_and_day_and_is_never_late(reference):
    reference["source"].edit(
        "2026_253", jargmiseks="Eelnõu arutelu komisjonis", tegevuse_tahtaeg="2026-11-01"
    )
    run = _run(reference, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    step = _open_step("2026_253")
    assert (step.kind, step.text, step.target_date, step.date_semantics) == (
        ActionKind.SOURCE,
        "Eelnõu arutelu komisjonis",
        dt.date(2026, 11, 1),
        "REVIEW_ON",
    )
    assert not step.is_overdue(dt.date(2026, 11, 12))
    assert step.is_due_for_review(dt.date(2026, 11, 12))
    assert not NextAction.objects.overdue(dt.date(2026, 11, 12)).filter(pk=step.pk).exists()


def test_a_cleared_sentence_ends_only_the_sources_own_step(reference):
    source = reference["source"]
    source.edit("2026_232", jargmiseks="Ootan valitsust", tegevuse_tahtaeg="2026-11-27")
    _run(reference, at=_at(0))
    source.edit("2026_232", jargmiseks="", tegevuse_tahtaeg=None)
    run = _run(reference, at=_at(1))
    assert _decisions(run, "2026_232")["next_action_text"].outcome == SyncOutcome.APPLIED
    assert _open_step("2026_232") is None
    cancelled = NextAction.objects.get(matter=_matter("2026_232"), text="Ootan valitsust")
    assert cancelled.status == ActionStatus.CANCELLED  # never COMPLETED


def test_a_moved_opinion_deadline_moves_the_same_request(reference):
    reference["source"].edit("2026_253", arvamuse_tahtaeg="2099-12-01")
    run = _run(reference, at=_at(0))
    assert _decisions(run, "2026_253")["opinion_deadline"].outcome == SyncOutcome.APPLIED
    matter = _matter("2026_253")
    assert matter.response_deadline == dt.date(2099, 12, 1)
    moved = MatterResponseDeadline.objects.get(matter=matter)
    assert (moved.outcome, moved.next_deadline) == (
        ResponseDeadlineOutcome.MOVED,
        dt.date(2099, 12, 1),
    )
    # One request, never a second ...
    assert MatterResponseDeadline.objects.filter(matter=matter).count() == 1
    # ... and the drafting step follows the request's new day (review P2-1),
    # so it never sits late on the old one.
    step = _open_step("2026_253")
    assert (step.kind, step.date_semantics, step.target_date) == (
        ActionKind.DO,
        "DEADLINE",
        dt.date(2099, 12, 1),
    )
    # Once mirrored, a second move moves the step again.
    reference["source"].edit("2026_253", arvamuse_tahtaeg="2099-12-15")
    again = _run(reference, at=_at(24))
    assert again.status == SyncRunStatus.SUCCEEDED, again.error
    assert _matter("2026_253").response_deadline == dt.date(2099, 12, 15)
    assert _open_step("2026_253").target_date == dt.date(2099, 12, 15)


def test_a_passed_deadline_is_never_sent(reference):
    reference["source"].edit("2026_253", arvamuse_tahtaeg="2026-10-01")
    _run(reference, at=_at(0))
    assert _outcome("2026_253") is None
    assert not Submission.objects.exists()


# ---------------------------------------------------------------------------
# Technical holds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "field", "code"),
    [
        ({"vastutaja": "Tundmatu Isik"}, "owner", lovable_sync.UNRESOLVED_OWNER),
        ({"hetkeseis": "Riigikogus menetluses"}, "stage", lovable_sync.STAGE_UNMAPPED),
        (
            {"valja": "ei saatnud", "valja_kuupaev": "2026-11-04"},
            "sent",
            lovable_sync.SENT_CONTRADICTORY,
        ),
        (
            {"valja": "2026-11-03", "valja_kuupaev": "2026-11-04"},
            "sent",
            lovable_sync.SENT_CONTRADICTORY,
        ),
        ({"valja_kuupaev": "2027-01-01"}, "sent", lovable_sync.SENT_FUTURE),
        ({"valja": "vist saadeti"}, "sent", lovable_sync.SENT_UNREADABLE),
        ({"teema": ""}, "title", lovable_sync.TITLE_EMPTY),
        ({"tegevuse_tahtaeg": "2026-11-30", "jargmiseks": ""}, "next_action_text", None),
        ({"operatiivne_ulatus": False}, "operational_scope", lovable_sync.SCOPE_NOT_REPRESENTABLE),
    ],
)
def test_technical_problems_are_held_with_a_code_and_write_nothing(reference, values, field, code):
    if code is None:
        code = lovable_sync.STEP_DATE_NO_TEXT
    matter = _matter("2026_253")
    events = ChangeEvent.objects.filter(matter=matter).count()
    reference["source"].edit("2026_253", **values)
    run = _run(reference, at=_at(0))
    decision = _decisions(run, "2026_253")[field]
    assert (decision.outcome, decision.reason) == (SyncOutcome.REVIEW_REQUIRED, code)
    assert code in lovable_sync.TECHNICAL_HOLDS
    assert ChangeEvent.objects.filter(matter=matter).count() == events
    pending = [p for p in lovable_sync.pending_differences() if p.reference == "2026_253"]
    assert {p.reason for p in pending} == {code}


def test_a_lovable_reopening_reopens_the_teema_at_the_mapped_stage(reference):
    source = reference["source"]
    source.edit("2026_253", hetkeseis="jõustunud")
    _run(reference, at=_at(0))
    assert not _matter("2026_253").is_open
    source.edit("2026_253", hetkeseis="valitsuses")
    run = _run(reference, at=_at(1))
    assert _decisions(run, "2026_253")["stage"].outcome == SyncOutcome.APPLIED
    matter = _matter("2026_253")
    assert (matter.is_open, matter.stage.key) == (True, "government")


def test_a_teema_closed_in_juristid_stays_closed_while_lovable_is_unchanged(reference):
    from app.matters.services import close_matter

    close_matter(
        matter=_matter("2026_253"),
        disposition=Disposition.OTHER,
        actor=None,
        follow_ups_confirmed=True,
    )
    reference["source"].edit("2026_253", jargmiseks="Ootan", tegevuse_tahtaeg="2026-11-30")
    run = _run(reference, at=_at(0))
    assert not _matter("2026_253").is_open
    found = _decisions(run, "2026_253")
    # The Teema is closed: Lovable's new step has nowhere current to go.
    assert found["next_action_text"].outcome == SyncOutcome.ALREADY_CONVERGED
    assert _open_step("2026_253") is None


# ---------------------------------------------------------------------------
# Breakers, history, the closed archive
# ---------------------------------------------------------------------------


def test_the_breaker_still_holds_a_night_over_its_limit_and_a_supervised_run_may_raise_it(
    reference, monkeypatch
):
    monkeypatch.setattr(lovable_sync, "MAX_CHANGED_MATTERS", 3)
    _lovable_now(reference["source"], with_259=False)
    events = ChangeEvent.objects.count()
    held = _run(reference, at=_at(0))
    assert (held.status, held.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.BREAKER)
    assert ChangeEvent.objects.count() == events
    assert not LovableOpinionOutcome.objects.exists()
    raised = _run(reference, at=_at(1), max_changed=50)
    assert raised.status == SyncRunStatus.SUCCEEDED, raised.error
    assert raised.counts["breaker_limit"] == 50
    assert not _matter("2025_166").is_open


def test_history_and_imported_documents_are_preserved(reference):
    from tests import factories

    m216 = _matter("2026_216")
    document = factories.DocumentFactory(matter=m216)
    steps = NextAction.objects.filter(matter=m216).count()
    _lovable_now(reference["source"])
    _run(reference, at=_at(0))
    # One new step; every earlier one stays, none deleted or completed.
    assert NextAction.objects.filter(matter=m216).count() == steps + 1
    assert not NextAction.objects.filter(matter=m216, status=ActionStatus.COMPLETED).exists()
    document.refresh_from_db()
    assert document.matter_id == m216.pk and document.removed_at is None
    assert EvidencePilotRecord.objects.filter(matter=m216).exists()


def test_the_closed_historical_register_keeps_its_rules(reference):
    from app.legacy_import.lovable_pilot import HISTORY_COHORT

    matter = _matter("2026_238")
    LovableSyncState.objects.filter(matter=matter).update(cohort=HISTORY_COHORT)
    state = LovableSyncState.objects.select_related("matter").get(matter=matter)
    assert not lovable_sync.is_authoritative(state)
    row = {**reference["source"].rows[_id("2026_238")], "hetkeseis": "jõustunud"}
    plan = lovable_sync.plan_matter(
        state,
        {"row": row, "commencements": [], "stage_links": []},
        resolver=lovable_sync.OwnerResolver([state]),
        today=NIGHT.date(),
    )
    stage = next(d for d in plan.decisions if d.field == "stage")
    # The rules of 0213 §4, unchanged: a stage that ends the work is a person's.
    assert (stage.outcome, stage.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.STAGE_ENDS_WORK,
    )


# ---------------------------------------------------------------------------
# Review of 11.11 (PR #614)
# ---------------------------------------------------------------------------


def test_a_teema_with_an_outcome_chain_can_be_deleted(reference):
    """P2-2: `previous` is SET_NULL, so the purge of a permanent deletion takes
    a Teema with several outcome rows."""
    from app.matters.deletion import delete_matter

    source = reference["source"]
    source.edit("2026_253", valja="2026-11-03", valja_kuupaev="2026-11-03")
    _run(reference, at=_at(0))
    source.edit("2026_253", valja="ei saatnud", valja_kuupaev=None)
    _run(reference, at=_at(1))
    matter = _matter("2026_253")
    rows = LovableOpinionOutcome.objects.filter(matter=matter)
    assert rows.count() == 2 and rows.filter(previous__isnull=False).count() == 1
    delete_matter(matter=matter, actor=None)
    assert not LovableOpinionOutcome.objects.filter(matter_id=matter.pk).exists()


def test_a_teema_the_sync_closed_reads_unchanged_the_next_night(reference):
    """Item 4: «jõustunud» closes the Teema and its step with it; Lovable's
    unchanged sentence is then never a local edit."""
    reference["source"].edit("2026_150", hetkeseis="jõustunud")
    first = _run(reference, at=_at(0))
    assert first.status == SyncRunStatus.SUCCEEDED, first.error
    assert not _matter("2026_150").is_open
    second = _run(reference, at=_at(24))
    assert not second.decisions.filter(outcome=SyncOutcome.LOCAL_TEST_CHANGE).exists()
    assert "next_action_text" not in _decisions(second, "2026_150")
    third = _run(reference, at=_at(48))
    assert not third.decisions.exists()


def test_a_teema_closed_already_settles_lovables_unchanged_sentence(reference):
    """Item 4, the shape seen on the clone: closed with a step baseline left over."""
    reference["source"].edit("2026_150", hetkeseis="jõustunud")
    _run(reference, at=_at(0))
    state = LovableSyncState.objects.get(reference="2026_150")
    assert state.applied["next_action"]["target"] is None
    # The shape a run before this fix left: the step baseline still the old step.
    applied = dict(state.applied)
    applied["next_action"] = {
        "target": {"id": "x", "text": "Ootan jõustumist", "kind": "WAIT"},
        "treatment": "SOURCE_NEXT_STEP",
    }
    LovableSyncState.objects.filter(pk=state.pk).update(applied=applied)
    second = _run(reference, at=_at(24))
    assert not second.decisions.filter(outcome=SyncOutcome.LOCAL_TEST_CHANGE).exists()
    assert (
        LovableSyncState.objects.get(reference="2026_150").applied["next_action"]["target"] is None
    )


def test_a_cleared_outcome_is_no_outcome_any_more(reference):
    """P3-3: Lovable clearing its sent day withdraws the outcome, and the request
    the synchronisation ended on that word no longer reads as one."""
    from app.matters.register_filters import opinion_outcome_recorded_q

    reader = reference["world"]["mari"]
    source = reference["source"]
    source.edit("2026_225", valja="2026-11-04", valja_kuupaev="2026-11-04")
    _run(reference, at=_at(0))
    matter = _matter("2026_225")

    def recorded() -> bool:
        return Matter.objects.filter(opinion_outcome_recorded_q(reader), pk=matter.pk).exists()

    assert recorded()
    source.edit("2026_225", valja="", valja_kuupaev=None)
    run = _run(reference, at=_at(1))
    assert _decisions(run, "2026_225")["sent"].outcome == SyncOutcome.APPLIED
    assert _outcome("2026_225") is None
    assert LovableOpinionOutcome.objects.filter(matter=matter, outcome="CLEARED").exists()
    assert not recorded()


def test_a_held_outcome_neither_sets_nor_moves_a_request(reference):
    """P3-8: a contradictory sent status is not «nothing sent»."""
    reference["source"].edit(
        "2026_253",
        arvamuse_tahtaeg="2099-12-01",
        valja="ei saatnud",
        valja_kuupaev="2026-11-04",
    )
    run = _run(reference, at=_at(0))
    decision = _decisions(run, "2026_253")["opinion_deadline"]
    assert (decision.outcome, decision.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.OUTCOME_HELD,
    )
    assert lovable_sync.OUTCOME_HELD in lovable_sync.TECHNICAL_HOLDS
    assert LOVABLE_REASONS[lovable_sync.OUTCOME_HELD]
    assert _matter("2026_253").response_deadline == dt.date(2099, 11, 4)


def test_closing_from_the_source_takes_the_lock_and_refuses_a_closed_teema(reference):
    """P3-9: the Matter's lock first; an already closed Teema is refused before
    its stage could move."""
    from app.core.errors import DomainError
    from app.matters.services import close_matter_from_source
    from app.workflow.models import StageVocabulary

    reference["source"].edit("2026_150", hetkeseis="jõustunud")
    _run(reference, at=_at(0))
    matter = _matter("2026_150")
    with pytest.raises(DomainError, match="juba suletud"):
        close_matter_from_source(
            matter=matter,
            disposition=Disposition.MONITORING_STOPPED,
            stage=StageVocabulary.objects.get(key="monitoring_stopped"),
        )
    matter.refresh_from_db()
    assert (matter.stage.key, matter.disposition) == ("in_force", Disposition.COMPLETED)


def test_an_mcp_change_of_a_source_step_keeps_its_review_day():
    """P3-2: a `change_current` of Lovable's unclassified step is never a KeyError."""
    from types import SimpleNamespace

    from app.mcp_writes.operations.actions import (
        ALLOWED_MEANINGS,
        SetNextAction,
        SetNextActionArguments,
    )

    assert ALLOWED_MEANINGS["SOURCE"] == ("REVIEW_ON",)
    current = SimpleNamespace(kind="SOURCE", date_semantics="REVIEW_ON")
    args = SetNextActionArguments.model_construct(date_meaning=None)
    assert SetNextAction()._semantics(args, current, "SOURCE", dated=True) == "REVIEW_ON"
