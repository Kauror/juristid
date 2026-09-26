"""Two figures that must mean their list, and one list that must not cost a query a row.

ENG-059: Minu asjad's *Kuupäevata* rail counts the open undated steps a person
is **responsible** for, and its «Näita kõiki N» opened every open Matter that
person **owns** — a different population, which on a desk with more than eight
undated steps was a different number and different rows. It now opens
`?too=kuupaevata&too_vastutaja=<person>`, resolved from the rail's own selector.

ENG-079: the Dokumendid tab asked each row's `is_restricted`, which read the
parent Matter — one query per document, 70 on a dense `?koik=1`.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.documents.models import Document
from app.matters import work_items as wi
from app.matters.my_work import build_my_work
from app.matters.register_filters import register_population
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


# --- ENG-059 ---------------------------------------------------------------


def _undated(matter, responsible, actor):
    return set_next_action(
        matter=matter, text="Sünteetiline samm", responsible=responsible, actor=actor
    )


@pytest.fixture
def desk(specialist, other_specialist):
    """Eleven undated steps for `specialist`, three of them on a colleague's Matters.

    And the populations the old link confused them with: Matters `specialist`
    owns whose step belongs to somebody else, a dated step, and a restricted
    Matter the viewer cannot see.
    """
    mine_owned = [factories.MatterFactory(owner=specialist) for _ in range(8)]
    mine_elsewhere = [factories.MatterFactory(owner=other_specialist) for _ in range(3)]
    for matter in [*mine_owned, *mine_elsewhere]:
        _undated(matter, specialist, specialist)

    # Owned by `specialist`, but the step is the colleague's.
    for _ in range(2):
        _undated(factories.MatterFactory(owner=specialist), other_specialist, specialist)
    # Owned and dated: not undated.
    set_next_action(
        matter=factories.MatterFactory(owner=specialist),
        text="Dateeritud",
        target_date=timezone.localdate() + timedelta(days=4),
        responsible=specialist,
        actor=specialist,
    )
    return {"expected": {m.pk for m in [*mine_owned, *mine_elsewhere]}}


def test_the_rail_count_and_its_link_are_one_population(desk, specialist):
    work = build_my_work(specialist)

    assert work.undated_total == 11
    assert len(work.undated) < work.undated_total, "the overflow link would not render"
    assert "too=kuupaevata" in work.undated_url
    assert f"too_vastutaja={specialist.pk}" in work.undated_url

    listed = set(
        register_population(
            specialist,
            {
                "olek": "avatud",
                "liik": "FULL",
                "too": wi.WORK_UNDATED,
                "too_vastutaja": str(specialist.pk),
            },
        ).values_list("pk", flat=True)
    )
    assert listed == desk["expected"]


def test_the_page_renders_that_link_and_it_opens_those_rows(client, desk, specialist):
    client.force_login(specialist)
    body = client.get(reverse("matters:my_work")).content.decode()

    assert "Näita kõiki 11" in body
    assert "too=kuupaevata" in body

    register = client.get(build_my_work(specialist).undated_url)
    assert register.status_code == 200
    assert register.context["active_filters"]
    shown = {chip["value"] for chip in register.context["active_filters"]}
    assert "Kuupäevata" in shown


def test_the_population_is_scoped_to_what_the_reader_may_see(
    desk, specialist, other_specialist, reader
):
    hidden = factories.MatterFactory(owner=other_specialist)
    hidden_step = _undated(hidden, specialist, other_specialist)
    type(hidden).objects.filter(pk=hidden.pk).update(visibility=Visibility.RESTRICTED)

    as_reader = wi.work_population_ids(reader, wi.WORK_UNDATED, responsible=specialist.pk)
    assert hidden.pk not in as_reader
    assert hidden_step.matter_id == hidden.pk


def test_unassigned_undated_steps_are_their_own_population(specialist):
    nobody = factories.MatterFactory(owner=specialist)
    step = _undated(nobody, None, specialist)
    # A step nobody was named for defaults to the owner; an unassigned one is a
    # step whose responsible was cleared, which is the state this narrows to.
    type(step).objects.filter(pk=step.pk).update(responsible=None)

    ids = wi.work_population_ids(specialist, wi.WORK_UNDATED, responsible=None)
    assert nobody.pk in ids
    assert wi.work_population_ids(specialist, wi.WORK_UNDATED, responsible=specialist.pk) == set()


# --- ENG-079 ---------------------------------------------------------------


def _documents_queries(client, matter, *, show_all: bool) -> int:
    url = reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url + ("?koik=1" if show_all else ""))
    assert response.status_code == 200
    return len(queries)


@pytest.mark.parametrize("show_all", [False, True], ids=["page", "koik"])
def test_documents_add_no_query_per_row(signed_in, specialist, show_all):
    few = factories.MatterFactory(owner=specialist)
    many = factories.MatterFactory(owner=specialist)
    for _ in range(3):
        factories.DocumentFactory(matter=few, created_by=specialist)
    for index in range(30):
        factories.DocumentFactory(
            matter=many,
            created_by=specialist,
            visibility_override=Visibility.RESTRICTED if index % 3 == 0 else "",
        )
    assert Document.objects.filter(matter=many).count() == 30

    baseline = _documents_queries(signed_in, few, show_all=show_all)
    dense = _documents_queries(signed_in, many, show_all=show_all)

    # A per-row fetch would add 9 queries on the page (12 rows against 3) and
    # 27 on `?koik=1`. Other per-page reads do not grow with the rows.
    assert dense <= baseline + 2, (baseline, dense)


def test_the_restricted_badge_is_still_right(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    factories.DocumentFactory(
        matter=matter,
        created_by=specialist,
        title="Piiratud näidis",
        visibility_override=Visibility.RESTRICTED,
    )
    factories.DocumentFactory(matter=matter, created_by=specialist, title="Tavaline näidis")

    body = signed_in.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()
    assert body.count("Piiratud") >= 1
