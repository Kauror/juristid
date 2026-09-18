"""`Vastuseid` and the completion's evidence, on one round, in one session.

Two fixes reached `main` a few hours apart and they meet on the same
`Kaasamine` row: one made `Lõpeta kaasamine` bind `request.FILES`, so an
attached answer survives instead of vanishing; the other put `Vastuseid` on the
correction form, so a miscounted round has a way back out.

Each has its own module — `tests/test_engagement_feedback_wait.py` and
`tests/test_engagement_correction.py` — and each passed on its own branch.
Neither proves what a lawyer actually does, which is to correct the count and
*then* finish the round. This module asserts the pair: after the second save,
both facts are still on the record, and finishing a round nobody counted does
not invent a count for it.

It exists because the two changes were integrated separately. A regression in
either direction would leave both original modules green.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.core.dates import format_estonian_date
from app.documents.models import Document
from app.matters.enums import EngagementKind
from app.matters.services import add_engagement, engagement_revision_token
from tests import factories

pytestmark = pytest.mark.django_db


def _evidence_of(engagement):
    return Document.objects.filter(links__engagement=engagement)


def _fields(engagement, **changes):
    payload = {
        "kind": engagement.kind,
        "title": engagement.title,
        "response_count": (
            "" if engagement.response_count is None else str(engagement.response_count)
        ),
        "url": engagement.url,
        "smaily_url": engagement.smaily_url,
        "alchemer_url": engagement.alchemer_url,
        "note": engagement.note,
        "occurred_on": (
            format_estonian_date(engagement.occurred_on)
            if engagement.occurred_on and not engagement.has_approximate_date
            else ""
        ),
        "feedback_deadline": (
            format_estonian_date(engagement.feedback_deadline)
            if engagement.feedback_deadline
            else ""
        ),
        "feedback_received": engagement.feedback_received,
        "revision": engagement_revision_token(engagement),
    }
    payload.update(changes)
    return payload


def test_a_count_correction_and_a_file_bearing_completion_on_one_round(signed_in, specialist):
    """QA-03 then QA-02, on the same consultation, in the order a lawyer works.

    The point of the probe: neither fix may undo the other. The count is
    corrected through `Muuda`, then the round is finished with an actual file,
    and afterwards *both* facts have to still be on the record.
    """
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=timezone.localdate() - dt.timedelta(days=1),
        feedback_deadline=timezone.localdate() + dt.timedelta(days=7),
        response_count=7,
        actor=specialist,
    )

    # QA-03: 7 -> 8 through the correction form.
    correct_url = reverse(
        "matters:update_engagement",
        kwargs={"pk": matter.pk, "engagement_id": engagement.pk},
    )
    response = signed_in.post(correct_url, _fields(engagement, response_count="8"))
    assert response.status_code in (200, 204), response.content.decode()[:800]
    engagement.refresh_from_db()
    assert engagement.response_count == 8

    # QA-02: finish the round with a real file attached.
    finish_url = reverse(
        "matters:complete_engagement_feedback",
        kwargs={"pk": matter.pk, "engagement_id": engagement.pk},
    )
    response = signed_in.post(
        finish_url,
        {
            "revision": engagement_revision_token(engagement),
            "feedback_received": "Liikmed vastasid kirjaga.",
            "attachments": SimpleUploadedFile(
                "vastus.pdf", b"%PDF-1.4 vastus", content_type="application/pdf"
            ),
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200, response.content.decode()[:800]

    engagement.refresh_from_db()
    # QA-02 held: the file became evidence on this round.
    assert _evidence_of(engagement).count() == 1
    assert _evidence_of(engagement).get().title == "vastus.pdf"
    assert engagement.feedback_closed_at is not None
    assert engagement.feedback_received == "Liikmed vastasid kirjaga."
    # QA-03 held: completing the round did not reset the corrected count.
    assert engagement.response_count == 8


def test_finishing_with_a_file_does_not_invent_a_count(signed_in, specialist):
    """A round nobody counted stays uncounted after a file-bearing completion."""
    matter = factories.MatterFactory(owner=specialist)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="liikmed",
        occurred_on=timezone.localdate() - dt.timedelta(days=1),
        feedback_deadline=timezone.localdate() + dt.timedelta(days=7),
        actor=specialist,
    )
    assert engagement.response_count is None

    finish_url = reverse(
        "matters:complete_engagement_feedback",
        kwargs={"pk": matter.pk, "engagement_id": engagement.pk},
    )
    response = signed_in.post(
        finish_url,
        {
            "revision": engagement_revision_token(engagement),
            "feedback_received": "",
            "attachments": SimpleUploadedFile(
                "vastus.pdf", b"%PDF-1.4 vastus", content_type="application/pdf"
            ),
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200, response.content.decode()[:800]

    engagement.refresh_from_db()
    assert _evidence_of(engagement).count() == 1
    assert engagement.response_count is None
