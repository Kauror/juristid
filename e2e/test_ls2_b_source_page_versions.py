"""A source page read by two captures, in a real browser against the real stylesheets.

The page is `legacy_import/source_page.html` rendered with hand-built objects —
a `PageReading` per captured version, as `source_page_versions` makes them — and
set into the browser with the application's CSS inlined, the way
`e2e/test_aud_w4_source_page_wrapping.py` sets it. No server and no seeded world:
what is proved is the template and the stylesheet — the version line, the
change words, the closed «Varasemad versioonid» disclosure that opens by
keyboard, and nothing pushing a phone-width page sideways (live-sync round 2).

Every string is invented.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.template.loader import render_to_string
from playwright.sync_api import expect

from app.legacy_import import historical_reading as reading
from app.legacy_import.source_page_versions import ORIGINAL, PageReading
from e2e.conftest import document_overflows

pytestmark = pytest.mark.e2e

STATIC = Path(settings.BASE_DIR) / "static"
STYLESHEETS = (
    "css/tokens.css",
    "css/base.css",
    "css/app.css",
    "css/ux.css",
    "css/historical-reading.css",
)

#: Where the screenshots go, when asked (`LS2_B_SCREENS`).
SCREENS = os.environ.get("LS2_B_SCREENS", "")

FIRST_IMPORTED = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
LATER_IMPORTED = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)


def _reading(number: int, *, latest: bool) -> PageReading:
    return PageReading(
        number=number,
        token=ORIGINAL if number == 1 else str(uuid.uuid4()),
        revision=None if number == 1 else SimpleNamespace(pk=uuid.uuid4()),
        title="Näidisseaduse eelnõu" + ("" if number == 1 else " (Riigikogus)"),
        source_section="Näidissektsioon",
        source_section_group="",
        source_modified_at=datetime(2024, 4, 2, tzinfo=UTC)
        if number == 1
        else datetime(2026, 11, 7, tzinfo=UTC),
        imported_at=FIRST_IMPORTED if number == 1 else LATER_IMPORTED,
        capture_id=f"synthetic-capture-{number}",
        source_xml_sha256=str(number) * 64,
        blocks=[],
        links=[],
        latest=latest,
    )


def _text(text: str, *, change: str = "", dated: bool = False) -> dict:
    return {
        "kind": "text",
        "text": text,
        "parts": [],
        "depth_class": reading.depth_class(0),
        "list_item": False,
        "anchor": "",
        "heading": False,
        "dated": {"date": "05.11.2026"} if dated else None,
        "change": change,
    }


def _file(*, change: str = "", later: bool = False) -> dict:
    version = SimpleNamespace(pk=uuid.uuid4(), size_bytes=51_200)
    return {
        "kind": "file",
        "state": "imported",
        "filename": "Koja_arvamus_kooskõlastusringile_lõplik_versioon.pdf",
        "size_bytes": 48_213,
        "opens_inline": True,
        "version": version,
        "document": SimpleNamespace(pk=uuid.uuid4()),
        "original_pk": None,
        "added_known": True,
        "added_at": datetime(2024, 3, 12, tzinfo=UTC),
        "anchor": "",
        "depth_class": reading.depth_class(1),
        "type": "PDF",
        "quiet": False,
        "later": {"number": 2, "modified_at": datetime(2026, 11, 6, tzinfo=UTC)} if later else None,
        "change": change,
    }


def _html(*, latest: bool) -> str:
    pk = uuid.uuid4()
    first, second = _reading(1, latest=False), _reading(2, latest=True)
    shown = second if latest else first
    page = SimpleNamespace(
        title=first.title,
        display_location="Näidissektsioon",
        source_modified_at=first.source_modified_at,
        source_notebook="Näidismärkmik",
        source_section="Näidissektsioon",
        source_parent_page="",
        page_level=1,
        block_count=6,
        file_count=1,
        file_bytes=48_213,
        reading_order_ambiguous=False,
        source_page_id="{0000BB01-0000-4000-8000-0000000000B1}",
        page_key="0000BB01-0000-4000-8000-0000000000B1",
        source_xml_sha256="1" * 64,
        capture_id=first.capture_id,
        first_imported_at=FIRST_IMPORTED,
        source_xml_storage_key="",
        onenote_hyperlink="",
    )
    if latest:
        blocks = [
            _text("12.03.2024 - Ministeerium saatis eelnõu kooskõlastamiseks.", dated=True),
            _file(change="versioned", later=True),
            _text("Koda koostab arvamuse liikmete ettepanekute põhjal."),
            _text("Tähtaega pikendati nelja nädalani.", change="changed"),
            _text("05.11.2026 - Riigikogu menetlusse saadetud eelnõu uus tekst.", change="added"),
        ]
        summary = [
            "Võrreldes eelmise versiooniga: 1 plokk lisandus, 1 plokk muutus, 1 plokk eemaldati.",
            "1 faili uus versioon.",
        ]
        removed = ("Ajutine märkus, mis hiljem kustutati.",)
    else:
        blocks = [
            _text("12.03.2024 - Ministeerium saatis eelnõu kooskõlastamiseks.", dated=True),
            _file(),
            _text("Koda koostab arvamuse liikmete ettepanekute põhjal."),
            _text("Tähtaeg on kolm nädalat."),
            _text("Ajutine märkus, mis hiljem kustutati."),
        ]
        summary, removed = [], ()
    return render_to_string(
        "legacy_import/source_page.html",
        {
            "matter": SimpleNamespace(
                pk=pk, title="Näidisseaduse eelnõu", display_reference="2026_21"
            ),
            "link": SimpleNamespace(pk=pk, get_match_method_display="Lovable'i täpne lehe GUID"),
            "page": page,
            "reading": shown,
            "readings": [first, second],
            "latest_reading": second,
            "version_summary": summary,
            "removed_blocks": removed,
            "blocks": reading.reading_entries(blocks),
            "contents": [],
            "file_summary": "Lehel on 1 fail; see on selle teema dokument.",
            "page_links": [],
            "other_matters": [],
            "dossier": SimpleNamespace(shared=False),
            "dossier_rows": [],
            "dossier_files": [],
            "nav_active": "teemad",
        },
    )


def _open(page, width: int, *, latest: bool = True) -> None:
    page.set_default_timeout(5000)
    page.set_viewport_size({"width": width, "height": 900})
    styles = "".join(
        f"<style>{(STATIC / sheet).read_text(encoding='utf-8')}</style>" for sheet in STYLESHEETS
    )
    page.set_content(_html(latest=latest).replace("<head>", f"<head>{styles}", 1))


def _shot(page, name: str) -> None:
    if SCREENS:
        Path(SCREENS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SCREENS) / f"{name}.png"), full_page=True)


@pytest.mark.parametrize("width", [1440, 390])
def test_the_latest_version_says_what_it_is_and_opens_the_earlier_ones(page, width):
    _open(page, width)
    note = page.locator(".sourceversion")
    expect(note).to_be_visible()
    expect(note.locator(".sourceversion__line").first).to_contain_text("Viimane hõive")
    expect(note.locator(".sourceversion__line").first).to_contain_text("versioon 2/2")
    expect(note).to_contain_text("1 plokk lisandus, 1 plokk muutus, 1 plokk eemaldati")
    expect(page.locator(".casefile .statechip--new")).to_have_count(3)
    earlier = note.locator("details", has_text="Varasemad versioonid")
    link = earlier.get_by_role("link", name="Versioon 1")
    expect(link).to_be_hidden()
    _shot(page, f"latest-{width}")
    earlier.locator("summary").focus()
    page.keyboard.press("Enter")
    expect(link).to_be_visible()
    assert link.get_attribute("href").endswith("?hoive=algne")
    assert not document_overflows(page)
    _shot(page, f"latest-versions-open-{width}")


@pytest.mark.parametrize("width", [1440, 390])
def test_an_earlier_version_says_a_later_one_exists(page, width):
    _open(page, width, latest=False)
    note = page.locator(".sourceversion")
    expect(note).to_contain_text("Varasem versioon")
    expect(note.get_by_role("link", name="Ava viimane hõive")).to_be_visible()
    expect(page.locator(".casefile .statechip--new")).to_have_count(0)
    assert not document_overflows(page)
    _shot(page, f"earlier-{width}")
