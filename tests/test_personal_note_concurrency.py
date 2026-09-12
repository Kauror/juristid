"""Two tabs, one note, and no silent overwrite.

Two tabs on one Teema is the ordinary way a lawyer works. Tab A wrote a note and
saved it; tab B, still holding the text as it was before that, autosaved 900 ms
after its own next keystroke — and last-write-wins overwrote A. No warning, no
copy kept, and nothing anywhere from which to recover it: the personal note and
the desk pad are the only writes in the product that file no `ChangeEvent`, so
the overwritten version was simply gone (adversarial QA 2026-09-12, QA-09).

That is persisted data loss, and it is the one thing an autosave must not be able
to do. So every rendered box carries the version it was filled from, and a save
whose version is not the stored one writes nothing and says so.

**What is deliberately not here.** No global optimistic locking of canonical
business editing — that is a much larger contract with a much larger surface, and
nothing about it is settled by this round. These two pads, and the reason they
come first, is that they are the two writes with no history behind them.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.core.errors import DomainError
from app.matters import person_work
from app.matters.models import MatterPersonalNote, PersonalScratchpad
from app.matters.services import (
    PersonalNoteConflict,
    personal_note_revision,
    save_personal_note,
)
from app.matters.views import NOTE_PREFIX

pytestmark = pytest.mark.django_db

A_TEXT = "Helistasin ministeeriumisse: tähtaeg pikeneb kahe nädala võrra."
B_TEXT = "Küsi Liinalt, kas see on direktiivi nõue."

SAVE_NOTE = "matters:save_note"
SAVE_PAD = "matters:save_scratchpad"


def _note_payload(body: str, revision: str) -> dict[str, str]:
    return {f"{NOTE_PREFIX}-body": body, f"{NOTE_PREFIX}-revision": revision}


def _post_note(client, matter, body: str, revision: str):
    return client.post(
        reverse(SAVE_NOTE, kwargs={"pk": matter.pk}),
        _note_payload(body, revision),
        headers={"HX-Request": "true"},
    )


def _rendered_revision(client, matter) -> str:
    """The token the page hands the browser, read off the rendered form."""
    import re

    body = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()
    field = re.search(rf'<input[^>]*name="{NOTE_PREFIX}-revision"[^>]*>', body)
    assert field is not None, "the note form renders no revision field"
    # Django's `HiddenInput` omits `value` entirely when there is nothing to put
    # in it, which is the honest markup for a note that has never been saved —
    # and the browser posts such a field as the empty string either way.
    found = re.search(r'value="([^"]*)"', field.group(0))
    return found.group(1) if found else ""


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


def test_a_first_note_saves_against_the_empty_revision(normal_matter, specialist):
    """A pad that has never been written has no version, and «» says so."""
    record = save_personal_note(
        matter=normal_matter, author=specialist, body=A_TEXT, expected_revision=""
    )

    assert record.body == A_TEXT
    assert personal_note_revision(record) != ""


def test_saving_against_the_current_revision_works(normal_matter, specialist):
    first = save_personal_note(
        matter=normal_matter, author=specialist, body=A_TEXT, expected_revision=""
    )

    second = save_personal_note(
        matter=normal_matter,
        author=specialist,
        body=B_TEXT,
        expected_revision=personal_note_revision(first),
    )

    assert second.body == B_TEXT
    assert personal_note_revision(second) != personal_note_revision(first)


def test_a_stale_revision_is_refused_and_writes_nothing(normal_matter, specialist):
    """The two-tab case, at the seam that owns it.

    A and B both loaded R1. A saved, so the row is R2. B's save carries R1.
    """
    stale = personal_note_revision(
        save_personal_note(
            matter=normal_matter, author=specialist, body="Esimene", expected_revision=""
        )
    )
    save_personal_note(
        matter=normal_matter, author=specialist, body=A_TEXT, expected_revision=stale
    )

    with pytest.raises(PersonalNoteConflict) as raised:
        save_personal_note(
            matter=normal_matter, author=specialist, body=B_TEXT, expected_revision=stale
        )

    # Nothing was written, and the conflict carries what is stored so the person
    # can see the other side of it.
    assert MatterPersonalNote.objects.get(matter=normal_matter, author=specialist).body == A_TEXT
    assert raised.value.current.body == A_TEXT


def test_a_stale_first_save_is_refused_too(normal_matter, specialist):
    """B's box was empty when it loaded; A has since created the row.

    The «no version» token is a version like any other, so a second tab that
    never saw the first note cannot silently replace it.
    """
    save_personal_note(matter=normal_matter, author=specialist, body=A_TEXT, expected_revision="")

    with pytest.raises(PersonalNoteConflict):
        save_personal_note(
            matter=normal_matter, author=specialist, body=B_TEXT, expected_revision=""
        )

    assert MatterPersonalNote.objects.get(matter=normal_matter, author=specialist).body == A_TEXT


def test_a_caller_with_no_opinion_still_saves(normal_matter, specialist):
    """`expected_revision=None` is for creating a Matter and its first note in
    one act, where there is no earlier version for a second tab to hold."""
    save_personal_note(matter=normal_matter, author=specialist, body=A_TEXT)
    record = save_personal_note(matter=normal_matter, author=specialist, body=B_TEXT)

    assert record.body == B_TEXT


def test_two_people_on_one_matter_do_not_conflict_with_each_other(
    normal_matter, specialist, other_specialist
):
    """One row per author, so these are two notes and not one.

    A conflict between two people here would be this fix inventing a collision
    the model does not have.
    """
    mine = save_personal_note(
        matter=normal_matter, author=specialist, body=A_TEXT, expected_revision=""
    )
    theirs = save_personal_note(
        matter=normal_matter, author=other_specialist, body=B_TEXT, expected_revision=""
    )

    assert mine.pk != theirs.pk
    save_personal_note(
        matter=normal_matter,
        author=specialist,
        body="Uuendatud",
        expected_revision=personal_note_revision(mine),
    )
    theirs.refresh_from_db()
    assert theirs.body == B_TEXT


def test_the_note_still_files_no_history(normal_matter, specialist):
    """The one property this change must not have quietly acquired."""
    from app.audit.models import ChangeEvent

    before = ChangeEvent.objects.count()
    save_personal_note(matter=normal_matter, author=specialist, body=A_TEXT, expected_revision="")

    assert ChangeEvent.objects.count() == before


# ---------------------------------------------------------------------------
# The route, which is what the browser meets
# ---------------------------------------------------------------------------


def test_the_route_saves_and_hands_back_the_new_revision(signed_in, normal_matter, specialist):
    revision = _rendered_revision(signed_in, normal_matter)

    response = _post_note(signed_in, normal_matter, A_TEXT, revision)

    assert response.status_code == 200
    body = response.content.decode()
    assert "Salvestatud" in body
    # The token moves on, or the next autosave would arrive holding the version
    # this one just replaced and conflict with itself.
    record = MatterPersonalNote.objects.get(matter=normal_matter, author=specialist)
    assert f'name="{NOTE_PREFIX}-revision"' in body
    assert f'value="{personal_note_revision(record)}"' in body
    assert personal_note_revision(record) != revision


def test_the_stale_tab_is_answered_409_and_overwrites_nothing(signed_in, normal_matter, specialist):
    """The whole finding, end to end, through the route the browser posts to.

    A loads R1. B loads R1. A saves A2, so the server is R2. B sends B2 with R1.
    """
    shared = _rendered_revision(signed_in, normal_matter)
    _post_note(signed_in, normal_matter, A_TEXT, shared)

    refused = _post_note(signed_in, normal_matter, B_TEXT, shared)
    body = refused.content.decode()

    assert refused.status_code == 409
    # The database still holds A's version.
    assert MatterPersonalNote.objects.get(matter=normal_matter, author=specialist).body == A_TEXT
    # B is told, in a sentence about what happened.
    assert "teises aknas" in body.lower()
    # And A's newer text is recoverable without anything replacing B's.
    assert A_TEXT in body
    # The textarea is not in the answer, so B's own words stay in the box.
    assert "<textarea" not in body


def test_the_refused_answer_does_not_adopt_the_newer_revision(signed_in, normal_matter, specialist):
    """No silent retry.

    Handing back the newer token would mean the next keystroke overwrites what
    the other tab saved — the defect with one more step in it.
    """
    shared = _rendered_revision(signed_in, normal_matter)
    saved = _post_note(signed_in, normal_matter, A_TEXT, shared)
    current = MatterPersonalNote.objects.get(matter=normal_matter, author=specialist)

    refused = _post_note(signed_in, normal_matter, B_TEXT, shared).content.decode()

    assert personal_note_revision(current) not in refused
    assert saved.status_code == 200


def test_a_second_stale_save_is_refused_again_rather_than_applied(
    signed_in, normal_matter, specialist
):
    """Repeating the autosave does not wear the boundary down.

    The textarea is untouched, so htmx fires again on the next keystroke with the
    same stale token — and the answer has to be the same one every time.
    """
    shared = _rendered_revision(signed_in, normal_matter)
    _post_note(signed_in, normal_matter, A_TEXT, shared)

    for attempt in range(3):
        refused = _post_note(signed_in, normal_matter, f"{B_TEXT} {attempt}", shared)
        assert refused.status_code == 409

    assert MatterPersonalNote.objects.get(matter=normal_matter, author=specialist).body == A_TEXT


def test_reloading_the_page_lets_the_save_work_again(signed_in, normal_matter, specialist):
    """The way out is a reload, and it has to actually be a way out."""
    shared = _rendered_revision(signed_in, normal_matter)
    _post_note(signed_in, normal_matter, A_TEXT, shared)
    assert _post_note(signed_in, normal_matter, B_TEXT, shared).status_code == 409

    fresh = _rendered_revision(signed_in, normal_matter)
    accepted = _post_note(signed_in, normal_matter, B_TEXT, fresh)

    assert accepted.status_code == 200
    assert MatterPersonalNote.objects.get(matter=normal_matter, author=specialist).body == B_TEXT


def test_the_conflict_text_is_escaped(signed_in, normal_matter, specialist):
    """It is somebody's own typing, rendered back into the page they are reading."""
    shared = _rendered_revision(signed_in, normal_matter)
    _post_note(signed_in, normal_matter, "<script>window.__paha=1</script>", shared)

    refused = _post_note(signed_in, normal_matter, B_TEXT, shared).content.decode()

    assert "<script>window.__paha" not in refused
    assert "&lt;script&gt;" in refused


# ---------------------------------------------------------------------------
# Minu asjad's desk pad, which shares the shape and not the model
# ---------------------------------------------------------------------------


def test_the_desk_pad_refuses_a_stale_autosave(specialist):
    person_work.save_scratchpad(specialist, "Esimene", expected_revision="")
    stale = person_work.scratchpad_revision(person_work.scratchpad_for(specialist))
    person_work.save_scratchpad(specialist, A_TEXT, expected_revision=stale)

    with pytest.raises(person_work.ScratchpadConflict) as raised:
        person_work.save_scratchpad(specialist, B_TEXT, expected_revision=stale)

    assert PersonalScratchpad.objects.get(user=specialist).body == A_TEXT
    assert raised.value.current is not None
    assert raised.value.current.body == A_TEXT


def test_the_desk_pad_route_answers_409_and_keeps_the_newer_text(signed_in, specialist):
    first = signed_in.post(reverse(SAVE_PAD), {"body": A_TEXT, "revision": ""})
    assert first.status_code == 200

    refused = signed_in.post(reverse(SAVE_PAD), {"body": B_TEXT, "revision": ""})
    body = refused.content.decode()

    assert refused.status_code == 409
    assert PersonalScratchpad.objects.get(user=specialist).body == A_TEXT
    assert "teises aknas" in body.lower()
    assert A_TEXT in body
    assert "<textarea" not in body


def test_the_desk_pad_still_refuses_a_body_it_cannot_hold(specialist):
    """The length cap is unchanged: it truncates, it does not raise.

    Asserted because the save path was rewritten around it, and a pad that
    started refusing a long note instead of trimming it would be a new defect
    dressed as this fix.
    """
    row = person_work.save_scratchpad(
        specialist, "x" * (person_work.SCRATCHPAD_MAX_LENGTH + 50), expected_revision=""
    )

    assert len(row.body) == person_work.SCRATCHPAD_MAX_LENGTH


def test_an_anonymous_note_save_is_still_a_domain_refusal(normal_matter):
    """Unchanged, and not swallowed by the new conflict branch."""
    with pytest.raises(DomainError):
        save_personal_note(matter=normal_matter, author=None, body=A_TEXT, expected_revision="")
