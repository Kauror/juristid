"""The Statistika surfaces: navigation, filters, lists and exports.

These go through the real views because that is where filter state, the shared
gate and the CSV headers actually live. What each number *means* is settled in
`test_reporting_metrics`; this file is about whether a person can reach it,
narrow it, share the URL and take the rows away.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.accounts.enums import AuthMode
from app.reporting.context import DEFAULT_PERIOD, parse_period, period_options
from tests.synthetic_statistics import RESTRICTED_ONLY_WORD

pytestmark = pytest.mark.django_db

TABS = (
    "/statistika/",
    "/statistika/teemad/",
    "/statistika/tegevus/",
    "/statistika/ajalooline/",
    "/statistika/andmekvaliteet/",
)


def csv_body(response) -> str:
    assert response.status_code == 200
    return b"".join(response.streaming_content).decode("utf-8-sig")


# ---------------------------------------------------------------------------
# Reaching it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", TABS)
def test_every_tab_renders(path, client, world):
    client.force_login(world.martin)
    response = client.get(path)
    assert response.status_code == 200
    assert "Statistika" in response.content.decode()


def test_statistika_is_in_the_main_navigation(client, world):
    client.force_login(world.martin)
    response = client.get("/statistika/")
    body = response.content.decode()
    assert 'href="/statistika/"' in body
    assert "Ülevaade" in body and "Minu töö" in body and "Teemad" in body


def test_the_active_tab_is_marked_for_a_screen_reader(client, world):
    client.force_login(world.martin)
    body = client.get("/statistika/teemad/").content.decode()
    assert 'aria-current="page"' in body


def test_a_signed_out_reader_is_sent_to_sign_in(client, world):
    response = client.get("/statistika/")
    assert response.status_code == 302


def test_the_shared_gate_reaches_statistika_without_a_persona(client, world, settings):
    """The department scope has to be useful before anybody has picked a name.

    Somebody arrives from the password with `request.user` still anonymous.
    Reaching for it here would render an empty page to a reader who is entitled
    to the whole department's statistics; borrowing an arbitrary persona to fill
    it would show one lawyer's restricted files to whoever knew the password
    (Stage-2D auth brief 6).
    """
    password = "seda-parooli-ei-ole-kusagil-mujal"  # noqa: S105
    settings.AUTH_MODE = AuthMode.SHARED_GATE
    settings.SHARED_GATE_PASSWORD = password
    settings.DEV_LOGIN_ENABLED = False
    settings.LOGIN_URL = "accounts:choose_persona"

    assert client.post(reverse("accounts:shared_gate"), {"password": password}).status_code == 302

    response = client.get("/statistika/", {"periood": "koik"})
    assert response.status_code == 200
    # The department's numbers, and none of the restricted Matter's.
    assert response.context["cards"][0].value == 11
    assert RESTRICTED_ONLY_WORD not in response.content.decode()


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_the_default_period_is_the_current_year(world):
    period = parse_period("", world.today)
    assert period.key == DEFAULT_PERIOD
    assert period.start_year == period.end_year == world.today.year


def test_the_period_selector_offers_four_quick_filters(world):
    keys = [option.key for option in period_options(world.today)]
    assert keys == ["kaesolev", "eelmine", "viimased5", "koik"]


def test_an_explicit_year_in_the_url_is_honoured(world):
    period = parse_period(str(world.archive_year), world.today)
    assert period.start_year == period.end_year == world.archive_year


def test_an_unreadable_period_falls_back_rather_than_erroring(world):
    """A statistics URL is something people edit by hand and forward."""
    assert parse_period("eelmine-aasta", world.today).key == DEFAULT_PERIOD
    assert parse_period("2026-2020", world.today).key == DEFAULT_PERIOD


def test_filter_state_survives_in_the_url_and_in_the_chips(client, world):
    client.force_login(world.martin)
    response = client.get(
        "/statistika/teemad/", {"periood": "koik", "vastutaja": str(world.martin.pk)}
    )
    assert response.status_code == 200
    chips = response.context["chips"]
    assert [chip.label for chip in chips] == ["Vastutaja"]
    assert chips[0].value == "Martin Testjurist"
    # And the chip's remove link keeps the period while dropping the owner.
    assert "periood=koik" in chips[0].remove_query
    assert "vastutaja" not in chips[0].remove_query


def test_a_chip_shows_a_name_rather_than_a_key(client, world):
    client.force_login(world.martin)
    response = client.get("/statistika/teemad/", {"valdkond": "maksud"})
    chips = response.context["chips"]
    assert chips[0].value == "Maksud"


def test_changing_tab_keeps_every_filter(client, world):
    client.force_login(world.martin)
    response = client.get("/statistika/", {"periood": "koik", "vastutaja": str(world.martin.pk)})
    body = response.content.decode()
    assert f"/statistika/teemad/?periood=koik&amp;vastutaja={world.martin.pk}" in body


def test_a_narrowed_period_changes_the_numbers(client, world):
    client.force_login(world.martin)
    everything = client.get("/statistika/teemad/", {"periood": "koik"})
    this_year = client.get("/statistika/teemad/", {"periood": "kaesolev"})
    assert everything.context["cards"][0].value == 11
    assert this_year.context["cards"][0].value == 5


def test_only_the_tabs_that_can_use_a_filter_offer_it(client, world):
    client.force_login(world.martin)
    matters = client.get("/statistika/teemad/")
    historical = client.get("/statistika/ajalooline/")
    assert matters.context["show_tag"] is True
    assert matters.context["show_section"] is False
    assert historical.context["show_section"] is True
    assert historical.context["show_tag"] is False


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def test_the_submission_list_is_the_products_only_list_of_them(client, world):
    client.force_login(world.martin)
    response = client.get("/statistika/arvamused/", {"periood": "koik"})
    assert response.status_code == 200
    assert response.context["total"] == 3
    body = response.content.decode()
    assert "Esimene arvamus" in body
    assert "Koostamisel arvamus" not in body  # a draft is not a submission


def test_the_material_list_shows_occurrences_with_their_content_hash(client, world):
    client.force_login(world.martin)
    response = client.get("/statistika/materjalid/", {"periood": "koik"})
    assert response.context["total"] == 8
    body = response.content.decode()
    assert "eelnou.pdf" in body
    assert "eelnou-koopia.pdf" in body  # the same bytes, a second occurrence


def test_an_unknown_materialisation_state_is_a_404_not_an_empty_list(client, world):
    """An empty list would read as "there are none of those"."""
    client.force_login(world.martin)
    assert client.get("/statistika/materjalid/", {"seisund": "kadunud"}).status_code == 404


def test_an_unreadable_uuid_in_a_list_url_is_a_404(client, world):
    client.force_login(world.martin)
    assert client.get("/statistika/arvamused/", {"saaja": "mitte-uuid"}).status_code == 404


def test_the_definitions_page_lists_the_whole_catalogue(client, world):
    from app.reporting.metric_catalogue import CATALOGUE

    client.force_login(world.martin)
    response = client.get("/statistika/definitsioonid/")
    assert response.status_code == 200
    assert len(response.context["definitions"]) == len(CATALOGUE)
    body = response.content.decode()
    assert "Teemasid perioodil" in body
    assert "MATTERS_TOTAL@1" in body


def test_the_definitions_page_names_what_is_never_measured(client, world):
    client.force_login(world.martin)
    body = client.get("/statistika/definitsioonid/").content.decode()
    assert "tootlikkus" in body.lower()
    assert "18.8" in body


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def test_the_matter_export_carries_provenance_columns(client, world):
    client.force_login(world.martin)
    body = csv_body(client.get("/statistika/eksport/teemad.csv", {"periood": "koik"}))
    header = body.splitlines()[0]
    assert header.split(";")[:5] == [
        "viide",
        "pealkiri",
        "kirje_liik",
        "paritolu",
        "aruandlusaasta",
    ]
    assert len(body.strip().splitlines()) == 12  # header plus eleven visible Matters


def test_the_export_opens_in_a_spreadsheet_here(client, world):
    """Semicolons and a byte-order mark, or it lands in one column of mojibake."""
    client.force_login(world.martin)
    response = client.get("/statistika/eksport/teemad.csv", {"periood": "koik"})
    raw = b"".join(response.streaming_content)
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b";" in raw
    assert "Näidisteema" in raw.decode("utf-8-sig") or "eelnõu" in raw.decode("utf-8-sig")


def test_the_export_respects_the_active_filters(client, world):
    client.force_login(world.martin)
    everything = csv_body(client.get("/statistika/eksport/teemad.csv", {"periood": "koik"}))
    narrowed = csv_body(client.get("/statistika/eksport/teemad.csv", {"periood": "kaesolev"}))
    assert len(everything.strip().splitlines()) == 12
    assert len(narrowed.strip().splitlines()) == 6


def test_the_submission_export_keeps_addressees_and_copies_apart(client, world):
    client.force_login(world.martin)
    body = csv_body(client.get("/statistika/eksport/arvamused.csv", {"periood": "koik"}))
    header, *rows = body.strip().splitlines()
    assert "adressaadid" in header and "teadmiseks" in header
    first = next(row for row in rows if "Esimene arvamus" in row)
    columns = first.split(";")
    assert "Näidisministeerium" in columns[5]
    assert "Näidiskomisjon" in columns[6]


def test_the_material_export_shows_the_duplicate_as_two_rows(client, world):
    client.force_login(world.martin)
    body = csv_body(client.get("/statistika/eksport/materjalid.csv", {"periood": "koik"}))
    rows = body.strip().splitlines()[1:]
    assert len(rows) == 8
    hashes = [row.split(";")[5] for row in rows]
    assert len(set(hashes)) == 7  # one SHA-256 appears twice, on purpose


def test_the_quality_export_marks_which_rows_are_limitations(client, world):
    client.force_login(world.martin)
    body = csv_body(client.get("/statistika/eksport/andmekvaliteet.csv"))
    header, *rows = body.strip().splitlines()
    assert header.split(";") == ["jarjekord", "arv", "on_katvuse_markus", "selgitus"]
    assert any(row.split(";")[2] == "jah" for row in rows)
    assert any(row.split(";")[2] == "ei" for row in rows)


def test_an_unknown_export_is_a_404(client, world):
    client.force_login(world.martin)
    assert client.get("/statistika/eksport/koik.csv").status_code == 404


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------


def test_the_quality_page_separates_tasks_from_limitations(client, world):
    client.force_login(world.martin)
    response = client.get("/statistika/andmekvaliteet/")
    queues = response.context["queues"]
    by_key = {queue.key: queue for queue in queues}

    assert by_key["reconciliation_conflict"].count == 1
    assert by_key["reconciliation_conflict"].is_coverage_note is False
    assert by_key["reading_order"].is_coverage_note is True
    assert by_key["materialisation_empty"].is_coverage_note is True


def test_the_attention_total_excludes_coverage_limitations(client, world, reporting_context):
    from app.reporting import metric_catalogue as keys
    from app.reporting.selectors.quality import queues
    from app.reporting.services import compute

    context = reporting_context(world.martin)
    actionable = [queue for queue in queues(context) if not queue.is_coverage_note]
    assert compute(keys.DATA_QUALITY_ATTENTION, context).value == sum(
        queue.count for queue in actionable
    )


def test_an_archive_matter_without_a_next_action_is_never_a_queue_row(
    client, world, reporting_context
):
    """Five archive rows have none, and not one of them is a defect."""
    from app.reporting.selectors.quality import queues

    row = next(
        queue
        for queue in queues(reporting_context(world.martin))
        if queue.key == "active_without_next_action"
    )
    assert row.count == 1


def test_the_quality_page_says_what_it_deliberately_does_not_measure(client, world):
    client.force_login(world.martin)
    body = client.get("/statistika/andmekvaliteet/").content.decode()
    assert "LEGACY_MEMBER_ASKED_COUNT" in body
    assert "vastamismäära" in body


def test_a_reader_who_cannot_open_the_review_queue_is_told_where_it_lives(world, reporting_context):
    """A number with no link and no explanation reads as a dead end.

    The queue can create Matters, so it stays administrator-only. What a lawyer
    gets instead is the count and a sentence saying whose job it is.
    """
    from app.reporting.selectors.quality import queues

    for viewer, expect_link in ((world.martin, False), (world.admin, True)):
        row = next(
            queue
            for queue in queues(reporting_context(viewer))
            if queue.key == "reconciliation_conflict"
        )
        assert bool(row.url) is expect_link
        assert ("halduri ajaloo-ülevaatuse" in row.explanation) is not expect_link
