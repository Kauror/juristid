"""The real-time loop on the one canonical `NextAction` (docs/adr/0126).

*Set the next step → do the work → record the real event → finish the step →
set the next one.* Asserted here, each where it is decided:

* **the direct control** — `PRAEGUNE TEGEVUS` draws `+ Lisa tegevus` on a
  file with no open step, and only there; it is `Muuda`'s own form
  and writes through the same service, so a step made directly and one made by a
  ticked future `+ Märge` are the same canonical record;
* **the completion** — `Koja arvamus` and `Lõpeta kaasamine` offer
  `Märgi praegune tegevus tehtuks` only beside an open step, unticked; ticked,
  the named step ends COMPLETED (never SUPERSEDED), no `Mida tegid?` note is
  written, and the substantive record is exactly what it would have been;
* **the boundary** — a stale or foreign step id is refused before anything is
  written, and nothing is ever completed without the tick or from a wait that
  is not a step;
* **the history** — the completion folds under «Arvamus välja» or the round as
  one row, and paging still walks the unbounded projection;
* **the surfaces** — the step that follows reads on `PRAEGUNE TEGEVUS`, Minu
  asjad, the Teemad register and Osakond like any other.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.core.errors import DomainError
from app.documents.models import Document
from app.matters.enums import EngagementKind
from app.matters.models import Entry, MatterEngagement, MatterProceduralDevelopment
from app.matters.services import add_engagement, close_matter, engagement_revision_token
from app.matters.timeline import matter_timeline
from app.matters.workspace import (
    STALE_ACTION_REFUSAL,
    add_engagement_feedback,
    add_matter_koda_opinion,
    add_procedural_development,
    complete_current_action,
)
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics, Disposition
from app.workflow.models import NextAction
from app.workflow.services import set_next_action_for_new_work
from tests import factories

pytestmark = pytest.mark.django_db

CTA = "+ Lisa tegevus"
OPTION = "Märgi praegune tegevus tehtuks"
STEP = "Vormista ja saada Koja seisukoht"
FOLLOWING = "Kontrolli menetluse seisu ja uusi materjale"


def _day(offset: int) -> dt.date:
    """A day relative to the application's clock, never written down."""
    return timezone.localdate() + dt.timedelta(days=offset)


def _et(day: dt.date) -> str:
    return format_estonian_date(day)


def _pdf(name: str = "Koja_arvamus.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, f"%PDF-1.4 {name}".encode(), content_type="application/pdf")


def _teema(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _zone(body: str) -> str:
    """`PRAEGUNE TEGEVUS` and nothing else on the page."""
    start = body.index('id="praegune-tegevus"')
    return body[start : body.index("</section>", start)]


def _open_steps(matter):
    return NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN)


def _step(matter, actor, *, text: str = STEP, days: int = -1) -> NextAction:
    """An ordinary open step, dated relative to today (yesterday: visibly late)."""
    return set_next_action_for_new_work(
        matter=matter, text=text, target_date=_day(days), actor=actor
    )


def _set_directly(client, matter, **fields):
    payload = {"text": FOLLOWING, "target_date": _et(_day(6))}
    payload.update(fields)
    return client.post(
        reverse("matters:set_action", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _koja_arvamus(client, matter, organisation, **fields):
    """`Lisa teemale → Koja arvamus`, posted as the panel posts it."""
    payload = {
        "upload": _pdf(),
        "recipients": [str(organisation.pk)],
        "sent_on": _et(timezone.localdate()),
        "summary": "Toetame eelnõu pikema üleminekuajaga.",
    }
    payload.update(fields)
    return client.post(
        reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _waiting(matter, actor, *, days: int = 7) -> MatterEngagement:
    """One consultation round still waiting, ``days`` from today."""
    deadline = _day(days)
    return add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=min(_day(-1), deadline),
        feedback_deadline=deadline,
        actor=actor,
    )


def _finish(client, engagement, **fields):
    """Press `Lõpeta kaasamine` the way the row does: with the current token."""
    engagement.refresh_from_db()
    payload = {"revision": engagement_revision_token(engagement)}
    payload.update(fields)
    return client.post(
        reverse(
            "matters:complete_engagement_feedback",
            kwargs={"pk": engagement.matter_id, "engagement_id": engagement.pk},
        ),
        payload,
        headers={"HX-Request": "true"},
    )


def _events(matter, event_type) -> list[ChangeEvent]:
    return list(ChangeEvent.objects.filter(matter=matter, event_type=event_type))


@pytest.fixture
def matter(specialist):
    return factories.MatterFactory(owner=specialist, stage=None)


# ---------------------------------------------------------------------------
# 1. The direct control
# ---------------------------------------------------------------------------


def test_a_file_with_no_step_offers_the_direct_control(signed_in, matter):
    zone = _zone(_teema(signed_in, matter))

    assert "Järgmine samm on määramata" in zone
    assert CTA in zone
    # `Muuda`'s own form, posting to `Muuda`'s own endpoint.
    assert 'id="lisa-jargmine"' in zone
    assert reverse("matters:set_action", kwargs={"pk": matter.pk}) in zone
    assert ">Salvesta</button>" in zone
    # Nothing to complete, so nothing asks what was done.
    assert "Mida tegid?" not in zone
    assert ">Muuda<" not in zone


def test_a_file_with_a_step_offers_muuda_and_not_the_direct_control(signed_in, matter, specialist):
    _step(matter, specialist)

    zone = _zone(_teema(signed_in, matter))

    assert STEP in zone
    assert ">Muuda<" in zone
    # Still offered beside a current step since docs/adr/0143 — it plans a
    # dated future action there rather than replacing this one.
    assert CTA in zone
    assert 'id="lisa-planeeritud"' in zone
    # The editor of the current step is drawn once, beside the task.
    assert zone.count('id="lisa-jargmine"') == 1


def test_a_closed_file_and_a_reader_get_no_direct_control(client, signed_in, specialist, reader):
    closed = factories.MatterFactory(owner=specialist)
    close_matter(matter=closed, disposition=Disposition.OTHER, actor=specialist)
    assert CTA not in _zone(_teema(signed_in, closed))

    open_matter = factories.MatterFactory(owner=specialist)
    client.force_login(reader)
    assert CTA not in _zone(_teema(client, open_matter))


def test_the_control_sits_under_an_upcoming_milestone_without_turning_it_into_a_step(
    signed_in, matter, specialist
):
    """An `Oluline tähtaeg` ahead is shown where no step is set (ADR 0120 §2) —
    and it is not a step: the control is still offered, and nothing is created."""
    from app.intelligence.services import add_important_date

    add_important_date(
        matter=matter,
        actor=specialist,
        title="Kooskõlastusringi lõpp",
        date_value=_day(9),
        period_end=_day(9),
        date_precision=DatePrecision.EXACT,
    )

    zone = _zone(_teema(signed_in, matter))

    assert "Kooskõlastusringi lõpp" in zone
    assert CTA in zone
    assert not NextAction.objects.filter(matter=matter).exists()


def test_setting_a_step_directly_writes_one_canonical_open_action(signed_in, matter, specialist):
    response = _set_directly(signed_in, matter, text=STEP, target_date=_et(_day(5)))

    assert response.status_code == 200
    action = _open_steps(matter).get()
    assert action.text == STEP
    assert action.target_date == _day(5)
    assert (action.kind, action.date_semantics, action.date_precision) == (
        ActionKind.DO,
        DateSemantics.DEADLINE,
        DatePrecision.EXACT,
    )
    assert action.responsible == specialist
    assert action.created_by == specialist
    assert [event.object_id for event in _events(matter, ChangeEventType.NEXT_ACTION_SET)] == [
        action.pk
    ]
    # **Not a fake Märge.** The plan is a plan: no development, no note, and
    # nothing in the history claiming the work happened.
    assert not MatterProceduralDevelopment.objects.filter(matter=matter).exists()
    assert not Entry.objects.filter(matter=matter).exists()
    # The answer is the column: the task is shown, with `Muuda` beside it.
    body = response.content.decode()
    assert STEP in _zone(body)
    # The control stays (docs/adr/0143): beside a current step it plans one.
    assert 'id="lisa-planeeritud"' in _zone(body)


def test_a_direct_step_may_have_no_day_yet(signed_in, matter):
    """ADR 0106 through the direct door: «no deadline recorded yet» is an answer."""
    response = _set_directly(signed_in, matter, target_date="")

    assert response.status_code == 200
    action = _open_steps(matter).get()
    assert action.target_date is None
    assert action.date_precision == DatePrecision.EXACT
    assert "Kuupäev määramata" in _zone(response.content.decode())


def test_a_direct_step_may_be_known_only_to_a_month(signed_in, matter):
    month = _day(40).replace(day=1)
    response = _set_directly(
        signed_in,
        matter,
        target_date="",
        next_precision=DatePrecision.MONTH,
        next_month=str(month.month),
        next_year=str(month.year),
    )

    assert response.status_code == 200, response.content.decode()[:500]
    action = _open_steps(matter).get()
    assert (action.target_date, action.date_precision) == (month, DatePrecision.MONTH)


def test_a_direct_step_with_no_sentence_is_refused_in_its_own_panel(signed_in, matter):
    response = _set_directly(signed_in, matter, text="")

    assert response.status_code == 400
    assert not NextAction.objects.filter(matter=matter).exists()
    zone = _zone(response.content.decode())
    # Reopened where it was typed, with the sentence the step controls give.
    assert re.search(r'id="lisa-jargmine"[^>]*\bopen\b', zone, re.S)
    assert "Kirjuta järgmine tegevus." in zone


def test_editing_through_muuda_supersedes_and_leaves_one_open_step(signed_in, matter, specialist):
    first = _step(matter, specialist)

    response = _set_directly(signed_in, matter, text=STEP + " ja edasta EIS-i")

    assert response.status_code == 200
    first.refresh_from_db()
    current = _open_steps(matter).get()
    assert current.text == STEP + " ja edasta EIS-i"
    assert first.status == ActionStatus.SUPERSEDED
    assert first.replaced_by == current
    # Superseded is not completed.
    assert not _events(matter, ChangeEventType.NEXT_ACTION_COMPLETED)


def test_direct_and_future_marge_steps_are_the_same_canonical_record(specialist):
    """ADR 0124 and ADR 0126 are two doors onto one service, not two records."""
    direct = factories.MatterFactory(owner=specialist)
    via_marge = factories.MatterFactory(owner=specialist)
    day = _day(8)

    set_next_action_for_new_work(matter=direct, text=STEP, target_date=day, actor=specialist)
    add_procedural_development(
        matter=via_marge, author=specialist, title=STEP, occurred_on=day, as_next_step=True
    )

    def canonical(matter):
        action = _open_steps(matter).get()
        return (
            action.text,
            action.target_date,
            action.kind,
            action.date_semantics,
            action.date_precision,
            action.responsible_id,
            action.status,
            action.replaced_by_id,
        )

    assert canonical(direct) == canonical(via_marge)
    assert NextAction.objects.filter(matter__in=[direct, via_marge]).count() == 2
    # The `Märge` is still its own record, as ADR 0124 decided; the direct
    # step writes none.
    assert MatterProceduralDevelopment.objects.filter(matter=via_marge).count() == 1
    assert not MatterProceduralDevelopment.objects.filter(matter=direct).exists()


# ---------------------------------------------------------------------------
# 2. Koja arvamus finishes the step
# ---------------------------------------------------------------------------


def test_the_opinion_panel_offers_the_option_only_beside_an_open_step(
    signed_in, matter, specialist
):
    body = _teema(signed_in, matter)
    assert OPTION not in body

    _step(matter, specialist)
    body = _teema(signed_in, matter)
    panel = body[body.index('id="arvamus-koja"') :]
    panel = panel[: panel.index("</form>")]
    assert OPTION in panel
    assert STEP in panel
    box = re.search(r'<input type="checkbox" name="complete_action"[^>]*>', panel, re.S)
    assert box is not None
    # Unticked, and naming exactly the open step.
    assert "checked" not in box.group(0)
    assert f'value="{_open_steps(matter).get().pk}"' in box.group(0)


def test_registering_the_opinion_with_the_tick_completes_the_step(
    signed_in, matter, specialist, organisation
):
    step = _step(matter, specialist)

    response = _koja_arvamus(signed_in, matter, organisation, complete_action=str(step.pk))

    assert response.status_code == 200, response.content.decode()[:800]
    # The opinion, exactly as the panel always recorded it.
    submission = Submission.objects.get(matter=matter)
    assert submission.status == SubmissionStatus.SENT
    assert submission.final_version is not None
    assert submission.final_version.document.matter_id == matter.pk
    assert timezone.localtime(submission.sent_at).date() == timezone.localdate()
    # The step: COMPLETED, by this person, now — not superseded, not replaced.
    step.refresh_from_db()
    assert step.status == ActionStatus.COMPLETED
    assert step.replaced_by is None
    assert step.ended_by == specialist
    assert timezone.localtime(step.ended_at).date() == timezone.localdate()
    # What is open now is the send's own `Arvamuse järelkontroll`, promoted by
    # the established rule — the earliest planned action becomes current when
    # the current one is completed (docs/adr/0143 §A4, docs/adr/0146 §4).
    (current,) = _open_steps(matter)
    assert current.follow_up.submission == submission
    assert NextAction.objects.filter(matter=matter, follow_up__isnull=True).count() == 1
    assert not _events(matter, ChangeEventType.NEXT_ACTION_CANCELLED)
    # **No second record of the same act.** No note, no development.
    assert not Entry.objects.filter(matter=matter).exists()
    assert not MatterProceduralDevelopment.objects.filter(matter=matter).exists()
    # One act: the send and the completion share their operation.
    sent = _events(matter, ChangeEventType.SUBMISSION_SENT)
    completed = _events(matter, ChangeEventType.NEXT_ACTION_COMPLETED)
    assert len(sent) == len(completed) == 1
    assert sent[0].operation_id is not None
    assert sent[0].operation_id == completed[0].operation_id
    # And the zone that asked about the step now reads what comes next: the
    # opinion's own check, current by promotion (docs/adr/0146 §4), with the
    # manual control still under it.
    zone = _zone(response.content.decode())
    assert "Kontrolli, kas adressaat on Koja arvamusele vastanud" in zone
    assert CTA in zone
    assert STEP not in zone


def test_registering_the_opinion_without_the_tick_leaves_the_step_open(
    signed_in, matter, specialist, organisation
):
    step = _step(matter, specialist)

    response = _koja_arvamus(signed_in, matter, organisation)

    assert response.status_code == 200
    assert Submission.objects.filter(matter=matter, status=SubmissionStatus.SENT).count() == 1
    step.refresh_from_db()
    assert step.status == ActionStatus.OPEN
    assert not _events(matter, ChangeEventType.NEXT_ACTION_COMPLETED)
    assert STEP in _zone(response.content.decode())


def test_a_step_changed_meanwhile_refuses_the_whole_save_before_any_bytes(
    signed_in, matter, specialist, organisation
):
    """The stale tab: the box names the step it showed; a colleague has replaced it."""
    shown = _step(matter, specialist)
    replacement = _step(matter, specialist, text="Uus plaan", days=4)

    response = _koja_arvamus(signed_in, matter, organisation, complete_action=str(shown.pk))

    assert response.status_code == 400
    assert STALE_ACTION_REFUSAL in response.content.decode()
    assert not Submission.objects.filter(matter=matter).exists()
    assert not Document.objects.filter(matter=matter).exists()
    replacement.refresh_from_db()
    assert replacement.status == ActionStatus.OPEN
    assert not _events(matter, ChangeEventType.NEXT_ACTION_COMPLETED)


def test_a_step_from_another_matter_is_not_found(signed_in, matter, specialist, organisation):
    _step(matter, specialist)
    elsewhere = _step(factories.MatterFactory(owner=specialist), specialist)

    response = _koja_arvamus(signed_in, matter, organisation, complete_action=str(elsewhere.pk))

    assert response.status_code == 404
    assert not Submission.objects.filter(matter=matter).exists()
    elsewhere.refresh_from_db()
    assert elsewhere.status == ActionStatus.OPEN


def test_the_use_case_refuses_a_step_that_is_not_the_open_one(matter, specialist, organisation):
    """The boundary is the use case, not the page: a caller that skips the form
    and names a finished step gets the same refusal, and nothing is written."""
    old = _step(matter, specialist)
    _step(matter, specialist, text="Uus plaan", days=4)

    with pytest.raises(DomainError, match=re.escape(STALE_ACTION_REFUSAL)):
        add_matter_koda_opinion(
            matter=matter,
            author=specialist,
            upload=_pdf(),
            recipients=[organisation],
            sent_on=timezone.localdate(),
            complete_action_id=old.pk,
        )
    assert not Document.objects.filter(matter=matter).exists()


# ---------------------------------------------------------------------------
# 3. Lõpeta kaasamine, and the wait that is not a step
# ---------------------------------------------------------------------------


def test_a_wait_alone_is_not_a_step_and_offers_nothing_to_complete(signed_in, matter, specialist):
    engagement = _waiting(matter, specialist)

    body = _teema(signed_in, matter)
    assert "Ootame tagasisidet" in _zone(body)
    assert "Lõpeta kaasamine" in body
    assert OPTION not in body

    response = _finish(signed_in, engagement, feedback_received="Kaks vastust.")

    assert response.status_code == 200
    assert "HX-Retarget" not in response
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is not None
    assert not NextAction.objects.filter(matter=matter).exists()
    assert not _events(matter, ChangeEventType.NEXT_ACTION_COMPLETED)


def test_closing_a_round_with_the_tick_completes_the_step_and_answers_with_the_column(
    signed_in, matter, specialist
):
    step = _step(matter, specialist, text="Kaasa liikmed ja koonda seisukohad", days=3)
    engagement = _waiting(matter, specialist)

    body = _teema(signed_in, matter)
    row = body[body.index(f'id="kaasamine-{engagement.pk}-sisu"') :]
    finish = row[row.index('aria-label="Kaasamise lõpetamine"') :]
    finish = finish[: finish.index("</form>")]
    assert OPTION in finish
    assert "Kaasa liikmed ja koonda seisukohad" in finish
    assert f'id="id_kaasamine_{engagement.pk}_tegevus_tehtud"' in finish

    response = _finish(
        signed_in, engagement, feedback_received="Neli vastust.", complete_action=str(step.pk)
    )

    assert response.status_code == 200
    assert response["HX-Retarget"] == "#teema-vaade"
    assert response["HX-Reswap"] == "outerHTML"
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is not None
    assert engagement.feedback_received == "Neli vastust."
    step.refresh_from_db()
    assert step.status == ActionStatus.COMPLETED
    assert step.replaced_by is None
    assert not Entry.objects.filter(matter=matter).exists()
    closed = _events(matter, ChangeEventType.ENGAGEMENT_FEEDBACK_CLOSED)
    completed = _events(matter, ChangeEventType.NEXT_ACTION_COMPLETED)
    assert closed[0].operation_id is not None
    assert closed[0].operation_id == completed[0].operation_id
    zone = _zone(response.content.decode())
    assert CTA in zone
    # The wait is gone from the zone with the step.
    assert "Ootame tagasisidet" not in zone


def test_closing_a_round_without_the_tick_leaves_the_step_and_swaps_the_row(
    signed_in, matter, specialist
):
    step = _step(matter, specialist)
    engagement = _waiting(matter, specialist)

    response = _finish(signed_in, engagement)

    assert response.status_code == 200
    assert "HX-Retarget" not in response
    step.refresh_from_db()
    assert step.status == ActionStatus.OPEN
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is not None


def test_a_stale_step_refuses_the_closure_too(signed_in, matter, specialist):
    shown = _step(matter, specialist)
    engagement = _waiting(matter, specialist)
    _step(matter, specialist, text="Uus plaan", days=4)

    response = _finish(signed_in, engagement, complete_action=str(shown.pk))

    assert response.status_code == 400
    assert STALE_ACTION_REFUSAL in response.content.decode()
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is None
    assert not _events(matter, ChangeEventType.ENGAGEMENT_FEEDBACK_CLOSED)


def test_the_closure_use_case_finishes_only_the_named_step(matter, specialist):
    step = _step(matter, specialist)
    engagement = _waiting(matter, specialist)

    add_engagement_feedback(engagement=engagement, author=specialist, complete_action_id=step.pk)

    step.refresh_from_db()
    assert step.status == ActionStatus.COMPLETED
    # Without the id, a second round closes and no step moves.
    following = _step(matter, specialist, text=FOLLOWING, days=6)
    second = _waiting(matter, specialist, days=9)
    add_engagement_feedback(engagement=second, author=specialist)
    following.refresh_from_db()
    assert following.status == ActionStatus.OPEN


# ---------------------------------------------------------------------------
# 4. Completed is not superseded
# ---------------------------------------------------------------------------


def test_replacing_supersedes_and_finishing_completes(matter, specialist, organisation):
    first = _step(matter, specialist, text="Esimene")
    second = _step(matter, specialist, text="Teine")
    first.refresh_from_db()
    assert (first.status, first.replaced_by_id) == (ActionStatus.SUPERSEDED, second.pk)

    add_matter_koda_opinion(
        matter=matter,
        author=specialist,
        upload=_pdf(),
        recipients=[organisation],
        sent_on=timezone.localdate(),
        complete_action_id=second.pk,
    )
    second.refresh_from_db()
    assert (second.status, second.replaced_by_id) == (ActionStatus.COMPLETED, None)
    assert [
        event.object_id for event in _events(matter, ChangeEventType.NEXT_ACTION_COMPLETED)
    ] == [second.pk]


def test_mida_tegid_still_writes_its_note_and_completes(matter, specialist):
    step = _step(matter, specialist)

    result = complete_current_action(
        matter=matter, author=specialist, action_id=step.pk, body="Helistasin ministeeriumi."
    )

    step.refresh_from_db()
    assert step.status == ActionStatus.COMPLETED
    assert result.entry is not None
    assert Entry.objects.filter(matter=matter).count() == 1


# ---------------------------------------------------------------------------
# 5. Teema käik: one act, one row
# ---------------------------------------------------------------------------


def _rows(matter, user):
    items, _ = matter_timeline(matter=matter, user=user, limit=200)
    return items


def test_the_completion_folds_under_the_sent_opinion(matter, specialist, organisation):
    step = _step(matter, specialist)
    add_matter_koda_opinion(
        matter=matter,
        author=specialist,
        upload=_pdf(),
        recipients=[organisation],
        sent_on=timezone.localdate(),
        complete_action_id=step.pk,
    )

    rows = _rows(matter, specialist)

    sends = [row for row in rows if row.submission is not None]
    assert len(sends) == 1
    assert sends[0].milestone.what == "Arvamus välja"
    assert sends[0].completed_step is not None
    assert sends[0].completed_step.text == STEP
    # No second row for the same act: no lone «märkis eelmise sammu tehtuks».
    assert not [
        row
        for row in rows
        if row.record is None
        and any(event.event_type == ChangeEventType.NEXT_ACTION_COMPLETED for event in row.events)
    ]
    assert not [row for row in rows if row.is_entry]


def test_the_completion_folds_under_the_finished_round(matter, specialist):
    step = _step(matter, specialist)
    engagement = _waiting(matter, specialist)
    add_engagement_feedback(engagement=engagement, author=specialist, complete_action_id=step.pk)

    rows = _rows(matter, specialist)

    rounds = [row for row in rows if row.is_engagement]
    assert len(rounds) == 1
    assert rounds[0].completed_step is not None
    assert rounds[0].completed_step.text == STEP
    assert not [
        row
        for row in rows
        if row.record is None
        and any(event.event_type == ChangeEventType.NEXT_ACTION_COMPLETED for event in row.events)
    ]


def test_a_note_completion_keeps_its_clause_and_no_pill(matter, specialist):
    step = _step(matter, specialist)
    complete_current_action(
        matter=matter, author=specialist, action_id=step.pk, body="Helistasin ministeeriumi."
    )

    rows = _rows(matter, specialist)

    note = next(row for row in rows if row.is_entry)
    assert "märkis eelmise sammu tehtuks" in note.summary_verbs
    assert note.completed_step is None


def test_the_folded_completion_renders_on_the_page(signed_in, matter, specialist, organisation):
    step = _step(matter, specialist)
    _koja_arvamus(signed_in, matter, organisation, complete_action=str(step.pk))

    body = _teema(signed_in, matter)
    kaik = body[body.index('id="ajajoon"') :]

    assert "Arvamus välja" in kaik
    assert re.search(r"✓</span>\s*<span class=\"uxtl__nextlabel\">Tehtud</span>", kaik)
    assert STEP in kaik
    assert "märkis eelmise sammu tehtuks" not in kaik


def test_paging_walks_the_same_rows_with_folded_completions(specialist, organisation):
    """ENG-018's contract with the new fold: sends and rounds that each finished a
    step, dated over several weeks and recorded today, walk page by page to
    exactly the unbounded projection."""
    from tests.test_timeline_pagination_walk import _new_matter, _signature, _walk

    matter = _new_matter(specialist)
    for offset in range(12):
        step = _step(matter, specialist, text=f"Samm {offset}", days=offset)
        if offset % 2:
            engagement = _waiting(matter, specialist, days=offset + 1)
            add_engagement_feedback(
                engagement=engagement, author=specialist, complete_action_id=step.pk
            )
        else:
            add_matter_koda_opinion(
                matter=matter,
                author=specialist,
                upload=_pdf(f"arvamus-{offset}.pdf"),
                recipients=[organisation],
                # Backdated sends recorded today: the completion's own moment
                # is today, its row weeks ago.
                sent_on=_day(-3 * offset),
                complete_action_id=step.pk,
            )

    whole, _ = matter_timeline(matter=matter, user=specialist, limit=100_000)
    pages = _walk(matter, specialist, limit=4)
    walked = [row for page in pages for row in page]

    assert [_signature(row) for row in walked] == [_signature(row) for row in whole]
    assert sum(1 for row in whole if row.completed_step is not None) == 12


# ---------------------------------------------------------------------------
# 6. The step that follows, on every work surface
# ---------------------------------------------------------------------------


def test_the_following_step_reads_on_every_work_surface(
    client, signed_in, matter, specialist, department_head, organisation
):
    step = _step(matter, specialist)
    _koja_arvamus(signed_in, matter, organisation, complete_action=str(step.pk))

    response = _set_directly(signed_in, matter, text=FOLLOWING, target_date=_et(_day(6)))
    assert response.status_code == 200

    following = _open_steps(matter).get()
    assert following.text == FOLLOWING
    assert NextAction.objects.filter(matter=matter, follow_up__isnull=True).count() == 2
    # The opinion's check, current until this step was set, went back to the
    # plan on its own day rather than being superseded (docs/adr/0146 §4).
    assert NextAction.objects.get(matter=matter, follow_up__isnull=False).status == "PLANNED"

    assert FOLLOWING in _zone(_teema(signed_in, matter))
    assert FOLLOWING in signed_in.get(reverse("matters:my_work")).content.decode()
    register = signed_in.get(reverse("matters:matter_list") + "?olek=avatud").content.decode()
    assert matter.title in register
    assert FOLLOWING in register

    client.force_login(department_head)
    assert matter.title in client.get(reverse("matters:department")).content.decode()
