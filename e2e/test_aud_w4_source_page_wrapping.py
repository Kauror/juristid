"""A historical OneNote page (`/ajalugu/<pk>/`) shows every character it holds (E-03).

The audit of 2026-11 found the page text of a historical case file clipped at
every width it measured — 320, 375, 768 and 1440 px — whenever a paragraph held
a token longer than the card: an address pasted into OneNote, or a file name
typed as one word. `.casefile` is a grid whose implicit track is `auto`, so the
longest unbreakable word set the track's minimum width; `.casefile__text` is
`white-space: pre-wrap`, which keeps the word whole; and `section.card` is
`overflow: hidden`, so the rest of the word was cut off at the card's edge.
No scrollbar and no ellipsis: the text was simply gone. Production holds 23
tokens over 190 characters, the longest 335.

**The page is the real template and the real stylesheets.** It is rendered from
`legacy_import/source_page.html` with hand-built objects and set into the
browser with `tokens.css`, `base.css`, `app.css` and `ux.css` inlined, the way
`e2e/test_tl_c_historical_rows.py` sets the Teema page's historical rows. No
server and no seeded world: what is proved is the template and the stylesheet.

**The assertion is widths, not a computed style.** A card whose `scrollWidth`
exceeds its `clientWidth` under `overflow: hidden` is a card hiding content,
and so is any text element whose own content is wider than its box. And the
text must still be there verbatim, wrapped and not elided: this is evidence,
read as it was archived.

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

pytestmark = pytest.mark.e2e

STATIC = Path(settings.BASE_DIR) / "static"
STYLESHEETS = (
    "css/tokens.css",
    "css/base.css",
    "css/app.css",
    "css/ux.css",
    "css/historical-reading.css",
)

#: 320 is the narrowest phone; 375 the product's claimed minimum; 768 the
#: tablet breakpoint; 1440 the desktop. The defect clipped at all four.
WIDTHS = (320, 375, 768, 1440)

#: Sub-pixel layout may report one pixel more than the box (as in
#: `e2e/test_long_token_wrapping.py`).
ROUNDING = 1

#: As long as the longest token in production (335 characters), and with no
#: character a browser may break at: no space, no hyphen, no slash.
UNBROKEN = ("Kooskõlastustabelimärkustekokkuvõte" * 10)[:335]
assert len(UNBROKEN) == 335

#: An address pasted into a paragraph, with a query string.
LONG_URL = (
    "https://eelnoud.example.invalid/legal-content/ET/TXT/HTML/?uri=CELEX%3A52026PC0142"
    "&qid=1758170000000&from=ET&locale=et&jalgimisparameeter="
    + "abcdefghijklmnopqrstuvwxyz0123456789"
    * 3
)

#: Ordinary prose: no word longer than the narrowest column, so every line
#: break it takes is at a space or where it was written with a line break.
PROSE = (
    "Eelnõu tuli kooskõlastusringile ja koda saatis märkused ministeeriumile.\n"
    "Vastus saadeti e-postiga."
)
PARAGRAPH = "Ministeeriumi vastus on siin: " + LONG_URL
LIST_ITEM = "Lisa: " + UNBROKEN
LONG_TITLE = "Näidisseaduse muutmise seaduse eelnõu " + UNBROKEN[:120]
FILENAME = "KOOSKÕLASTUSTABEL_" + "Näidisseaduse_muutmise_seaduse_eelnõu_" * 4 + "lõplik.docx"


def _block_text(text: str, *, list_item: bool = False) -> dict:
    return {"kind": "text", "text": text, "list_item": list_item}


def _block_file(state: str, filename: str) -> dict:
    version = SimpleNamespace(pk=uuid.uuid4())
    document = SimpleNamespace(pk=uuid.uuid4())
    return {
        "kind": "file",
        "state": state,
        "filename": filename,
        "size_bytes": 48_213,
        "opens_inline": False,
        "version": version,
        "document": document,
        "original_pk": uuid.uuid4(),
    }


def _dossier(pk: uuid.UUID) -> tuple[SimpleNamespace, list[dict], list[dict]]:
    """A page shared by two Teemad that are not one chain (docs/adr/0208)."""
    here = SimpleNamespace(
        link=SimpleNamespace(pk=pk),
        matter=SimpleNamespace(
            pk=uuid.uuid4(), display_reference="2026-0142", title=LONG_TITLE, is_open=True
        ),
    )
    other = SimpleNamespace(
        link=SimpleNamespace(pk=uuid.uuid4()),
        matter=SimpleNamespace(
            pk=uuid.uuid4(), display_reference="2025-0917", title=LONG_TITLE, is_open=False
        ),
    )
    dossier = SimpleNamespace(
        shared=True,
        ambiguous=True,
        hidden=False,
        kind="NONE",
        unchained=[],
        chains=[],
        teemad=[here, other],
    )
    rows = [{"teema": item, "relation": "Jätku seost ei ole"} for item in (here, other)]
    files = [
        {
            "state": "held_shared",
            "filename": FILENAME,
            "added_known": True,
            "added_at": datetime(2019, 3, 4, 10, 0, tzinfo=UTC),
        },
        {"state": "held_shared", "filename": UNBROKEN, "added_known": False},
    ]
    return dossier, rows, files


def _source_page_html(*, shared: bool) -> str:
    pk = uuid.uuid4()
    page = SimpleNamespace(
        title="Näidisseaduse eelnõu kooskõlastus",
        display_location="Näidismärkmik / Eelnõud",
        source_modified_at=None,
        source_notebook="Näidismärkmik",
        source_section="Eelnõud",
        source_parent_page="",
        page_level=1,
        file_count=2,
        file_bytes=96_426,
        reading_order_ambiguous=False,
        source_page_id="{6F1C2A0E-0000-4000-8000-00000000AB12}{1}{E19531}",
        page_key="näidis-" + "0123456789abcdef" * 4,
        source_xml_sha256="ab" * 32,
        capture_id="onenote-capture-2026-11-synthetic",
        source_xml_storage_key="",
        onenote_hyperlink="",
    )
    blocks = [
        _block_text(PROSE),
        _block_text(UNBROKEN),
        _block_text(PARAGRAPH),
        _block_text(LIST_ITEM, list_item=True),
        _block_file("imported", FILENAME),
        _block_file("held_shared", FILENAME),
    ]
    if shared:
        dossier, dossier_rows, dossier_files = _dossier(pk)
    else:
        dossier, dossier_rows, dossier_files = SimpleNamespace(shared=False), [], []
    return render_to_string(
        "legacy_import/source_page.html",
        {
            "matter": SimpleNamespace(pk=pk, title="Näidisseaduse eelnõu"),
            "link": SimpleNamespace(pk=pk, get_match_method_display="Käsitsi seotud"),
            "page": page,
            "blocks": blocks,
            "page_links": [{"url": LONG_URL, "text": UNBROKEN}],
            "other_matters": [],
            "dossier": dossier,
            "dossier_rows": dossier_rows,
            "dossier_files": dossier_files,
            "nav_active": "teemad",
        },
    )


def _open(page, width: int, *, shared: bool = False) -> None:
    page.set_default_timeout(5000)
    page.set_viewport_size({"width": width, "height": 900})
    styles = "".join(
        f"<style>{(STATIC / sheet).read_text(encoding='utf-8')}</style>" for sheet in STYLESHEETS
    )
    html = _source_page_html(shared=shared)
    # The rendered page's own stylesheet links point at `/static/`, which a
    # `set_content` page cannot load; the same files are inlined instead.
    page.set_content(html.replace("<head>", f"<head>{styles}", 1))
    # `Tehnilised andmed` is measured open: the identifiers inside are long too.
    page.evaluate("() => document.querySelectorAll('details').forEach(d => { d.open = true; })")


#: Every element of the page's content whose box hides part of what it holds,
#: and every card. A card is `overflow: hidden`, so content wider than it is cut
#: off; a text element whose content is wider than its own box is either cut off
#: by an ancestor or pushes out of it.
_MEASURE = """(rounding) => {
    const de = document.documentElement;
    const clipped = [];
    const scope = document.querySelector('main, .page') || document.body;
    for (const el of scope.querySelectorAll('section.card, section.card *')) {
        if (el.closest('.visually-hidden')) continue;
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.display === 'contents') continue;
        if (el.scrollWidth > el.clientWidth + rounding && el.clientWidth > 0) {
            clipped.push({
                tag: el.tagName.toLowerCase(),
                cls: (el.className || '').toString().slice(0, 60),
                scrollWidth: el.scrollWidth,
                clientWidth: el.clientWidth,
                text: (el.textContent || '').trim().slice(0, 40),
            });
        }
    }
    const cards = [...document.querySelectorAll('section.card')].map(card => {
        const box = card.getBoundingClientRect();
        const outside = [];
        for (const el of card.querySelectorAll('*')) {
            if (el.closest('.visually-hidden')) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) continue;
            if (r.right > box.right + rounding || r.left < box.left - rounding) {
                outside.push({
                    tag: el.tagName.toLowerCase(),
                    cls: (el.className || '').toString().slice(0, 60),
                    right: Math.round(r.right),
                    cardRight: Math.round(box.right),
                });
            }
        }
        return outside;
    }).flat();
    return {
        docOverflow: de.scrollWidth - de.clientWidth,
        clipped,
        outside: cards,
        texts: [...document.querySelectorAll('.casefile__text')].map(p => p.textContent),
        names: [...document.querySelectorAll('.casefile__name')].map(n => n.textContent),
    };
}"""


@pytest.mark.parametrize("shared", [False, True], ids=["one-teema", "shared-dossier"])
@pytest.mark.parametrize("width", WIDTHS)
def test_a_historical_page_shows_every_character_of_a_long_token(page, width, shared):
    """No card hides content, no text element is wider than its box, nothing is elided.

    `shared-dossier` adds the review tables of a page shared by several Teemad,
    whose file names are the same long tokens.
    """
    _open(page, width, shared=shared)

    measured = page.evaluate(_MEASURE, ROUNDING)

    assert measured["docOverflow"] <= ROUNDING, (
        f"the page scrolls sideways by {measured['docOverflow']}px at {width}px"
    )
    assert measured["clipped"] == [], f"wider than its box at {width}px: {measured['clipped']}"
    assert measured["outside"] == [], f"outside its card at {width}px: {measured['outside']}"
    # Verbatim: wrapped, never shortened.
    assert UNBROKEN in measured["texts"]
    assert PARAGRAPH in measured["texts"]
    assert LIST_ITEM in measured["texts"]
    assert all("…" not in text for text in measured["texts"])
    assert [name.strip() for name in measured["names"]] == [FILENAME, FILENAME]


@pytest.mark.parametrize("width", WIDTHS)
def test_ordinary_prose_still_breaks_only_at_spaces(page, width):
    """Wrapping anywhere is the last resort: an ordinary sentence is never split mid-word."""
    _open(page, width)

    measured = page.evaluate(
        """(prose) => {
            const p = [...document.querySelectorAll('.casefile__text')]
                .find(el => el.textContent === prose);
            const text = p.firstChild;
            const range = document.createRange();
            let top = null;
            const breaks = [];
            for (let i = 0; i < text.length; i++) {
                range.setStart(text, i);
                range.setEnd(text, i + 1);
                const rect = range.getBoundingClientRect();
                if (!rect.width) continue;
                // A new visual line: record what it was broken after.
                if (top !== null && rect.top > top + 1) breaks.push(text.data[i - 1]);
                top = rect.top;
            }
            return {breaks, lines: breaks.length + 1};
        }""",
        PROSE,
    )

    # The character before every line break is a space or the written break.
    assert all(char in (" ", "\n") for char in measured["breaks"]), measured
    if width >= 768:
        # Two written lines, each with room to spare: nothing else wraps.
        assert measured["lines"] == 2, measured


#: The breaks inside one element's text, as the character each new visual line
#: follows. A word wrapped mid-word follows a letter.
_BREAKS = """(el) => {
    const breaks = [];
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const range = document.createRange();
    let top = null;
    let previous = null;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        for (let i = 0; i < node.length; i++) {
            range.setStart(node, i);
            range.setEnd(node, i + 1);
            const rect = range.getBoundingClientRect();
            if (rect.width && top !== null && rect.top > top + 1) breaks.push(previous);
            if (rect.width) top = rect.top;
            previous = node.data[i];
        }
    }
    return breaks;
}"""


@pytest.mark.parametrize("width", WIDTHS)
def test_the_review_tables_keep_short_words_whole(page, width):
    """Only a title or file-name cell breaks mid-word; «Avatud» never reads «Avat ud»."""
    _open(page, width, shared=True)

    cells = page.locator(".dossierreview th, .dossierreview td:not(.dossierreview__text)")
    assert cells.count() > 0
    for index in range(cells.count()):
        cell = cells.nth(index)
        breaks = cell.evaluate(_BREAKS)
        assert all(char in (" ", "\n", "-") for char in breaks), (cell.inner_text(), breaks)


@pytest.mark.parametrize("width", (375, 1440))
def test_a_card_s_content_starts_at_the_card_s_padding(page, width):
    """E-04: nothing in a card sits flush against its edge; label and text share one inset.

    A card's text is its own note when it has one, else its first body element.
    UI-12 moved «Tehnilised andmed» into the «Allikas» card at the foot of the
    page; its grid sits inside the disclosure's own bordered box, which is
    inset by that box's padding, as every disclosure is (E-04 prepared on
    fix/aud-w4 at 6d0f3290, folded in by UI-12).
    """
    _open(page, width, shared=True)

    insets = page.evaluate(
        """() => [...document.querySelectorAll('section.card')].map(card => {
            const left = card.getBoundingClientRect().left + card.clientLeft;
            const label = card.querySelector(':scope > .sectionlabel');
            const body = card.querySelector(':scope > .cardnote')
                || card.querySelector('.casefile__text, .detailgrid, table, .filelist');
            return {
                label: Math.round(label.getBoundingClientRect().left - left),
                body: Math.round(body.getBoundingClientRect().left - left),
            };
        })"""
    )

    assert insets, "no card on the page"
    for inset in insets:
        assert inset["body"] >= 12, insets
        assert inset["label"] == inset["body"], insets
