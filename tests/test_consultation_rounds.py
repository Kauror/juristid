"""Several consultation rounds on one Teema, and the one obligation each surface reports.

Admin & Workflow R1, workstream D (owner brief §6, D1–D5). The question was
whether the existing `Arvamuse tähtaeg` model (docs/adr/0135, 0174, 0050) already
solves the multi-round case, or whether a second round/deadline model is needed.
This module is the independent verification, driven only through the canonical
services and the real pages:

**D1** — a ministry sends a VTK and asks for an opinion; Koda answers; the first
request is fulfilled. Months later the draft bill comes for coordination on the
same Teema with a new deadline. The new deadline is outstanding (and overdue
once its day passes) although an opinion was sent earlier, and the earlier
opinion stays the historical answer to the first request.

**D2** — moving versus a new request, explicit completion, linking a sent
opinion to a specific request, an official «no comments» answer, a decision not
to answer, a cancelled consultation, reopening a closed Teema, imported legacy
deadlines with no `response_requested_at`, stale tabs, audit events. (Real
concurrent writers are in ``tests/test_consultation_rounds_concurrency.py``.)

**D3** — the same obligation, read from every surface reachable from a test:
the Teema header, Teema käik, the rail's ended deadlines, Minu asjad, Osakond's
strip and team table, the overdue and this-week populations, the register's
``?too=`` filters, Statistika's answer rows, and the MCP read projections. Each
surface must agree; none may compute overdue/outstanding with its own rule.

**D4** — a WAIT review date, a `Kaasamine` member-feedback date, a planned
`NextAction` date, a commencement date and a milestone are never Koda's response
obligation.

The finding: **verified — the existing implementation is sufficient**. Every
assertion here passed against the unchanged canonical services.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters import work_items as wi
from app.matters.department_dashboard import TEAM_COLUMNS, seis_figures, team_rows
from app.matters.enums import EngagementKind, ResponseDeadlineChange, ResponseDeadlineOutcome
from app.matters.models import Matter, MatterResponseDeadline
from app.matters.my_work import build_my_work
from app.matters.register_filters import WORK_PARAM, register_population
from app.matters.response_deadlines import (
    ACTIVE_DEADLINE_EXISTS,
    LEGACY_DISCHARGE_NOTE,
    STALE_DEADLINE_REFUSAL,
    change_response_deadline,
    deadline_revision,
    ended_deadlines,
    request_response_deadline,
    resolve_response_deadline,
)
from app.matters.selectors import response_deadline_of
from app.matters.services import add_engagement, close_matter, reopen_matter_into_stage
from app.matters.timeline import matter_timeline
from app.mcp_server import readers
from app.reporting.context import ReportingContext, parse_period
from app.reporting.selectors import management
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.models import Submission
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent,
)
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics, Disposition
from app.workflow.models import NextAction, StageVocabulary
from app.workflow.services import set_next_action
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db


def _day(offset: int) -> dt.date:
    return timezone.localdate() + dt.timedelta(days=offset)


def _ago(days: int) -> dt.datetime:
    return timezone.now() - dt.timedelta(days=days)


def _et(day: dt.date) -> str:
    return f"{day.day}.{day.month}.{day.year}"


#: Round 1: the VTK. Asked 200 days ago, due 150 days ago, answered 160 days ago.
FIRST_ASKED, FIRST_DUE, FIRST_SENT = 200, -150, 160
#: Round 2: the draft bill. Asked 20 days ago, due two days ago — overdue today.
SECOND_ASKED, SECOND_DUE = 20, -2


# ---------------------------------------------------------------------------
# The canonical doors, used the way the product uses them
# ---------------------------------------------------------------------------


def _send(
    matter: Matter,
    actor: Any,
    *,
    title: str = "Koja arvamus",
    kind: str = SubmissionKind.FORMAL_OPINION,
    sent_at: dt.datetime | None = None,
) -> Submission:
    """A sent letter through the submission services, never a hand-written SENT row."""
    submission = create_submission(matter=matter, title=title, kind=kind, actor=actor)
    attach_final_evidence(
        submission=submission,
        content=b"%PDF-1.4 synthetic answer",
        original_filename=f"{title}.pdf",
        mime_type="application/pdf",
        actor=actor,
    )
    submission.refresh_from_db()
    return mark_submission_sent(submission=submission, actor=actor, sent_at=sent_at)


def _asked(matter: Matter, actor: Any, deadline: dt.date, *, days_ago: int) -> Matter:
    """Record a request the way the header does, then place it in the past.

    Only the request *time* is moved back — a test cannot wait months. The
    service still wrote the row; the update says when the letter arrived.
    """
    if (
        matter.response_deadline is None
        and not MatterResponseDeadline.objects.filter(matter=matter).exists()
    ):
        change_response_deadline(matter=matter, deadline=deadline, actor=actor)
    else:
        request_response_deadline(
            matter=matter,
            deadline=deadline,
            actor=actor,
            expected_revision=deadline_revision(matter),
        )
    Matter.objects.filter(pk=matter.pk).update(response_requested_at=_ago(days_ago))
    matter.refresh_from_db()
    return matter


def _first_round_answered(owner: Any, **matter_fields: Any) -> tuple[Matter, Submission]:
    """D1, first half: the VTK was asked for, answered, and the answer named."""
    matter = factories.MatterFactory(
        owner=owner, title="Sünteetiline VTK ja eelnõu", **matter_fields
    )
    _asked(matter, owner, _day(FIRST_DUE), days_ago=FIRST_ASKED)
    opinion = _send(matter, owner, title="Arvamus VTK kohta", sent_at=_ago(FIRST_SENT))
    resolve_response_deadline(
        matter=matter,
        outcome=ResponseDeadlineOutcome.ANSWERED,
        actor=owner,
        submission=opinion,
        expected_revision=deadline_revision(matter),
    )
    # Ended the day it was answered, months ago.
    MatterResponseDeadline.objects.filter(matter=matter).update(ended_at=_ago(FIRST_SENT))
    matter.refresh_from_db()
    return matter, opinion


@pytest.fixture(autouse=True)
def _public_origin(settings):
    """The MCP projections build absolute links (app/mcp_server/links.py)."""
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example"


@pytest.fixture
def second_round(specialist):
    """D1, second half: the draft bill's request is current and two days late."""
    matter, opinion = _first_round_answered(specialist)
    _asked(matter, specialist, _day(SECOND_DUE), days_ago=SECOND_ASKED)
    return matter, opinion


def _ctx(viewer: Any) -> ReportingContext:
    today = timezone.localdate()
    return ReportingContext(
        viewer=viewer,
        period=parse_period(f"{today.year - 1}-{today.year}", today),
        today=today,
        now=timezone.now(),
    )


def _row_of(matter: Matter, outcome: str) -> MatterResponseDeadline:
    return MatterResponseDeadline.objects.get(matter=matter, outcome=outcome)


def _owed(user: Any) -> set[Any]:
    return set(wi.response_obligations(user).values_list("pk", flat=True))


def _late_on_my_desk(response: Any) -> list[tuple[str, Any]]:
    """Minu asjad's overdue band as rendered: (source, what it is about), oldest first.

    A follow-up check is named by the opinion it follows up, so the test says
    which letter's check it is rather than only that one exists.
    """
    late: list[tuple[str, Any]] = []
    for band in response.context["work"].bands:
        if band.key != wi.BAND_OVERDUE:
            continue
        for item in band.items:
            if item.source_type == wi.SOURCE_RESPONSE_DEADLINE:
                late.append((item.source_type, item.matter_id))
            else:
                action = NextAction.objects.get(pk=item.object_id)
                late.append((item.source_type, action.follow_up.submission_id))
    return late


def _response_items(user: Any, matter: Matter) -> list[wi.WorkItem]:
    return [
        item
        for item in wi.work_items(user, today=timezone.localdate())
        if item.matter_id == matter.pk and item.source_type == wi.SOURCE_RESPONSE_DEADLINE
    ]


# ---------------------------------------------------------------------------
# D1 — two rounds on one Teema
# ---------------------------------------------------------------------------


def test_d1_the_new_request_is_outstanding_although_an_opinion_was_sent(specialist, second_round):
    matter, first_opinion = second_round

    assert matter.response_deadline == _day(SECOND_DUE)
    assert matter.response_requested_at > first_opinion.sent_at
    obligation = wi.response_obligation_of(matter, specialist)
    assert obligation.is_outstanding
    assert obligation.is_overdue
    assert obligation.days_late == -SECOND_DUE
    assert matter.pk in _owed(specialist)
    assert wi.response_deadline_is_outstanding(matter, specialist)


def test_d1_the_first_opinion_stays_the_answer_to_the_first_request(specialist, second_round):
    matter, first_opinion = second_round

    (first,) = MatterResponseDeadline.objects.filter(matter=matter)
    assert first.deadline == _day(FIRST_DUE)
    assert first.outcome == ResponseDeadlineOutcome.ANSWERED
    assert first.submission == first_opinion
    (shown,) = ended_deadlines(matter, specialist)
    assert shown.submission == first_opinion
    assert not shown.needs_review


def test_d1_answering_the_second_request_leaves_the_first_answer_where_it_was(
    specialist, second_round
):
    matter, first_opinion = second_round
    second_opinion = _send(matter, specialist, title="Arvamus eelnõu kohta")

    resolve_response_deadline(
        matter=matter,
        outcome=ResponseDeadlineOutcome.ANSWERED,
        actor=specialist,
        submission=second_opinion,
        expected_revision=deadline_revision(matter),
    )

    matter.refresh_from_db()
    assert matter.response_deadline is None
    answers = {
        row.deadline: row.submission_id
        for row in MatterResponseDeadline.objects.filter(matter=matter)
    }
    assert answers == {_day(FIRST_DUE): first_opinion.pk, _day(SECOND_DUE): second_opinion.pk}
    assert matter.pk not in _owed(specialist)


def test_d1_through_the_actual_pages(signed_in, specialist, organisation):
    """The two rounds entered through `+ Koja arvamus` and `+ Lisa → Arvamuse tähtaeg`."""
    matter = factories.MatterFactory(owner=specialist, title="Sünteetiline kaheringne teema")
    _asked(matter, specialist, _day(FIRST_DUE), days_ago=FIRST_ASKED)
    sent_on = _day(-FIRST_SENT)

    first = signed_in.post(
        reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}),
        {
            "upload": SimpleUploadedFile(
                "vtk.pdf", b"%PDF-1.4\n%%EOF", content_type="application/pdf"
            ),
            "recipients": [str(organisation.pk)],
            "sent_on": _et(sent_on),
            "summary": "Sünteetiline arvamus VTK kohta.",
            "vastab_tahtajale": deadline_revision(matter),
        },
        headers={"HX-Request": "true"},
    )
    assert first.status_code == 200
    first_opinion = Submission.objects.get(matter=matter, status=SubmissionStatus.SENT)
    MatterResponseDeadline.objects.filter(matter=matter).update(ended_at=_ago(FIRST_SENT))
    matter.refresh_from_db()
    assert matter.response_deadline is None

    second = signed_in.post(
        reverse("matters:add_response_deadline", kwargs={"pk": matter.pk}),
        {
            "response_deadline_date": _et(_day(SECOND_DUE)),
            "revision": deadline_revision(matter),
        },
        headers={"HX-Request": "true"},
    )
    assert second.status_code == 200
    Matter.objects.filter(pk=matter.pk).update(response_requested_at=_ago(SECOND_ASKED))

    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    body = page.content.decode()
    # The header: the new request, late, in the deadline colour.
    assert f"{_et(_day(SECOND_DUE))} · {-SECOND_DUE} p üle" in body
    # The rail's history: the first request, answered.
    assert 'id="arvamuse-tahtajad"' in body
    assert f"{_et(_day(SECOND_DUE))}</time> · praegune" in body
    assert f"{_et(_day(FIRST_DUE))}</time> · vastatud" in body
    # Teema käik: the first opinion's row names the request it answered.
    rows, _more = matter_timeline(matter=matter, user=specialist)
    (sent_row,) = [row for row in rows if isinstance(row.record, Submission)]
    assert sent_row.record.pk == first_opinion.pk
    assert sent_row.answered_deadline == _et(_day(FIRST_DUE))

    mine = signed_in.get(reverse("matters:my_work"))
    # Minu asjad's red band: the new request, and — a separate fact — the
    # first opinion's own `Arvamuse järelkontroll` (ADR 0146), never the
    # first request again.
    assert _late_on_my_desk(mine) == [
        (wi.SOURCE_NEXT_ACTION, first_opinion.pk),
        (wi.SOURCE_RESPONSE_DEADLINE, matter.pk),
    ]
    assert matter.title in mine.content.decode()
    osakond = signed_in.get(reverse("matters:department"))
    figures = {figure.key: figure.value for figure in osakond.context["page"].seis}
    assert figures["overdue"] == 1

    # The second opinion, naming the second request.
    signed_in.post(
        reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}),
        {
            "upload": SimpleUploadedFile(
                "eelnou.pdf", b"%PDF-1.4\n%%EOF", content_type="application/pdf"
            ),
            "recipients": [str(organisation.pk)],
            "sent_on": _et(timezone.localdate()),
            "summary": "Sünteetiline arvamus eelnõu kohta.",
            "vastab_tahtajale": deadline_revision(Matter.objects.get(pk=matter.pk)),
        },
        headers={"HX-Request": "true"},
    )
    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    assert "p üle" not in body
    assert (wi.SOURCE_RESPONSE_DEADLINE, matter.pk) not in _late_on_my_desk(
        signed_in.get(reverse("matters:my_work"))
    )
    rows, _more = matter_timeline(matter=matter, user=specialist)
    answered = {
        row.record.pk: row.answered_deadline for row in rows if isinstance(row.record, Submission)
    }
    assert answered[first_opinion.pk] == _et(_day(FIRST_DUE))
    assert set(answered.values()) == {_et(_day(FIRST_DUE)), _et(_day(SECOND_DUE))}


# ---------------------------------------------------------------------------
# D2 — every way a request changes
# ---------------------------------------------------------------------------


def test_d2_moving_the_new_request_keeps_it_unanswered(specialist, second_round):
    matter, _first_opinion = second_round
    requested_at = matter.response_requested_at

    change_response_deadline(
        matter=matter,
        deadline=_day(10),
        actor=specialist,
        change=ResponseDeadlineChange.MOVED,
        expected_revision=deadline_revision(matter),
    )

    matter.refresh_from_db()
    assert matter.response_deadline == _day(10)
    assert matter.response_requested_at == requested_at
    moved = _row_of(matter, ResponseDeadlineOutcome.MOVED)
    assert (moved.deadline, moved.next_deadline, moved.submission) == (
        _day(SECOND_DUE),
        _day(10),
        None,
    )
    obligation = wi.response_obligation_of(matter, specialist)
    assert obligation.is_outstanding and not obligation.is_overdue


def test_d2_a_new_request_replacing_an_unanswered_one_starts_a_fresh_request(specialist):
    matter = _asked(factories.MatterFactory(owner=specialist), specialist, _day(-30), days_ago=60)
    first_requested = matter.response_requested_at
    _send(matter, specialist, title="Saadetud ilma tähtajata seoseta", sent_at=_ago(40))

    change_response_deadline(
        matter=matter,
        deadline=_day(14),
        actor=specialist,
        change=ResponseDeadlineChange.REPLACED,
        previous_outcome=ResponseDeadlineOutcome.SUPERSEDED,
        expected_revision=deadline_revision(matter),
    )

    matter.refresh_from_db()
    assert matter.response_requested_at > first_requested
    replaced = _row_of(matter, ResponseDeadlineOutcome.SUPERSEDED)
    assert replaced.next_deadline == _day(14)
    assert replaced.submission is None
    # The opinion sent before the new request answers neither.
    assert matter.pk in _owed(specialist)


def test_d2_a_new_request_can_record_that_the_old_one_was_answered(specialist):
    matter = _asked(factories.MatterFactory(owner=specialist), specialist, _day(-30), days_ago=60)
    opinion = _send(matter, specialist, sent_at=_ago(35))

    change_response_deadline(
        matter=matter,
        deadline=_day(14),
        actor=specialist,
        change=ResponseDeadlineChange.REPLACED,
        previous_outcome=ResponseDeadlineOutcome.ANSWERED,
        previous_submission=opinion,
        expected_revision=deadline_revision(matter),
    )

    assert _row_of(matter, ResponseDeadlineOutcome.ANSWERED).submission == opinion
    assert matter.pk in _owed(specialist)


def test_d2_explicit_completion_in_words_creates_no_opinion(specialist, second_round):
    matter, _first_opinion = second_round
    before = Submission.objects.filter(matter=matter).count()

    resolve_response_deadline(
        matter=matter,
        outcome=ResponseDeadlineOutcome.ANSWERED,
        actor=specialist,
        note="Vastati ministeeriumi töörühmas suuliselt.",
        expected_revision=deadline_revision(matter),
    )

    assert Submission.objects.filter(matter=matter).count() == before
    row = MatterResponseDeadline.objects.get(matter=matter, deadline=_day(SECOND_DUE))
    assert (row.outcome, row.submission, row.note) == (
        ResponseDeadlineOutcome.ANSWERED,
        None,
        "Vastati ministeeriumi töörühmas suuliselt.",
    )
    assert matter.pk not in _owed(specialist)
    # Statistika: an answer in words has no sending day and is never «on time».
    assert {row.deadline for row in management.answer_rows(_ctx(specialist))} == {_day(FIRST_DUE)}


def test_d2_a_send_is_linked_to_the_request_it_is_named_for(specialist, second_round):
    """Two sent letters on one Teema: each request names exactly one, and the
    earlier letter named for the later request is not passed off as on time."""
    matter, first_opinion = second_round

    resolve_response_deadline(
        matter=matter,
        outcome=ResponseDeadlineOutcome.ANSWERED,
        actor=specialist,
        submission=first_opinion,
        expected_revision=deadline_revision(matter),
    )

    rows = {row.deadline: row.group for row in management.answer_rows(_ctx(specialist))}
    assert rows[_day(FIRST_DUE)] == management.ON_TIME
    # The request was recorded after that letter went: unverifiable, not on time.
    assert rows[_day(SECOND_DUE)] == management.CHANGED_AFTER_SENDING


def test_d2_an_official_no_comments_answer_answers_the_request(specialist, second_round):
    matter, _first_opinion = second_round
    answer = _send(
        matter,
        specialist,
        title="Märkusi ei ole",
        kind=SubmissionKind.NO_OBJECTION_RESPONSE,
    )

    resolve_response_deadline(
        matter=matter,
        outcome=ResponseDeadlineOutcome.ANSWERED,
        actor=specialist,
        submission=answer,
        expected_revision=deadline_revision(matter),
    )

    assert (
        MatterResponseDeadline.objects.get(matter=matter, deadline=_day(SECOND_DUE)).submission
        == answer
    )
    assert matter.pk not in _owed(specialist)
    groups = {row.deadline: row.group for row in management.answer_rows(_ctx(specialist))}
    assert groups[_day(SECOND_DUE)] == management.LATE


@pytest.mark.parametrize(
    "outcome",
    [ResponseDeadlineOutcome.NOT_ANSWERING, ResponseDeadlineOutcome.CANCELLED],
    ids=["decided-not-to-answer", "consultation-cancelled"],
)
def test_d2_not_answering_or_a_cancelled_consultation_ends_the_obligation_quietly(
    specialist, second_round, outcome
):
    matter, first_opinion = second_round

    resolve_response_deadline(
        matter=matter,
        outcome=outcome,
        actor=specialist,
        expected_revision=deadline_revision(matter),
    )

    matter.refresh_from_db()
    assert matter.response_deadline is None
    assert MatterResponseDeadline.objects.get(matter=matter, deadline=_day(SECOND_DUE)).outcome == (
        outcome
    )
    assert matter.pk not in _owed(specialist)
    assert response_deadline_of(matter, specialist) is None
    assert _response_items(specialist, matter) == []
    # Nothing was sent for it, so Statistika has nothing to judge.
    assert {row.deadline for row in management.answer_rows(_ctx(specialist))} == {_day(FIRST_DUE)}
    # And the first round is untouched.
    assert _row_of(matter, ResponseDeadlineOutcome.ANSWERED).submission == first_opinion


def test_d2_emptying_the_field_is_a_cancelled_consultation(specialist, second_round):
    matter, _first_opinion = second_round

    change_response_deadline(
        matter=matter, deadline=None, actor=specialist, expected_revision=deadline_revision(matter)
    )

    assert _row_of(matter, ResponseDeadlineOutcome.CANCELLED).deadline == _day(SECOND_DUE)


@pytest.mark.parametrize("keep", [False, True], ids=["left-behind", "carried-forward"])
def test_d2_reopening_a_closed_teema_for_a_new_round(specialist, keep):
    matter, first_opinion = _first_round_answered(specialist)
    _asked(matter, specialist, _day(SECOND_DUE), days_ago=SECOND_ASKED)
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)
    matter.refresh_from_db()
    assert response_deadline_of(matter, specialist).settled == "teema suletud"
    assert matter.pk not in _owed(specialist)

    reopen_matter_into_stage(
        matter=matter,
        stage=StageVocabulary.objects.get(key="consultation"),
        actor=specialist,
        keep_response_deadline=keep,
    )
    matter.refresh_from_db()

    if keep:
        assert matter.response_deadline == _day(SECOND_DUE)
        assert wi.response_obligation_of(matter, specialist).is_overdue
    else:
        assert _row_of(matter, ResponseDeadlineOutcome.CLOSED).deadline == _day(SECOND_DUE)
        # The reopened file is asked again: a third request, outstanding.
        request_response_deadline(matter=matter, deadline=_day(7), actor=specialist)
        matter.refresh_from_db()
        assert matter.pk in _owed(specialist)
    assert _row_of(matter, ResponseDeadlineOutcome.ANSWERED).submission == first_opinion


def test_d2_an_imported_deadline_answered_the_old_way_gives_way_to_a_new_request(specialist):
    """A legacy deadline (no `response_requested_at`) keeps ADR 0059's reading —
    any sent opinion discharged it — and a new request is not discharged by it."""
    matter = factories.MatterFactory(
        owner=specialist, response_deadline=_day(-120), title="Imporditud teema"
    )
    assert matter.response_requested_at is None
    old = _send(matter, specialist, title="Registri ajastu arvamus", sent_at=_ago(125))
    assert response_deadline_of(matter, specialist).settled == "lõpetatud"
    assert matter.pk not in _owed(specialist)

    request_response_deadline(matter=matter, deadline=_day(-1), actor=specialist)

    matter.refresh_from_db()
    assert matter.response_requested_at is not None
    legacy = _row_of(matter, ResponseDeadlineOutcome.ANSWERED)
    # No link is inferred to the old letter; the note says how it was answered.
    assert (legacy.deadline, legacy.submission, legacy.requested_at, legacy.note) == (
        _day(-120),
        None,
        None,
        LEGACY_DISCHARGE_NOTE,
    )
    assert Submission.objects.filter(pk=old.pk, status=SubmissionStatus.SENT).exists()
    assert wi.response_obligation_of(matter, specialist).is_overdue


def test_d2_an_imported_deadline_still_owed_is_never_overwritten_by_a_new_request(specialist):
    matter = factories.MatterFactory(owner=specialist, response_deadline=_day(-5))

    with refused(ACTIVE_DEADLINE_EXISTS.format(date=_et(_day(-5)))):
        request_response_deadline(matter=matter, deadline=_day(20), actor=specialist)

    # The header says which it was: here, a new request replacing it unanswered.
    change_response_deadline(
        matter=matter,
        deadline=_day(20),
        actor=specialist,
        change=ResponseDeadlineChange.REPLACED,
        previous_outcome=ResponseDeadlineOutcome.SUPERSEDED,
    )
    matter.refresh_from_db()
    assert matter.response_requested_at is not None
    assert _row_of(matter, ResponseDeadlineOutcome.SUPERSEDED).requested_at is None


def test_d2_a_tab_drawn_before_the_new_request_ends_nothing(specialist, second_round):
    matter, _first_opinion = second_round
    stale = deadline_revision(matter)
    change_response_deadline(
        matter=matter,
        deadline=_day(5),
        actor=specialist,
        change=ResponseDeadlineChange.MOVED,
        expected_revision=stale,
    )

    with refused(STALE_DEADLINE_REFUSAL):
        resolve_response_deadline(
            matter=matter,
            outcome=ResponseDeadlineOutcome.NOT_ANSWERING,
            actor=specialist,
            expected_revision=stale,
        )
    matter.refresh_from_db()
    assert matter.response_deadline == _day(5)


def test_d2_every_ending_is_audited_without_naming_the_opinion(specialist, second_round):
    matter, first_opinion = second_round
    second_opinion = _send(matter, specialist, title="Teine arvamus")
    resolve_response_deadline(
        matter=matter,
        outcome=ResponseDeadlineOutcome.ANSWERED,
        actor=specialist,
        submission=second_opinion,
    )

    ended = ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.RESPONSE_DEADLINE_ENDED
    ).order_by("created_at")
    assert [(event.payload["deadline"], event.payload["outcome"]) for event in ended] == [
        (_day(FIRST_DUE).isoformat(), ResponseDeadlineOutcome.ANSWERED),
        (_day(SECOND_DUE).isoformat(), ResponseDeadlineOutcome.ANSWERED),
    ]
    assert all(event.actor_id == specialist.pk for event in ended)
    for event in ended:
        assert str(first_opinion.pk) not in str(event.payload)
        assert str(second_opinion.pk) not in str(event.payload)
    # Each date the field took is a change event of its own: set, cleared, set, cleared.
    dates = ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_DATE_CHANGED
    ).order_by("created_at")
    assert [event.payload["deadline_to"] for event in dates] == [
        _day(FIRST_DUE).isoformat(),
        None,
        _day(SECOND_DUE).isoformat(),
        None,
    ]


# ---------------------------------------------------------------------------
# D3 — one obligation, every surface
# ---------------------------------------------------------------------------


def _surfaces(viewer: Any, matter: Matter) -> dict[str, Any]:
    """What every reachable surface says about this Matter's response obligation."""
    today = timezone.localdate()
    matter = Matter.objects.get(pk=matter.pk)
    items = wi.work_items(viewer, today=today)
    mine = [item for item in items if item.matter_id == matter.pk]
    header = response_deadline_of(matter, viewer, today)
    snapshot = readers.matter_snapshot(viewer, str(matter.pk))["opinion_deadline"]
    listed = next(
        row
        for row in readers.list_matters(viewer, include_test_data=True)["matters"]
        if row["id"].endswith(str(matter.pk))
    )["opinion_deadline"]
    figures = {figure.key: figure.value for figure in seis_figures(viewer, today, items=items)}
    (total,) = [row for row in team_rows(viewer, today, items=items) if row.is_total]
    columns = [column[0] for column in TEAM_COLUMNS]
    team = {key: total.cells[columns.index(key)].value for key in ("overdue", "week")}
    work = build_my_work(viewer, today=today, subject=matter.owner)

    def register(key: str) -> bool:
        return (
            register_population(viewer, {WORK_PARAM: key}, today=today)
            .filter(pk=matter.pk)
            .exists()
        )

    return {
        "owed": matter.pk in _owed(viewer),
        "work_list": matter.pk
        in set(wi.outstanding_response_deadlines(viewer).values_list("pk", flat=True)),
        "item_overdue": [
            item.is_overdue for item in mine if item.source_type == wi.SOURCE_RESPONSE_DEADLINE
        ],
        "overdue_items": len([i for i in wi.overdue_items(items) if i.matter_id == matter.pk]),
        "week_items": len([i for i in wi.week_items(items, today) if i.matter_id == matter.pk]),
        "header_overdue": header.is_overdue if header else None,
        "header_owed": header.is_active if header else None,
        "mcp_overdue": snapshot["is_overdue"] if snapshot else None,
        "mcp_owed": snapshot["still_owed"] if snapshot else None,
        "mcp_list_overdue": listed["is_overdue"] if listed else None,
        "osakond_overdue": figures["overdue"],
        "osakond_week": figures["week"],
        "team_overdue": team["overdue"],
        "team_week": team["week"],
        "minu_overdue": work.overdue,
        "register_overdue": register(wi.WORK_OVERDUE),
        "register_week": register(wi.WORK_DEADLINE_THIS_WEEK),
    }


def test_d3_every_surface_reports_the_new_request_as_one_overdue_obligation(
    specialist, department_head, second_round
):
    matter, _first_opinion = second_round

    for viewer in (specialist, department_head):
        seen = _surfaces(viewer, matter)
        assert seen["owed"] and seen["work_list"], viewer
        assert seen["item_overdue"] == [True]
        assert seen["overdue_items"] == 1
        assert seen["header_overdue"] is True and seen["header_owed"] is True
        assert seen["mcp_overdue"] is True and seen["mcp_owed"] is True
        assert seen["mcp_list_overdue"] is True
        assert seen["osakond_overdue"] == 1 and seen["team_overdue"] == 1
        assert seen["minu_overdue"] == 1
        assert seen["register_overdue"] is True
        assert seen["register_week"] is False


def test_d3_every_surface_agrees_once_the_new_request_is_answered(
    specialist, department_head, second_round
):
    matter, _first_opinion = second_round
    resolve_response_deadline(
        matter=matter,
        outcome=ResponseDeadlineOutcome.ANSWERED,
        actor=specialist,
        submission=_send(matter, specialist, title="Teine arvamus"),
    )

    for viewer in (specialist, department_head):
        seen = _surfaces(viewer, matter)
        assert not seen["owed"] and not seen["work_list"]
        assert seen["item_overdue"] == []
        assert seen["overdue_items"] == 0
        # The header has no current deadline left to state.
        assert seen["header_overdue"] is None and seen["mcp_overdue"] is None
        assert seen["mcp_list_overdue"] is None
        assert seen["osakond_overdue"] == 0 and seen["minu_overdue"] == 0
        assert seen["team_overdue"] == 0 and seen["team_week"] == 0
        assert not seen["register_overdue"] and not seen["register_week"]


def test_d3_a_new_request_due_today_is_this_weeks_and_not_late_everywhere(specialist):
    matter, _first_opinion = _first_round_answered(specialist)
    _asked(matter, specialist, _day(0), days_ago=SECOND_ASKED)

    seen = _surfaces(specialist, matter)

    assert seen["owed"] and seen["work_list"]
    assert seen["item_overdue"] == [False]
    assert seen["overdue_items"] == 0 and seen["week_items"] == 1
    assert seen["header_overdue"] is False and seen["header_owed"] is True
    assert seen["mcp_overdue"] is False and seen["mcp_owed"] is True
    assert seen["osakond_overdue"] == 0 and seen["osakond_week"] == 1
    assert seen["team_overdue"] == 0 and seen["team_week"] == 1
    assert seen["register_week"] is True and seen["register_overdue"] is False


def test_d3_an_instruction_moves_the_work_not_the_obligation_and_every_surface_says_so(
    specialist, second_round
):
    """ADR 0050: an open DO says what happens next, so the date leaves the work
    lists and the header stops counting it late — while the obligation itself
    (`response_obligations`, MCP `still_owed`) stays owed. Both readings agree
    across surfaces; neither is computed beside the canonical selectors."""
    matter, _first_opinion = second_round
    set_next_action(
        matter=matter, text="Koostan arvamuse eelnõu kohta", target_date=_day(3), actor=specialist
    )

    seen = _surfaces(specialist, matter)

    assert seen["owed"] is True and seen["mcp_owed"] is True and seen["header_owed"] is True
    assert seen["work_list"] is False and seen["item_overdue"] == []
    assert seen["header_overdue"] is False and seen["mcp_overdue"] is False
    assert seen["osakond_overdue"] == 0 and seen["minu_overdue"] == 0
    assert seen["register_overdue"] is False


# ---------------------------------------------------------------------------
# D4 — other dates are not Koda's response obligation
# ---------------------------------------------------------------------------


def _wait(matter: Matter, actor: Any, on: dt.date) -> None:
    set_next_action(
        matter=matter,
        text="Ootan ministeeriumi uut versiooni",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=on,
        date_precision=DatePrecision.EXACT,
        actor=actor,
    )


def _feedback(matter: Matter, actor: Any, on: dt.date) -> None:
    add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Liikmete küsitlus",
        occurred_on=on - dt.timedelta(days=10),
        feedback_deadline=on,
        actor=actor,
    )


def _plan(matter: Matter, actor: Any, on: dt.date) -> None:
    set_next_action(matter=matter, text="Kohtumine ministeeriumiga", target_date=on, actor=actor)


def _commencement(matter: Matter, actor: Any, on: dt.date) -> None:
    factories.EffectiveDateFactory(matter=matter, date_value=on, period_end=on)


def _milestone(matter: Matter, actor: Any, on: dt.date) -> None:
    factories.ImportantDateFactory(matter=matter, title="Riigikogu II lugemine", date_value=on)


OTHER_DATES = {
    "wait-review-date": _wait,
    "member-feedback-date": _feedback,
    "planned-next-action": _plan,
    "commencement-date": _commencement,
    "milestone": _milestone,
}


@pytest.mark.parametrize("record", list(OTHER_DATES), ids=list(OTHER_DATES))
def test_d4_a_passed_date_of_another_kind_is_no_response_obligation(specialist, record):
    matter = factories.MatterFactory(owner=specialist, title="Teema ilma arvamuse küsimiseta")
    OTHER_DATES[record](matter, specialist, _day(-10))

    assert matter.pk not in _owed(specialist)
    assert not wi.outstanding_response_deadlines(specialist).filter(pk=matter.pk).exists()
    assert _response_items(specialist, matter) == []
    assert response_deadline_of(matter, specialist) is None
    assert readers.matter_snapshot(specialist, str(matter.pk))["opinion_deadline"] is None
    assert not MatterResponseDeadline.objects.filter(matter=matter).exists()
    assert management.answer_rows(_ctx(specialist)) == []


@pytest.mark.parametrize("record", list(OTHER_DATES), ids=list(OTHER_DATES))
def test_d4_a_passed_date_of_another_kind_never_makes_the_request_late(specialist, record):
    """The request is due in a week; the other date went by ten days ago."""
    matter, _first_opinion = _first_round_answered(specialist)
    _asked(matter, specialist, _day(7), days_ago=SECOND_ASKED)
    OTHER_DATES[record](matter, specialist, _day(-10))

    obligation = wi.response_obligation_of(matter, specialist)
    assert obligation.is_outstanding and not obligation.is_overdue
    header = response_deadline_of(matter, specialist)
    assert header.value == _day(7) and not header.is_overdue
    assert [item.is_overdue for item in _response_items(specialist, matter)] in ([False], [])
    snapshot = readers.matter_snapshot(specialist, str(matter.pk))["opinion_deadline"]
    assert snapshot["is_overdue"] is False and snapshot["still_owed"] is True


def test_d4_a_wait_review_date_passing_is_not_lateness_of_any_kind(specialist):
    matter = factories.MatterFactory(owner=specialist)
    _wait(matter, specialist, _day(-10))

    (item,) = [i for i in wi.work_items(specialist) if i.matter_id == matter.pk]
    assert item.source_type == wi.SOURCE_NEXT_ACTION
    assert not item.is_overdue and item.is_review_ripe


def test_d4_a_member_feedback_date_is_the_offices_wait_not_kodas_answer(specialist):
    matter = factories.MatterFactory(owner=specialist)
    _feedback(matter, specialist, _day(-10))

    (item,) = [i for i in wi.work_items(specialist) if i.matter_id == matter.pk]
    assert item.source_type == wi.SOURCE_FEEDBACK_WAIT
    assert item.source_type != wi.SOURCE_RESPONSE_DEADLINE
    assert matter.pk not in _owed(specialist)
