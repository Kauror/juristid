"""UI-12 in a real browser: the historical OneNote copy reads as a document.

The page is the real template, filled by the real block projection
(`historical_views._rendered_blocks`, `historical_reading`) from the synthetic
shared dossier's own stored blocks (`tests/test_ui12_world.py`, in the shape the
capture import writes them), with the application's stylesheets inlined and
served from a routed `http://juristid.test/` page so that `#plokk-N` and the
contents list behave as on the real page. No server, no seeded world, no
database: what is proved is the projection, the template and the stylesheet.

Every string is invented.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.template.loader import render_to_string
from playwright.sync_api import expect

from app.legacy_import import historical_reading as reading
from app.legacy_import.historical_views import _quiet_common_state, _rendered_blocks
from e2e.conftest import document_overflows
from tests.test_ui12_world import (
    DOSSIER_FILES,
    DOSSIER_TITLE,
    DOUBLE_QUOTED_SHOWN,
    LITERAL_ENTITY_SHOWN,
    dossier_blocks,
)

pytestmark = pytest.mark.e2e

STATIC = Path(settings.BASE_DIR) / "static"
STYLESHEETS = (
    "css/tokens.css",
    "css/base.css",
    "css/app.css",
    "css/ux.css",
    "css/historical-reading.css",
)
ORIGIN = "http://juristid.test"
GUID = "00000071-0000-4000-8000-00000000AB71"
PATH = "/ajalugu/0b5d8f0e-1111-4c2b-9d0e-000000000071/"


def _stored_blocks() -> list[dict]:
    """The dossier's blocks as `onenote_capture.rendered_blocks` stores them."""
    out = []
    for block in dossier_blocks(GUID):
        row = dict(block, source_kind=block["kind"])
        if block["kind"] == "FILE":
            row["kind"], row["resource_key"] = "FILE_ATTACHMENT", block["object_id"]
        else:
            row["resource_key"] = ""
        out.append(row)
    return out


def _html() -> str:
    blocks = _stored_blocks()
    resources, records = {}, {}
    for n, name, created, _parent in DOSSIER_FILES:
        key = f"{{{GUID}}}{{{n}}}{{B0}}"
        resources[key] = SimpleNamespace(
            pk=uuid.uuid4(),
            original_filename=name,
            size_bytes=48_000 + n,
            source_added_at=datetime.fromisoformat(created.replace("Z", "+00:00"))
            if created
            else None,
        )
        records[key] = SimpleNamespace(
            document_id=None,
            document=None,
            document_version=None,
            state="SKIPPED",
            error_code="SHARED_PAGE_AWAITING_REVIEW",
        )
    page = SimpleNamespace(
        title=DOSSIER_TITLE,
        blocks=blocks,
        links=[],
        display_location="Kliima",
        source_modified_at=datetime(2024, 9, 18, tzinfo=UTC),
        source_notebook="Näidismärkmik",
        source_section="Kliima",
        source_parent_page="",
        page_level=1,
        reading_order_ambiguous=False,
        source_page_id="{00000071-0000-4000-8000-00000000AB71}{1}{E19531}",
        page_key="p-00000071-0000-4000-8000-00000000ab71",
        source_xml_sha256="ab" * 32,
        capture_id="synthetic-capture",
        first_imported_at=None,
        block_count=len(blocks),
        file_count=len(DOSSIER_FILES),
        file_bytes=0,
        source_xml_storage_key="",
        onenote_hyperlink="",
    )
    rendered = _rendered_blocks(
        page,
        resources,
        records,
        readable=set(),
        servable={resource.pk for resource in resources.values()},
    )
    _quiet_common_state(rendered, shared=True)
    here = SimpleNamespace(pk=uuid.uuid4())
    teemad = [
        SimpleNamespace(
            link=here if index == 1 else SimpleNamespace(pk=uuid.uuid4()),
            matter=SimpleNamespace(pk=uuid.uuid4(), display_reference=reference, title=title),
        )
        for index, (reference, title) in enumerate(
            (
                ("2024_101", "Kliimaseaduse väljatöötamiskavatsus"),
                ("2025_140", DOSSIER_TITLE),
                ("2025_196", "Kliimaseaduse rakendusaktid"),
            )
        )
    ]
    html = render_to_string(
        "legacy_import/source_page.html",
        {
            "matter": SimpleNamespace(
                pk=uuid.uuid4(), title=DOSSIER_TITLE, display_reference="2025_140"
            ),
            "link": SimpleNamespace(pk=here.pk, get_match_method_display="Lovable'i link"),
            "page": page,
            "blocks": reading.reading_entries(rendered),
            "contents": reading.contents(rendered),
            "file_summary": (
                f"Lehel on {len(DOSSIER_FILES)} faili; ükski neist ei ole selle teema dokument."
            ),
            "page_links": [],
            "other_matters": [],
            "dossier": SimpleNamespace(
                shared=True, teemad=teemad, chains=[], kind="NONE", hidden=0, unchained=[]
            ),
            "dossier_rows": [],
            "dossier_files": [],
        },
    )
    styles = "".join(
        f"<style>{(STATIC / sheet).read_text(encoding='utf-8')}</style>" for sheet in STYLESHEETS
    )
    return html.replace("<head>", f"<head>{styles}", 1)


def _open(page, width: int, fragment: str = "") -> None:
    page.set_default_timeout(5000)
    page.set_viewport_size({"width": width, "height": 900})
    body = _html()
    page.route(
        f"{ORIGIN}/**",
        lambda route: route.fulfill(status=200, content_type="text/html", body=body),
    )
    page.goto(f"{ORIGIN}{PATH}{fragment}")


@pytest.mark.parametrize("width", [1440, 390])
def test_the_page_reads_at_a_reading_width_and_nothing_overflows(page, width):
    _open(page, width)
    assert not document_overflows(page)
    widths = page.evaluate(
        """() => [...document.querySelectorAll('.casefile__text')].map(p => {
            const probe = document.createElement('span');
            probe.textContent = '0'.repeat(10);
            p.appendChild(probe);
            const ch = probe.getBoundingClientRect().width / 10;
            probe.remove();
            return p.getBoundingClientRect().width / ch;
        })"""
    )
    # About 72 characters at most, whatever the viewport.
    assert widths and max(widths) <= 80, widths
    # Every card holds its content: nothing is cut off by `overflow: hidden` (E-03).
    clipped = page.evaluate(
        """() => [...document.querySelectorAll('section.card, section.card *')]
            .filter(el => getComputedStyle(el).display !== 'none'
                && el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 1)
            .map(el => el.className)"""
    )
    assert clipped == []


def test_prose_quotes_show_as_quotes_and_a_typed_entity_as_typed(page):
    _open(page, 1440)
    texts = page.locator(".casefile__text").all_inner_texts()
    assert DOUBLE_QUOTED_SHOWN in texts
    assert LITERAL_ENTITY_SHOWN in texts
    assert not [text for text in texts if "&quot;kliima" in text]
    link = page.get_by_role("link", name="https://eelnoud.example.invalid/kliima/2024-0142")
    expect(link).to_have_attribute("rel", "noopener noreferrer")


def test_the_shared_status_reads_once_and_every_file_keeps_its_download(page):
    _open(page, 1440)
    expect(page.get_by_text("Ootab teemaga seostamist")).to_have_count(0)
    expect(page.locator(".casefile__file")).to_have_count(len(DOSSIER_FILES))
    expect(page.get_by_role("link", name="Laadi alla (allikas)")).to_have_count(len(DOSSIER_FILES))
    first = page.locator(".casefile__file").first
    expect(first.locator(".casefile__type")).to_have_text("PDF")
    expect(first).to_contain_text("lisatud 12.03.2024")


def test_the_contents_list_and_an_anchor_land_on_the_source_block(page):
    _open(page, 1440)
    toc = page.locator("details.sourcetoc")
    toc.locator("summary").click()
    entries = toc.get_by_role("link")
    expect(entries).to_have_count(6)
    entries.filter(has_text="Eelnõu läks kolmandale lugemisele").click()
    target = page.locator("#plokk-23")
    expect(target).to_be_in_viewport()
    assert page.evaluate("() => location.hash") == "#plokk-23"
    outline = target.evaluate("el => getComputedStyle(el).outlineStyle")
    assert outline == "solid"


@pytest.mark.parametrize("width", [1440, 390])
def test_a_timeline_link_opens_the_page_at_its_note(page, width):
    _open(page, width, "#plokk-14")
    target = page.locator("#plokk-14")
    expect(target).to_be_in_viewport()
    expect(target).to_have_text("05.2024 - Teine lugemine Riigikogus.")
    assert not document_overflows(page)
