"""`✓ Tehtud` on a planned row (docs/adr/0144 §1).

A planned future action can be finished on its own, on any day. What is held:

* it finishes that action and nothing else — the current action keeps its text
  and date, no other planned row moves, nothing is promoted;
* what happened is written as the completion note, files go with it, and the
  chronology reads one «✓» row;
* a stale tab, an empty answer and a reader are refused with nothing written;
* `Muuda` still changes words and day freely (earlier or later), and `×` —
  `Kustuta planeeritud tegevus` — still cancels and keeps history;
* the row draws `✓ Tehtud | Muuda | ×`.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.matters.models import Entry
from app.matters.workspace import complete_planned_action
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from app.workflow.services import (
    PLANNED_ACTION_CHANGED,
    add_planned_action,
    cancel_planned_action,
    change_planned_action,
    set_next_action_for_new_work,
)
from tests.refusals import refused

pytestmark = pytest.mark.django_db


def _day(offset: int):
    return timezone.localdate() + timedelta(days=offset)


def _post(client, name: str, matter, data: dict, **kwargs):
    return client.post(
        reverse(f"matters:{name}", kwargs={"pk": matter.pk, **kwargs}),
        data,
        headers={"HX-Request": "true"},
    )


@pytest.fixture
def plan(normal_matter, specialist):
    current = set_next_action_for_new_work(
        matter=normal_matter, text="Loe eelnõu läbi", target_date=_day(2), actor=specialist
    )
    early = add_planned_action(
        matter=normal_matter, text="Kohtu ministeeriumiga", target_date=_day(10), actor=specialist
    )
    late = add_planned_action(
        matter=normal_matter, text="Saada arvamus", target_date=_day(20), actor=specialist
    )
    return normal_matter, current, early, late


def test_a_future_planned_action_is_finished_before_its_day(plan, specialist):
    matter, _current, early, _late = plan

    result = complete_planned_action(
        matter=matter, author=specialist, action_id=early.pk, body="Kohtumist ei toimunud."
    )

    early.refresh_from_db()
    assert early.status == ActionStatus.COMPLETED
    assert early.ended_by == specialist
    assert early.target_date == _day(10)
    assert "Kohtumist ei toimunud" in result.entry.body


def test_the_current_action_and_the_other_planned_rows_do_not_move(plan, specialist):
    matter, current, early, late = plan
    before = (current.text, current.target_date)

    complete_planned_action(matter=matter, author=specialist, action_id=late.pk, body="Tehtud.")

    current.refresh_from_db()
    early.refresh_from_db()
    assert current.status == ActionStatus.OPEN
    assert (current.text, current.target_date) == before
    assert early.status == ActionStatus.PLANNED
    assert NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).count() == 1


def test_nothing_is_promoted_even_without_a_current_action(normal_matter, specialist):
    current = set_next_action_for_new_work(
        matter=normal_matter, text="Praegune", target_date=_day(1), actor=specialist
    )
    first = add_planned_action(
        matter=normal_matter, text="Esimene", target_date=_day(5), actor=specialist
    )
    second = add_planned_action(
        matter=normal_matter, text="Teine", target_date=_day(6), actor=specialist
    )
    NextAction.objects.filter(pk=current.pk).update(status=ActionStatus.CANCELLED)

    complete_planned_action(
        matter=normal_matter, author=specialist, action_id=first.pk, body="Tehtud."
    )

    second.refresh_from_db()
    assert second.status == ActionStatus.PLANNED
    assert not NextAction.objects.filter(matter=normal_matter, status=ActionStatus.OPEN).exists()


def test_only_one_action_is_completed(plan, specialist):
    matter, _current, early, _late = plan
    before = NextAction.objects.filter(matter=matter, status=ActionStatus.COMPLETED).count()

    complete_planned_action(matter=matter, author=specialist, action_id=early.pk, body="Tehtud.")

    assert (
        NextAction.objects.filter(matter=matter, status=ActionStatus.COMPLETED).count()
        == before + 1
    )


def test_the_completion_is_audited_as_a_planned_completion(plan, specialist):
    matter, _current, early, _late = plan

    complete_planned_action(matter=matter, author=specialist, action_id=early.pk, body="Tehtud.")

    event = ChangeEvent.objects.get(
        event_type=ChangeEventType.NEXT_ACTION_COMPLETED, object_id=early.pk
    )
    assert event.payload == {"kind": early.kind, "planned": True}


def test_files_go_with_the_note(plan, specialist):
    matter, _current, early, _late = plan
    upload = SimpleUploadedFile("memo.pdf", b"%PDF-1.4 memo", content_type="application/pdf")

    result = complete_planned_action(
        matter=matter, author=specialist, action_id=early.pk, body="Kohtusime.", uploads=[upload]
    )

    assert [document.current_version.original_filename for document in result.documents] == [
        "memo.pdf"
    ]


def test_a_restricted_planned_action_gives_its_note_the_restriction(plan, specialist):
    matter, _current, early, _late = plan
    NextAction.objects.filter(pk=early.pk).update(visibility_override=Visibility.RESTRICTED)

    result = complete_planned_action(
        matter=matter, author=specialist, action_id=early.pk, body="Konfidentsiaalne."
    )

    assert result.entry.visibility_override == Visibility.RESTRICTED


def test_a_row_that_is_no_longer_planned_is_refused(plan, specialist):
    matter, _current, early, _late = plan
    cancel_planned_action(matter=matter, action_id=early.pk, actor=specialist)
    entries = Entry.objects.filter(matter=matter).count()

    with refused(PLANNED_ACTION_CHANGED):
        complete_planned_action(matter=matter, author=specialist, action_id=early.pk, body="X")

    assert Entry.objects.filter(matter=matter).count() == entries


def test_the_current_action_cannot_be_finished_through_the_planned_door(plan, specialist):
    matter, current, _early, _late = plan

    with refused(PLANNED_ACTION_CHANGED):
        complete_planned_action(matter=matter, author=specialist, action_id=current.pk, body="X")

    current.refresh_from_db()
    assert current.status == ActionStatus.OPEN


# ---------------------------------------------------------------------------
# Through the page
# ---------------------------------------------------------------------------


def test_the_row_draws_tehtud_muuda_and_kustuta(client, specialist, plan):
    matter, _current, early, _late = plan
    client.force_login(specialist)

    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    row = body[body.index(f'id="planeeritud-tehtud-{early.pk}"') :]
    row = row[: row.index("</li>")]

    assert row.index("✓ Tehtud") < row.index("Muuda") < row.index("×")
    assert 'title="Kustuta planeeritud tegevus"' in row
    assert 'aria-label="Kustuta planeeritud tegevus"' in row
    assert (
        reverse("matters:complete_planned_action", kwargs={"pk": matter.pk, "action_id": early.pk})
        in row
    )


def test_the_panel_finishes_the_named_row(client, specialist, plan):
    matter, current, early, _late = plan
    client.force_login(specialist)

    response = _post(
        client,
        "complete_planned_action",
        matter,
        {"body": "Vajadus langes ära."},
        action_id=early.pk,
    )

    assert response.status_code == 200
    early.refresh_from_db()
    current.refresh_from_db()
    assert early.status == ActionStatus.COMPLETED
    assert current.status == ActionStatus.OPEN
    body = response.content.decode()
    assert "Vajadus langes ära." in body


def test_an_empty_answer_is_refused_in_its_own_row(client, specialist, plan):
    matter, _current, early, _late = plan
    client.force_login(specialist)

    response = _post(client, "complete_planned_action", matter, {"body": ""}, action_id=early.pk)

    assert response.status_code == 400
    body = response.content.decode()
    row = body[body.index(f'id="planeeritud-tehtud-{early.pk}"') :]
    row = row[: row.index("</details>")]
    assert " open>" in row[: row.index("<summary")]
    assert "Kirjelda, mida tegid." in row
    early.refresh_from_db()
    assert early.status == ActionStatus.PLANNED


def test_a_refused_muuda_now_says_why(client, specialist, plan):
    matter, _current, early, _late = plan
    client.force_login(specialist)

    response = _post(
        client,
        "change_planned_action",
        matter,
        {"text": "Kohtu ministeeriumiga", "target_date": ""},
        action_id=early.pk,
    )

    assert response.status_code == 400
    body = response.content.decode()
    row = body[body.index(f'id="planeeritud-{early.pk}"') :]
    row = row[: row.index("</details>")]
    assert "Planeeritud tegevusel peab olema kuupäev." in row


def test_a_reader_cannot_finish_a_planned_action(client, reader, plan):
    matter, _current, early, _late = plan
    client.force_login(reader)

    response = _post(client, "complete_planned_action", matter, {"body": "X"}, action_id=early.pk)

    assert response.status_code in (403, 404)
    early.refresh_from_db()
    assert early.status == ActionStatus.PLANNED


# ---------------------------------------------------------------------------
# Muuda and ×, unchanged in meaning
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("offset", [3, 30], ids=["earlier", "later"])
def test_muuda_moves_the_day_freely(plan, specialist, offset):
    matter, _current, early, _late = plan

    changed = change_planned_action(
        matter=matter,
        action_id=early.pk,
        text="Kohtu ministeeriumiga",
        target_date=_day(offset),
        actor=specialist,
    )

    assert changed.status == ActionStatus.PLANNED
    assert changed.target_date == _day(offset)


def test_kustuta_keeps_the_history(plan, specialist):
    matter, _current, early, _late = plan

    cancel_planned_action(matter=matter, action_id=early.pk, actor=specialist)

    early.refresh_from_db()
    assert early.status == ActionStatus.CANCELLED
    assert ChangeEvent.objects.filter(
        event_type=ChangeEventType.NEXT_ACTION_CANCELLED, object_id=early.pk
    ).exists()
