"""Every compact `LISA TEEMALE` door saves a drawn form once (owner decision D3, 2026-10-17).

JQA-005 (audit of 2026-10-17): only `Koja arvamus` carried a save-once token
(`salvestus`, `workspace._spend_once`). A double click, a network retry, two
tabs of the same drawn form or a resubmit filed every other compact record
twice — two rounds and two open waits for one `Alusta kaasamist`. The owner
decided that every compact door gets the same token (docs/adr/0167, amendment
of 2026-10-17):

* each door draws its own fresh token;
* the second save of the same drawn form is refused with `ALREADY_SAVED`
  (400, nothing written) and the refused form keeps the spent token, so a third
  press is refused too;
* a refused save (validation or service) spends nothing, and the re-drawn form
  carries the same unspent token, so correcting it and saving again works;
* a successful save re-draws the form with a fresh token for the next record,
  so a deliberate repeat is opening the form again;
* a form opened from a step (docs/adr/0168) carries the token too.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone

from app.intelligence.models import MatterEffectiveDate, MatterImportantDate, MatterWorkVictory
from app.matters.entry_enums import EntryKind
from app.matters.enums import EngagementKind, ExternalPositionProvenance
from app.matters.models import Entry, MatterEngagement, MatterExternalPosition
from app.matters.services import add_engagement
from app.matters.workspace import ALREADY_SAVED
from app.workflow.enums import PlanStepState
from app.workflow.models import MatterPlanStep
from app.workflow.plan import plan_revision, plan_steps_of, seed_standard_plan, skip_plan_step
from tests import factories
from tests.follow_ups import day, et

pytestmark = pytest.mark.django_db

RECEIVED = ExternalPositionProvenance.RECEIVED.value
DISCOVERED = ExternalPositionProvenance.DISCOVERED.value


@dataclass(frozen=True)
class Door:
    """One compact door: its route, what a valid and a refused save post, and its rows."""

    route: str
    valid: Callable[[Any], dict[str, Any]]
    invalid: Callable[[Any], dict[str, Any]]
    rows: Callable[[Any], Any]

    def __str__(self) -> str:
        return self.route


DOORS = [
    Door(
        "add_engagement_compact",
        lambda m: {
            "audience": "Liikmed",
            "occurred_on": et(day(0)),
            "feedback_deadline": et(day(7)),
        },
        lambda m: {"audience": "", "occurred_on": et(day(0))},
        lambda m: MatterEngagement.objects.filter(matter=m),
    ),
    Door(
        "add_important_date",
        lambda m: {
            "deadline_title": "Kooskõlastuse tähtaeg",
            "deadline_date": et(day(10)),
            "deadline_precision": "EXACT",
        },
        lambda m: {"deadline_title": "", "deadline_date": "", "deadline_precision": "EXACT"},
        lambda m: MatterImportantDate.objects.filter(matter=m),
    ),
    Door(
        "add_effective_date",
        lambda m: {"effective_title": "Seadus jõustub", "effective_on": et(day(30))},
        lambda m: {"effective_title": "", "effective_on": et(day(30))},
        lambda m: MatterEffectiveDate.objects.filter(matter=m),
    ),
    Door(
        "add_work_victory",
        lambda m: {"victory_change": "Säte jäeti välja", "victory_date": et(day(0))},
        lambda m: {"victory_change": "", "victory_date": et(day(0))},
        lambda m: MatterWorkVictory.objects.filter(matter=m),
    ),
    Door(
        "add_other",
        lambda m: {"description": "Taustamaterjal", "occurred_on": et(day(0)), "url": ""},
        lambda m: {"description": "", "occurred_on": et(day(0)), "url": ""},
        lambda m: Entry.objects.filter(matter=m, kind=EntryKind.OTHER),
    ),
    Door(
        "add_meeting",
        lambda m: {"summary": "Kohtumine ministeeriumiga", "occurred_on": et(day(0))},
        lambda m: {"summary": "", "occurred_on": et(day(0))},
        lambda m: Entry.objects.filter(matter=m, kind=EntryKind.MEETING),
    ),
    Door(
        "add_engagement_reply",
        lambda m: {
            "provenance": DISCOVERED,
            "organisation_name": "Avaldaja Liit",
            "summary": "Sama avaldatud seisukoht.",
        },
        # No provenance: refused by the form (docs/adr/0170 §1).
        lambda m: {"summary": "Sama avaldatud seisukoht."},
        lambda m: MatterExternalPosition.objects.filter(matter=m),
    ),
]


def _url(route: str, matter: Any) -> str:
    return reverse(f"matters:{route}", kwargs={"pk": matter.pk})


def _post(client: Any, route: str, matter: Any, data: dict[str, Any]) -> Any:
    return client.post(_url(route, matter), data, headers={"HX-Request": "true"})


def _page(client: Any, matter: Any, query: str = "") -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}) + query)
    assert response.status_code == 200
    return response.content.decode()


def _token(html: str, matter: Any, route: str) -> str:
    """The `salvestus` token inside the form that posts to ``route``."""
    start = html.find(f'hx-post="{_url(route, matter)}"')
    assert start != -1, f"{route}: the form is not on the page"
    form = html[start : html.index("</form>", start)]
    found = re.findall(r'<input type="hidden" name="salvestus" value="([^"]*)"', form)
    assert len(found) == 1 and found[0], f"{route}: the form carries no save-once token"
    return found[0]


# ---------------------------------------------------------------------------
# The drawn form
# ---------------------------------------------------------------------------


def test_every_compact_door_draws_its_own_token(signed_in, normal_matter):
    body = _page(signed_in, normal_matter)

    tokens = [_token(body, normal_matter, door.route) for door in DOORS]
    tokens.append(_token(body, normal_matter, "add_koda_opinion"))

    for token in tokens:
        uuid.UUID(token)
    # One per form: a draft kept in one panel across another panel's save keeps
    # a token that save did not spend (static/js/app.js `keepDrafts`).
    assert len(set(tokens)) == len(tokens)
    # Drawing the page fresh draws fresh tokens.
    again = _page(signed_in, normal_matter)
    assert not set(tokens) & {_token(again, normal_matter, door.route) for door in DOORS}


# ---------------------------------------------------------------------------
# One drawn form, one record
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("door", DOORS, ids=str)
def test_the_same_drawn_form_saves_once(signed_in, normal_matter, door):
    token = str(uuid.uuid4())
    data = {**door.valid(normal_matter), "salvestus": token}

    first = _post(signed_in, door.route, normal_matter, data)
    second = _post(signed_in, door.route, normal_matter, data)

    assert first.status_code == 200
    assert second.status_code == 400
    assert ALREADY_SAVED in second.content.decode()
    assert door.rows(normal_matter).count() == 1
    # The refused form keeps the spent token: a third press is refused as well.
    assert _token(second.content.decode(), normal_matter, door.route) == token
    third = _post(signed_in, door.route, normal_matter, data)
    assert third.status_code == 400
    assert door.rows(normal_matter).count() == 1


def test_a_double_pressed_alusta_kaasamist_opens_one_wait(signed_in, normal_matter):
    """The audit's case (JQA-005): two rounds and two open waits for one press."""
    data = {**DOORS[0].valid(normal_matter), "salvestus": str(uuid.uuid4())}

    _post(signed_in, "add_engagement_compact", normal_matter, data)
    _post(signed_in, "add_engagement_compact", normal_matter, data)

    rounds = MatterEngagement.objects.filter(matter=normal_matter)
    assert rounds.count() == 1
    assert sum(round_.has_open_feedback_wait for round_ in rounds) == 1


@pytest.mark.parametrize("door", DOORS, ids=str)
def test_a_fresh_form_is_a_deliberate_repeat(signed_in, normal_matter, door):
    data = door.valid(normal_matter)

    first = _post(signed_in, door.route, normal_matter, {**data, "salvestus": str(uuid.uuid4())})
    second = _post(signed_in, door.route, normal_matter, {**data, "salvestus": str(uuid.uuid4())})

    assert (first.status_code, second.status_code) == (200, 200)
    assert door.rows(normal_matter).count() == 2


@pytest.mark.parametrize("door", DOORS, ids=str)
def test_a_saved_form_is_drawn_again_with_a_fresh_token(signed_in, normal_matter, door):
    token = _token(_page(signed_in, normal_matter), normal_matter, door.route)

    saved = _post(
        signed_in, door.route, normal_matter, {**door.valid(normal_matter), "salvestus": token}
    )

    assert saved.status_code == 200
    fresh = _token(saved.content.decode(), normal_matter, door.route)
    assert fresh != token
    # The next record is the person's to make, from the form as it now is.
    again = _post(
        signed_in, door.route, normal_matter, {**door.valid(normal_matter), "salvestus": fresh}
    )
    assert again.status_code == 200
    assert door.rows(normal_matter).count() == 2


# ---------------------------------------------------------------------------
# A refused save spends nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("door", DOORS, ids=str)
def test_a_refused_save_keeps_its_token_unspent(signed_in, normal_matter, door):
    token = _token(_page(signed_in, normal_matter), normal_matter, door.route)

    refused = _post(
        signed_in, door.route, normal_matter, {**door.invalid(normal_matter), "salvestus": token}
    )

    assert refused.status_code == 400
    assert ALREADY_SAVED not in refused.content.decode()
    assert not door.rows(normal_matter).exists()
    # The corrected form is the same drawn form: same token, and it saves.
    assert _token(refused.content.decode(), normal_matter, door.route) == token
    corrected = _post(
        signed_in, door.route, normal_matter, {**door.valid(normal_matter), "salvestus": token}
    )
    assert corrected.status_code == 200
    assert door.rows(normal_matter).count() == 1


def test_a_service_refusal_after_the_spend_rolls_the_token_back(signed_in, normal_matter):
    """A refusal raised under the lock after the token was spent — here a file
    the evidence store refuses — unwinds the spend with everything else."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    token = str(uuid.uuid4())
    data = {"description": "Taust", "occurred_on": et(day(0)), "url": "", "salvestus": token}
    not_a_pdf = SimpleUploadedFile("vale.pdf", b"MZ not a pdf", content_type="application/pdf")

    refused = _post(signed_in, "add_other", normal_matter, {**data, "attachments": [not_a_pdf]})

    assert refused.status_code == 400
    assert ALREADY_SAVED not in refused.content.decode()
    saved = _post(signed_in, "add_other", normal_matter, data)
    assert saved.status_code == 200
    assert Entry.objects.filter(matter=normal_matter, kind=EntryKind.OTHER).count() == 1


# ---------------------------------------------------------------------------
# Lisa tagasiside: linked replies and the form `Tehtud` opens
# ---------------------------------------------------------------------------


def _round(matter: Any, actor: Any) -> MatterEngagement:
    return add_engagement(
        matter=matter,
        kind=EngagementKind.OTHER,
        title="Liikmete küsitlus",
        occurred_on=timezone.localdate(),
        feedback_deadline=day(7),
        actor=actor,
    )


def test_a_linked_reply_drawn_once_is_saved_once(signed_in, normal_matter, specialist):
    round_ = _round(normal_matter, specialist)
    token = _token(
        _page(signed_in, normal_matter, f"?ava=tagasiside&kaasamine={round_.pk}"),
        normal_matter,
        "add_engagement_reply",
    )
    data = {
        "engagement": str(round_.pk),
        "provenance": RECEIVED,
        "summary": "Toetame.",
        "salvestus": token,
    }

    first = _post(signed_in, "add_engagement_reply", normal_matter, data)
    second = _post(signed_in, "add_engagement_reply", normal_matter, data)

    assert (first.status_code, second.status_code) == (200, 400)
    assert MatterExternalPosition.objects.filter(engagement=round_).count() == 1
    round_.refresh_from_db()
    assert round_.feedback_closed_at is not None


# ---------------------------------------------------------------------------
# A form opened from a step (docs/adr/0168)
# ---------------------------------------------------------------------------


def test_alusta_kaasamist_opened_from_its_step_saves_once(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    seed_standard_plan(matter=matter, actor=specialist)
    for step in plan_steps_of(matter):
        if step.template_step_key == "consult-members":
            break
        skip_plan_step(
            step=step, actor=specialist, expected_revision=plan_revision(plan_steps_of(matter))
        )
    step = MatterPlanStep.objects.get(matter=matter, template_step_key="consult-members")
    body = _page(signed_in, matter, f"?ava=kaasamine&samm={step.pk}")
    token = _token(body, matter, "add_engagement_compact")
    data = {
        "audience": "Liikmed",
        "occurred_on": et(day(0)),
        "samm": str(step.pk),
        "salvestus": token,
    }

    first = _post(signed_in, "add_engagement_compact", matter, data)
    second = _post(signed_in, "add_engagement_compact", matter, data)

    assert (first.status_code, second.status_code) == (200, 400)
    assert MatterEngagement.objects.filter(matter=matter).count() == 1
    step.refresh_from_db()
    assert step.state == PlanStepState.COMPLETED
