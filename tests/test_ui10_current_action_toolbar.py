"""UI-10: the current step's toolbar, `✓ Tehtud | Vaatasin üle | Muuda`.

The owner's brief (6 November 2026, §13): the three stay together, in that
order, at the right of the step; each toggles its own form; all three may be
open at once; and a form drawn before another save changed the step must not
submit an obsolete revision.

What this module holds, server side:

* the toolbar is three buttons in one group, in that order, with
  `aria-expanded` / `aria-controls`, and the three panels are siblings under the
  step's line in the same order — not `<details>`, not inside the group;
* every panel's form posts the revision it was drawn from, spelled exactly as
  `views.current_action_revision` spells it, and the same token is each
  panel's `data-draft-state`;
* a stale revision is refused with `STALE_REVISION_REFUSAL` and nothing
  written — for `Muuda`, `Vaatasin üle` and `✓ Tehtud` alike — while the
  current revision saves and a form posting no revision keeps the id check it
  always had;
* a second press of the same `Vaatasin üle` is still one review (ENG-021);
* (review round) a stale panel is refused as stale **before** it is validated,
  and redrawn from the step as it is now rather than under the fresh token with
  the old fields — so the corrected save cannot put the old day back; a Matter
  closed meanwhile gets the closed-Matter refusal instead; a stale `✓ Tehtud`
  with a file stores no `Document` and no bytes;
* a reader and a closed Matter get no toolbar at all.

The browser half — all three open together, the buttons not moving, typed
text surviving a toggle, focus — is `e2e/test_ui10_current_action_toolbar.py`.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.documents.models import Document
from app.matters import views
from app.matters.forms import period_initial
from app.matters.locks import CLOSED_MATTER_REFUSAL
from app.matters.services import close_matter
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import acknowledge_review, set_next_action
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db

DAY = datetime.date(2026, 11, 20)


def _wait(matter, owner, *, on=DAY):
    return set_next_action(
        matter=matter,
        text="Ootame ministeeriumi vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=on,
        date_precision=DatePrecision.EXACT,
        responsible=owner,
        actor=owner,
    )


def _detail(client, matter) -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _zone(body: str) -> str:
    return body[
        body.index('id="praegune-tegevus"') : body.index(
            "</section>", body.index('id="praegune-tegevus"')
        )
    ]


def _revisions(body: str) -> set[str]:
    return set(re.findall(r'name="revision" value="([^"]+)"', _zone(body)))


def _open(matter):
    return NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)


def _muuda(action, revision, *, text="Ootame ministeeriumi vastust", when=DAY):
    """What `Muuda` posts, built the way the page prefills it (`period_initial`)."""
    data = {"action_id": str(action.pk), "revision": revision, "text": text}
    for key, value in period_initial(
        "next", when, DatePrecision.EXACT, date_field="target_date"
    ).items():
        data[key] = format_estonian_date(value) if isinstance(value, datetime.date) else value
    return data


# ---------------------------------------------------------------------------
# The markup
# ---------------------------------------------------------------------------


def test_the_toolbar_is_three_buttons_in_order_with_their_panels_below(
    signed_in, specialist, normal_matter
):
    _wait(normal_matter, specialist)
    zone = _zone(_detail(signed_in, normal_matter))

    tools = zone[
        zone.index('class="curact__tools"') : zone.index(
            "</div>", zone.index('class="curact__tools"')
        )
    ]
    buttons = re.findall(
        r'<button type="button"[^>]*aria-controls="([^"]+)"[^>]*>([^<]+)</button>', tools
    )
    assert buttons == [
        ("tehtud", "✓ Tehtud"),
        ("vaatasin-ule", "Vaatasin üle"),
        ("lisa-jargmine", "Muuda"),
    ]
    assert tools.count('aria-expanded="false"') == 3
    assert "<details" not in tools and "<form" not in tools, (
        "nothing a button opens lives in the group"
    )

    panels = zone[zone.index('class="curact__toolpanels"') :]
    order = [panels.index(f'id="{panel}"') for panel in ("tehtud", "vaatasin-ule", "lisa-jargmine")]
    assert order == sorted(order), "the panels follow the toolbar's order"
    for panel in ("tehtud", "vaatasin-ule", "lisa-jargmine"):
        classes = re.search(rf'<div class="([^"]*)" id="{panel}"', panels).group(1)
        # Shut by a class the stylesheet hides only where scripting runs, never
        # by `hidden`: without scripting every panel is shown (review P3).
        assert "curact__toolpanel--shut" in classes, f"#{panel} is shut until its button is pressed"
        opening = panels[panels.index(f'id="{panel}"') :].split(">", 1)[0]
        assert " hidden" not in opening
        assert "<details" not in opening
    # The toolbar sits on the step's own line, before any panel.
    assert zone.index('class="curact__tools"') < zone.index('class="curact__toolpanels"')


def test_a_step_that_does_not_wait_has_no_review_button(signed_in, specialist, normal_matter):
    set_next_action(
        matter=normal_matter,
        text="Koosta vastus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=DAY,
        responsible=specialist,
        actor=specialist,
    )
    zone = _zone(_detail(signed_in, normal_matter))
    tools = zone[
        zone.index('class="curact__tools"') : zone.index(
            "</div>", zone.index('class="curact__tools"')
        )
    ]
    assert re.findall(r'aria-controls="([^"]+)"', tools) == ["tehtud", "lisa-jargmine"]
    assert 'id="vaatasin-ule"' not in zone


def test_every_panel_posts_the_revision_it_was_drawn_from(signed_in, specialist, normal_matter):
    action = _wait(normal_matter, specialist)
    body = _detail(signed_in, normal_matter)
    expected = views.current_action_revision(_open(normal_matter))

    assert _revisions(body) == {expected}
    assert len(re.findall(r'name="revision" value="', _zone(body))) == 3
    for panel in ("tehtud", "vaatasin-ule", "lisa-jargmine"):
        opening = body[body.index(f'id="{panel}"') :].split(">", 1)[0]
        assert f'data-draft-state="{expected}"' in opening
    assert expected.startswith(f"{action.pk}@")


def test_a_refusal_reopens_its_own_panel_and_names_it_expanded(
    signed_in, specialist, normal_matter
):
    action = _wait(normal_matter, specialist)
    url = reverse("matters:review_action", kwargs={"pk": normal_matter.pk, "action_id": action.pk})

    response = signed_in.post(url, {"next_review_date": "31.02.2026"})

    assert response.status_code == 400
    body = response.content.decode()
    assert 'aria-controls="vaatasin-ule"\n                aria-expanded="true"' in body
    classes = re.search(r'<div class="([^"]*)" id="vaatasin-ule"', body).group(1)
    assert "curact__toolpanel--shut" not in classes


# ---------------------------------------------------------------------------
# A stale revision is refused, and nothing is written
# ---------------------------------------------------------------------------


def test_the_hold_refuses_a_revision_another_save_replaced(specialist, normal_matter):
    action = _wait(normal_matter, specialist)
    drawn = views.current_action_revision(action)
    acknowledge_review(action=action, actor=specialist, next_review_date=datetime.date(2026, 12, 1))

    with refused(views.STALE_REVISION_REFUSAL):
        views._hold_the_drawn_revision(normal_matter, action.pk, drawn)


def test_the_hold_takes_the_current_revision_and_lets_an_unposted_one_pass(
    specialist, normal_matter
):
    action = _wait(normal_matter, specialist)
    held = views._hold_the_drawn_revision(
        normal_matter, action.pk, views.current_action_revision(action)
    )
    assert held is not None and held.pk == action.pk
    assert views._hold_the_drawn_revision(normal_matter, action.pk, "") is None


def test_the_hold_refuses_a_step_of_another_matter(specialist, normal_matter):
    other = factories.MatterFactory(owner=specialist)
    action = _wait(other, specialist)
    with refused(views.STALE_REVISION_REFUSAL):
        views._hold_the_drawn_revision(
            normal_matter, action.pk, views.current_action_revision(action)
        )


def test_a_muuda_drawn_before_a_review_does_not_save_the_old_day_back(
    signed_in, specialist, normal_matter
):
    """The case the brief names: `Vaatasin üle` saved, `Muuda` still open beside it."""
    action = _wait(normal_matter, specialist)
    drawn = views.current_action_revision(action)
    review = reverse(
        "matters:review_action", kwargs={"pk": normal_matter.pk, "action_id": action.pk}
    )
    assert (
        signed_in.post(review, {"next_review_date": "4.12.2026", "revision": drawn}).status_code
        == 200
    )

    response = signed_in.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        _muuda(action, drawn, text="Ootame vastust (parandatud)", when=datetime.date(2026, 11, 20)),
    )

    assert response.status_code == 400
    assert views.STALE_REVISION_REFUSAL in response.content.decode()
    current = _open(normal_matter)
    assert current.pk == action.pk, "nothing was superseded"
    assert (current.text, current.target_date) == (
        "Ootame ministeeriumi vastust",
        datetime.date(2026, 12, 4),
    )
    # What they typed comes back in the reopened form, now drawn from the new revision.
    body = response.content.decode()
    assert "Ootame vastust (parandatud)" in body
    assert _revisions(body) == {views.current_action_revision(current)}


def test_a_review_drawn_before_another_review_is_refused(signed_in, specialist, normal_matter):
    action = _wait(normal_matter, specialist)
    drawn = views.current_action_revision(action)
    acknowledge_review(action=action, actor=specialist, next_review_date=datetime.date(2026, 12, 1))
    review = reverse(
        "matters:review_action", kwargs={"pk": normal_matter.pk, "action_id": action.pk}
    )

    response = signed_in.post(review, {"next_review_date": "8.12.2026", "revision": drawn})

    assert response.status_code == 400
    assert views.STALE_REVISION_REFUSAL in response.content.decode()
    assert _open(normal_matter).target_date == datetime.date(2026, 12, 1)
    assert (
        ChangeEvent.objects.filter(
            matter=normal_matter, event_type=ChangeEventType.NEXT_ACTION_REVIEWED
        ).count()
        == 1
    )


def test_the_same_review_pressed_twice_is_still_one_review(signed_in, specialist, normal_matter):
    """ENG-021 survives the revision: the second press asks for the day the step already has."""
    action = _wait(normal_matter, specialist)
    drawn = views.current_action_revision(action)
    review = reverse(
        "matters:review_action", kwargs={"pk": normal_matter.pk, "action_id": action.pk}
    )
    posted = {"next_review_date": "4.12.2026", "revision": drawn}

    first = signed_in.post(review, posted)
    second = signed_in.post(review, posted)

    assert (first.status_code, second.status_code) == (200, 200)
    assert (
        ChangeEvent.objects.filter(
            matter=normal_matter, event_type=ChangeEventType.NEXT_ACTION_REVIEWED
        ).count()
        == 1
    )


def test_a_completion_drawn_before_a_review_is_refused_and_writes_nothing(
    signed_in, specialist, normal_matter
):
    action = _wait(normal_matter, specialist)
    drawn = views.current_action_revision(action)
    acknowledge_review(action=action, actor=specialist, next_review_date=datetime.date(2026, 12, 1))
    entries = normal_matter.entries.count()

    response = signed_in.post(
        reverse("matters:complete_current_action", kwargs={"pk": normal_matter.pk}),
        {"action_id": str(action.pk), "revision": drawn, "body": "Helistasin ministeeriumi"},
    )

    assert response.status_code == 400
    assert views.STALE_REVISION_REFUSAL in response.content.decode()
    assert _open(normal_matter).pk == action.pk
    assert normal_matter.entries.count() == entries
    # The words are not lost: the refused form comes back open with them.
    assert "Helistasin ministeeriumi" in response.content.decode()


def test_the_current_revision_saves_from_every_panel(signed_in, specialist, normal_matter):
    action = _wait(normal_matter, specialist)
    review = reverse(
        "matters:review_action", kwargs={"pk": normal_matter.pk, "action_id": action.pk}
    )
    assert (
        signed_in.post(
            review,
            {"next_review_date": "4.12.2026", "revision": views.current_action_revision(action)},
        ).status_code
        == 200
    )

    current = _open(normal_matter)
    response = signed_in.post(
        reverse("matters:set_action", kwargs={"pk": normal_matter.pk}),
        _muuda(current, views.current_action_revision(current), text="Ootame vastust uuesti"),
    )
    assert response.status_code == 200
    replaced = _open(normal_matter)
    assert replaced.text == "Ootame vastust uuesti"

    response = signed_in.post(
        reverse("matters:complete_current_action", kwargs={"pk": normal_matter.pk}),
        {
            "action_id": str(replaced.pk),
            "revision": views.current_action_revision(replaced),
            "body": "Vastus saabus",
        },
    )
    assert response.status_code == 200
    assert not NextAction.objects.filter(matter=normal_matter, status=ActionStatus.OPEN).exists()


# ---------------------------------------------------------------------------
# Read-only access: no toolbar
# ---------------------------------------------------------------------------


def test_a_reader_gets_the_step_and_no_toolbar(client, reader, specialist, normal_matter):
    _wait(normal_matter, specialist)
    client.force_login(reader)
    zone = _zone(_detail(client, normal_matter))
    assert "Ootame ministeeriumi vastust" in zone
    for absent in (
        "curact__tools",
        "curact__toolpanels",
        "data-curact-toggle",
        'name="revision"',
        "<form",
    ):
        assert absent not in zone


def test_a_closed_matter_has_no_toolbar(signed_in, specialist, normal_matter):
    _wait(normal_matter, specialist)
    close_matter(
        matter=normal_matter, disposition="COMPLETED", actor=specialist, follow_ups_confirmed=True
    )
    zone = _zone(_detail(signed_in, normal_matter))
    assert "Puudub — teema on suletud" in zone
    for absent in ("curact__tools", "data-curact-toggle", "<form", "<button"):
        assert absent not in zone


# ---------------------------------------------------------------------------
# Review round (2026-11-06): a stale panel is refused before it is validated
# ---------------------------------------------------------------------------


def _evidence_files() -> list[Path]:
    return [path for path in Path(settings.EVIDENCE_ROOT).rglob("*") if path.is_file()]


def test_a_validation_refusal_does_not_launder_a_stale_muuda(signed_in, specialist, normal_matter):
    """The reviewer's sequence (P2), exactly.

    Muuda is drawn while the step is due 20.11; a colleague's `Vaatasin üle`
    moves it to 9.12; the stale Muuda is posted with a validation error; the
    person corrects the text and saves. Until the fix the 400 redrew the old day
    under the fresh token, and the corrected save put 20.11 back with a 200.
    """
    action = _wait(normal_matter, specialist, on=datetime.date(2026, 11, 20))
    drawn = views.current_action_revision(action)
    acknowledge_review(action=action, actor=specialist, next_review_date=datetime.date(2026, 12, 9))
    url = reverse("matters:set_action", kwargs={"pk": normal_matter.pk})

    invalid = signed_in.post(url, _muuda(action, drawn, text="", when=datetime.date(2026, 11, 20)))

    assert invalid.status_code == 400
    body = invalid.content.decode()
    assert views.STALE_REVISION_REFUSAL in body, "a stale panel is refused as stale, first"
    assert "Kirjuta järgmine tegevus." not in body
    # Redrawn from the step as it is now: the new day and the new token, never
    # the posted old day under the fresh token.
    current = _open(normal_matter)
    assert _revisions(body) == {views.current_action_revision(current)}
    muuda = body[body.index('id="lisa-jargmine"') :]
    muuda = muuda[: muuda.index("</form>")]
    assert 'value="9.12.2026"' in muuda
    assert 'value="20.11.2026"' not in muuda

    # The corrected save, from the page as it was answered: the old fields
    # with the old token are refused again; nothing reverts the day.
    again = signed_in.post(
        url, _muuda(action, drawn, text="Ootame vastust", when=datetime.date(2026, 11, 20))
    )
    assert again.status_code == 400
    assert views.STALE_REVISION_REFUSAL in again.content.decode()
    current = _open(normal_matter)
    assert (current.pk, current.target_date) == (action.pk, datetime.date(2026, 12, 9))

    # And the redrawn panel's own save goes through, keeping the new day.
    saved = signed_in.post(
        url,
        _muuda(
            current,
            views.current_action_revision(current),
            text="Ootame vastust",
            when=datetime.date(2026, 12, 9),
        ),
    )
    assert saved.status_code == 200
    assert (_open(normal_matter).text, _open(normal_matter).target_date) == (
        "Ootame vastust",
        datetime.date(2026, 12, 9),
    )


@pytest.mark.parametrize("panel", ["review", "complete"])
def test_a_stale_review_or_completion_is_refused_before_validation(
    signed_in, specialist, normal_matter, panel
):
    action = _wait(normal_matter, specialist)
    drawn = views.current_action_revision(action)
    acknowledge_review(action=action, actor=specialist, next_review_date=datetime.date(2026, 12, 9))
    if panel == "review":
        url = reverse(
            "matters:review_action", kwargs={"pk": normal_matter.pk, "action_id": action.pk}
        )
        data = {"next_review_date": "31.02.2026", "revision": drawn}
    else:
        url = reverse("matters:complete_current_action", kwargs={"pk": normal_matter.pk})
        data = {"action_id": str(action.pk), "revision": drawn, "body": ""}

    response = signed_in.post(url, data)

    assert response.status_code == 400
    assert views.STALE_REVISION_REFUSAL in response.content.decode()
    assert _revisions(response.content.decode()) == {
        views.current_action_revision(_open(normal_matter))
    }
    assert _open(normal_matter).target_date == datetime.date(2026, 12, 9)


@pytest.mark.parametrize("panel", ["muuda", "review", "complete"])
def test_a_matter_closed_meanwhile_gets_the_closed_refusal_not_the_stale_one(
    signed_in, specialist, normal_matter, panel
):
    action = _wait(normal_matter, specialist)
    drawn = views.current_action_revision(action)
    close_matter(
        matter=normal_matter, disposition="COMPLETED", actor=specialist, follow_ups_confirmed=True
    )
    if panel == "muuda":
        url = reverse("matters:set_action", kwargs={"pk": normal_matter.pk})
        data = _muuda(action, drawn)
    elif panel == "review":
        url = reverse(
            "matters:review_action", kwargs={"pk": normal_matter.pk, "action_id": action.pk}
        )
        data = {"next_review_date": "4.12.2026", "revision": drawn}
    else:
        url = reverse("matters:complete_current_action", kwargs={"pk": normal_matter.pk})
        data = {"action_id": str(action.pk), "revision": drawn, "body": "Helistasin"}

    response = signed_in.post(url, data)

    assert response.status_code == 400
    body = response.content.decode()
    assert CLOSED_MATTER_REFUSAL in body
    assert views.STALE_REVISION_REFUSAL not in body


def test_the_hold_says_closed_before_stale(specialist, normal_matter):
    action = _wait(normal_matter, specialist)
    drawn = views.current_action_revision(action)
    close_matter(
        matter=normal_matter, disposition="COMPLETED", actor=specialist, follow_ups_confirmed=True
    )
    with refused(CLOSED_MATTER_REFUSAL), transaction.atomic():
        views._hold_the_drawn_revision(normal_matter, action.pk, drawn)


def test_a_stale_completion_with_a_file_stores_nothing(signed_in, specialist, normal_matter):
    action = _wait(normal_matter, specialist)
    drawn = views.current_action_revision(action)
    acknowledge_review(action=action, actor=specialist, next_review_date=datetime.date(2026, 12, 9))
    documents = Document.objects.count()
    stored = _evidence_files()

    response = signed_in.post(
        reverse("matters:complete_current_action", kwargs={"pk": normal_matter.pk}),
        {
            "action_id": str(action.pk),
            "revision": drawn,
            "body": "Saatsin kirja",
            "attachments": SimpleUploadedFile(
                "ministeeriumi-kiri.pdf", b"%PDF-1.4\n% ui10 stale\n%%EOF\n", "application/pdf"
            ),
        },
    )

    assert response.status_code == 400
    assert views.STALE_REVISION_REFUSAL in response.content.decode()
    assert Document.objects.count() == documents
    assert _evidence_files() == stored
    assert _open(normal_matter).pk == action.pk
