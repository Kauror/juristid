"""`assign_matter` refuses a person handing a Teema to somebody work may not be given to.

Integrity finding of the UX programme preparation (24.11.2026): every person-facing
route already narrows the choice — `assignable_including` in the forms, `_is_assignable`
in the MCP operation — but the service itself trusted its caller. It now asks the same
rule where the write happens, for a person's assignment only:

* to an assignable colleague: unchanged;
* to a switched-off account, an administrator, a reader: refused, nothing written;
* clearing the owner, and re-submitting the current owner (even one who has left):
  unchanged;
* an operation's assignment (``provenance``: the Lovable sync, the pilot, the owner
  backfill) and a system call with no actor: unchanged — the sync resolves names to
  assignable people itself, and an import may record a historical owner.
"""

from __future__ import annotations

import pytest

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.services import ASSIGNMENT_RECIPIENT_NOT_ASSIGNABLE, assign_matter
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db


def _events(matter) -> int:
    return ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_ASSIGNED
    ).count()


def test_a_colleague_may_be_given_the_teema(specialist, other_specialist):
    matter = factories.MatterFactory(owner=specialist)

    assign_matter(matter=matter, owner=other_specialist, actor=specialist)

    matter.refresh_from_db()
    assert matter.owner == other_specialist


@pytest.mark.parametrize(
    "recipient",
    [
        lambda: factories.UserFactory(is_active=False),
        lambda: factories.AdministratorFactory(),
        lambda: factories.ReaderFactory(),
    ],
    ids=["switched-off", "administrator", "reader"],
)
def test_a_person_cannot_hand_it_to_somebody_work_may_not_be_given_to(specialist, recipient):
    matter = factories.MatterFactory(owner=specialist)
    before = _events(matter)

    with refused(ASSIGNMENT_RECIPIENT_NOT_ASSIGNABLE):
        assign_matter(matter=matter, owner=recipient(), actor=specialist)

    matter.refresh_from_db()
    assert matter.owner == specialist
    assert _events(matter) == before


def test_clearing_the_owner_is_unchanged(specialist):
    matter = factories.MatterFactory(owner=specialist)

    assign_matter(matter=matter, owner=None, actor=specialist)

    matter.refresh_from_db()
    assert matter.owner is None


def test_the_current_owner_who_has_left_can_be_resubmitted(specialist):
    """A form posting the owner a record already names writes nothing and refuses nothing."""
    departed = factories.UserFactory(is_active=False)
    matter = factories.MatterFactory(owner=departed)

    assign_matter(matter=matter, owner=departed, actor=specialist)

    matter.refresh_from_db()
    assert matter.owner == departed


def test_an_operation_with_provenance_keeps_its_own_rules(specialist):
    """The owner backfill and the imports may record a historical owner."""
    departed = factories.UserFactory(is_active=False)
    matter = factories.MatterFactory(owner=None)

    assign_matter(matter=matter, owner=departed, actor=None, provenance={"source": "test-import"})

    matter.refresh_from_db()
    assert matter.owner == departed


def test_a_system_call_without_a_person_is_unchanged():
    departed = factories.UserFactory(is_active=False)
    matter = factories.MatterFactory(owner=None)

    assign_matter(matter=matter, owner=departed, actor=None)

    matter.refresh_from_db()
    assert matter.owner == departed
