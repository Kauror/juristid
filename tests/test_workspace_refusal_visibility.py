"""A refused workspace save must say so — even when the panel it came from is gone.

`_workspace_refusal` re-renders the column with one bound form and one sentence,
and puts the sentence inside the panel the save came from. That is the right
place while the panel exists. Two stale-tab refusals arrive at a page on which
it does not:

* `PRAEGUNE TEGEVUS` → `Salvesta` after a colleague finished the step and set
  no new one. The refusal is `STALE_ACTION_REFUSAL`; the fresh column has no
  open step, so `current_action.html` renders «Järgmine samm on määramata» and
  the form — and the paragraph that would have carried the sentence — is not
  rendered at all.
* `+ Lõpeta teema` → `Salvesta` after the Matter was closed elsewhere. The
  refusal is «Teema on juba suletud.»; the fresh column is a closed Matter, so
  `overview.html` includes no `LISA TEEMALE` and the panel the sentence was
  addressed to does not exist.

In both cases the browser swaps in a 400 that looks like a successful save by
somebody else, and what the person typed is gone without a word. The sentence
has to land somewhere that is rendered: the workspace-level `composer_error`
slot `overview.html` already keeps for exactly «a refusal no panel owns».
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.matters import workspace
from app.matters.locks import CLOSED_MATTER_REFUSAL
from app.matters.models import Entry, Matter
from app.matters.services import close_matter, reopen_matter
from app.workflow.enums import ActionKind, DateSemantics, Disposition
from app.workflow.services import complete_next_action, set_next_action

pytestmark = pytest.mark.django_db


def _action(matter, actor, *, text: str = "Vaata eelnõu üle"):
    return set_next_action(
        matter=matter,
        text=text,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=timezone.localdate() + timedelta(days=7),
        actor=actor,
    )


def _post(client, route, matter, payload):
    return client.post(
        reverse(route, kwargs={"pk": matter.pk}), payload, headers={"HX-Request": "true"}
    )


# ---------------------------------------------------------------------------
# The refusal is visible when the panel it came from is not rendered
# ---------------------------------------------------------------------------


def test_a_stale_completion_with_no_replacement_step_still_shows_the_refusal(
    signed_in, normal_matter, specialist
):
    """Tab A finishes the step and sets nothing new. Tab B presses Salvesta."""
    action = _action(normal_matter, specialist)
    complete_next_action(action=action, actor=specialist)

    response = _post(
        signed_in,
        "matters:complete_current_action",
        normal_matter,
        {"action_id": str(action.pk), "body": "<p>Tab B kirjeldus.</p>"},
    )

    html = response.content.decode()
    assert response.status_code == 400
    assert not Entry.objects.filter(matter=normal_matter).exists()
    # The fresh state is shown — and so is the reason the save was refused.
    assert "Järgmine samm on määramata" in html
    assert workspace.STALE_ACTION_REFUSAL in html
    assert 'role="alert"' in html


def test_a_stale_completion_on_a_closed_matter_still_shows_the_refusal(
    signed_in, normal_matter, specialist
):
    """Tab A closes the Matter. Tab B, still holding the step, presses Salvesta."""
    action = _action(normal_matter, specialist)
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)

    response = _post(
        signed_in,
        "matters:complete_current_action",
        normal_matter,
        {"action_id": str(action.pk), "body": "<p>Hiline tulemus.</p>"},
    )

    html = response.content.decode()
    assert response.status_code == 400
    assert not Entry.objects.filter(matter=normal_matter).exists()
    assert CLOSED_MATTER_REFUSAL in html


def test_a_second_closure_from_a_stale_tab_still_shows_the_refusal(
    signed_in, normal_matter, specialist
):
    """Tab A closes the Matter. Tab B's `+ Lõpeta teema` is still open and is saved."""
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)
    events_before = ChangeEvent.objects.filter(
        event_type=ChangeEventType.MATTER_CLOSED, matter=normal_matter
    ).count()

    response = _post(
        signed_in,
        "matters:close_from_workspace",
        normal_matter,
        {"disposition": "INITIATIVE_WITHDRAWN", "closing_words": "Teist korda."},
    )

    html = response.content.decode()
    assert response.status_code == 400
    assert "Teema on juba suletud." in html
    # The stale tab's header still said «Avatud»; the refusal brings the closed
    # header with it, out of band, as the successful closure does.
    assert 'id="teema-pais" hx-swap-oob="true"' in html
    normal_matter.refresh_from_db()
    # The first closure's facts are untouched by the refused second one.
    assert normal_matter.disposition == Disposition.COMPLETED
    assert (
        ChangeEvent.objects.filter(
            event_type=ChangeEventType.MATTER_CLOSED, matter=normal_matter
        ).count()
        == events_before
    )


def test_a_refusal_the_panel_can_show_is_still_shown_in_the_panel(
    signed_in, normal_matter, specialist
):
    """The existing behaviour: with a replacement step the form carries the sentence."""
    stale = _action(normal_matter, specialist, text="Vana")
    _action(normal_matter, specialist, text="Uus")

    response = _post(
        signed_in,
        "matters:complete_current_action",
        normal_matter,
        {"action_id": str(stale.pk), "body": "<p>Vana tulemus.</p>"},
    )

    html = response.content.decode()
    assert response.status_code == 400
    assert workspace.STALE_ACTION_REFUSAL in html
    assert "workspace__error" not in html


# ---------------------------------------------------------------------------
# Reopening is serialised on the Matter row like closing is
# ---------------------------------------------------------------------------


def test_a_double_submitted_reopen_writes_one_reopen_event(normal_matter, specialist):
    """Two tabs, both showing a closed Matter, both press «Ava uuesti…».

    `close_matter` locks and re-reads the row so a second closure is refused
    with «Teema on juba suletud.»; the reopen has to answer the mirror case the
    same way, rather than trusting the instance the request arrived with and
    recording that the Matter was reopened twice.
    """
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)
    first_tab = Matter.objects.get(pk=normal_matter.pk)
    second_tab = Matter.objects.get(pk=normal_matter.pk)
    assert first_tab.is_open is False and second_tab.is_open is False

    reopen_matter(matter=first_tab, actor=specialist)
    with pytest.raises(DomainError, match="juba avatud"):
        reopen_matter(matter=second_tab, actor=specialist)

    assert (
        ChangeEvent.objects.filter(
            event_type=ChangeEventType.MATTER_REOPENED, matter=normal_matter
        ).count()
        == 1
    )
    normal_matter.refresh_from_db()
    assert normal_matter.is_open is True


def test_a_reopen_reads_the_row_it_locked_rather_than_the_instance_it_was_given(
    normal_matter, specialist
):
    """The instance says closed; the row says open by the time the lock is taken."""
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)
    stale = Matter.objects.get(pk=normal_matter.pk)
    reopen_matter(matter=Matter.objects.get(pk=normal_matter.pk), actor=specialist)

    with pytest.raises(DomainError, match="juba avatud"):
        reopen_matter(matter=stale, actor=specialist)

    fresh = Matter.objects.get(pk=normal_matter.pk)
    assert fresh.is_open is True
    assert fresh.closed_at is None
