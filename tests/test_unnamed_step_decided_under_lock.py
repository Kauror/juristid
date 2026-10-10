"""A step added without naming one is decided under the Matter's lock (AUD-004, AUD-040).

`+ Määra järgmine tegevus` has two forms: the panel drawn when no step is open
(`set_action` with no `action_id`) and the one drawn beside a current step
(`add_planned_action_view`). Both used to decide «is a step open?» on a read
taken before the lock, so a page drawn before a colleague's save — or two saves
landing together — wrote the current step twice and the second silently
superseded the first. Both now go through `workspace.add_unnamed_step`:

* nothing open under the lock → the current step;
* a step open and this one dated → a planned step on that day (with its period);
* a step open and this one undated → `STALE_ACTION_REFUSAL`, nothing written.

The step that was open stays open in every case. The two-connection race is in
`tests/test_unnamed_step_concurrency.py`, because it needs real transactions.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.matters.workspace import STALE_ACTION_REFUSAL, add_unnamed_step
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import set_next_action, set_next_action_for_new_work
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db


def _day(offset: int) -> dt.date:
    return timezone.localdate() + dt.timedelta(days=offset)


@pytest.fixture
def matter(specialist):
    return factories.MatterFactory(owner=specialist, stage=None)


def _post(client, user, name: str, matter, payload: dict) -> object:
    client.force_login(user)
    return client.post(
        reverse(f"matters:{name}", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _steps(matter, status: str) -> list[NextAction]:
    return list(NextAction.objects.filter(matter=matter, status=status).order_by("created_at"))


# ---------------------------------------------------------------------------
# The stale tab, deterministically: A draws the page, B saves, A saves.
# ---------------------------------------------------------------------------


def test_a_stale_dated_save_is_planned_and_the_colleagues_step_stays_current(
    client, matter, specialist, other_specialist
):
    # A draws the Teema page while nothing is open: the direct panel is there.
    client.force_login(specialist)
    drawn = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    assert reverse("matters:set_action", kwargs={"pk": matter.pk}) in drawn.content.decode()

    # B saves a step in the meantime.
    first = _post(client, other_specialist, "set_action", matter, {"text": "Samm B"})
    assert first.status_code == 200
    colleagues = NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)

    # A saves from the page drawn before B's step: 200, and nothing superseded.
    stale = _post(
        client,
        specialist,
        "set_action",
        matter,
        {"text": "Samm A", "target_date": _day(5).strftime("%d.%m.%Y")},
    )

    assert stale.status_code == 200
    colleagues.refresh_from_db()
    assert colleagues.status == ActionStatus.OPEN
    assert [a.text for a in _steps(matter, ActionStatus.OPEN)] == ["Samm B"]
    planned = _steps(matter, ActionStatus.PLANNED)
    assert [(a.text, a.target_date) for a in planned] == [("Samm A", _day(5))]
    assert _steps(matter, ActionStatus.SUPERSEDED) == []


def test_a_stale_undated_save_is_refused_and_writes_nothing(
    client, matter, specialist, other_specialist
):
    client.force_login(specialist)
    client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    _post(client, other_specialist, "set_action", matter, {"text": "Samm B"})
    before = NextAction.objects.filter(matter=matter).count()

    stale = _post(client, specialist, "set_action", matter, {"text": "Samm A"})

    assert STALE_ACTION_REFUSAL in stale.content.decode()
    assert NextAction.objects.filter(matter=matter).count() == before
    assert [a.text for a in _steps(matter, ActionStatus.OPEN)] == ["Samm B"]


def test_the_planned_panel_drawn_before_a_step_existed_files_a_plan_after_one_appears(
    client, matter, specialist, other_specialist
):
    """The beside-a-step panel decides under the lock too, never on the page's read."""
    _post(client, other_specialist, "set_action", matter, {"text": "Samm B"})

    response = _post(
        client,
        specialist,
        "add_planned_action",
        matter,
        {"text": "Samm A", "target_date": _day(3).isoformat()},
    )

    assert response.status_code == 200
    assert [a.text for a in _steps(matter, ActionStatus.OPEN)] == ["Samm B"]
    assert [a.text for a in _steps(matter, ActionStatus.PLANNED)] == ["Samm A"]


def test_with_nothing_open_the_planned_panel_still_writes_the_current_step(
    client, matter, specialist
):
    response = _post(
        client,
        specialist,
        "add_planned_action",
        matter,
        {"text": "Samm A", "target_date": _day(3).isoformat()},
    )

    assert response.status_code == 200
    assert [a.text for a in _steps(matter, ActionStatus.OPEN)] == ["Samm A"]
    assert _steps(matter, ActionStatus.PLANNED) == []


# ---------------------------------------------------------------------------
# The service itself.
# ---------------------------------------------------------------------------


def test_new_work_never_borrows_the_kind_of_the_step_that_is_open(matter, specialist):
    """A colleague's `WAIT` step is theirs; the stale save is `DO` / `DEADLINE`."""
    set_next_action(
        matter=matter,
        text="Ootame ministeeriumi vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        actor=specialist,
    )

    planned = add_unnamed_step(
        matter=matter, actor=specialist, text="Koosta kokkuvõte", target_date=_day(4)
    )

    assert planned.status == ActionStatus.PLANNED
    assert (planned.kind, planned.date_semantics) == (ActionKind.DO, DateSemantics.DEADLINE)


def test_a_stale_period_keeps_its_period_in_the_plan(matter, specialist):
    """«Oktoober» is not filed as the 1st of October (docs/adr/0079 §1)."""
    set_next_action_for_new_work(matter=matter, text="Praegune", actor=specialist)
    anchor = _day(40).replace(day=1)

    planned = add_unnamed_step(
        matter=matter,
        actor=specialist,
        text="Kuu jooksul",
        target_date=anchor,
        date_precision=DatePrecision.MONTH,
    )

    assert (planned.status, planned.target_date, planned.date_precision) == (
        ActionStatus.PLANNED,
        anchor,
        DatePrecision.MONTH,
    )


def test_a_step_this_saver_cannot_see_is_not_superseded_either(
    matter, specialist, other_specialist
):
    """A restricted open step stays open; dated new work waits in the plan, unrestricted."""
    hidden = set_next_action_for_new_work(matter=matter, text="Salastatud", actor=specialist)
    NextAction.objects.filter(pk=hidden.pk).update(visibility_override=Visibility.RESTRICTED)

    planned = add_unnamed_step(
        matter=matter, actor=other_specialist, text="Uus töö", target_date=_day(2)
    )

    hidden.refresh_from_db()
    assert hidden.status == ActionStatus.OPEN
    assert planned.status == ActionStatus.PLANNED
    assert planned.visibility_override == ""
    with refused(STALE_ACTION_REFUSAL):
        add_unnamed_step(matter=matter, actor=other_specialist, text="Kuupäevata")


def test_a_closed_matter_refuses_before_deciding(matter, specialist):
    from app.matters.locks import CLOSED_MATTER_REFUSAL
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)

    with refused(CLOSED_MATTER_REFUSAL):
        add_unnamed_step(matter=matter, actor=specialist, text="Hiline", target_date=_day(1))
    assert not NextAction.objects.filter(matter=matter).exists()


def test_new_work_is_assigned_to_the_owner_as_it_is_under_the_lock(
    matter, specialist, other_specialist
):
    """AUD-052 WF-4: a caller's instance older than a reassignment does not decide."""
    stale_instance = type(matter).objects.get(pk=matter.pk)
    type(matter).objects.filter(pk=matter.pk).update(owner=other_specialist)

    action = set_next_action_for_new_work(matter=stale_instance, text="Uus", actor=specialist)

    assert action.responsible_id == other_specialist.pk
