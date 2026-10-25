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

from app.documents.services import add_evidence_version
from app.matters.services import close_matter
from app.mcp_server import readers
from app.mcp_server.readers import InvalidArgument, NotFound
from app.submissions.enums import SubmissionStatus
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


def _owed_and_late(owner, title, days_late):
    """An open Matter whose requested `Arvamuse tähtaeg` passed and nobody has answered."""
    return factories.MatterFactory(
        owner=owner,
        title=title,
        response_deadline=timezone.localdate() - timedelta(days=days_late),
        response_requested_at=timezone.now() - timedelta(days=days_late + 30),
    )


def _count_queries(call):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as captured:
        answer = call()
    return len(captured), answer


@pytest.mark.parametrize("role", ["specialist", "reader"])
def test_overdue_rows_do_not_cost_a_query_each(people, role, request):
    """PERF (2026-10-25): `is_overdue` was one `.exists()` per row with a passed deadline.

    `response_deadline_of` asks `response_deadline_is_outstanding` for every open
    row whose deadline has passed and is still owed, and the page read that
    answer one round trip per row: a page of twenty overdue files cost 26
    queries where one with none cost 6. The page's own statement now carries the
    answer, so the count is the same however many rows are late.
    """
    marko, _ = people
    reader = request.getfixturevalue(role)
    _owed_and_late(marko, "Hilinenud 0", days_late=1)
    one_late, answer = _count_queries(lambda: readers.list_matters(reader))
    assert [row["opinion_deadline"]["is_overdue"] for row in answer["matters"]] == [True]

    for n in range(1, 20):
        _owed_and_late(marko, f"Hilinenud {n}", days_late=n + 1)
    twenty_late, answer = _count_queries(lambda: readers.list_matters(reader))

    assert len(answer["matters"]) == 20
    assert all(row["opinion_deadline"]["is_overdue"] for row in answer["matters"])
    assert twenty_late == one_late


def test_the_page_says_what_the_matter_page_says_about_each_deadline(people):
    """Answered on the page's statement, `is_overdue` is still the one-Matter rule's answer.

    Every way a passed deadline stops being operational work is on the page —
    a DO step outranks it, a WAIT does not (docs/adr/0050), a SENT opinion
    discharges an untracked one — beside one still owed and one not yet due; each row must
    say exactly what `response_deadline_of` says about that Matter read alone.
    """
    from app.matters.models import Matter
    from app.matters.selectors import response_deadline_of

    marko, _ = people
    owed = _owed_and_late(marko, "Võlgu", days_late=3)
    instructed = _owed_and_late(marko, "Järgmine samm on", days_late=3)
    set_next_action(matter=instructed, text="Koosta arvamus", actor=marko)
    waiting = _owed_and_late(marko, "Ootame", days_late=3)
    set_next_action(
        matter=waiting,
        text="Ootame ministeeriumi vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=timezone.localdate() + timedelta(days=7),
        actor=marko,
    )
    # A deadline from before requests were tracked: a sent opinion discharges
    # it; without one it stays owed (`_discharge_exists`).
    untracked = _owed_and_late(marko, "Vana tähtaeg", days_late=3)
    answered = _owed_and_late(marko, "Vastatud", days_late=3)
    Matter.objects.filter(pk__in=[untracked.pk, answered.pk]).update(response_requested_at=None)
    version = add_evidence_version(
        document=factories.DocumentFactory(matter=answered),
        content=b"%PDF-1.4\nArvamus",
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
    )
    factories.SubmissionFactory(
        matter=answered,
        status=SubmissionStatus.SENT,
        sent_at=timezone.now(),
        final_version=version,
    )
    factories.MatterFactory(
        owner=marko,
        title="Veel aega",
        response_deadline=timezone.localdate() + timedelta(days=5),
        response_requested_at=timezone.now(),
    )

    answer = readers.list_matters(marko)
    today = timezone.localdate()
    seen = {}
    for row in answer["matters"]:
        alone = response_deadline_of(Matter.objects.get(pk=row["id"].split(":")[1]), marko, today)
        assert row["opinion_deadline"]["is_overdue"] == alone.is_overdue, row["title"]
        assert row["opinion_deadline"]["still_owed"] == alone.is_active, row["title"]
        seen[row["title"]] = row["opinion_deadline"]["is_overdue"]

    assert seen == {
        "Võlgu": True,
        "Järgmine samm on": False,
        "Ootame": True,
        "Vana tähtaeg": True,
        "Vastatud": False,
        "Veel aega": False,
    }
    assert owed.display_reference in _references(answer)


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


# -- NEW-03: real matters by default, TEST ones only when asked and marked --------------


def test_test_matters_are_left_out_by_default_and_marked_when_asked_for(people):
    from app.matters.enums import MatterDataClass

    marko, kati = people
    real = factories.MatterFactory(owner=marko, title="Päris teema")
    test = factories.MatterFactory(
        owner=marko, title="Arenduse proovimuutmine", data_class=MatterDataClass.TEST
    )

    default = readers.list_matters(kati, responsible="Marko")
    assert _references(default) == {real.display_reference}
    assert default["total"] == 1
    assert default["filters"]["include_test_data"] is False

    widened = readers.list_matters(kati, responsible="Marko", include_test_data=True)
    assert _references(widened) == {real.display_reference, test.display_reference}
    assert widened["total"] == 2
    titles = {row["reference"]: row["title"] for row in widened["matters"]}
    assert titles[test.display_reference] == "[TEST] Arenduse proovimuutmine"
    assert titles[real.display_reference] == "Päris teema"
    rows = {row["reference"]: row for row in widened["matters"]}
    assert rows[test.display_reference]["is_test_data"] is True


def test_include_test_data_must_be_a_boolean(people):
    marko, _ = people
    with pytest.raises(InvalidArgument, match="include_test_data must be true or false"):
        readers.list_matters(marko, include_test_data="yes")  # type: ignore[arg-type]


def test_the_tool_schema_defaults_to_real_matters():
    from app.mcp_server import schemas

    field = schemas.ListMattersArguments.model_json_schema()["properties"]["include_test_data"]
    assert field["default"] is False


# -- NEW-11: `responsible` is a name, and a former colleague's matters stay findable ------


def test_an_address_fragment_lists_nobody(people):
    marko, kati = people
    factories.MatterFactory(owner=marko)
    factories.MatterFactory(owner=kati)
    for fragment in ("@example.invalid", "marko.mets", "example"):
        answer = readers.list_matters(kati, responsible=fragment)
        assert answer["filters"]["responsible"]["people_matched"] == [], fragment
        assert answer["total"] == 0, fragment


def test_a_former_colleagues_matters_are_found_by_their_name(people):
    _, kati = people
    former = factories.UserFactory(display_name="Endine Kolleeg", is_active=False)
    left_behind = factories.MatterFactory(owner=former, title="Pooleli jäänud teema")
    # A former colleague who owns nothing this reader can see is named by nobody.
    factories.UserFactory(display_name="Endine Teine", is_active=False)

    answer = readers.list_matters(kati, responsible="endine")

    assert answer["filters"]["responsible"]["people_matched"] == ["Endine Kolleeg"]
    assert _references(answer) == {left_behind.display_reference}


def test_a_one_letter_fragment_is_refused(people):
    marko, _ = people
    with pytest.raises(InvalidArgument, match="responsible must be at least 2 characters"):
        readers.list_matters(marko, responsible=" m ")


def test_the_tool_schema_asks_for_two_characters():
    from app.mcp_server import schemas

    field = schemas.ListMattersArguments.model_json_schema()["properties"]["responsible"]
    [text] = [option for option in field["anyOf"] if option.get("type") == "string"]
    assert text["minLength"] == 2
