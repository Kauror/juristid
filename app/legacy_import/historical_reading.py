"""How a historical OneNote page reads at `/ajalugu/<pk>/` (UI-12, November 2026).

Pure functions over the stored blocks: nothing here reads the database, and
nothing stored changes. `historical_views._rendered_blocks` calls them to say,
for each block, what the reading view needs — the text a reader sees, where a
web address in it is, whether the block is a source heading or the start of a
dated note, and the block's stable anchor — so the template still decides
nothing.

**The visible `&quot;` (UI-12).** The desktop reader keeps a block's text as
OneNote stores it: `one:T` is an HTML fragment, and the reader strips its tags
but keeps its character references (`tools/onenote_ingress/context.py`
`blocks_from_desktop_xml`, PR #595). The capture import copies the block
verbatim (`onenote_capture.rendered_blocks`, which marks every block it writes
with `source_kind`). So a `"` typed in OneNote is stored as `&quot;`, and the
template's autoescape turned that into `&amp;quot;`: the reader saw the
reference, escaped a second time. `display_text` decodes the references
**exactly once**, and only for a block the capture import wrote — the one shape
whose encoding is proven. The result is plain text and is autoescaped like any
other; nothing is marked safe, nothing is decoded twice (a person who typed
«&quot;» is stored `&amp;quot;` and reads «&quot;»), and the stored text is
never rewritten. Blocks of the earlier archive and pilot shapes are shown as
stored: their encoding is not proven, so they are not decoded.

**Headings** are structural only. The capture keeps no paragraph style, so a
OneNote «Heading 1» cannot be told from body text. A text block is a source
heading when the outline nests other blocks under it (the same structure the
capture calls a `CONTAINER_HEADING`), it is one short line, it is not the start
of a dated note, and it does not end like a sentence. Nothing else is promoted,
and no heading is written that the page did not have.

**Dated sections** are exactly the dated notes the chronology reads
(`historical_notes.parse_page`), so `Tegevused` and this page agree on what a
dated section is, and the note row's «OneNote'i koopia» lands on its start.
"""

from __future__ import annotations

import html
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise
from typing import Any

from app.legacy_import.historical_notes import (
    _URL,
    _URL_TRAILING,
    headline,
    parse_page,
)

#: The field only the capture import writes on a block (`rendered_blocks`): the
#: proof that the text is the desktop reader's tag-stripped HTML fragment.
CAPTURE_MARK = "source_kind"

#: A heading is one line of at most this many characters.
HEADING_LIMIT = 90

#: The contents list is drawn from this many entries: a short page needs none.
CONTENTS_MIN = 3

#: Indentation is drawn to this depth; deeper blocks keep it.
DEPTH_LIMIT = 4

#: A file type shown as its extension, no longer than this.
TYPE_LIMIT = 5


def is_capture_block(block: Mapping[str, Any]) -> bool:
    """A block written by the desktop capture import, whose text is entity-encoded."""
    return CAPTURE_MARK in block


def display_text(block: Mapping[str, Any]) -> str:
    """The block's text as a reader sees it: decoded once where proven encoded.

    Plain text either way. The template escapes it; nothing is marked safe.
    """
    text = str(block.get("text") or "")
    return html.unescape(text) if is_capture_block(block) else text


def text_parts(text: str) -> list[tuple[str, str]]:
    """``text`` as (piece, href) pairs: href is "" for prose, a web address otherwise.

    Only an `http://`, `https://` or `www.` address written out in the text is
    a link — the rule the chronology uses for a note's links (R10). Every
    character of ``text`` is in exactly one piece, in order.
    """
    parts: list[tuple[str, str]] = []
    cursor = 0
    for match in _URL.finditer(text):
        shown = match.group(0).rstrip(_URL_TRAILING)
        if not shown:
            continue
        start, end = match.start(), match.start() + len(shown)
        if start > cursor:
            parts.append((text[cursor:start], ""))
        href = shown if shown.lower().startswith(("http://", "https://")) else f"https://{shown}"
        parts.append((shown, href))
        cursor = end
    if cursor < len(text):
        parts.append((text[cursor:], ""))
    return parts


def anchor(ordinal: int) -> str:
    """The id a block carries on the page: `plokk-<stored ordinal>`."""
    return f"plokk-{ordinal}"


def _ordinal(block: Mapping[str, Any]) -> int | None:
    value = block.get("ordinal")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def anchored_ordinals(blocks: Iterable[Mapping[str, Any]]) -> frozenset[int]:
    """The stored ordinals that name one block only: the ones an anchor can trust."""
    counts = Counter(
        ordinal
        for block in blocks
        if isinstance(block, Mapping) and (ordinal := _ordinal(block)) is not None
    )
    return frozenset(ordinal for ordinal, count in counts.items() if count == 1)


def _parents(blocks: Sequence[Mapping[str, Any]]) -> set[int]:
    """Ordinals of blocks the outline nests other blocks under."""
    if blocks and "outline" in blocks[0]:
        return {
            parent
            for block in blocks
            if isinstance(parent := block.get("parent"), int) and not isinstance(parent, bool)
        }
    out: set[int] = set()
    for here, after in pairwise(blocks):
        ordinal = _ordinal(here)
        if ordinal is not None and int(after.get("depth") or 0) > int(here.get("depth") or 0):
            out.add(ordinal)
    return out


def dated_starts(
    blocks: Sequence[Mapping[str, Any]], links: Any = (), *, page_modified: Any = None
) -> dict[int, dict[str, str]]:
    """The first block of each dated note, as the chronology reads them.

    ``{ordinal: {"date": "12.03.2024", "iso": "2024-03-12", "label": …}}`` —
    the label is the note's own first words (≤ 65 characters, cut at a word).
    """
    parsed = parse_page(blocks, links, page_modified=page_modified)
    out: dict[int, dict[str, str]] = {}
    for note in parsed.notes:
        if note.date is None or note.start < 0:
            continue
        first = note.paragraphs[0] if note.paragraphs else ""
        out[note.start] = {
            "date": note.date.display,
            "iso": note.date.iso,
            "label": headline(first) if first else "",
        }
    return out


def headings(blocks: Sequence[Mapping[str, Any]], *, dated: Iterable[int] = ()) -> set[int]:
    """Ordinals of the text blocks that head nested material (the module's rule)."""
    parents = _parents([b for b in blocks if isinstance(b, Mapping)])
    skip = set(dated)
    out: set[int] = set()
    for block in blocks:
        ordinal = _ordinal(block)
        if ordinal is None or ordinal not in parents or ordinal in skip:
            continue
        if str(block.get("kind") or "TEXT") != "TEXT":
            continue
        text = display_text(block).strip()
        if not text or "\n" in text or len(text) > HEADING_LIMIT or text.endswith("."):
            continue
        out.add(ordinal)
    return out


def depth_of(block: Mapping[str, Any]) -> int:
    """How far the block is indented on the page, 0 to `DEPTH_LIMIT`."""
    try:
        depth = int(block.get("depth") or 0)
    except (TypeError, ValueError):
        depth = 0
    return max(0, min(depth, DEPTH_LIMIT))


def depth_class(depth: int) -> str:
    """The indentation class for ``depth`` (`historical-reading.css`)."""
    return f"casefile__block--d{max(0, min(depth, DEPTH_LIMIT))}"


def file_type(filename: str) -> str:
    """«PDF», «DOCX»: the file's extension, upper case, or "" when it has none."""
    name = (filename or "").rsplit("/", 1)[-1]
    if "." not in name.strip("."):
        return ""
    extension = name.rsplit(".", 1)[-1].upper()
    return extension if 0 < len(extension) <= TYPE_LIMIT and extension.isalnum() else ""


def reading_entries(blocks: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rendered blocks with each run of list items gathered into one list.

    Order is kept exactly: a list is closed by the first block that is not a
    list item, so a file between two items splits them into two lists.
    """
    out: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("kind") == "text" and block.get("list_item"):
            if out and out[-1]["kind"] == "list":
                out[-1]["items"].append(block)
            else:
                out.append({"kind": "list", "items": [block]})
        else:
            out.append(block)
    return out


def contents(blocks: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """The page's own headings and dated sections, in reading order, for its contents list.

    Empty for a page with fewer than `CONTENTS_MIN` of them. Only a block with
    a stable anchor is listed: an entry that leads nowhere is worse than none.
    """
    entries = []
    for block in blocks:
        if block.get("kind") != "text" or not block.get("anchor"):
            continue
        if block.get("dated"):
            entries.append(
                {
                    "anchor": block["anchor"],
                    "date": block["dated"]["date"],
                    "iso": block["dated"]["iso"],
                    "label": block["dated"]["label"],
                }
            )
        elif block.get("heading"):
            entries.append(
                {"anchor": block["anchor"], "date": "", "iso": "", "label": block["text"]}
            )
    return entries if len(entries) >= CONTENTS_MIN else []
