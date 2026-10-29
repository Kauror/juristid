"""One human closure is one act on every surface (2026-10-28 audit A-02, A-03, B-03).

Scenario C of the remediation brief: a Matter owned by A, closed by B while a
current and a planned next step exist. `close_matter` ends both steps with the
closure reason (`step_markers.CLOSED_WITH_MATTER`) — two audit rows a person
never chose. Every surface that reads the closure must say the same thing:

* Osakond → *Tehtud* names B (it named the owner, B-03);
* Osakond → *Muudatused* has one «sulges teema» row in B's name and no
  «tühistas» row;
* A's *Viimased tegevused* has one «Teema suleti» row naming B and no
  «Järgmine tegevus tühistatud» row (the feed's suppression was never tested
  against a real closure, A-02);
* the audit keeps both cancellations, and the work model holds nothing for
  the closed Matter.

The two readers share the markers the workflow writes (`app.workflow.step_markers`);
Minu asjad no longer loads Osakond's module graph for them (A-03).
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import timedelta

import pytest
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters import department_changes as dc
from app.matters import department_dashboard as dd
from app.matters import work_items as wi
from app.matters.activity import BASIS_LABELS, ActivityBasis
from app.matters.recent_activity import (
    ACTION_CANCELLED_LABEL,
    ACTION_SET_LABEL,
    build_recent_activity,
)
from app.matters.services import close_matter, create_matter
from app.workflow.enums import ActionStatus, Disposition
from app.workflow.models import NextAction
from app.workflow.services import add_planned_action, set_next_action
from app.workflow.step_markers import (
    CLOSED_WITH_MATTER,
    closure_cancelled_q,
    system_step_change_q,
)
from tests import factories

pytestmark = pytest.mark.django_db

CLOSED_LABEL = BASIS_LABELS[ActivityBasis.CLOSURE]


def _closed_by_colleague(owner, closer):
    """A's Matter with a current and a planned step, closed by B."""
    matter = create_matter(title="Sulgeb kolleeg", owner=owner, actor=owner)
    set_next_action(matter=matter, text="Praegune samm", actor=owner)
    add_planned_action(
        matter=matter,
        text="Planeeritud samm",
        target_date=timezone.localdate() + timedelta(days=14),
        actor=owner,
    )
    close_matter(
        matter=matter,
        disposition=Disposition.COMPLETED,
        actor=closer,
        follow_ups_confirmed=True,
    )
    matter.refresh_from_db()
    return matter


def _digest_closures(reader):
    period = dd.period_from({}, timezone.localdate())
    return [row for row in dd.build_digest(reader, period).rows if row.kind == "closed"]


def _feed_on(matter, subject):
    return [
        row for row in build_recent_activity(subject, subject).rows if row.matter.pk == matter.pk
    ]


def test_the_closure_ended_both_steps_with_the_reason_both_readers_recognise(
    specialist, other_specialist
):
    matter = _closed_by_colleague(specialist, other_specialist)

    assert matter.closed_by == other_specialist
    assert set(NextAction.objects.filter(matter=matter).values_list("status", flat=True)) == {
        ActionStatus.CANCELLED
    }
    cancellations = ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.NEXT_ACTION_CANCELLED
    )
    assert cancellations.count() == 2
    assert {event.payload["reason"] for event in cancellations} == {CLOSED_WITH_MATTER}
    # Both predicates recognise exactly these rows, as the real writer wrote them.
    assert set(cancellations.filter(closure_cancelled_q())) == set(cancellations)
    assert set(cancellations.filter(system_step_change_q())) == set(cancellations)
    # The work model holds nothing of a closed Matter.
    assert [item for item in wi.work_items(specialist) if item.matter.pk == matter.pk] == []


def test_tehtud_names_whoever_closed_the_matter_not_its_owner(
    department_head, specialist, other_specialist
):
    matter = _closed_by_colleague(specialist, other_specialist)

    assert [
        (row.text, row.initials, row.actor_name) for row in _digest_closures(department_head)
    ] == [(matter.title, other_specialist.initials, other_specialist.display_name)]


def test_tehtud_falls_back_to_the_owner_only_when_the_closure_names_nobody(
    department_head, specialist
):
    """An imported closure has no `closed_by`: the owner, as for a send without `sent_by`."""
    factories.MatterFactory(
        owner=specialist,
        title="Imporditud sulgemine",
        is_open=False,
        disposition=Disposition.COMPLETED,
        closed_at=timezone.now() - timedelta(hours=2),
        closed_by=None,
    )

    [row] = _digest_closures(department_head)

    assert row.initials == specialist.initials


def test_the_owners_feed_shows_one_closure_by_the_colleague_and_no_cancellations(
    specialist, other_specialist
):
    matter = _closed_by_colleague(specialist, other_specialist)

    on_matter = _feed_on(matter, specialist)

    [closure] = [row for row in on_matter if row.label == CLOSED_LABEL]
    assert closure.actor == other_specialist
    assert not any(ACTION_CANCELLED_LABEL in row.label for row in on_matter), [
        row.label for row in on_matter
    ]
    # The owner's own steps, set before the closure, stay the owner's acts.
    assert any(ACTION_SET_LABEL in row.label for row in on_matter)


def test_muudatused_tehtud_and_the_feed_name_the_same_person_for_the_one_closure(
    department_head, specialist, other_specialist
):
    matter = _closed_by_colleague(specialist, other_specialist)

    changes = dc.build_changes(department_head)
    tegevused = next(result for result in changes.categories if result.key == "tegevused")
    closure_rows = [row for row in tegevused.rows if row.event_type == dc.CLOSURE_ROW]

    assert [(row.matter_title, row.actor_name) for row in closure_rows] == [
        (matter.title, other_specialist.display_name)
    ]
    assert dict(tegevused.breakdown).get("tühistas järgmise tegevuse", 0) == 0
    [feed_closure] = [row for row in _feed_on(matter, specialist) if row.label == CLOSED_LABEL]
    [digest_closure] = _digest_closures(department_head)
    assert (
        feed_closure.actor.display_name
        == closure_rows[0].actor_name
        == digest_closure.actor_name
        == other_specialist.display_name
    )


def test_minu_asjad_does_not_load_the_department_graph_for_the_markers():
    """`recent_activity` reads the markers from the workflow, not from Osakond (A-03)."""
    probe = (
        "import sys, django; django.setup(); "
        "import app.matters.recent_activity, app.matters.my_work; "
        "names = ('app.matters.department_changes', 'app.matters.department_dashboard'); "
        "print([name for name in names if name in sys.modules])"
    )
    result = subprocess.run(  # noqa: S603 - a fixed interpreter and a constant probe
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
    )
    assert result.stdout.strip() == "[]", result.stderr
