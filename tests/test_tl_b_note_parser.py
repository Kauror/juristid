"""The historical-note parser: which OneNote blocks are a dated note, and what it says.

Pure tests of `app.legacy_import.historical_notes`: no database.

Most cases are agent A's synthetic vectors (`test_tl_b_vectors.json`, copied
verbatim from the round's worklog, invented text in the corpus's real shapes):
45 opening lines, 20 trailing-date headings and 22 pages, each with the exact
segments, page files and page text the specification (R0–R12) requires. The
tests after them pin what the vectors do not spell out.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.legacy_import import historical_notes as hn
from app.legacy_import.historical_notes import (
    DATE_AFTER_WRITING,
    HEADLINE_LIMIT,
    NEXT_ACTIONS,
    NOTE,
    HistoricalFile,
    PageFile,
    PageResource,
    block_keys_of,
    files_headline,
    headline,
    leading_date,
    normalise,
    parse_page,
    source_ref,
    structural_comment,
    trailing_date,
)

VECTORS = json.loads(Path(__file__).with_name("test_tl_b_vectors.json").read_text("utf-8"))
SEGMENT_KEYS = ("kind", "date_display", "precision", "paragraphs", "block_ordinals", "direct_files")


# -- agent A's vectors --------------------------------------------------------------


@pytest.mark.parametrize("vector", VECTORS["line_vectors"], ids=lambda v: v["text"][:40])
def test_line_vectors(vector):
    found = leading_date(vector["text"])
    if vector["date_display"] is None:
        assert found is None
        return
    assert found is not None
    assert found.form == vector["form"]
    assert found.display == vector["date_display"]
    assert found.precision == vector["precision"]
    assert found.reason == vector["reason"]
    if vector["rest"] is not None:
        assert found.rest == vector["rest"]


@pytest.mark.parametrize("vector", VECTORS["trailing_vectors"], ids=lambda v: v["text"][:40])
def test_trailing_vectors(vector):
    found = trailing_date(vector["text"])
    assert (found.display if found else None) == vector["date_display"]


@pytest.mark.parametrize("vector", VECTORS["page_vectors"], ids=lambda v: v["id"])
def test_page_vectors(vector):
    parsed = parse_page(vector["blocks"], page_modified=vector.get("page_modified"))
    got = [
        {
            "kind": note.kind,
            "date_display": note.date.display if note.date else "",
            "precision": note.date.precision if note.date else None,
            "paragraphs": list(note.paragraphs),
            "block_ordinals": list(note.ordinals),
            "direct_files": sorted(note.direct_files),
        }
        for note in parsed.notes
    ]
    expected = [{key: segment[key] for key in SEGMENT_KEYS} for segment in vector["expect"]]
    assert got == expected
    for note, segment in zip(parsed.notes, vector["expect"], strict=True):
        for flag in segment.get("flags_include", []):
            assert flag in note.flags
    assert list(parsed.page_files) == sorted(vector.get("page_files", []))
    if "page_text" in vector:
        assert list(parsed.page_text) == vector["page_text"]


# -- beyond the vectors ---------------------------------------------------------------


def block(ordinal, text="", *, kind="TEXT", parent=None, depth=1, outline=0, key="", when=None):
    when = when or "2026-10-30T09:00:00.000Z"
    return {
        "ordinal": ordinal,
        "kind": kind,
        "text": text,
        "depth": depth,
        "parent": parent,
        "outline": outline,
        "created": when,
        "modified": when,
        "object_id": f"{{o}}{{{ordinal}}}",
        "source_kind": kind,
        "resource_key": key,
    }


def file(ordinal, key, **kwargs):
    return block(ordinal, kind="FILE_ATTACHMENT", key=key, **kwargs)


def test_normalisation_decodes_entities_and_collapses_whitespace():
    assert normalise("27.07.2026&nbsp;-&nbsp;Koda​  &quot;vastas&quot;\n") == (
        '27.07.2026 - Koda "vastas"'
    )


def test_an_attached_note_next_to_a_dated_note_does_not_make_the_file_direct():
    # The lead's binding finding: text indented under file N is usually the
    # label of file N+1. A dated text nested under a file ties nothing to it.
    parsed = parse_page(
        [
            file(0, "kiri"),
            block(1, "12.03.2024 - Ministeeriumi vastus", parent=0, depth=2),
            file(2, "jargmine"),
        ]
    )
    (note,) = parsed.notes
    assert note.date.display == "12.03.2024"
    assert note.direct_files == () and note.file_keys == ()
    assert list(parsed.page_files) == [0, 2]


def test_only_a_file_nested_under_the_notes_own_first_block_is_direct():
    parsed = parse_page(
        [
            block(0, "12.03.2024 - Saadeti arvamus"),
            block(1, "Lisad:", parent=0, depth=2),
            file(2, "sees", parent=1, depth=3),
            file(3, "korval"),
            block(4, "Arvamused:"),
            file(5, "sildi-all", parent=4, depth=2),
        ]
    )
    (note,) = parsed.notes
    assert note.file_keys == ("sees",)
    assert note.ordinals == (0, 1, 2, 3, 4, 5)
    assert list(parsed.page_files) == [3, 5]


def test_a_date_written_far_ahead_of_its_block_is_undated():
    parsed = parse_page(
        [block(0, "20.12.2026 - jõustub", when="2026-08-10T07:00:00.000Z"), block(1, "")]
    )
    (note,) = parsed.notes
    assert note.date is None
    assert DATE_AFTER_WRITING in note.flags
    # Undated: the text stays whole, the date included.
    assert note.paragraphs == ("20.12.2026 - jõustub",)


def test_a_date_within_the_tolerance_stays_dated():
    parsed = parse_page([block(0, "20.09.2026 - istung", when="2026-08-10T07:00:00.000Z")])
    assert parsed.notes[0].date.display == "20.09.2026"


def test_the_trailing_date_rule_is_one_switch(monkeypatch):
    blocks = [block(0, "Koja arvamus 12.03.2024:"), block(1, "tekst", parent=0, depth=2)]
    (note,) = parse_page(blocks).notes
    assert note.form == "TRAILING"
    assert note.paragraphs == ("Koja arvamus 12.03.2024:", "tekst")
    monkeypatch.setattr(hn, "TRAILING_DATES", False)
    assert parse_page(blocks).notes == ()


def test_a_dated_line_under_next_actions_is_a_dated_next_actions_note():
    parsed = parse_page(
        [
            block(0, "Mis saab edasi?"),
            block(1, "27.10.2026 seisuga ei ole eelnõu edasi liikunud", parent=0, depth=2),
        ]
    )
    heading, dated = parsed.notes
    assert (heading.kind, heading.date) == (NEXT_ACTIONS, None)
    assert dated.kind == NEXT_ACTIONS and dated.date.display == "27.10.2026"
    assert dated.paragraphs == ("seisuga ei ole eelnõu edasi liikunud",)


def test_visible_addresses_are_the_notes_links_and_onenote_links_never():
    parsed = parse_page(
        [
            block(
                0, "12.03.2024 - Vaata https://eelnoud.example.invalid/a, ka www.example.invalid."
            ),
            block(1, "onenote:#leht"),
        ]
    )
    assert parsed.notes[0].links == (
        ("https://eelnoud.example.invalid/a", "https://eelnoud.example.invalid/a"),
        ("www.example.invalid", "https://www.example.invalid"),
    )


def test_the_earlier_captures_links_are_read_by_block():
    legacy = [
        {"ordinal": 0, "kind": "TEXT", "text": "12.03.2024 - Vaata eelnõu", "depth": 0},
        {"ordinal": 1, "kind": "TEXT", "text": "Muu", "depth": 0},
    ]
    parsed = parse_page(
        legacy,
        [
            {"href": "https://x.example.invalid/", "text": "Eelnõu", "blockOrdinal": 0},
            {"href": "https://y.example.invalid/", "text": "Muu", "blockOrdinal": 1},
            {"href": "onenote:#x", "text": "Leht", "blockOrdinal": 0},
        ],
    )
    assert parsed.shape == "LEGACY"
    assert parsed.notes[0].links == (("Eelnõu", "https://x.example.invalid/"),)


# -- headline, provenance, files ------------------------------------------------------


def test_a_short_note_is_its_own_headline():
    assert headline("kojal ei olnud  märkusi eelnõu kohta") == (
        "kojal ei olnud märkusi eelnõu kohta"
    )


def test_a_long_note_is_cut_at_a_word_with_an_ellipsis():
    value = (
        "Ministeerium saatis kooskõlastusringile eelnõu, millega täpsustatakse "
        "näidismääruses kasutatavaid mõisteid ja kehtestatakse uued nõuded"
    )
    cut = headline(value)
    assert len(cut) <= HEADLINE_LIMIT
    assert cut.endswith("…")
    assert value.startswith(cut[:-1])
    assert value[len(cut) - 1] == " "


def test_a_cut_never_ends_on_a_comma():
    value = "Ministeerium saatis kooskõlastusringile eelnõu, " + "x" * 80
    assert headline(value, limit=50) == "Ministeerium saatis kooskõlastusringile eelnõu…"


def test_the_source_ref_names_the_capture_and_the_block_range():
    assert source_ref("capture-x", [7]) == "capture-x · plokk 7"
    assert source_ref("capture-x", [3, 0, 1, 2]) == "capture-x · plokid 0–3"


def test_the_structural_comment_is_read_verbatim_and_nothing_else():
    note = "\n".join(
        [
            "OneNote: Sektsioon → Leht",
            "Lisatud OneNote'i: 29.07.2026 09:03",
            "Pealkiri OneNote'is: 12.03.2024 Saadeti arvamus",
            "Märkus OneNote'is: esimene rida",
            "teine rida",
            "Allikas: capture-x, plokk 4",
        ]
    )
    assert structural_comment(note) == "12.03.2024 Saadeti arvamus\nesimene rida\nteine rida"
    assert structural_comment("OneNote: x\nAllikas: y") == ""


def _file(name):
    return HistoricalFile(
        title=name,
        original_filename=name,
        document_url="/d/",
        open_url="/o/",
        added_display="29.07.2026",
        comment="",
        predates_page=False,
    )


def test_the_files_headline_names_the_first_and_counts_the_rest():
    assert files_headline([]) == ""
    assert files_headline([_file("EELNÕU.pdf")]) == "EELNÕU.pdf"
    assert files_headline([_file("EELNÕU.pdf"), _file("b.pdf")]) == "EELNÕU.pdf ja 1 muu fail"
    assert files_headline([_file("EELNÕU.pdf")] + [_file("x")] * 3) == (
        "EELNÕU.pdf ja 3 muud faili"
    )


def test_a_later_captures_file_maps_to_a_block_only_by_unique_name_and_time():
    blocks = [
        {"ordinal": 0, "kind": "TEXT", "text": "12.03.2024 - x", "depth": 0},
        {"ordinal": 1, "kind": "FILE_ATTACHMENT", "resource_key": "r001", "depth": 1},
        {"ordinal": 2, "kind": "FILE_ATTACHMENT", "resource_key": "r002", "depth": 1},
        {"ordinal": 3, "kind": "FILE_ATTACHMENT", "resource_key": "r003", "depth": 1},
    ]
    resources = [
        PageResource("r001", "Kiri.pdf", "T1"),
        PageResource("r002", "Lisa.pdf", "T2"),
        PageResource("r003", "Lisa.pdf", "T2"),
        PageResource("{GUID-1}", "Kiri.pdf", "T1"),
    ]
    files = [
        PageFile("{GUID-1}", _file("Kiri.pdf"), "v1", filename="Kiri.pdf", added_at="T1"),
        PageFile("{GUID-2}", _file("Lisa.pdf"), "v2", filename="Lisa.pdf", added_at="T2"),
        PageFile("{GUID-3}", _file("Muu.pdf"), "v3", filename="Muu.pdf", added_at=None),
    ]
    mapped = block_keys_of(files, blocks, resources)
    # Unique name and time: mapped. Two candidates, or no time: page-level.
    assert {key: item.version_id for key, item in mapped.items()} == {"r001": "v1"}
    # The earlier shape: depth gives the nesting (R6), so all three are direct.
    (note,) = parse_page(blocks).notes
    assert note.kind == NOTE and note.file_keys == ("r001", "r002", "r003")
