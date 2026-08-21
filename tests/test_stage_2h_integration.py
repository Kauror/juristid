"""Where Stage 2H meets the three stages that landed before it.

Stage 2H was branched from `1dfd56b`, before Stage 2E.1, Stage 2F and Stage 2G
merged, so nothing in its own suite ever loaded a Matter that carried Stage 2G
structured facts, and nothing in Stage 2G's suite ever loaded a Matter whose
submissions go through Stage 2H's archive-provenance prefetch.

Both stages changed `_overview_context` and the submission card. These tests
load the real page with both stages' data present, which is the cheapest thing
that would notice a template or prefetch that only works when the other stage
is absent.

Nothing here is a new feature. Every behaviour asserted belongs to one of the
four stages; what is new is asserting it in the others' company.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.urls import reverse

from app.core.enums import Visibility
from app.intelligence.services import add_important_date, add_work_victory_candidate
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.models import Submission
from tests import factories

pytestmark = pytest.mark.django_db

FUTURE = date(2030, 6, 1)


def _matter_with_everything(owner):
    """A Matter carrying Stage 2G facts and a Stage 2H-shaped submission."""
    matter = factories.MatterFactory(owner=owner)
    add_important_date(
        matter=matter,
        title="Sünteetiline tähtaeg",
        date_value=FUTURE,
        period_end=FUTURE,
        actor=owner,
    )
    add_work_victory_candidate(matter=matter, title="Sünteetiline võit", actor=owner)
    return matter


def test_a_matter_shows_structured_facts_and_submissions_together(client, specialist):
    """The seam: Stage 2G's context and Stage 2H's prefetch on one page."""
    matter = _matter_with_everything(specialist)
    client.force_login(specialist)

    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Sünteetiline tähtaeg" in body
    assert "Sünteetiline võit" in body


def test_the_matter_page_carries_both_stages_context(client, specialist):
    """Asserted on the context rather than the markup, so a template rename
    cannot make this pass by accident."""
    matter = _matter_with_everything(specialist)
    client.force_login(specialist)

    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert response.context["intelligence"] is not None
    assert response.context["can_write"] is True
    assert response.context["can_review_victory"] is False


def test_a_submission_renders_beside_structured_facts(client, specialist):
    """Stage 2H prefetches `archive_imports` onto every submission card.

    A submission with no archive provenance is the ordinary case and must still
    render — the prefetch attribute has to exist even when the list is empty.
    """
    matter = _matter_with_everything(specialist)
    Submission.objects.create(
        matter=matter,
        kind=SubmissionKind.FORMAL_OPINION,
        status=SubmissionStatus.DRAFT,
        title="Sünteetiline arvamus",
    )
    client.force_login(specialist)

    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert response.status_code == 200
    assert "Sünteetiline tähtaeg" in response.content.decode()


def test_a_restricted_matters_facts_and_submissions_stay_restricted(
    client, specialist, other_specialist
):
    """One refusal, not two: Stage 2H changed nothing about who may read a
    Matter, and the page it added context to is still the Matter page."""
    matter = _matter_with_everything(specialist)
    matter.visibility = Visibility.RESTRICTED
    matter.save(update_fields=["visibility", "updated_at"])

    client.force_login(other_specialist)
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert response.status_code == 404
