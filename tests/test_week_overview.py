"""«Selle nädala ülevaade» (r97): every figure is a counted record, never a guess.

The rules held here, in the order the ADR states them:

* a **postponement is not a completion** — a day moved on is *edasi lükatud*, a
  changed sentence is *asendatud*, and only a COMPLETED row is *tehtud*;
* an ending with **no person behind it** (the system, the synchronisation) is
  nobody's work, and somebody else's completion is not mine;
* the week is Monday to Sunday in **Europe/Tallinn**, so a completion at 23:30 on
  Sunday belongs to that week and one at 00:10 on Monday to the next;
* a **WAIT/MONITOR review date** that has passed is never an overdue obligation;
* the figure and the page behind it are **one population** — the records page
  lists exactly the rows the overview counted, for the reader asking only.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from app.matters import week_overview as wo
from app.matters.models import Matter
from app.matters.my_work import build_my_work
from app.matters.services import create_matter
from app.workflow.enums import ActionKind, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import (
    cancel_next_action,
    complete_next_action,
    set_next_action,
)

pytestmark = pytest.mark.django_db

TALLINN = ZoneInfo("Europe/Tallinn")
#: Friday 9 October 2026; its week is Monday 5 – Sunday 11 October.
FRIDAY = dt.date(2026, 10, 9)
MONDAY = dt.date(2026, 10, 5)


def at(day: dt.date, hour: int = 12, minute: int = 0) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=TALLINN)


def a_matter(owner: Any, title: str = "Teema") -> Matter:
    return create_matter(owner=owner, actor=owner, title=title)


def step(
    matter: Matter, owner: Any, text: str = "Koosta kiri", day: dt.date | None = FRIDAY, **kw: Any
) -> NextAction:
    return set_next_action(
        matter=matter, text=text, target_date=day, responsible=owner, actor=owner, **kw
    )


def ended(action: NextAction, when: dt.datetime) -> NextAction:
    """Pin the ending to a controlled Tallinn moment (the service stamps now())."""
    NextAction.objects.filter(pk=action.pk).update(ended_at=when)
    action.refresh_from_db()
    return action


def overview(user: Any, today: dt.date = FRIDAY, *, subject: Any = None) -> wo.WeekOverview:
    work = build_my_work(user, today=today, subject=subject if subject is not None else user)
    assert work.overview is not None
    return work.overview


def value(figures: list[wo.WeekFigure], key: str) -> int:
    figure = next(f for f in figures if f.key == key)
    assert figure.cell.value is not None
    return figure.cell.value


# --- completion vs postponement ------------------------------------------------


def test_a_completed_action_is_done_and_a_postponed_one_is_not(specialist) -> None:
    done_matter, moved_matter = a_matter(specialist, "A"), a_matter(specialist, "B")
    finished = complete_next_action(action=step(done_matter, specialist), actor=specialist)
    ended(finished, at(dt.date(2026, 10, 7)))
    first = step(moved_matter, specialist, day=dt.date(2026, 10, 8))
    second = step(moved_matter, specialist, day=dt.date(2026, 10, 15))  # same words, later day
    ended(first, at(dt.date(2026, 10, 8)))
    assert second.pk != first.pk

    figures = overview(specialist).actions
    assert value(figures, wo.DONE) == 1
    assert value(figures, wo.POSTPONED) == 1
    assert value(figures, wo.SUPERSEDED) == 0
    assert value(figures, wo.CANCELLED) == 0


def test_a_changed_sentence_is_a_replacement_not_a_postponement(specialist) -> None:
    matter = a_matter(specialist)
    old = step(matter, specialist, "Esimene", dt.date(2026, 10, 8))
    step(matter, specialist, "Hoopis teine", dt.date(2026, 10, 20))
    ended(old, at(dt.date(2026, 10, 8)))
    figures = overview(specialist).actions
    assert (
        value(figures, wo.SUPERSEDED),
        value(figures, wo.POSTPONED),
        value(figures, wo.DONE),
    ) == (1, 0, 0)


def test_a_cancelled_action_is_cancelled_never_done(specialist) -> None:
    action = step(a_matter(specialist), specialist)
    ended(cancel_next_action(action=action, actor=specialist), at(dt.date(2026, 10, 6)))
    figures = overview(specialist).actions
    assert (value(figures, wo.CANCELLED), value(figures, wo.DONE)) == (1, 0)


def test_somebody_elses_completion_and_the_systems_are_not_mine(
    specialist, other_specialist
) -> None:
    mine = a_matter(specialist, "Minu")
    theirs = complete_next_action(action=step(mine, specialist), actor=other_specialist)
    ended(theirs, at(dt.date(2026, 10, 6)))
    automatic = a_matter(specialist, "Automaatne")
    system = complete_next_action(action=step(automatic, specialist), actor=None)
    ended(system, at(dt.date(2026, 10, 6)))
    assert value(overview(specialist).actions, wo.DONE) == 0
    assert value(overview(other_specialist).actions, wo.DONE) == 1


def test_a_completion_and_its_successor_are_two_facts(specialist) -> None:
    matter = a_matter(specialist)
    first = complete_next_action(action=step(matter, specialist), actor=specialist)
    ended(first, at(dt.date(2026, 10, 6)))
    step(matter, specialist, "Järgmine", dt.date(2026, 10, 12))
    figures = overview(specialist).actions
    assert value(figures, wo.DONE) == 1
    assert value(figures, wo.OUTSTANDING) == 0  # the successor is due next week, not owed by Sunday


# --- the week is Monday to Sunday in Tallinn ----------------------------------


@pytest.mark.parametrize(
    ("moment", "counted"),
    [
        pytest.param(at(dt.date(2026, 10, 4), 23, 30), False, id="sunday-before-23-30"),
        pytest.param(at(MONDAY, 0, 10), True, id="monday-00-10"),
        pytest.param(at(dt.date(2026, 10, 11), 23, 30), True, id="sunday-23-30"),
        pytest.param(at(dt.date(2026, 10, 12), 0, 10), False, id="next-monday-00-10"),
    ],
)
def test_the_week_boundary_is_the_tallinn_midnight(specialist, moment, counted) -> None:
    action = complete_next_action(action=step(a_matter(specialist), specialist), actor=specialist)
    ended(action, moment)
    assert value(overview(specialist).actions, wo.DONE) == int(counted)


# --- review dates are not obligations -----------------------------------------


def test_a_passed_review_date_is_not_an_overdue_obligation(specialist) -> None:
    matter = a_matter(specialist)
    step(
        matter,
        specialist,
        "Ootan ministeeriumi vastust",
        dt.date(2026, 10, 1),
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
    )
    view = overview(specialist)
    assert value(view.actions, wo.OUTSTANDING) == 0
    assert not [row for row in view.seven_days if row.is_late]


def test_the_seven_day_panel_holds_open_work_only(specialist) -> None:
    kept, finished = a_matter(specialist, "Avatud"), a_matter(specialist, "Tehtud")
    step(kept, specialist, "Kirjuta", dt.date(2026, 10, 12))
    complete_next_action(
        action=step(finished, specialist, "Valmis", dt.date(2026, 10, 12)), actor=specialist
    )
    rows = overview(specialist).seven_days
    assert [row.matter.pk for row in rows] == [kept.pk]
    assert all(row.when <= FRIDAY + dt.timedelta(days=6) for row in rows)


# --- the figure and the page behind it are one population ---------------------


@pytest.mark.parametrize("kind", [wo.DONE, wo.POSTPONED, wo.CANCELLED, wo.SUPERSEDED])
def test_the_records_page_lists_exactly_what_the_figure_counted(client, specialist, kind) -> None:
    done = complete_next_action(
        action=step(a_matter(specialist, "D"), specialist), actor=specialist
    )
    ended(done, at(dt.date(2026, 10, 6)))
    gone = cancel_next_action(action=step(a_matter(specialist, "C"), specialist), actor=specialist)
    ended(gone, at(dt.date(2026, 10, 6)))
    moved = a_matter(specialist, "P")
    first_move = step(moved, specialist, day=dt.date(2026, 10, 7))
    step(moved, specialist, day=dt.date(2026, 10, 14))
    ended(first_move, at(dt.date(2026, 10, 7)))
    swapped = a_matter(specialist, "S")
    first_swap = step(swapped, specialist, "x", dt.date(2026, 10, 7))
    step(swapped, specialist, "y", dt.date(2026, 10, 9))
    ended(first_swap, at(dt.date(2026, 10, 7)))

    figure = next(f for f in overview(specialist).actions if f.key == kind)
    client.force_login(specialist)
    response = client.get(figure.url)
    assert response.status_code == 200
    assert len(response.context["rows"]) == figure.cell.value == 1


def test_the_records_page_never_shows_another_persons_rows(
    client, specialist, other_specialist
) -> None:
    done = complete_next_action(action=step(a_matter(specialist), specialist), actor=specialist)
    ended(done, at(dt.date(2026, 10, 6)))
    client.force_login(other_specialist)
    url = wo.records_url(wo.DONE, MONDAY)
    assert client.get(url).context["rows"] == []


def test_the_preset_is_removed_by_a_plain_link_to_the_ordinary_page(client, specialist) -> None:
    client.force_login(specialist)
    body = client.get(wo.records_url(wo.DONE, MONDAY)).content.decode()
    assert "Eemalda eelseadistus" in body
    assert f'href="{reverse("matters:my_work")}"' in body


def test_a_garbage_preset_falls_back_instead_of_failing(client, specialist) -> None:
    client.force_login(specialist)
    response = client.get(reverse("matters:my_week_records") + "?naitaja=%3Cscript%3E&nadal=nope")
    assert response.status_code == 200


# --- the strip, the scope and the manager's view ------------------------------


def test_the_four_primary_figures_come_first(specialist) -> None:
    keys = [f.key for f in build_my_work(specialist, today=FRIDAY).seis]
    assert keys[:4] == ["overdue", "attention", "opinions_drafting", "open"]


def test_a_colleagues_desk_gets_no_personal_statistics(department_head, specialist) -> None:
    view = overview(department_head, subject=specialist)
    assert view.has_statistics is False
    assert view.previous == [] and view.monitoring == [] and view.recent_opinions == []


def test_the_overview_renders_and_never_writes(
    client, specialist, django_assert_max_num_queries
) -> None:
    a_matter(specialist)
    client.force_login(specialist)
    before = NextAction.objects.count()
    with django_assert_max_num_queries(400):
        response = client.get(reverse("matters:my_work"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Selle nädala ülevaade" in body and "Minu 7 päeva tähtajad" in body
    assert NextAction.objects.count() == before
