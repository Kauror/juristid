"""`Lõpeta teema` — Koda finishes a file directly (docs/adr/0131, amendment of 2026-10-25).

The owner's requirement: a Matter is Koda's work on an issue, and that work can
end whatever the external process does — meetings with a ministry that then
drops its draft, an informal discussion needing nothing more, no common
position, a finished follow-up. The workflow is

    open Matter → Lõpeta teema → Põhjus → (Selgitus) → Lõpeta teema → closed

through `close_matter` and nothing beside it. What is pinned here:

* the panel is on an open file for a writer, defaults to «Koda ei tegele edasi»
  and asks nothing else that is required;
* closing writes the closure fields, the actor, the moment and one
  `MATTER_CLOSED` — and no `Hetkeseis` move, `Submission`, `Entry`, `Töövõit`,
  answered deadline or follow-up check;
* live work is named and confirmed by digest before it is ended (docs/adr/0152);
* the closure reads back — reason, explanation, actor, day — on the closed file
  and in `Teema käik`, and survives `Ava uuesti`;
* a second press, a stale tab, a concurrent closure, a reader, a hidden or a
  deleted Matter are all refused, and the terminal-stage closure still works.
"""

from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Callable
from datetime import timedelta

import pytest
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.intelligence.models import MatterWorkVictory
from app.matters.closure_work import LIVE_WORK_CHANGED, LIVE_WORK_LABEL
from app.matters.entry_enums import EntryKind
from app.matters.enums import EngagementKind
from app.matters.forms import CLOSURE_CHOICES, CLOSURE_EXPLANATION_MAX_LENGTH
from app.matters.models import Entry, Matter, MatterResponseDeadline, MatterStageEpisode
from app.matters.response_deadlines import change_response_deadline
from app.matters.services import add_engagement, change_stage, close_matter, create_matter
from app.matters.views import CLOSURE_DIGEST_HEADER
from app.matters.workspace import add_matter_meeting, close_matter_from_workspace
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import ActionStatus, Disposition, FollowUpState
from app.workflow.follow_ups import (
    CLOSED_WITH_MATTER,
    FOLLOW_UP_CLOSURE_WARNING,
    FollowUpClosureUnconfirmed,
)
from app.workflow.models import NextAction, OpinionFollowUp, StageVocabulary
from app.workflow.services import set_next_action
from tests import factories
from tests.follow_ups import day, follow_up_of, send_koja_arvamus
from tests.refusals import refused

pytestmark = pytest.mark.django_db

EXPLANATION = (
    "Kohtusime ministeeriumiga kolm korda. Ministeerium loobus eelnõust ja Koda "
    "selle teemaga edasi ei tegele."
)


def _stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def _matter(owner, *, stage: str = "consultation") -> Matter:
    """A natively filed Matter in a nonterminal `Hetkeseis`, so it has a period."""
    return create_matter(
        title="Pakendiseaduse muudatus", actor=owner, owner=owner, stage=_stage(stage)
    )


def _close(client, matter, **data):
    payload = {"disposition": Disposition.MONITORING_STOPPED.value, **data}
    return client.post(
        reverse("matters:close_from_workspace", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _page(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


#: `close_matter`'s own refusal of a Matter that is not open any more.
ALREADY_CLOSED = "Teema on juba suletud."


def _checked(html: str, value: str, *, attribute: str = "value") -> bool:
    """Whether the `<input>` whose ``attribute`` is ``value`` renders `checked`."""
    tag = re.search(rf'<input[^>]*\s{attribute}="{re.escape(value)}"[^>]*>', html, re.S)
    return tag is not None and re.search(r"\schecked[\s>]", tag.group(0)) is not None


def _closures(matter) -> int:
    return ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_CLOSED
    ).count()


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------


def test_the_panel_is_on_an_open_file_and_defaults_to_koda_ei_tegele_edasi(signed_in, specialist):
    matter = _matter(specialist)

    page = _page(signed_in, matter)
    zone = page[page.index('id="lisa-teemale"') :]

    assert 'id="teema-lopeta-valik"' in zone and 'id="teema-lopeta"' in zone
    assert ">Lõpeta teema</label>" in zone
    assert reverse("matters:close_from_workspace", kwargs={"pk": matter.pk}) in zone
    panel = zone[zone.index('id="teema-lopeta"') :]
    # «Koda ei tegele edasi» first and already chosen; every choice is a sentence.
    assert CLOSURE_CHOICES[0] == (Disposition.MONITORING_STOPPED.value, "Koda ei tegele edasi")
    assert _checked(panel, "MONITORING_STOPPED")
    for _value, label in CLOSURE_CHOICES:
        assert label in panel
    assert 'name="explanation"' in panel
    # No date, participant, institution, file, stage or opinion field.
    for retired in ("occurred_on", "attachments", "organisation", "stage", "recipients"):
        assert f'name="{retired}"' not in panel.split("</form>")[0]


def test_the_successor_and_duplicate_reasons_are_not_offered_or_accepted(signed_in, specialist):
    offered = {value for value, _label in CLOSURE_CHOICES}
    assert Disposition.SUPERSEDED.value not in offered
    assert Disposition.DUPLICATE.value not in offered

    matter = _matter(specialist)
    for refused_reason in (Disposition.SUPERSEDED.value, Disposition.DUPLICATE.value, "BOGUS"):
        response = _close(signed_in, matter, disposition=refused_reason)
        assert response.status_code == 400
    matter.refresh_from_db()
    assert matter.is_open and _closures(matter) == 0


def test_jo_ustus_voi_too_loppes_is_not_offered_or_accepted(signed_in, specialist):
    """«Jõustus või töö lõppes» joined a law in force (a `Hetkeseis`) and Koda
    being done («Koda ei tegele edasi»), so the panel no longer offers it
    (docs/adr/0131, release review of 2026-10-25). The disposition stays: a
    hand-built post with it is refused like any reason the panel lacks."""
    offered = {value for value, _label in CLOSURE_CHOICES}
    assert Disposition.COMPLETED.value not in offered

    matter = _matter(specialist)
    panel = _page(signed_in, matter)
    panel = panel[panel.index('id="teema-lopeta"') :].split("</form>")[0]
    assert "Jõustus või töö lõppes" not in panel
    assert f'value="{Disposition.COMPLETED.value}"' not in panel

    response = _close(signed_in, matter, disposition=Disposition.COMPLETED.value)
    assert response.status_code == 400
    assert "Vali, miks teema lõpetatakse." in response.content.decode()
    matter.refresh_from_db()
    assert matter.is_open and _closures(matter) == 0


def test_a_file_closed_as_completed_still_reads_as_before(signed_in, specialist):
    """Existing `COMPLETED` closures — every «Jõustunud» and every earlier
    direct one — keep their words on the banner and in `Teema käik`."""
    matter = _matter(specialist)
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)

    matter.refresh_from_db()
    assert matter.closure_reason_label == "Jõustus või töö lõppes"
    assert "Jõustus või töö lõppes" in _page(signed_in, matter)


def test_no_panel_on_a_closed_file_or_for_a_reader(client, signed_in, specialist, reader):
    matter = _matter(specialist)
    client.force_login(reader)
    assert 'id="teema-lopeta"' not in _page(client, matter)

    _close(signed_in, matter)
    assert 'id="teema-lopeta"' not in _page(signed_in, matter)


# ---------------------------------------------------------------------------
# 1, 4, 12 — the plain closure, and what it does not write
# ---------------------------------------------------------------------------


def test_a_file_with_no_opinion_and_no_action_closes_with_one_press(signed_in, specialist):
    matter = _matter(specialist)
    stage_before = matter.stage_id
    episodes_before = MatterStageEpisode.objects.filter(matter=matter).count()
    entries_before = Entry.objects.filter(matter=matter).count()
    follow_ups_before = OpinionFollowUp.objects.count()
    before = timezone.now()

    response = _close(signed_in, matter)

    assert response.status_code == 200
    # The header rides along out of band: its state badge moved.
    assert "hx-swap-oob" in response.content.decode()
    matter.refresh_from_db()
    assert not matter.is_open
    assert matter.disposition == Disposition.MONITORING_STOPPED
    assert matter.disposition_reason == ""
    assert matter.closed_by == specialist
    assert matter.closed_at is not None and matter.closed_at >= before
    # `Hetkeseis` is where the external process stands, and it did not move.
    assert matter.stage_id == stage_before
    assert MatterStageEpisode.objects.filter(matter=matter).count() == episodes_before
    assert not ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_STAGE_CHANGED, created_at__gte=before
    ).exists()
    # Nothing invented: no Entry, no Submission, no Töövõit, no check.
    assert Entry.objects.filter(matter=matter).count() == entries_before
    assert not Submission.objects.filter(matter=matter).exists()
    assert not MatterWorkVictory.objects.filter(matter=matter).exists()
    assert not NextAction.objects.filter(matter=matter).exists()
    assert OpinionFollowUp.objects.count() == follow_ups_before
    event = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.MATTER_CLOSED)
    assert event.actor == specialist
    assert event.payload["disposition"] == Disposition.MONITORING_STOPPED
    assert event.payload["reason"] == ""


def test_the_explanation_is_stored_whole_on_the_matter_and_its_event(signed_in, specialist):
    matter = _matter(specialist)
    long_explanation = (EXPLANATION + " ") * 6  # well past the 200-character summary

    _close(signed_in, matter, explanation=f"  {long_explanation}  ")

    matter.refresh_from_db()
    event = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.MATTER_CLOSED)
    assert matter.disposition_reason == long_explanation.strip()
    assert event.payload["reason"] == long_explanation.strip()
    assert len(event.summary) <= 200


def test_an_explanation_past_the_limit_is_refused_and_nothing_closes(signed_in, specialist):
    matter = _matter(specialist)

    response = _close(signed_in, matter, explanation="x" * (CLOSURE_EXPLANATION_MAX_LENGTH + 1))

    assert response.status_code == 400
    assert "Selgitus võib olla kuni" in response.content.decode()
    matter.refresh_from_db()
    assert matter.is_open


# ---------------------------------------------------------------------------
# 2, 3 — informal advocacy: meetings, a ministry withdrawing, no Submission
# ---------------------------------------------------------------------------


def test_meetings_are_recorded_first_and_do_not_close_the_file(signed_in, specialist):
    matter = _matter(specialist)
    for offset in (-20, -10, -2):
        add_matter_meeting(
            matter=matter,
            author=specialist,
            summary="Kohtumine ministeeriumiga eelnõu üle.",
            occurred_on=timezone.localdate() + timedelta(days=offset),
        )
    matter.refresh_from_db()
    # Rule 7: recording a meeting never closes anything.
    assert matter.is_open

    _close(signed_in, matter, explanation="Arutelud toimusid; Koda ei jätka.")

    matter.refresh_from_db()
    assert not matter.is_open
    assert Entry.objects.filter(matter=matter, kind=EntryKind.MEETING).count() == 3
    assert not Submission.objects.filter(matter=matter).exists()


def test_a_ministry_withdrawing_is_recorded_without_claiming_koda_caused_it(signed_in, specialist):
    matter = _matter(specialist)

    _close(
        signed_in,
        matter,
        disposition=Disposition.INITIATIVE_WITHDRAWN.value,
        explanation="Ministeerium võttis eelnõu tagasi.",
    )

    matter.refresh_from_db()
    assert matter.disposition == Disposition.INITIATIVE_WITHDRAWN
    assert matter.disposition_reason == "Ministeerium võttis eelnõu tagasi."
    # What happened to the initiative, and nothing about who brought it about.
    assert matter.closure_reason_label == "Eelnõu või algatus lõpetati"
    assert "Koda" not in matter.closure_reason_label
    assert not MatterWorkVictory.objects.filter(matter=matter).exists()
    page = _page(signed_in, matter)
    assert "Ministeerium võttis eelnõu tagasi." in page
    assert "Töövõit" not in page[page.index('id="ajalugu-loend"') :]


@pytest.mark.parametrize("key", ["idea", "consultation", "awaiting_entry"])
def test_a_nonterminal_stage_is_kept(signed_in, specialist, key):
    """Rule 1: Koda finishing is not the external process moving."""
    matter = _matter(specialist, stage=key)

    _close(signed_in, matter)

    matter.refresh_from_db()
    assert not matter.is_open
    assert matter.stage.key == key


# ---------------------------------------------------------------------------
# 5, 6 — live work: named, confirmed by digest, ended in the closure
# ---------------------------------------------------------------------------


def test_live_work_is_named_and_closes_only_once_confirmed(signed_in, specialist):
    matter = _matter(specialist)
    step = set_next_action(matter=matter, text="Jälgi eelnõu", actor=specialist)
    add_engagement(
        matter=matter,
        kind=EngagementKind.values[0],
        title="Liikmete küsitlus",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )

    first = _close(signed_in, matter, explanation=EXPLANATION)

    assert first.status_code == 400
    body = first.content.decode()
    assert "Teema sulgemisel lõpetatakse ka pooleli töö: praegune tegevus" in body
    assert "kaasamise tagasiside ootus" in body
    assert LIVE_WORK_LABEL in body
    digest = first[CLOSURE_DIGEST_HEADER]
    assert f'value="{digest}"' in body
    # The panel comes back open, with what was typed.
    assert _checked(body, "teema-lopeta-valik", attribute="id")
    assert EXPLANATION in body
    matter.refresh_from_db()
    step.refresh_from_db()
    assert matter.is_open and step.status == ActionStatus.OPEN

    second = _close(signed_in, matter, explanation=EXPLANATION, confirm_follow_up_closure=digest)

    assert second.status_code == 200
    matter.refresh_from_db()
    step.refresh_from_db()
    assert not matter.is_open
    assert step.status == ActionStatus.CANCELLED
    assert not matter.engagements.open_waits().exists()
    event = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.MATTER_CLOSED)
    assert str(step.pk) in event.payload["ended"]["actions"]


def test_work_added_after_the_warning_is_asked_about_again(signed_in, specialist, other_specialist):
    """A stale tab: the digest confirmed one step, a colleague added another."""
    matter = _matter(specialist)
    set_next_action(matter=matter, text="Esimene", actor=specialist)
    digest = _close(signed_in, matter)[CLOSURE_DIGEST_HEADER]
    from app.workflow.services import add_planned_action

    add_planned_action(
        matter=matter,
        text="Kolleegi plaan",
        target_date=timezone.localdate() + timedelta(days=7),
        actor=other_specialist,
    )

    again = _close(signed_in, matter, confirm_follow_up_closure=digest)

    assert again.status_code == 400
    assert LIVE_WORK_CHANGED in again.content.decode()
    matter.refresh_from_db()
    assert matter.is_open


def test_a_pending_opinion_follow_up_needs_its_own_confirmation(
    signed_in, specialist, organisation
):
    matter = _matter(specialist)
    submission = send_koja_arvamus(matter, specialist, [organisation], day(-1))
    assert follow_up_of(submission).state == FollowUpState.MONITORING

    first = _close(signed_in, matter)
    assert first.status_code == 400
    assert FOLLOW_UP_CLOSURE_WARNING in first.content.decode()
    # A bare tick drawn before the digest rule is no confirmation.
    assert _close(signed_in, matter, confirm_follow_up_closure="on").status_code == 400
    matter.refresh_from_db()
    assert matter.is_open

    done = _close(
        signed_in,
        matter,
        disposition=Disposition.RESPONSE_COMPLETE.value,
        confirm_follow_up_closure=first[CLOSURE_DIGEST_HEADER],
    )

    assert done.status_code == 200
    matter.refresh_from_db()
    follow_up = follow_up_of(submission)
    assert not matter.is_open
    assert follow_up.state == FollowUpState.CANCELLED
    assert follow_up.end_reason == CLOSED_WITH_MATTER
    # Nothing replaced the ended check.
    assert not NextAction.objects.filter(
        matter=matter, status__in=(ActionStatus.OPEN, ActionStatus.PLANNED)
    ).exists()


# ---------------------------------------------------------------------------
# 7 — opinions and deadline history are left as they were
# ---------------------------------------------------------------------------


def test_opinion_counts_and_the_deadline_history_are_untouched(signed_in, specialist, organisation):
    matter = _matter(specialist)
    deadline = timezone.localdate() + timedelta(days=14)
    change_response_deadline(matter=matter, deadline=deadline, actor=specialist)
    submission = send_koja_arvamus(matter, specialist, [organisation], day(-1))
    matter.refresh_from_db()
    submissions_before = list(Submission.objects.filter(matter=matter).values_list("pk", "status"))
    ended_before = list(
        MatterResponseDeadline.objects.filter(matter=matter).values_list("pk", "outcome")
    )
    current_deadline = matter.response_deadline
    follow_ups_before = OpinionFollowUp.objects.count()

    digest = _close(signed_in, matter)[CLOSURE_DIGEST_HEADER]
    _close(signed_in, matter, confirm_follow_up_closure=digest)

    matter.refresh_from_db()
    submission.refresh_from_db()
    assert not matter.is_open
    assert list(Submission.objects.filter(matter=matter).values_list("pk", "status")) == (
        submissions_before
    )
    assert submission.status == SubmissionStatus.SENT
    # The closure ended the opinion's check; it did not file another one.
    assert OpinionFollowUp.objects.count() == follow_ups_before
    # Closing answered nothing: the current deadline is not ended as answered.
    assert matter.response_deadline == current_deadline
    assert (
        list(MatterResponseDeadline.objects.filter(matter=matter).values_list("pk", "outcome"))
        == ended_before
    )


# ---------------------------------------------------------------------------
# 8, 9 — it reads back, and `Ava uuesti` still works
# ---------------------------------------------------------------------------


def test_the_closed_file_shows_reason_explanation_actor_and_day(signed_in, specialist):
    matter = _matter(specialist)

    _close(signed_in, matter, explanation=EXPLANATION)

    matter.refresh_from_db()
    page = _page(signed_in, matter)
    banner = page[page.index('class="banner banner--closed"') :].split("</div>")[0]
    assert "Koda ei tegele edasi" in banner and EXPLANATION in banner
    assert f"{matter.closed_at.day}.{matter.closed_at.month}.{matter.closed_at.year}" in page
    assert "Sulges" in page
    # `Teema käik`: «Teema suletud», the reason and the explanation under «Selgitus».
    history = page[page.index('id="ajalugu-loend"') :]
    assert "Teema suletud" in history
    assert "Koda ei tegele edasi" in history
    assert "Selgitus" in history and EXPLANATION in history
    # One row for the act, not a second generic Entry repeating it.
    assert history.count(EXPLANATION) == 1


def test_ava_uuesti_reopens_and_the_closure_stays_in_the_history(signed_in, specialist):
    matter = _matter(specialist)
    _close(signed_in, matter, explanation=EXPLANATION)

    signed_in.post(
        reverse("matters:reopen", kwargs={"pk": matter.pk}),
        {"stage": str(_stage("consultation").pk)},
    )

    matter.refresh_from_db()
    assert matter.is_open
    assert matter.disposition == "" and matter.disposition_reason == ""
    assert ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_REOPENED
    ).exists()
    history = _page(signed_in, matter)
    history = history[history.index('id="ajalugu-loend"') :]
    # Read back from the event, now that the Matter's own field is cleared.
    assert EXPLANATION in history
    # And the file can be finished again.
    assert _close(signed_in, matter).status_code == 200
    assert _closures(matter) == 2


# ---------------------------------------------------------------------------
# 10 — duplicate, concurrent and unauthorized attempts
# ---------------------------------------------------------------------------


def test_a_second_press_is_refused_and_writes_nothing(signed_in, specialist):
    matter = _matter(specialist)
    assert _close(signed_in, matter, explanation="Esimene").status_code == 200

    again = _close(signed_in, matter, explanation="Teine")

    assert again.status_code == 400
    assert ALREADY_CLOSED in again.content.decode()
    matter.refresh_from_db()
    assert matter.disposition_reason == "Esimene"
    assert _closures(matter) == 1


def test_the_service_refuses_a_closed_matter(specialist):
    matter = _matter(specialist)
    close_matter_from_workspace(
        matter=matter, author=specialist, disposition=Disposition.MONITORING_STOPPED
    )

    with refused(ALREADY_CLOSED):
        close_matter_from_workspace(
            matter=Matter.objects.get(pk=matter.pk),
            author=specialist,
            disposition=Disposition.OTHER,
        )
    assert _closures(matter) == 1


def test_a_reader_is_answered_404_and_nothing_closes(client, specialist, reader):
    matter = _matter(specialist)
    client.force_login(reader)

    assert _close(client, matter).status_code == 404
    matter.refresh_from_db()
    assert matter.is_open and _closures(matter) == 0


def test_a_matter_outside_the_visible_set_is_answered_404(client, signed_in, specialist):
    """Through `visible_to`: an administrator sees no restricted file, and an
    unknown id is the same answer — neither confirms a Matter exists."""
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    administrator = factories.AdministratorFactory()
    client.force_login(administrator)

    assert _close(client, matter).status_code == 404
    unknown = Matter(pk=uuid.uuid4())
    assert _close(signed_in, unknown).status_code == 404
    matter.refresh_from_db()
    assert matter.is_open and _closures(matter) == 0


def test_a_writers_get_on_the_close_url_is_405_and_nothing_closes(signed_in, specialist):
    matter = _matter(specialist)

    response = signed_in.get(reverse("matters:close_from_workspace", kwargs={"pk": matter.pk}))

    assert response.status_code == 405
    matter.refresh_from_db()
    assert matter.is_open and _closures(matter) == 0


def test_a_deleted_matter_is_answered_404(signed_in, specialist):
    matter = _matter(specialist)
    Matter._base_manager.filter(pk=matter.pk).update(deleted_at=timezone.now())

    assert _close(signed_in, matter).status_code == 404
    assert _closures(matter) == 0


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_a_direct_closure_and_a_terminal_stage_take_turns(specialist):
    """Two tabs: one presses `Lõpeta teema`, the other chooses «Jõustunud». One wins."""
    from tests.test_evidence_concurrency import TIMEOUT, Runner

    matter = _matter(specialist)
    start = threading.Barrier(2, timeout=TIMEOUT)
    outcomes: dict[str, BaseException | None] = {}

    def run(tag: str, act: Callable[[], object]) -> Callable[[], None]:
        def go() -> None:
            start.wait()
            try:
                with transaction.atomic():
                    act()
                outcomes[tag] = None
            except BaseException as error:  # recorded, not swallowed
                outcomes[tag] = error

        return go

    direct = Runner(
        run(
            "direct",
            lambda: close_matter_from_workspace(
                matter=Matter.objects.get(pk=matter.pk),
                author=specialist,
                disposition=Disposition.MONITORING_STOPPED,
            ),
        )
    ).start()
    staged = Runner(
        run(
            "stage",
            lambda: change_stage(
                matter=Matter.objects.get(pk=matter.pk),
                stage=_stage("in_force"),
                actor=specialist,
            ),
        )
    ).start()
    assert direct.join() is None and staged.join() is None

    refusals = [error for error in outcomes.values() if error is not None]
    assert len(refusals) == 1 and isinstance(refusals[0], DomainError)
    assert not isinstance(refusals[0], FollowUpClosureUnconfirmed)
    matter.refresh_from_db()
    assert not matter.is_open
    assert _closures(matter) == 1


# ---------------------------------------------------------------------------
# 11 — the terminal-stage closure is unchanged
# ---------------------------------------------------------------------------


def test_jo_ustunud_still_closes_the_file(specialist):
    matter = _matter(specialist)

    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)

    matter.refresh_from_db()
    assert not matter.is_open
    assert matter.disposition == Disposition.COMPLETED
    assert matter.closure_reason_label == "Jõustus või töö lõppes"
    event = ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.MATTER_CLOSED)
    assert event.payload["reason"] == ""


def test_rohkem_ei_tegele_still_closes_the_file(specialist):
    matter = _matter(specialist)

    change_stage(matter=matter, stage=_stage("monitoring_stopped"), actor=specialist)

    matter.refresh_from_db()
    assert not matter.is_open
    assert matter.disposition == Disposition.MONITORING_STOPPED
    assert matter.closure_reason_label == "Koda ei tegele edasi"
