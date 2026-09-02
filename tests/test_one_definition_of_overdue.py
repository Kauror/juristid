"""Late is decided once, in Python, from the day the page was built.

DUP-06. Three templates compared a date against `today` themselves:

    {% if matter.response_deadline and matter.response_deadline < today %}
    {% if not row.is_effective_date and row.record.period_end < today %}
    {% if row.effective_date.period_end < today %}

`app/matters/overview.py`'s docstring states the rule they broke — *"**One
definition of overdue.** Everything dated comes from `app.matters.work_items` …
A second idea of *late* written next door is how a department head ends up
looking at two screens that disagree about the same Matter."* — and
`WorkItem` says why the comparison cannot live in a template at all: *"a Django
template cannot hand an argument to a property — and a row that had to be told
what day it is would end up being told twice, differently."*

That is the defect, and it is about **provenance**, not arithmetic. `today` in a
template is whatever the view happened to put in the context; two views can put
two different things there, and one of them can put nothing at all — in which
case the comparison silently reads `None` and every date renders as not-late.

The predicate itself is unchanged on purpose. `wi.outstanding_response_deadlines`
knows a stronger rule — a `Järgmiseks` supersedes the register's date
(docs/adr/0050) — and adopting it here would change what the rail shows. That is
a product decision, and this round is a cleanup.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from app.intelligence import selectors as isel
from app.intelligence.enums import EventKind
from app.matters import overview as ov

pytestmark = pytest.mark.django_db

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


# ---------------------------------------------------------------------------
# No template decides this any more
# ---------------------------------------------------------------------------


def test_no_template_compares_a_date_against_today():
    """The whole class, not the three that were found.

    A grep rather than three assertions, because the next copy will be written
    somewhere none of them looks — and the reason it is wrong is the same
    wherever it appears.
    """
    offenders = [
        f"{path.relative_to(TEMPLATES)}:{number}"
        for path in TEMPLATES.rglob("*.html")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if ("< today" in line or "> today" in line) and "{%" in line
    ]

    assert not offenders, (
        "a template is deciding whether a date has passed: "
        + ", ".join(offenders)
        + ". `today` in a template is whatever the view put there; decide it on "
        "the read model, the way `WorkItem` and `AreaMatterLine` do."
    )


# ---------------------------------------------------------------------------
# The areas rail
# ---------------------------------------------------------------------------


def _area_line(user, today, deadline):
    from app.taxonomy.models import PolicyArea
    from tests import factories

    area = PolicyArea.objects.filter(is_active=True).order_by("sort_order")[0]
    matter = factories.MatterFactory(
        owner=user,
        title="Tähtajaga teema",
        reference_year=2026,
        reference_number=901,
        response_deadline=deadline,
    )
    matter.policy_areas.add(area)

    rows, _ = ov.area_rows(user, today, ov.wi.work_items(user, today=today))
    ov.attach_area_matters(user, rows, today)
    row = next(row for row in rows if row.key == area.key)
    return next(line for line in row.matters if line.matter.pk == matter.pk)


@pytest.mark.parametrize(
    ("offset", "expected"),
    [(-1, True), (0, False), (1, False)],
    ids=["yesterday", "today", "tomorrow"],
)
def test_an_area_line_decides_lateness_from_the_pages_own_day(department_head, offset, expected):
    """Today is not late. The deadline is the last day it may be done on."""
    today = datetime.date(2026, 9, 2)

    line = _area_line(department_head, today, today + datetime.timedelta(days=offset))

    assert line.is_overdue is expected


def test_an_area_line_without_a_deadline_is_never_late(department_head):
    today = datetime.date(2026, 9, 2)

    line = _area_line(department_head, today, None)

    assert line.is_overdue is False
    assert line.deadline is None


def test_the_area_line_carries_the_day_it_was_read_against(department_head):
    """The same Matter, two days, two answers — from the argument rather than
    from an ambient value. A rail built for a date in the past must say what was
    late *then*."""
    deadline = datetime.date(2026, 9, 2)

    late = _area_line(department_head, deadline + datetime.timedelta(days=5), deadline)

    assert late.is_overdue is True


# ---------------------------------------------------------------------------
# The calendar rows
# ---------------------------------------------------------------------------


def _entry(kind, period_end, today):
    record = type("Rec", (), {"period_end": period_end})()
    field = "important_date" if kind == EventKind.IMPORTANT_DATE else "effective_date"
    return isel.CalendarEntry(event_kind=kind.value, today=today, **{field: record})


def test_a_passed_deadline_is_late():
    today = datetime.date(2026, 9, 2)

    entry = _entry(EventKind.IMPORTANT_DATE, today - datetime.timedelta(days=1), today)

    assert entry.is_overdue is True


def test_a_passed_commencement_is_never_late():
    """*Jõustuvad aktid* is not a list of obligations.

    A commencement that has passed is not a failure — the act came into force,
    which is the thing everybody was waiting for (02-EKRAANID §D). The old
    template knew this and said so with `not row.is_effective_date`; the rule
    now lives where the tense does.
    """
    today = datetime.date(2026, 9, 2)

    entry = _entry(EventKind.EFFECTIVE_DATE, today - datetime.timedelta(days=1), today)

    assert entry.is_overdue is False
    assert entry.has_taken_effect is True


def test_a_future_commencement_has_not_taken_effect():
    today = datetime.date(2026, 9, 2)

    entry = _entry(EventKind.EFFECTIVE_DATE, today + datetime.timedelta(days=1), today)

    assert entry.has_taken_effect is False


def test_a_row_with_no_end_date_is_neither_late_nor_in_force():
    """An approximate date can carry no period end, and `None < today` is a
    `TypeError` in Python and a silent falsehood in a Django template."""
    today = datetime.date(2026, 9, 2)

    assert _entry(EventKind.IMPORTANT_DATE, None, today).is_overdue is False
    assert _entry(EventKind.EFFECTIVE_DATE, None, today).has_taken_effect is False
