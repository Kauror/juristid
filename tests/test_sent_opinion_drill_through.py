"""«Saadetud arvamusi N» opens a list of exactly those N (2026-10-28 audit B-04).

Osakond's rail counts opinions sent this calendar year without the official
«no comments» answers (`NOT_AN_OPINION_KINDS`, docs/adr/0174). Its link opened
`/arvamused/saadetud/?aasta=<year>`, which listed every SENT send — so a year
with one «Märkusteta vastus» said 41 and listed 42. The owner kept the figure
clickable (decision 2 of 2026-10-29); the link now carries the explicit
``?ainult=arvamused`` the workspace applies with the same exclusion
(`workspace.reportable_opinions`).

Scenario E of the remediation brief: two reportable SENT opinions and one SENT
`NO_OBJECTION_RESPONSE` this year, one opinion last year, and a draft → the
figure says 2 and the page it opens lists exactly those two.
"""

from __future__ import annotations

import datetime
import re

import pytest
from django.utils import timezone

from app.matters import department_dashboard as dd
from app.submissions import workspace
from app.submissions.enums import SubmissionKind
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent,
)
from app.submissions.workspace import SentFilters, SubmissionQueryRefused
from tests import factories

pytestmark = pytest.mark.django_db


def _send(matter, actor, *, title, kind=SubmissionKind.FORMAL_OPINION, sent_at=None):
    submission = create_submission(
        matter=matter,
        title=title,
        kind=kind,
        actor=actor,
        recipients=[factories.OrganisationFactory(name=f"Saaja {title}")],
    )
    attach_final_evidence(
        submission=submission,
        content=b"%PDF-1.4 arvamus",
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
        actor=actor,
    )
    submission.refresh_from_db()
    return mark_submission_sent(submission=submission, actor=actor, sent_at=sent_at)


@pytest.fixture
def scenario_e(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Arvamuste aasta")
    reportable = [
        _send(matter, specialist, title="Esimene arvamus"),
        _send(matter, specialist, title="Teine arvamus"),
    ]
    _send(
        matter,
        specialist,
        title="Märkusteta vastus",
        kind=SubmissionKind.NO_OBJECTION_RESPONSE,
    )
    last_year = timezone.localdate().year - 1
    _send(
        matter,
        specialist,
        title="Mullune arvamus",
        sent_at=timezone.make_aware(datetime.datetime(last_year, 6, 1, 12, 0)),
    )
    create_submission(matter=matter, title="Mustand", actor=specialist)
    return reportable


def _rail_row(user):
    return next(row for row in dd.reporting_rail(user) if row.label == "Saadetud arvamusi")


def test_the_figure_counts_the_two_opinions(department_head, scenario_e):
    assert _rail_row(department_head).count == 2


def test_the_page_it_opens_lists_exactly_those_two(client, department_head, scenario_e):
    row = _rail_row(department_head)
    client.force_login(department_head)

    response = client.get(row.url)

    assert response.status_code == 200
    listed = {submission.pk for submission in response.context["page"].object_list}
    assert listed == {submission.pk for submission in scenario_e}
    assert response.context["total"] == row.count == 2
    body = response.content.decode()
    assert "2 vastet." in body
    assert "märkusteta vastused ei ole selles loendis" in body
    # The narrowing survives the next search.
    assert 'name="ainult" value="arvamused"' in body


def test_the_figure_and_the_filter_are_one_population(department_head, scenario_e):
    year = timezone.localdate().year
    start, end = dd.reporting_year(timezone.localdate())
    figure = set(dd.sent_submissions(department_head, since=start, until=end))
    listed = set(
        workspace.sent_queryset(
            department_head, SentFilters(year=str(year), only=workspace.OPINIONS_ONLY)
        )
    )

    assert figure == listed == set(scenario_e)


def test_without_the_narrowing_the_list_still_shows_every_send(department_head, scenario_e):
    """The workspace's own default is unchanged: a «no comments» answer is a send."""
    year = str(timezone.localdate().year)

    every_send = workspace.sent_queryset(department_head, SentFilters(year=year))

    assert every_send.count() == 3
    assert {s.kind for s in every_send} >= {SubmissionKind.NO_OBJECTION_RESPONSE}


def test_an_unknown_narrowing_is_refused_with_a_sentence(department_head):
    with pytest.raises(SubmissionQueryRefused, match=re.escape("Tundmatu valik: koik.")):
        workspace.sent_queryset(department_head, SentFilters(only="koik"))
