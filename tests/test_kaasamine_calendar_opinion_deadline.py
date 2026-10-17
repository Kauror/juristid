"""`Tagasisidet ootame kuni`'s calendar marks the Matter's `Arvamuse tähtaeg`.

`+ Kaasamine · Alusta kaasamist` asks by when feedback is wanted. The person
choosing that day should see when the Chamber's own opinion is due, so the
feedback-deadline box carries the opinion deadline as one *informational* mark
for the shared calendar (`static/js/app.js` `buildCalendar`): the day is drawn in
the header's deadline orange, with «Arvamuse tähtaeg» on hover and in its
accessible name.

Information only, and these tests hold it to that: the mark is not a value, not
an initial, and not a limit. A Matter without an opinion deadline renders the box
exactly as before. The browser half is `e2e/test_kaasamine_calendar_opinion_deadline.py`.
"""

from __future__ import annotations

import re
from datetime import date

import pytest
from django.urls import reverse

from app.matters.forms import CompactEngagementForm
from app.matters.models import MatterEngagement
from tests import factories

pytestmark = pytest.mark.django_db

MARK = 'data-datepicker-mark="30.10.2026"'
LABEL = 'data-datepicker-mark-label="Arvamuse tähtaeg"'


def _feedback_box(html: str) -> str:
    """The `Tagasisidet ootame kuni` input of `Alusta kaasamist`, as rendered."""
    match = re.search(r'<input[^>]*name="feedback_deadline"[^>]*>', html)
    assert match, "the feedback-deadline box is on the page"
    return match.group(0)


def _teema_page(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def test_the_form_marks_the_opinion_deadline_in_the_feedback_calendar():
    form = CompactEngagementForm(opinion_deadline=date(2026, 10, 30))

    box = str(form["feedback_deadline"])

    assert MARK in box
    assert LABEL in box


def test_without_an_opinion_deadline_the_box_is_unchanged():
    plain = str(CompactEngagementForm()["feedback_deadline"])
    explicit_none = str(CompactEngagementForm(opinion_deadline=None)["feedback_deadline"])

    assert "data-datepicker-mark" not in plain
    assert plain == explicit_none


def test_the_mark_is_not_a_value_and_not_an_initial():
    form = CompactEngagementForm(opinion_deadline=date(2026, 10, 30))

    box = str(form["feedback_deadline"])

    assert form["feedback_deadline"].value() in (None, "")
    # This widget always renders a `value` attribute; it stays empty.
    assert 'value=""' in box
    assert 'value="30.10.2026"' not in box


def test_the_mark_belongs_to_one_form_instance_only():
    """The widget is a shared module-level instance; marking one form must not
    leak the attribute into the next form built without a deadline."""
    CompactEngagementForm(opinion_deadline=date(2026, 10, 30))

    later = str(CompactEngagementForm()["feedback_deadline"])

    assert "data-datepicker-mark" not in later


def test_the_teema_page_marks_the_matters_opinion_deadline(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, response_deadline=date(2026, 10, 30))

    page = _teema_page(signed_in, matter)

    box = _feedback_box(page)
    assert MARK in box
    assert LABEL in box


def test_a_teema_without_an_opinion_deadline_shows_the_plain_calendar(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, response_deadline=None)

    page = _teema_page(signed_in, matter)

    assert "data-datepicker-mark" not in _feedback_box(page)


def test_a_refused_save_keeps_the_mark(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, response_deadline=date(2026, 10, 30))

    response = signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {"audience": "", "feedback_deadline": "5.11.2026"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    assert not MatterEngagement.objects.exists()
    box = _feedback_box(response.content.decode())
    assert MARK in box
    # What the person typed is kept, and the mark did not replace it.
    assert 'value="5.11.2026"' in box


def test_a_feedback_date_after_the_opinion_deadline_is_still_accepted(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, response_deadline=date(2026, 10, 30))

    response = signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {"audience": "Liikmed", "feedback_deadline": "15.11.2026"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    engagement = MatterEngagement.objects.get(matter=matter)
    assert engagement.feedback_deadline == date(2026, 11, 15)
