"""`list_matters`: the register, filtered — «what is Marko responsible for?» (QA owner C/E).

The eighth tool. Built on the register's own population
(`matter_list_queryset(user)`) and rules (`status_q`, the next-step rule for
NONE, the «asutus» organisation-either-direction filter), every filter applied
before counting and slicing, and bounded: a page of twenty rows costs a fixed
number of queries, not one per row.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from app.matters.services import close_matter
from app.mcp_server import readers
from app.mcp_server.readers import InvalidArgument, NotFound
from app.workflow.enums import ActionKind, DateSemantics, Disposition
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"


@pytest.fixture
def people():
    marko = factories.UserFactory(display_name="Marko Mets", upn="marko.mets@example.invalid")
    kati = factories.UserFactory(display_name="Kati Kask", upn="kati.kask@example.invalid")
    return marko, kati


def _references(answer):
    return {row["reference"] for row in answer["matters"]}


def test_what_marko_is_responsible_for(people):
    marko, kati = people
    mine = [factories.MatterFactory(owner=marko, title=f"Marko teema {n}") for n in range(3)]
    factories.MatterFactory(owner=kati, title="Kati teema")
    closed = factories.MatterFactory(owner=marko, title="Marko suletud")
    close_matter(matter=closed, disposition=Disposition.COMPLETED, actor=marko)
    factories.ArchiveMatterFactory(owner=marko, title="Marko arhiiv")

    answer = readers.list_matters(kati, responsible="marko")

    assert _references(answer) == {matter.display_reference for matter in mine}
    assert answer["total"] == 3
    assert answer["filters"]["responsible"]["people_matched"] == ["Marko Mets"]
    assert all(row["owner"] == "Marko Mets" for row in answer["matters"])
    row = answer["matters"][0]
    assert row["url"].startswith("https://juristid.example.test/teemad/")
    assert {"stage", "work_status", "current_next_action", "opinion_deadline"} <= set(row)

    with_closed = readers.list_matters(kati, responsible="Marko", is_open=None)
    assert closed.display_reference in _references(with_closed)
    everything = readers.list_matters(kati, responsible="Marko", is_open=None, record_mode="ALL")
    assert everything["total"] == 5


def test_a_name_that_matches_nobody_says_so(people):
    marko, _ = people
    factories.MatterFactory(owner=marko)
    answer = readers.list_matters(marko, responsible="Tundmatu Isik")
    assert answer["matters"] == [] and answer["total"] == 0
    assert answer["filters"]["responsible"]["people_matched"] == []


def test_what_we_are_waiting_for_from_an_organisation(people):
    marko, _ = people
    ministry = factories.OrganisationFactory(name="Kliimaministeerium")
    sent_by = factories.MatterFactory(owner=marko, title="Saatis ministeerium")
    sent_by.source_organisations.add(ministry)
    waiting = factories.MatterFactory(owner=marko, title="Ootame vastust")
    today = timezone.localdate()
    set_next_action(
        matter=waiting,
        text="Ootame Kliimaministeeriumi vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=today + timedelta(days=7),
        actor=marko,
    )
    set_next_action(matter=sent_by, text="Koosta arvamus", actor=marko)
    factories.MatterFactory(owner=marko, title="Muu teema")

    either = readers.list_matters(marko, organisation="kliimaministeer")
    assert _references(either) == {sent_by.display_reference, waiting.display_reference}
    assert either["filters"]["organisation"]["organisations_matched"] == ["Kliimaministeerium"]

    waits = readers.list_matters(marko, organisation="kliimaministeer", next_action_kind="WAIT")
    assert _references(waits) == {waiting.display_reference}
    [row] = waits["matters"]
    assert row["current_next_action"]["kind"] == "WAIT"
    assert row["waiting_for"] == "Ootame Kliimaministeeriumi vastust"
    assert row["current_next_action"]["date"]["certainty"] == "planned"


def test_matters_with_no_next_step(people):
    marko, _ = people
    empty = factories.MatterFactory(owner=marko, title="Tühi")
    busy = factories.MatterFactory(owner=marko, title="Tööl")
    set_next_action(matter=busy, text="Analüüsi", actor=marko)
    answer = readers.list_matters(marko, next_action_kind="NONE")
    assert empty.display_reference in _references(answer)
    assert busy.display_reference not in _references(answer)
    dos = readers.list_matters(marko, next_action_kind="do")
    assert _references(dos) == {busy.display_reference}


def test_pages_of_twenty_with_the_total(people):
    marko, _ = people
    for n in range(25):
        factories.MatterFactory(owner=marko, title=f"Teema {n}")
    first = readers.list_matters(marko, responsible="Marko")
    second = readers.list_matters(marko, responsible="Marko", page=2)
    assert len(first["matters"]) == 20 and first["has_more"] is True and first["total"] == 25
    assert len(second["matters"]) == 5 and second["has_more"] is False
    assert not _references(first) & _references(second)


def test_a_page_costs_a_bounded_number_of_queries(people, django_assert_max_num_queries):
    marko, _ = people
    ministry = factories.OrganisationFactory(name="Rahandusministeerium")
    for n in range(20):
        matter = factories.MatterFactory(
            owner=marko,
            title=f"Teema {n}",
            response_deadline=timezone.localdate() + timedelta(days=n + 1),
        )
        matter.source_organisations.add(ministry)
        set_next_action(matter=matter, text=f"Samm {n}", actor=marko)
    with django_assert_max_num_queries(40):
        answer = readers.list_matters(marko, responsible="Marko", organisation="rahandus")
    assert len(answer["matters"]) == 20


def test_an_administrator_without_a_business_role_reads_nothing(administrator):
    with pytest.raises(NotFound, match="No such record"):
        readers.list_matters(administrator)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"next_action_kind": "LATER"}, "next_action_kind must be one of"),
        ({"record_mode": "SOME"}, "record_mode must be one of"),
        ({"page": 11}, "page must be between"),
        ({"responsible": "x" * 101}, "responsible must be at most"),
    ],
)
def test_bad_arguments_are_refused(people, arguments, message):
    marko, _ = people
    with pytest.raises(InvalidArgument, match=message):
        readers.list_matters(marko, **arguments)
