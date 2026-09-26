"""`Jätkub teise teema all` names the Matter it continues under (ENG-065, HAF-04).

`Disposition.SUPERSEDED` is the one closure reason that asserts a continuation,
and `Matter.superseded_by` is where the continuation is recorded. The composer
leaves the choice out rather than post a null successor, because «work continues
under another Matter» naming none is worse than not offering it
(`CLOSURE_CHOICES`). But `close_matter` only refused the reverse — a successor
with another reason — so a direct call, or the legacy `POST /teemad/<pk>/sulge/`
that offered every disposition and no successor field, could close a file as
SUPERSEDED into nothing.

The rule now lives in the service, and the route that had no UI and no
documented reason to exist is retired. `DUPLICATE` is untouched: no ADR ties it
to a successor, and inventing that rule here would be a product decision.

A refused closure leaves the Matter open, its current step open, and writes no
`MATTER_CLOSED` or other event.
"""

from __future__ import annotations

import pytest
from django.urls import NoReverseMatch, resolve, reverse
from django.urls.exceptions import Resolver404
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.matters import services as matter_services
from app.matters.models import Matter
from app.matters.services import close_matter
from app.workflow.enums import ActionStatus, Disposition
from app.workflow.models import NextAction
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


def _events(matter) -> list[str]:
    return list(ChangeEvent.objects.filter(matter=matter).values_list("event_type", flat=True))


@pytest.fixture
def working_matter(specialist):
    matter = factories.MatterFactory(owner=specialist)
    set_next_action(matter=matter, text="Vaatan eelnõu üle", actor=specialist)
    return matter


def _assert_still_open(matter, events_before):
    matter.refresh_from_db()
    assert matter.is_open
    assert matter.disposition == ""
    assert matter.superseded_by_id is None
    assert matter.closed_at is None
    assert NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).count() == 1
    assert _events(matter) == events_before
    assert ChangeEventType.MATTER_CLOSED not in _events(matter)


def test_superseded_without_a_successor_is_refused(working_matter, specialist):
    before = _events(working_matter)

    with pytest.raises(DomainError) as refusal:
        close_matter(
            matter=working_matter,
            disposition=Disposition.SUPERSEDED,
            actor=specialist,
            reason="Töö jätkub uues teemas",
        )

    assert str(refusal.value) == matter_services.SUPERSEDED_NEEDS_A_SUCCESSOR
    _assert_still_open(working_matter, before)


def test_superseded_with_a_successor_closes_and_points_at_it(working_matter, specialist):
    successor = factories.MatterFactory(owner=specialist)

    close_matter(
        matter=working_matter,
        disposition=Disposition.SUPERSEDED,
        actor=specialist,
        successor=successor,
    )

    working_matter.refresh_from_db()
    assert not working_matter.is_open
    assert working_matter.superseded_by_id == successor.pk
    closed = ChangeEvent.objects.get(
        matter=working_matter, event_type=ChangeEventType.MATTER_CLOSED
    )
    assert closed.payload["successor"] == str(successor.pk)


def test_a_matter_cannot_continue_under_itself(working_matter, specialist):
    before = _events(working_matter)

    with pytest.raises(DomainError):
        close_matter(
            matter=working_matter,
            disposition=Disposition.SUPERSEDED,
            actor=specialist,
            successor=Matter.objects.get(pk=working_matter.pk),
        )

    _assert_still_open(working_matter, before)


def test_a_deleted_successor_is_refused(working_matter, specialist):
    """A tombstone (docs/adr/0096) is not somewhere work can continue."""
    successor = factories.MatterFactory(owner=specialist)
    Matter.all_objects.filter(pk=successor.pk).update(deleted_at=timezone.now())
    before = _events(working_matter)

    with pytest.raises(DomainError) as refusal:
        close_matter(
            matter=working_matter,
            disposition=Disposition.SUPERSEDED,
            actor=specialist,
            successor=Matter.all_objects.get(pk=successor.pk),
        )

    assert str(refusal.value) == matter_services.SUCCESSOR_DELETED_REFUSAL
    _assert_still_open(working_matter, before)


def test_a_successor_the_closer_cannot_read_is_refused_as_if_absent(working_matter, reader):
    """The service's own statement of the rule, whoever calls it. The routes
    already refuse a `READER` any closure; the refusal here names nothing about
    the restricted file, so it is the same sentence a missing one gets."""
    hidden = factories.MatterFactory(
        owner=factories.UserFactory(), visibility=Visibility.RESTRICTED
    )
    before = _events(working_matter)

    with pytest.raises(DomainError) as refusal:
        close_matter(
            matter=working_matter,
            disposition=Disposition.SUPERSEDED,
            actor=reader,
            successor=hidden,
        )

    assert str(refusal.value) == matter_services.SUCCESSOR_DELETED_REFUSAL
    assert hidden.title not in str(refusal.value)
    _assert_still_open(working_matter, before)


def test_a_successor_with_another_reason_is_still_refused(working_matter, specialist):
    before = _events(working_matter)

    with pytest.raises(DomainError):
        close_matter(
            matter=working_matter,
            disposition=Disposition.COMPLETED,
            actor=specialist,
            successor=factories.MatterFactory(owner=specialist),
        )

    _assert_still_open(working_matter, before)


@pytest.mark.parametrize(
    "disposition",
    [value for value in Disposition.values if value != Disposition.SUPERSEDED],
)
def test_every_other_reason_closes_without_a_successor(specialist, disposition):
    """Ordinary closure is unaffected — `DUPLICATE` included: no ADR ties a
    duplicate to a successor, so none is demanded."""
    matter = factories.MatterFactory(owner=specialist)

    close_matter(matter=matter, disposition=disposition, actor=specialist)

    matter.refresh_from_db()
    assert not matter.is_open
    assert matter.disposition == disposition
    assert matter.superseded_by_id is None


# ---------------------------------------------------------------------------
# The legacy route is gone
# ---------------------------------------------------------------------------


def test_the_legacy_close_route_is_retired():
    """`matters:close` had no template, no script, no test of its own behaviour
    and no documented compatibility reason; it was the one door that offered
    `SUPERSEDED` with nowhere to name the successor (HAF-04)."""
    with pytest.raises(NoReverseMatch):
        reverse("matters:close", kwargs={"pk": "00000000-0000-0000-0000-000000000000"})
    with pytest.raises(Resolver404):
        resolve("/teemad/00000000-0000-0000-0000-000000000000/sulge/")

    from app.matters import forms, views

    assert not hasattr(views, "close")
    assert not hasattr(forms, "CloseMatterForm")


def test_a_crafted_post_to_the_old_address_closes_nothing(signed_in, working_matter):
    before = _events(working_matter)

    response = signed_in.post(
        f"/teemad/{working_matter.pk}/sulge/",
        {"disposition": Disposition.SUPERSEDED, "reason": ""},
    )

    assert response.status_code == 404
    _assert_still_open(working_matter, before)


def test_the_live_closure_still_closes(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("matters:close_from_workspace", kwargs={"pk": matter.pk}),
        {"disposition": Disposition.COMPLETED},
    )

    assert response.status_code == 200
    matter.refresh_from_db()
    assert not matter.is_open
