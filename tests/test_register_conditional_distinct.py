"""The register applies DISTINCT only when a join can repeat a row (AUD-001).

The register's row query carries a dozen correlated subqueries, and
``SELECT DISTINCT`` over it made PostgreSQL evaluate all of them for every
visible row before the page limit — and in the production image pushed the
plan's estimated cost over ``jit_above_cost`` (AUD-039). On the default
register no join repeats a Matter, so the DISTINCT bought nothing.

It is not simply removed: ``?ulatus=minu`` joins the collaborators and
``?asutus=`` the senders, and each repeats a Matter once per match. The audit's
naïve removal duplicated rows in 20 of 180 cases. This module re-runs a parity
matrix of the same shape — personas × filters × sorts × pages — against the
real view and asserts, for every case:

* no Matter appears twice across the pages;
* the pages together are exactly the de-duplicated population;
* the count beside the list equals the rows the pages hold.

And the query shape: no ``DISTINCT`` in the default register's row query, one
where the query fans out.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.core.enums import Visibility
from app.matters import register_filters
from app.matters.models import Matter
from tests import factories

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")
SORTS = ("", "title", "updated", "deadline", "viimane_uusim", "kuupaev_desc")


@pytest.fixture
def world(specialist, other_specialist, department_head, administrator, reader):
    sender_a = factories.OrganisationFactory(name="Pariteedi ministeerium A")
    sender_b = factories.OrganisationFactory(name="Pariteedi amet B")
    collaborated = factories.MatterFactory(owner=specialist, title="Pariteet: kolm kaasautorit")
    collaborated.collaborators.add(other_specialist, department_head, administrator)
    two_senders = factories.MatterFactory(owner=other_specialist, title="Pariteet: kaks saatjat")
    two_senders.source_organisations.add(sender_a, sender_b)
    restricted = factories.MatterFactory(
        owner=specialist, title="Pariteet: piiratud", visibility=Visibility.RESTRICTED
    )
    restricted.collaborators.add(other_specialist)
    restricted.source_organisations.add(sender_a, sender_b)
    for number in range(14):
        matter = factories.MatterFactory(
            owner=(specialist, other_specialist, department_head)[number % 3],
            title=f"Pariteet: tavaline {number:02d}",
        )
        if number % 4 == 0:
            matter.collaborators.add(specialist)
        if number % 5 == 0:
            matter.source_organisations.add(sender_a)
    return {
        "people": {
            "specialist": specialist,
            "collaborator": other_specialist,
            "head": department_head,
            "administrator": administrator,
            "reader": reader,
        },
        "senders": (sender_a, sender_b),
        "collaborated": collaborated,
        "restricted": restricted,
    }


def _parameter_sets(world) -> list[dict[str, str]]:
    sender_a, sender_b = (str(o.pk) for o in world["senders"])
    sets: list[dict[str, str]] = []
    for filters in (
        {},
        {"olek": "koik"},
        {"ulatus": "minu"},
        {"ulatus": "minu", "olek": "koik"},
        {"saatja": sender_a},
        {"asutus": sender_a},
        {"asutus": sender_b, "ulatus": "minu"},
    ):
        for sort in SORTS:
            params = dict(filters)
            if sort:
                params["jarjestus"] = sort
            sets.append(params)
    return sets


def _walk(client, user, params) -> tuple[list[str], int]:
    """Every row id across every page, and the count the page states."""
    client.force_login(user)
    seen: list[str] = []
    total = None
    page = 1
    while True:
        response = client.get(REGISTER, {**params, "kaupa": "12", "leht": str(page)})
        assert response.status_code == 200
        context = response.context
        total = context["total"]
        seen.extend(str(matter.pk) for matter in context["page"].object_list)
        if not context["page"].has_next():
            break
        page += 1
    return seen, total


def _expected(user, params) -> set[str]:
    """The population de-duplicated unconditionally — the old behaviour's rows."""
    queryset, _ = register_filters.apply_register_filters(
        Matter.objects.visible_to(user), user, register_filters.as_text(params)
    )
    return {str(pk) for pk in queryset.distinct().values_list("pk", flat=True)}


def test_the_parity_matrix_holds_for_every_persona_filter_sort_and_page(client, world):
    cases = 0
    for name, user in world["people"].items():
        for params in _parameter_sets(world):
            seen, total = _walk(client, user, params)
            label = f"{name} {params}"
            assert len(seen) == len(set(seen)), f"a Matter appears twice: {label}"
            assert set(seen) == _expected(user, params), label
            assert total == len(seen), f"count {total} != rows {len(seen)}: {label}"
            cases += 1
    assert cases == 5 * 7 * len(SORTS)


def test_a_matter_with_three_collaborators_is_listed_once_under_minu(client, world):
    for user in (world["people"]["collaborator"], world["people"]["head"]):
        seen, total = _walk(client, user, {"ulatus": "minu", "olek": "koik"})
        assert seen.count(str(world["collaborated"].pk)) == 1
        assert total == len(seen)


def test_a_restricted_matter_reaches_its_participant_once_and_nobody_else(client, world):
    restricted = str(world["restricted"].pk)
    sender_a = str(world["senders"][0].pk)
    participant, _ = _walk(client, world["people"]["collaborator"], {"asutus": sender_a})
    outsider, _ = _walk(client, world["people"]["reader"], {"asutus": sender_a})

    assert participant.count(restricted) == 1
    assert restricted not in outsider


def _row_queries(captured) -> list[str]:
    """The paged row statements: they read the Matter and carry LIMIT."""
    return [
        q["sql"]
        for q in captured
        if 'FROM "matters_matter"' in q["sql"] and "LIMIT" in q["sql"] and "COUNT(" not in q["sql"]
    ]


def test_the_default_register_row_query_carries_no_distinct(client, world):
    client.force_login(world["people"]["specialist"])
    with CaptureQueriesContext(connection) as captured:
        assert client.get(REGISTER).status_code == 200

    rows = _row_queries(captured)
    assert rows, "no row statement was captured"
    assert not any(sql.lstrip().upper().startswith("SELECT DISTINCT") for sql in rows)


def test_a_fanning_filter_keeps_its_distinct(client, world):
    client.force_login(world["people"]["specialist"])
    with CaptureQueriesContext(connection) as captured:
        assert client.get(REGISTER, {"ulatus": "minu"}).status_code == 200

    assert any(sql.lstrip().upper().startswith("SELECT DISTINCT") for sql in _row_queries(captured))


def test_kpi_population_matches_the_register_rows(world):
    """`register_population` is what the cards count; it de-duplicates the same way."""
    for user in world["people"].values():
        for params in ({}, {"ulatus": "minu"}, {"asutus": str(world["senders"][0].pk)}):
            population = register_filters.register_population(user, params)
            ids = list(population.values_list("pk", flat=True))
            assert len(ids) == len(set(ids))
            assert {str(pk) for pk in ids} == _expected(user, params)
