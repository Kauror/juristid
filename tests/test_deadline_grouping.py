"""Which window a future deadline lands in, and what the panel shows.

Three things are being asserted, and they are different in kind:

* **arithmetic** — `overview.deadline_windows` cuts three touching intervals out
  of any day of the year, month ends and week-crossings included. Pure, so it
  runs without a database and over every day rather than a sample. It is a read
  model rather than a rendered panel since ADR 0049 merged the two department
  pages; the windows the merged page actually cuts are
  `department_dashboard.upcoming_windows`, asserted the same way in
  `tests/test_department_page.py`;
* **the panel** — Osakond's *Eesolev*: what is shown, what is held back behind
  «Näita veel», and what the header count promises;
* **what must not change** — a WAIT is not a deadline, a month-precision date
  does not become a day, and a restricted Matter is not visible to a reader who
  may not see it.

The pixels are the browser suite's job (`e2e/test_ui_shell.py`). This file
checks structure: the groups, the counts, the rows and the links.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.dates import end_of_month
from app.core.enums import Visibility
from app.intelligence.services import add_important_date
from app.matters import department_dashboard as dd
from app.matters import overview as ov
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


def _groups(user, today: date) -> dict[str, ov.DeadlineGroup]:
    page = ov.build_overview(user, scope=ov.SCOPE_DEPARTMENT, today=today)
    return {group.key: group for group in page.deadlines}


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


def _upcoming(user, today: date) -> dict[str, dd.UpcomingGroup]:
    """The five windows the page renders, by key.

    Deliberately not `_groups`, which reads the three-window model above: on the
    Wednesday this module freezes, *sel nädalal* has already half gone by while
    the panel's own first window is *today* (`dd.upcoming_windows`).
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
# The arithmetic, over every day of a year
# ---------------------------------------------------------------------------


def test_this_week_is_the_calendar_week_and_not_the_next_seven_days() -> None:
    """Monday to Sunday, whatever day it is read on.

    A rolling seven days would move under the reader every morning: a date on
    the list on Tuesday is off it on Wednesday, and the group stops meaning the
    thing the department says out loud to each other.
    """
    for offset in range(366):
        day = date(2026, 1, 1) + timedelta(days=offset)
        _, _, starts, ends = ov.deadline_windows(day)[0]
        assert starts.weekday() == 0, f"{day}: the week does not start on Monday"
        assert ends is not None and ends.weekday() == 6, f"{day}: the week does not end on Sunday"
        assert ends - starts == timedelta(days=6)
        assert starts <= day <= ends


def test_the_rest_of_the_month_starts_after_sunday_and_stops_at_the_month_end() -> None:
    """The middle window's two ends, over every day of a year.

    Two separate claims, and the second is the one a `today + 30` horizon got
    wrong: a window that ends thirty days out ends in the middle of a week
    nobody chose, and "the rest of the month" is then a heading over something
    else.
    """
    for offset in range(366):
        day = date(2026, 1, 1) + timedelta(days=offset)
        (_, _, _, week_end), (_, _, starts, ends) = ov.deadline_windows(day)[:2]
        assert week_end is not None
        assert starts == week_end + timedelta(days=1), f"{day}: a gap after Sunday"
        if starts <= (ends or starts):
            assert ends == end_of_month(day), f"{day}: the window does not stop at the month end"


def test_a_week_that_crosses_the_month_end_leaves_the_middle_window_empty() -> None:
    """The boundary case the handoff named: no duplicates, no backwards range.

    Monday 28.09.2026, Sunday 04.10.2026 — one week, read from either side of
    the month end, and the answer is different on purpose:

    * read on the 28th, 29th or 30th there is no "rest of September" left after
      that Sunday, so the middle window holds no days at all and the panel omits
      it. *Kaugemal* then starts on the 5th and not on the 1st, which would put
      the first four days of October in two windows at once;
    * read on the 1st, the month the reader is in is October, so the middle
      window is 05.10–31.10 — the rest of the month they are actually standing
      in, which is the question the group's heading asks.

    The week itself is the same seven days either way, which is the property
    that makes the panel stop moving under somebody halfway through a week.
    """
    week = (date(2026, 9, 28), date(2026, 10, 4))

    for day in (date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)):
        (_, _, *span), rest, (_, _, far_start, far_end) = ov.deadline_windows(day)
        _, _, rest_start, rest_end = rest

        assert tuple(span) == week
        assert rest_end is not None and rest_start > rest_end, "the middle window is not empty"
        assert far_start == date(2026, 10, 5), "the far window reopens days already shown"
        assert far_end is None

        group = ov.DeadlineGroup("ulejaanud_kuu", "Ülejäänud kuu", [], 5, rest_start, rest_end)
        assert group.is_empty_window
        assert group.range_label == ""

    for day in (date(2026, 10, 1), date(2026, 10, 4)):
        (_, _, *span), rest, (_, _, far_start, _) = ov.deadline_windows(day)
        _, _, rest_start, rest_end = rest

        assert tuple(span) == week, "the week moved when the month turned over"
        assert (rest_start, rest_end) == (date(2026, 10, 5), date(2026, 10, 31))
        assert far_start == date(2026, 11, 1)


def test_december_does_not_produce_a_thirteenth_month() -> None:
    """The one arithmetic that can go wrong in `end_of_month`."""
    assert end_of_month(date(2026, 12, 3)) == date(2026, 12, 31)
    assert end_of_month(date(2028, 2, 1)) == date(2028, 2, 29), "leap year"
    assert end_of_month(date(2026, 2, 1)) == date(2026, 2, 28)


# ---------------------------------------------------------------------------
# The two groups, on the page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_this_week_holds_monday_to_sunday_and_is_never_cut_short(
    department_head, wednesday
) -> None:
    """Requirement 1 and 2: the whole week, however many that is.

    Nine deadlines in a week print nine rows. The group exists so somebody can
    see their week without opening anything, and a group that stops at five has
    stopped doing that.
    """
    today = wednesday
    for day in range(7):
        on = date(2026, 8, 31) + timedelta(days=day)
        _deadline(department_head, on=on, title=f"Nädala eelnõu {day}")
    _deadline(department_head, on=date(2026, 8, 30), title="Eelmine pühapäev")
    _deadline(department_head, on=date(2026, 9, 7), title="Järgmine esmaspäev")

    week = _groups(department_head, today)["sel_nadalal"]

    assert week.count == 7
    assert week.shown == 7, "the week is truncated"
    assert week.remaining == 0, "the week hides rows behind Näita veel"
    assert len(week.preview) == 7
    assert "Eelmine pühapäev" not in _titles(week)
    assert "Järgmine esmaspäev" not in _titles(week)


@pytest.mark.django_db
def test_the_rest_of_the_month_holds_the_days_after_sunday_up_to_the_month_end(
    department_head, wednesday
) -> None:
    """Requirement 3 and 4, with a near-miss on each end."""
    today = wednesday
    _deadline(department_head, on=date(2026, 9, 6), title="Pühapäev — veel see nädal")
    _deadline(department_head, on=date(2026, 9, 7), title="Esmaspäev — esimene kuu päev")
    _deadline(department_head, on=date(2026, 9, 30), title="Kuu viimane päev")
    _deadline(department_head, on=date(2026, 10, 1), title="Järgmine kuu")

    groups = _groups(department_head, today)
    rest = groups["ulejaanud_kuu"]

    assert rest.starts == date(2026, 9, 7)
    assert rest.ends == date(2026, 9, 30)
    assert _titles(rest) == {"Esmaspäev — esimene kuu päev", "Kuu viimane päev"}
    assert "Pühapäev — veel see nädal" in _titles(groups["sel_nadalal"])
    assert "Järgmine kuu" in _titles(groups["kaugemal"])


@pytest.mark.django_db
def test_no_deadline_lands_in_two_groups_or_in_none(department_head, wednesday) -> None:
    """Requirement 9, asserted over a run of consecutive days.

    The windows are built to touch, but "built to" is not "does": this walks
    every day from the Monday before today to a month past the month end and
    checks each one is in exactly one group.
    """
    today = wednesday
    days = [date(2026, 8, 31) + timedelta(days=n) for n in range(70)]
    for day in days:
        _deadline(department_head, on=day, title=f"Eelnõu {day.isoformat()}")

    groups = _groups(department_head, today)
    seen: dict[str, list[str]] = {}
    for key, group in groups.items():
        for title in _titles(group):
            seen.setdefault(title, []).append(key)

    assert len(seen) == len(days), "a dated deadline is in no group at all"
    doubled = {title: keys for title, keys in seen.items() if len(keys) > 1}
    assert not doubled, f"counted twice: {doubled}"


# ---------------------------------------------------------------------------
# Näita veel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_rest_of_the_month_shows_five_and_holds_the_rest_behind_naita_veel(
    client, department_head, wednesday
) -> None:
    """Requirements 5, 6 and 7, on the rendered panel.

    Fourteen dates, five rows, «Näita veel 9» — and the nine are in the markup
    already, inside the same block. The control is a `<details>`, so opening it
    reveals rows that were served with the page rather than fetching a second
    one: there is nowhere to navigate to.
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
    assert len(group.preview) == 5
    assert group.remaining == 9

    panel = _panel(client, department_head)
    assert "Näita veel 9" in panel
    # The five that are visible without opening anything, and the nine behind
    # the disclosure — all fourteen in one block.
    for n in range(14):
        assert f"Eelnõu {n:02d}" in panel, f"row {n} is on no screen at all"
    disclosure = panel.split('class="uxdl__more"')[-1]
    for n in range(5, 14):
        assert f"Eelnõu {n:02d}" in disclosure, f"row {n} is not inside the disclosure"


@pytest.mark.django_db
def test_there_is_no_naita_veel_when_the_month_holds_five_or_fewer(
    client, department_head, wednesday
) -> None:
    """Requirement 8. A control that promises nothing is noise."""
    today = wednesday
    for n in range(5):
        _deadline(department_head, on=date(2026, 9, 14) + timedelta(days=n), title=f"Eelnõu {n}")

    group = _upcoming(department_head, today)["kuu"]
    assert group.count == 5
    assert group.remaining == 0
    assert group.rest == []

    assert "Näita veel" not in _panel(client, department_head)


@pytest.mark.django_db
def test_next_week_never_offers_naita_veel_however_many_it_holds(
    client, department_head, wednesday
) -> None:
    """The week being planned is whole, so the control cannot appear over it.

    Today, tomorrow and next week are shown entire: they are what somebody is
    working in, and the point of the group is that a manager can see the week
    without opening anything (design handoff C §3.3).
    """
    today = wednesday
    for n in range(9):
        # Nine deadlines across the seven days inside next week's window: two of
        # them double up, which is the case a per-day cap would have hidden.
        on = date(2026, 9, 4) + timedelta(days=n % 7)
        _deadline(department_head, on=on, title=f"Nädala eelnõu {n}")

    panel = _panel(client, department_head)
    week_block = panel.split("JÄRGMINE NÄDAL")[-1].split('class="uxdl__head"')[0]

    assert _upcoming(department_head, today)["nadal"].count == 9
    assert "Näita veel" not in week_block
    for n in range(9):
        assert f"Nädala eelnõu {n}" in week_block


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
# kõik N →
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_group_link_opens_exactly_the_matters_the_header_counted(
    department_head, wednesday
) -> None:
    """Requirement 13, and requirement 3 of the brief: «kõik N →» is not «Näita veel».

    One expands rows where the reader stands; the other opens the register. The
    register is asked for the group's own URL rather than for a condition
    rebuilt here, so this cannot pass by two similar queries agreeing with each
    other (app/matters/register_filters.py, `register_population`).
    """
    from urllib.parse import parse_qsl, urlparse

    from app.matters.register_filters import register_population

    today = wednesday
    # Two deadlines on one Matter, and the second is an Oluline tähtaeg — the
    # other half of what the department may honestly call a deadline. The header
    # counts Matters, because that is what the list behind it holds.
    twice = _deadline(department_head, on=date(2026, 9, 10), title="Kaks tähtaega")
    add_important_date(
        matter=twice,
        title="Jõustumine",
        date_value=date(2026, 9, 20),
        period_end=date(2026, 9, 20),
        actor=department_head,
    )
    _deadline(department_head, on=date(2026, 9, 3), title="Sel nädalal")
    _deadline(department_head, on=date(2026, 9, 25), title="Kuu lõpus")
    _deadline(department_head, on=date(2026, 11, 5), title="Novembris")

    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=today)
    for group in page.deadlines:
        query = dict(parse_qsl(urlparse(group.url).query))
        listed = register_population(department_head, query, today=today)
        assert listed.count() == group.matter_count, f"{group.key} promises what it cannot show"

    rest = {group.key: group for group in page.deadlines}["ulejaanud_kuu"]
    assert rest.count == 3, "two dates on one Matter are two rows"
    assert rest.matter_count == 2, "two dates on one Matter are one file to open"


# ---------------------------------------------------------------------------
# What must not change
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_wait_and_a_monitor_are_still_not_deadlines(department_head, wednesday) -> None:
    """Requirement 12. A commitment nobody made is not a commitment.

    A WAIT's expected date and a MONITOR's review date belong in the
    intervention list, where they read as "look at this again" (master
    specification 18.8). Both windows are checked, because a regrouping that
    widened the predicate would show it in whichever one the date fell in.
    """
    today = wednesday
    _deadline(
        department_head,
        on=date(2026, 9, 3),
        title="Ootan ministeeriumi vastust",
        kind=ActionKind.WAIT,
        semantics=DateSemantics.EXPECTED_AROUND,
    )
    _deadline(
        department_head,
        on=date(2026, 9, 15),
        title="Vaatan üle",
        kind=ActionKind.MONITOR,
        semantics=DateSemantics.REVIEW_ON,
    )
    _deadline(department_head, on=date(2026, 9, 4), title="Päris tähtaeg")

    groups = _groups(department_head, today)

    assert _titles(groups["sel_nadalal"]) == {"Päris tähtaeg"}
    assert groups["ulejaanud_kuu"].count == 0


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
def test_the_rows_of_a_group_run_earliest_first(department_head, wednesday) -> None:
    """Requirement 4 of the brief, in both groups.

    The existing tie-break is preserved: two items on one day order by the
    Matter's reference and then by the step's own text, so the list is stable
    between two reads of the same data (`wi.sort_items`).
    """
    today = wednesday
    for day in (25, 7, 30, 12):
        _deadline(department_head, on=date(2026, 9, day), title=f"Eelnõu {day}")
    for day in (5, 1, 6):
        _deadline(department_head, on=date(2026, 9, day), title=f"Nädala eelnõu {day}")

    groups = _groups(department_head, today)

    assert [item.when for item in groups["sel_nadalal"].items] == [
        date(2026, 9, 1),
        date(2026, 9, 5),
        date(2026, 9, 6),
    ]
    assert [item.when for item in groups["ulejaanud_kuu"].items] == [
        date(2026, 9, 7),
        date(2026, 9, 12),
        date(2026, 9, 25),
        date(2026, 9, 30),
    ]


@pytest.mark.django_db
def test_the_week_group_holds_a_date_that_has_already_passed_this_week(
    department_head, wednesday
) -> None:
    """Monday's deadline is still this week's business on Wednesday.

    The deliberate consequence of cutting by the calendar week rather than from
    today: a date between Monday and yesterday is in *Sel nädalal* as well as in
    *Üle tähtaja*, and that is the point — the week group is what somebody looks
    at to see their week, missed days included. `Üle tähtaja` remains the count
    of what is actually late, and it is unchanged by this.
    """
    today = wednesday
    _deadline(department_head, on=date(2026, 8, 31), title="Esmaspäevane tähtaeg")

    page = ov.build_overview(department_head, scope=ov.SCOPE_DEPARTMENT, today=today)
    week = {group.key: group for group in page.deadlines}["sel_nadalal"]
    assert _titles(week) == {"Esmaspäevane tähtaeg"}

    items = wi.work_items(department_head, today=today)
    overdue = {item.matter.title for item in wi.overdue_items(items)}
    assert overdue == {"Esmaspäevane tähtaeg"}, "the overdue population changed"


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


@pytest.mark.django_db
def test_the_far_line_names_the_nearest_date_and_counts_the_rest(
    department_head, wednesday
) -> None:
    """The one-line pointer past the month, unchanged by the regrouping.

    It prints the next date and how many more sit behind it: a list a month out
    is a plan nobody can act on today, and a number with nothing to open is a
    figure nobody can check (design handoff 1a).
    """
    today = wednesday
    _deadline(department_head, on=date(2026, 11, 3), title="Novembri eelnõu")
    _deadline(department_head, on=date(2026, 12, 1), title="Detsembri eelnõu")

    far = _groups(department_head, today)["kaugemal"]

    assert far.is_far
    assert far.first is not None
    assert far.first.matter.title == "Novembri eelnõu", "the far line names the nearest date"
    assert far.beyond_first == 1
    assert far.shown == 1
