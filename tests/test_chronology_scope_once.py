"""A reader's chronology reads its event scope once, then names events by id (QRY-04).

`scope_change_events` is one correlated `EXISTS` per child family, and the
chronology inlined it three or four times into every statement of `bound` and
`load`, and again on every doubling round — 113 KB of SQL for a reader on a
dense Matter, planned at five times the cost of running it. For a reader whose
visibility the rule narrows, the visible event ids of this one Matter are now
read once and every later statement filters on them.

The rows are the same rows: checked against the inlined spelling for the same
reader, over a Matter with a restricted child.

Since docs/adr/0181 no request scope narrows: the three business roles read
everything and keep the inlined scope, and the id list is taken only for an
actor outside them — an administrator reading through a break-glass grant,
who is the reader these tests now use. Until then it was the READER, whose
scope hid the restricted entries.
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
def reader(administrator, department_head):
    """The one actor the id list still serves: outside the business roles, inside a grant."""
    from app.accounts.services import grant_break_glass

    grant_break_glass(
        user=administrator,
        granted_by=department_head,
        reason="Ajaloo kontroll",
        duration=timedelta(hours=1),
    )
    return administrator


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
        "ROLES_WITH_BUSINESS_READ",
        frozenset({*timeline.ROLES_WITH_BUSINESS_READ, reader.role}),
    )


@pytest.mark.parametrize("limit", [5, EVERYTHING])
def test_the_reader_sees_the_same_rows_either_way(monkeypatch, busy_matter, reader, limit):
    materialised, more = matter_timeline(matter=busy_matter, user=reader, limit=limit)
    _inlined_for(monkeypatch, reader)
    inlined, inlined_more = matter_timeline(matter=busy_matter, user=reader, limit=limit)

    assert _signature(materialised) == _signature(inlined)
    assert more == inlined_more


def test_every_reader_sees_every_entry(busy_matter, reader, specialist):
    """The four restricted entries were hidden from a READER until docs/adr/0181."""
    from app.accounts.enums import UserRole

    lugeja = factories.UserFactory(role=UserRole.READER)
    for user in (reader, lugeja, specialist):
        items, _ = matter_timeline(matter=busy_matter, user=user, limit=EVERYTHING)
        assert len([item for item in items if item.is_entry]) == 12, user.role


def _scoped_statements(user, matter) -> list[str]:
    with CaptureQueriesContext(connection) as captured:
        matter_timeline(matter=matter, user=user, limit=5)
    return [q["sql"] for q in captured if "audit_changeevent" in q["sql"] and "EXISTS" in q["sql"]]


def test_the_scope_is_read_once_for_a_reader(busy_matter, reader):
    assert len(_scoped_statements(reader, busy_matter)) == 1


def test_the_legal_department_keeps_the_inlined_scope(busy_matter, specialist):
    """Their predicate is empty, so the id list measured slower than the scope."""
    assert len(_scoped_statements(specialist, busy_matter)) > 1
