"""A reader's chronology reads its event scope once, then names events by id (QRY-04).

`scope_change_events` is one correlated `EXISTS` per child family, and the
chronology inlined it three or four times into every statement of `bound` and
`load`, and again on every doubling round — 113 KB of SQL for a reader on a
dense Matter, planned at five times the cost of running it. For a reader whose
visibility the rule narrows, the visible event ids of this one Matter are now
read once and every later statement filters on them.

The rows are the same rows: checked against the inlined spelling for the same
reader, over a Matter with a restricted child whose events must stay hidden.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.core.enums import Visibility
from app.matters import timeline
from app.matters.timeline import matter_timeline
from app.workflow.services import set_next_action
from tests import factories
from tests.test_timeline_pagination_walk import _signature as walk_signature

pytestmark = pytest.mark.django_db

EVERYTHING = 10_000


@pytest.fixture
def busy_matter(specialist):
    matter = factories.MatterFactory(owner=specialist, visibility=Visibility.NORMAL)
    for index in range(12):
        factories.EntryFactory(
            matter=matter,
            author=specialist,
            occurred_at=timezone.now() - timedelta(hours=index),
            visibility_override=Visibility.RESTRICTED if index % 3 == 0 else "",
        )
    for index in range(4):
        set_next_action(matter=matter, text=f"Samm {index}", actor=specialist)
    return matter


def _signature(items: list[Any]) -> list[tuple]:
    return [walk_signature(item) for item in items]


def _inlined_for(monkeypatch, reader) -> None:
    """Force the inlined scope for this reader: their role joins the empty-predicate set."""
    monkeypatch.setattr(
        timeline,
        "ROLES_WITH_RESTRICTED_ACCESS",
        frozenset({*timeline.ROLES_WITH_RESTRICTED_ACCESS, reader.role}),
    )


@pytest.mark.parametrize("limit", [5, EVERYTHING])
def test_the_reader_sees_the_same_rows_either_way(monkeypatch, busy_matter, reader, limit):
    materialised, more = matter_timeline(matter=busy_matter, user=reader, limit=limit)
    _inlined_for(monkeypatch, reader)
    inlined, inlined_more = matter_timeline(matter=busy_matter, user=reader, limit=limit)

    assert _signature(materialised) == _signature(inlined)
    assert more == inlined_more


def test_a_restricted_child_stays_hidden_from_the_reader(busy_matter, reader, specialist):
    reader_items, _ = matter_timeline(matter=busy_matter, user=reader, limit=EVERYTHING)
    lawyer_items, _ = matter_timeline(matter=busy_matter, user=specialist, limit=EVERYTHING)

    reader_entries = [item for item in reader_items if item.is_entry]
    lawyer_entries = [item for item in lawyer_items if item.is_entry]
    assert len(lawyer_entries) == 12
    assert len(reader_entries) == 8  # the four restricted entries are not there


def _scoped_statements(user, matter) -> list[str]:
    with CaptureQueriesContext(connection) as captured:
        matter_timeline(matter=matter, user=user, limit=5)
    return [q["sql"] for q in captured if "audit_changeevent" in q["sql"] and "EXISTS" in q["sql"]]


def test_the_scope_is_read_once_for_a_reader(busy_matter, reader):
    assert len(_scoped_statements(reader, busy_matter)) == 1


def test_the_legal_department_keeps_the_inlined_scope(busy_matter, specialist):
    """Their predicate is empty, so the id list measured slower than the scope."""
    assert len(_scoped_statements(specialist, busy_matter)) > 1
