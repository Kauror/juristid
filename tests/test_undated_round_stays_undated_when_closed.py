"""A `Kaasamine` whose day is unknown stays undated when it is finished.

The historical replay of 2026-10-04 reported (F-002) that an undated round,
once finished with `Lõpeta kaasamine`, read as a round held on the day it was
closed. Reproduced deterministically, that is not what the product does: the
replayed rounds had been stored with **today's** `Kaasamise kuupäev`, because
the add form pre-fills today (visibly, and clearable — docs/adr/0078 §2) and
the replay driver never emptied the box. The product stored what the form
said.

This file pins the contract the finding was about, so it cannot start being
true later: finishing a round writes `feedback_closed_at` and nothing else
about time; the round's own date stays unknown on its row, in the read model
and in the chronology's order; and the close reads under its own name.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.enums import EngagementKind
from app.matters.models import MatterEngagement
from app.matters.services import add_engagement, engagement_revision_token
from app.matters.timeline import ENGAGEMENT_DATE_UNKNOWN, matter_timeline
from app.workflow.enums import DatePrecision
from tests import factories

pytestmark = pytest.mark.django_db


def _round(matter, *, title: str, occurred_on, actor, recorded_days_ago: int):
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title=title,
        occurred_on=occurred_on,
        actor=actor,
    )
    # Recorded some weeks ago: the close below is a later, separate act.
    MatterEngagement.objects.filter(pk=engagement.pk).update(
        created_at=timezone.now() - dt.timedelta(days=recorded_days_ago)
    )
    engagement.refresh_from_db()
    return engagement


def _finish(client, engagement):
    engagement.refresh_from_db()
    return client.post(
        reverse(
            "matters:complete_engagement_feedback",
            kwargs={"pk": engagement.matter_id, "engagement_id": engagement.pk},
        ),
        {"revision": engagement_revision_token(engagement)},
        headers={"HX-Request": "true"},
    )


def _engagement_rows(matter, user):
    rows, _more = matter_timeline(matter=matter, user=user)
    return [row for row in rows if row.is_engagement]


def test_finishing_an_undated_round_writes_no_day_onto_it(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    undated = _round(
        matter, title="Teadmata ajal", occurred_on=None, actor=specialist, recorded_days_ago=40
    )

    response = _finish(signed_in, undated)

    assert response.status_code == 200
    undated.refresh_from_db()
    assert undated.feedback_closed_at is not None
    assert undated.occurred_on is None
    assert undated.occurred_on_precision == DatePrecision.EXACT


def test_the_finished_undated_round_reads_unknown_and_the_close_reads_as_the_close(
    signed_in, specialist
):
    matter = factories.MatterFactory(owner=specialist)
    undated = _round(
        matter, title="Teadmata ajal", occurred_on=None, actor=specialist, recorded_days_ago=40
    )

    _finish(signed_in, undated)

    (row,) = _engagement_rows(matter, specialist)
    assert row.milestone.display_date == ENGAGEMENT_DATE_UNKNOWN
    # Placed on the day it was recorded, never on the day it was finished.
    assert timezone.localtime(row.occurred_at).date() != timezone.localdate()
    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    body = page.content.decode()
    start = body.index(f'id="kaasamine-{undated.pk}-sisu"')
    article = body[start : body.index("</article>", start)]
    assert ENGAGEMENT_DATE_UNKNOWN in article
    today = timezone.localdate()
    assert f"Tagasiside ootamine lõpetatud {today.day}.{today.month}.{today.year}" in article


def test_finishing_today_does_not_move_the_undated_round_ahead_of_dated_history(
    signed_in, specialist
):
    """The chronology's order is the same before and after the close."""
    matter = factories.MatterFactory(owner=specialist)
    undated = _round(
        matter, title="Teadmata ajal", occurred_on=None, actor=specialist, recorded_days_ago=40
    )
    _round(
        matter,
        title="Kümme päeva tagasi",
        occurred_on=timezone.localdate() - dt.timedelta(days=10),
        actor=specialist,
        recorded_days_ago=10,
    )
    before = [row.milestone.what for row in _engagement_rows(matter, specialist)]

    _finish(signed_in, undated)

    after = [row.milestone.what for row in _engagement_rows(matter, specialist)]
    assert after == before
    assert after[0].endswith("Kümme päeva tagasi")
