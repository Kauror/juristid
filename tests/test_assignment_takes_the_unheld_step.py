"""The first owner a Matter gets also gets the step nobody was holding (ENG-023).

`Matter.owner` and `NextAction.responsible` are two facts and stay two facts:
who carries the file, and who must do the next thing on it. `assign_matter`
keeps them together in exactly the cases where nobody decided they should
differ, and these tests pin down which cases those are.

The defect was a gap in that rule. A step written on an **unowned** Matter is
stored with no responsible person — `set_next_action` has no owner to fall back
to, and `responsible_for_new_work` deliberately stores nobody rather than
inventing somebody (docs/adr/0036 §5). That includes the «Koostan arvamuse»
step Uus teema writes from an Arvamuse tähtaeg. The rule that moves a step with
the file covered only a step whose responsible *was* the previous owner, so the
first assignment left it on nobody's desk:

* the new owner's Minu asjad did not list it, and its row read «järgmine
  tegevus puudub · Määra» while the Teema's own PRAEGUNE TEGEVUS showed it;
* once it was late it was missing from their «üle tähtaja», while Osakond — which
  groups by owner — counted it against them;
* and MATTER_ASSIGNED recorded ``next_action_moved=None``, which was true, and
  was the problem.

What these cases hold:

A. unowned, step held by nobody, assigned to Martin — the step is Martin's;
B. Sandra owns it and holds the step, assigned to Martin — it follows (unchanged);
C. somebody else was named for the step — it stays theirs, whoever owned the file;
D. no open step — nothing but the assignment happens;
E. the event says exactly which step moved, and nothing when none did;
F. Minu asjad shows the work the moment it is assigned;
G. the late step is late on the desk it now belongs to, and Osakond agrees.

Every date is stated against a frozen day, so nothing here depends on the day
the suite runs.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.matters import work_items as wi
from app.matters.deletion import delete_matter
from app.matters.department_dashboard import TEAM_COLUMNS, team_rows
from app.matters.models import Matter
from app.matters.my_work import build_my_work
from app.matters.services import ASSIGNMENT_ON_DELETED_MATTER, assign_matter, create_matter
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction
from app.workflow.services import (
    complete_next_action,
    establish_opinion_preparation_action,
    set_next_action_for_new_work,
)
from tests import factories

pytestmark = pytest.mark.django_db

#: A Wednesday, so «this week» is never a day away from «overdue».
TODAY = dt.date(2026, 9, 16)


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> dt.date:
    """Hold the application's idea of today still.

    Patched on `django.utils.timezone`, which every module here imports, so the
    views and the read models agree with the dates the tests state. `now()` is
    left alone: nothing written during a test claims to be from the future.
    """
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: TODAY)
    return TODAY


@pytest.fixture
def martin(db) -> Any:
    return factories.UserFactory(display_name="Martin Määratav")


@pytest.fixture
def sandra(db) -> Any:
    return factories.UserFactory(display_name="Sandra Senine")


@pytest.fixture
def kaur(db) -> Any:
    return factories.UserFactory(display_name="Kaur Kolleeg")


def unowned_matter(actor: Any, title: str = "Vastutajata teema") -> Matter:
    return create_matter(title=title, owner=None, actor=actor)


def open_step(matter: Matter) -> NextAction:
    return NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)


def assignment_events(matter: Matter) -> list[ChangeEvent]:
    return list(
        ChangeEvent.objects.filter(matter=matter, event_type=ChangeEventType.MATTER_ASSIGNED)
    )


# ---------------------------------------------------------------------------
# A — the initial assignment takes the step nobody held
# ---------------------------------------------------------------------------


def test_the_first_owner_takes_the_step_nobody_held(department_head, martin, frozen) -> None:
    """A. The reported defect, through the services the product uses."""
    matter = unowned_matter(department_head)
    step = set_next_action_for_new_work(
        matter=matter,
        text="Koosta arvamuse kavand",
        target_date=frozen + dt.timedelta(days=5),
        actor=department_head,
    )
    assert step.responsible is None, "the precondition: an unowned file's step is nobody's"

    assign_matter(matter=matter, owner=martin, actor=department_head)

    assert open_step(matter).pk == step.pk, "moved, not replaced"
    assert open_step(matter).responsible == martin
    (event,) = assignment_events(matter)
    assert event.payload["next_action_moved"] == str(step.pk)


def test_the_uus_teema_opinion_step_follows_the_triage_route(
    client, department_head, martin, frozen
) -> None:
    """A, the way the audit reproduced it: Uus teema with no Vastutaja, then triage.

    `establish_opinion_preparation_action` is what Uus teema calls with the
    Arvamuse tähtaeg, and `assign_owner` is the register's triage control. The
    route promises the step moves; now it does.
    """
    matter = unowned_matter(department_head, "Uus teema ilma vastutajata")
    step = establish_opinion_preparation_action(
        matter=matter, prepare_by=frozen + dt.timedelta(days=7), actor=department_head
    )
    assert step.responsible is None

    client.force_login(department_head)
    response = client.post(
        reverse("matters:assign_owner", kwargs={"pk": matter.pk}), {"owner": str(martin.pk)}
    )

    assert response.status_code == 302
    matter.refresh_from_db()
    assert matter.owner == martin
    assert open_step(matter).responsible == martin
    (event,) = assignment_events(matter)
    assert event.payload["next_action_moved"] == str(step.pk)


def test_the_header_owner_control_moves_it_as_well(client, department_head, martin) -> None:
    """The Teema header's own Vastutaja field reaches the same service."""
    matter = unowned_matter(department_head)
    step = set_next_action_for_new_work(matter=matter, text="Loe eelnõu", actor=department_head)

    client.force_login(department_head)
    response = client.post(
        reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "owner"}),
        {"owner": str(martin.pk)},
    )

    assert response.status_code == 200
    assert open_step(matter).responsible == martin
    (event,) = assignment_events(matter)
    assert event.payload["next_action_moved"] == str(step.pk)


def test_an_operation_assignment_takes_the_unheld_step_and_raises_no_notice(
    department_head, martin
) -> None:
    """The owner backfill passes `provenance`, and goes through the same rule.

    Had the owner been known when the importer wrote the step, `set_next_action`
    would have made it the owner's. Giving it the owner now is that same
    default, arriving late — and it is still no colleague's decision, so nobody
    is told «Uus asi».
    """
    from app.matters.models import MatterAssignmentNotice

    matter = unowned_matter(department_head)
    step = set_next_action_for_new_work(matter=matter, text="Registri juhis", actor=None)

    assign_matter(matter=matter, owner=martin, actor=None, provenance={"method": "test"})

    assert open_step(matter).responsible == martin
    (event,) = assignment_events(matter)
    assert event.payload["next_action_moved"] == str(step.pk)
    assert not MatterAssignmentNotice.objects.filter(matter=matter).exists()


# ---------------------------------------------------------------------------
# B, C, D — the rule that was already there, and its limit
# ---------------------------------------------------------------------------


def test_a_step_following_the_previous_owner_still_follows(sandra, martin) -> None:
    """B. Unchanged: Sandra's own step on Sandra's file goes to Martin with it."""
    matter = factories.MatterFactory(owner=sandra)
    step = set_next_action_for_new_work(matter=matter, text="Koosta arvamus", actor=sandra)
    assert step.responsible == sandra

    assign_matter(matter=matter, owner=martin, actor=sandra)

    assert open_step(matter).responsible == martin
    (event,) = assignment_events(matter)
    assert event.payload["next_action_moved"] == str(step.pk)


def test_a_step_somebody_else_was_named_for_is_never_taken(sandra, martin, kaur) -> None:
    """C. Kaur was deliberately given the step on Sandra's file; he keeps it."""
    matter = factories.MatterFactory(owner=sandra)
    set_next_action_for_new_work(
        matter=matter, text="Palun vaata sina", responsible=kaur, actor=sandra
    )

    assign_matter(matter=matter, owner=martin, actor=sandra)

    assert open_step(matter).responsible == kaur
    (event,) = assignment_events(matter)
    assert event.payload["next_action_moved"] is None


def test_a_named_step_on_an_unowned_file_is_not_taken_either(department_head, martin, kaur) -> None:
    """C, on the initial assignment. Only a step *nobody* holds is adopted."""
    matter = unowned_matter(department_head)
    set_next_action_for_new_work(
        matter=matter, text="Kaur vaatab üle", responsible=kaur, actor=department_head
    )

    assign_matter(matter=matter, owner=martin, actor=department_head)

    assert open_step(matter).responsible == kaur
    (event,) = assignment_events(matter)
    assert event.payload["next_action_moved"] is None


def test_without_an_open_step_only_the_assignment_happens(department_head, martin) -> None:
    """D. No step is invented, and a finished one is not reopened or moved."""
    matter = unowned_matter(department_head)
    finished = set_next_action_for_new_work(matter=matter, text="Tehtud", actor=department_head)
    complete_next_action(action=finished, actor=department_head)
    steps_before = NextAction.objects.filter(matter=matter).count()

    assign_matter(matter=matter, owner=martin, actor=department_head)

    assert NextAction.objects.filter(matter=matter).count() == steps_before
    finished.refresh_from_db()
    assert finished.status == ActionStatus.COMPLETED
    assert finished.responsible is None
    (event,) = assignment_events(matter)
    assert event.payload["next_action_moved"] is None


def test_unassigning_leaves_a_step_nobody_held_where_it_was(department_head) -> None:
    """Assigning nobody to an unowned file is not an assignment at all."""
    matter = unowned_matter(department_head)
    set_next_action_for_new_work(matter=matter, text="Ootel", actor=department_head)

    assign_matter(matter=matter, owner=None, actor=department_head)

    assert open_step(matter).responsible is None
    assert assignment_events(matter) == []


# ---------------------------------------------------------------------------
# E — a refused assignment moves nothing and says nothing
# ---------------------------------------------------------------------------


def test_an_assignment_refused_on_a_deleted_matter_moves_no_step(
    department_head, martin, specialist
) -> None:
    """The ENG-075 lock is untouched: the tombstone refuses, and writes nothing."""
    matter = unowned_matter(department_head)
    set_next_action_for_new_work(matter=matter, text="Kustub", actor=department_head)
    stale = Matter.objects.get(pk=matter.pk)
    delete_matter(matter=Matter.objects.get(pk=matter.pk), actor=specialist)
    events_before = ChangeEvent.objects.filter(event_type=ChangeEventType.MATTER_ASSIGNED).count()

    with pytest.raises(DomainError) as refusal:
        assign_matter(matter=stale, owner=martin, actor=department_head)

    assert str(refusal.value) == ASSIGNMENT_ON_DELETED_MATTER
    assert (
        ChangeEvent.objects.filter(event_type=ChangeEventType.MATTER_ASSIGNED).count()
        == events_before
    )
    assert not NextAction.objects.filter(matter_id=matter.pk, responsible=martin).exists()


def test_a_reader_cannot_assign_and_the_step_stays_unheld(client, reader, martin) -> None:
    """READER makes no business write: no owner, no moved step, no event."""
    matter = factories.MatterFactory(owner=None)
    set_next_action_for_new_work(matter=matter, text="Lugeja ei määra", actor=None)

    client.force_login(reader)
    response = client.post(
        reverse("matters:assign_owner", kwargs={"pk": matter.pk}), {"owner": str(martin.pk)}
    )

    assert response.status_code in (403, 404)
    matter.refresh_from_db()
    assert matter.owner is None
    assert open_step(matter).responsible is None
    assert assignment_events(matter) == []


# ---------------------------------------------------------------------------
# F, G — the work lands on the desk, and the desk and Osakond agree
# ---------------------------------------------------------------------------


def test_minu_asjad_shows_the_newly_assigned_work(client, department_head, martin, frozen) -> None:
    """F. Martin opens his desk and the step is on it, on its own row."""
    matter = unowned_matter(department_head, "Triaažitud teema")
    establish_opinion_preparation_action(
        matter=matter, prepare_by=frozen + dt.timedelta(days=3), actor=department_head
    )
    assign_matter(matter=matter, owner=martin, actor=department_head)

    client.force_login(martin)
    work = client.get(reverse("matters:my_work")).context["work"]

    dated = [item.matter_id for band in work.bands for item in band.items]
    assert dated == [matter.pk]
    (row,) = [row for row in work.portfolio.all_rows if row.matter.pk == matter.pk]
    assert row.has_action, "no «järgmine tegevus puudub · Määra» on a file with a step"
    assert work.quiet_total == 0


def _cell(user: Any, person: Any, column: str, today: dt.date) -> int:
    index = next(i for i, (key, _l, _g, _s) in enumerate(TEAM_COLUMNS) if key == column)
    row = next(row for row in team_rows(user, today) if row.key == str(person.pk))
    return row.cells[index].value


def test_a_late_step_is_late_on_the_new_owners_desk_and_in_osakond(
    department_head, martin, frozen
) -> None:
    """G. One late step, counted once, against the person who now has it.

    Before the fix Osakond — which groups by owner — counted it against Martin
    while his own «üle tähtaja» did not list it, because the desk groups by
    responsible and the step had nobody.
    """
    matter = unowned_matter(department_head, "Hilinenud triaažitud teema")
    set_next_action_for_new_work(
        matter=matter,
        text="Saada arvamus",
        target_date=frozen - dt.timedelta(days=2),
        actor=department_head,
    )
    assign_matter(matter=matter, owner=martin, actor=department_head)

    desk = build_my_work(department_head, today=frozen, subject=martin)
    osakond = _cell(department_head, martin, "overdue", frozen)

    assert desk.overdue == 1
    assert osakond == 1
    assert desk.overdue == osakond
    assert matter.pk in wi.work_population_ids(
        department_head, wi.WORK_OVERDUE, today=frozen, responsible=martin
    )
    unassigned = next(row for row in team_rows(department_head, frozen) if row.is_unassigned)
    index = next(i for i, (key, _l, _g, _s) in enumerate(TEAM_COLUMNS) if key == "overdue")
    assert unassigned.cells[index].value == 0
