"""A round that cannot be linked is refused in the words that say why.

* **JQA-009** — a linked `Lisa tagasiside` posted from a stale tab on a Teema
  somebody has since closed reads the closed-Teema sentence
  (`CLOSED_MATTER_REFUSAL`), not the generic «Valige korrektne väärtus…» it got
  while `answerable_rounds` dropped every round of a closed Matter. Still 400,
  still nothing written.
* **JUX-012** — `Muuda` on a published («Avaldatud mujal») position refuses a
  newly chosen round under `Seotud kaasamine`, in the service's own sentence
  (`EXTERNAL_POSITION_ROUND_ANSWER_IS_RECEIVED`), instead of offering a choice
  the service always refused at row level. The round a row already holds is
  kept, as the service keeps it (docs/adr/0170 §3).
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.enums import EngagementKind, ExternalPositionProvenance
from app.matters.forms import ExternalPositionEditForm
from app.matters.locks import CLOSED_MATTER_REFUSAL
from app.matters.models import MatterEngagement, MatterExternalPosition
from app.matters.services import (
    EXTERNAL_POSITION_ROUND_ANSWER_IS_RECEIVED,
    add_engagement,
    close_matter,
    external_position_revision,
)
from app.matters.workspace import add_matter_external_position
from app.workflow.enums import Disposition

pytestmark = pytest.mark.django_db

RECEIVED = ExternalPositionProvenance.RECEIVED.value
DISCOVERED = ExternalPositionProvenance.DISCOVERED.value
GENERIC_CHOICE_REFUSAL = "Valige korrektne väärtus"


def _round(matter, actor) -> MatterEngagement:
    return add_engagement(
        matter=matter,
        kind=EngagementKind.OTHER,
        title="Liikmete küsitlus",
        occurred_on=timezone.localdate(),
        actor=actor,
    )


# -- JQA-009 --------------------------------------------------------------------


@pytest.mark.parametrize("linked", [True, False])
def test_a_reply_from_a_stale_tab_on_a_closed_teema_reads_the_closed_sentence(
    signed_in, normal_matter, specialist, linked
):
    round_ = _round(normal_matter, specialist)
    close_matter(
        matter=normal_matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        follow_ups_confirmed=True,
    )
    data = {"provenance": RECEIVED, "summary": "Hiline vastus."}
    if linked:
        data["engagement"] = str(round_.pk)

    response = signed_in.post(
        reverse("matters:add_engagement_reply", kwargs={"pk": normal_matter.pk}),
        data,
        headers={"HX-Request": "true"},
    )

    body = response.content.decode()
    assert response.status_code == 400
    assert CLOSED_MATTER_REFUSAL in body
    assert GENERIC_CHOICE_REFUSAL not in body
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()


# -- JUX-012 --------------------------------------------------------------------


def _position(matter, organisation, actor, *, provenance=DISCOVERED, engagement=None):
    return add_matter_external_position(
        matter=matter,
        author=actor,
        organisation=organisation,
        provenance=provenance,
        summary="Liit avaldas seisukoha.",
        engagement=engagement,
    ).record


def _fields(position, **changes) -> dict[str, str]:
    return {
        "organisation": str(position.organisation_id),
        "url": position.url,
        "summary": position.summary,
        "position_precision": "EXACT",
        "engagement": str(position.engagement_id or ""),
        "revision": external_position_revision(position),
        **changes,
    }


def _correct(client, position, **changes):
    return client.post(
        reverse(
            "matters:update_external_position",
            kwargs={"pk": position.matter_id, "position_id": position.pk},
        ),
        _fields(position, **changes),
        headers={"HX-Request": "true"},
    )


def test_a_published_position_refuses_a_new_round_under_the_field(
    signed_in, normal_matter, organisation, specialist
):
    round_ = _round(normal_matter, specialist)
    position = _position(normal_matter, organisation, specialist)
    token = external_position_revision(position)

    response = _correct(signed_in, position, engagement=str(round_.pk))

    body = response.content.decode()
    assert response.status_code == 400
    field_error = f'id="id_valine_seisukoht_{position.pk}_engagement_error">'
    assert field_error in body
    start = body.index(field_error) + len(field_error)
    assert body[start : body.index("</p>", start)] == EXTERNAL_POSITION_ROUND_ANSWER_IS_RECEIVED
    position.refresh_from_db()
    assert position.engagement_id is None
    assert external_position_revision(position) == token


def test_the_bound_edit_form_refuses_it_before_the_service(normal_matter, organisation, specialist):
    round_ = _round(normal_matter, specialist)
    position = _position(normal_matter, organisation, specialist)

    form = ExternalPositionEditForm(
        _fields(position, engagement=str(round_.pk)), record=position, viewer=specialist
    )

    assert not form.is_valid()
    assert form.errors["engagement"] == [EXTERNAL_POSITION_ROUND_ANSWER_IS_RECEIVED]


def test_a_round_a_published_row_already_holds_is_kept(
    signed_in, normal_matter, organisation, specialist
):
    """A historical link is not rewritten (docs/adr/0170 §3): the row stays correctable."""
    round_ = _round(normal_matter, specialist)
    position = _position(normal_matter, organisation, specialist)
    MatterExternalPosition.objects.filter(pk=position.pk).update(engagement=round_)
    position.refresh_from_db()

    response = _correct(signed_in, position, summary="Parandatud sõnastus.")

    assert response.status_code == 200
    position.refresh_from_db()
    assert position.summary == "Parandatud sõnastus."
    assert position.engagement_id == round_.pk


def test_received_feedback_may_still_be_linked_to_a_round(
    signed_in, normal_matter, organisation, specialist
):
    round_ = _round(normal_matter, specialist)
    position = _position(normal_matter, organisation, specialist, provenance=RECEIVED)

    response = _correct(signed_in, position, engagement=str(round_.pk))

    assert response.status_code == 200
    position.refresh_from_db()
    assert position.engagement_id == round_.pk
