"""UI-13: an opened OneNote note row of `Tegevused` says each thing once.

The pure half holds the parser's paragraphs (a deliberately repeated sentence
stays, the text is decoded once and escaped once) and the template's rule:
the full first paragraph takes the preview's place on the line, the rest
follows once, a note that fits its preview is shown once, no generated
«Kuupäev» or «Allikas OneNote: …» row and no capture or block reference, and
«OneNote'i koopia» with its copy icon right after it. The database half reads
the synthetic world (`tests/test_ui12_world.py`): the shared-page indicator,
and every row's anchor landing on an element of the source page.

Every page, note and Teema is invented.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from app.legacy_import.historical_notes import HistoricalNoteRow, parse_page
from app.legacy_import.source_pages import MatterSourcePage
from app.matters.timeline import HISTORICAL_NOTE_ITEM, matter_timeline
from tests.test_tl_c_historical_rows import Node, a_note, note_item, render, the_row
from tests.test_ui12_world import (
    DOSSIER_TITLE,
    NOTE_FIRST,
    NOTE_SECOND,
    NOTE_SECOND_SHOWN,
    SHORT_NOTE,
    SINGLE_TITLE,
    SUMMARY,
    build_world,
    single_blocks,
)

GUID = "00000072-0000-4000-8000-00000000AB72"


def _notes():
    return {note.date.display: note for note in parse_page(single_blocks(GUID)).notes if note.date}


# -- paragraphs and escaping -------------------------------------------------------------


def test_a_deliberately_repeated_sentence_is_two_paragraphs():
    note = _notes()["08.2026"]
    assert note.paragraphs == (SUMMARY, SUMMARY)


def test_each_block_is_one_paragraph_and_the_leading_date_only_is_removed():
    note = _notes()["27.07.2026"]
    assert note.paragraphs == (NOTE_FIRST, NOTE_SECOND_SHOWN)
    assert NOTE_SECOND != NOTE_SECOND_SHOWN  # stored encoded, decoded once


def test_a_note_is_decoded_once_and_escaped_once():
    blocks = [
        {
            "ordinal": 0,
            "kind": "TEXT",
            "text": "12.03.2024 - Koda &quot;toetab&quot; &amp;quot; &lt;b&gt;",
            "outline": 0,
            "parent": None,
        }
    ]
    (note,) = parse_page(blocks).notes
    assert note.paragraphs == ('Koda "toetab" &quot; <b>',)
    html, _root = render(note_item(a_note(paragraphs=note.paragraphs, headline="Koda")))
    assert "Koda &quot;toetab&quot; &amp;quot; &lt;b&gt;" in html
    assert "<b>" not in html and "&amp;amp;" not in html


# -- the opened row ----------------------------------------------------------------------


def _row_of(note: HistoricalNoteRow | object, key: str = "onenote-note:1") -> Node:
    _html, root = render(note_item(note, key=key))  # type: ignore[arg-type]
    return the_row(root)


def test_the_first_paragraph_replaces_the_preview_and_the_rest_follows_once():
    note = a_note(
        headline="Näidisamet saatis kooskõlastusringile eelnõu, millega…",
        paragraphs=(NOTE_FIRST, SUMMARY, SUMMARY),
    )
    row = _row_of(note)
    details = row.one("details")
    assert "uxtl__hist--inplace" in details.classes()
    summary = details.one("summary")
    assert summary.one(cls="uxtl__histfirst").words() == NOTE_FIRST
    panel = row.one(cls="uxtl__histpanel")
    assert [p.words() for p in panel.all("p", "uxtl__histpara")] == [SUMMARY, SUMMARY]
    # Nothing subtracted, nothing repeated: the first paragraph is not in the panel.
    assert NOTE_FIRST not in panel.words()


def test_a_note_that_fits_its_preview_is_shown_once():
    row = _row_of(a_note(headline=SHORT_NOTE, paragraphs=(SHORT_NOTE,)))
    assert "uxtl__hist--inplace" not in row.one("details").classes()
    assert row.all(cls="uxtl__histfirst") == []
    assert row.all(cls="uxtl__histtext") == []
    assert row.words().count(SHORT_NOTE) == 1
    # Still its source access and its permalink.
    assert [a.words() for a in row.all("a", "uxtl__link")] == ["OneNote'i koopia"]


def test_a_note_with_no_text_keeps_its_files_headline():
    row = _row_of(a_note(headline="EELNÕU.pdf ja 1 muu fail", paragraphs=()))
    assert "uxtl__hist--inplace" not in row.one("details").classes()
    assert row.one(cls="uxtl__histpreview").words() == "EELNÕU.pdf ja 1 muu fail"


def test_no_technical_string_reaches_the_normal_flow():
    note = a_note(
        source_ref="onenote-desktop-20261104-full · plokk 358",
        section="Näidissektsioon",
        anchor="plokk-358",
    )
    words = _row_of(note).words()
    for technical in ("onenote-desktop", "plokk 358", "plokid", "Allikas", "Kuupäev"):
        assert technical not in words, technical
    assert "Näidissektsioon" not in words  # the generated «OneNote: section → page» row


def test_the_copy_icon_follows_its_link_copies_it_and_names_it():
    row = _row_of(a_note(anchor="plokk-7", page_title="Näidisseaduse muutmine 2026"))
    (pair,) = row.all(cls="uxtl__histcopy")
    elements = [child for child in pair.children if isinstance(child, Node)]
    assert [element.tag for element in elements] == ["a", "button"]
    link, button = elements
    assert link.words() == "OneNote'i koopia"
    assert link.attrs["href"].endswith("/#plokk-7")
    assert button.attrs["data-copy-href"] == link.attrs["href"]
    assert button.attrs["aria-label"] == (
        "Kopeeri OneNote'i koopia link: Näidisseaduse muutmine 2026"
    )
    # The entry's own permalink is still there, and is a different address.
    (permalink,) = row.all(cls="uxtl__permalink")
    entry = permalink.one("button")
    assert "?kirje=" in entry.attrs["data-copy-href"]
    assert entry.attrs["data-copy-href"] != button.attrs["data-copy-href"]


def test_without_a_stable_anchor_the_copy_is_the_page_itself():
    row = _row_of(a_note(anchor=""))
    link = row.one(cls="uxtl__histcopy").one("a")
    assert "#" not in link.attrs["href"]


def test_a_shared_page_note_says_so_compactly_and_only_then():
    assert _row_of(a_note(shared_page=True)).one(cls="uxtl__histshared").words() == "ühine leht"
    assert _row_of(a_note(shared_page=False)).all(cls="uxtl__histshared") == []


# -- against the synthetic world --------------------------------------------------------


@pytest.fixture
def world(tmp_path, settings, evidence_root):
    settings.ONENOTE_CAPTURE_IMPORT_ENABLED = True
    settings.REAL_DATA_ALLOWED = True
    corpus = tmp_path / "corpus"
    settings.HISTORICAL_SOURCE_ROOT = str(corpus)
    return build_world(corpus)


@pytest.mark.django_db
def test_every_row_lands_on_its_block_of_the_source_page(world, client, specialist):
    second = world["teemad"][1]
    items, _ = matter_timeline(matter=second, user=specialist, limit=100)
    notes = [item.historical_note for item in items if item.item_type == HISTORICAL_NOTE_ITEM]
    assert {note.date_display for note in notes} == {
        "12.03.2024",
        "05.2024",
        "18.09.2024",
        "27.07.2026",
        "10.08.2026",
        "08.2026",
    }
    by_page = {
        DOSSIER_TITLE: MatterSourcePage.objects.get(
            matter=second, source_page__title=DOSSIER_TITLE
        ),
        SINGLE_TITLE: MatterSourcePage.objects.get(matter=second, source_page__title=SINGLE_TITLE),
    }
    client.force_login(specialist)
    pages = {
        title: client.get(
            reverse("legacy_import:source_page", kwargs={"pk": link.pk})
        ).content.decode()
        for title, link in by_page.items()
    }
    for note in notes:
        assert note.anchor, note.date_display
        assert note.source_url == f"{note.page_url}#{note.anchor}"
        assert f'id="{note.anchor}"' in pages[note.page_title], note.date_display
        # Shared only where the page is linked to several live Teemad.
        assert note.shared_page is (note.page_title == DOSSIER_TITLE)


@pytest.mark.django_db
def test_the_teema_page_reads_each_note_once_with_no_technical_reference(world, client, specialist):
    second = world["teemad"][1]
    client.force_login(specialist)
    html = client.get(reverse("matters:matter_detail", kwargs={"pk": second.pk})).content.decode()
    chronology = html.split('id="ajalugu-loend"', 1)[1]
    assert "OneNote'i koopia" in chronology
    assert "Ava OneNote'i leht Juristides" not in chronology
    assert not re.search(r"synthetic-capture · plok", chronology)
    # A short note that is its own preview: once.
    assert chronology.count(SHORT_NOTE) == 1
    # The repeated sentence: in the preview, as the first paragraph that
    # replaces it, and as the source's own repeat below — never deduplicated.
    assert chronology.count(SUMMARY) == 3
