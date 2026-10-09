"""`Töö olek` beside the title, and the two display corrections the pilot needed.

docs/adr/0148 §5: the badge is derived from `is_open`, the closure `Disposition`
and the successor — never stored — so these tests set those columns and read
the word back, in the rule and on the page.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.matters.enums import RecordMode
from app.matters.services import add_entry, close_matter
from app.matters.work_status import (
    ACTIVE,
    CONCLUDED,
    CONTINUES,
    INACTIVE,
    WORK_STATUSES,
    work_status_of,
)
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db


def _closed_archive(**fields):
    return factories.MatterFactory(record_mode=RecordMode.ARCHIVE, is_open=False, **fields)


def test_open_work_is_active_whatever_was_sent():
    assert work_status_of(factories.MatterFactory()) == ACTIVE


@pytest.mark.parametrize(
    "disposition, expected",
    [
        (Disposition.COMPLETED, INACTIVE),
        (Disposition.INITIATIVE_WITHDRAWN, INACTIVE),
        ("", INACTIVE),
        (Disposition.MONITORING_STOPPED, CONCLUDED),
        (Disposition.NO_POSITION_FORMED, CONCLUDED),
        (Disposition.RESPONSE_COMPLETE, CONCLUDED),
        (Disposition.DUPLICATE, CONCLUDED),
        (Disposition.OTHER, CONCLUDED),
    ],
)
def test_every_closure_reads_as_exactly_one_status(disposition, expected):
    assert work_status_of(_closed_archive(disposition=disposition)) == expected


def test_a_continuation_reads_as_continuing_elsewhere():
    successor = factories.MatterFactory()
    matter = _closed_archive(disposition=Disposition.SUPERSEDED, superseded_by=successor)
    assert work_status_of(matter) == CONTINUES


def test_the_four_words_and_modifiers_are_distinct():
    assert len({status.label for status in WORK_STATUSES}) == 4
    assert len({status.modifier for status in WORK_STATUSES}) == 4
    assert [status.label for status in WORK_STATUSES] == [
        "Aktiivne",
        "Mitteaktiivne",
        "Lõpetatud",
        "Jätkub mujal",
    ]


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def test_the_header_draws_the_word_and_its_modifier(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    body = _detail(signed_in, matter)
    assert "badge--active" in body and "Aktiivne" in body

    successor = factories.MatterFactory(owner=specialist)
    close_matter(
        matter=matter, disposition=Disposition.SUPERSEDED, actor=specialist, successor=successor
    )
    body = _detail(signed_in, matter)
    assert "badge--continues" in body and "Jätkub mujal" in body
    assert "badge--open" not in body


def test_the_archive_notice_still_marks_the_historical_register(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist, record_mode=RecordMode.ARCHIVE)
    assert matter.shows_register_archive_notice
    assert "Arhiivikirje." in _detail(signed_in, matter)


def test_an_entry_without_an_author_says_what_it_is(signed_in, specialist):
    """A note the pilot carried over names nobody; its line says «Märkus» instead."""
    matter = factories.MatterFactory(owner=specialist)
    add_entry(matter=matter, body="Jätkub teema 2026_9 all.", author=None)
    body = _detail(signed_in, matter)
    assert '<span class="uxtl__author">Märkus</span>' in body
