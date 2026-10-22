"""Search rows say what kind of record they are (EVAL-001, EVAL-003).

* An official «no comments» reply (`NO_OBJECTION_RESPONSE`) is not an opinion
  (docs/adr/0174). The search projection has one badge for every SUBMISSION —
  «Arvamus» — so the MCP row takes the submission's own kind label instead and
  says ``is_opinion: false``.
* Two entries of one Matter are both «Sissekanne: Märkus»; the title carries the
  entry's date so a client can tell them apart.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from app.mcp_server import readers
from app.search.indexing import rebuild_all
from app.submissions.enums import SubmissionKind
from app.submissions.services import attach_final_evidence, create_submission, mark_submission_sent
from tests import factories

pytestmark = pytest.mark.django_db

NO_COMMENTS_LABEL = str(SubmissionKind.NO_OBJECTION_RESPONSE.label)


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"


def _send(matter: Any, actor: Any, *, kind: str, title: str) -> Any:
    ministry = factories.OrganisationFactory(name=f"Ministeerium {title}")
    submission = create_submission(
        matter=matter, title=title, kind=kind, actor=actor, recipients=[ministry]
    )
    attach_final_evidence(
        submission=submission,
        content=b"%PDF-1.4 vastus",
        original_filename="vastus.pdf",
        mime_type="application/pdf",
        actor=actor,
    )
    submission.refresh_from_db()
    return mark_submission_sent(submission=submission, actor=actor)


@pytest.fixture
def sends(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Kemikaaliseaduse muutmine")
    opinion = _send(
        matter, specialist, kind=SubmissionKind.FORMAL_OPINION, title="Kemikaalide arvamus"
    )
    reply = _send(
        matter,
        specialist,
        kind=SubmissionKind.NO_OBJECTION_RESPONSE,
        title="Kemikaalide vastus",
    )
    rebuild_all()
    return opinion, reply


def test_a_no_comments_reply_is_not_labelled_an_opinion(sends, specialist):
    opinion, reply = sends
    rows = {
        row["id"]: row
        for row in readers.search_records(specialist, "kemikaalide", source_kinds=["SUBMISSION"])[
            "results"
        ]
    }
    no_comments = rows[f"sub:{reply.pk}"]
    assert no_comments["source_label"] == NO_COMMENTS_LABEL
    assert "Arvamus:" not in no_comments["title"]
    assert NO_COMMENTS_LABEL in no_comments["title"]
    assert no_comments["submission_kind"] == "NO_OBJECTION_RESPONSE"
    assert no_comments["is_opinion"] is False

    sent_opinion = rows[f"sub:{opinion.pk}"]
    assert sent_opinion["source_label"] == "Arvamus"
    assert sent_opinion["is_opinion"] is True
    assert sent_opinion["submission_kind"] == "FORMAL_OPINION"


def test_search_titles_carry_the_same_label(sends, specialist):
    _opinion, reply = sends
    titles = {
        row["id"]: row["title"]
        for row in readers.search_results(specialist, "kemikaalide")["results"]
    }
    assert NO_COMMENTS_LABEL in titles[f"sub:{reply.pk}"]
    assert "Arvamus:" not in titles[f"sub:{reply.pk}"]


def test_two_entries_of_one_matter_have_distinct_dated_titles(specialist):
    matter = factories.MatterFactory(owner=specialist, title="Liiklusseaduse muutmine")
    now = timezone.now()
    first = factories.EntryFactory(
        matter=matter,
        author=specialist,
        body="<p>Liikluskoosolek ministeeriumis.</p>",
        occurred_at=now - timedelta(days=10),
    )
    second = factories.EntryFactory(
        matter=matter,
        author=specialist,
        body="<p>Liikluskoosolek liitudega.</p>",
        occurred_at=now - timedelta(days=2),
    )
    rebuild_all()
    titles = {
        row["id"]: row["title"]
        for row in readers.search_results(specialist, "liikluskoosolek")["results"]
    }
    first_title, second_title = titles[f"entry:{first.pk}"], titles[f"entry:{second.pk}"]
    assert first_title != second_title
    day = timezone.localtime(first.occurred_at).date()
    assert f"{day.day:02d}.{day.month:02d}.{day.year}" in first_title


def test_labels_cost_no_query_per_row(sends, specialist, django_assert_max_num_queries):
    with django_assert_max_num_queries(40):
        readers.search_records(specialist, "kemikaalide")
    with django_assert_max_num_queries(40):
        readers.search_results(specialist, "kemikaalide")
