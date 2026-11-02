"""«Vali veerud» and «Laadi Excel» above a register answer of zero rows (review of PR #585, P3).

The chooser and the three downloads used to be drawn only above rows, so a
filter that matched nothing took away the two downloads that ignore the table's
filters along with it. They are now drawn whenever the reader sees the
register; the filtered download of an empty answer is a valid file that says it
holds 0 rows.
"""

from __future__ import annotations

import html
import io
import re

import openpyxl
import pytest
from django.http import QueryDict
from django.urls import reverse

from app.core.enums import Visibility
from tests import factories

pytestmark = pytest.mark.django_db

REGISTER = reverse("matters:matter_list")
TABLE = reverse("matters:export_table")
FULL = reverse("matters:export_full")
LEGACY = reverse("reporting:export_legacy_xlsx")

LINKS = (
    "Laadi Excel tabelis kuvatud infoga",
    "Laadi Excel kõikide andmetega",
    "Laadi Excel vana Exceli formaadis",
)


def _book(response):
    assert response.status_code == 200, response.status_code
    return openpyxl.load_workbook(io.BytesIO(response.content))


def _rows(sheet):
    return [[cell.value for cell in row] for row in sheet.iter_rows(min_row=2)]


def _assert_controls(body: str) -> None:
    assert 'class="columnbar"' in body
    assert "Vali veerud" in body
    assert 'name="veerud"' in body
    for label in LINKS:
        assert label in body, label
    assert f'href="{FULL}"' in body
    assert f'href="{LEGACY}"' in body


def _table_href(body: str) -> QueryDict:
    match = re.search(rf'href="{re.escape(TABLE)}\?([^"]*)"', body)
    assert match, "no filtered-table download link"
    return QueryDict(html.unescape(match.group(1)))


def _empty_stage():
    return factories.StageFactory(key="zm-tuhi", label_et="Kellelgi pole seda")


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def test_a_filter_matching_nothing_keeps_the_chooser_and_all_three_downloads(signed_in):
    factories.MatterFactory(title="Teine seis")
    stage = _empty_stage()

    response = signed_in.get(REGISTER, {"hetkeseis": stage.key})

    assert response.context["total"] == 0
    body = response.content.decode()
    assert "Selle filtriga teemasid ei leitud." in body
    _assert_controls(body)


def test_an_empty_authorized_register_keeps_the_chooser_and_all_three_downloads(signed_in):
    response = signed_in.get(REGISTER)

    assert response.context["total"] == 0
    _assert_controls(response.content.decode())


def test_a_search_matching_nothing_keeps_the_controls_in_the_live_fragment(signed_in):
    factories.MatterFactory(title="Midagi")

    response = signed_in.get(REGISTER, {"q": "olematusõna"}, HTTP_HX_REQUEST="true")

    assert response.context["total"] == 0
    _assert_controls(response.content.decode())


def test_filters_and_columns_survive_in_the_links_and_the_chooser(signed_in, specialist):
    stage = _empty_stage()
    address = {"hetkeseis": stage.key, "vastutaja": str(specialist.pk), "veerud": "viide"}

    response = signed_in.get(REGISTER, address)
    body = response.content.decode()

    assert response.context["total"] == 0
    carried = QueryDict(_table_href(body).urlencode())
    assert carried.get("hetkeseis") == stage.key
    assert carried.get("vastutaja") == str(specialist.pk)
    assert carried.get("veerud") == "viide"
    # The chooser re-submits every filter and only the columns it shows.
    chooser = body[
        body.index('class="columnchooser__body"') : body.index(
            "</form>", body.index('class="columnchooser__body"')
        )
    ]
    assert f'<input type="hidden" name="hetkeseis" value="{stage.key}">' in chooser
    assert f'<input type="hidden" name="vastutaja" value="{specialist.pk}">' in chooser
    assert '<input type="hidden" name="veerud"' not in chooser
    assert re.search(r'name="veerud" value="viide"[^>]*checked', chooser)
    assert "Vali veerud · muudetud" in body
    reset = QueryDict(html.unescape(response.context["columns_reset_query"]))
    assert reset.get("hetkeseis") == stage.key and "veerud" not in reset


def test_saabunud_still_has_no_chooser_when_empty(signed_in):
    body = signed_in.get(reverse("matters:inbox")).content.decode()

    assert "Vali veerud" not in body
    assert "exportmenu" not in body


# ---------------------------------------------------------------------------
# The downloads behind an empty answer
# ---------------------------------------------------------------------------


def test_the_filtered_export_of_nothing_is_a_valid_workbook_that_says_zero(signed_in):
    factories.MatterFactory(title="Ei vasta")
    stage = _empty_stage()

    book = _book(signed_in.get(TABLE, {"hetkeseis": stage.key, "veerud": "viide,hetkeseis"}))

    assert book.sheetnames == ["Teave", "Teemad"]
    sheet = book["Teemad"]
    assert [cell.value for cell in sheet[1]] == ["Pealkiri", "Hetkeseis", "Viide"]
    assert _rows(sheet) == []
    teave = [str(row[0]) for row in _rows(book["Teave"])]
    assert any(line.startswith("Ridu: 0") for line in teave), teave
    assert any("ei vasta ühtegi teemat" in line for line in teave), teave
    assert any(line.startswith("Tingimused: ") and "Kellelgi pole seda" in line for line in teave)


def test_the_full_export_still_holds_every_visible_teema_when_the_table_matched_nothing(
    signed_in,
):
    factories.MatterFactory(title="Sõltumatu teema", reporting_year=2026)
    stage = _empty_stage()
    page = signed_in.get(REGISTER, {"hetkeseis": stage.key})
    assert page.context["total"] == 0

    # The link the empty page offers carries no filters, and the view would
    # ignore them if it did.
    assert f'href="{FULL}"' in page.content.decode()
    book = _book(signed_in.get(FULL, {"hetkeseis": stage.key}))

    titles = {row[5] for row in _rows(book["2026"])}
    assert "Sõltumatu teema" in titles


def test_the_legacy_export_is_reachable_from_an_empty_answer(signed_in):
    factories.MatterFactory(title="Vana formaat", reporting_year=2026)
    stage = _empty_stage()
    assert f'href="{LEGACY}"' in signed_in.get(REGISTER, {"hetkeseis": stage.key}).content.decode()

    response = signed_in.get(LEGACY)

    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    openpyxl.load_workbook(io.BytesIO(response.content))


def test_an_outsider_sees_the_controls_and_still_downloads_nothing(client):
    factories.MatterFactory(title="Salajane", reporting_year=2026, visibility=Visibility.RESTRICTED)
    client.force_login(factories.AdministratorFactory())

    page = client.get(REGISTER)

    assert page.status_code == 200
    assert "Salajane" not in page.content.decode()
    table = client.get(TABLE)
    if table.status_code == 200:
        assert _rows(_book(table)["Teemad"]) == []
    full = client.get(FULL)
    if full.status_code == 200:
        book = _book(full)
        assert all(_rows(book[name]) == [] for name in book.sheetnames if name != "Teave")
