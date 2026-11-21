"""«Failid märkuse juures»: a file in a note's own run of lines opens from the timeline.

Owner, 2026-11-21: the letter a OneNote note talks about («Raul kirjutas …»)
sat beside it on the page and was reachable only through «OneNote'i koopia».
D9 still holds — such a file is page-level, never tags the note and keeps its
own document row — but the opened note now lists it with its open/download
link.
"""

from __future__ import annotations

import pytest

from tests.test_tl_b_timeline_notes import (
    _utc,
    blank,
    documents_of,
    file_block,
    make_page,
    notes_of,
    rows_of,
    text,
)
from tests.test_tl_c_historical_rows import a_file, a_note, note_item, render, the_row

BESIDE_BLOCKS = [
    text(0, "12.03.2024 - Raul kirjutas, et komisjon jõuab muudatusteni aprillis"),
    file_block(1, "kiri"),
    blank(2),
    file_block(3, "muu"),
]
BESIDE_FILES = [
    ("kiri", "Vs_Kas midagi on kuulda.msg", _utc(2024, 3, 12, 10, 0)),
    ("muu", "Muu.pdf", _utc(2024, 3, 12, 10, 5)),
]


@pytest.mark.django_db
def test_a_file_in_the_notes_run_is_listed_beside_it_and_keeps_its_own_row(
    normal_matter, specialist
):
    _page, made = make_page([normal_matter], BESIDE_BLOCKS, BESIDE_FILES)
    _link, versions = made[normal_matter.pk]
    rows = rows_of(normal_matter, specialist)
    (note,) = notes_of(rows)
    row = note.historical_note
    assert row.direct_files == ()
    assert [file.original_filename for file in row.beside_files] == ["Vs_Kas midagi on kuulda.msg"]
    assert str(versions["kiri"].pk) in row.beside_files[0].open_url
    # Past the empty line the note has ended: that file is the page's alone.
    assert row.page_files_count == 2
    (document_row,) = documents_of(rows)
    assert {file.original_filename for file in document_row.historical_files} == {
        "Vs_Kas midagi on kuulda.msg",
        "Muu.pdf",
    }


def test_the_opened_note_lists_beside_files_with_their_open_link_and_no_tag():
    _html, root = render(note_item(a_note(beside_files=(a_file(7),))))
    row = the_row(root)
    assert row.one("summary").all(cls="uxtl__histtag") == []
    words = row.words()
    assert "Failid märkuse juures" in words
    assert "Ava või laadi alla" in words
    assert 'href="/dokumendid/7/ava/"' in _html
