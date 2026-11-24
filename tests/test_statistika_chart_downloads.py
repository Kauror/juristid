"""UX-005: every Statistika chart panel downloads as SVG/PNG and Excel, from its spec.

The numbers in a file are the page's for the same URL: the SVG prints the
spec's texts, the workbook holds the spec's values, and the page prints the
same spec (`test_statistika_chart_spec_records`). Unknown stays blank, the file
is the reader's own scope, and every file is on the export trail
(docs/adr/0214 §8).
"""

from __future__ import annotations

import datetime as dt
import io
import re
from xml.etree import ElementTree

import pytest
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core.xlsx_safety import workbook_bytes
from app.legacy_import.lovable_sync_models import OpinionOutcome
from app.reporting import chart_spec, chart_svg, chart_workbook
from app.reporting import opinion_stats as stats
from tests import factories
from tests.test_statistika_dashboard import _imported

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 11, 16)
_REAL_LOCALDATE = timezone.localdate
SVG_NS = "{http://www.w3.org/2000/svg}"


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    def frozen(value=None, timezone=None):
        return TODAY if value is None else _REAL_LOCALDATE(value, timezone)

    monkeypatch.setattr("django.utils.timezone.localdate", frozen)
    return TODAY


@pytest.fixture
def world(db):
    mari = factories.UserFactory(display_name="Mari Maasikas")
    jaan = factories.UserFactory(display_name="Jaan Tamm")
    rows = [
        (mari, 2026, dt.date(2026, 2, 1), dt.date(2026, 2, 10), dt.date(2026, 2, 11)),
        (mari, 2026, dt.date(2026, 4, 1), dt.date(2026, 5, 4), dt.date(2026, 4, 20)),
        (jaan, 2026, dt.date(2026, 5, 2), dt.date(2026, 5, 12), None),
        (jaan, 2024, dt.date(2024, 1, 1), dt.date(2024, 2, 1), dt.date(2024, 1, 31)),
    ]
    for number, (owner, year, received, sent_on, deadline) in enumerate(rows, start=1):
        matter = factories.MatterFactory(
            owner=owner,
            title=f"Näidisseadus {number}",
            reference_year=year,
            received_date=received,
        )
        _imported(matter, OpinionOutcome.SENT, sent_on, request_deadline=deadline)
    return {"mari": mari, "jaan": jaan}


def _parse(text: str) -> ElementTree.Element:
    """The application's own SVG, not outside input; it carries no DTD or entity."""
    return ElementTree.fromstring(text.split("\n", 1)[1])  # noqa: S314


def _svg(client, key: str, query: str = "") -> tuple[ElementTree.Element, str]:
    response = client.get(reverse("reporting:chart_svg", args=[key]) + query)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("image/svg+xml")
    text = response.content.decode()
    return _parse(text), text


def _texts(root: ElementTree.Element) -> list[str]:
    return [node.text or "" for node in root.iter(f"{SVG_NS}text")]


def _sheet(client, key: str, query: str = "") -> tuple[list[tuple], list[str]]:
    response = client.get(reverse("reporting:chart_xlsx", args=[key]) + query)
    assert response.status_code == 200
    book = load_workbook(io.BytesIO(response.content))
    data = [tuple(row) for row in book["Andmed"].iter_rows(values_only=True)]
    info = [row[0] for row in book["Teave"].iter_rows(values_only=True)]
    return data, info


def _charts(client, query: str = "") -> dict[str, chart_spec.ChartSpec]:
    return client.get(reverse("reporting:dashboard") + query).context["charts"]


# ---------------------------------------------------------------------------
# Parity: the page, the image and the workbook hold the same numbers
# ---------------------------------------------------------------------------

QUERIES = ["", "?periood=aasta&aasta=2026", "?periood=6&jurist=Mari", "?trend=5&jurist=Jaan"]


@pytest.mark.parametrize("query", QUERIES)
def test_every_workbook_holds_the_panels_values_blank_for_unknown(client, specialist, world, query):
    client.force_login(specialist)

    for key, spec in _charts(client, query).items():
        data, info = _sheet(client, key, query)
        assert list(data[0]) == chart_workbook.columns(spec), key
        assert len(data) - 1 == len(spec.categories), key
        for row, (title, values) in zip(data[1:], spec.rows(), strict=True):
            assert row[0] == title
            # A None value is an empty cell, never 0.
            assert list(row[1 : 1 + len(values)]) == list(values), (key, title)
        if spec.totals:
            assert [row[1 + len(spec.series)] for row in data[1:]] == list(spec.totals)
        assert spec.title in info and chart_workbook.BLANK in info
        assert chart_workbook.SCOPE in info
        for label, value in spec.filters:
            assert f"{label}: {value}" in info


@pytest.mark.parametrize("query", QUERIES)
def test_every_image_prints_the_panels_texts_title_period_and_filters(
    client, specialist, world, query
):
    client.force_login(specialist)

    for key, spec in _charts(client, query).items():
        if spec.is_table:
            response = client.get(reverse("reporting:chart_svg", args=[key]) + query)
            assert response.status_code == 404, key
            continue
        root, _ = _svg(client, key, query)
        texts = _texts(root)
        assert texts[0] == spec.title and texts[1] == spec.period, key
        jurist = dict(spec.filters)["Jurist"]
        assert any(f"Jurist: {jurist}" in text for text in texts), key
        if spec.empty:
            assert "Andmed puuduvad." in texts
            continue
        if spec.kind == chart_spec.STACKED:
            for series in spec.series:
                assert series.name in texts
            printed = [t for t in texts if t.isdigit()]
            for total in spec.totals:
                if total:
                    assert str(total) in printed
        elif spec.kind == chart_spec.YEARS:
            series = spec.series[0]
            for value, text in zip(series.values, series.texts, strict=True):
                assert (text if value is not None else "—") in texts, (key, text)
        elif spec.kind == chart_spec.SHARE:
            for text in spec.series[0].texts:
                assert text in texts
        joined = " ".join(texts)
        for note in spec.notes:
            assert note[:24] in joined, (key, note)


def test_the_flow_image_prints_each_months_two_numbers(client, specialist, world):
    client.force_login(specialist)
    spec = _charts(client)["voog"]

    texts = _texts(_svg(client, "voog")[0])

    for received, sent in zip(spec.series[0].values, spec.series[1].values, strict=True):
        for value in (received, sent):
            if value:
                assert str(value) in texts


def test_an_unknown_year_is_no_column_and_a_dash_never_a_zero():
    years = [
        stats.YearBar(2024, None, "—", "2024: andmed puuduvad", 0.0),
        stats.YearBar(2025, 20.0, "20", "2025", 100.0),
        stats.YearBar(2026, 0.0, "0", "2026", 0.0, partial=True),
    ]
    spec = chart_spec.trend(stats.Trend("days", "Antud aeg", years, "mediaan"), "2024–2026", "")

    svg = chart_svg.render(spec, generated=TODAY)
    texts = _texts(_parse(svg))
    book = chart_workbook.workbook(spec, dt.datetime(2026, 11, 16, 12))

    assert "—" in texts and "Andmed puuduvad: 2024." in texts
    assert chart_workbook.rows(spec)[0] == ["2024", None, None]
    assert chart_workbook.rows(spec)[2] == ["2026", 0.0, "jah"]
    assert workbook_bytes(book)[:2] == b"PK"


# ---------------------------------------------------------------------------
# The file itself
# ---------------------------------------------------------------------------


def test_the_svg_is_self_contained(client, specialist, world):
    client.force_login(specialist)

    for key in ("kuud", "voog", "aeg", "otsus", "trend-ontime"):
        root, text = _svg(client, key)
        assert root.tag == f"{SVG_NS}svg"
        for tag in ("script", "image", "foreignObject", "style", "link", "use"):
            assert not list(root.iter(f"{SVG_NS}{tag}")), (key, tag)
        assert "href" not in text and "url(" not in text and "var(--" not in text
        assert "@import" not in text and "http://" not in text.replace(
            'xmlns="http://www.w3.org/2000/svg"', ""
        )


def test_the_opaque_image_has_a_white_ground_and_the_transparent_one_none(
    client, specialist, world
):
    client.force_login(specialist)

    opaque, _ = _svg(client, "kuud")
    clear, _ = _svg(client, "kuud", "?taust=puudub")

    first = next(iter(opaque.iter(f"{SVG_NS}rect")))
    assert first.get("fill") == "#ffffff" and first.get("width") == str(chart_svg.WIDTH)
    assert not [r for r in clear.iter(f"{SVG_NS}rect") if r.get("fill") == "#ffffff"]


def test_the_download_is_private_named_and_cannot_run(client, specialist, world):
    client.force_login(specialist)

    response = client.get(
        reverse("reporting:chart_svg", args=["kuud"]) + "?periood=aasta&aasta=2026"
    )

    assert response["Cache-Control"] == "private, no-store"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'none'" in response["Content-Security-Policy"]
    assert "statistika-kuud-2026.svg" in response["Content-Disposition"]
    xlsx = client.get(reverse("reporting:chart_xlsx", args=["trend-sent"]) + "?trend=5")
    assert "statistika-trend-sent-2022-2026.xlsx" in xlsx["Content-Disposition"]


def test_a_formula_looking_label_is_text_in_the_workbook():
    period = stats.rolling_period("12", TODAY)
    spec = chart_spec.group(
        "saatjad",
        [stats.InstrumentRow("x", "=SUM(A1)", 1, 100.0, 100, None, None, 0)],
        period,
        "",
    )

    book = load_workbook(
        io.BytesIO(workbook_bytes(chart_workbook.workbook(spec, dt.datetime(2026, 11, 16, 12))))
    )

    cell = book["Andmed"]["A2"]
    assert cell.value == "=SUM(A1)" and cell.data_type == "s"
    assert book["Andmed"]["D2"].value is None  # unknown on-time share: blank


# ---------------------------------------------------------------------------
# Scope, refusal and the trail
# ---------------------------------------------------------------------------


def test_an_administrator_without_a_business_role_downloads_nothing_counted(
    client, administrator, world
):
    client.force_login(administrator)

    data, _ = _sheet(client, "trend-sent")

    assert all(row[1] == 0 for row in data[1:])
    root, _ = _svg(client, "kuud")
    assert "Andmed puuduvad." in _texts(root)


def test_a_signed_out_reader_is_sent_to_sign_in_and_nothing_is_recorded(client, world):
    before = SecurityAuditEvent.objects.count()

    response = client.get(reverse("reporting:chart_xlsx", args=["kuud"]))

    assert response.status_code == 302
    assert SecurityAuditEvent.objects.count() == before


def test_an_unknown_panel_is_a_404_and_writes_nothing(client, specialist, world):
    client.force_login(specialist)
    before = SecurityAuditEvent.objects.count()

    assert client.get(reverse("reporting:chart_xlsx", args=["eiole"])).status_code == 404
    assert client.get(reverse("reporting:chart_svg", args=["liigid"])).status_code == 404

    assert SecurityAuditEvent.objects.count() == before


def test_each_format_is_one_row_on_the_trail(client, specialist, world):
    client.force_login(specialist)
    url = reverse("reporting:chart_svg", args=["kuud"])

    client.get(url + "?jurist=Mari")
    client.get(url + "?kuju=png&taust=puudub")
    client.get(reverse("reporting:chart_xlsx", args=["kuud"]))

    rows = SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.EXPORT_GENERATED
    ).order_by("created_at")
    assert [(r.detail["export"], r.detail["format"]) for r in rows] == [
        ("reporting.chart.kuud", "svg"),
        ("reporting.chart.kuud", "png"),
        ("reporting.chart.kuud", "xlsx"),
    ]
    assert rows[0].detail["filter_keys"] == ["jurist"]
    assert "Mari" not in str(rows[0].detail)


def test_the_page_offers_four_formats_for_a_chart_and_excel_for_a_table(client, specialist, world):
    client.force_login(specialist)

    body = client.get(reverse("reporting:dashboard") + "?jurist=Mari").content.decode()

    menus = re.findall(r'<details class="opinionmenu statdownload".*?</details>', body, re.S)
    charts = _charts(client, "?jurist=Mari")
    assert len(menus) == len(charts)
    for menu in menus:
        labels = re.findall(r'class="opinionmenu__item"[^>]*>([^<]+)<', menu)
        assert labels in (["PNG", "PNG (läbipaistev)", "SVG", "Excel"], ["Excel"]), labels
        for link in re.findall(r'(?:href|data-chart-png)="([^"]+)"', menu):
            assert "jurist=Mari" in link
    tables = [m for m in menus if "PNG" not in m]
    assert len(tables) == 3
