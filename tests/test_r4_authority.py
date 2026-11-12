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
from django.utils import timezone

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
    row = _row(reference, hetkeseis=status, jargmiseks="Koostan arvamuse", arvamuse_tahtaeg=REQUEST)
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


def test_the_reference_night_reproduces_lovable_and_its_new_successor_the_same_night(reference):
    """Regression: LS-01 (ST-007) — 2026_140 continues under 2026_259, which
    Lovable created tonight: 140 closes under it in the same run, not the next
    night; a rerun writes nothing."""
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

    # 2026_140 → 2026_259: the successor was not in Juristid when 140 was first
    # planned; it was created tonight, so 140 is planned again and closes now.
    found = _decisions(run, "2026_140")
    assert not {d.reason for d in found.values()} & {
        lovable_sync.SUCCESSOR_MISSING,
        lovable_sync.LIFECYCLE_HELD,
    }
    assert not _matter("2026_140").is_open

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

    # Nothing is pending: the successor converged the predecessor the same night.
    assert not [p for p in lovable_sync.pending_differences() if p.kind == "FIELD"]
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

    # And the next night writes nothing at all.
    events = ChangeEvent.objects.count()
    outcomes = LovableOpinionOutcome.objects.count()
    third = _run(reference, at=_at(24))
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


def test_a_local_title_is_restored_while_the_source_is_unchanged(reference):
    """Lovable is the source of truth at all times (owner decision 11.11)."""
    from app.matters.services import set_matter_title

    _run(reference, at=_at(0))
    set_matter_title(matter=_matter("2026_253"), value="Juristi oma pealkiri", actor=None)
    run = _run(reference, at=_at(1))
    title = _decisions(run, "2026_253")["title"]
    assert (title.outcome, title.reason) == (SyncOutcome.APPLIED, lovable_sync.RESTORED)
    assert _matter("2026_253").title == "Näidisteema 2026_253"
    assert LOVABLE_REASONS[lovable_sync.RESTORED]
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


def test_a_cleared_sentence_ends_the_step_or_leaves_the_structured_drafting_step(reference):
    source = reference["source"]
    source.edit("2026_232", jargmiseks="Ootan valitsust", tegevuse_tahtaeg="2026-11-27")
    _run(reference, at=_at(0))
    # Lovable clears the sentence; its request is still open and nothing sent:
    # the structured drafting step (docs/adr/0148 §10) is the current step.
    source.edit("2026_232", jargmiseks="", tegevuse_tahtaeg=None)
    run = _run(reference, at=_at(1))
    assert _decisions(run, "2026_232")["next_action_text"].outcome == SyncOutcome.APPLIED
    step = _open_step("2026_232")
    assert (step.text, step.kind, step.target_date) == (
        "Koostan arvamuse",
        "DO",
        dt.date(2099, 11, 4),
    )
    # With no request either, there is no current step at all.
    source.edit("2026_232", arvamuse_tahtaeg=None)
    _run(reference, at=_at(2))
    assert _open_step("2026_232") is None
    replaced = NextAction.objects.get(matter=_matter("2026_232"), text="Ootan valitsust")
    assert replaced.status == ActionStatus.SUPERSEDED  # never COMPLETED


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


def test_a_teema_closed_in_juristid_is_reopened_while_lovable_has_it_open(reference):
    from app.matters.services import close_matter

    _run(reference, at=_at(0))
    close_matter(
        matter=_matter("2026_253"),
        disposition=Disposition.OTHER,
        actor=None,
        follow_ups_confirmed=True,
    )
    run = _run(reference, at=_at(1))
    stage = _decisions(run, "2026_253")["stage"]
    assert (stage.outcome, stage.reason) == (SyncOutcome.APPLIED, lovable_sync.RESTORED)
    matter = _matter("2026_253")
    assert (matter.is_open, matter.stage.key) == (True, "parliament")
    # The step the closure ended is set again (a new step; the old one stays cancelled).
    assert _open_step("2026_253").text == "Koostan arvamuse"
    assert not NextAction.objects.filter(matter=matter, status=ActionStatus.COMPLETED).exists()


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


def test_a_tombstone_keeps_the_earlier_rules(reference):
    from django.utils import timezone

    matter = _matter("2026_238")
    Matter.all_objects.filter(pk=matter.pk).update(deleted_at=timezone.now())
    state = LovableSyncState.objects.select_related("matter").get(matter=matter)
    assert not lovable_sync.is_authoritative(state)


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


# ---------------------------------------------------------------------------
# Owner decision 11.11: Lovable is the source of truth at all times
# ---------------------------------------------------------------------------


def _parity(reference: str) -> dict[str, Any]:
    matter = _matter(reference)
    values = lovable_sync.full_values(matter)
    step = values["next_action"]
    return {
        "title": values["title"],
        "owner": values["owner"],
        "lifecycle": values["lifecycle"],
        "step": {k: v for k, v in (step or {}).items() if k != "id"},
        "request": values["opinion_deadline"],
        "koda": values["koda_initiative"],
        "received": values["received"],
        "legal_act": values["legal_act"],
        "commencements": sorted(map(tuple, values["commencements"]), key=str),
    }


def test_every_local_edit_of_a_source_owned_field_is_restored(reference):
    from app.intelligence.models import MatterImportantDate
    from app.intelligence.services import cancel_important_date
    from app.matters.models import MatterResponseDeadline as Request
    from app.matters.response_deadlines import change_response_deadline
    from app.matters.services import (
        assign_matter,
        change_stage,
        change_track,
        set_legal_instruments,
        set_matter_dates,
        set_matter_title,
    )
    from app.submissions.models import Submission
    from app.workflow.models import StageVocabulary
    from app.workflow.services import set_next_action
    from tests import factories

    source = reference["source"]
    source.commencements.append(
        {
            "id": "c253",
            "topic_id": _id("2026_253"),
            "liik": "Eeldatava kuupäevaga",
            "kuupaev": "2027-05-01",
            "tapsustus": "§ 3",
            "position": 1,
        }
    )
    source.edit("2026_253", oigusakt="seadus")
    _run(reference, at=_at(0))
    before = _parity("2026_253")
    matter = _matter("2026_253")
    # Juristid-only work on the same Teema: never touched.
    entry = factories.EntryFactory(matter=matter)
    document = factories.DocumentFactory(matter=matter)
    draft = factories.SubmissionFactory(matter=matter)
    own_date = factories.ImportantDateFactory(matter=matter)

    jaan = reference["world"]["jaan"]
    set_matter_title(matter=matter, value="Juristi oma pealkiri", actor=jaan)
    assign_matter(matter=_matter("2026_253"), owner=jaan, actor=jaan)
    change_stage(
        matter=_matter("2026_253"), stage=StageVocabulary.objects.get(key="government"), actor=jaan
    )
    set_next_action(matter=_matter("2026_253"), text="Helistan ministeeriumi", actor=jaan)
    change_response_deadline(
        matter=_matter("2026_253"),
        deadline=dt.date(2099, 12, 24),
        actor=jaan,
        change="MOVED",
    )
    change_track(matter=_matter("2026_253"), track=Track.KODA_INITIATIVE, actor=jaan)
    set_matter_dates(matter=_matter("2026_253"), received_date=dt.date(2026, 2, 2), actor=jaan)
    other = (
        Matter._meta.get_field("legal_instruments")
        .related_model._default_manager.exclude(key="seadus")
        .first()
    )
    set_legal_instruments(matter=_matter("2026_253"), legal_instruments=[other], actor=jaan)
    estimate = MatterImportantDate.objects.get(
        matter=matter, kind="ESTIMATED_COMMENCEMENT", status="ACTIVE"
    )
    cancel_important_date(record=estimate, actor=jaan, reason="kohalik")
    assert _parity("2026_253") != before

    run = _run(reference, at=_at(1))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    restored = {
        d.field
        for d in run.decisions.filter(
            state__reference="2026_253",
            outcome=SyncOutcome.APPLIED,
            reason=lovable_sync.RESTORED,
        )
    }
    assert {
        "title",
        "owner",
        "stage",
        "next_action_text",
        "opinion_deadline",
        "koda_initiative",
        "received",
        "legal_act",
        "commencements",
    } <= restored
    assert not run.decisions.filter(outcome=SyncOutcome.LOCAL_TEST_CHANGE).exists()
    assert _parity("2026_253") == before
    # The lawyer's step stays in the history, superseded, never completed.
    local = NextAction.objects.get(matter=matter, text="Helistan ministeeriumi")
    assert local.status == ActionStatus.SUPERSEDED
    # The moved request stays in the history as moved.
    assert Request.objects.filter(matter=matter, outcome="MOVED").exists()
    # Juristid-only records are untouched.
    for record in (entry, document, draft, own_date):
        fresh = type(record)._default_manager.get(pk=record.pk)
        assert fresh.matter_id == matter.pk
        assert getattr(fresh, "removed_at", None) is None
    assert MatterImportantDate.objects.get(pk=own_date.pk).status == "ACTIVE"
    assert Submission.objects.get(pk=draft.pk).status == draft.status

    events = ChangeEvent.objects.count()
    again = _run(reference, at=_at(2))
    assert not again.decisions.filter(outcome=SyncOutcome.APPLIED).exists()
    assert ChangeEvent.objects.count() == events


def test_a_lifecycle_changed_locally_is_put_back(reference):
    from app.matters.services import close_matter, reopen_matter_into_stage
    from app.workflow.models import StageVocabulary

    reference["source"].edit("2026_150", hetkeseis="jõustunud")
    _run(reference, at=_at(0))
    assert not _matter("2026_150").is_open
    # A lawyer reopens what Lovable closed, and closes what Lovable keeps open.
    reopen_matter_into_stage(
        matter=_matter("2026_150"), stage=StageVocabulary.objects.get(key="parliament")
    )
    close_matter(
        matter=_matter("2026_232"),
        disposition=Disposition.OTHER,
        actor=None,
        follow_ups_confirmed=True,
    )
    run = _run(reference, at=_at(1))
    for ref in ("2026_150", "2026_232"):
        stage = _decisions(run, ref)["stage"]
        assert (stage.outcome, stage.reason) == (SyncOutcome.APPLIED, lovable_sync.RESTORED), ref
    m150 = _matter("2026_150")
    assert (m150.is_open, m150.disposition) == (False, Disposition.COMPLETED)
    assert _matter("2026_232").is_open
    events = ChangeEvent.objects.count()
    third = _run(reference, at=_at(2))
    assert not third.decisions.filter(outcome=SyncOutcome.APPLIED).exists()
    assert ChangeEvent.objects.count() == events


def test_a_technical_hold_is_never_restored_over(reference):
    from app.matters.services import assign_matter

    reference["source"].edit("2026_253", vastutaja="Tundmatu Isik")
    _run(reference, at=_at(0))
    jaan = reference["world"]["jaan"]
    assign_matter(matter=_matter("2026_253"), owner=jaan, actor=jaan)
    run = _run(reference, at=_at(1))
    owner = _decisions(run, "2026_253")["owner"]
    assert (owner.outcome, owner.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.UNRESOLVED_OWNER,
    )
    assert _matter("2026_253").owner == jaan


# -- the historical register -----------------------------------------------------


def _history(reference: dict[str, Any], rows: dict[str, dict[str, Any]]) -> None:
    """Closed archive Teemad enrolled as `lovable-history`, the way the backfill
    wrote them (closed ARCHIVE, no step), with Lovable serving ``rows``."""
    from app.legacy_import.lovable_pilot import HISTORY_COHORT

    topics = [
        _register_topic(
            ref,
            _row(ref, **{"operatiivne_ulatus": False, "hetkeseis": "jõustunud", **values}),
            status_raw="jõustunud",
            stage="in_force",
            lifecycle="INACTIVE",
            disposition="COMPLETED",
            next_action=None,
        )
        for ref, values in rows.items()
    ]
    package = _package(*topics)
    package["snapshot"]["highest_reference"] = {"2025": 200, "2026": 260}
    package["reserve_through"] = {"2025": 200, "2026": 260}
    digest = build_plan(package).digest
    _apply(package)
    EvidencePilotRecord.objects.filter(package_sha256=digest).update(pilot=HISTORY_COHORT)
    lovable_sync.enroll(
        cohort=HISTORY_COHORT, package_sha256=digest, expect=len(topics), reason="Ajalugu"
    )
    for topic in topics:
        reference["source"].put(
            topic["source"]["id"], copy.deepcopy(topic["source"]["row"]), version=3
        )


def test_the_historical_register_follows_lovable_and_stays_closed_archive(reference):
    from app.legacy_import.lovable_pilot import HISTORY_COHORT
    from app.matters.enums import RecordMode

    _history(
        reference,
        {
            "2026_101": {"hetkeseis": "lõpetatud", "valja": "2019-05-02"},
            "2026_102": {"hetkeseis": "jõustunud", "jargmiseks": "Jõustus 2020"},
        },
    )
    first = _run(reference, at=_at(0))
    assert first.status == SyncRunStatus.SUCCEEDED, first.error
    # «lõpetatud» on a closed Teema is converged, never held; no reason invented.
    stage = _decisions(first, "2026_101").get("stage")
    assert stage is None or stage.outcome in (SyncOutcome.UNCHANGED, SyncOutcome.ALREADY_CONVERGED)
    assert not first.decisions.filter(
        state__cohort=HISTORY_COHORT, outcome=SyncOutcome.REVIEW_REQUIRED
    ).exists()
    # The sent status is mirrored on the archive Teema.
    assert _outcome("2026_101").sent_on == dt.date(2019, 5, 2)
    # A changed title is written; the Teema stays closed ARCHIVE with no step.
    reference["source"].edit("2026_102", teema="Ajaloolise teema uus pealkiri")
    second = _run(reference, at=_at(1))
    assert _decisions(second, "2026_102")["title"].outcome == SyncOutcome.APPLIED
    for ref in ("2026_101", "2026_102"):
        matter = _matter(ref)
        assert (matter.is_open, matter.record_mode) == (False, RecordMode.ARCHIVE), ref
        assert _open_step(ref) is None
    assert _matter("2026_102").title == "Ajaloolise teema uus pealkiri"
    third = _run(reference, at=_at(2))
    assert not third.decisions.filter(outcome=SyncOutcome.APPLIED).exists()
    assert not third.decisions.filter(outcome=SyncOutcome.LOCAL_TEST_CHANGE).exists()


def test_a_history_teema_lovable_reopens_inside_its_scope_joins_the_register(reference):
    from app.legacy_import.lovable_pilot import HISTORY_COHORT
    from app.legacy_import.lovable_sync_models import LovableSyncMembership, MembershipBasis
    from app.matters.enums import RecordMode

    _history(reference, {"2026_103": {}, "2026_104": {}})
    _run(reference, at=_at(0))
    sizes = lovable_sync.expected_cohorts()
    reference["source"].edit("2026_103", hetkeseis="Riigikogus", operatiivne_ulatus=True)
    reference["source"].edit("2026_104", hetkeseis="Riigikogus")  # still outside the scope
    run = _run(reference, at=_at(1))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    moved = _matter("2026_103")
    assert (moved.is_open, moved.stage.key, moved.record_mode) == (
        True,
        "parliament",
        RecordMode.FULL,
    )
    state = LovableSyncState.objects.get(matter=moved)
    assert state.cohort == REGISTER_COHORT
    assert (
        LovableSyncMembership.objects.get(matter=moved, cohort=REGISTER_COHORT).basis
        == MembershipBasis.MOVED
    )
    after = lovable_sync.expected_cohorts()
    assert after[REGISTER_COHORT] == sizes[REGISTER_COHORT] + 1
    assert after[HISTORY_COHORT] == sizes[HISTORY_COHORT] - 1
    outside = _decisions(run, "2026_104")["stage"]
    assert (outside.outcome, outside.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.HISTORY_REOPENED_OUTSIDE_SCOPE,
    )
    assert not _matter("2026_104").is_open
    assert LOVABLE_REASONS[lovable_sync.HISTORY_REOPENED_OUTSIDE_SCOPE]
    # The cohorts still add up: the next night runs.
    nxt = _run(reference, at=_at(2))
    assert nxt.status == SyncRunStatus.SUCCEEDED, nxt.error


# -- commencements --------------------------------------------------------------------


def _commencements(source: Source, ref: str, *rows: tuple[str, str | None, str]) -> None:
    source.commencements[:] = [c for c in source.commencements if c["topic_id"] != _id(ref)] + [
        {
            "id": f"{ref}-{n}",
            "topic_id": _id(ref),
            "liik": liik,
            "kuupaev": day,
            "tapsustus": note,
            "position": n,
        }
        for n, (liik, day, note) in enumerate(rows, 1)
    ]
    source.edit(ref)


def _active(ref: str) -> list[tuple[str, str | None, str]]:
    from app.intelligence.models import MatterEffectiveDate, MatterImportantDate

    matter = _matter(ref)
    effective = [
        (r.kind, r.date_value.isoformat() if r.date_value else None, r.description)
        for r in MatterEffectiveDate.objects.filter(matter=matter, status="ACTIVE")
    ]
    important = [
        (r.kind, r.date_value.isoformat(), r.note)
        for r in MatterImportantDate.objects.filter(
            matter=matter, status="ACTIVE", kind__endswith="COMMENCEMENT"
        )
    ]
    return sorted(effective + important, key=str)


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (  # 2026_148: an estimate becomes fixed on the same day, with a §-scope, plus 2028
            [("Eeldatava kuupäevaga", "2027-03-02", "")],
            [
                ("Kindla kuupäevaga jõustumine", "2027-03-02", "§ 1–4"),
                ("Kindla kuupäevaga jõustumine", "2028-01-01", ""),
            ],
            [
                ("PLANNED_COMMENCEMENT", "2027-03-02", "§ 1–4"),
                ("PLANNED_COMMENCEMENT", "2028-01-01", ""),
            ],
        ),
        (  # 2026_205: an estimate becomes fixed
            [("Eeldatava kuupäevaga", "2027-01-01", "")],
            [("Kindla kuupäevaga", "2027-01-01", "")],
            [("PLANNED_COMMENCEMENT", "2027-01-01", "")],
        ),
        (  # 2026_212: general order gets a fixed day
            [("Üldises korras", None, "")],
            [("Kindla kuupäevaga", "2026-11-14", "")],
            [("PLANNED_COMMENCEMENT", "2026-11-14", "")],
        ),
        (  # a fixed day moves
            [("Kindla kuupäevaga", "2027-01-01", "")],
            [("Kindla kuupäevaga", "2027-02-01", "")],
            [("PLANNED_COMMENCEMENT", "2027-02-01", "")],
        ),
        (  # removed
            [("Kindla kuupäevaga", "2027-01-01", "")],
            [],
            [],
        ),
    ],
)
def test_a_changed_or_removed_commencement_is_reconciled_with_its_history(
    reference, before, after, expected
):
    from app.intelligence.models import MatterEffectiveDate, MatterImportantDate

    source = reference["source"]
    _commencements(source, "2026_253", *before)
    first = _run(reference, at=_at(0))
    assert first.status == SyncRunStatus.SUCCEEDED, first.error
    matter = _matter("2026_253")
    earlier = list(MatterEffectiveDate.objects.filter(matter=matter)) + list(
        MatterImportantDate.objects.filter(matter=matter)
    )
    assert len(earlier) == len(before)
    _commencements(source, "2026_253", *after)
    run = _run(reference, at=_at(1))
    decision = _decisions(run, "2026_253")["commencements"]
    assert decision.outcome == SyncOutcome.APPLIED, decision.reason
    assert _active("2026_253") == sorted(expected, key=str)
    # The earlier one is kept, cancelled with its own event — never deleted.
    for record in earlier:
        fresh = type(record)._default_manager.get(pk=record.pk)
        assert fresh.status == "CANCELLED"
    assert ChangeEvent.objects.filter(
        matter=matter,
        event_type__in=("EFFECTIVE_DATE_CANCELLED", "IMPORTANT_DATE_CANCELLED"),
    ).count() == len(before)
    again = _run(reference, at=_at(2))
    assert not again.decisions.filter(outcome=SyncOutcome.APPLIED).exists()


# -- the 11.11 parity check (round 4b) ------------------------------------------------


def test_the_step_is_lovables_exact_sentence_with_the_kind_the_rule_reads(reference):
    """A: exact text, never an equivalence; B: the kind is re-derived from the
    unchanged sentence every run and a kind an earlier import gave is restored."""
    from app.workflow.models import NextAction as Step

    source = reference["source"]
    source.edit("2026_253", jargmiseks="Koostan arvamust. Arvamuse tähtaeg 11.11.2026")
    _run(reference, at=_at(0))
    step = _open_step("2026_253")
    assert (step.text, step.kind, step.target_date) == (
        "Koostan arvamust. Arvamuse tähtaeg 11.11.2026",
        ActionKind.DO,
        dt.date(2099, 11, 4),  # a DO with no day of its own: the request's day
    )
    source.edit("2026_232", jargmiseks="ooatn ministeeriumi vastust", tegevuse_tahtaeg="2026-11-30")
    _run(reference, at=_at(1))
    assert _open_step("2026_232").kind == ActionKind.WAIT
    # An earlier classification differing from the rule, with Lovable unchanged.
    Step.objects.filter(pk=_open_step("2026_232").pk).update(kind=ActionKind.MONITOR)
    run = _run(reference, at=_at(2))
    text = _decisions(run, "2026_232")["next_action_text"]
    assert (text.outcome, text.reason) == (SyncOutcome.APPLIED, lovable_sync.RESTORED)
    assert _open_step("2026_232").kind == ActionKind.WAIT
    assert Step.objects.filter(
        matter=_matter("2026_232"), kind=ActionKind.MONITOR, status=ActionStatus.SUPERSEDED
    ).exists()


def test_a_teema_closed_for_another_reason_takes_lovables_reason(reference):
    """C (2026_217): «rohkem ei tegele» in Lovable, NO_POSITION_FORMED in Juristid."""
    from app.matters.services import close_matter

    _run(reference, at=_at(0))
    matter = _matter("2026_238")
    close_matter(
        matter=matter,
        disposition=Disposition.NO_POSITION_FORMED,
        actor=None,
        follow_ups_confirmed=True,
    )
    closed_at = _matter("2026_238").closed_at
    reference["source"].edit("2026_238", hetkeseis="rohkem ei tegele")
    run = _run(reference, at=_at(1))
    assert _decisions(run, "2026_238")["stage"].outcome == SyncOutcome.APPLIED
    matter = _matter("2026_238")
    assert (matter.is_open, matter.disposition, matter.closed_at) == (
        False,
        Disposition.MONITORING_STOPPED,
        closed_at,
    )
    event = ChangeEvent.objects.get(matter=matter, event_type="MATTER_CLOSURE_REASON_CHANGED")
    assert event.payload["from"]["disposition"] == Disposition.NO_POSITION_FORMED
    assert event.payload["to"]["disposition"] == Disposition.MONITORING_STOPPED
    assert event.payload["provenance"]["sync_run"] == str(run.pk)
    again = _run(reference, at=_at(2))
    assert not again.decisions.filter(outcome=SyncOutcome.APPLIED).exists()


def test_history_owners_who_are_not_users_are_one_grouped_class(reference):
    """D: former staff on archive Teemad are the historical register's own group;
    a name that resolves to an active user is mirrored."""
    from app.legacy_import.lovable_pilot import HISTORY_COHORT

    _history(reference, {"2026_105": {}, "2026_106": {}})
    _run(reference, at=_at(0))
    reference["source"].edit("2026_105", vastutaja="Endine Töötaja")
    reference["source"].edit("2026_106", vastutaja="Jaan Näidis")
    run = _run(reference, at=_at(1))
    held = _decisions(run, "2026_105")["owner"]
    assert (held.outcome, held.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.HISTORY_OWNER_UNRESOLVED,
    )
    assert lovable_sync.historical_hold(HISTORY_COHORT, held.reason, held.baseline, held.source)
    assert LOVABLE_REASONS[lovable_sync.HISTORY_OWNER_UNRESOLVED]
    assert _matter("2026_106").owner == reference["world"]["jaan"]
    assert not _matter("2026_106").is_open


# -- round 4c ------------------------------------------------------------------------


def test_a_request_moved_onto_a_day_lovable_says_was_declined_ends_again(reference):
    """Clone defect (2026_238): «ei saatnud» for 10.11; a lawyer set a new request;
    the restore must end it as Lovable says, never leave it current."""
    from app.matters.response_deadlines import change_response_deadline

    source = reference["source"]
    source.edit("2026_238", valja="ei saatnud")
    _run(reference, at=_at(0))
    matter = _matter("2026_238")
    assert matter.response_deadline is None
    change_response_deadline(matter=matter, deadline=dt.date(2099, 12, 15), actor=None)
    run = _run(reference, at=_at(1))
    decision = _decisions(run, "2026_238")["opinion_deadline"]
    assert (decision.outcome, decision.reason) == (SyncOutcome.APPLIED, lovable_sync.RESTORED)
    matter.refresh_from_db()
    assert matter.response_deadline is None
    assert (
        MatterResponseDeadline.objects.filter(matter=matter, outcome="NOT_ANSWERING").count() == 2
    )
    again = _run(reference, at=_at(2))
    assert not again.decisions.filter(outcome=SyncOutcome.APPLIED).exists()


def test_a_history_teema_with_an_open_label_is_converged_until_lovable_changes(reference):
    """P1: the backfill closed «Riigikogus» archive topics; that is no reopening."""
    from app.matters.enums import RecordMode

    _history(reference, {"2026_107": {"hetkeseis": "Riigikogus"}})
    for night in (0, 1):
        run = _run(reference, at=_at(night))
        assert run.status == SyncRunStatus.SUCCEEDED, run.error
        assert not run.decisions.filter(
            state__reference="2026_107", outcome=SyncOutcome.REVIEW_REQUIRED
        ).exists(), night
        matter = _matter("2026_107")
        assert (matter.is_open, matter.record_mode) == (False, RecordMode.ARCHIVE)
    # Its scope becoming operational is a real change: it reopens.
    reference["source"].edit("2026_107", operatiivne_ulatus=True)
    run = _run(reference, at=_at(2))
    assert _decisions(run, "2026_107")["stage"].outcome == SyncOutcome.APPLIED
    assert _matter("2026_107").is_open


def test_the_blank_sentence_drafting_step_follows_the_readers_predicate(reference):
    """P2-2: a blank sentence is the drafting step only for an open, unsent,
    non-initiative request whose day has not passed; a held outcome holds it."""
    source = reference["source"]
    source.edit("2026_253", jargmiseks="", koja_ettepanek=True)
    _run(reference, at=_at(0))
    assert _open_step("2026_253") is None  # an initiative: no drafting step
    source.edit("2026_232", jargmiseks="", valja="ei saatnud", valja_kuupaev="2026-11-04")
    run = _run(reference, at=_at(1))
    step = _decisions(run, "2026_232")["next_action_text"]
    assert (step.outcome, step.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.OUTCOME_HELD,
    )


def test_a_persons_own_commencement_is_never_reconciled(reference):
    """P2-3: only commencements from Lovable (the import's provenance or the
    sync's `source_text` mark) are compared; a person's own stays."""
    from app.intelligence.models import MatterEffectiveDate
    from app.intelligence.services import add_effective_date

    matter = _matter("2026_253")
    own = add_effective_date(
        matter=matter,
        kind="KNOWN_DATE",
        date_value=dt.date(2030, 1, 1),
        period_end=dt.date(2030, 1, 1),
        actor=reference["world"]["mari"],
    )
    _commencements(reference["source"], "2026_253", ("Kindla kuupäevaga", "2027-01-01", "§ 2"))
    run = _run(reference, at=_at(0))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert MatterEffectiveDate.objects.get(pk=own.pk).status == "ACTIVE"
    added = lovable_sync.lovable_commencements(matter.pk)
    assert added == [["MatterImportantDate", "PLANNED_COMMENCEMENT", "2027-01-01", "§ 2"]]


def test_a_changed_scope_note_is_written_and_a_duplicate_row_added_once(reference):
    """P3-1: the note is part of the value; P3-2: a row stated twice is one."""
    source = reference["source"]
    _commencements(
        source,
        "2026_253",
        ("Kindla kuupäevaga", "2027-01-01", "§ 1"),
        ("Kindla kuupäevaga", "2027-01-01", "§ 1"),
    )
    _run(reference, at=_at(0))
    matter = _matter("2026_253")
    assert lovable_sync.lovable_commencements(matter.pk) == [
        ["MatterImportantDate", "PLANNED_COMMENCEMENT", "2027-01-01", "§ 1"]
    ]
    _commencements(source, "2026_253", ("Kindla kuupäevaga", "2027-01-01", "§ 1–3"))
    run = _run(reference, at=_at(1))
    assert _decisions(run, "2026_253")["commencements"].outcome == SyncOutcome.APPLIED
    assert lovable_sync.lovable_commencements(matter.pk) == [
        ["MatterImportantDate", "PLANNED_COMMENCEMENT", "2027-01-01", "§ 1–3"]
    ]
    again = _run(reference, at=_at(2))
    assert not again.decisions.filter(outcome=SyncOutcome.APPLIED).exists()


def test_restores_have_their_own_breaker_and_never_stop_source_changes(reference, monkeypatch):
    """P2-4: over `MAX_RESTORED_MATTERS` the restores wait; the source change is written."""
    from app.matters.services import set_matter_title

    _run(reference, at=_at(0))
    monkeypatch.setattr(lovable_sync, "MAX_RESTORED_MATTERS", 1)
    for ref in ("2026_232", "2026_238"):
        set_matter_title(matter=_matter(ref), value="Juristi oma", actor=None)
    reference["source"].edit("2026_253", teema="Uus pealkiri Lovableist")
    run = _run(reference, at=_at(1))
    assert (run.status, run.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.RESTORE_BREAKER)
    assert (run.counts["changed_source"], run.counts["changed_restores"]) == (1, 2)
    assert run.counts["restore_limit"] == 1
    assert _matter("2026_253").title == "Uus pealkiri Lovableist"
    assert _matter("2026_232").title == "Juristi oma"
    assert _decisions(run, "2026_232")["title"].outcome == SyncOutcome.ELIGIBLE
    assert LOVABLE_REASONS[lovable_sync.RESTORE_BREAKER]
    assert any("taastamisi: 2/1" in line for line in lovable_sync.summary_lines(run))


def test_closure_reason_change_refuses_a_tombstone_and_forgets_the_old_successor(
    reference, monkeypatch
):
    """P3-3."""
    from django.utils import timezone

    from app.matters import services

    _lovable_now(reference["source"])
    _run(reference, at=_at(0))
    _run(reference, at=_at(24))
    m140 = _matter("2026_140")
    forgotten: list[Any] = []
    monkeypatch.setattr(services, "forget_continuation_history", forgotten.append)
    services.change_closure_from_source(
        matter=m140, disposition=Disposition.MONITORING_STOPPED, reason="test"
    )
    assert _matter("2026_259").pk in {getattr(m, "pk", None) for m in forgotten}
    Matter.all_objects.filter(pk=m140.pk).update(deleted_at=timezone.now())
    with pytest.raises(Matter.DoesNotExist):
        services.change_closure_from_source(
            matter=m140, disposition=Disposition.COMPLETED, reason="test"
        )


def test_a_restored_closure_a_link_and_a_continuation(reference):
    """P3-7: a lawyer reopens a Teema Lovable continues elsewhere (re-closed with
    its successor), and a newer local OneNote link gives way to Lovable's."""
    from app.legacy_import.lovable_sync import _append_link_observation
    from app.matters.services import reopen_matter_into_stage
    from app.workflow.models import StageVocabulary

    _lovable_now(reference["source"])
    _run(reference, at=_at(0))
    _run(reference, at=_at(24))
    reopen_matter_into_stage(
        matter=_matter("2026_140"), stage=StageVocabulary.objects.get(key="parliament")
    )
    run_obj = LovableSyncDecision.objects.first().run
    _append_link_observation(
        matter=_matter("2026_253"),
        url="https://example.invalid/kohalik",
        run=run_obj,
        snapshot={"sha256": "0" * 64},
        provenance={},
    )
    run = _run(reference, at=_at(48))
    stage = _decisions(run, "2026_140")["stage"]
    assert (stage.outcome, stage.reason) == (SyncOutcome.APPLIED, lovable_sync.RESTORED)
    m140 = _matter("2026_140")
    assert (m140.is_open, m140.superseded_by_id) == (False, _matter("2026_259").pk)
    link = _decisions(run, "2026_253")["onenote_link"]
    assert (link.outcome, link.reason) == (SyncOutcome.APPLIED, lovable_sync.RESTORED)
    assert lovable_sync.current_values(_matter("2026_253"))["onenote_link"] != (
        "https://example.invalid/kohalik"
    )


def test_an_initiative_with_no_recorded_track_is_held_not_guessed(reference):
    """P3-7: Juristid says Chamber initiative with no recorded earlier track."""
    Matter.all_objects.filter(pk=_matter("2026_253").pk).update(track=Track.KODA_INITIATIVE)
    run = _run(reference, at=_at(0))
    koda = _decisions(run, "2026_253")["koda_initiative"]
    assert (koda.outcome, koda.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.TRACK_NOT_STATED,
    )


def test_a_moved_history_teema_deleted_afterwards_keeps_the_cohorts_whole(reference):
    """P3-7: moved to the register, then permanently deleted: both cohorts still add up."""
    from app.legacy_import.lovable_pilot import HISTORY_COHORT
    from app.matters.deletion import delete_matter

    _history(reference, {"2026_108": {}})
    _run(reference, at=_at(0))
    reference["source"].edit("2026_108", hetkeseis="Riigikogus", operatiivne_ulatus=True)
    _run(reference, at=_at(1))
    assert LovableSyncState.objects.get(reference="2026_108").cohort == REGISTER_COHORT
    sizes = lovable_sync.expected_cohorts()
    delete_matter(matter=_matter("2026_108"), actor=None)
    after = lovable_sync.expected_cohorts()
    assert after[REGISTER_COHORT] == sizes[REGISTER_COHORT] - 1
    assert after[HISTORY_COHORT] == sizes[HISTORY_COHORT]
    del reference["source"].rows[_id("2026_108")]
    run = _run(reference, at=_at(2))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error


# -- round 4d ------------------------------------------------------------------------


def test_a_teema_with_a_source_change_applies_its_restores_when_the_restore_breaker_trips(
    reference, monkeypatch
):
    """P1: a lawyer closed 2026_216 and Lovable changed its step: the reopening (a
    restore) is written with the step (the step needs the open Teema); only the
    restore-only Teema waits. `--max-restored` drains it on a supervised run."""
    from app.matters.services import close_matter, set_matter_title

    _run(reference, at=_at(0))
    close_matter(
        matter=_matter("2026_216"),
        disposition=Disposition.OTHER,
        actor=None,
        follow_ups_confirmed=True,
    )
    set_matter_title(matter=_matter("2026_232"), value="Juristi oma", actor=None)
    reference["source"].edit("2026_216", jargmiseks="Jälgin ELi menetlust")
    monkeypatch.setattr(lovable_sync, "MAX_RESTORED_MATTERS", 0)
    run = _run(reference, at=_at(1))
    assert (run.status, run.error) == (SyncRunStatus.REVIEW_ONLY, lovable_sync.RESTORE_BREAKER)
    assert (run.counts["changed_source"], run.counts["changed_restores"]) == (1, 1)
    assert run.counts["failed"] == 0
    assert _matter("2026_216").is_open
    assert _open_step("2026_216").text == "Jälgin ELi menetlust"
    stage = _decisions(run, "2026_216")["stage"]
    assert (stage.outcome, stage.reason) == (SyncOutcome.APPLIED, lovable_sync.RESTORED)
    assert _matter("2026_232").title == "Juristi oma"
    drained = _run(reference, at=_at(2), max_restored=5)
    assert drained.status == SyncRunStatus.SUCCEEDED, drained.error
    assert drained.counts["restore_limit"] == 5
    assert _matter("2026_232").title == "Näidisteema 2026_232"


def test_the_command_takes_a_one_run_restore_limit():
    from app.legacy_import.management.commands.lovable_sync import Command

    parser = Command().create_parser("manage.py", "lovable_sync")
    options = parser.parse_args(["run", "--max-restored", "250"])
    assert options.max_restored == 250
    assert parser.parse_args(["run"]).max_restored is None


def test_a_request_the_sync_ended_comes_back_when_lovable_blanks_its_outcome(reference):
    """P2-b, probe 2026_238: «ei saatnud» ended the request; Lovable then blanks
    `valja` — Lovable wins, the request is set on its day, no answer invented."""
    source = reference["source"]
    source.edit("2026_238", valja="ei saatnud")
    _run(reference, at=_at(0))
    assert _matter("2026_238").response_deadline is None
    source.edit("2026_238", valja=None)
    run = _run(reference, at=_at(1))
    request = _decisions(run, "2026_238")["opinion_deadline"]
    assert request.outcome == SyncOutcome.APPLIED, request.reason
    assert _matter("2026_238").response_deadline == dt.date.fromisoformat(REQUEST)
    assert not MatterResponseDeadline.objects.filter(
        matter=_matter("2026_238"), outcome="ANSWERED"
    ).exists()


def test_a_persons_answer_holds_only_its_own_day(reference):
    """P2-b, probe 2026_253: a person answered Lovable's request in Juristid —
    held while Lovable says nothing; a new day in Lovable is a new request."""
    from app.matters.response_deadlines import resolve_response_deadline

    _run(reference, at=_at(0))
    resolve_response_deadline(
        matter=_matter("2026_253"),
        outcome="ANSWERED",
        actor=reference["world"]["mari"],
        note="Vastatud e-kirjaga.",
    )
    run = _run(reference, at=_at(1))
    held = _decisions(run, "2026_253")["opinion_deadline"]
    assert (held.outcome, held.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.ANSWERED_IN_JURISTID,
    )
    assert _matter("2026_253").response_deadline is None
    reference["source"].edit("2026_253", arvamuse_tahtaeg="2099-12-20")
    run = _run(reference, at=_at(2))
    request = _decisions(run, "2026_253")["opinion_deadline"]
    assert (request.outcome, request.reason) == (SyncOutcome.APPLIED, "")  # a source change
    assert _matter("2026_253").response_deadline == dt.date(2099, 12, 20)


def test_a_lawyers_later_request_is_withdrawn_not_answered_by_an_earlier_send(reference):
    """P3-1: Lovable records the opinion sent on 01.11; the current request was
    made after it (a lawyer's own, another day): ended CANCELLED, never ANSWERED."""
    from app.matters.response_deadlines import change_response_deadline

    _run(reference, at=_at(0))
    matter = _matter("2026_253")
    change_response_deadline(matter=matter, deadline=None, actor=None)
    change_response_deadline(matter=_matter("2026_253"), deadline=dt.date(2099, 12, 15), actor=None)
    reference["source"].edit("2026_253", valja="2026-11-01", valja_kuupaev="2026-11-01")
    run = _run(reference, at=_at(1))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert _matter("2026_253").response_deadline is None
    ended = MatterResponseDeadline.objects.filter(matter=matter).order_by("-ended_at").first()
    assert (ended.deadline, ended.outcome) == (dt.date(2099, 12, 15), "CANCELLED")
    assert "enne seda küsimist" in ended.note
    assert not MatterResponseDeadline.objects.filter(
        matter=matter, deadline=dt.date(2099, 12, 15), outcome="ANSWERED"
    ).exists()


def test_a_derived_change_is_a_source_change_not_a_restore(reference, monkeypatch):
    """P3-2: the drafting step ending because its day passed, and the request
    ending because Lovable's sent status changed, are not restores — they count
    as source changes and the restore breaker never holds them."""
    source = reference["source"]
    source.edit("2026_253", jargmiseks="", arvamuse_tahtaeg="2026-11-13")
    _run(reference, at=_at(0))
    assert _open_step("2026_253").target_date == dt.date(2026, 11, 13)
    monkeypatch.setattr(lovable_sync, "MAX_RESTORED_MATTERS", 0)
    late = _run(reference, at=_at(48))  # 14.11: the request day has passed
    step = _decisions(late, "2026_253")["next_action_text"]
    assert (step.outcome, step.reason) == (SyncOutcome.APPLIED, "")
    assert _open_step("2026_253") is None
    assert late.status == SyncRunStatus.SUCCEEDED, late.error
    assert late.counts["changed_restores"] == 0

    source.edit("2026_232", valja="2026-11-10", valja_kuupaev="2026-11-10")
    run = _run(reference, at=_at(49))
    request = _decisions(run, "2026_232")["opinion_deadline"]
    assert request.reason != lovable_sync.RESTORED
    assert _matter("2026_232").response_deadline is None


def test_the_onenote_evidence_pilot_is_not_a_lovable_commencement(reference):
    """P3-3: only the Lovable importer's cohorts (`lovable_pilot.COHORTS`) mark a
    commencement as Lovable's; the OneNote evidence pilot writes the same kinds."""
    from app.intelligence.services import add_effective_date
    from app.legacy_import.evidence_pilot import PILOT as ONENOTE_PILOT
    from app.legacy_import.lovable_pilot import COHORTS, T_EFFECTIVE_DATE

    matter = _matter("2026_253")
    record = add_effective_date(
        matter=matter,
        kind="KNOWN_DATE",
        date_value=dt.date(2030, 1, 1),
        period_end=dt.date(2030, 1, 1),
        actor=None,
    )
    assert ONENOTE_PILOT not in COHORTS
    for pilot, key in ((ONENOTE_PILOT, "onenote"), (REGISTER_COHORT, "lovable")):
        EvidencePilotRecord.objects.create(
            pilot=pilot,
            pilot_version="1",
            package_sha256="f" * 64,
            record_key=key,
            record_kind=T_EFFECTIVE_DATE,
            matter=matter,
            target_model=T_EFFECTIVE_DATE,
            target_id=record.pk,
        )
        if pilot == ONENOTE_PILOT:
            assert lovable_sync.lovable_commencements(matter.pk) == []
    assert lovable_sync.lovable_commencements(matter.pk) == [
        [T_EFFECTIVE_DATE, "KNOWN_DATE", "2030-01-01", ""]
    ]


def test_standing_holds_are_counted_and_the_historical_ones_stay_in_the_archive_group(
    reference,
):
    """P3-4: fields held while Lovable is unchanged come back every night; the
    run counts them per cohort and reason, and on a closed history Teema they
    are the archive's own (`historical_hold`), not operational work."""
    from app.legacy_import.lovable_pilot import HISTORY_COHORT

    _history(reference, {"2026_107": {}})
    _run(reference, at=_at(0))
    # Juristid says Chamber initiative with no recorded earlier track, Lovable
    # unchanged: `TRACK_NOT_STATED` every night (`_scalar`'s ``known_differs``).
    for ref in ("2026_107", "2026_253"):
        Matter.all_objects.filter(pk=_matter(ref).pk).update(track=Track.KODA_INITIATIVE)
    key = lovable_sync.TRACK_NOT_STATED
    for night in (1, 2):
        run = _run(reference, at=_at(night))
        counts = run.counts["standing_holds"]
        assert counts.get(f"{REGISTER_COHORT}:{key}") == 1, counts
        assert counts.get(f"{HISTORY_COHORT}:{key}") == 1, counts
    held = LovableSyncDecision.objects.filter(run=run, reason=key).select_related("state")
    by_cohort = {
        d.state.cohort: lovable_sync.historical_hold(d.state.cohort, d.reason, d.baseline, d.source)
        for d in held
    }
    assert by_cohort == {REGISTER_COHORT: False, HISTORY_COHORT: True}
    assert held.filter(lovable_sync.historical_hold_q()).count() == 1


# -- round 4e: request identity --------------------------------------------------------


def _last_ended(ref: str) -> MatterResponseDeadline:
    return (
        MatterResponseDeadline.objects.filter(matter=_matter(ref))
        .order_by("-ended_at", "-created_at")
        .first()
    )


def test_lovables_own_request_moved_by_lovable_with_an_earlier_send_is_answered(reference):
    """P2-1 probe A: in one edit Lovable records the opinion sent on 01.11 and
    moves its request day: the current request is Lovable's — ANSWERED."""
    _run(reference, at=_at(0))
    reference["source"].edit(
        "2026_253", valja="2026-11-01", valja_kuupaev="2026-11-01", arvamuse_tahtaeg="2099-12-01"
    )
    run = _run(reference, at=_at(1))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert _matter("2026_253").response_deadline is None
    ended = _last_ended("2026_253")
    assert (ended.deadline, ended.outcome) == (dt.date.fromisoformat(REQUEST), "ANSWERED")
    assert ended.note.startswith("Lovable'i järgi saadetud")


def test_lovables_request_moved_by_a_person_is_answered_by_an_earlier_send(reference):
    """P2-1 probe B: a person moved Lovable's request (MOVED keeps its
    `requested_at`); Lovable then records an earlier send — ANSWERED, no false note."""
    from app.matters.enums import ResponseDeadlineChange
    from app.matters.response_deadlines import change_response_deadline

    _run(reference, at=_at(0))
    change_response_deadline(
        matter=_matter("2026_253"),
        deadline=dt.date(2099, 12, 24),
        actor=reference["world"]["mari"],
        change=ResponseDeadlineChange.MOVED,
    )
    reference["source"].edit("2026_253", valja="2026-11-01", valja_kuupaev="2026-11-01")
    run = _run(reference, at=_at(1))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    ended = _last_ended("2026_253")
    assert (ended.deadline, ended.outcome) == (dt.date(2099, 12, 24), "ANSWERED")
    assert "enne seda küsimist" not in ended.note


def test_a_request_moved_and_answered_by_a_person_is_still_lovables_request(reference):
    """P2-2: a person moved Lovable's request (11.04 → 12.24) and answered it:
    the chain holds Lovable's day — held, never a second consultation."""
    from app.matters.enums import ResponseDeadlineChange
    from app.matters.response_deadlines import change_response_deadline, resolve_response_deadline

    _run(reference, at=_at(0))
    mari = reference["world"]["mari"]
    change_response_deadline(
        matter=_matter("2026_253"),
        deadline=dt.date(2099, 12, 24),
        actor=mari,
        change=ResponseDeadlineChange.MOVED,
    )
    resolve_response_deadline(
        matter=_matter("2026_253"), outcome="ANSWERED", actor=mari, note="Vastatud."
    )
    run = _run(reference, at=_at(1))
    held = _decisions(run, "2026_253")["opinion_deadline"]
    assert (held.outcome, held.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.ANSWERED_IN_JURISTID,
    )
    assert _matter("2026_253").response_deadline is None


def test_only_the_syncs_own_ending_lifts_the_hold(reference):
    """P3-1: an ending with no actor but Juristid's note (a pilot's, or one whose
    person was since deleted), or with a submission, is Juristid's: held."""
    from app.matters.response_deadlines import resolve_response_deadline

    assert lovable_sync.is_sync_ending(
        ended_by_id=None, note="Lovable'i järgi saadetud 01.11.2026.", submission_id=None
    )
    assert not lovable_sync.is_sync_ending(
        ended_by_id=None, note="Lovable'i järgi saadetud 01.11.2026.", submission_id="x"
    )
    assert not lovable_sync.is_sync_ending(
        ended_by_id=None, note="Vastatud e-kirjaga.", submission_id=None
    )
    _run(reference, at=_at(0))
    resolve_response_deadline(
        matter=_matter("2026_253"), outcome="ANSWERED", actor=None, note="Vastatud e-kirjaga."
    )
    run = _run(reference, at=_at(1))
    held = _decisions(run, "2026_253")["opinion_deadline"]
    assert (held.outcome, held.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.ANSWERED_IN_JURISTID,
    )


def _replace_and_answer(reference: dict[str, Any], previous_outcome: str) -> None:
    from app.matters.enums import ResponseDeadlineChange
    from app.matters.response_deadlines import change_response_deadline, resolve_response_deadline

    mari = reference["world"]["mari"]
    change_response_deadline(
        matter=_matter("2026_253"),
        deadline=dt.date(2099, 12, 24),
        actor=mari,
        change=ResponseDeadlineChange.REPLACED,
        previous_outcome=previous_outcome,
        previous_note="Vastatud e-kirjaga." if previous_outcome == "ANSWERED" else "",
    )
    resolve_response_deadline(
        matter=_matter("2026_253"), outcome="ANSWERED", actor=mari, note="Vastatud."
    )


def test_a_request_set_again_after_juristids_answer_follows_whether_lovable_changed(reference):
    """4e P3-2 as amended in 4f: a person replaced Lovable's request (SUPERSEDED,
    not answered) and answered their own. Lovable unchanged: Lovable's request
    is put back as a restore (its breaker applies); Lovable stating a new day is
    a source change."""
    _run(reference, at=_at(0))
    _replace_and_answer(reference, "SUPERSEDED")
    run = _run(reference, at=_at(1))
    request = _decisions(run, "2026_253")["opinion_deadline"]
    assert (request.outcome, request.reason) == (SyncOutcome.APPLIED, lovable_sync.RESTORED)
    assert _matter("2026_253").response_deadline == dt.date.fromisoformat(REQUEST)


def test_lovables_request_answered_then_replaced_and_answered_is_held(reference):
    """4f P2-A probe B: REPLACED with previous_outcome=ANSWERED — Lovable's own
    request was answered in Juristid; the later answer must not hide it."""
    _run(reference, at=_at(0))
    _replace_and_answer(reference, "ANSWERED")
    run = _run(reference, at=_at(1))
    held = _decisions(run, "2026_253")["opinion_deadline"]
    assert (held.outcome, held.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.ANSWERED_IN_JURISTID,
    )
    assert _matter("2026_253").response_deadline is None


def test_lovables_request_answered_then_a_fresh_one_answered_is_held(reference):
    """4f P2-A probe A: Juristid answered Lovable's request, then asked and
    answered a fresh request of its own: still held, no second consultation."""
    from app.matters.response_deadlines import change_response_deadline, resolve_response_deadline

    _run(reference, at=_at(0))
    mari = reference["world"]["mari"]
    resolve_response_deadline(
        matter=_matter("2026_253"), outcome="ANSWERED", actor=mari, note="Vastatud."
    )
    change_response_deadline(matter=_matter("2026_253"), deadline=dt.date(2099, 12, 24), actor=mari)
    resolve_response_deadline(
        matter=_matter("2026_253"), outcome="ANSWERED", actor=mari, note="Vastatud ka."
    )
    run = _run(reference, at=_at(1))
    held = _decisions(run, "2026_253")["opinion_deadline"]
    assert (held.outcome, held.reason) == (
        SyncOutcome.REVIEW_REQUIRED,
        lovable_sync.ANSWERED_IN_JURISTID,
    )


def test_the_first_night_without_a_recorded_identity_falls_back_to_the_dates(reference):
    """4f P3-1: no `requested_at` recorded yet and the first night is an ending:
    a request recorded after the send, for another day, is withdrawn with the
    «enne seda küsimist» note; recorded on the send day the dates cannot tell,
    so it is taken as Lovable's (ANSWERED). Every Teema's identity is recorded
    on that night."""
    from app.matters.response_deadlines import change_response_deadline

    for ref in ("2026_253", "2026_232"):
        change_response_deadline(matter=_matter(ref), deadline=None, actor=None)
        change_response_deadline(matter=_matter(ref), deadline=dt.date(2099, 12, 15), actor=None)
    today = timezone.localdate()
    reference["source"].edit(
        "2026_253", valja=(today - dt.timedelta(days=3)).isoformat(), valja_kuupaev=None
    )
    # Sent today: the request was recorded today, not after the send.
    reference["source"].edit("2026_232", valja=today.isoformat(), valja_kuupaev=None)
    for state in LovableSyncState.objects.filter(reference__in=["2026_253", "2026_232"]):
        assert lovable_sync.REQUESTED_AT not in state.applied.get("opinion_deadline", {})
    run = _run(reference, at=dt.datetime.combine(today, dt.time(23, 0), tzinfo=TALLINN))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    later = _last_ended("2026_253")
    assert (later.outcome, "enne seda küsimist" in later.note) == ("CANCELLED", True)
    assert _last_ended("2026_232").outcome == "ANSWERED"
    for state in LovableSyncState.objects.all():
        if "opinion_deadline" in state.applied:
            assert lovable_sync.REQUESTED_AT in state.applied["opinion_deadline"], state.reference


def test_the_sent_path_ends_only_lovables_own_request(reference):
    """4f P3-3 / P3-2: Lovable records the send for its request day, but the
    current request on that day is a fresh one of Juristid's (REPLACED, then
    moved onto Lovable's day): withdrawn, never answered by the send; recorded
    on the send day, its note claims no order of events."""
    from app.matters.enums import ResponseDeadlineChange
    from app.matters.response_deadlines import change_response_deadline

    today = timezone.localdate()
    _run(reference, at=dt.datetime.combine(today, dt.time(21, 0), tzinfo=TALLINN))
    mari = reference["world"]["mari"]
    change_response_deadline(
        matter=_matter("2026_253"),
        deadline=dt.date(2099, 12, 24),
        actor=mari,
        change=ResponseDeadlineChange.REPLACED,
        previous_outcome="SUPERSEDED",
    )
    change_response_deadline(
        matter=_matter("2026_253"),
        deadline=dt.date.fromisoformat(REQUEST),
        actor=mari,
        change=ResponseDeadlineChange.MOVED,
    )
    reference["source"].edit("2026_253", valja=today.isoformat(), valja_kuupaev=None)
    run = _run(reference, at=dt.datetime.combine(today, dt.time(22, 0), tzinfo=TALLINN))
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    ended = _last_ended("2026_253")
    assert (ended.deadline, ended.outcome) == (dt.date.fromisoformat(REQUEST), "CANCELLED")
    assert "enne seda" not in ended.note and "ei ole Lovable'i" in ended.note
    assert not MatterResponseDeadline.objects.filter(
        matter=_matter("2026_253"), outcome="ANSWERED"
    ).exists()


def test_a_legacy_moved_request_keeps_its_whole_chain(reference):
    """4f P3-4: a request with no `requested_at`, moved and then answered: the
    MOVED rows lead back to Lovable's day, so it is held."""
    from app.matters.enums import ResponseDeadlineChange
    from app.matters.response_deadlines import change_response_deadline, resolve_response_deadline

    _run(reference, at=_at(0))
    mari = reference["world"]["mari"]
    matter = _matter("2026_253")
    Matter.all_objects.filter(pk=matter.pk).update(response_requested_at=None)
    state = LovableSyncState.objects.get(reference="2026_253")
    entry = dict(state.applied["opinion_deadline"])
    entry.pop(lovable_sync.REQUESTED_AT, None)
    LovableSyncState.objects.filter(pk=state.pk).update(
        applied={**state.applied, "opinion_deadline": entry}
    )
    for day in (dt.date(2099, 12, 24), dt.date(2099, 12, 28)):
        change_response_deadline(
            matter=_matter("2026_253"),
            deadline=day,
            actor=mari,
            change=ResponseDeadlineChange.MOVED,
        )
    resolve_response_deadline(
        matter=_matter("2026_253"), outcome="ANSWERED", actor=mari, note="Vastatud."
    )
    assert lovable_sync._last_ended_request(matter.pk)["days"] == [
        REQUEST,
        "2099-12-24",
        "2099-12-28",
    ]
    run = _run(reference, at=_at(1))
    held = _decisions(run, "2026_253")["opinion_deadline"]
    assert held.reason == lovable_sync.ANSWERED_IN_JURISTID
