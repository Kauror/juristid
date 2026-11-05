"""The historical OneNote rows of `Teema käik`, rendered from the contract alone.

`app.legacy_import.historical_notes` (agent B) projects a OneNote page's notes
and files into `TimelineItem.historical_note` / `TimelineItem.historical_files`;
`templates/matters/partials/timeline_items.html` draws them. These tests render
that fragment with hand-built rows, no database and no browser, so the template
is held to the interface — collapsed line, tag rule, precision, escaping,
links — independently of how the rows were found.

The row doubles below carry exactly the contract's fields; the last test holds
them to the real dataclasses once those exist. Every string is invented.
"""

from __future__ import annotations

import dataclasses
import importlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from types import SimpleNamespace
from typing import Any

import pytest
from django.template.loader import render_to_string

from app.matters.timeline import HISTORICAL_IMPORT_ITEM, ChronologyMilestone, TimelineItem

# ---------------------------------------------------------------------------
# The contract (COMMON.md, «Interface contract»), as local doubles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoricalFile:
    title: str
    original_filename: str
    document_url: str
    open_url: str
    added_display: str
    comment: str
    predates_page: bool


@dataclass(frozen=True)
class HistoricalNoteRow:
    date_display: str
    precision: str | None
    headline: str
    paragraphs: tuple[str, ...]
    kind: str
    links: tuple[tuple[str, str], ...]
    direct_files: tuple[HistoricalFile, ...]
    page_files_count: int
    page_url: str
    page_title: str
    section: str
    source_ref: str


@dataclass
class Item:
    """The parts of `TimelineItem` the historical partials read."""

    row_key: str
    item_type: str
    occurred_at: datetime
    historical_note: HistoricalNoteRow | None = None
    historical_files: tuple[HistoricalFile, ...] = ()
    milestone: ChronologyMilestone | None = None
    imported_documents: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


PAGE_URL = "/ajalugu/0b5d8f0e-1111-4c2b-9d0e-000000000001/"


def a_file(number: int = 1, **overrides: Any) -> HistoricalFile:
    values: dict[str, Any] = {
        "title": f"Näidiseelnõu lisa {number}",
        "original_filename": f"NAIDIS_LISA_{number}.pdf",
        "document_url": f"/dokumendid/{number}/",
        "open_url": f"/dokumendid/{number}/ava/",
        "added_display": "29.07.2026",
        "comment": "",
        "predates_page": False,
    }
    values.update(overrides)
    return HistoricalFile(**values)


def a_note(**overrides: Any) -> HistoricalNoteRow:
    values: dict[str, Any] = {
        "date_display": "27.07.2026",
        "precision": "DAY",
        "headline": "Näidisamet saatis kooskõlastusringile eelnõu, millega…",
        "paragraphs": (
            "Näidisamet saatis kooskõlastusringile eelnõu, millega muudetakse näidisseadust.",
            "Teine lõik.\nTeine rida samas lõigus.",
        ),
        "kind": "NOTE",
        "links": (),
        "direct_files": (),
        "page_files_count": 0,
        "page_url": PAGE_URL,
        "page_title": "Näidisseaduse muutmine 2026",
        "section": "Näidissektsioon",
        "source_ref": "capture näidis-01 · plokid 0–3",
    }
    values.update(overrides)
    return HistoricalNoteRow(**values)


def note_item(note: HistoricalNoteRow, key: str = "onenote-note:1") -> Item:
    return Item(
        row_key=key,
        item_type="HISTORICAL_NOTE",
        occurred_at=datetime(2026, 7, 27, 20, 59, tzinfo=UTC),
        historical_note=note,
    )


def files_item(
    files: tuple[HistoricalFile, ...], *, what: str, display: str = "29.07.2026"
) -> Item:
    return Item(
        row_key="onenote:grupp-1",
        item_type="HISTORICAL_DOCUMENTS_IMPORTED",
        # 29.07.2026 08:15 in Tallinn.
        occurred_at=datetime(2026, 7, 29, 5, 15, tzinfo=UTC),
        historical_files=files,
        milestone=ChronologyMilestone(
            what=what,
            display_date=display,
            sub=f"{len(files)} faili · OneNote: Näidissektsioon → Näidisseaduse muutmine 2026",
        ),
        imported_documents=len(files),
    )


# ---------------------------------------------------------------------------
# A small DOM, so the assertions are about structure and not about whitespace
# ---------------------------------------------------------------------------

VOID = {"br", "img", "input", "meta", "link", "hr", "wbr", "source"}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    children: list[Node | str] = field(default_factory=list)

    def text(self) -> str:
        out = []
        for child in self.children:
            out.append(child if isinstance(child, str) else child.text())
        return "".join(out)

    def words(self) -> str:
        return " ".join(self.text().split())

    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def all(self, tag: str | None = None, cls: str | None = None) -> list[Node]:
        found = []
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and (cls is None or cls in child.classes()):
                    found.append(child)
                found.extend(child.all(tag, cls))
        return found

    def one(self, tag: str | None = None, cls: str | None = None) -> Node:
        (node,) = self.all(tag, cls)
        return node


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {name: value or "" for name, value in attrs})
        self.stack[-1].children.append(node)
        if tag == "br":
            node.children.append("\n")
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        assert self.stack[-1].tag == tag, f"</{tag}> closes <{self.stack[-1].tag}>"
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def render(*items: Item) -> tuple[str, Node]:
    html = render_to_string(
        "matters/partials/timeline_items.html",
        {
            "timeline_items": list(items),
            "matter": SimpleNamespace(pk=uuid.uuid4(), is_open=True),
            "timeline_saved_keys": set(),
            "colleagues": (),
            "timeline_has_more": False,
        },
    )
    tree = _Tree()
    tree.feed(html)
    tree.close()
    assert tree.stack == [tree.root], "an element was left open"
    return html, tree.root


def the_row(root: Node) -> Node:
    (article,) = root.all("article")
    return article


# ---------------------------------------------------------------------------
# The collapsed line
# ---------------------------------------------------------------------------


def test_a_note_is_one_disclosure_whose_line_is_date_tag_headline():
    files = (a_file(1), a_file(2), a_file(3), a_file(4))
    _html, root = render(note_item(a_note(direct_files=files)))
    row = the_row(root)
    assert {"uxtl__item", "uxtl__item--secondary", "uxtl__item--historical"} <= row.classes()
    assert row.attrs["data-kaik-rida"] == "onenote-note:1"
    details = row.one("details", "uxtl__hist")
    assert "open" not in details.attrs, "every row renders closed"
    summary = details.one("summary")
    assert summary.attrs["id"] == "kaik-onenote-note:1-rida"
    assert summary.words() == (
        "27.07.2026 4 dokumenti Näidisamet saatis kooskõlastusringile eelnõu, millega…"
    )
    # Order on the line is date, tag, headline.
    kinds = [child.tag for child in summary.children if isinstance(child, Node)]
    assert kinds[:3] == ["time", "span", "span"]
    # No button-and-script accordion on this row.
    assert not row.all(cls="uxtl__toggle")


def test_one_direct_file_tags_the_note_dokument():
    _html, root = render(note_item(a_note(direct_files=(a_file(1),))))
    summary = the_row(root).one("summary")
    assert [tag.words() for tag in summary.all(cls="uxtl__histtag")] == ["Dokument"]


def test_a_note_without_direct_files_has_no_tag_even_with_page_files():
    _html, root = render(note_item(a_note(direct_files=(), page_files_count=4)))
    summary = the_row(root).one("summary")
    assert summary.all(cls="uxtl__histtag") == []
    assert "dokument" not in summary.words().lower()


@pytest.mark.parametrize(
    ("display", "precision", "iso", "said"),
    [
        ("27.07.2026", "DAY", "2026-07-27", "27.07.2026"),
        ("07.2026", "MONTH", "2026-07", "07.2026 (kuu täpsusega)"),
        ("2026", "YEAR", "2026", "2026 (aasta täpsusega)"),
    ],
)
def test_the_date_keeps_its_precision_in_text_and_in_datetime(display, precision, iso, said):
    _html, root = render(note_item(a_note(date_display=display, precision=precision)))
    row = the_row(root)
    time = row.one("summary").one("time")
    assert time.attrs["datetime"] == iso
    assert time.words() == display
    dated = [line.words() for line in row.all(cls="uxtl__mssub")]
    assert f"Kuupäev {said}" in dated


def test_an_undated_note_shows_no_date_and_invents_none():
    _html, root = render(note_item(a_note(date_display="", precision=None)))
    row = the_row(root)
    summary = row.one("summary")
    assert summary.all("time") == []
    assert summary.all(cls="uxtl__msdate") == []
    assert not re.search(r"\d{1,2}\.\d{1,2}\.\d{4}", summary.words())
    assert summary.words().startswith("Näidisamet saatis")
    assert "Kuupäev OneNote'i märkusel kuupäeva ei ole" in [
        line.words() for line in row.all(cls="uxtl__mssub")
    ]


# ---------------------------------------------------------------------------
# The opened row
# ---------------------------------------------------------------------------


def test_the_opened_note_holds_the_verbatim_paragraphs_with_their_line_breaks():
    html, root = render(note_item(a_note()))
    paragraphs = the_row(root).all("p", "uxtl__histpara")
    assert [p.words() for p in paragraphs] == [
        "Näidisamet saatis kooskõlastusringile eelnõu, millega muudetakse näidisseadust.",
        "Teine lõik. Teine rida samas lõigus.",
    ]
    assert "Teine lõik.<br>Teine rida samas lõigus." in html


def test_the_source_page_section_link_and_ref_are_all_there():
    _html, root = render(note_item(a_note(page_files_count=3)))
    row = the_row(root)
    assert "Allikas OneNote: Näidissektsioon → Näidisseaduse muutmine 2026" in [
        line.words() for line in row.all(cls="uxtl__mssub")
    ]
    links = {a.words(): a.attrs["href"] for a in row.all("a", "uxtl__link")}
    assert links["Ava OneNote'i leht Juristides"] == PAGE_URL
    assert links["Sama OneNote'i lehe failid (3)"] == PAGE_URL + "#sisu-heading"
    assert row.one(cls="uxtl__histref").words() == "capture näidis-01 · plokid 0–3"


def test_no_page_files_means_no_page_files_link():
    _html, root = render(note_item(a_note(page_files_count=0)))
    assert "Sama OneNote'i lehe failid" not in the_row(root).words()


def test_direct_files_each_carry_title_filename_day_comment_and_bytes():
    files = (
        a_file(1, comment="Märkus OneNote'is: näidiskiri ministeeriumile"),
        a_file(2, predates_page=True, added_display="lisamise kuupäev teadmata"),
    )
    _html, root = render(note_item(a_note(direct_files=files)))
    row = the_row(root)
    entries = row.all("li", "uxtl__histfile")
    assert len(entries) == 2
    first, second = entries
    assert first.one("a", "uxtl__file").attrs["href"] == "/dokumendid/1/"
    assert first.one("a", "uxtl__file").words() == "Näidiseelnõu lisa 1"
    assert "NAIDIS_LISA_1.pdf · lisatud OneNote'i 29.07.2026" in first.words()
    assert first.one(cls="uxtl__histcomment").words() == (
        "Märkus OneNote'is: näidiskiri ministeeriumile"
    )
    assert first.one("a", "uxtl__histopen").attrs["href"] == "/dokumendid/1/ava/"
    assert first.one("a", "uxtl__histopen").words() == "Ava või laadi alla — NAIDIS_LISA_1.pdf"
    # An unknown day is said as the source says it, with no «lisatud OneNote'i».
    assert "lisamise kuupäev teadmata · lisatud enne praeguse lehe loomist" in second.words()
    assert "lisatud OneNote'i lisamise" not in second.words()
    assert "lisatud enne praeguse lehe loomist" not in first.words()


def test_a_filename_equal_to_the_title_is_not_said_twice():
    file = a_file(1, title="EELNOU.pdf", original_filename="EELNOU.pdf")
    _html, root = render(note_item(a_note(direct_files=(file,))))
    assert the_row(root).one("li").words().count("EELNOU.pdf") == 2  # link + hidden name


def test_a_next_actions_note_is_labelled_history_and_offers_no_control():
    note = a_note(
        kind="NEXT_ACTIONS",
        headline="Järgmised tegevused: küsida ministeeriumilt…",
        paragraphs=("Järgmised tegevused:", "küsida ministeeriumilt uut versiooni"),
    )
    _html, root = render(note_item(note))
    row = the_row(root)
    summary = row.one("summary")
    assert "Ajalooline: Järgmised tegevused" in summary.words()
    label = row.one(cls="uxtl__histtext").one(cls="uxtl__msnotelabel")
    assert label.words() == "Ajalooline: Järgmised tegevused"
    # History, not a task: no form, no checkbox, no button but the copy icon.
    assert row.all("input") == [] and row.all("form") == [] and row.all("select") == []
    assert [b.attrs.get("class") for b in row.all("button")] == ["uxtl__icon uxtl__icon--copy"]
    assert not row.all(cls="uxtl__next") and not row.all(cls="uxtl__nextlabel")


def test_an_ordinary_note_carries_no_next_actions_label():
    _html, root = render(note_item(a_note()))
    assert "Järgmised tegevused" not in the_row(root).words()


def test_a_hostile_title_text_and_link_are_escaped_and_never_executed():
    hostile = '<script>alert("x")</script><img src=x onerror=alert(1)>'
    note = a_note(
        headline=hostile,
        paragraphs=(hostile, "rida\n<b>paks</b>"),
        page_title=hostile,
        section=hostile,
        source_ref=hostile,
        links=(
            (hostile, "javascript:alert(1)"),
            ("Näidisleht", "https://example.invalid/leht?a=1&b=2"),
            ("", "onenote:///näidis"),
        ),
        direct_files=(a_file(1, title=hostile, original_filename=hostile, comment=hostile),),
    )
    html, root = render(note_item(note))
    assert "<script" not in html and "<img" not in html and "<b>" not in html
    assert "&lt;script&gt;" in html
    row = the_row(root)
    hrefs = [a.attrs.get("href", "") for a in row.all("a")]
    assert not [href for href in hrefs if href.lower().startswith(("javascript:", "onenote:"))]
    # The safe link opens elsewhere and says so; its query string is escaped.
    (web,) = [a for a in row.all("a") if a.attrs.get("href", "").startswith("https://")]
    assert web.attrs["target"] == "_blank" and web.attrs["rel"] == "noopener noreferrer"
    assert web.words() == "Näidisleht — avaneb uues aknas"
    assert 'href="https://example.invalid/leht?a=1&amp;b=2"' in html
    # The refused address keeps its text, as text.
    assert hostile in [ref.text() for ref in row.all(cls="uxtl__histref")]


# ---------------------------------------------------------------------------
# Document rows (`HISTORICAL_DOCUMENTS_IMPORTED`)
# ---------------------------------------------------------------------------


def test_a_document_row_reads_day_count_and_the_informative_headline():
    files = (a_file(1, title="EELNÕU.pdf"), a_file(2), a_file(3), a_file(4))
    _html, root = render(files_item(files, what="EELNÕU.pdf ja 3 muud faili"))
    row = the_row(root)
    summary = row.one("details", "uxtl__hist").one("summary")
    assert summary.words() == "29.07.2026 4 dokumenti EELNÕU.pdf ja 3 muud faili"
    # The day is the Tallinn day of the row's own time.
    assert summary.one("time").attrs["datetime"] == "2026-07-29"
    assert len(row.all("li", "uxtl__histfile")) == 4
    assert "OneNote'i lisati" not in row.words()


def test_a_single_document_row_says_dokument():
    _html, root = render(files_item((a_file(1),), what="NAIDIS_LISA_1.pdf"))
    assert [t.words() for t in the_row(root).all(cls="uxtl__histtag")] == ["Dokument"]


def test_the_undated_document_row_names_no_day():
    files = (a_file(1, added_display="lisamise kuupäev teadmata"), a_file(2))
    _html, root = render(
        files_item(files, what="NAIDIS_LISA_1.pdf ja 1 muu fail", display="kuupäev teadmata")
    )
    summary = the_row(root).one("summary")
    assert summary.all("time") == []
    assert summary.words().startswith("kuupäev teadmata 2 dokumenti")


def test_a_document_row_without_historical_files_keeps_the_milestone_rendering():
    # The real item, as `timeline.py` builds it today: no `historical_files`.
    when = datetime(2026, 7, 29, 5, 15, tzinfo=UTC)
    item = TimelineItem(
        occurred_at=when,
        created_at=when,
        sort_key="onenote:grupp-1",
        item_type=HISTORICAL_IMPORT_ITEM,
        milestone=ChronologyMilestone(
            what="OneNote'i lisati 2 dokumenti", display_date="29.7.2026", sub="2 faili"
        ),
        imported_documents=2,
    )
    html, root = render(item)  # type: ignore[arg-type]
    assert "uxtl__item--historical" not in html
    assert "OneNote'i lisati 2 dokumenti" in the_row(root).words()


def test_notes_and_document_rows_render_side_by_side_with_distinct_ids():
    html, root = render(
        note_item(a_note(), key="onenote-note:1"),
        note_item(a_note(), key="onenote-note:2"),
        files_item((a_file(1),), what="NAIDIS_LISA_1.pdf"),
    )
    ids = re.findall(r'\sid="([^"]+)"', html)
    assert len(ids) == len(set(ids))
    assert len(root.all("details")) == 3


# ---------------------------------------------------------------------------
# The doubles are the contract
# ---------------------------------------------------------------------------


def test_the_doubles_have_exactly_the_contract_fields():
    try:
        module = importlib.import_module("app.legacy_import.historical_notes")
    except ImportError:
        pytest.skip("agent B's module is not in this tree yet")
    for double in (HistoricalFile, HistoricalNoteRow):
        real = getattr(module, double.__name__)
        fields = dataclasses.fields(real)
        wanted = [f.name for f in dataclasses.fields(double)]
        # The contract's fields, in its order; anything the real class adds
        # after them has a default, so a row built from the contract still is one.
        assert [f.name for f in fields[: len(wanted)]] == wanted, double.__name__
        for extra in fields[len(wanted) :]:
            assert extra.default is not dataclasses.MISSING or (
                extra.default_factory is not dataclasses.MISSING
            ), f"{double.__name__}.{extra.name}"
