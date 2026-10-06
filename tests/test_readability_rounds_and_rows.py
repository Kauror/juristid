"""Stage III of the historical-regression round: a round says what came back,
and a system row says what it did.

* UX-006 / F-017 — a `Kaasamine` lists the answers linked to it (only those the
  reader may see), keeps its reply-by day after it is finished, and takes a file
  while it is still open without being finished by it;
* UX-011 — a row whose only content was «määras järgmise sammu» names the step,
  in the words it had when it was set.

UX-012's date-and-kind filter was removed again by docs/adr/0140 §8, and its
tests with it (`tests/test_ui_cleanup_teema.py` asserts it is gone).
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.documents.links import DocumentLink
from app.matters.enums import EngagementKind
from app.matters.services import add_engagement, record_external_position
from app.matters.timeline import matter_timeline
from app.workflow.enums import ActionStatus
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


def _day(n: int) -> dt.date:
    return timezone.localdate() + dt.timedelta(days=n)


def _detail(client, matter, query: str = "") -> str:
    return client.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk}) + query
    ).content.decode()


def _round(matter, actor, *, title="Liikmete küsitlus", deadline_days=5):
    return add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title=title,
        occurred_on=_day(-3),
        feedback_deadline=_day(deadline_days),
        actor=actor,
    )


# ---------------------------------------------------------------------------
# III.1 — the round
# ---------------------------------------------------------------------------


def test_a_round_lists_the_answers_linked_to_it(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, specialist)
    member = factories.OrganisationFactory(name="Sünteetiline Liit")
    record_external_position(
        matter=matter,
        organisation=member,
        stated_on=_day(-1),
        summary="Toetame.",
        engagement=engagement,
        actor=specialist,
    )

    body = _detail(signed_in, matter)
    start = body.index(f'id="kaasamine-{engagement.pk}-sisu"')
    row = body[start : body.index("</article>", start)]

    assert "Seotud seisukohti 1" in row
    assert "Sünteetiline Liit" in row
    assert "Vastuseid 0" not in row


def test_a_restricted_answer_is_neither_listed_nor_counted(client, specialist, reader):
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, specialist)
    record_external_position(
        matter=matter,
        organisation=factories.OrganisationFactory(name="Avalik Liit"),
        stated_on=_day(-2),
        summary="Avalik seisukoht.",
        engagement=engagement,
        actor=specialist,
    )
    hidden = record_external_position(
        matter=matter,
        organisation=factories.OrganisationFactory(name="Salajane Liit"),
        stated_on=_day(-1),
        summary="Salajane seisukoht.",
        engagement=engagement,
        actor=specialist,
    )
    type(hidden).objects.filter(pk=hidden.pk).update(visibility_override=Visibility.RESTRICTED)

    client.force_login(reader)
    body = _detail(client, matter)

    assert "Salajane Liit" not in body
    assert "Seotud seisukohti 1" in body


def test_a_finished_round_keeps_its_reply_by_day(signed_in, specialist):
    from app.matters.services import complete_engagement_feedback

    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, specialist, deadline_days=2)
    complete_engagement_feedback(engagement=engagement, actor=specialist)

    body = _detail(signed_in, matter)

    day = _day(2)
    assert f"tagasiside tähtaeg oli {day.day}.{day.month}.{day.year}" in body


def test_a_file_reaches_an_open_round_and_finishes_nothing(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, specialist)
    action = set_next_action(matter=matter, text="Koondan vastused", actor=specialist)

    response = signed_in.post(
        reverse(
            "matters:add_engagement_evidence",
            kwargs={"pk": matter.pk, "engagement_id": engagement.pk},
        ),
        {
            "attachments": SimpleUploadedFile(
                "küsitluse-eksport.pdf",
                b"%PDF-1.4 synthetic export",
                content_type="application/pdf",
            )
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert DocumentLink.objects.filter(engagement=engagement).count() == 1
    engagement.refresh_from_db()
    action.refresh_from_db()
    assert engagement.feedback_closed_at is None
    assert action.status == ActionStatus.OPEN


def test_a_finished_round_takes_no_file_here(signed_in, specialist):
    from app.matters.services import complete_engagement_feedback

    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, specialist)
    complete_engagement_feedback(engagement=engagement, actor=specialist)

    response = signed_in.post(
        reverse(
            "matters:add_engagement_evidence",
            kwargs={"pk": matter.pk, "engagement_id": engagement.pk},
        ),
        {
            "attachments": SimpleUploadedFile(
                "hiline.pdf", b"%PDF-1.4 x", content_type="application/pdf"
            )
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    assert not DocumentLink.objects.filter(engagement=engagement).exists()
    # The form targets the whole column, so the refusal answers with the whole
    # column — never one round row in its place — and says why on this row.
    page = response.content.decode()
    assert 'id="teema-vaade"' in page
    assert 'role="alert"' in page


# ---------------------------------------------------------------------------
# III.2 — a system row names the step it set, as it was then
# ---------------------------------------------------------------------------


def test_a_step_row_names_the_step_in_its_own_words_of_the_time(specialist):
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(matter=matter, text="Ootan ministeeriumi analüüsi", actor=specialist)
    # A later step replaces it; the earlier row still says what it said then.
    set_next_action(matter=matter, text="Koostan arvamuse", actor=specialist)

    rows, _more = matter_timeline(matter=matter, user=specialist)
    sentences = [row.summary_sentence for row in rows if not row.is_entry]

    # One line since the owner's UX round (docs/adr/0143): «Järgmine samm – …».
    assert any("Järgmine samm – Ootan ministeeriumi analüüsi" in s for s in sentences)


def test_a_file_on_a_restricted_round_is_restricted_with_it(specialist):
    """docs/adr/0129 §4's rule for an opinion's working file, for this new door."""
    from app.matters.models import MatterEngagement
    from app.matters.workspace import add_engagement_evidence

    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, specialist)
    MatterEngagement.objects.filter(pk=engagement.pk).update(
        visibility_override=Visibility.RESTRICTED
    )
    engagement.refresh_from_db()

    result = add_engagement_evidence(
        engagement=engagement,
        author=specialist,
        uploads=[SimpleUploadedFile("piiratud.pdf", b"%PDF-1.4 r", content_type="application/pdf")],
    )

    assert [d.visibility_override for d in result.documents] == [Visibility.RESTRICTED]
