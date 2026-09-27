"""`Teema käik` is an accordion: one line per row until somebody opens it.

docs/adr/0074 §14, amended 2026-09-27. What is asserted here is what the server
decides — the markup and the stylesheet's contract with it:

* every row renders **closed**, with one toggle button whose `aria-expanded` is
  `false` and whose name is the row's own line (`aria-labelledby`);
* the line a closed row shows is the headline and the date (a milestone) or the
  author, the act and the time (a work entry) — and **nothing else**: no body,
  no file, no `Juristi märkus`, no `Muuda` / `+ Lisa fail` / `Kustuta`;
* the two levels of #340 are untouched, and only a primary row carries a
  primary hook;
* a row with nothing behind its line has nothing for the toggle to open.

What a closed row *shows* is decided by `static/css/app.css`, so the rule is
mirrored here as `_shown_while_closed` and the mirror is pinned to the
stylesheet by `test_the_mirror_is_the_stylesheets_own_closed_state` — a change
to one without the other fails. The behaviour itself (a click opens a row,
one row at a time, the chevron turns, nothing overflows) is the browser lane's:
`e2e/test_teema_kaik_accordion.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.enums import ExternalPositionProvenance
from app.matters.timeline import matter_timeline
from tests.test_teema_page_cleanup import _sent_opinion

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parent.parent
APP_CSS = ROOT / "static" / "css" / "app.css"

_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)


# ---------------------------------------------------------------------------
# A small tree, because the question is about structure
# ---------------------------------------------------------------------------


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    children: list[_Node | str] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def elements(self) -> list[_Node]:
        return [child for child in self.children if isinstance(child, _Node)]

    def walk(self):
        yield self
        for child in self.elements():
            yield from child.walk()

    def text(self) -> str:
        parts: list[str] = []
        for child in self.children:
            parts.append(child if isinstance(child, str) else child.text())
        return " ".join(" ".join(parts).split())

    def find(self, cls: str) -> list[_Node]:
        return [node for node in self.walk() if cls in node.classes]


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {name: value or "" for name, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack[-1].children.append(_Node(tag, {name: value or "" for name, value in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for depth in range(len(self.stack) - 1, 0, -1):
            if self.stack[depth].tag == tag:
                del self.stack[depth:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.stack[-1].children.append(data)


def _tree(html: str) -> _Node:
    parser = _Tree()
    parser.feed(html)
    parser.close()
    return parser.root


def _rows_in(html: str) -> list[_Node]:
    return [
        node
        for node in _tree(html).walk()
        if node.tag == "article" and "uxtl__item" in node.classes
    ]


# ---------------------------------------------------------------------------
# The closed state, mirrored from the stylesheet
# ---------------------------------------------------------------------------

#: Children of `.uxtl__body` that stay on screen while a row is closed.
BODY_KEEPS = ("uxtl__toggle", "uxtl__head", "uxtl__meta", "uxtl__ms-body")
#: Children of a correction element's `.uxtl__ms-body` that stay.
MS_BODY_KEEPS = ("uxtl__head", "uxtl__editform")


def _shown_while_closed(row: _Node) -> list[_Node]:
    """What a closed row leaves on screen, by the stylesheet's own rules.

    `@media screen and (scripting: enabled)` in `static/css/app.css`: a body
    child that is not the toggle, the line or a correction element is hidden;
    inside a correction element only its line (and an open editor's headline)
    stays; and the line's own controls are hidden.
    """
    (body,) = [node for node in row.elements() if "uxtl__body" in node.classes]
    shown: list[_Node] = []
    for child in body.elements():
        if not child.classes & set(BODY_KEEPS):
            continue
        if "uxtl__ms-body" in child.classes:
            for part in child.elements():
                if "uxtl__head" in part.classes:
                    shown.append(_without_controls(part))
                elif "uxtl__editform" in part.classes:
                    shown.extend(p for p in part.elements() if "uxtl__ms" in p.classes)
        elif "uxtl__head" in child.classes:
            shown.append(_without_controls(child))
        else:
            shown.append(child)
    return shown


def _without_controls(head: _Node) -> _Node:
    return _Node(
        head.tag,
        head.attrs,
        [c for c in head.children if isinstance(c, str) or "uxtl__editactions" not in c.classes],
    )


def _closed_text(row: _Node) -> str:
    return " ".join(
        node.text() for node in _shown_while_closed(row) if "uxtl__toggle" not in node.classes
    )


def _css_block(css: str, opener: str) -> str:
    start = css.index(opener)
    depth = 0
    for index in range(css.index("{", start), len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[start : index + 1]
    raise AssertionError(f"unclosed block {opener!r}")


def _squash(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return " ".join(css.split())


# ---------------------------------------------------------------------------
# The world: one of every kind of row a lawyer meets
# ---------------------------------------------------------------------------


@pytest.fixture
def kaik_file(specialist, organisation, capture_evidence):
    """An outcome, a correctable milestone of each kind, a note and `Teema loodud`.

    Created through the service, so `Teema loodud` is on it as it is on every
    real file.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile

    from app.matters.enums import EngagementKind
    from app.matters.services import (
        add_engagement,
        add_entry,
        create_matter,
        record_external_position,
    )
    from app.matters.workspace import add_procedural_development

    normal_matter = create_matter(
        title="Pakendiseaduse muutmine", actor=specialist, owner=specialist
    )
    _sent_opinion(normal_matter, capture_evidence, days_ago=3)
    add_procedural_development(
        matter=normal_matter,
        author=specialist,
        title="Rääkisin MKM-iga",
        note="Nad ootavad liikmete arvamust.",
        uploads=[SimpleUploadedFile("kohtumise-protokoll.pdf", b"%PDF-1.4 minutes")],
    )
    record_external_position(
        matter=normal_matter,
        organisation=organisation,
        provenance=ExternalPositionProvenance.RECEIVED.value,
        summary="Liige toetab eelnõu",
        actor=specialist,
    )
    add_engagement(
        matter=normal_matter,
        kind=EngagementKind.SURVEY.value,
        title="Liikmete küsitlus",
        occurred_on=timezone.localdate(),
        actor=specialist,
    )
    add_entry(matter=normal_matter, author=specialist, body="<p>Helistasin ministeeriumisse.</p>")
    return normal_matter


def _teema(matter) -> str:
    return reverse("matters:matter_detail", kwargs={"pk": matter.pk})


def _items(matter, user):
    items, _more = matter_timeline(matter=matter, user=user, limit=200)
    return items


# ---------------------------------------------------------------------------
# Every row renders closed, with one toggle named by its own line
# ---------------------------------------------------------------------------


def test_every_row_renders_closed_with_one_toggle(signed_in, kaik_file):
    rows = _rows_in(signed_in.get(_teema(kaik_file)).content.decode())
    assert len(rows) >= 6

    for row in rows:
        assert "uxtl__item--open" not in row.classes, row.attrs
        toggles = row.find("uxtl__toggle")
        assert len(toggles) == 1, row.attrs
        (toggle,) = toggles
        assert toggle.tag == "button"
        assert toggle.attrs.get("type") == "button"
        assert toggle.attrs.get("aria-expanded") == "false"
        # First in the row's body: the first thing Tab reaches, and the landing
        # `app.js` gives a row after a swap.
        (body,) = [node for node in row.elements() if "uxtl__body" in node.classes]
        assert body.elements()[0] is toggle
        # The chevron is drawn, and says nothing to a screen reader.
        (chevron,) = toggle.find("uxtl__chevron")
        assert chevron.attrs.get("aria-hidden") == "true"


def test_the_toggle_is_named_by_the_line_the_closed_row_shows(signed_in, kaik_file, specialist):
    """`aria-labelledby` resolves to exactly one element, inside the same row,
    and that element is the row's line — so a correction that renames the
    headline renames the toggle, with no second copy of the words to update."""
    html = signed_in.get(_teema(kaik_file)).content.decode()
    rows = _rows_in(html)
    items = _items(kaik_file, specialist)
    assert len(rows) == len(items)

    ids = [node.attrs["id"] for node in _tree(html).walk() if "id" in node.attrs]
    for row, item in zip(rows, items, strict=True):
        (toggle,) = row.find("uxtl__toggle")
        target = toggle.attrs["aria-labelledby"]
        assert target == f"kaik-{item.row_key}-rida"
        assert ids.count(target) == 1, target
        (line,) = [node for node in row.walk() if node.attrs.get("id") == target]
        assert line.classes & {"uxtl__ms", "uxtl__meta"}, line.attrs
        assert row.attrs["data-kaik-rida"] == item.row_key
        if item.is_milestone:
            assert item.milestone.what in line.text()
            assert item.milestone.display_date in line.text()
        else:
            assert "uxtl__meta" in line.classes


def test_a_closed_row_shows_its_line_and_none_of_its_detail(signed_in, kaik_file):
    """The body, the files, `Juristi märkus`, the sub-line and every control
    are in the page — and all of it is behind the toggle."""
    rows = _rows_in(signed_in.get(_teema(kaik_file)).content.decode())

    def row_with(words: str) -> _Node:
        (match,) = [row for row in rows if words in row.text()]
        return match

    detail = {
        "Helistasin ministeeriumisse.": "Helistasin ministeeriumisse.",
        "Rääkisin MKM-iga": "Nad ootavad liikmete arvamust.",
        "Liige toetab eelnõu": "Liige toetab eelnõu",
        "Arvamus välja": "arvamus-3.pdf",
    }
    assert "kohtumise-protokoll.pdf" in row_with("Rääkisin MKM-iga").text()
    assert "kohtumise-protokoll.pdf" not in _closed_text(row_with("Rääkisin MKM-iga"))
    for words, hidden in detail.items():
        row = row_with(words)
        assert hidden in row.text(), words
        assert hidden not in _closed_text(row), (words, _closed_text(row))

    for row in rows:
        closed = _closed_text(row)
        for control in ("Muuda", "Kustuta", "+ Lisa fail", "Lõpeta kaasamine", "Ootan tagasisidet"):
            assert control not in closed, (control, closed)
        # Nothing behind the line reaches a closed row: no file link, no link
        # out, no pill.
        for shown in _shown_while_closed(row):
            for cls in (
                "uxtl__file",
                "uxtl__link",
                "uxtl__next",
                "uxtl__msnote",
                "uxtl__mssub",
                "richtext",
            ):
                assert not shown.find(cls), (cls, closed)


def test_the_controls_are_still_there_behind_the_line(signed_in, kaik_file):
    """Collapsing is presentation: every control a row had, it still has."""
    html = signed_in.get(_teema(kaik_file)).content.decode()
    history = html[html.index('id="ajalugu-loend"') :]

    assert history.count(">Muuda<") >= 4
    assert "+ Lisa fail" in history
    assert "Kustuta" in history


def test_a_row_with_nothing_behind_its_line_has_nothing_to_open(signed_in, kaik_file):
    """`Teema loodud` is a headline and a date. The stylesheet draws no toggle
    for a body holding nothing the closed state hides; this is the other half
    of that rule — the row really does hold nothing else."""
    rows = _rows_in(signed_in.get(_teema(kaik_file)).content.decode())
    (created,) = [row for row in rows if "Teema loodud" in row.text()]
    (body,) = [node for node in created.elements() if "uxtl__body" in node.classes]

    assert [sorted(child.classes) for child in body.elements()] == [
        ["uxtl__toggle"],
        ["uxtl__head"],
    ]
    (head,) = body.find("uxtl__head")
    assert not head.find("uxtl__editactions")


# ---------------------------------------------------------------------------
# #340's two levels, unchanged by the accordion
# ---------------------------------------------------------------------------


def test_only_a_primary_row_carries_a_primary_hook(signed_in, kaik_file, specialist):
    rows = _rows_in(signed_in.get(_teema(kaik_file)).content.decode())
    items = _items(kaik_file, specialist)

    primary = [row for row, item in zip(rows, items, strict=True) if item.is_primary]
    secondary = [row for row, item in zip(rows, items, strict=True) if not item.is_primary]
    assert len(primary) == 1 and "Arvamus välja" in primary[0].text()
    assert secondary

    for row in primary:
        assert "uxtl__item--primary" in row.classes
        assert "uxtl__item--secondary" not in row.classes
        assert row.find("uxtl__dot--primary")
    for row in secondary:
        assert "uxtl__item--secondary" in row.classes
        assert "uxtl__item--primary" not in row.classes
        assert not row.find("uxtl__dot--primary")


def test_the_stylesheet_keeps_the_hierarchy_on_the_closed_line():
    """Only the primary line is semibold; a secondary headline and author are
    regular and one step down in ink; no date is ever bold; and the one panel
    allowed the accent edge is an open primary row."""
    css = _squash(APP_CSS.read_text(encoding="utf-8"))

    assert (
        "#teema-vaade-wrap .uxtl__item--primary .uxtl__mswhat { "
        "font-weight: var(--typography-weight-semibold); }" in css
    )
    assert (
        "#teema-vaade-wrap .uxtl__item--secondary .uxtl__mswhat, "
        "#teema-vaade-wrap .uxtl__item--secondary .uxtl__author { "
        "font-weight: var(--typography-weight-regular); color: var(--text-secondary); }"
    ) in css
    msdate = re.search(r"#teema-vaade-wrap \.uxtl__msdate \{([^}]*)\}", css)
    assert msdate and "font-weight: var(--typography-weight-regular)" in msdate.group(1)
    assert "color: var(--text-muted)" in msdate.group(1)
    assert "semibold" not in " ".join(re.findall(r"uxtl__msdate[^{]*\{([^}]*)\}", css))
    assert (
        "#teema-vaade-wrap .uxtl__item--primary.uxtl__item--open > .uxtl__body { "
        "border-color: var(--accent-border); }"
    ) in css


# ---------------------------------------------------------------------------
# The stylesheet's closed state is the one mirrored above
# ---------------------------------------------------------------------------


def test_the_mirror_is_the_stylesheets_own_closed_state():
    css = APP_CSS.read_text(encoding="utf-8")
    closed = _squash(_css_block(css, "@media screen and (scripting: enabled)"))

    base = "#teema-vaade-wrap .uxtl__item:not(.uxtl__item--open)"
    assert f"{base} .uxtl__body > :not({', '.join('.' + c for c in BODY_KEEPS)})" in closed
    assert f"{base} .uxtl__ms-body > :not({', '.join('.' + c for c in MS_BODY_KEEPS)})" in closed
    assert f"{base} .uxtl__ms-body > .uxtl__editform > :not(.uxtl__ms)" in closed
    assert f"{base} .uxtl__head > .uxtl__editactions" in closed
    assert "display: none;" in closed


def test_a_closed_row_has_no_box():
    """Transparent border, no background: only the open row is a panel."""
    css = _squash(APP_CSS.read_text(encoding="utf-8"))
    body = re.search(r"#teema-vaade-wrap \.uxtl__body \{([^}]*)\}", css)
    assert body is not None
    rules = body.group(1)
    assert "border: 1px solid transparent" in rules
    assert "background" not in rules
    opened = re.search(r"#teema-vaade-wrap \.uxtl__item--open > \.uxtl__body \{([^}]*)\}", css)
    assert opened and "border-color: var(--border-default)" in opened.group(1)


def test_without_scripting_every_row_reads_open():
    """The closed state lives only under `(scripting: enabled)`; with scripting
    off no toggle is drawn and nothing is hidden, so the record still reads."""
    css = APP_CSS.read_text(encoding="utf-8")
    fallback = _squash(_css_block(css, "@media not screen, (scripting: none)"))
    assert "#teema-vaade-wrap .uxtl__toggle { display: none; }" in fallback
    outside = _squash(css.replace(_css_block(css, "@media screen and (scripting: enabled)"), ""))
    assert ".uxtl__item:not(.uxtl__item--open)" not in outside


# ---------------------------------------------------------------------------
# The key the toggle and the script share
# ---------------------------------------------------------------------------


def test_the_row_key_is_the_records_own_pk_or_the_sort_key(kaik_file, specialist):
    items = _items(kaik_file, specialist)
    keys = [item.row_key for item in items]

    assert len(set(keys)) == len(keys)
    for item in items:
        if item.record is not None:
            assert item.row_key == str(item.record.pk) == item.sort_key
        else:
            assert item.row_key == item.sort_key


def test_a_corrected_row_keeps_the_id_its_toggle_is_named_by(signed_in, kaik_file):
    """The correction element comes back from its own view — `Tühista` after
    `Muuda` — and the line it draws still carries `kaik-<pk>-rida`, so the
    toggle outside the swap still resolves its name."""
    from app.matters.models import MatterEngagement
    from app.matters.views import ENGAGEMENT_READ_QUERY

    engagement = MatterEngagement.objects.get(matter=kaik_file)
    url = reverse(
        "matters:update_engagement", kwargs={"pk": kaik_file.pk, "engagement_id": engagement.pk}
    )
    html = signed_in.get(f"{url}{ENGAGEMENT_READ_QUERY}", HTTP_HX_REQUEST="true").content.decode()
    (line,) = [
        node for node in _tree(html).walk() if node.attrs.get("id") == f"kaik-{engagement.pk}-rida"
    ]
    assert "uxtl__ms" in line.classes
    assert "Liikmete küsitlus" in line.text()


def test_older_rows_arrive_closed_as_well(signed_in, normal_matter, specialist):
    """«Näita varasemaid» answers with the same fragment, so the rows it swaps
    in are closed and carry their toggle like the first page's."""
    from app.matters.services import add_entry

    for index in range(35):
        add_entry(matter=normal_matter, author=specialist, body=f"<p>Märkus {index}</p>")
    url = reverse("matters:timeline_page", kwargs={"pk": normal_matter.pk})
    rows = _rows_in(signed_in.get(f"{url}?nihe=30", HTTP_HX_REQUEST="true").content.decode())

    assert rows
    for row in rows:
        (toggle,) = row.find("uxtl__toggle")
        assert toggle.attrs["aria-expanded"] == "false"
        assert "uxtl__item--open" not in row.classes
