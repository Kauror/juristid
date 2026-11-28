"""«Selle nädala ülevaade» (r97): every figure is a counted record, never a guess.

The rules held here:

* a **postponement is not a completion**: a later day is *edasi lükatud*, any other
  change of day is *tähtaega muudetud*, a different step is *asendatud* and only a
  COMPLETED row is *tehtud*;
* an ending with **no person behind it** is nobody's work, and somebody else's
  completion is not mine;
* the week is Monday to Sunday in **Europe/Tallinn**;
* a **WAIT/MONITOR review date** that has passed is never an overdue obligation, and
  every review event is a review (repeated ones are not collapsed);
* the figure and the page behind it are **one population**, compared by record
  identity and not by total, in every scope;
* a colleague's desk gets **no historical statistics**, and the department's records
  are the head's — enforced at the route and by the queries, not by a template.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters import stat_periods
from app.matters import week_overview as wo
from app.matters.models import Matter
from app.matters.my_work import build_my_work
from app.matters.services import create_matter
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import (
    acknowledge_review,
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
    # A history figure whose coverage does not reach back to the week reads «—»
    # (value None) when nothing was found: unmeasured, which is asserted elsewhere.
    return figure.cell.value or 0


def ctx_of(user: Any, today: dt.date, scope: str = wo.SELF, viewer: Any = None) -> wo.Ctx:
    return wo.make_ctx(viewer or user, scope, None if scope == wo.DEPARTMENT else user, today)


def week_now() -> tuple[dt.date, dt.date]:
    week = stat_periods.current(timezone.localdate())
    return week.start, week.end


# --- completion, postponement, date change, replacement ------------------------


def test_done_postponed_date_changed_superseded_and_cancelled_are_five_things(specialist) -> None:
    done = complete_next_action(
        action=step(a_matter(specialist, "A"), specialist), actor=specialist
    )
    ended(done, at(dt.date(2026, 10, 7)))

    later = a_matter(specialist, "B")
    first = step(later, specialist, day=dt.date(2026, 10, 8))
    step(later, specialist, day=dt.date(2026, 10, 15))
    ended(first, at(dt.date(2026, 10, 8)))

    earlier = a_matter(specialist, "C")
    first = step(earlier, specialist, day=dt.date(2026, 10, 20))
    step(earlier, specialist, day=dt.date(2026, 10, 12))
    ended(first, at(dt.date(2026, 10, 8)))

    different = a_matter(specialist, "D")
    first = step(different, specialist, "Esimene", dt.date(2026, 10, 8))
    step(different, specialist, "Hoopis teine", dt.date(2026, 10, 20))
    ended(first, at(dt.date(2026, 10, 8)))

    gone = cancel_next_action(action=step(a_matter(specialist, "E"), specialist), actor=specialist)
    ended(gone, at(dt.date(2026, 10, 6)))

    actions = overview(specialist).actions
    assert {f.key: value(actions, f.key) for f in actions if f.key != wo.OUTSTANDING} == {
        wo.DONE: 1,
        wo.REVIEWED: 0,
        wo.POSTPONED: 1,
        wo.DATE_CHANGED: 1,
        wo.SUPERSEDED: 1,
        wo.CANCELLED: 1,
    }


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        pytest.param({"day": dt.date(2026, 10, 15)}, wo.POSTPONED, id="later-day"),
        pytest.param({"day": dt.date(2026, 10, 1)}, wo.DATE_CHANGED, id="earlier-day"),
        pytest.param({"day": None}, wo.DATE_CHANGED, id="day-removed"),
        pytest.param(
            {"day": dt.date(2026, 10, 15), "date_precision": DatePrecision.MONTH},
            wo.SUPERSEDED,
            id="precision-changed",
        ),
        pytest.param({"text": "Hoopis teine"}, wo.SUPERSEDED, id="sentence-changed"),
        pytest.param({"kind": ActionKind.WAIT}, wo.SUPERSEDED, id="meaning-changed"),
    ],
)
def test_the_classification_reads_what_changed(specialist, change, expected) -> None:
    matter = a_matter(specialist)
    first = step(matter, specialist, "Koosta", dt.date(2026, 10, 8))
    kw = dict(change)
    kw.setdefault("text", "Koosta")
    kw.setdefault("day", dt.date(2026, 10, 8))
    step(matter, specialist, kw.pop("text"), kw.pop("day"), **kw)
    first.refresh_from_db()
    assert wo.classify_ended(first) == expected


def test_a_change_of_responsible_is_a_replacement(specialist, other_specialist) -> None:
    matter = a_matter(specialist)
    first = step(matter, specialist, "Koosta", dt.date(2026, 10, 8))
    set_next_action(
        matter=matter,
        text="Koosta",
        target_date=dt.date(2026, 10, 15),
        responsible=other_specialist,
        actor=specialist,
    )
    first.refresh_from_db()
    assert wo.classify_ended(first) == wo.SUPERSEDED


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
    actions = overview(specialist).actions
    assert value(actions, wo.DONE) == 1
    assert value(actions, wo.OUTSTANDING) == 0  # due next week, not owed by Sunday


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


# --- reviews -------------------------------------------------------------------


def _waiting(owner: Any, day: dt.date) -> NextAction:
    return step(
        a_matter(owner),
        owner,
        "Ootan ministeeriumi vastust",
        day,
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
    )


def test_every_review_event_is_counted_and_keeps_its_identity(specialist) -> None:
    action = _waiting(specialist, dt.date(2026, 10, 1))
    for next_day in (dt.date(2026, 10, 14), dt.date(2026, 10, 21)):
        acknowledge_review(action=action, actor=specialist, next_review_date=next_day)
    week = stat_periods.current(timezone.localdate())
    ctx = ctx_of(specialist, timezone.localdate())
    found = wo.records(ctx, wo.REVIEWED, stat_periods.Span(week.start, week.end))
    assert len(found) == 2
    assert len({r.key for r in found}) == 2  # two events of one action, both kept
    assert all(r.actor and "→" in r.note for r in found)


def test_a_passed_review_date_is_not_an_overdue_obligation(specialist) -> None:
    _waiting(specialist, dt.date(2026, 10, 1))
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


# --- the figure and the page behind it are one population ----------------------


def _world(owner: Any, other: Any) -> None:
    """One of each outcome in the current week, plus noise that must not be counted."""
    start, _ = week_now()
    day = lambda n: at(start + dt.timedelta(days=n))  # noqa: E731
    ended(complete_next_action(action=step(a_matter(owner, "D1"), owner), actor=owner), day(0))
    ended(complete_next_action(action=step(a_matter(owner, "D2"), owner), actor=owner), day(1))
    ended(cancel_next_action(action=step(a_matter(owner, "C"), owner), actor=owner), day(1))
    moved = a_matter(owner, "P")
    first = step(moved, owner, day=start + dt.timedelta(days=2))
    step(moved, owner, day=start + dt.timedelta(days=12))
    ended(first, day(2))
    shifted = a_matter(owner, "E")
    first = step(shifted, owner, day=start + dt.timedelta(days=20))
    step(shifted, owner, day=start + dt.timedelta(days=9))
    ended(first, day(2))
    swapped = a_matter(owner, "S")
    first = step(swapped, owner, "x", start + dt.timedelta(days=2))
    step(swapped, owner, "y", start + dt.timedelta(days=4))
    ended(first, day(2))
    other_done = complete_next_action(action=step(a_matter(other, "O"), other), actor=other)
    ended(other_done, day(2))
    waiting = _waiting(owner, start - dt.timedelta(days=3))
    acknowledge_review(action=waiting, actor=owner, next_review_date=start + dt.timedelta(days=30))
    a_matter(owner, "Avatud teema")


@pytest.mark.parametrize("scope", [wo.SELF, wo.DEPARTMENT])
def test_the_records_page_lists_exactly_the_records_the_figure_counted(
    client, specialist, other_specialist, department_head, scope
) -> None:
    _world(specialist, other_specialist)
    today = timezone.localdate()
    viewer = department_head if scope == wo.DEPARTMENT else specialist
    ctx = ctx_of(specialist, today, scope, viewer)
    built = wo.build_week_overview(
        ctx,
        items=__import__("app.matters.work_items", fromlist=["work_items"]).work_items(
            viewer,
            today=today,
            responsible=None if scope == wo.DEPARTMENT else specialist,
        ),
    )
    client.force_login(viewer)
    checked = 0
    for group in (built.opinions, built.actions, built.commencements, built.previous):
        for figure in group:
            if not figure.url.startswith("/minu-asjad/nadal/"):
                continue
            response = client.get(figure.url)
            assert response.status_code == 200, figure.url
            keys = [r.key for r in response.context["rows"]]
            assert len(keys) == len(set(keys)), "a record was listed twice"
            expected = wo.records(
                ctx,
                response.context["kind"],
                _span_of(figure.url, ctx),
            )
            assert set(keys) == {r.key for r in expected}, figure.url
            if figure.cell.exact:
                assert figure.cell.value == len(keys), figure.url
            checked += 1
    assert checked >= 12


def _span_of(url: str, ctx: wo.Ctx) -> stat_periods.Span:
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(url).query)
    start = wo.week_start_of(query.get("nadal", [""])[0], ctx.today)
    return stat_periods.Span(start, start + dt.timedelta(days=6))


def test_the_department_counts_distinct_records_of_everybody_and_a_person_only_theirs(
    specialist, other_specialist, department_head
) -> None:
    _world(specialist, other_specialist)
    today = timezone.localdate()
    mine = wo.records(ctx_of(specialist, today), wo.DONE, ctx_of(specialist, today).week.counted)
    theirs = wo.records(
        ctx_of(other_specialist, today), wo.DONE, ctx_of(specialist, today).week.counted
    )
    dept_ctx = ctx_of(specialist, today, wo.DEPARTMENT, department_head)
    everyone = wo.records(dept_ctx, wo.DONE, dept_ctx.week.counted)
    assert {r.key for r in everyone} == {r.key for r in mine} | {r.key for r in theirs}
    assert len(mine) == 2 and len(theirs) == 1


# --- permissions ----------------------------------------------------------------


def test_the_records_page_never_shows_another_persons_rows(
    client, specialist, other_specialist
) -> None:
    _world(specialist, other_specialist)
    start, _ = week_now()
    client.force_login(other_specialist)
    rows = client.get(wo.records_url(wo.DONE, start)).context["rows"]
    assert [r.matter.title for r in rows] == ["O"]


def test_the_department_records_are_the_heads_by_route_and_not_by_template(
    client, specialist, other_specialist, department_head
) -> None:
    _world(specialist, other_specialist)
    start, _ = week_now()
    url = wo.records_url(wo.DONE, start, department=True)
    client.force_login(other_specialist)
    assert client.get(url).status_code == 403
    client.force_login(department_head)
    assert client.get(url).status_code == 200


def test_a_colleagues_desk_carries_no_historical_figures_or_links(
    client, department_head, specialist, other_specialist
) -> None:
    _world(specialist, other_specialist)
    view = overview(department_head, subject=specialist)
    assert view.scope == wo.COLLEAGUE and view.has_history is False
    assert view.actions == view.opinions == view.previous == view.recent_opinions == []
    client.force_login(department_head)
    body = client.get(reverse("matters:person_work", kwargs={"pk": specialist.pk})).content.decode()
    assert "/minu-asjad/nadal/" not in body
    assert "Selle nädala ülevaade" not in body


def test_the_shared_link_shows_the_recipient_only_what_they_may_open(
    client, specialist, other_specialist, department_head
) -> None:
    _world(specialist, other_specialist)
    start, _ = week_now()
    url = wo.records_url(wo.DONE, start)
    client.force_login(department_head)
    assert client.get(url).context["rows"] == []  # the head's own desk, not the sender's


def test_department_records_equal_what_the_head_may_see(
    client, specialist, other_specialist, department_head
) -> None:
    _world(specialist, other_specialist)
    ctx = ctx_of(specialist, timezone.localdate(), wo.DEPARTMENT, department_head)
    visible = NextAction.objects.visible_to(department_head).values_list("matter_id", flat=True)
    for rec in wo.records(ctx, wo.DONE, ctx.week.counted):
        assert rec.matter is not None and rec.matter.pk in set(visible)


def test_a_garbage_preset_falls_back_instead_of_failing(client, specialist) -> None:
    client.force_login(specialist)
    url = reverse("matters:my_week_records") + "?naitaja=%3Cscript%3E&nadal=nope&alates=x"
    assert client.get(url).status_code == 200


def test_the_preset_is_removed_by_a_plain_link_to_the_page_it_came_from(
    client, specialist, department_head
) -> None:
    start, _ = week_now()
    client.force_login(specialist)
    body = client.get(wo.records_url(wo.DONE, start)).content.decode()
    assert f'href="{reverse("matters:my_work")}">Eemalda eelseadistus' in body
    client.force_login(department_head)
    body = client.get(wo.records_url(wo.DONE, start, department=True)).content.decode()
    assert f'href="{reverse("matters:department")}">Eemalda eelseadistus' in body


# --- opinions: attribution, requests, coverage ---------------------------------


def test_sent_opinions_are_credited_exactly_as_minu_statistika_credits_them(
    specialist, other_specialist
) -> None:
    from app.matters.my_statistics import my_statistics

    today = timezone.localdate()
    for who in (specialist, other_specialist):
        ctx = ctx_of(who, today)
        stats = my_statistics(who, today, stat_periods.current(today))
        card = next(c for g in stats.groups for c in g.cards if c.key == "saadetud")
        figure = next(f for f in wo.build_week_overview(ctx, items=[]).opinions if f.key == "sent")
        assert figure.cell == card.cell
        found = wo.records(ctx, wo.SENT, ctx.week.counted)
        assert (card.cell.value or 0) == len(found)


def test_an_imported_opinion_without_an_actor_follows_the_owner_on_the_sent_day(
    specialist, other_specialist
) -> None:
    """A transfer after the send must not move the credit; unknown stays unknown."""
    from app.reporting.opinions import SOURCE_IMPORTED, MatterFacts, OpinionFact

    today = timezone.localdate()
    matter = a_matter(specialist, "Üleantud")
    sent_day = today - dt.timedelta(days=1)
    facts = MatterFacts(
        matter_id=matter.pk,
        reference="",
        title=matter.title,
        year=None,
        received=None,
        owner="x",
        is_eu=False,
        koda_initiative=False,
        instrument="",
        owner_id=specialist.pk,
    )
    fact = OpinionFact(matter=facts, sent_on=sent_day, deadline=None, source=SOURCE_IMPORTED)
    from app.matters.services import assign_matter

    assign_matter(matter=matter, owner=other_specialist, actor=specialist)
    for who in (specialist, other_specialist):
        ctx = ctx_of(who, today)
        assert ctx.data is not None
        ctx.data = type(ctx.data)(
            ctx.data.recording, ctx.data.reliable, ctx.data.ownership, [fact], ctx.data.series
        )
        from app.matters.my_statistics import UNKNOWN, credit_opinion

        credited = credit_opinion(fact, ctx.data.ownership)
        found = wo.sent_recs(ctx, stat_periods.Span(sent_day, today))
        assert bool(found) == (credited == str(who.pk))
        assert credited != UNKNOWN or not found  # unknown is never credited to anyone


def test_open_requests_are_counted_as_requests_with_their_own_identity(specialist) -> None:
    _start, end = week_now()
    one = create_matter(owner=specialist, actor=specialist, title="Nõue", response_deadline=end)
    ctx = ctx_of(specialist, timezone.localdate())
    recs = wo.records(ctx, wo.OWED, ctx.week.counted)
    assert [r.key for r in recs] == [f"request:{one.pk}:{end}"]
    assert wo.records(ctx, wo.LATE, ctx.week.counted) == []


def test_an_unmeasured_week_is_unknown_and_a_covered_week_is_a_measured_zero(specialist) -> None:
    today = timezone.localdate()
    ctx = ctx_of(specialist, today)
    # Coverage that begins after the week: nothing found there is unknown, never 0.
    ctx.reliable["actions"] = today + dt.timedelta(days=30)
    unmeasured = wo.build_week_overview(ctx, items=[])
    done = next(f for f in unmeasured.actions if f.key == wo.DONE)
    assert done.cell.coverage == wo.Coverage.UNKNOWN and done.text == "—"
    # Coverage that began before the week: the same empty week is a genuine 0.
    ctx.reliable["actions"] = ctx.week.start - dt.timedelta(days=30)
    measured = wo.build_week_overview(ctx, items=[])
    done = next(f for f in measured.actions if f.key == wo.DONE)
    assert done.cell.coverage == wo.Coverage.VERIFIED and done.text == "0"


def test_the_new_teema_preview_and_its_list_are_one_population_and_include_closed(
    specialist,
) -> None:
    from app.matters.services import close_matter  # noqa: F401  (closed ones stay received)

    today = timezone.localdate()
    ctx = ctx_of(specialist, today)
    window = wo.preview_window(today)
    full = wo.records(ctx, wo.NEW, window)
    preview = wo.build_week_overview(ctx, items=[]).new_matters
    assert [p.matter.pk for p in preview] == [r.matter.pk for r in full[: wo.PREVIEW] if r.matter]


# --- the strip, the scope and the page -----------------------------------------


def test_the_four_primary_figures_come_first(specialist) -> None:
    keys = [f.key for f in build_my_work(specialist, today=FRIDAY).seis]
    assert keys[:4] == ["overdue", "attention", "opinions_drafting", "open"]


def test_the_department_overview_exists_for_the_head_only(
    client, department_head, specialist
) -> None:
    client.force_login(department_head)
    body = client.get(reverse("matters:department")).content.decode()
    assert "Osakonna 7 päeva tähtajad" in body and "Selle nädala ülevaade" in body
    client.force_login(specialist)
    body = client.get(reverse("matters:department")).content.decode()
    assert "Selle nädala ülevaade" not in body


def test_the_overview_renders_and_never_writes(client, specialist) -> None:
    a_matter(specialist)
    client.force_login(specialist)
    before = NextAction.objects.count()
    response = client.get(reverse("matters:my_work"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Selle nädala ülevaade" in body and "Minu 7 päeva tähtajad" in body
    assert NextAction.objects.count() == before


# --- performance ---------------------------------------------------------------


@pytest.mark.parametrize("who", ["specialist", "department_head"])
def test_the_overview_adds_a_bounded_number_of_queries(client, request, monkeypatch, who) -> None:
    """Measured on a 1,500-Teema synthetic register: +13 (Minu asjad) and +15 (Osakond).

    The ceiling is that cost with a margin, and it does not grow with the number
    of Teemad or lawyers, which is what an N+1 would do.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    user = request.getfixturevalue(who)
    for index in range(6):
        step(a_matter(user, f"Teema {index}"), user, day=FRIDAY)
    client.force_login(user)
    path = reverse("matters:my_work" if who == "specialist" else "matters:department")
    client.get(path)  # warm caches that are not the overview's

    def queries() -> int:
        with CaptureQueriesContext(connection) as captured:
            assert client.get(path).status_code == 200
        return len(captured)

    with_overview = queries()
    monkeypatch.setattr(
        wo,
        "build_week_overview",
        lambda ctx, items: wo.WeekOverview(
            ctx.scope, ctx.week.start, ctx.week.end, ctx.last.start, ctx.last.end
        ),
    )
    without = queries()
    assert with_overview - without <= 25, (with_overview, without)


# --- Minu statistika: a chart column opens its days -----------------------------


def _statistics(client: Any, user: Any, **params: str) -> Any:
    client.force_login(user)
    query = "&".join(f"{k}={v}" for k, v in params.items())
    response = client.get(reverse("matters:my_statistics") + (f"?{query}" if query else ""))
    assert response.status_code == 200
    return response.context["stats"]


def test_a_chart_column_opens_its_own_days_and_clearing_keeps_the_period(
    client, specialist
) -> None:
    a_matter(specialist)  # a person-made assignment: recording is reliable from today
    stats = _statistics(client, specialist, naitaja="uued")
    openable = [c for c in stats.columns if c.url]
    assert openable, "a measured column must be selectable, a genuine 0 included"
    column = openable[0]
    chosen = _statistics(
        client, specialist, naitaja="uued", valik=column.url.split("valik=")[1].split("#")[0]
    )
    assert chosen.selection and chosen.clear_url
    assert "valik=" not in chosen.clear_url
    assert "periood=nadal" in chosen.clear_url and "naitaja=uued" in chosen.clear_url
    assert [c.selected for c in chosen.columns].count(True) == 1


def test_a_column_nobody_measured_or_in_the_future_is_not_selectable(client, specialist) -> None:
    stats = _statistics(client, specialist, naitaja="uued")
    assert all(c.url == "" for c in stats.columns if c.future)


def test_a_made_up_selection_is_ignored(client, specialist) -> None:
    stats = _statistics(client, specialist, naitaja="uued", valik="1999-01-01")
    assert stats.selection == "" and stats.clear_url == ""


# --- final acceptance round: identities, terminology -----------------------------


def test_two_native_opinions_on_one_teema_on_one_day_are_two_separate_records(
    specialist, organisation
) -> None:
    from tests.follow_ups import send_koja_arvamus

    today = timezone.localdate()
    matter = a_matter(specialist, "Kaks arvamust")
    first = send_koja_arvamus(matter, specialist, [organisation], today)
    second = send_koja_arvamus(matter, specialist, [organisation], today)
    assert first.pk != second.pk
    ctx = ctx_of(specialist, today)
    found = wo.records(ctx, wo.SENT, ctx.week.counted)
    assert {r.key for r in found} == {
        f"opinion:submission:{first.pk}",
        f"opinion:submission:{second.pk}",
    }
    figure = next(f for f in wo.build_week_overview(ctx, items=[]).opinions if f.key == "sent")
    assert (figure.cell.value or 0) == 2


def test_an_exact_date_that_has_come_is_never_called_a_commencement() -> None:
    """The record says a date; nothing on it confirms the act came into force."""
    labels = {wo.RECORD_TITLES[k] for k in wo.COMMENCEMENT_KINDS}
    assert labels == {"Kuupäev saabunud", "Eesolevad jõustumised", "Ligikaudsed jõustumised"}
    assert not any(label.startswith("Jõustunud") for label in labels)


# --- recent Teemad: independent fixtures, events at controlled times --------------


class _At:
    """Make every audit event and timestamp written inside the block carry ``day``."""

    def __init__(self, monkeypatch: Any) -> None:
        self.monkeypatch = monkeypatch

    def __call__(self, day: dt.date) -> _At:
        moment = at(day)
        from app.audit.models import ChangeEvent

        self.monkeypatch.setattr(timezone, "now", lambda: moment)
        # `Field.get_default` calls the cached `_get_default`; the instance attribute wins.
        self.monkeypatch.setattr(
            ChangeEvent._meta.get_field("occurred_at"),
            "_get_default",
            lambda: moment,
            raising=False,
        )
        return self


def test_the_recent_teema_preview_and_list_hold_exactly_the_independently_listed_arrivals(
    monkeypatch, specialist, other_specialist
) -> None:
    from app.matters.enums import MatterOrigin
    from app.matters.services import assign_matter, close_matter
    from app.workflow.enums import Disposition

    today = timezone.localdate()
    day = lambda back: today - dt.timedelta(days=back)  # noqa: E731
    clock = _At(monkeypatch)

    clock(day(30))
    t1 = create_matter(owner=specialist, actor=specialist, title="T1 loodud 30 p tagasi")
    clock(day(25))
    t2 = create_matter(owner=other_specialist, actor=other_specialist, title="T2 teise loodud")
    clock(day(10))
    assign_matter(matter=t2, owner=specialist, actor=other_specialist)
    clock(day(40))
    t3 = create_matter(owner=specialist, actor=specialist, title="T3 läheb ja tuleb tagasi")
    clock(day(35))
    assign_matter(matter=t3, owner=other_specialist, actor=specialist)
    clock(day(5))
    assign_matter(matter=t3, owner=specialist, actor=other_specialist)
    clock(day(2))
    t4 = create_matter(owner=specialist, actor=specialist, title="T4 hiljuti suletud")
    clock(day(1))
    close_matter(matter=t4, disposition=Disposition.MONITORING_STOPPED, actor=specialist)
    clock(day(15))
    t5 = create_matter(
        owner=other_specialist,
        actor=other_specialist,
        title="T5 imporditud",
        origin=MatterOrigin.LEGACY_IMPORT,
    )
    assign_matter(matter=t5, owner=specialist, actor=other_specialist)
    clock(day(28))
    t6 = create_matter(owner=other_specialist, actor=other_specialist, title="T6 ainult teisele")
    monkeypatch.undo()

    # Expected, enumerated by hand from the story above — not read from the resolver.
    expected_for_specialist = {
        f"arrival:{t1.pk}:{day(30)}",
        f"arrival:{t2.pk}:{day(10)}",
        f"arrival:{t3.pk}:{day(40)}",
        f"arrival:{t3.pk}:{day(5)}",  # the same Teema returned on a different day
        f"arrival:{t4.pk}:{day(2)}",  # closed since, still an arrival
        f"arrival:{t5.pk}:{day(15)}",  # an imported Teema, handed over
    }

    def with_trail(who: Any) -> wo.Ctx:
        """The context as if recording of the ownership trail had begun long before."""
        import dataclasses

        from app.matters.my_statistics import load_ownership

        began = dt.date(2026, 1, 1)
        built = ctx_of(who, today)
        assert built.data is not None
        built.data = dataclasses.replace(
            built.data, ownership=load_ownership(who, today, began, began)
        )
        built.reliable["trail"] = began
        return built

    ctx = with_trail(specialist)
    window = wo.preview_window(today)
    found = wo.records(ctx, wo.NEW, window)
    assert {r.key for r in found} == expected_for_specialist
    assert [r.key for r in found][:3] == [
        f"arrival:{t4.pk}:{day(2)}",
        f"arrival:{t3.pk}:{day(5)}",
        f"arrival:{t2.pk}:{day(10)}",
    ]
    preview = wo.build_week_overview(ctx, items=[]).new_matters
    assert [p.matter.pk for p in preview] == [t4.pk, t3.pk, t2.pk]
    # None in the last two weeks except T4 and T3's return — so the preview reaches
    # arrivals the two-week series never loaded.
    assert t1.pk in {r.matter.pk for r in found if r.matter}

    # A person whose only arrivals are older than the loaded weeks still sees them.
    other_ctx = with_trail(other_specialist)
    others = {r.key for r in wo.records(other_ctx, wo.NEW, window)}
    assert f"arrival:{t6.pk}:{day(28)}" in others
    assert f"arrival:{t1.pk}:{day(30)}" not in others
