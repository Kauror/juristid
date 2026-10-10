"""Closing a Teema confirms any live work it would end (owner's decision of 2026-10-10).

docs/adr/0152. A `Hetkeseis` that ends the Matter — «Jõustunud», «Rohkem ei tegele»
— closes it, and closure ends the current step, planned steps and checks, planned
website overviews and open feedback waits. None is deleted; until now only a
pending `Arvamuse järelkontroll` asked first (docs/adr/0146 §8). What is pinned:

* any live work stops the closure, names what (in counts), and closes nothing;
* confirmed, the closure goes ahead exactly as before;
* nothing live, nothing asked;
* the check-only case keeps ADR 0146's words, sentence and box;
* a step the same save completes is done, not outstanding;
* every person-facing path shows the sentence and the confirming box.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.closure_work import CHECKS_ONLY_LABEL, LIVE_WORK_LABEL, live_work_at_closure
from app.matters.enums import EngagementKind
from app.matters.models import Matter
from app.matters.services import (
    add_engagement,
    change_stage,
    create_matter,
    plan_website_overview,
)
from app.matters.workspace import complete_current_action
from app.workflow.enums import ActionStatus
from app.workflow.follow_ups import FOLLOW_UP_CLOSURE_WARNING, FollowUpClosureUnconfirmed
from app.workflow.models import StageVocabulary
from app.workflow.services import add_planned_action, set_next_action

pytestmark = pytest.mark.django_db


def stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def filed(owner, **extra) -> Matter:
    return create_matter(
        title="Jõustuv määrus", actor=owner, owner=owner, stage=stage("awaiting_entry"), **extra
    )


def refusal_of(matter: Matter, actor) -> FollowUpClosureUnconfirmed:
    with pytest.raises(FollowUpClosureUnconfirmed) as caught:
        change_stage(matter=matter, stage=stage("in_force"), actor=actor)
    return caught.value


def test_a_current_step_stops_the_closure_and_is_named(specialist):
    matter = filed(specialist)
    step = set_next_action(matter=matter, text="Jälgi rakendusakti eelnõu", actor=specialist)

    refusal = refusal_of(matter, specialist)

    assert "praegune tegevus" in str(refusal)
    assert refusal.label == LIVE_WORK_LABEL
    # Counts and kinds, never the step's own words.
    assert "Jälgi rakendusakti eelnõu" not in str(refusal)
    matter.refresh_from_db()
    step.refresh_from_db()
    assert matter.is_open
    assert step.status == ActionStatus.OPEN


def test_confirmed_the_closure_goes_ahead_as_before(specialist):
    matter = filed(specialist)
    step = set_next_action(matter=matter, text="Viimane samm", actor=specialist)

    change_stage(
        matter=matter, stage=stage("in_force"), actor=specialist, follow_ups_confirmed=True
    )

    matter.refresh_from_db()
    step.refresh_from_db()
    assert not matter.is_open
    assert step.status == ActionStatus.CANCELLED


def test_nothing_live_closes_without_asking(specialist):
    matter = filed(specialist)

    change_stage(matter=matter, stage=stage("in_force"), actor=specialist)

    matter.refresh_from_db()
    assert not matter.is_open


def test_every_kind_of_live_work_is_counted(specialist):
    matter = filed(specialist)
    set_next_action(matter=matter, text="Praegune", actor=specialist)
    for days in (5, 9):
        add_planned_action(
            matter=matter,
            text=f"Plaan {days}",
            target_date=timezone.localdate() + timedelta(days=days),
            actor=specialist,
        )
    plan_website_overview(matter=matter, actor=specialist)
    add_engagement(
        matter=matter,
        kind=EngagementKind.values[0],
        title="Liikmete küsitlus",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )

    work = live_work_at_closure(matter)
    refusal = refusal_of(matter, specialist)

    assert (work.current, work.planned, work.overviews, work.waits) == (True, 2, 1, 1)
    assert str(refusal).startswith(
        "Teema sulgemisel lõpetatakse ka pooleli töö: praegune tegevus, 2 planeeritud "
        "tegevust, 1 planeeritud kodulehe ülevaade ja 1 kaasamise tagasiside ootus."
    )
    assert "seotud teemale" in str(refusal)


def test_checks_alone_keep_the_follow_up_words(specialist):
    """ADR 0146 §8's sentence and box, unchanged, when a check is all there is."""
    from app.organisations.models import Organisation
    from tests.follow_ups import day, send_koja_arvamus

    matter = filed(specialist)
    ministry = Organisation.objects.create(name="Näidisministeerium")
    send_koja_arvamus(matter, specialist, [ministry], day(-1))

    refusal = refusal_of(matter, specialist)

    assert str(refusal) == FOLLOW_UP_CLOSURE_WARNING
    assert refusal.label == CHECKS_ONLY_LABEL


def test_a_step_finished_in_the_same_save_is_not_outstanding(specialist):
    """«✓ Tehtud» that also sets «Jõustunud» finishes the step it closes over."""
    matter = filed(specialist)
    step = set_next_action(matter=matter, text="Viimane samm", actor=specialist)

    complete_current_action(
        matter=matter, author=specialist, action_id=step.pk, body="Tehtud.", stage=stage("in_force")
    )

    matter.refresh_from_db()
    step.refresh_from_db()
    assert not matter.is_open
    assert step.status == ActionStatus.COMPLETED


def test_tehtud_still_asks_about_the_rest(specialist):
    matter = filed(specialist)
    step = set_next_action(matter=matter, text="Praegune", actor=specialist)
    add_planned_action(
        matter=matter,
        text="Hiljem",
        target_date=timezone.localdate() + timedelta(days=4),
        actor=specialist,
    )

    with pytest.raises(FollowUpClosureUnconfirmed) as caught:
        complete_current_action(
            matter=matter,
            author=specialist,
            action_id=step.pk,
            body="Tehtud.",
            stage=stage("in_force"),
        )

    assert "1 planeeritud tegevus" in str(caught.value)
    assert "praegune tegevus" not in str(caught.value)
    step.refresh_from_db()
    assert step.status == ActionStatus.OPEN


def test_muuda_teemat_shows_the_sentence_and_the_box(signed_in, specialist):
    from app.matters.forms import edit_initial

    matter = filed(specialist)
    set_next_action(matter=matter, text="Praegune", actor=specialist)
    initial = edit_initial(matter)
    payload = {
        "revision": initial["revision"],
        "title": initial["title"],
        "owner": str(specialist.pk),
        "stage": str(stage("in_force").pk),
        "legal_instruments": [""],
    }

    response = signed_in.post(reverse("matters:matter_edit", kwargs={"pk": matter.pk}), payload)

    assert response.status_code == 400
    body = response.content.decode()
    assert "Teema sulgemisel lõpetatakse ka pooleli töö: praegune tegevus." in body
    assert LIVE_WORK_LABEL in body
    matter.refresh_from_db()
    assert matter.is_open

    payload["confirm_follow_up_closure"] = "on"
    response = signed_in.post(reverse("matters:matter_edit", kwargs={"pk": matter.pk}), payload)
    assert response.status_code == 302
    matter.refresh_from_db()
    assert not matter.is_open


def test_the_header_offers_the_confirming_button_in_its_words(signed_in, specialist):
    matter = filed(specialist)
    set_next_action(matter=matter, text="Praegune", actor=specialist)

    response = signed_in.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "stage"}),
        {"stage": str(stage("in_force").pk)},
        HTTP_HX_REQUEST="true",
    )

    body = response.content.decode()
    assert response.status_code == 400
    assert "praegune tegevus" in body
    assert f">{LIVE_WORK_LABEL}</button>" in body
    matter.refresh_from_db()
    assert matter.is_open
