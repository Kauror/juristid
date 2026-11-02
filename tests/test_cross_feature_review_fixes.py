"""Findings of the independent cross-feature review of 2026-11-02, each held by a test."""

from __future__ import annotations

import io
from datetime import date

import openpyxl
import pytest
from django.http import QueryDict
from django.urls import reverse
from docx import Document

from app.core.xlsx_safety import Column, SheetWriter, new_workbook, workbook_bytes, xml_safe
from tests import factories
from tests.report_builder_support import as_post, entry, form_fields, sent

pytestmark = pytest.mark.django_db

OCTOBER = {"algus": "1.10.2026", "lopp": "31.10.2026"}
REGISTER = reverse("matters:matter_list")


# ---------------------------------------------------------------------------
# P2 — a browser-typed multi-line summary is not «the reader's edit»
# ---------------------------------------------------------------------------


def test_a_crlf_summary_is_still_the_source_text_after_a_round_trip(signed_in, capture_evidence):
    matter = factories.MatterFactory(title="Mitmerealine")
    sent(matter, capture_evidence, day=date(2026, 10, 2), summary="Rida 1\r\nRida 2")

    page = signed_in.get(reverse("reporting:report_builder"), OCTOBER)
    preview = signed_in.post(
        reverse("reporting:report_preview"), as_post(form_fields(page), toiming="eelvaade")
    )

    assert preview.status_code == 200
    assert "(muudetud)" not in preview.content.decode()
    export = signed_in.post(
        reverse("reporting:report_export"), as_post(form_fields(preview), toiming="laadi")
    )
    assert export.status_code == 200
    text = "\n".join(
        paragraph.text
        for cell in Document(io.BytesIO(export.content)).tables[1]._cells
        for paragraph in cell.paragraphs
    )
    assert "Rida 1" in text and "\r" not in text


# ---------------------------------------------------------------------------
# P2 — a control character pasted into a record never fails an export
# ---------------------------------------------------------------------------


def test_xml_safe_replaces_only_what_xml_cannot_hold():
    assert xml_safe("a\x0bb\x0cc\td\ne") == "a b c\td\ne"


def test_a_cell_with_a_control_character_is_written():
    workbook = new_workbook()
    writer = SheetWriter(workbook, "Leht", [Column("A")])
    writer.append(["Pealkiri\x0bpaberilt"])

    sheet = openpyxl.load_workbook(io.BytesIO(workbook_bytes(workbook)))["Leht"]

    assert sheet["A2"].value == "Pealkiri paberilt"


@pytest.mark.parametrize(
    "route", ["matters:export_table", "matters:export_full", "reporting:export_legacy_xlsx"]
)
def test_every_xlsx_export_survives_a_control_character(signed_in, route):
    factories.MatterFactory(title="Kopeeritud\x0cWordist", reporting_year=2026)

    response = signed_in.get(reverse(route), {"olek": "koik"})

    assert response.status_code == 200


def test_the_word_report_survives_a_control_character(signed_in, capture_evidence):
    matter = factories.MatterFactory(title="Juhtmärk\x0bpealkirjas")
    sent(matter, capture_evidence, day=date(2026, 10, 2), summary="Kokkuvõte\x0cPDF-ist")

    page = signed_in.get(reverse("reporting:report_builder"), OCTOBER)
    export = signed_in.post(
        reverse("reporting:report_export"), as_post(form_fields(page), toiming="laadi")
    )

    assert export.status_code == 200


# ---------------------------------------------------------------------------
# P3 — an event recorded after composing stops the export instead of vanishing
# ---------------------------------------------------------------------------


def test_an_event_recorded_after_composing_is_announced_and_stops_the_export(
    signed_in, capture_evidence
):
    matter = factories.MatterFactory(title="Hiljem lisandunud")
    sent(matter, capture_evidence, day=date(2026, 10, 2), summary="Esimene.")
    page = signed_in.get(reverse("reporting:report_builder"), OCTOBER)
    fields = form_fields(page)

    entry(matter, day=date(2026, 10, 12), body="<p>Kohtumine hiljem.</p>")
    export = signed_in.post(reverse("reporting:report_export"), as_post(fields, toiming="laadi"))

    assert export.status_code == 409
    body = export.content.decode()
    assert "uut sündmust" in body and "Hiljem lisandunud" in body


# ---------------------------------------------------------------------------
# P3 — register values are never dropped or left unremovable
# ---------------------------------------------------------------------------


def test_two_different_window_starts_are_unreadable_not_last_wins(signed_in):
    factories.MatterFactory(title="Midagi")

    response = signed_in.get(f"{REGISTER}?saadetud_alates=1.1.2026&saadetud_alates=1.6.2026")

    assert list(response.context["page"].object_list) == []


def test_a_chip_removes_a_value_that_arrived_with_spaces(signed_in):
    stage = factories.StageFactory(key="rv-a", label_et="RV A")

    response = signed_in.get(f"{REGISTER}?hetkeseis=%20{stage.key}")
    (chip,) = [chip for chip in response.context["active_filters"] if chip["name"] == "hetkeseis"]

    assert QueryDict(chip["remove_query"]).getlist("hetkeseis") == []


def test_a_chosen_value_the_panel_no_longer_lists_stays_ticked(signed_in):
    body = signed_in.get(REGISTER, {"valdkond": "vana-ala"}).content.decode()

    assert 'name="valdkond" value="vana-ala" checked' in body
