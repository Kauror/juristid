"""Contract B and Exports 1–2 — «Vali veerud», «Laadi Excel tabelis kuvatud infoga»,
«Laadi Excel kõikide andmetega» (owner's brief of 2026-11-02).
"""

from __future__ import annotations

import io
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import openpyxl
import pytest
from django.db import connection
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.matters import register_columns, register_export, register_selection
from app.matters.enums import MatterOrigin
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from tests import factories
from tests.synthetic_statistics import _document, _version

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")
TABLE = reverse("matters:export_table")
FULL = reverse("matters:export_full")
TALLINN = ZoneInfo("Europe/Tallinn")


def _book(response):
    assert response.status_code == 200, response.status_code
    return openpyxl.load_workbook(io.BytesIO(response.content))


def _rows(sheet):
    return [[cell.value for cell in row] for row in sheet.iter_rows(min_row=2)]


def _sent(matter, on: date):
    document = _document(matter, f"{matter.title} lõplik")
    version = _version(document, filename="a.pdf", payload=f"cx {matter.pk} {on}")
    return Submission.objects.create(
        matter=matter,
        title="Arvamus",
        status=SubmissionStatus.SENT,
        sent_at=datetime.combine(on, time(10), tzinfo=TALLINN),
        final_version=version,
    )


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


def test_no_veerud_is_exactly_the_register_as_it_was():
    assert register_columns.chosen_keys(QueryDict("")) == (
        "pealkiri",
        "hetkeseis",
        "vastutaja",
        "jargmiseks",
        "kuupaev",
        "viimane",
    )


def test_both_spellings_unknown_words_and_the_title():
    comma = register_columns.chosen_keys(QueryDict("veerud=valdkond,viide,mitte-olemas"))
    repeated = register_columns.chosen_keys(QueryDict("veerud=viide&veerud=valdkond"))

    assert comma == repeated
    assert comma[0] == "pealkiri"
    assert "mitte-olemas" not in comma


def test_a_sort_on_a_hidden_column_falls_back_visibly():
    sort, notice = register_columns.effective_sort("kuupaev_asc", ("pealkiri", "hetkeseis"))
    kept, silent = register_columns.effective_sort("kuupaev_asc", ("pealkiri", "kuupaev"))

    assert (sort, notice) == (register_selection.DEFAULT_SORT, register_columns.SORT_RESET_NOTICE)
    assert (kept, silent) == ("kuupaev_asc", "")


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


def test_choosing_a_column_does_not_change_which_rows_are_shown(signed_in):
    area = factories.PolicyAreaFactory(key="cx-ala", name_et="CX ala")
    with_area = factories.MatterFactory(title="Valdkonnaga")
    with_area.policy_areas.add(area)
    factories.MatterFactory(title="Valdkonnata")

    plain = signed_in.get(REGISTER)
    chosen = signed_in.get(REGISTER, {"veerud": "valdkond"})

    assert chosen.context["total"] == plain.context["total"] == 2
    body = chosen.content.decode()
    assert "CX ala" in body
    assert 'class="table__extra"' in body


def test_hiding_default_columns_drops_their_cells_and_headings(signed_in):
    factories.MatterFactory(title="Üks")

    body = signed_in.get(REGISTER, {"veerud": "hetkeseis"}).content.decode()

    assert 'class="table__stage"' in body
    assert 'class="table__owner"' not in body
    assert "table__lastactivity" not in body


def test_a_filter_keeps_the_columns_and_the_columns_keep_the_filters(signed_in, specialist):
    factories.MatterFactory(owner=specialist)

    response = signed_in.get(REGISTER, {"vastutaja": str(specialist.pk), "veerud": "viide"})

    assert response.context["columns_param"] == "pealkiri,viide"
    carried = response.context["chooser_carried"]
    assert ("vastutaja", str(specialist.pk)) in carried
    assert all(name != "veerud" for name, _ in carried)
    assert QueryDict(response.context["columns_reset_query"]).get("vastutaja") == str(specialist.pk)


def test_a_hidden_sort_column_is_reset_and_said(signed_in):
    factories.MatterFactory()

    response = signed_in.get(REGISTER, {"jarjestus": "kuupaev_asc", "veerud": "hetkeseis"})

    assert register_columns.SORT_RESET_NOTICE in response.content.decode()


def test_saabunud_keeps_its_six_static_columns(signed_in):
    body = signed_in.get(reverse("matters:inbox")).content.decode()

    assert "table__extra" not in body
    assert "Vali veerud" not in body


def test_query_count_does_not_grow_with_rows_or_columns(signed_in):
    every = ",".join(key for key in register_columns.BY_KEY if key != "pealkiri")
    for _ in range(3):
        factories.MatterFactory()
    with CaptureQueriesContext(connection) as few:
        signed_in.get(REGISTER, {"veerud": every})
    for _ in range(12):
        factories.MatterFactory()
    with CaptureQueriesContext(connection) as many:
        signed_in.get(REGISTER, {"veerud": every})

    assert len(many) <= len(few) + 2, (len(few), len(many))


# ---------------------------------------------------------------------------
# Export 1 — the table as shown
# ---------------------------------------------------------------------------


def test_export_one_is_every_row_of_the_applied_address_in_table_order(signed_in, specialist):
    stage = factories.StageFactory(key="cx-s", label_et="CX seis")
    for index in range(30):
        factories.MatterFactory(title=f"Rida {index:02}", stage=stage if index % 3 else None)
    address = (
        f"hetkeseis={stage.key}&hetkeseis=puudub&jarjestus=title&kaupa=12&veerud=viide,hetkeseis"
    )

    shown = []
    for page in (1, 2, 3):
        response = signed_in.get(f"{REGISTER}?{address}&leht={page}")
        shown += [matter.title for matter in response.context["page"].object_list]
    book = _book(signed_in.get(f"{TABLE}?{address}"))
    sheet = book["Teemad"]

    assert [cell.value for cell in sheet[1]] == ["Pealkiri", "Hetkeseis", "Viide"]
    titles = [row[0] for row in _rows(sheet)]
    assert titles == shown and len(titles) == 30
    assert book.sheetnames == ["Teave", "Teemad"]


def test_export_one_carries_the_search_word(signed_in):
    from app.search.indexing import indexable_matters, refresh_matters

    factories.MatterFactory(title="Pakendiaktsiis")
    factories.MatterFactory(title="Midagi muud")
    refresh_matters(indexable_matters())

    sheet = _book(signed_in.get(TABLE, {"q": "pakendiaktsiis"}))["Teemad"]

    assert [row[0] for row in _rows(sheet)] == ["Pakendiaktsiis"]


def test_export_one_types_its_cells(signed_in):
    matter = factories.MatterFactory(
        title='=HYPERLINK("https://example.invalid")',
        response_deadline=date(2026, 12, 1),
        reference_year=2026,
    )
    _sent(matter, date(2026, 11, 2))

    sheet = _book(signed_in.get(TABLE, {"veerud": "viide,tahtaeg,saadetud", "olek": "koik"}))[
        "Teemad"
    ]
    header = [cell.value for cell in sheet[1]]
    row = dict(zip(header, sheet[2], strict=True))

    assert row["Pealkiri"].data_type == "s" and row["Pealkiri"].quotePrefix
    assert row["Viide"].data_type == "s" and row["Viide"].value.startswith("2026_")
    assert row["Arvamuse tähtaeg"].is_date
    assert row["Arvamuse tähtaeg"].value.date() == date(2026, 12, 1)
    assert row["Viimati saadetud"].value.date() == date(2026, 11, 2)


def test_export_one_is_private_and_reads_nothing_on_a_post(signed_in):
    response = signed_in.get(TABLE)

    assert response["Cache-Control"] == "private, no-store"
    assert ".xlsx" in response["Content-Disposition"]
    assert signed_in.post(TABLE).status_code == 405


def test_an_administrator_without_a_business_role_exports_nothing(client):
    factories.MatterFactory(title="Nähtamatu")
    client.force_login(factories.AdministratorFactory())

    response = client.get(TABLE)

    if response.status_code == 200:
        assert _rows(_book(response)["Teemad"]) == []


# ---------------------------------------------------------------------------
# Export 2 — every supported field
# ---------------------------------------------------------------------------


def test_export_two_groups_by_reporting_year_and_never_by_creation_year(signed_in):
    factories.MatterFactory(title="Kahekümne neli", reporting_year=2024)
    factories.MatterFactory(title="Kahekümne kuus", reporting_year=2026)
    factories.MatterFactory(
        title="OneNote", reporting_year=2025, origin=MatterOrigin.LEGACY_ONENOTE
    )
    factories.MatterFactory(title="Aastata", reporting_year=None)

    book = _book(signed_in.get(FULL))

    assert book.sheetnames == ["Teave", "2026", "2024", "Teadmata"]
    assert [row[0] for row in _rows(book["2024"])][0:1] and "Kahekümne neli" in [
        row[5] for row in _rows(book["2024"])
    ]
    unknown_titles = [row[5] for row in _rows(book["Teadmata"])]
    assert set(unknown_titles) == {"OneNote", "Aastata"}


def test_export_two_ignores_the_table_narrowing_and_says_so(signed_in, specialist):
    factories.MatterFactory(title="Avatud", reporting_year=2026)
    closed = factories.MatterFactory(title="Suletud", reporting_year=2026)
    from app.matters.services import close_matter

    close_matter(
        matter=closed, actor=specialist, disposition="COMPLETED", follow_ups_confirmed=True
    )

    book = _book(signed_in.get(FULL, {"olek": "avatud", "q": "midagi"}))

    titles = {row[5] for row in _rows(book["2026"])}
    assert {"Avatud", "Suletud"} <= titles
    teave = " ".join(str(row[0]) for row in _rows(book["Teave"]))
    assert "Teemade tabeli filtrid ja otsing ei kehti" in teave


def test_export_two_carries_valdkond_and_identity(signed_in):
    area = factories.PolicyAreaFactory(key="cx-v", name_et="Juristidi oma valdkond")
    matter = factories.MatterFactory(title="Valdkonnaga", reporting_year=2026)
    matter.policy_areas.add(area)

    sheet = _book(signed_in.get(FULL))["2026"]
    header = [cell.value for cell in sheet[1]]
    row = dict(zip(header, [cell.value for cell in sheet[2]], strict=True))

    assert header[0] == "Teema ID" and row["Teema ID"] == str(matter.pk)
    assert row["Valdkond"] == "Juristidi oma valdkond"
    assert "Teema ID" in header and "Päritolu" in header


def test_export_two_query_count_does_not_grow_per_row(specialist):
    for _ in range(4):
        factories.MatterFactory(reporting_year=2026)
    with CaptureQueriesContext(connection) as few:
        register_export.full_workbook(specialist)
    for _ in range(20):
        factories.MatterFactory(reporting_year=2026)
    with CaptureQueriesContext(connection) as many:
        register_export.full_workbook(specialist)

    assert len(many) == len(few)
