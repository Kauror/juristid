"""A closed Matter keeps its `Hetkeseis` until `Ava uuesti` (RULE-03, docs/adr/0131 §12).

The ordinary stage door — `change_stage`, behind `Muuda teemat` and the header's
own control — moved the stage of a closed file: a new period, the terminal one
ended, a `MATTER_STAGE_CHANGED` in the history of a file that says it is shut.
`reopen_matter_into_stage` is the one way a closed file's stage moves, and it
reopens the file in the same act.

* the same stage again is no move, so a correction of anything else on a closed
  file still saves;
* clearing the stage is a move;
* the decision is the committed row's, not the instance a stale tab arrived with;
* `Muuda teemat` is one save: a refused stage refuses the title beside it.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.core.invariants import check_domain_invariants
from app.matters.models import Matter, MatterStageEpisode
from app.matters.services import (
    CLOSED_MATTER_STAGE_REFUSAL,
    REOPEN_INTO_TERMINAL_STAGE,
    change_stage,
    close_matter,
    create_matter,
    reopen_matter_into_stage,
)
from app.workflow.enums import Disposition
from app.workflow.models import StageVocabulary
from tests.test_oigusakt_field import edit_payload

pytestmark = pytest.mark.django_db


def _stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def _closed(author, *, terminal: str = "in_force", before: str = "consultation") -> Matter:
    """A file closed the ordinary way: a `Hetkeseis` that ends it."""
    matter = create_matter(
        title="Pakendiseaduse muudatus", actor=author, owner=author, stage=_stage(before)
    )
    change_stage(matter=matter, stage=_stage(terminal), actor=author)
    matter.refresh_from_db()
    assert not matter.is_open
    return matter


def _state(matter: Matter) -> dict[str, object]:
    """Everything a refused stage move must leave exactly as it was."""
    row = Matter.objects.get(pk=matter.pk)
    current = MatterStageEpisode.objects.filter(matter=matter, is_current=True).first()
    return {
        "title": row.title,
        "is_open": row.is_open,
        "disposition": row.disposition,
        "closed_at": row.closed_at,
        "stage": row.stage_id,
        "episodes": MatterStageEpisode.objects.filter(matter=matter).count(),
        "current": current.pk if current else None,
        "current_ended_at": current.ended_at if current else None,
        "events": ChangeEvent.objects.filter(matter=matter).count(),
    }


def _events(matter: Matter, event_type: str) -> int:
    return ChangeEvent.objects.filter(matter=matter, event_type=event_type).count()


def _refused_cleanly(matter: Matter, before: dict[str, object]) -> None:
    assert _state(matter) == before
    assert check_domain_invariants().ok


# ---------------------------------------------------------------------------
# A, B, C — the ordinary door
# ---------------------------------------------------------------------------


def test_a_closed_matter_refuses_an_ordinary_stage_move(specialist):
    matter = _closed(specialist)
    before = _state(matter)
    moves = _events(matter, ChangeEventType.MATTER_STAGE_CHANGED)

    with pytest.raises(DomainError, match=re.escape(CLOSED_MATTER_STAGE_REFUSAL)):
        change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)

    _refused_cleanly(matter, before)
    assert before["disposition"] == Disposition.COMPLETED
    assert _events(matter, ChangeEventType.MATTER_STAGE_CHANGED) == moves
    assert _events(matter, ChangeEventType.MATTER_REOPENED) == 0


def test_the_same_stage_on_a_closed_matter_is_no_move(specialist):
    matter = _closed(specialist)
    before = _state(matter)

    change_stage(matter=matter, stage=_stage("in_force"), actor=specialist)

    _refused_cleanly(matter, before)


def test_clearing_the_stage_of_a_closed_matter_is_a_move(specialist):
    matter = _closed(specialist)
    before = _state(matter)

    with pytest.raises(DomainError, match=re.escape(CLOSED_MATTER_STAGE_REFUSAL)):
        change_stage(matter=matter, stage=None, actor=specialist)

    _refused_cleanly(matter, before)


# ---------------------------------------------------------------------------
# D — the stale tab
# ---------------------------------------------------------------------------


def test_a_stale_open_copy_cannot_move_a_file_closed_since(specialist, other_specialist):
    matter = create_matter(
        title="Pakendiseaduse muudatus",
        actor=specialist,
        owner=specialist,
        stage=_stage("consultation"),
    )
    alice_copy = Matter.objects.get(pk=matter.pk)
    assert alice_copy.is_open and alice_copy.stage.key == "consultation"

    # Bob closes it through the stage, from his own copy.
    change_stage(
        matter=Matter.objects.get(pk=matter.pk), stage=_stage("in_force"), actor=other_specialist
    )
    before = _state(matter)
    assert before["is_open"] is False

    # Alice's copy still says «open, Kooskõlastusringil».
    with pytest.raises(DomainError, match=re.escape(CLOSED_MATTER_STAGE_REFUSAL)):
        change_stage(matter=alice_copy, stage=_stage("parliament"), actor=specialist)

    _refused_cleanly(matter, before)
    row = Matter.objects.get(pk=matter.pk)
    assert row.stage.key == "in_force"
    assert _events(matter, ChangeEventType.MATTER_REOPENED) == 0


def test_a_stale_header_save_is_refused_and_moves_nothing(client, specialist):
    matter = _closed(specialist)
    before = _state(matter)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "stage"}),
        {"stage": str(_stage("parliament").pk)},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 400
    assert CLOSED_MATTER_STAGE_REFUSAL in response.content.decode()
    _refused_cleanly(matter, before)


# ---------------------------------------------------------------------------
# E, F — `Muuda teemat` is one save
# ---------------------------------------------------------------------------


def test_muuda_teemat_still_corrects_a_closed_file_whose_stage_it_echoes(client, specialist):
    matter = _closed(specialist)
    before = _state(matter)
    moves = _events(matter, ChangeEventType.MATTER_STAGE_CHANGED)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        edit_payload(matter, title="Pakendiseaduse muudatus (parandatud)"),
    )

    assert response.status_code == 302
    after = _state(matter)
    assert after["title"] == "Pakendiseaduse muudatus (parandatud)"
    for key in ("is_open", "disposition", "stage", "episodes", "current", "current_ended_at"):
        assert after[key] == before[key], key
    assert _events(matter, ChangeEventType.MATTER_STAGE_CHANGED) == moves
    assert _events(matter, ChangeEventType.MATTER_REOPENED) == 0


def test_muuda_teemat_refuses_a_stage_move_and_the_title_beside_it(client, specialist):
    matter = _closed(specialist)
    before = _state(matter)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        edit_payload(
            matter,
            title="Pakendiseaduse muudatus (parandatud)",
            stage=str(_stage("parliament").pk),
        ),
    )

    assert response.status_code == 400
    assert CLOSED_MATTER_STAGE_REFUSAL in response.content.decode()
    _refused_cleanly(matter, before)


def test_muuda_teemat_refuses_clearing_a_closed_files_stage(client, specialist):
    matter = _closed(specialist)
    before = _state(matter)
    client.force_login(specialist)

    response = client.post(
        reverse("matters:matter_edit", kwargs={"pk": matter.pk}),
        edit_payload(matter, title="Uus pealkiri", stage=""),
    )

    assert response.status_code == 400
    _refused_cleanly(matter, before)


# ---------------------------------------------------------------------------
# G — what the pages offer
# ---------------------------------------------------------------------------


def _stage_slot(body: str) -> str:
    return re.search(
        r'<span class="metaline__item" id="teema-hetkeseis".*?\n</span>', body, re.S
    ).group(0)


def test_a_closed_header_states_the_stage_and_offers_ava_uuesti(signed_in, specialist):
    matter = _closed(specialist)

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    slot = _stage_slot(body)
    assert "Jõustunud" in slot
    assert "inlineedit" not in slot and 'name="stage"' not in slot
    assert reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "stage"}) not in body
    banner = re.search(r'<div class="banner banner--closed">.*?</form>', body, re.S).group(0)
    assert "Ava uuesti" in banner and 'name="stage"' in banner


def test_an_open_header_keeps_its_stage_editor(signed_in, specialist):
    matter = create_matter(
        title="Pakendiseaduse muudatus",
        actor=specialist,
        owner=specialist,
        stage=_stage("consultation"),
    )

    body = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    slot = _stage_slot(body)
    assert "inlineedit" in slot and 'name="stage"' in slot
    assert reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "stage"}) in slot


def test_muuda_teemat_shows_a_closed_files_stage_without_offering_a_move(signed_in, specialist):
    matter = _closed(specialist)

    body = signed_in.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk})).content.decode()

    # The stage is stated and travels unchanged; no other stage is a choice.
    assert re.search(rf'<input type="hidden" name="stage" value="{_stage("in_force").pk}"', body), (
        "the held stage must post back unchanged"
    )
    assert 'type="radio" name="stage"' not in body
    assert CLOSED_MATTER_STAGE_REFUSAL in body


def test_muuda_teemat_on_an_open_file_keeps_the_stage_choices(signed_in, specialist):
    matter = create_matter(
        title="Pakendiseaduse muudatus",
        actor=specialist,
        owner=specialist,
        stage=_stage("consultation"),
    )

    body = signed_in.get(reverse("matters:matter_edit", kwargs={"pk": matter.pk})).content.decode()

    assert 'type="radio" name="stage"' in body
    assert CLOSED_MATTER_STAGE_REFUSAL not in body


# ---------------------------------------------------------------------------
# H, I, J — `Ava uuesti` is the exception, and stays exactly what it was
# ---------------------------------------------------------------------------


def test_ava_uuesti_still_moves_the_stage_and_reopens(specialist):
    matter = _closed(specialist)
    terminal = MatterStageEpisode.objects.get(matter=matter, is_current=True)

    reopen_matter_into_stage(matter=matter, stage=_stage("parliament"), actor=specialist)

    row = Matter.objects.get(pk=matter.pk)
    assert row.is_open and row.stage.key == "parliament" and row.disposition == ""
    terminal.refresh_from_db()
    assert not terminal.is_current and terminal.ended_at is not None
    current = MatterStageEpisode.objects.get(matter=matter, is_current=True)
    assert current.stage.key == "parliament" and current.sequence == terminal.sequence + 1
    reopened = ChangeEvent.objects.filter(matter=matter, event_type=ChangeEventType.MATTER_REOPENED)
    assert reopened.count() == 1
    assert reopened.get().stage_episode_id == current.pk
    assert check_domain_invariants().ok


def test_ava_uuesti_into_a_terminal_stage_is_still_refused(specialist):
    matter = _closed(specialist, terminal="monitoring_stopped")
    before = _state(matter)

    with pytest.raises(DomainError, match=re.escape(REOPEN_INTO_TERMINAL_STAGE)):
        reopen_matter_into_stage(matter=matter, stage=_stage("in_force"), actor=specialist)

    _refused_cleanly(matter, before)


def test_a_file_closed_on_an_ordinary_stage_keeps_it_until_ava_uuesti(specialist):
    """Legacy: closed before `Hetkeseis` could close anything, so the stage is not terminal."""
    matter = create_matter(
        title="Vana toimik", actor=specialist, owner=specialist, stage=_stage("government")
    )
    close_matter(matter=matter, disposition=Disposition.OTHER, actor=specialist)
    before = _state(matter)

    with pytest.raises(DomainError, match=re.escape(CLOSED_MATTER_STAGE_REFUSAL)):
        change_stage(matter=matter, stage=_stage("parliament"), actor=specialist)
    _refused_cleanly(matter, before)

    # Reopening into the stage it holds continues that period (docs/adr/0131 §12) …
    reopen_matter_into_stage(matter=matter, stage=_stage("government"), actor=specialist)
    row = Matter.objects.get(pk=matter.pk)
    assert row.is_open and row.stage.key == "government"
    assert MatterStageEpisode.objects.filter(matter=matter).count() == before["episodes"]
    assert MatterStageEpisode.objects.get(matter=matter, is_current=True).pk == before["current"]
    # … and once open, the ordinary door is open again.
    change_stage(matter=row, stage=_stage("parliament"), actor=specialist)
    assert Matter.objects.get(pk=matter.pk).stage.key == "parliament"
