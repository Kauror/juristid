"""Assessor B (WS1, 2026-10-29): one person's records, reached by another persona.

The product keeps four kinds of per-person rows — `MatterPersonalNote`
(author), `PersonalScratchpad` (user), `MatterAssignmentNotice` (recipient) and
`accounts.DisclosurePreference` (user). Under the shared gate every persona
reads every Matter (docs/adr/0181), so the only line left between colleagues is
*whose* note, pad, receipt or folding a write lands on. This module fires the
routes that could cross that line with a second persona signed in:

* the receipt route is the one that takes the row's id in the path — another
  person's notice, a superseded one and a random id must all be the same 404
  and must leave `viewed_at` untouched;
* the note route takes a Matter id, never an author: B's autosave on A's
  Matter writes B's row and leaves A's exactly as it was;
* the disclosure route takes a registry key, never a person: B's choice cannot
  move A's row, and an unregistered key is refused before any write;
* a READER is refused the personal note on a Matter (`business_write_required`
  wraps `matters:save_note`) while it may keep its own scratchpad — recorded
  here as the current behaviour, with the policy question left to the owner
  (WS1B-FINDINGS, owner question 1).

Status: boundary verified sound (passes on main 359e34bf), except for the last
test, which pins behaviour the owner may decide to change.
"""

from __future__ import annotations

import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from app.accounts.models import DisclosurePreference
from app.matters.models import MatterAssignmentNotice, MatterPersonalNote
from app.matters.services import save_personal_note
from tests import factories

pytestmark = pytest.mark.django_db

NOTE_PREFIX = "markmed"


def _notice_url(notice_id) -> str:
    return reverse("matters:open_assignment_notice", kwargs={"notice_id": notice_id})


@pytest.fixture
def two_people():
    a = factories.UserFactory(display_name="Persoon A")
    b = factories.UserFactory(display_name="Persoon B")
    matter = factories.MatterFactory(owner=a)
    return a, b, matter


def test_another_persons_assignment_notice_is_a_404_and_stays_unread(client, two_people):
    a, b, matter = two_people
    notice = MatterAssignmentNotice.objects.create(matter=matter, recipient=a, assigned_by=b)
    client.force_login(b)

    for target in (notice.pk, uuid.uuid4()):
        response = client.post(_notice_url(target))
        assert response.status_code == 404, target
    notice.refresh_from_db()
    assert notice.viewed_at is None

    # The recipient's own, superseded by a hand-over, is the same 404.
    notice.superseded_at = timezone.now()
    notice.save(update_fields=["superseded_at"])
    client.force_login(a)
    assert client.post(_notice_url(notice.pk)).status_code == 404
    notice.refresh_from_db()
    assert notice.viewed_at is None

    # And the live one resolves for its recipient only.
    live = MatterAssignmentNotice.objects.create(matter=matter, recipient=a, assigned_by=b)
    response = client.post(_notice_url(live.pk))
    assert response.status_code == 302
    live.refresh_from_db()
    assert live.viewed_at is not None


def test_a_personal_note_autosave_cannot_touch_a_colleagues_note(client, two_people):
    a, b, matter = two_people
    mine = save_personal_note(matter=matter, author=a, body="A salajane mõte")
    client.force_login(b)

    response = client.post(
        reverse("matters:save_note", kwargs={"pk": matter.pk}),
        {f"{NOTE_PREFIX}-body": "B kirjutas üle", f"{NOTE_PREFIX}-revision": ""},
    )

    assert response.status_code == 200
    mine.refresh_from_db()
    assert mine.body == "A salajane mõte"
    assert MatterPersonalNote.objects.filter(matter=matter, author=b).get().body == "B kirjutas üle"
    assert MatterPersonalNote.objects.filter(matter=matter).count() == 2


def test_a_remembered_block_is_the_senders_own(client, two_people):
    a, b, _ = two_people
    DisclosurePreference.objects.create(user=a, key="my_work.statistics", is_open=False)
    client.force_login(b)

    assert (
        client.post(
            reverse("accounts:save_disclosure"), {"key": "my_work.statistics", "open": "1"}
        ).status_code
        == 204
    )
    assert DisclosurePreference.objects.get(user=a, key="my_work.statistics").is_open is False
    assert DisclosurePreference.objects.get(user=b, key="my_work.statistics").is_open is True

    # No person parameter is read, and an unregistered key writes nothing.
    before = DisclosurePreference.objects.count()
    refused = client.post(
        reverse("accounts:save_disclosure"),
        {"key": f"matter.{uuid.uuid4()}", "open": "1", "user": str(a.pk), "user_id": str(a.pk)},
    )
    assert refused.status_code == 400
    assert DisclosurePreference.objects.count() == before

    # Reset is the sender's own rows only.
    client.post(reverse("accounts:reset_disclosures"), {})
    assert DisclosurePreference.objects.filter(user=a).exists()
    assert not DisclosurePreference.objects.filter(user=b).exists()


def test_a_reader_keeps_a_scratchpad_but_is_refused_a_matter_note(client, two_people):
    """Current behaviour, pinned — the policy is the owner's (WS1B owner question 1).

    `matters:save_note` sits behind `business_write_required`, so a READER —
    an authorized internal reader since docs/adr/0181 — cannot keep private
    `Märkmed` on a Matter it reads, although `matters:save_scratchpad`, the
    same kind of private row, is open to it (`CLASSIFIED_ELSEWHERE`, class C).
    """
    _, _, matter = two_people
    reader = factories.ReaderFactory()
    client.force_login(reader)

    pad = client.post(
        reverse("matters:save_scratchpad"), {"body": "lugeja märkmik", "revision": ""}
    )
    assert pad.status_code == 200

    note = client.post(
        reverse("matters:save_note", kwargs={"pk": matter.pk}),
        {f"{NOTE_PREFIX}-body": "lugeja märge", f"{NOTE_PREFIX}-revision": ""},
    )
    assert note.status_code == 404
    assert not MatterPersonalNote.objects.filter(author=reader).exists()
