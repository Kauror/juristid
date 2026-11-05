"""Agent E (independent QA): adversarial probes of the historical-note parser.

Pure tests, synthetic text shaped like the real corpus. Failing ones document
defects in `app.legacy_import.historical_notes`; nothing there is changed.
"""

from __future__ import annotations

import pytest

from app.legacy_import import historical_notes as hn


def _t(ordinal, value, *, parent=None, outline=0, created="2024-03-01T08:00:00.000Z"):
    return {
        "kind": "TEXT",
        "text": value,
        "depth": 1 if parent is None else 2,
        "parent": parent,
        "ordinal": ordinal,
        "outline": outline,
        "resource_key": "",
        "created": created,
        "modified": created,
    }


def _b(ordinal, **kw):
    return {**_t(ordinal, "", **kw), "kind": "BLANK"}


def _f(ordinal, key, **kw):
    return {**_t(ordinal, "", **kw), "kind": "FILE_ATTACHMENT", "resource_key": key}


def dated(blocks):
    return [n for n in hn.parse_page(blocks).notes if n.date is not None]


# -- what must never become an event date ------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        # A form label whose value is a deadline («feedback due»): the real corpus
        # has «Tagasiside andmise kuupäev: <D. kuu YYYY>» on a page written before it.
        "Tagasiside andmise kuupäev: 15. märts 2024",
        "Vastamise tähtaeg: 15.03.2024",
        "Arvamuse andmise tähtpäev 15.03.2024",
        "Kooskõlastamise lõpp 15.03.2024",
    ],
)
def test_a_deadline_label_with_a_trailing_date_is_not_an_event(line):
    blocks = [_t(0, line, created="2024-02-27T08:00:00.000Z")]
    assert dated(blocks) == [], line


@pytest.mark.parametrize(
    "line",
    [
        "Ministeerium saatis 20.05.2024 kirja:",
        "KLIM ootab vastust 10.08.",
        "Eelnõu kohaselt jõustub seadus 2024. aasta 1. jaanuaril.",
        "Seisuga 26.10 ei ole uusi arenguid.",
        "Õiguskantsleri 13.03.2024 arvamus:",
        "Tähtaeg 23.06.",
        "13. juunil 2024. a vastu võetud määrus nr 5",
    ],
)
def test_prose_dates_never_become_events(line):
    assert dated([_t(0, line)]) == [], line


def test_a_date_in_the_next_actions_section_prose_is_no_row_and_its_dated_line_is_labelled():
    blocks = [
        _t(0, "Järgmised tegevused:"),
        _t(1, "Ootame vastust 10.08.", parent=0),
        _t(2, "01.03.2024 - jälgime edasi", parent=0),
    ]
    notes = hn.parse_page(blocks).notes
    rows = [n for n in notes if n.date is not None]
    assert [(n.kind, n.date.display) for n in rows] == [(hn.NEXT_ACTIONS, "01.03.2024")]
    assert all(n.kind == hn.NEXT_ACTIONS for n in notes)


# -- splitting ------------------------------------------------------------------------


def test_every_text_block_reaches_exactly_one_place():
    blocks = [
        _t(0, "12.03.2024 - esimene"),
        _t(1, "jätk", parent=0),
        _b(2),
        _t(3, "vahepealne lehe tekst"),
        _t(4, "20.03.2024 - teine"),
        _t(5, "teise jätk"),
        _t(6, "Järgmised tegevused:"),
        _t(7, "ootame", parent=6),
        _t(8, "ujuv", outline=-1),
    ]
    page = hn.parse_page(blocks)
    placed = [o for n in page.notes for o in n.ordinals]
    assert len(placed) == len(set(placed))
    texts = [p for n in page.notes for p in n.paragraphs] + list(page.page_text)
    assert sorted(texts) == sorted(
        [
            "esimene",
            "jätk",
            "vahepealne lehe tekst",
            "teine",
            "teise jätk",
            "Järgmised tegevused:",
            "ootame",
            "ujuv",
        ]
    )


def test_a_note_never_crosses_an_outline():
    blocks = [_t(0, "12.03.2024 - esimene"), _t(1, "teise kasti tekst", outline=1)]
    (note,) = dated(blocks)
    assert note.ordinals == (0,)


def test_a_sibling_line_with_its_own_date_is_folded_into_the_previous_note():
    """Design observation (Q2 off), pinned so a change is deliberate: «Kolmas
    lugemine 10.12.2025 - võeti vastu» directly after «Teine lugemine
    03.12.2025» reads as part of the 03.12 note (12 such lines in production)."""
    blocks = [_t(0, "Teine lugemine 03.12.2025"), _t(1, "Kolmas lugemine 10.12.2025 - võeti vastu")]
    blocks[0]["created"] = blocks[1]["created"] = "2025-12-10T08:00:00.000Z"
    (note,) = dated(blocks)
    assert note.date.display == "03.12.2025"
    assert note.ordinals == (0, 1)


# -- files ----------------------------------------------------------------------------


def test_attached_note_label_shift_never_makes_a_file_direct():
    blocks = [
        _t(0, "12.03.2024 - Koda saatis arvamuse"),
        _f(1, "a"),
        _t(2, "Lisa B:", parent=1),  # the label of the NEXT file, nested under the previous one
        _f(3, "b"),
    ]
    page = hn.parse_page(blocks)
    assert all(n.direct_files == () for n in page.notes)
    assert page.page_files == (1, 3)


def test_a_file_nested_under_an_undated_label_inside_a_note_is_not_direct():
    blocks = [
        _t(0, "12.03.2024 - Koda saatis arvamuse"),
        _t(1, "Arvamused:"),
        _f(2, "a", parent=1),
    ]
    page = hn.parse_page(blocks)
    assert all(n.direct_files == () for n in page.notes)


def test_only_a_file_under_the_notes_first_block_is_direct():
    blocks = [_t(0, "12.03.2024 - Koda saatis arvamuse"), _f(1, "a", parent=0), _f(2, "b")]
    (note,) = dated(blocks)
    assert note.direct_files == (1,)
    assert hn.parse_page(blocks).page_files == (2,)


# -- robustness -----------------------------------------------------------------------


def test_a_self_parented_or_cyclic_block_neither_crashes_nor_is_silently_lost():
    blocks = [
        _t(0, "12.03.2024 - esimene"),
        {**_t(1, "ise enda vanem"), "parent": 1},
        {**_t(2, "tsükkel A"), "parent": 3},
        {**_t(3, "tsükkel B"), "parent": 2},
    ]
    page = hn.parse_page(blocks)
    texts = [p for n in page.notes for p in n.paragraphs] + list(page.page_text)
    assert "ise enda vanem" in texts and "tsükkel A" in texts and "tsükkel B" in texts


def test_a_deeply_nested_page_does_not_overflow_the_stack():
    blocks = [_t(0, "12.03.2024 - algus")]
    for i in range(1, 1500):
        blocks.append({**_t(i, f"tase {i}"), "parent": i - 1})
    page = hn.parse_page(blocks)
    assert len([p for n in page.notes for p in n.paragraphs]) == 1500


@pytest.mark.parametrize(
    ("line", "display", "precision"),
    [
        ("1.2.2024 - lühike", "01.02.2024", "DAY"),
        ("01.02.24 - kahekohaline aasta", "01.02.2024", "DAY"),
        ("02.2024 - kuu", "02.2024", "MONTH"),
        ("Märts 2024 - kuu nimega", "03.2024", "MONTH"),
        ("2024 - aasta", "2024", "YEAR"),
        ("29.02.2023 - pole kuupäev", None, None),
        ("31.04.2024 - pole kuupäev", None, None),
        ("12.03.2024KLIM", None, None),
    ],
)
def test_leading_date_precision(line, display, precision):
    found = dated([_t(0, line, created="2024-03-20T08:00:00.000Z")])
    if display is None:
        assert found == []
    else:
        (note,) = found
        assert (note.date.display, note.date.precision) == (display, precision)


def test_headline_is_at_most_65_and_cut_at_a_word():
    text = "sõna " * 40
    head = hn.headline(text)
    assert len(head) <= hn.HEADLINE_LIMIT and head.endswith("…")
    assert not head[:-1].endswith(" ")
    assert text.startswith(head[:-1])
