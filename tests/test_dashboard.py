"""The open-work populations, and what they must never let leak.

The dangerous failure here is quiet. A restricted Matter hidden from a list but
still counted in a total tells the reader it exists, and nothing on screen looks
wrong — so most of this file is the same assertion from five points of view.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.matters import dashboard
from app.matters.enums import RecordMode
from app.matters.services import create_matter
from app.workflow.enums import ActionKind, DateSemantics
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

RESTRICTED_TITLE = "Piiratud ühinemise eelnõu"


def _active(user) -> int:
    return dashboard.active_matters(user).count()


@pytest.fixture
def world(db, specialist, other_specialist):
    """One open Matter everybody sees, one only its owner does."""
    today = timezone.localdate()
    stage = factories.StageFactory()
    ministry = factories.OrganisationFactory(name="Näidisministeerium")

    visible = create_matter(
        title="Avalik pakendiseaduse eelnõu",
        owner=other_specialist,
        reference_year=2026,
        stage=stage,
        source_organisations=[ministry],
        received_date=today - timedelta(days=2),
        response_deadline=today + timedelta(days=3),
    )
    set_next_action(
        matter=visible,
        text="Koosta arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today + timedelta(days=3),
        actor=other_specialist,
    )

    restricted = create_matter(
        title=RESTRICTED_TITLE,
        owner=specialist,
        reference_year=2026,
        stage=stage,
        source_organisations=[ministry],
        visibility=Visibility.RESTRICTED,
        received_date=today - timedelta(days=1),
        response_deadline=today + timedelta(days=2),
    )
    set_next_action(
        matter=restricted,
        text="Konfidentsiaalne tegevus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=today - timedelta(days=4),
        actor=specialist,
    )
    return {"visible": visible, "restricted": restricted, "stage": stage}


# -- the boundary, from every angle ----------------------------------------


def test_the_owner_counts_both(world, specialist) -> None:
    assert _active(specialist) == 2


def test_an_unrelated_specialist_counts_only_what_they_see(world, reader) -> None:
    assert _active(reader) == 1


def test_a_department_head_counts_both(world, department_head) -> None:
    assert _active(department_head) == 2


def test_a_technical_administrator_does_not(world, administrator) -> None:
    """Administering the system is not permission to read the content."""
    assert _active(administrator) == 1


def test_an_anonymous_caller_counts_nothing(world) -> None:
    from django.contrib.auth.models import AnonymousUser

    assert _active(AnonymousUser()) == 0
    assert not dashboard.without_next_action(AnonymousUser()).exists()
    assert not dashboard.drafting_matters(AnonymousUser()).exists()


# -- what the numbers actually mean ----------------------------------------


def test_archive_matters_are_not_counted_as_active(db, specialist) -> None:
    create_matter(
        title="Arhiiviteema",
        owner=specialist,
        assign_reference=False,
        record_mode=RecordMode.ARCHIVE,
    )
    assert _active(specialist) == 0


def test_a_matter_without_a_next_action_is_still_findable(db, specialist) -> None:
    matter = create_matter(title="Vaikne teema", owner=specialist, reference_year=2026)

    assert matter in dashboard.without_next_action(specialist)


# -- the page --------------------------------------------------------------


def test_the_page_is_reached_by_its_own_address(world, client, specialist) -> None:
    """Osakond is a destination, not what the root chooses.

    Ülevaade used to be both: `/` redirected here. Since Minu asjad became a
    person's default home the root goes there instead, and this page is reached
    from the navigation bar or from a pasted link — which is the whole contract
    this test holds. What `/` picks is tested in `tests/test_default_home.py`.

    Reversed through the name Ülevaade's route carried, which now resolves to
    the page that replaced it rather than to the compatibility redirect, so a
    caller holding the old name lands directly (docs/adr/0049 §2).
    """
    client.force_login(specialist)
    response = client.get(reverse("matters:department"))
    assert response.status_code == 200
    assert response.request["PATH_INFO"] == "/osakond/"
    assert response.resolver_match.view_name == "matters:department"


def test_the_page_renders_and_hides_what_it_should(world, client, reader) -> None:
    client.force_login(reader)
    response = client.get(reverse("matters:department"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Osakond" in body
    assert RESTRICTED_TITLE not in body


def test_the_page_requires_signing_in(world, client) -> None:
    assert client.get(reverse("matters:department")).status_code == 302
