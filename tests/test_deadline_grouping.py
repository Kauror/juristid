"""Which window a future deadline lands in, and what the panel shows.

The panel is Osakond's *Eesolev* and it cuts five windows
(`department_dashboard.upcoming_windows`, ADR 0049 §5). This module was written
against the three-window read model that preceded it (`overview.deadline_groups`
and friends); that model outlived its renderer, was compared window by window
against this one, and has been removed. What is left here is what the live panel
still has to be true about:

* **the panel** — that each window is one shut `<details>` holding every row it
  counted, and what the count on its summary promises;
* **what must not change** — a WAIT is not a deadline, a month-precision date
  does not become a day, a restricted Matter is not visible to a reader who may
  not see it, and the rows of a window run earliest first;
* **the arithmetic underneath it** — `end_of_month`, which is what cuts the
  fourth window.

The partition itself — five touching intervals, nothing between them or across
them — is asserted in `tests/test_department_page.py` (a year of dates out of
chosen days) and in `tests/test_ux_pass.py` (a year of days, and that *Ülejäänud
kuu* ends where its month does).

The pixels are the browser suite's job (`e2e/test_ui_shell.py`). This file
checks structure: the groups, the counts, the rows and the links.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from app.core.dates import end_of_month
from app.core.enums import Visibility
from app.matters import department_dashboard as dd
from app.matters import work_items as wi
from app.matters.services import create_matter
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.services import set_next_action

PANEL = 'aria-label="Eesolev"'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deadline(
    owner,
    *,
    on: date,
    title: str,
    kind: str = ActionKind.DO,
    semantics: str = DateSemantics.DEADLINE,
    precision: str = DatePrecision.EXACT,
    actor=None,
    visibility: str | None = None,
):
    """A Matter carrying one dated step, on an absolute date.

    Absolute rather than "N days out": every boundary this module cares about
    is a property of the calendar — the Monday of a week, the last day of a
    month — and an offset from an unknown today can only approximate it.
    """
    matter = create_matter(title=title, owner=owner, reference_year=2026, actor=actor or owner)
    if visibility is not None:
        matter.visibility = visibility
        matter.save(update_fields=["visibility"])
    set_next_action(
        matter=matter,
        text="Esita arvamus",
        kind=kind,
        date_semantics=semantics,
        target_date=on,
        date_precision=precision,
        responsible=owner,
        actor=actor or owner,
    )
    return matter


def _titles(group) -> set[str]:
    return {item.matter.title for item in group.items}


def _panel(client, user) -> str:
    """The rendered *Eesolev* section and nothing else on the page.

    The status is asserted rather than assumed: a redirect decodes to a body
    that contains none of the strings this module looks for, so a test that
    only checked absence would pass on a page nobody could open.
    """
    client.force_login(user)
    response = client.get(reverse("matters:department") + "?vaade=osakond")
    assert response.status_code == 200, f"{user} cannot open Osakond"
    body = response.content.decode()
    assert PANEL in body, "the Eesolev section is not on the page at all"
    return body.split(PANEL)[-1].split("</section>")[0]


def _window(panel: str, heading: str) -> str:
    """One window's `<details>`, from its heading to the end of the element.

    The windows are siblings and each is one `<details>`, so "everything after
    this heading and before the next `</details>`" is exactly the group — and a
    row that leaked out of its disclosure into the panel around it fails rather
    than being found by a substring search over the whole section.
    """
    assert heading in panel, f"{heading} is not on the page"
    return panel.split(heading)[-1].split("</details>")[0]


def _upcoming(user, today: date) -> dict[str, dd.UpcomingGroup]:
    """The five windows the page renders, by key.

    The panel's first window is *today*, which is why this module's frozen
    Wednesday matters: a deadline earlier in the same week is behind the panel
    rather than in its first window, and *Üle tähtaja* is what counts it
    (`dd.upcoming_windows`).
    """
    return {group.key: group for group in dd.upcoming_groups(user, today)}


#: A Wednesday whose week starts in the previous month — the handoff's own
#: worked example. Monday 31.08, Sunday 06.09, the rest of the month 07.09–30.09.
WEDNESDAY_02_09 = date(2026, 9, 2)


@pytest.fixture
def wednesday(monkeypatch):
    """Freeze the application's idea of today at Wednesday 02.09.2026.

    The view reads `timezone.localdate()` for itself, so a test that asserts on
    rendered HTML cannot state the dates it is testing unless the clock holds
    still. And these dates have to be stated: what this module checks *is* the
    calendar — a week that starts in the previous month, a month that ends on a
    Wednesday — and none of it can be expressed as an offset from whichever day
    CI happens to run on.

    Patched on `django.utils.timezone` itself, which is the object every module
    here imported, and undone by `monkeypatch` at the end of the test. Only the
    date moves: `now()` is left alone, so nothing written during the test claims
    to have been written in the future.
    """
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: WEDNESDAY_02_09)
    return WEDNESDAY_02_09


# ---------------------------------------------------------------------------
# The arithmetic the fourth window is cut by
# ---------------------------------------------------------------------------


def test_december_does_not_produce_a_thirteenth_month() -> None:
    """The one arithmetic that can go wrong in `end_of_month`."""
    assert end_of_month(date(2026, 12, 3)) == date(2026, 12, 31)
    assert end_of_month(date(2028, 2, 1)) == date(2028, 2, 29), "leap year"
    assert end_of_month(date(2026, 2, 1)) == date(2026, 2, 28)


# ---------------------------------------------------------------------------
# One window, one disclosure
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_window_holds_every_one_of_its_rows_in_one_disclosure(
    client, department_head, wednesday
) -> None:
    """Requirements 5, 6 and 7, re-read after the window itself became the control.

    Fourteen dates and one `<details>` holding all fourteen. The panel used to
    print five and put the other nine behind a second «Näita veel 9» disclosure
    *inside* the group; now the group is the disclosure, and a control that says
    «kõik 14» opens fourteen rows. Two nested disclosures over one list is two
    things to open to read one window, and the inner one made the outer count a
    claim about a list nobody could see all of.
    """
    today = wednesday
    # From the Monday after next week ends: on this Wednesday *järgmine nädal*
    # runs to 13.09 and *ülejäänud kuu* starts on the 14th.
    for n in range(14):
        _deadline(
            department_head, on=date(2026, 9, 14) + timedelta(days=n), title=f"Eelnõu {n:02d}"
        )

    group = _upcoming(department_head, today)["kuu"]
    assert group.count == 14

    panel = _panel(client, department_head)
    assert "kõik 14" in panel
    # Every row is in the markup, and every row is inside the group's own
    # `<details>` rather than half of them above it.
    window = _window(panel, "ÜLEJÄÄNUD KUU")
    for n in range(14):
        assert f"Eelnõu {n:02d}" in window, f"row {n} is not inside the window's disclosure"


@pytest.mark.django_db
def test_the_former_preview_and_the_former_remainder_are_in_the_same_disclosure(
    client, department_head, wednesday
) -> None:
    """The two halves the read model still cuts are one list on the page.

    `preview` and `rest` are what the old two-tier panel rendered separately.
    They are asserted through the read model here rather than by counting rows,
    so this cannot pass because the template happens to print fourteen titles in
    some order: the rows that used to be behind the inner control are named, and
    every one of them is inside the outer one.
    """
    today = wednesday
    for n in range(14):
        _deadline(
            department_head, on=date(2026, 9, 14) + timedelta(days=n), title=f"Eelnõu {n:02d}"
        )

    group = _upcoming(department_head, today)["kuu"]
    assert group.preview and group.rest, "the fixture no longer exercises the split"

    window = _window(_panel(client, department_head), "ÜLEJÄÄNUD KUU")
    for item in [*group.preview, *group.rest]:
        assert item.matter.title in window, item.matter.title


@pytest.mark.django_db
def test_no_window_offers_naita_veel_however_many_it_holds(
    client, department_head, wednesday
) -> None:
    """Requirement 8, generalised: the nested control is gone from Eesolev.

    It was only ever offered by the two windows that sliced — the rest of the
    month and the far one — so the fixture fills both, plus next week, which
    never offered it. `deadline_more.html` is deleted, so this is a guard
    against it being reintroduced rather than a check on a live branch.
    """
    for n in range(9):
        # Nine deadlines across the seven days inside next week's window: two of
        # them double up, which is the case a per-day cap would have hidden.
        _deadline(department_head, on=date(2026, 9, 4) + timedelta(days=n % 7), title=f"Nädal {n}")
    for n in range(8):
        _deadline(department_head, on=date(2026, 9, 14) + timedelta(days=n), title=f"Kuu {n}")
    for n in range(7):
        _deadline(department_head, on=date(2026, 11, 3) + timedelta(days=n), title=f"Kaugel {n}")

    panel = _panel(client, department_head)
    assert "Näita veel" not in panel
    assert "Näita kõiki" not in panel

    for heading, prefix, held in (
        ("JÄRGMINE NÄDAL", "Nädal", 9),
        ("ÜLEJÄÄNUD KUU", "Kuu", 8),
        ("KAUGEMAL", "Kaugel", 7),
    ):
        window = _window(panel, heading)
        assert f"kõik {held}" in window, heading
        for n in range(held):
            assert f"{prefix} {n}" in window, f"{heading}: row {n}"


@pytest.mark.django_db
def test_every_window_is_a_shut_details_and_the_group_link_is_gone(
    client, department_head, wednesday
) -> None:
    """Native `<details>`, shut, and no `<a>` in the summary.

    Shut is asserted on the tag itself rather than through the rows being
    missing — the rows are served with the page, which is the whole point — and
    the tag is matched exactly, so `<details class="uxdl" open>` fails here
    rather than shipping a panel that opens itself. The `<div>` it replaces
    fails the same assertion.

    The anchor is asserted twice over: the register URL each window used to
    carry is not in the panel, and no `<a>` of any kind is inside a `<summary>`.
    The second is the rule — a link inside a disclosure trigger is two controls
    in one place — and the first is the specific one this round removed.
    """
    today = wednesday
    _deadline(department_head, on=date(2026, 9, 4), title="Nädala eelnõu")
    _deadline(department_head, on=date(2026, 11, 3), title="Novembri eelnõu")

    panel = _panel(client, department_head)

    populated = [group for group in _upcoming(department_head, today).values() if group.count]
    assert {group.key for group in populated} == {"nadal", "kaugemal"}

    assert panel.count('<details class="uxdl">') == 2
    assert panel.count('<summary class="uxdl__head">') == 2
    assert '<div class="uxdl">' not in panel

    for group in populated:
        assert escape(group.url) not in panel, f"{group.key} still links to the register"
    # `too_alates` is what made a link a *window's* link. The section's own
    # «Kõik tähtajad →» carries `?too=tahtaeg-vahemik` with no dates on it, so
    # this cannot pass by the whole panel having lost its links.
    assert "too_alates" not in panel
    assert "Kõik tähtajad →" in panel

    for summary in panel.split('<summary class="uxdl__head">')[1:]:
        assert "<a " not in summary.split("</summary>")[0]


# ---------------------------------------------------------------------------
# Empty groups
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_empty_group_is_omitted_rather_than_headed(client, department_head, wednesday) -> None:
    """Requirement 5 of the brief: no empty section noise.

    One deadline, next month. The panel prints the group that holds it and no
    heading over the two that hold nothing — and it does *not* print the
    whole-panel empty state either, because there is something to see.
    """
    _deadline(department_head, on=date(2026, 10, 20), title="Oktoobri eelnõu")

    panel = _panel(client, department_head)

    assert "KAUGEMAL" in panel
    assert "TÄNA" not in panel
    assert "JÄRGMINE NÄDAL" not in panel
    assert "ÜLEJÄÄNUD KUU" not in panel
    assert "Ühtegi tähtaega ei ole ees." not in panel


@pytest.mark.django_db
def test_a_panel_with_nothing_ahead_keeps_its_one_line_empty_state(
    client, department_head, wednesday
) -> None:
    """The existing compact pattern, not a new large empty state."""
    panel = _panel(client, department_head)

    assert "Ühtegi tähtaega ei ole ees." in panel
    assert "uxdl__head" not in panel


# ---------------------------------------------------------------------------
# What must not change
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_month_precision_date_still_prints_as_a_month(client, department_head, wednesday) -> None:
    """Requirements 10 and 11. The panel does not manufacture a day.

    A date recorded to a month is anchored on the first of it so that it sorts,
    and prints as the month — because printing `01.09.2026` would name a day
    nobody chose (master specification 3.5). Regrouping reads the anchor; it
    must not start rendering it.
    """
    today = wednesday
    # October rather than September: a month-precision September date anchors on
    # the 1st, which this Wednesday has already passed, and *Eesolev* holds only
    # what is ahead.
    _deadline(
        department_head,
        on=date(2026, 10, 1),
        title="Kuu täpsusega eelnõu",
        precision=DatePrecision.MONTH,
    )
    _deadline(department_head, on=date(2026, 10, 2), title="Täpse kuupäevaga eelnõu")

    far = _upcoming(department_head, today)["kaugemal"]
    by_title = {item.matter.title: item for item in far.items}

    approximate = by_title["Kuu täpsusega eelnõu"]
    assert approximate.day_month == "oktoober 2026"
    assert approximate.weekday_letter == "", "a weekday was named for a month-precision date"

    exact = by_title["Täpse kuupäevaga eelnõu"]
    assert exact.day_month == "02.10"
    assert exact.weekday_letter == "R"

    panel = _panel(client, department_head)
    assert "oktoober 2026" in panel
    # In a row, where a date is printed as a day. The window heading beside it
    # legitimately says «alates 01.10» — that is the interval the group holds,
    # not a claim about when this Matter is due.
    assert "<b>01.10</b>" not in panel, "a month became a day"


@pytest.mark.django_db
def test_the_groups_are_still_read_through_visible_to(
    client, department_head, reader, specialist, wednesday
) -> None:
    """Requirement 15. Authorization before arithmetic, unchanged.

    A restricted Matter contributes nothing to a row, a count or a header for a
    reader who may not see it — and the count in «kõik N →» is the count for
    *that* reader, not a departmental total leaked through a header.
    """
    today = wednesday
    _deadline(
        specialist,
        on=date(2026, 9, 4),
        title="Piiratud teema",
        visibility=Visibility.RESTRICTED,
    )
    _deadline(specialist, on=date(2026, 9, 7), title="Tavaline teema")

    head = _upcoming(department_head, today)["nadal"]
    assert _titles(head) == {"Piiratud teema", "Tavaline teema"}
    assert head.matter_count == 2

    for_reader = _upcoming(reader, today)["nadal"]
    assert _titles(for_reader) == {"Tavaline teema"}
    assert for_reader.matter_count == 1

    assert "Piiratud teema" not in _panel(client, reader)


@pytest.mark.django_db
def test_the_rows_of_a_window_run_earliest_first(department_head, wednesday) -> None:
    """Requirement 4 of the brief, on the windows that replaced the ones it named.

    The panel renders `group.items` in whatever order the read model put them,
    and `tests/test_department_page.py` asserts exactly that — the rendering
    preserves the read model's order — while deliberately *not* restating what
    that order is, so a branch that changed the sort could not go green on a
    test which had copied it. That leaves the order itself asserted nowhere.
    This is the other half of the pair: that the order is chronological, and
    that two reads of the same data produce the same one (`wi.sort_items`).

    Two windows, because a sort applied per window and a sort applied to the
    population before it is cut are different mistakes, and only one of them
    shows in a single window.
    """
    today = wednesday
    # Out of order on purpose, and interleaved across the two windows so that a
    # sort that happened to follow insertion order would have to be lucky twice.
    for day in (12, 4, 8):
        _deadline(department_head, on=date(2026, 9, day), title=f"Nädala eelnõu {day}")
    for day in (25, 14, 30, 18):
        _deadline(department_head, on=date(2026, 9, day), title=f"Kuu eelnõu {day}")

    groups = _upcoming(department_head, today)

    assert [item.when for item in groups["nadal"].items] == [
        date(2026, 9, 4),
        date(2026, 9, 8),
        date(2026, 9, 12),
    ]
    assert [item.when for item in groups["kuu"].items] == [
        date(2026, 9, 14),
        date(2026, 9, 18),
        date(2026, 9, 25),
        date(2026, 9, 30),
    ]


@pytest.mark.django_db
def test_two_deadlines_on_one_day_keep_one_order_between_reads(department_head, wednesday) -> None:
    """The tie-break, asserted as stability rather than as its own rule.

    Two obligations falling on the same day cannot be ordered by date, and the
    list still has to be the same list on the next request — a window whose two
    rows swap between two reads of unchanged data is a page that looks like it
    changed when nothing did. What the tie-break *is* belongs to `wi.sort_items`;
    what this window needs from it is only that there is one.
    """
    today = wednesday
    for index in range(3):
        _deadline(department_head, on=date(2026, 9, 8), title=f"Sama päev {index}")

    first = _upcoming(department_head, today)["nadal"]
    second = _upcoming(department_head, today)["nadal"]

    assert first.count == 3
    assert [item.when for item in first.items] == [date(2026, 9, 8)] * 3
    assert [item.matter_id for item in first.items] == [item.matter_id for item in second.items]


@pytest.mark.django_db
def test_the_department_wide_this_week_figure_is_untouched(department_head, wednesday) -> None:
    """The SEIS strip's «tähtaeg sel nädalal» still counts from today.

    It is a different question with the same words: the strip asks what is
    still ahead of the department this week, and the panel shows the week. The
    regrouping deliberately did not redefine `WORK_DEADLINE_THIS_WEEK`, because
    a figure that started counting Monday's missed deadline as upcoming would
    disagree with «üle tähtaja» beside it (app/matters/department_dashboard.py).
    """
    today = wednesday
    _deadline(department_head, on=date(2026, 8, 31), title="Esmaspäevane tähtaeg")
    _deadline(department_head, on=date(2026, 9, 4), title="Reedene tähtaeg")

    items = wi.work_items(department_head, today=today)
    ahead = wi.work_population_items(items, wi.WORK_DEADLINE_THIS_WEEK, today)

    assert {item.matter.title for item in ahead} == {"Reedene tähtaeg"}
    assert wi.deadline_window(wi.WORK_DEADLINE_THIS_WEEK, today) == (today, date(2026, 9, 6))
