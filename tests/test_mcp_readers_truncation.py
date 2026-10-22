"""`get_matter` never truncates silently (MCPSEC-05).

Every bounded list in the snapshot says how many there are and whether it
was cut (`list_totals`, `truncated_lists`), and the sent-opinion counts are
counted in the database, not from the list that was cut to twenty. A long
activity item says its text was cut and names the id that fetches it whole.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from django.utils import timezone

from app.matters.models import MatterProceduralDevelopment
from app.mcp_server import readers
from app.submissions.enums import SubmissionKind
from app.submissions.services import attach_final_evidence, create_submission, mark_submission_sent
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"


@pytest.fixture
def today() -> date:
    return timezone.localdate()


def _send(matter: Any, actor: Any, *, kind: str, title: str) -> Any:
    ministry = factories.OrganisationFactory(name=f"Ministeerium {title}")
    submission = create_submission(
        matter=matter, title=title, kind=kind, actor=actor, recipients=[ministry]
    )
    attach_final_evidence(
        submission=submission,
        content=b"%PDF-1.4 arvamus",
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
        actor=actor,
    )
    submission.refresh_from_db()
    return mark_submission_sent(submission=submission, actor=actor)


def test_counts_are_counted_not_taken_from_the_cut_list(specialist, monkeypatch):
    # The list of sends is LIST_LIMIT * 2 long; shrink it so five sends overflow it.
    monkeypatch.setattr(readers, "LIST_LIMIT", 2)
    matter = factories.MatterFactory(owner=specialist)
    for index in range(4):
        _send(matter, specialist, kind=SubmissionKind.FORMAL_OPINION, title=f"Arvamus {index}")
    _send(matter, specialist, kind=SubmissionKind.NO_OBJECTION_RESPONSE, title="Ei ole märkusi")

    snapshot = readers.matter_snapshot(specialist, str(matter.pk))

    assert len(snapshot["submissions_sent"]) == 4
    assert snapshot["counts"]["opinions_sent"] == 4
    assert snapshot["counts"]["no_comments_replies_sent"] == 1
    assert snapshot["list_totals"]["submissions_sent"] == {
        "shown": 4,
        "total": 5,
        "truncated": True,
    }
    assert "submissions_sent" in snapshot["truncated_lists"]


def test_every_bounded_list_states_its_total(specialist, today):
    matter = factories.MatterFactory(owner=specialist)
    for index in range(7):
        MatterProceduralDevelopment.objects.create(
            matter=matter,
            title=f"Areng {index}",
            occurred_on=today - timedelta(days=index + 1),
            created_by=specialist,
        )
    snapshot = readers.matter_snapshot(specialist, str(matter.pk))
    totals = snapshot["list_totals"]
    assert totals["latest_developments"] == {"shown": 5, "total": 7, "truncated": True}
    for name in (
        "alternate_titles",
        "planned_actions",
        "upcoming_dates",
        "past_dates",
        "commencements",
        "submissions_sent",
        "open_feedback_waits",
        "latest_developments",
        "work_victories",
    ):
        assert set(totals[name]) == {"shown", "total", "truncated"}, name
        assert totals[name]["shown"] == len(snapshot[name]), name
    assert snapshot["truncated_lists"] == ["latest_developments"]


def test_a_cut_activity_item_says_so_and_names_its_fetch_id(specialist):
    matter = factories.MatterFactory(owner=specialist)
    entry = factories.EntryFactory(
        matter=matter, author=specialist, body="<p>" + "pikk tekst " * 400 + "</p>"
    )
    answer = readers.matter_activity(specialist, str(matter.pk), kind="entries")
    [item] = answer["items"]
    assert item["text_truncated"] is True
    assert item["id"] == f"entry:{entry.pk}"
    assert len(item["text"]) <= readers.ACTIVITY_ITEM_CHARS
