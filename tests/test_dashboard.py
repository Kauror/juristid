"""Ülevaade: what it counts, and what it must never let leak.

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
from app.matters import dashboard, selectors
from app.matters.enums import RecordMode
from app.matters.services import create_matter
from app.workflow.enums import ActionKind, DateSemantics
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

RESTRICTED_TITLE = "Piiratud ühinemise eelnõu"


def _card(user, key):
    return next(card for card in dashboard.summary_cards(user) if card.key == key)


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
    assert _card(specialist, "active").count == 2


def test_an_unrelated_specialist_counts_only_what_they_see(world, other_specialist) -> None:
    assert _card(other_specialist, "active").count == 1


def test_a_department_head_counts_both(world, department_head) -> None:
    assert _card(department_head, "active").count == 2


def test_a_technical_administrator_does_not(world, administrator) -> None:
    """Administering the system is not permission to read the content."""
    assert _card(administrator, "active").count == 1


def test_an_anonymous_caller_counts_nothing(world) -> None:
    from django.contrib.auth.models import AnonymousUser

    assert _card(AnonymousUser(), "active").count == 0
    assert dashboard.attention_rows(AnonymousUser()) == []
    assert dashboard.owner_inventory(AnonymousUser()) == []
    assert dashboard.stage_distribution(AnonymousUser()) == []


def test_a_hidden_matter_changes_no_total(world, specialist, other_specialist) -> None:
    """Every card, from two viewpoints, differs by exactly the hidden row."""
    mine = {card.key: card.count for card in dashboard.summary_cards(specialist)}
    theirs = {card.key: card.count for card in dashboard.summary_cards(other_specialist)}

    assert mine["active"] - theirs["active"] == 1
    assert mine["deadlines"] - theirs["deadlines"] == 1
    # The restricted Matter carries the only overdue action.
    assert mine["overdue"] == 1
    assert theirs["overdue"] == 0


def test_a_hidden_matter_does_not_appear_in_attention(world, other_specialist) -> None:
    titles = [row.matter.title for row in dashboard.attention_rows(other_specialist)]
    assert RESTRICTED_TITLE not in titles


def test_a_hidden_matter_does_not_appear_in_upcoming(world, other_specialist) -> None:
    result = dashboard.upcoming_rows(other_specialist)
    assert RESTRICTED_TITLE not in [row.matter.title for row in result.rows]


def test_a_hidden_matter_does_not_appear_in_recent_incoming(world, other_specialist) -> None:
    titles = [matter.title for matter in dashboard.recent_incoming(other_specialist)]
    assert RESTRICTED_TITLE not in titles


def test_owner_counts_exclude_what_the_reader_cannot_see(
    world, specialist, other_specialist
) -> None:
    """The tally that would otherwise say "somebody has a file you can't see"."""
    theirs = {row.label: row.count for row in dashboard.owner_inventory(other_specialist)}
    assert specialist.display_name not in theirs

    mine = {row.label: row.count for row in dashboard.owner_inventory(specialist)}
    assert mine[specialist.display_name] == 1


def test_stage_counts_exclude_what_the_reader_cannot_see(
    world, specialist, other_specialist
) -> None:
    label = world["stage"].label_et
    mine = {row.label: row.count for row in dashboard.stage_distribution(specialist)}
    theirs = {row.label: row.count for row in dashboard.stage_distribution(other_specialist)}
    assert mine[label] == 2
    assert theirs[label] == 1


# -- what the numbers actually mean ----------------------------------------


def test_only_a_real_deadline_counts_as_overdue(db, specialist) -> None:
    """A WAIT past its review date is due for a look, never overdue.

    Calling an ordinary dependency on a ministry a missed task is what makes a
    work queue stop being believed (specification 18.8).
    """
    today = timezone.localdate()
    matter = create_matter(title="Ootel teema", owner=specialist, reference_year=2026)
    set_next_action(
        matter=matter,
        text="Ootan ministeeriumi vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=today - timedelta(days=10),
        actor=specialist,
    )
    assert _card(specialist, "overdue").count == 0

    reasons = [row.reason for row in dashboard.attention_rows(specialist)]
    assert "Ülevaatuse aeg on käes" in reasons
    assert "Tegevuse tähtaeg möödas" not in reasons


def test_archive_matters_are_not_counted_as_active(db, specialist) -> None:
    create_matter(
        title="Arhiiviteema",
        owner=specialist,
        assign_reference=False,
        record_mode=RecordMode.ARCHIVE,
    )
    assert _card(specialist, "active").count == 0


def test_a_matter_without_a_next_action_is_still_findable(db, specialist) -> None:
    """The *Järgmine tegevus puudub* card is gone; the condition is not.

    With almost no historical record carrying a structured next action, the
    card measured how far the cutover had got rather than a problem anybody can
    act on. Removing it is only safe because the population is still one query
    away — which is what this asserts (app/matters/dashboard.py).
    """
    matter = create_matter(title="Vaikne teema", owner=specialist, reference_year=2026)

    assert "no_action" not in {card.key for card in dashboard.summary_cards(specialist)}
    assert matter in dashboard.without_next_action(specialist)
    assert matter in selectors.matters_without_next_action(specialist)


def test_an_unassigned_matter_is_reported(db, specialist) -> None:
    create_matter(title="Vastutajata teema", reference_year=2026)
    assert _card(specialist, "unassigned").count == 1
    labels = [row.label for row in dashboard.owner_inventory(specialist)]
    assert "Vastutajata" in labels


def test_upcoming_says_what_each_date_means(world, specialist) -> None:
    """Four kinds of date share one column and must not read alike."""
    meanings = {row.meaning for row in dashboard.upcoming_rows(specialist).rows}
    assert dashboard.MEANING_RESPONSE in meanings
    assert meanings <= {
        dashboard.MEANING_RESPONSE,
        dashboard.MEANING_ACTION,
        dashboard.MEANING_REVIEW,
        dashboard.MEANING_EXPECTED,
    }


def test_every_card_links_somewhere(world, specialist) -> None:
    """A number that promises a list must have one."""
    for card in dashboard.summary_cards(specialist):
        assert card.url.startswith("/")


# -- the page --------------------------------------------------------------


def test_the_root_opens_ulevaade(world, client, specialist) -> None:
    client.force_login(specialist)
    response = client.get("/")
    assert response.status_code == 302
    assert response["Location"] == reverse("matters:overview")


def test_the_page_renders_and_hides_what_it_should(world, client, other_specialist) -> None:
    client.force_login(other_specialist)
    response = client.get(reverse("matters:overview"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Ülevaade" in body
    assert RESTRICTED_TITLE not in body


def test_the_page_requires_signing_in(world, client) -> None:
    assert client.get(reverse("matters:overview")).status_code == 302


def test_the_dashboard_is_a_bounded_number_of_queries(
    world, specialist, django_assert_max_num_queries
) -> None:
    """It is the landing page; it must not scale its query count with the data."""
    for index in range(10):
        create_matter(title=f"Lisateema {index}", owner=specialist, reference_year=2026)
    with django_assert_max_num_queries(40):
        dashboard.build_dashboard(specialist)
