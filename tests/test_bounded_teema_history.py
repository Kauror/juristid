"""The grouped `Teema käik` reads a bounded window; the rest is one click away (AUD-008, D-2).

The grouped chronology read a Matter's *whole* history on every Teema page and
every column re-render (`limit=100_000`), so the page's cost was the file's
age. It now reads the newest `GROUPED_WINDOW` rows and groups them exactly as
before:

* every period that began after the oldest row read is complete;
* a period that began before it is marked partial («+» on its count) and the
  section offers «Näita kogu käik»;
* `?kaik=koik` reads every row, grouped the same way, with nothing marked.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.matters.episode_timeline import GROUPED_WINDOW, matter_episode_timeline
from app.matters.services import add_entry, change_stage
from app.workflow.models import StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db


def _stage(key: str):
    return StageVocabulary.objects.get(key=key)


@pytest.fixture
def busy(specialist):
    """Two periods, each with more rows than half the window."""
    matter = factories.MatterFactory(owner=specialist, stage=None)
    change_stage(matter=matter, stage=_stage("idea"), actor=specialist)
    for number in range(GROUPED_WINDOW):
        add_entry(matter=matter, author=specialist, body=f"Idee märge {number:03d}")
    change_stage(matter=matter, stage=_stage("consultation"), actor=specialist)
    for number in range(GROUPED_WINDOW // 2):
        add_entry(matter=matter, author=specialist, body=f"Kooskõlastuse märge {number:03d}")
    return matter


def _groups(matter, user, **kwargs):
    timeline = matter_episode_timeline(matter=matter, user=user, **kwargs)
    assert timeline is not None
    return timeline, {group.label: group for group in timeline.groups}


def test_the_default_read_is_bounded_and_says_so(busy, specialist):
    timeline, _ = _groups(busy, specialist)

    assert timeline.has_more
    assert timeline.count <= GROUPED_WINDOW
    current = next(group for group in timeline.groups if group.is_current)
    assert not current.partial
    assert len(current.items) == GROUPED_WINDOW // 2
    older = [group for group in timeline.groups if not group.is_current]
    assert older and all(group.partial for group in older)


def test_the_whole_history_is_grouped_the_same_and_complete(busy, specialist):
    bounded, _ = _groups(busy, specialist)
    whole, _ = _groups(busy, specialist, whole_history=True)

    assert not whole.has_more
    assert not any(group.partial for group in whole.groups)
    assert whole.count == GROUPED_WINDOW + GROUPED_WINDOW // 2
    assert [g.key for g in bounded.groups] == [g.key for g in whole.groups]
    # The bounded read is the newest rows of the whole read, in the same groups.
    for small, large in zip(bounded.groups, whole.groups, strict=True):
        assert [i.entry.pk for i in small.items if i.entry] == [
            i.entry.pk for i in large.items if i.entry
        ][: len(small.items)]


def test_a_short_history_is_whole_and_unmarked(specialist):
    matter = factories.MatterFactory(owner=specialist, stage=None)
    change_stage(matter=matter, stage=_stage("idea"), actor=specialist)
    add_entry(matter=matter, author=specialist, body="Üks märge")

    timeline, _ = _groups(matter, specialist)

    assert not timeline.has_more
    assert not any(group.partial for group in timeline.groups)


def test_the_page_offers_the_whole_history_and_serves_it(signed_in, busy):
    page = reverse("matters:matter_detail", kwargs={"pk": busy.pk})

    bounded = signed_in.get(page).content.decode()
    whole = signed_in.get(page, {"kaik": "koik"}).content.decode()

    assert "Näita kogu käik" in bounded
    assert "Idee märge 000" not in bounded
    assert "Näita kogu käik" not in whole
    assert "Idee märge 000" in whole
    assert len(bounded) < len(whole)
