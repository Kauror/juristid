"""`PRAEGUNE TEGEVUS` follows a round's own answer without a reload (F-003).

`Lõpeta kaasamine` without `Märgi praegune tegevus tehtuks` swaps only the
round's chronology row. The waiting lines in `PRAEGUNE TEGEVUS` above the task
were drawn once with the page, so «Ootame tagasisidet» stayed for a round that
had just been finished until the lawyer reloaded — seen in the historical
replay and in production QA.

The answer now carries those lines again, out of band, read after the write:
the finished round leaves them, every other open round stays, and nothing else
in the zone is replaced. The ticked path still answers with the whole column.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.enums import EngagementKind
from app.matters.services import add_engagement, close_matter, engagement_revision_token
from app.workflow.enums import Disposition
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

OOB_BOX = re.compile(
    r'<div id="praegune-ootused" class="curact__waits" hx-swap-oob="true">(.*?)</div>', re.S
)


def _round(matter, title, *, actor, deadline_days: int | None = 7):
    deadline = (
        timezone.localdate() + dt.timedelta(days=deadline_days)
        if deadline_days is not None
        else None
    )
    return add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title=title,
        occurred_on=timezone.localdate() - dt.timedelta(days=1),
        feedback_deadline=deadline,
        actor=actor,
    )


def _finish(client, engagement, *, htmx: bool = True, **fields):
    engagement.refresh_from_db()
    payload = {"revision": engagement_revision_token(engagement)}
    payload.update(fields)
    headers = {"HX-Request": "true"} if htmx else {}
    return client.post(
        reverse(
            "matters:complete_engagement_feedback",
            kwargs={"pk": engagement.matter_id, "engagement_id": engagement.pk},
        ),
        payload,
        headers=headers,
    )


def _waiting_lines(response) -> str:
    match = OOB_BOX.search(response.content.decode())
    assert match is not None, "the answer carries no waiting lines"
    return match.group(1)


@pytest.mark.parametrize("deadline_days", [7, None], ids=["with-deadline", "no-deadline"])
def test_finishing_a_round_removes_its_wait_from_current_work(signed_in, specialist, deadline_days):
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, "Liikmete küsitlus", actor=specialist, deadline_days=deadline_days)
    page = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    # A line is named by its round — `data-feedback-wait` — since the
    # audience text left the line (owner's round, 2026-10-07).
    line = f'data-feedback-wait="{engagement.pk}"'
    assert 'id="praegune-ootused"' in page
    assert line in page.split('id="praegune-ootused"', 1)[1].split("</div>", 1)[0]

    response = _finish(signed_in, engagement)

    assert response.status_code == 200
    assert "HX-Retarget" not in response
    assert line not in _waiting_lines(response)
    assert "Ootame tagasisidet" not in _waiting_lines(response)


def test_another_open_round_stays_listed(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    finished = _round(matter, "Esimene voor", actor=specialist)
    other = _round(matter, "Teine voor", actor=specialist, deadline_days=None)

    lines = _waiting_lines(_finish(signed_in, finished))

    assert f'data-feedback-wait="{finished.pk}"' not in lines
    assert f'data-feedback-wait="{other.pk}"' in lines
    assert "Tähtaeg määramata" in lines


def test_finishing_with_the_step_still_answers_with_the_whole_column(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, "Liikmete küsitlus", actor=specialist)
    action = set_next_action(matter=matter, text="Koondan vastused", actor=specialist)

    response = _finish(signed_in, engagement, complete_action=str(action.pk))

    assert response.status_code == 200
    assert response["HX-Retarget"] == "#teema-vaade"
    action.refresh_from_db()
    assert action.status == "COMPLETED"


def test_finishing_without_the_step_leaves_the_step_open(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, "Liikmete küsitlus", actor=specialist)
    action = set_next_action(matter=matter, text="Koondan vastused", actor=specialist)

    response = _finish(signed_in, engagement)

    action.refresh_from_db()
    assert action.status == "OPEN"
    # Only the waiting lines ride along: the task and its forms are not resent,
    # so nothing typed into them can be replaced.
    assert "Koondan vastused" not in _waiting_lines(response)
    assert 'id="praegune-tegevus"' not in response.content.decode()


def test_a_repeated_finish_is_refused_and_still_reads_the_present(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, "Liikmete küsitlus", actor=specialist)
    _finish(signed_in, engagement)

    # The same tab pressing again, with the token it was drawn with.
    stale = signed_in.post(
        reverse(
            "matters:complete_engagement_feedback",
            kwargs={"pk": matter.pk, "engagement_id": engagement.pk},
        ),
        {"revision": "stale"},
        headers={"HX-Request": "true"},
    )

    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is not None
    assert "Liikmete küsitlus" not in _waiting_lines(stale)


def test_a_plain_post_gets_no_out_of_band_box(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    engagement = _round(matter, "Liikmete küsitlus", actor=specialist)

    response = _finish(signed_in, engagement, htmx=False)

    assert 'hx-swap-oob="true"' not in response.content.decode()


def test_a_closed_file_gets_no_waiting_box(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _round(matter, "Liikmete küsitlus", actor=specialist)
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)

    page = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert 'id="praegune-ootused"' not in page
