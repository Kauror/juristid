"""`PRAEGUNE TEGEVUS` reads earliest day first, whatever kind each row is (#525).

The owner saw `04.11 — Koostan arvamuse` (the current step) above
`02.11 — Ootame tagasisidet` (an open round): the zone drew the current step,
then the waiting rounds, then the planned actions, each block sorted only
within itself. The rule is that an earlier day comes before a later one.

What is asserted here is the **DOM order** of the rows in `#praegune-ootused`
— on a full page load, after every HTMX save that answers with the whole
column, and in the out-of-band list a round's own answer carries — and that
the ordering is display-only: the current step stays the current step, every
row keeps its controls, a reader gets none, and an administrator gets no page.
"""

from __future__ import annotations

import datetime as dt
import re
from types import SimpleNamespace

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.current_work import chronological_rows
from app.matters.enums import EngagementKind
from app.matters.services import add_engagement, engagement_revision_token
from app.workflow.dates import format_at_precision
from app.workflow.enums import ActionStatus, DatePrecision
from app.workflow.models import NextAction
from app.workflow.services import add_planned_action, set_next_action_for_new_work
from tests import factories

pytestmark = pytest.mark.django_db

#: A row's identity in the list, by kind: `C` the current step, `W<pk>` a
#: waiting round, `P<pk>` a planned action.
ROW = re.compile(
    r'data-work-row="current"'
    r'|data-feedback-wait="(?P<wait>[^"]+)"'
    r'|<li class="curact__plannedrow" id="(?:tegevus|jarelkontroll)-(?P<planned>[^"]+)"'
)


def _day(offset: int) -> dt.date:
    return timezone.localdate() + dt.timedelta(days=offset)


def _list_html(html: str) -> str:
    start = html.index('id="praegune-ootused"')
    return html[start : html.index("</ul>", start)]


def _order(html: str) -> list[str]:
    rows = []
    for match in ROW.finditer(_list_html(html)):
        if match.group("wait"):
            rows.append(f"W{match.group('wait')}")
        elif match.group("planned"):
            rows.append(f"P{match.group('planned')}")
        else:
            rows.append("C")
    return rows


def _page(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _current(matter, *, text: str = "Koostan arvamuse", day: int | None, **extra) -> NextAction:
    return set_next_action_for_new_work(
        matter=matter,
        text=text,
        target_date=_day(day) if day is not None else None,
        actor=matter.owner,
        **extra,
    )


def _round(matter, *, day: int | None, title: str = "Liikmete küsitlus"):
    return add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title=title,
        occurred_on=timezone.localdate() - dt.timedelta(days=1),
        feedback_deadline=_day(day) if day is not None else None,
        actor=matter.owner,
    )


def _planned(matter, *, text: str, day: int) -> NextAction:
    return add_planned_action(matter=matter, text=text, target_date=_day(day), actor=matter.owner)


def _htmx(client, name: str, matter, data: dict, **kwargs):
    return client.post(
        reverse(f"matters:{name}", kwargs={"pk": matter.pk, **kwargs}),
        data,
        headers={"HX-Request": "true"},
    )


# ---------------------------------------------------------------------------
# Full page load
# ---------------------------------------------------------------------------


def test_the_owners_case_a_wait_due_earlier_reads_above_the_current_step(signed_in, specialist):
    """#525 as reported: a round due on the 2nd above a step due on the 4th."""
    matter = factories.MatterFactory(owner=specialist)
    _current(matter, day=12)
    wait = _round(matter, day=10)

    assert _order(_page(signed_in, matter)) == [f"W{wait.pk}", "C"]


def test_a_current_step_due_earlier_reads_above_the_wait(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _current(matter, day=10)
    wait = _round(matter, day=12)

    assert _order(_page(signed_in, matter)) == ["C", f"W{wait.pk}"]


def test_several_waits_and_planned_actions_interleave_by_day(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _current(matter, day=8)
    late_wait = _round(matter, day=20, title="Teine voor")
    early_wait = _round(matter, day=3, title="Esimene voor")
    late_plan = _planned(matter, text="Saadan arvamuse", day=15)
    early_plan = _planned(matter, text="Koondan vastused", day=5)

    assert _order(_page(signed_in, matter)) == [
        f"W{early_wait.pk}",
        f"P{early_plan.pk}",
        "C",
        f"P{late_plan.pk}",
        f"W{late_wait.pk}",
    ]


def test_an_equal_day_keeps_a_stable_order_current_then_waits_then_planned(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _current(matter, day=6)
    first_plan = _planned(matter, text="Esimene plaan", day=6)
    wait_b = _round(matter, day=6, title="B")
    second_plan = _planned(matter, text="Teine plaan", day=6)
    wait_a = _round(matter, day=6, title="A")
    waits = sorted([wait_a.pk, wait_b.pk])

    expected = ["C", f"W{waits[0]}", f"W{waits[1]}", f"P{first_plan.pk}", f"P{second_plan.pk}"]
    assert _order(_page(signed_in, matter)) == expected
    # Drawn twice, the same answer.
    assert _order(_page(signed_in, matter)) == expected


def test_undated_rows_follow_every_dated_row_and_say_so(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _current(matter, day=None)
    undated_wait = _round(matter, day=None, title="Ilma tähtajata")
    dated_wait = _round(matter, day=30, title="Tähtajaga")
    plan = _planned(matter, text="Koondan vastused", day=40)

    html = _page(signed_in, matter)

    assert _order(html) == [f"W{dated_wait.pk}", f"P{plan.pk}", "C", f"W{undated_wait.pk}"]
    rows = _list_html(html)
    assert "Kuupäev määramata" in rows
    assert "Tähtaeg määramata" in rows


def test_an_approximate_step_sorts_by_its_period_anchor_and_keeps_its_wording():
    """The same anchor `NextActionQuerySet.planned` orders by (ADR 0079)."""
    step = NextAction(
        text="Novembris", target_date=dt.date(2026, 11, 1), date_precision=DatePrecision.MONTH
    )
    wait_early = SimpleNamespace(pk=1, feedback_deadline=dt.date(2026, 10, 30))
    wait_late = SimpleNamespace(pk=2, feedback_deadline=dt.date(2026, 11, 5))

    rows = chronological_rows(
        current_action=step, feedback_waits=[wait_early, wait_late], planned_actions=[]
    )

    assert [row.record for row in rows] == [wait_early, step, wait_late]
    assert step.date_display == format_at_precision(step.target_date, DatePrecision.MONTH)
    assert step.date_display != "01.11.2026"


def test_the_order_is_display_only(signed_in, specialist):
    """A planned action due before the current step is drawn first and stays planned."""
    matter = factories.MatterFactory(owner=specialist)
    current = _current(matter, day=12)
    plan = _planned(matter, text="Varem planeeritud", day=4)

    assert _order(_page(signed_in, matter)) == [f"P{plan.pk}", "C"]
    current.refresh_from_db()
    plan.refresh_from_db()
    assert current.status == ActionStatus.OPEN
    assert plan.status == ActionStatus.PLANNED


def test_without_a_current_step_the_waits_still_read_by_day(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    late_wait = _round(matter, day=14, title="Hiljem")
    undated = _round(matter, day=None, title="Tähtajata")
    early_wait = _round(matter, day=9, title="Varem")
    assert not NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).exists()

    assert _order(_page(signed_in, matter)) == [
        f"W{early_wait.pk}",
        f"W{late_wait.pk}",
        f"W{undated.pk}",
    ]


# ---------------------------------------------------------------------------
# Controls and permissions travel with their rows
# ---------------------------------------------------------------------------


def test_every_row_keeps_its_own_controls(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    current = _current(matter, day=12)
    wait = _round(matter, day=10)
    plan = _planned(matter, text="Koondan vastused", day=11)

    rows = _list_html(_page(signed_in, matter))

    # The current step: `✓ Tehtud` with its form named by `action_id`, `Muuda`.
    current_row = rows[rows.index('id="tegevus-' + str(current.pk)) :]
    assert 'for="tehtud-valik">✓ Tehtud</label>' in current_row
    assert f'value="{current.pk}"' in current_row
    assert 'id="lisa-jargmine"' in current_row
    # The round: `Tehtud` opens `Lisa tagasiside` with this round chosen.
    assert f'data-open-feedback="{wait.pk}"' in rows
    # The planned row: `✓ Tehtud`, `Muuda` and `×`, each for this action.
    assert f'id="planeeritud-tehtud-{plan.pk}"' in rows
    assert f'id="planeeritud-{plan.pk}"' in rows
    assert (
        reverse("matters:cancel_planned_action", kwargs={"pk": matter.pk, "action_id": plan.pk})
        in rows
    )
    # `+ Lisa tegevus` stays outside the sorted list, after it.
    page = _page(signed_in, matter)
    assert page.index('id="lisa-planeeritud"') > page.index("</ul>", page.index("praegune-ootused"))


def test_a_reader_sees_the_ordered_rows_without_any_control(client, specialist, reader):
    matter = factories.MatterFactory(owner=specialist)
    _current(matter, day=12)
    wait = _round(matter, day=10)
    plan = _planned(matter, text="Koondan vastused", day=11)
    client.force_login(reader)

    html = _page(client, matter)
    rows = _list_html(html)

    assert _order(html) == [f"W{wait.pk}", f"P{plan.pk}", "C"]
    assert "✓ Tehtud" not in rows
    assert "data-open-feedback" not in rows
    assert "Kustuta planeeritud tegevus" not in rows
    assert 'id="lisa-jargmine"' not in rows


def test_an_account_without_business_read_gets_no_rows(client, specialist, administrator):
    matter = factories.MatterFactory(owner=specialist)
    _current(matter, text="Salajane samm", day=12)
    _round(matter, day=10)
    client.force_login(administrator)

    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))

    assert response.status_code in {403, 404}
    assert "Salajane samm" not in response.content.decode()


# ---------------------------------------------------------------------------
# After HTMX saves
# ---------------------------------------------------------------------------


def test_moving_the_current_steps_day_past_a_wait_reorders_the_column(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    current = _current(matter, day=5)
    wait = _round(matter, day=10)
    assert _order(_page(signed_in, matter)) == ["C", f"W{wait.pk}"]

    response = _htmx(
        signed_in,
        "set_action",
        matter,
        {
            "action_id": str(current.pk),
            "text": "Koostan arvamuse",
            "target_date": _day(15).strftime("%d.%m.%Y"),
        },
    )

    assert response.status_code == 200
    new_current = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert new_current.target_date == _day(15)
    assert _order(response.content.decode()) == [f"W{wait.pk}", "C"]


def test_adding_a_planned_action_lands_in_its_place(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _current(matter, day=12)
    wait = _round(matter, day=4)

    response = _htmx(
        signed_in,
        "add_planned_action",
        matter,
        {"text": "Koondan vastused", "target_date": _day(8).isoformat()},
    )

    plan = NextAction.objects.get(matter=matter, status=ActionStatus.PLANNED)
    assert _order(response.content.decode()) == [f"W{wait.pk}", f"P{plan.pk}", "C"]


def test_changing_a_planned_day_moves_the_row(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _current(matter, day=10)
    plan = _planned(matter, text="Koondan vastused", day=20)

    response = _htmx(
        signed_in,
        "change_planned_action",
        matter,
        {"text": "Koondan vastused", "target_date": _day(3).isoformat()},
        action_id=plan.pk,
    )

    moved = NextAction.objects.get(matter=matter, status=ActionStatus.PLANNED)
    assert _order(response.content.decode()) == [f"P{moved.pk}", "C"]


def test_completing_the_current_step_promotes_as_before_and_redraws_in_order(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    current = _current(matter, day=2)
    wait = _round(matter, day=10)
    later = _planned(matter, text="Saadan arvamuse", day=14)
    sooner = _planned(matter, text="Koondan vastused", day=6)

    response = _htmx(
        signed_in,
        "complete_current_action",
        matter,
        {"action_id": str(current.pk), "body": "<p>Arvamus koostatud.</p>"},
    )

    assert response.status_code == 200
    current.refresh_from_db()
    assert current.status == ActionStatus.COMPLETED
    # The earliest planned action is promoted — the promotion rule, untouched.
    promoted = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)
    assert promoted.pk == sooner.pk
    assert _order(response.content.decode()) == ["C", f"W{wait.pk}", f"P{later.pk}"]


def _oob_list(response) -> str:
    body = response.content.decode()
    match = re.search(r'<ul id="praegune-ootused"[^>]*hx-swap-oob="true"[^>]*>.*?</ul>', body, re.S)
    assert match is not None, "the answer carries no out-of-band list"
    return match.group(0)


def test_a_rounds_new_deadline_takes_its_place_in_the_out_of_band_list(signed_in, specialist):
    """`Ootan tagasisidet` answers with the row; the list rides along re-sorted."""
    matter = factories.MatterFactory(owner=specialist)
    current = _current(matter, day=8)
    plan = _planned(matter, text="Koondan vastused", day=12)
    engagement = add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title="Liikmete küsitlus",
        occurred_on=timezone.localdate() - dt.timedelta(days=1),
        lifecycle_tracked=False,
        actor=specialist,
    )
    assert _order(_page(signed_in, matter)) == ["C", f"P{plan.pk}"]

    engagement.refresh_from_db()
    response = signed_in.post(
        reverse(
            "matters:open_engagement_wait",
            kwargs={"pk": matter.pk, "engagement_id": engagement.pk},
        ),
        {
            "revision": engagement_revision_token(engagement),
            "feedback_deadline": _day(3).strftime("%d.%m.%Y"),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    oob = _oob_list(response)
    assert _order(oob) == [f"W{engagement.pk}", "C", f"P{plan.pk}"]
    # The rows that carry forms are not resent: the page keeps its own
    # elements for them, in their new place (`hx-preserve`).
    assert f'id="tegevus-{current.pk}" hx-preserve' in oob
    assert f'id="tegevus-{plan.pk}" hx-preserve' in oob
    assert "<form" not in oob.replace("data-open-feedback", "")
    assert 'id="tehtud"' not in oob


def test_the_page_render_itself_preserves_nothing(signed_in, specialist):
    """`hx-preserve` only on the out-of-band copy: a full render redraws every row."""
    matter = factories.MatterFactory(owner=specialist)
    _current(matter, day=8)
    _planned(matter, text="Koondan vastused", day=12)
    _round(matter, day=3)

    assert "hx-preserve" not in _page(signed_in, matter)
