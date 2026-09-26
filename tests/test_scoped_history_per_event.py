"""Per-Matter history asks visibility per event, not per child table (ENG-076).

`scope_change_events` tested each child family as an uncorrelated
``object_id IN (SELECT pk FROM <child> WHERE <visible>)``. For the lawyer roles
the predicate is empty, so every Matter page, chronology page and change-log
page built the set of *every* entry, next step, document and development in the
database to test a Matter's handful of events — a cost that grew with the
corpus. It is now a correlated ``EXISTS`` on the event's own ``object_id``.

The authorization boundary is the point, so the first test here is parity: for
every role, on a world holding restricted children on a normal Matter, a
restricted Matter, a child deleted outright and a second Matter, the new
spelling returns exactly the rows the old one did.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.db.models import Q
from django.utils import timezone

from app.audit.models import ChangeEvent
from app.audit.visibility import _child_families, child_event_types, scope_change_events
from app.core.authorization import apply as apply_scope
from app.core.authorization import child_visibility_q, scope_for_user
from app.core.enums import Visibility
from app.documents.services import add_evidence_version, create_document
from app.matters.models import Entry
from app.matters.services import add_entry
from app.matters.workspace import add_procedural_development
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


def _uncorrelated(events, user):
    """The spelling before ENG-076, kept here as the parity oracle."""
    scope = scope_for_user(user)
    eligible = ~Q(event_type__in=child_event_types())
    for event_types, model, paths in _child_families():
        population = apply_scope(model._default_manager.all(), child_visibility_q(scope, **paths))
        eligible |= Q(event_type__in=event_types, object_id__in=population.values("pk"))
    return events.filter(eligible)


def _history(matter, author):
    add_entry(matter=matter, body="<p>Tavaline märge</p>", author=author)
    add_entry(
        matter=matter,
        body="<p>Piiratud märge</p>",
        author=author,
        visibility_override=Visibility.RESTRICTED,
    )
    set_next_action(
        matter=matter,
        text="Järgmine",
        actor=author,
        target_date=timezone.localdate() + timedelta(days=3),
    )
    for override in ("", Visibility.RESTRICTED):
        document = create_document(
            matter=matter, title="Tõend", visibility_override=override, created_by=author
        )
        add_evidence_version(
            document=document,
            content=b"%PDF-1.4 synthetic",
            original_filename="toend.pdf",
            mime_type="application/pdf",
        )
    add_procedural_development(matter=matter, author=author, title="Samm")


@pytest.fixture
def world(specialist, other_specialist):
    normal = factories.MatterFactory(owner=specialist)
    restricted = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    elsewhere = factories.MatterFactory(owner=other_specialist)
    for matter, author in (
        (normal, specialist),
        (restricted, specialist),
        (elsewhere, other_specialist),
    ):
        _history(matter, author)
    # A child that no longer exists: its events stay, and stay hidden.
    gone = add_entry(matter=normal, body="<p>Kaob</p>", author=specialist)
    Entry.objects.filter(pk=gone.pk).delete()
    return normal, restricted, elsewhere


@pytest.mark.parametrize(
    "persona",
    ["specialist", "other_specialist", "department_head", "reader", "administrator"],
)
def test_every_role_sees_exactly_what_it_saw(request, world, persona):
    user = request.getfixturevalue(persona)
    querysets = [ChangeEvent.objects.filter(matter=matter) for matter in world]
    querysets.append(ChangeEvent.objects.all())

    for events in querysets:
        before = set(_uncorrelated(events, user).values_list("pk", flat=True))
        after = set(scope_change_events(events, user).values_list("pk", flat=True))
        assert after == before


def test_the_world_exercises_what_the_scope_decides(world, specialist, other_specialist):
    """Parity on a world where nothing is hidden would prove nothing."""
    normal, _restricted, _elsewhere = world
    everything = ChangeEvent.objects.filter(matter=normal)
    seen = scope_change_events(everything, other_specialist)
    assert seen.count() < everything.count(), "no event was hidden from a non-participant"


def test_each_child_family_is_a_correlated_exists(specialist, normal_matter):
    """The shape that makes the cost follow the events read, not the corpus."""
    events = scope_change_events(ChangeEvent.objects.filter(matter=normal_matter), specialist)
    sql = str(events.query)

    assert sql.count("EXISTS(") >= len(_child_families())
    assert '"object_id" IN (SELECT' not in sql
    for _types, model, _paths in _child_families():
        table = model._meta.db_table
        # Probed by primary key from the event row: `FROM <child> U0 WHERE
        # U0."id" = ("audit_changeevent"."object_id")`.
        probe = (
            rf'FROM "{table}" (U\d+) (?:.*? )?WHERE .*?'
            r'\1\."id" = \("audit_changeevent"\."object_id"\)'
        )
        assert re.search(probe, sql), table
