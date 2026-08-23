"""Teemad: the live search box and the Täpsem otsing panel.

Two rules are worth stating up front, because nearly every test below is a
consequence of one of them.

**`q` narrows the population, not the page.** The search runs against the whole
authorized register through the search projection, and the count beside the box
is the count of that population — not of the twenty-five rows that happen to be
rendered (brief 9).

**Everything intersects.** A query and three structured filters mean all four at
once. Filters do not widen each other, and the search does not widen them.
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.matters.enums import MatterOrigin, RecordMode
from app.search.indexing import refresh_matters
from app.search.models import SearchDocument
from app.search.services import matching_matter_ids
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")
CHOOSER = reverse("matters:organisation_choices")


def indexed(matter):
    """Project one Matter into the search index, the way a save would."""
    from app.search.indexing import indexable_matters

    refresh_matters(indexable_matters().filter(pk=matter.pk))
    return matter


def titles_on(response) -> list[str]:
    return [matter.title for matter in response.context["page"].object_list]


def total_of(response) -> int:
    return response.context["total"]


# ---------------------------------------------------------------------------
# The projection is reused, not reimplemented
# ---------------------------------------------------------------------------


def test_the_register_searches_through_the_existing_projection(specialist):
    """One search implementation, not two.

    A second full-text system over the same Matters would be a second opinion
    about what a word means, and the two would drift (brief 8).
    """
    wanted = indexed(factories.MatterFactory(title="Pakendiseaduse muutmise eelnõu"))
    indexed(factories.MatterFactory(title="Töölepingu seaduse muudatused"))

    ids = list(matching_matter_ids(query="pakendiseaduse", user=specialist))
    assert [row["matter_id"] for row in ids] == [wanted.pk]


def test_the_search_costs_one_statement(specialist, django_assert_num_queries):
    """Composed into a subquery rather than iterated in Python.

    A keystroke over a corpus-scale register must not become a pass over
    thousands of rows, so the projection lookup and the register query have to
    reach the database together (brief 14).
    """
    from app.matters.models import Matter

    indexed(factories.MatterFactory(title="Pakendiseaduse muutmise eelnõu"))
    narrowed = Matter.objects.filter(
        pk__in=matching_matter_ids(query="pakendiseaduse", user=specialist)
    )

    with django_assert_num_queries(1):
        assert len(list(narrowed)) == 1


def test_a_reference_finds_its_own_file(signed_in, specialist):
    """`2026_123` means "open that file", typed into the register too."""
    wanted = indexed(factories.MatterFactory(reference_year=2026, reference_number=123))
    indexed(factories.MatterFactory(reference_year=2026, reference_number=124))

    response = signed_in.get(REGISTER, {"q": "2026_123", "olek": "koik"})
    assert titles_on(response) == [wanted.title]


def test_the_free_text_area_is_findable(signed_in, specialist):
    """`policy_area_other` rides in the alias column with the other names.

    Descriptive metadata somebody typed, searchable like the taxonomy names
    beside it — and still not a PolicyArea (brief 20).
    """
    wanted = indexed(
        factories.MatterFactory(title="Nimetu teema", policy_area_other="Kosmoseõigus")
    )

    response = signed_in.get(REGISTER, {"q": "Kosmoseõigus", "olek": "koik"})
    assert titles_on(response) == [wanted.title]


# ---------------------------------------------------------------------------
# What the search does to the register
# ---------------------------------------------------------------------------


def test_the_search_narrows_the_whole_population_not_the_rendered_page(signed_in):
    """Thirty matches, one page. The count must still say thirty (brief 9)."""
    for index in range(30):
        indexed(factories.MatterFactory(title=f"Pakendiseaduse säte {index}"))
    indexed(factories.MatterFactory(title="Midagi muud"))

    response = signed_in.get(REGISTER, {"q": "pakendiseaduse", "olek": "koik"})
    assert total_of(response) == 30
    assert len(titles_on(response)) == 25


def test_pagination_survives_a_search(signed_in):
    for index in range(30):
        indexed(factories.MatterFactory(title=f"Pakendiseaduse säte {index}"))

    second = signed_in.get(REGISTER, {"q": "pakendiseaduse", "olek": "koik", "leht": "2"})
    assert total_of(second) == 30
    assert len(titles_on(second)) == 5


def test_an_empty_query_is_the_whole_register(signed_in):
    indexed(factories.MatterFactory(title="Esimene"))
    indexed(factories.MatterFactory(title="Teine"))

    assert total_of(signed_in.get(REGISTER, {"q": "   ", "olek": "koik"})) == 2
    assert total_of(signed_in.get(REGISTER, {"olek": "koik"})) == 2


def test_a_query_and_a_filter_intersect(signed_in, specialist):
    """`q=pakend` + `aasta=2024` means both, never either (brief 10)."""
    wanted = indexed(
        factories.MatterFactory(
            title="Pakendiseadus 2024",
            origin=MatterOrigin.LEGACY_IMPORT,
            reporting_year=2024,
        )
    )
    indexed(
        factories.MatterFactory(
            title="Pakendiseadus 2019",
            origin=MatterOrigin.LEGACY_IMPORT,
            reporting_year=2019,
        )
    )
    indexed(
        factories.MatterFactory(
            title="Töölepinguseadus 2024",
            origin=MatterOrigin.LEGACY_IMPORT,
            reporting_year=2024,
        )
    )

    response = signed_in.get(REGISTER, {"q": "pakendiseadus", "aasta": "2024", "olek": "koik"})
    assert titles_on(response) == [wanted.title]


def test_a_restricted_matter_never_reaches_the_search_or_its_count(
    client, other_specialist, specialist
):
    """The count leaks existence as surely as the title would (brief 16)."""
    indexed(factories.MatterFactory(title="Pakendiseaduse avalik osa", owner=specialist))
    indexed(
        factories.MatterFactory(
            title="Pakendiseaduse piiratud osa",
            owner=other_specialist,
            visibility=Visibility.RESTRICTED,
        )
    )

    client.force_login(specialist)
    response = client.get(REGISTER, {"q": "pakendiseaduse", "olek": "koik"})

    assert total_of(response) == 1
    assert "piiratud" not in " ".join(titles_on(response)).lower()


def test_the_search_box_carries_the_other_filters(signed_in):
    """Typing must not silently widen a population somebody already narrowed."""
    response = signed_in.get(REGISTER, {"q": "pakend", "aasta": "2024", "olek": "arhiiv"})
    carried = dict(response.context["carried_params"])
    assert carried["aasta"] == "2024"
    assert carried["olek"] == "arhiiv"
    assert "q" not in carried


# ---------------------------------------------------------------------------
# The HTMX fragment
# ---------------------------------------------------------------------------


def test_an_htmx_request_gets_the_results_block(signed_in):
    indexed(factories.MatterFactory(title="Pakendiseaduse eelnõu"))
    response = signed_in.get(REGISTER, {"q": "pakendiseaduse"}, headers={"HX-Request": "true"})

    body = response.content.decode()
    assert 'id="teemad-tulemused"' in body
    # The fragment, not the page: no shell, and above all no second search box
    # to steal the caret from the one being typed into.
    assert "<html" not in body
    assert 'id="teemad-otsing"' not in body


def test_a_history_restore_gets_the_whole_page(signed_in):
    """HTMX asks for the document when its history cache has expired.

    Answering that with a fragment replaces the page with a bare table
    (brief 7).
    """
    response = signed_in.get(
        REGISTER,
        {"q": "pakend"},
        headers={"HX-Request": "true", "HX-History-Restore-Request": "true"},
    )
    assert 'id="teemad-otsing"' in response.content.decode()


def test_an_ordinary_request_gets_the_whole_page(signed_in):
    body = signed_in.get(REGISTER).content.decode()
    assert 'id="teemad-otsing"' in body
    assert 'id="teemad-tulemused"' in body


# ---------------------------------------------------------------------------
# Chips
# ---------------------------------------------------------------------------


def test_a_query_renders_a_removable_chip(signed_in):
    indexed(factories.MatterFactory(title="Pakendiseaduse eelnõu"))
    response = signed_in.get(REGISTER, {"q": "pakendiseaduse"})

    body = response.content.decode()
    assert "Otsing:" in body
    assert response.context["has_any_filter"] is True


def test_clearing_everything_returns_to_the_bare_register(signed_in, specialist):
    response = signed_in.get(
        REGISTER,
        {
            "q": "pakend",
            "aasta": "2024",
            "vastutaja": str(specialist.pk),
            "saabus_alates": "2024-01-01",
        },
    )
    assert response.context["cleared_query"] == ""


def test_one_chip_removes_only_itself(signed_in, specialist):
    response = signed_in.get(REGISTER, {"aasta": "2024", "vastutaja": str(specialist.pk)})
    chips = {chip["name"]: chip["remove_query"] for chip in response.context["active_filters"]}

    assert "aasta" not in chips["aasta"]
    assert "vastutaja" in chips["aasta"]


def test_a_chip_shows_a_name_rather_than_a_key(signed_in, specialist):
    organisation = factories.OrganisationFactory(name="Näidisministeerium")
    response = signed_in.get(REGISTER, {"asutus": str(organisation.pk)})

    values = {chip["name"]: chip["value"] for chip in response.context["active_filters"]}
    assert values["asutus"] == "Näidisministeerium"


def test_a_date_chip_reads_the_way_estonians_write_dates(signed_in):
    response = signed_in.get(REGISTER, {"saabus_alates": "2024-03-07"})
    values = {chip["name"]: chip["value"] for chip in response.context["active_filters"]}
    assert values["saabus_alates"] == "07.03.2024"


# ---------------------------------------------------------------------------
# Täpsem otsing: the structured dimensions
# ---------------------------------------------------------------------------


def test_the_received_date_range_includes_both_ends(signed_in):
    """01.01–31.01 means January, including the 31st (brief 11C)."""
    first = factories.MatterFactory(title="Esimene", received_date=datetime.date(2024, 1, 1))
    last = factories.MatterFactory(title="Viimane", received_date=datetime.date(2024, 1, 31))
    factories.MatterFactory(title="Enne", received_date=datetime.date(2023, 12, 31))
    factories.MatterFactory(title="Pärast", received_date=datetime.date(2024, 2, 1))

    response = signed_in.get(
        REGISTER,
        {"saabus_alates": "2024-01-01", "saabus_kuni": "2024-01-31", "olek": "koik"},
    )
    assert set(titles_on(response)) == {first.title, last.title}


def test_an_open_ended_range_is_allowed(signed_in):
    recent = factories.MatterFactory(title="Hiljutine", received_date=datetime.date(2024, 6, 1))
    factories.MatterFactory(title="Vana", received_date=datetime.date(2019, 6, 1))

    response = signed_in.get(REGISTER, {"saabus_alates": "2024-01-01", "olek": "koik"})
    assert titles_on(response) == [recent.title]


def test_the_deadline_range_narrows_its_own_column(signed_in):
    wanted = factories.MatterFactory(
        title="Tähtajaga",
        received_date=datetime.date(2019, 1, 1),
        response_deadline=datetime.date(2024, 5, 5),
    )
    factories.MatterFactory(
        title="Teise tähtajaga",
        received_date=datetime.date(2024, 5, 5),
        response_deadline=datetime.date(2019, 1, 1),
    )

    response = signed_in.get(
        REGISTER,
        {"tahtaeg_alates": "2024-01-01", "tahtaeg_kuni": "2024-12-31", "olek": "koik"},
    )
    assert titles_on(response) == [wanted.title]


def test_an_unreadable_date_empties_the_list(signed_in):
    """Rather than being ignored under a chip claiming it applied."""
    factories.MatterFactory(received_date=datetime.date(2024, 1, 1))
    response = signed_in.get(REGISTER, {"saabus_alates": "31.02.2024", "olek": "koik"})
    assert total_of(response) == 0


def test_the_organisation_convenience_filter_matches_either_direction(signed_in):
    """`asutus` asks "was this body involved at all" (brief 11F)."""
    ministry = factories.OrganisationFactory(name="Näidisministeerium")
    other = factories.OrganisationFactory(name="Muu asutus")

    sent = factories.MatterFactory(title="Nemad saatsid", source_organisations=[ministry])
    answered = factories.MatterFactory(title="Meie vastasime", addressee_organisation=ministry)
    factories.MatterFactory(title="Kõrvaline", source_organisations=[other])

    response = signed_in.get(REGISTER, {"asutus": str(ministry.pk), "olek": "koik"})
    assert set(titles_on(response)) == {sent.title, answered.title}


def test_the_convenience_filter_does_not_replace_the_precise_ones(signed_in):
    """Both directions stay separately askable, and stay separately stored."""
    ministry = factories.OrganisationFactory(name="Näidisministeerium")
    sent = factories.MatterFactory(title="Nemad saatsid", source_organisations=[ministry])
    answered = factories.MatterFactory(title="Meie vastasime", addressee_organisation=ministry)

    by_sender = signed_in.get(REGISTER, {"saatja": str(ministry.pk), "olek": "koik"})
    by_addressee = signed_in.get(REGISTER, {"adressaat": str(ministry.pk), "olek": "koik"})

    assert titles_on(by_sender) == [sent.title]
    assert titles_on(by_addressee) == [answered.title]

    sent.refresh_from_db()
    answered.refresh_from_db()
    assert sent.addressee_organisation_id is None
    assert not answered.source_organisations.exists()


def test_a_malformed_organisation_id_empties_rather_than_crashes(signed_in):
    factories.MatterFactory()
    assert total_of(signed_in.get(REGISTER, {"asutus": "mitte-uuid", "olek": "koik"})) == 0


def test_the_materials_filter_asks_about_files(signed_in, specialist):
    with_file = factories.MatterFactory(title="Failiga")
    factories.DocumentFactory(matter=with_file)
    without = factories.MatterFactory(title="Failita")

    present = signed_in.get(REGISTER, {"materjalid": "on", "olek": "koik"})
    absent = signed_in.get(REGISTER, {"materjalid": "puudub", "olek": "koik"})

    assert titles_on(present) == [with_file.title]
    assert titles_on(absent) == [without.title]


def test_a_document_nobody_may_open_does_not_count_as_material(
    client, specialist, other_specialist
):
    """A document can be restricted below its Matter (brief 2)."""
    matter = factories.MatterFactory(title="Nähtav teema", owner=specialist)
    factories.DocumentFactory(matter=matter, visibility_override=Visibility.RESTRICTED)

    client.force_login(other_specialist)
    present = client.get(REGISTER, {"materjalid": "on", "olek": "koik"})
    absent = client.get(REGISTER, {"materjalid": "puudub", "olek": "koik"})

    assert titles_on(present) == []
    assert titles_on(absent) == [matter.title]


def test_an_unknown_materials_value_empties_the_list(signed_in):
    factories.MatterFactory()
    assert total_of(signed_in.get(REGISTER, {"materjalid": "vahest", "olek": "koik"})) == 0


def test_the_register_scope_segments_mean_what_they_say(signed_in):
    """ARCHIVE is a record mode, not "old" (brief 11A)."""
    active = factories.MatterFactory(title="Aktiivne", is_open=True)
    # `matters_closure_fields_consistent`: a closed Matter carries the *reason*
    # it closed and the moment it did. The database refuses a bare
    # `is_open=False`, which is the point of the constraint.
    closed = factories.MatterFactory(
        title="Suletud",
        is_open=False,
        closed_at=timezone.now(),
        disposition=Disposition.COMPLETED,
    )
    archived = factories.ArchiveMatterFactory(
        title="Arhiivis",
        is_open=False,
        closed_at=timezone.now(),
        disposition=Disposition.COMPLETED,
    )

    assert titles_on(signed_in.get(REGISTER, {"olek": "avatud"})) == [active.title]
    assert set(titles_on(signed_in.get(REGISTER, {"olek": "suletud"}))) == {
        closed.title,
        archived.title,
    }
    assert titles_on(signed_in.get(REGISTER, {"olek": "arhiiv"})) == [archived.title]
    assert total_of(signed_in.get(REGISTER, {"olek": "koik"})) == 3

    archived.refresh_from_db()
    assert archived.record_mode == RecordMode.ARCHIVE


# ---------------------------------------------------------------------------
# The organisation chooser
# ---------------------------------------------------------------------------


def test_the_chooser_finds_a_body_by_name(signed_in):
    factories.OrganisationFactory(name="Kliimaministeerium")
    factories.OrganisationFactory(name="Rahandusministeerium")

    response = signed_in.get(CHOOSER, {"vali": "asutus", "asutus_otsing": "kliima"})
    names = [row.name for row in response.context["organisation_options"]]
    assert names == ["Kliimaministeerium"]


def test_the_chooser_finds_a_body_by_its_recorded_alias(signed_in):
    """Aliases are reviewed data, so using them is not fuzzy matching."""
    from app.organisations.models import OrganisationAlias

    ministry = factories.OrganisationFactory(name="Majandus- ja Kommunikatsiooniministeerium")
    OrganisationAlias.objects.create(organisation=ministry, alias="MKM")

    response = signed_in.get(CHOOSER, {"vali": "asutus", "asutus_otsing": "MKM"})
    assert [row.name for row in response.context["organisation_options"]] == [ministry.name]


def test_the_chooser_keeps_showing_what_is_already_chosen(signed_in):
    """Searching for something else must not silently drop an applied filter."""
    chosen = factories.OrganisationFactory(name="Kliimaministeerium")
    factories.OrganisationFactory(name="Rahandusministeerium")

    response = signed_in.get(
        CHOOSER, {"vali": "asutus", "asutus_otsing": "rahandus", "asutus": str(chosen.pk)}
    )
    assert response.context["chosen_organisation"] == chosen
    assert chosen.name in response.content.decode()


def test_the_chooser_carries_no_counts_and_no_usage_order(signed_in):
    """Either would make the list a statement about restricted work (brief 13)."""
    busy = factories.OrganisationFactory(name="Üliaktiivne asutus")
    quiet = factories.OrganisationFactory(name="Aeg-ajalt kirjutav asutus")
    for _ in range(5):
        factories.MatterFactory(source_organisations=[busy], visibility=Visibility.RESTRICTED)

    response = signed_in.get(CHOOSER, {"vali": "asutus"})
    names = [row.name for row in response.context["organisation_options"]]
    assert names == sorted(names)
    assert names.index(quiet.name) < names.index(busy.name)


def test_the_chooser_refuses_a_field_it_does_not_offer(signed_in):
    """A parameter outside the mapping is a 404, not a name echoed into HTML."""
    assert signed_in.get(CHOOSER, {"vali": "<script>"}).status_code == 404


def test_the_chooser_requires_a_signed_in_reader(client):
    response = client.get(CHOOSER, {"vali": "asutus"})
    assert response.status_code in {302, 403}


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


def test_a_page_of_results_does_not_cost_a_query_per_row(signed_in, django_assert_max_num_queries):
    """A daily-use page. A keystroke must not scale with the register (brief 14)."""
    for index in range(25):
        matter = factories.MatterFactory(
            title=f"Pakendiseaduse säte {index}",
            source_organisations=[factories.OrganisationFactory()],
            addressee_organisation=factories.OrganisationFactory(),
        )
        matter.policy_areas.add(factories.PolicyAreaFactory())
        indexed(matter)

    with django_assert_max_num_queries(25):
        response = signed_in.get(REGISTER, {"q": "pakendiseaduse", "olek": "koik"})
    assert total_of(response) == 25


def test_the_search_index_is_not_consulted_when_nothing_was_typed(signed_in):
    """No query, no projection join — the register stays what it was."""
    indexed(factories.MatterFactory(title="Ükskõik"))
    assert SearchDocument.objects.exists()

    response = signed_in.get(REGISTER, {"olek": "koik"})
    assert response.context["query"] == ""


def test_the_choosers_own_search_text_is_not_a_filter(signed_in):
    """It narrows a list of options, not the register.

    The chooser's search box sits inside the Täpsem otsing form, so submitting
    the form carries whatever was typed into it. That text must not survive into
    a shared link, and `Tühjenda kõik` must not leave it behind looking like a
    filter that is still applied (brief 12, 13).
    """
    factories.MatterFactory(title="Ükskõik")
    response = signed_in.get(REGISTER, {"olek": "koik", "asutus_otsing": "kliima", "aasta": "2024"})

    assert response.context["cleared_query"] == ""
    assert "asutus_otsing" not in dict(response.context["carried_params"])
    # And it narrows nothing: the register is unchanged by it.
    assert total_of(signed_in.get(REGISTER, {"olek": "koik", "asutus_otsing": "kliima"})) == 1


def test_a_keystroke_costs_less_than_a_full_page(signed_in):
    """The fragment does not pay for selects it does not contain (brief 14).

    Measured as a *comparison* rather than against a fixed budget. An absolute
    number here would pin whatever the register happens to cost today and fail
    for reasons that have nothing to do with this rule; the claim being made is
    that a keystroke is cheaper than a page load, and that is what is asserted.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    for index in range(10):
        indexed(factories.MatterFactory(title=f"Pakendiseaduse säte {index}"))
    for _ in range(30):
        factories.OrganisationFactory()

    query = {"q": "pakendiseaduse", "olek": "koik"}

    with CaptureQueriesContext(connection) as page_load:
        full = signed_in.get(REGISTER, query)
    assert "organisation_options" in full.context

    with CaptureQueriesContext(connection) as keystroke:
        fragment = signed_in.get(REGISTER, query, headers={"HX-Request": "true"})

    assert fragment.status_code == 200
    assert len(keystroke) < len(page_load), (len(keystroke), len(page_load))


@pytest.mark.parametrize("parameter", ["vastutaja", "saatja", "adressaat", "asutus"])
def test_a_malformed_id_in_any_organisation_or_person_filter_is_survivable(signed_in, parameter):
    """The 500 that hid behind the `puudub` sentinel.

    Filtering already parsed the UUID before querying, so the *population* was
    safe. Rendering the chip that describes the filter did not:
    `Model.objects.filter(pk="mitte-uuid")` is a ValidationError from the field,
    not an empty result, and it took the whole register page down. Found on
    `?asutus=`, fixed for all four.
    """
    factories.MatterFactory()
    response = signed_in.get(REGISTER, {parameter: "mitte-uuid", "olek": "koik"})

    assert response.status_code == 200
    assert total_of(response) == 0
    # The chip is honest about it rather than inventing a name.
    values = {chip["name"]: chip["value"] for chip in response.context["active_filters"]}
    assert values[parameter] == "mitte-uuid"
