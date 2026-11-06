"""The dated notes of a Teema's OneNote pages, read for its chronology.

A OneNote page in this department is a running log: «27.07.2026 - KLIM saatis
kooskõlastusringile eelnõu …», four PDFs, «10.08.2026 - kojal ei olnud
kommentaare …». The import kept the page (`LegacySourcePage.blocks`) and made
its files into Documents, and the chronology used to say only what the importer
did — «OneNote'i lisati 4 dokumenti». This module reads what *happened*, from
the page's own words.

**A read-only projection.** Nothing here is stored: no Entry, Submission,
Development, action or Document is made, and no stored row changes. Every call
reads the page as it is and returns plain values (`HistoricalNoteRow`,
`HistoricalFile`) that the chronology renders.

**The rules are agent A's specification of 2026-11-05** (R0–R12, the private
worklog `onenote-timeline-titles/agent-a/rules.md`, adopted by the lead), each
numbered below where it is implemented. Precision before recall: every rule is
anchored on structure — where a block starts and ends, how it is nested, where
the empty lines are. A date in prose, a day and month without a year, a block's
own creation time, the capture time and the import time are never an event
date; anything uncertain is *undated*, stays on its page, and is one click away
from every row this module draws.

**Files.** A file is *direct* to a note only by real container structure, per
owner decision D9 of 2026-11-04 (R9a): the file block is nested in the outline
under the note's own first block. A file beside a note, inside its run of
lines, or under an undated label that follows it is page text only and reads
on its own document row on the day it was added to OneNote
(app/matters/timeline.py). **The structural comments are not the basis:** a
file's `ATTACHED_NOTE` («Märkus OneNote'is») was found to be, more often than
not, the indented label of the *next* file in a list, and a `CONTAINER_HEADING`
is mostly a category heading («Arvamused:») over several files (agent D, the
lead's finding of 2026-11-05). A comment is shown with its file
(`HistoricalFile.comment`) as Dokumendid shows it and never promoted. A file is
matched to its block by `resource_key`, never by a stored block ordinal (R9c).

The parser (`parse_page`) is pure and takes the stored blocks; the projection
(`project_page`, `matter_notes`) adds what only the database knows — which
link the reader came through and which imported files they may see.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

#: The three precisions a note date can have (docs/adr/0079's vocabulary).
DAY = "DAY"
MONTH = "MONTH"
YEAR = "YEAR"

#: What a note is: an ordinary dated entry, or a «Järgmised tegevused» section.
NOTE = "NOTE"
NEXT_ACTIONS = "NEXT_ACTIONS"
#: How a `NEXT_ACTIONS` note is labelled wherever it is drawn. Historical text,
#: never a current task: nothing here creates, opens or schedules an action.
NEXT_ACTIONS_LABEL = "Ajalooline: Järgmised tegevused"

#: What a file prints where its OneNote day would go, when the source has none.
UNDATED_FILE_DISPLAY = "lisamise kuupäev teadmata"

#: The collapsed row's headline: at most this many characters, «…» included.
HEADLINE_LIMIT = 65
ELLIPSIS = "…"

#: **R2b on or off, in one place** (open question Q1, lead decision: on). A
#: short heading that *ends* with a full date («Koja arvamus 12.03.2024:») is a
#: dated note, shown verbatim.
TRAILING_DATES = True

#: R2a validation: the years a note date may name.
YEAR_MIN, YEAR_MAX = 1990, 2035
#: R7 (Q5): a date more than this many days after its block was last touched
#: was written ahead (a forecast) or mistyped, and is not an event date. The
#: block's times only ever *veto* a date; they never supply one.
WRITTEN_AHEAD_DAYS = 45

#: Why a date-shaped start carries no date (R2a, R7).
NO_YEAR = "NO_YEAR"
YEAR_MALFORMED = "YEAR_MALFORMED"
YEAR_OUT_OF_RANGE = "YEAR_OUT_OF_RANGE"
NOT_A_CALENDAR_DATE = "NOT_A_CALENDAR_DATE"
DATE_AFTER_WRITING = "DATE_AFTER_WRITING"
UNDATED = "UNDATED"

#: The block kinds whose text can start or carry a note (R0).
TEXT_KINDS = frozenset({"TEXT", "LIST_ITEM"})
#: The block kinds that hold a file that can have a Document (R9).
FILE_KINDS = frozenset({"FILE_ATTACHMENT", "FILE", "IMAGE"})
#: The desktop capture's outline index of a floating object (R0).
FLOATING = -1


# -- the contract (C renders these; field names are fixed) -------------------------


@dataclass(frozen=True)
class HistoricalFile:
    """One imported file shown in an expanded row."""

    title: str
    original_filename: str
    #: The Document's own page (`documents:document_detail`).
    document_url: str
    #: The file itself: opened in a new tab where it may be, else downloaded
    #: (`app.documents.inline.file_link`).
    open_url: str
    #: «29.07.2026», the Tallinn day it was added to OneNote, or
    #: `UNDATED_FILE_DISPLAY`.
    added_display: str
    #: The file's structural comment (`Märkus OneNote'is` / `Pealkiri
    #: OneNote'is`), verbatim, or "". Shown with the file; never an association.
    comment: str
    #: Added to OneNote before the page it is on now was created (moved content).
    predates_page: bool
    #: **Extension** (not in the first contract): the `DocumentVersion` the links
    #: point at, so a caller can tell two files apart without parsing a URL.
    version_id: Any = None


@dataclass(frozen=True)
class HistoricalNoteRow:
    """`TimelineItem.historical_note` for item type `HISTORICAL_NOTE`."""

    #: «27.07.2026» | «07.2026» | «2026» | "" (undated).
    date_display: str
    precision: str | None
    #: ≤ `HEADLINE_LIMIT` characters, cut at a word boundary, «…» when cut.
    headline: str
    #: The full note, verbatim: one paragraph per text block (R8), normalised
    #: (R1), a *leading* date and its separator removed from the first.
    paragraphs: tuple[str, ...]
    #: `NOTE` or `NEXT_ACTIONS` (labelled `NEXT_ACTIONS_LABEL`).
    kind: str
    #: (text, href) of the addresses the note shows (R10).
    links: tuple[tuple[str, str], ...]
    #: Files nested under the note's first block (D9 category 1); drives the tag.
    direct_files: tuple[HistoricalFile, ...]
    #: How many of this page's imported files are *not* direct to any note
    #: (D9 category 2), for «Sama OneNote'i lehe failid (N)». Documents, not
    #: file blocks: a shared page with its files held counts 0 (Q8).
    page_files_count: int
    #: `/ajalugu/<msp_id>/`: the page, through the link this reader came by.
    page_url: str
    page_title: str
    section: str
    #: «<capture id> · plokid 0–4»: where on which capture this text is.
    source_ref: str
    #: **Extension** (lead, 2026-11-05): the date machine-readable, at its
    #: precision — "YYYY-MM-DD", "YYYY-MM", "YYYY", or "" (undated).
    date_iso: str = ""
    #: **Extension** (UI-13): the source page's id for the note's first block,
    #: `plokk-<ordinal>`, when that ordinal names one block of the page only —
    #: or "" when no anchor can be trusted, and the link opens the page's top.
    anchor: str = ""
    #: **Extension** (UI-13): the page is linked to more than one live Teema
    #: (`SharedDossier.shared`'s rule, readable or not), for «ühine leht».
    shared_page: bool = False

    @property
    def source_url(self) -> str:
        """The copy view at this note: `page_url`, at its block where stable."""
        return f"{self.page_url}#{self.anchor}" if self.anchor else self.page_url


# -- R1 normalisation -----------------------------------------------------------------

_SPACES = re.compile(r"[ \t\r\n   ]+")


def normalise(text: Any) -> str:
    """R1: entities decoded, zero-width marks removed, whitespace collapsed, trimmed.

    The production text still holds literal `&nbsp;`, `&quot;`, `&amp;` and
    `&gt;`. Nothing else changes: case, typos and punctuation are the source's.
    """
    value = html.unescape(str(text or ""))
    value = value.replace("​", "").replace("﻿", "")
    return _SPACES.sub(" ", value).strip()


#: Kept for callers that only need the whitespace rule.
collapse = normalise


def _collapse(text: str) -> str:
    """R1's whitespace rule alone, for text `normalise` has already decoded.

    Decoding is done once, where a block's stored text is first read. A second
    `normalise` would decode a second time: a person's own «&quot;», stored
    `&amp;quot;`, would read «"» (UI-13). Everything after the first read
    uses this.
    """
    return _SPACES.sub(" ", text).strip()


def day_display(value: date) -> str:
    """«29.07.2026»: the one zero-padded day every historical row and file prints."""
    return f"{value.day:02d}.{value.month:02d}.{value.year}"


# -- R2 dates -------------------------------------------------------------------------

#: Estonian month stems, with the case endings the corpus uses (nominative,
#: genitive, adessive, inessive): «veebruaril», «märtsis», «mail», «september».
_MONTH = (
    r"(?:(?P<mon>jaanuar|veebruar|märts|marts|aprill|august)(?:il|is|i)?"
    r"|(?P<mon2>mai|juuni|juuli)(?:l|s)?"
    r"|(?P<mon3>septemb|oktoob|novemb|detsemb)(?:er|ril|ris|ri))"
)
_MONTH_NUMBER = {
    "jaanuar": 1,
    "veebruar": 2,
    "märts": 3,
    "marts": 3,
    "aprill": 4,
    "mai": 5,
    "juuni": 6,
    "juuli": 7,
    "august": 8,
    "septemb": 9,
    "oktoob": 10,
    "novemb": 11,
    "detsemb": 12,
}
_WORD_END = r"(?![0-9A-Za-zÕÄÖÜõäöüŠšŽž])"
_AFTER_DATE = r"(?P<sep>\s*[-–—:,]\s*|\s+|$)"

#: R2a: (form, pattern, precision), tried in this order; the first match decides.
#: A form whose precision is ``None`` is date-shaped but claims no date: it
#: still starts a segment (so what follows never joins the previous note), undated.
_LEADING_FORMS: tuple[tuple[str, str, str | None], ...] = (
    ("DMY", r"(?P<d>\d{1,2})\.\s?(?P<m>\d{1,2})\.\s?(?P<y>\d{4})(?!\d)(?:\.(?=\s|$))?", DAY),
    ("DMY_SLASH", r"(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{4})(?!\d)", DAY),
    ("DMY2", r"(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{2})(?![\d.])", DAY),
    ("DMY_BADYEAR", r"(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{3}|\d{5,})(?!\d)", None),
    ("D_MONTH_Y", rf"(?P<d>\d{{1,2}})\.\s?{_MONTH}\s+(?P<y>\d{{4}})(?!\d)", DAY),
    ("D_MONTH", rf"(?P<d>\d{{1,2}})\.\s?{_MONTH}{_WORD_END}", None),
    ("DM", r"(?P<d>\d{2})\.(?P<m>\d{2})\.?(?!\d)", None),
    ("MY", r"(?P<m>\d{2})\.(?P<y>\d{4})(?!\d)", MONTH),
    ("MONTH_Y", rf"{_MONTH}\s+(?P<y>\d{{4}})(?!\d)", MONTH),
    ("Y", r"(?P<y>\d{4})(?=\s*[-–—:]\s|\s+seisu(?:ga|ks)\b)", YEAR),
)
_LEADING = tuple(
    (form, re.compile(rf"^(?P<date>{pattern}){_AFTER_DATE}", re.IGNORECASE), precision)
    for form, pattern, precision in _LEADING_FORMS
)
_UNUSABLE_REASON = {"DMY_BADYEAR": YEAR_MALFORMED, "D_MONTH": NO_YEAR, "DM": NO_YEAR}
#: R2a-7: a legal citation («13. juunil 2024. a vastu võetud määrus») is text.
_CITATION = re.compile(
    rf"^\d{{1,2}}\.\s?(?:\d{{1,2}}\.\s?|{_MONTH}\s+)\d{{4}}\.?\s?a\.?(?=\s|$)", re.IGNORECASE
)
_CITATION_TAIL = re.compile(r"\.?\s?a\.?(?=\s|$)", re.IGNORECASE)

#: R2b: a short heading that ends with a full date.
_TRAILING = re.compile(
    r"^(?P<head>\S.{1,88}?)\s*[-–—:,]?\s*\(?\s*(?:läks välja\s+)?"
    rf"(?<=[\s(])(?P<date>(?P<d>\d{{1,2}})\.\s?(?:(?P<m>\d{{1,2}})\.\s?(?P<y>\d{{4}}|\d{{2}})"
    rf"|{_MONTH}\s+(?P<y2>\d{{4}})))"
    r"\s*\)?\s*[:.]?$",
    re.IGNORECASE,
)
#: Never a dated heading: a deadline or due-date label («Vastamise tähtaeg»,
#: «Tagasiside andmise kuupäev:», «Arvamuse andmise tähtpäev»,
#: «Kooskõlastamise lõpp»), a future entry into force, a plan or a wait. Word
#: stems, so every case form is caught («kuupäevaks», «tähtpäeval», «lõppeb»);
#: «kuupäev» and «tähtpäev» also inside a compound («lõpukuupäev»).
_TRAILING_STOP = re.compile(
    r"tähtaeg|tähtaja|kuupäev|tähtpäev|\blõpp|alates|\bkuni\b|jõustu(?:b|vad)\b|kohaldatakse|hakkavad|hakkab|viimati|"
    r"\booda|\bootan|\bootame|plaanis|kavanda|planeeritud|eeldatav|\btoimub\b|\bseisuga\b|"
    r"\bseisuks\b",
    re.IGNORECASE,
)
_TRAILING_MAX_WORDS = 12

#: R4: a «Järgmised tegevused» heading, possibly with text after it.
_NEXT_HEADING = re.compile(
    r"^(?:järgmi(?:sed|ne)\s+(?:tegevus(?:ed)?|sammu?d?)"
    r"|edasised\s+(?:tegevused|sammud)"
    r"|(?:jälgida|järgida),?\s+mis\s+saab\s+edasi"
    r"|mis\s+(?:saab\s+)?edasi)"
    r"\s*[?:.!]*\s*(?P<inline>.*)$",
    re.IGNORECASE,
)

#: R3: a rule line («-----», «_____») separates like an empty line.
_RULE_LINE = re.compile(r"^[-–—_=~*·.]{3,}$")

#: R10: an address the note shows.
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"]+", re.IGNORECASE)
_URL_TRAILING = ".,;:)»”"


@dataclass(frozen=True)
class NoteDate:
    """A date read off a block (R2a leading, or R2b trailing)."""

    form: str
    #: DAY / MONTH / YEAR, or ``None`` when date-shaped but unusable (`reason`).
    precision: str | None
    year: int | None
    month: int | None
    day_of_month: int | None
    #: The block's normalised text after the date and its separator (R2a), or
    #: the whole text for a trailing date, which is never stripped (R2b).
    rest: str
    reason: str = ""

    @property
    def display(self) -> str:
        if self.precision == DAY:
            return f"{self.day_of_month:02d}.{self.month:02d}.{self.year}"
        if self.precision == MONTH:
            return f"{self.month:02d}.{self.year}"
        if self.precision == YEAR:
            return str(self.year)
        return ""

    @property
    def iso(self) -> str:
        if self.precision == DAY:
            return f"{self.year:04d}-{self.month:02d}-{self.day_of_month:02d}"
        if self.precision == MONTH:
            return f"{self.year:04d}-{self.month:02d}"
        if self.precision == YEAR:
            return f"{self.year:04d}"
        return ""

    @property
    def day(self) -> date:
        """The period's first day: the anchor an approximate date sorts on."""
        if self.precision is None or self.year is None:
            raise ValueError("an unusable date has no day")
        return date(self.year, self.month or 1, self.day_of_month or 1)

    @property
    def stripped(self) -> bool:
        return self.form != "TRAILING"


def _month_of(groups: Mapping[str, Any]) -> int | None:
    name = groups.get("mon") or groups.get("mon2") or groups.get("mon3")
    if name:
        return _MONTH_NUMBER[name.lower()]
    return int(groups["m"]) if groups.get("m") else None


def leading_date(text: Any) -> NoteDate | None:
    """R2a: the date a block *begins* with, or ``None`` when it begins with none.

    ``None`` is ordinary text. A `NoteDate` with ``precision=None`` is a
    date-shaped start that claims no date (a day and month without a year, a
    malformed or impossible year): it still starts a segment, undated (U5).
    """
    value = normalise(text)
    if _CITATION.match(value):
        return None
    for form, pattern, precision in _LEADING:
        match = pattern.match(value)
        if match is None:
            continue
        if _CITATION_TAIL.match(value[match.end("date") :]):
            return None
        groups = match.groupdict()
        day = int(groups["d"]) if groups.get("d") else None
        month = _month_of(groups)
        year = int(groups["y"]) if groups.get("y") else None
        if form == "DMY2" and year is not None:
            year += 2000
        reason = ""
        if precision is None:
            reason = _UNUSABLE_REASON[form]
            if form == "DM" and not (1 <= (day or 0) <= 31 and 1 <= (month or 0) <= 12):
                continue  # «09.26 seisuga»: not a date at all
        elif year is None or not (YEAR_MIN <= year <= YEAR_MAX):
            precision, reason = None, YEAR_OUT_OF_RANGE
        elif precision in (DAY, MONTH) and not (1 <= (month or 0) <= 12):
            if form == "MY":
                continue
            precision, reason = None, NOT_A_CALENDAR_DATE
        elif precision == DAY:
            try:
                date(year, month or 0, day or 0)
            except ValueError:
                precision, reason = None, NOT_A_CALENDAR_DATE
        return NoteDate(
            form=form,
            precision=precision,
            year=year,
            month=month,
            day_of_month=day,
            rest=value[match.end() :],
            reason=reason,
        )
    return None


def trailing_date(text: Any) -> NoteDate | None:
    """R2b: a short heading that *ends* with a full, valid date, or ``None``.

    «Koja arvamus 12.03.2024:», «Kolmas lugemine 15.06.2025». Never a deadline,
    a future entry into force, a plan or a wait (the stop list), never more than
    twelve words, one sentence and one date. Returned unstripped: only a
    *leading* date is redundant beside the row's date.
    """
    value = normalise(text)
    match = _TRAILING.match(value)
    if match is None:
        return None
    head = match["head"].strip()
    if (
        len(head.split()) > _TRAILING_MAX_WORDS
        or _TRAILING_STOP.search(head)
        or _NEXT_HEADING.match(head)
        or re.search(r"[.!?]\s+\S", head)
        or re.search(r"\d{1,2}\.\s?\d{1,2}\.", head)
    ):
        return None
    groups = match.groupdict()
    written = groups.get("y") or groups.get("y2") or ""
    year = int(written) + (2000 if len(written) == 2 else 0)
    month = _month_of(groups)
    day = int(groups["d"])
    if not (YEAR_MIN <= year <= YEAR_MAX):
        return None
    try:
        date(year, month or 0, day)
    except ValueError:
        return None
    return NoteDate(
        form="TRAILING", precision=DAY, year=year, month=month, day_of_month=day, rest=value
    )


def is_next_actions_heading(text: Any) -> bool:
    """R4: «Järgmised tegevused:», «Mis saab edasi?», «Järgmine samm:» and kin."""
    return bool(_NEXT_HEADING.match(normalise(text)))


def headline(text: str, limit: int = HEADLINE_LIMIT) -> str:
    """``text`` cut at a word boundary to at most ``limit`` characters, «…» when cut.

    ``text`` is already decoded (a note's paragraphs): only its whitespace is
    collapsed here, so nothing is decoded twice (UI-13).
    """
    text = _collapse(str(text or ""))
    if len(text) <= limit:
        return text
    room = limit - len(ELLIPSIS)
    cut = text[: room + 1]
    space = cut.rfind(" ")
    cut = cut[:space] if space > 0 else text[:room]
    return cut.rstrip(" ,;:–-—") + ELLIPSIS


# -- R5 segmentation (pure) ------------------------------------------------------------


@dataclass(frozen=True)
class ParsedNote:
    """One segment of a page: a note (dated or not) or a «Järgmised tegevused» section."""

    kind: str
    #: The usable date, or ``None``: undated (`flags` says why).
    date: NoteDate | None
    #: Every block of the segment, separators included, in ordinal order.
    ordinals: tuple[int, ...]
    paragraphs: tuple[str, ...]
    #: The file blocks nested under its first block (R9a), and their resource keys.
    direct_files: tuple[int, ...]
    file_keys: tuple[str, ...]
    #: The other file blocks inside it: page-level (D9).
    adjacent_files: tuple[int, ...]
    links: tuple[tuple[str, str], ...]
    flags: tuple[str, ...]
    #: The block it starts at.
    start: int
    #: R2a, R2b's «TRAILING», or "" for a «Järgmised tegevused» heading.
    form: str = ""


@dataclass(frozen=True)
class ParsedPage:
    notes: tuple[ParsedNote, ...]
    #: The text that is in no segment, in reading order.
    page_text: tuple[str, ...]
    #: Every file block that is not direct to a note: page-level (D9).
    page_files: tuple[int, ...]
    #: `DESKTOP` (2026-11-04 capture) or `LEGACY` (an earlier pilot capture), R0.
    shape: str


@dataclass
class _Segment:
    kind: str
    start: int | None
    date: NoteDate | None = None
    form: str = ""
    flags: list[str] = field(default_factory=list)
    ordinals: list[int] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    direct: list[int] = field(default_factory=list)
    adjacent: list[int] = field(default_factory=list)


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _day_of(value: Any) -> date | None:
    """The UTC date of a stored ISO time, or ``None``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "")
    try:
        return date.fromisoformat(text[:10]) if len(text) >= 10 else None
    except ValueError:
        return None


def parse_page(
    blocks: Sequence[Mapping[str, Any]],
    page_links: Any = (),
    *,
    page_modified: Any = None,
) -> ParsedPage:
    """Every segment of one page (R5), in reading order. Pure.

    Each outline is read on its own and a segment never crosses outlines; a
    floating block is page text. In one sibling list a segment starts at a text
    block with a leading date (R2a, dated or not), a trailing-date heading
    (R2b) or a «Järgmised tegevused» heading (R4), and continues over the
    following siblings — text, files, links — until an empty or rule line (R3),
    the next segment start or the end of the list. Every block nested under a
    block belongs to that block's segment, across empty lines inside the nested
    group; a nested segment start splits out on its own. Pages of the earlier
    capture shape have no empty lines, so there a note is its first block and
    what is nested under it, nothing more (R6). ``page_modified`` is that shape's
    stand-in for the block times R7 reads.
    """
    ordered = [block for block in blocks or () if isinstance(block, Mapping)]
    shape = "DESKTOP" if ordered and "outline" in ordered[0] else "LEGACY"
    by_ordinal: dict[int, Mapping[str, Any]] = {}
    ordinal_of: dict[int, int] = {}
    for index, block in enumerate(ordered):
        ordinal = _int(block.get("ordinal"))
        ordinal_of[id(block)] = ordinal if ordinal is not None else index
        by_ordinal.setdefault(ordinal_of[id(block)], block)

    parent: dict[int, int | None] = {}
    if shape == "DESKTOP":
        for block in ordered:
            parent[ordinal_of[id(block)]] = _int(block.get("parent"))
        _break_parent_cycles(parent, by_ordinal)
    else:
        stack: list[tuple[int, int]] = []
        for block in ordered:
            depth = _int(block.get("depth")) or 0
            while stack and stack[-1][0] >= depth:
                stack.pop()
            parent[ordinal_of[id(block)]] = stack[-1][1] if stack else None
            stack.append((depth, ordinal_of[id(block)]))

    def floating(block: Mapping[str, Any]) -> bool:
        return shape == "DESKTOP" and _int(block.get("outline")) == FLOATING

    children: dict[int, list[Mapping[str, Any]]] = {}
    roots: dict[int, list[Mapping[str, Any]]] = {}
    for block in ordered:
        if floating(block):
            continue
        above = parent[ordinal_of[id(block)]]
        if above is None or above not in by_ordinal:
            roots.setdefault(_int(block.get("outline")) or 0, []).append(block)
        else:
            children.setdefault(above, []).append(block)

    segments: list[_Segment] = []
    page = _Segment(kind="PAGE_TEXT", start=None)
    member: dict[int, _Segment] = {}

    def kind(block: Mapping[str, Any]) -> str:
        return str(block.get("kind") or "TEXT")

    def is_text(block: Mapping[str, Any]) -> bool:
        return kind(block) in TEXT_KINDS

    def separator(block: Mapping[str, Any]) -> bool:
        if kind(block) == "BLANK":
            return True
        if not is_text(block):
            return False
        value = normalise(block.get("text"))
        return not value or bool(_RULE_LINE.match(value))

    def in_next(segment: _Segment | None) -> bool:
        return segment is not None and segment.kind == NEXT_ACTIONS

    def last_touch(block: Mapping[str, Any]) -> date | None:
        times = [_day_of(block.get("created")), _day_of(block.get("modified"))]
        known = [value for value in times if value is not None]
        if not known and page_modified is not None:
            known = [value for value in [_day_of(page_modified)] if value is not None]
        return max(known) if known else None

    def open_note(block: Mapping[str, Any], found: NoteDate, within: _Segment | None) -> _Segment:
        segment = _Segment(
            kind=NEXT_ACTIONS if in_next(within) else NOTE,
            start=ordinal_of[id(block)],
            form=found.form,
        )
        if found.precision is None:
            segment.flags.append(found.reason)
        else:
            touched = last_touch(block)
            if touched is not None and (found.day - touched).days > WRITTEN_AHEAD_DAYS:
                segment.flags.append(DATE_AFTER_WRITING)  # R7
            else:
                segment.date = found
        if segment.date is None:
            segment.flags.append(UNDATED)
        segments.append(segment)
        return segment

    def add(segment: _Segment | None, block: Mapping[str, Any], found: NoteDate | None = None):
        target = segment if segment is not None else page
        ordinal = ordinal_of[id(block)]
        target.ordinals.append(ordinal)
        member[ordinal] = target
        if is_text(block):
            raw = block.get("text")
            target.texts.append(normalise(raw))
            if found is not None and target.date is found and found.stripped:
                # R8: the leading date and separator only. `rest` is already
                # decoded (`leading_date` read the normalised text): not twice.
                value = _collapse(found.rest)
            else:
                value = normalise(raw)
            if value:
                target.paragraphs.append(value)

    def group(siblings: list[Mapping[str, Any]], container: _Segment | None) -> None:
        """One sibling list, then each block's nested list right after it.

        Iterative, not recursive: a page nested deeper than Python's recursion
        limit is read like any other (the production pages nest at most four
        deep). Each frame is one sibling list: its blocks, the segment that
        contains the list, and the segment its next block continues.
        """
        frames: list[list[Any]] = [[iter(siblings), container, container]]
        while frames:
            frame = frames[-1]
            block = next(frame[0], None)
            if block is None:
                frames.pop()
                continue
            nested = step(block, frame[1], frame)
            ordinal = ordinal_of[id(block)]
            if ordinal in children:
                frames.append([iter(children[ordinal]), nested, nested])

    def step(block: Mapping[str, Any], container: _Segment | None, frame: list[Any]):
        """Place one block; return the segment its nested blocks belong to.

        ``frame[2]`` is the segment the next sibling continues, updated here.
        """
        current = frame[2]
        ordinal = ordinal_of[id(block)]
        if separator(block):
            frame[2] = container
            add(container, block)
            return container
        leading = leading_date(block.get("text")) if is_text(block) else None
        if leading is not None:
            current = open_note(block, leading, current if in_next(current) else container)
            add(current, block, leading)
        elif (
            TRAILING_DATES
            and is_text(block)
            and not in_next(current)
            and not is_next_actions_heading(block.get("text"))
            and (trailing := trailing_date(block.get("text"))) is not None
        ):
            current = open_note(block, trailing, container)
            add(current, block)
        elif is_text(block) and not in_next(current) and is_next_actions_heading(block.get("text")):
            current = _Segment(kind=NEXT_ACTIONS, start=ordinal, flags=[UNDATED])
            segments.append(current)
            add(current, block)
        else:
            add(current, block)
        nested = current
        if shape == "LEGACY" and current is not container and current is not None:
            if current.start == ordinal:
                current = container  # R6: no sibling continuation
        frame[2] = current
        return nested

    for outline in sorted(roots):
        group(roots[outline], None)
    for block in ordered:
        if floating(block):
            add(None, block)
    # Nothing is silently lost: a block the outline walk never reached — one
    # nested under a block that is itself in no outline list (a floating one,
    # say) — is page text, in reading order.
    for block in ordered:
        if ordinal_of[id(block)] not in member:
            add(None, block)

    # R9a: a file is direct to the segment whose start is its nearest
    # segment-start ancestor — if the file belongs to that segment.
    starts = {segment.start: segment for segment in segments}
    for block in ordered:
        if kind(block) not in FILE_KINDS:
            continue
        ordinal = ordinal_of[id(block)]
        owner: _Segment | None = None
        above = parent.get(ordinal)
        seen: set[int] = set()
        while above is not None and above not in seen:
            seen.add(above)
            if above in starts:
                if member.get(ordinal) is starts[above]:
                    owner = starts[above]
                break
            above = parent.get(above)
        if owner is not None:
            owner.direct.append(ordinal)
        else:
            member.get(ordinal, page).adjacent.append(ordinal)

    notes: list[ParsedNote] = []
    for segment in segments:
        inside = set(segment.ordinals)
        notes.append(
            ParsedNote(
                kind=segment.kind,
                date=segment.date,
                ordinals=tuple(sorted(segment.ordinals)),
                paragraphs=tuple(segment.paragraphs),
                direct_files=tuple(segment.direct),
                file_keys=tuple(
                    str(by_ordinal[ordinal].get("resource_key") or "") for ordinal in segment.direct
                ),
                adjacent_files=tuple(segment.adjacent),
                links=_note_links(
                    segment.paragraphs, page_links if shape == "LEGACY" else (), inside
                ),
                flags=tuple(segment.flags),
                start=segment.start if segment.start is not None else -1,
                form=segment.form,
            )
        )
    page_files = sorted(
        [*page.adjacent, *(ordinal for segment in segments for ordinal in segment.adjacent)]
    )
    return ParsedPage(
        notes=tuple(notes),
        page_text=tuple(page.paragraphs),
        page_files=tuple(page_files),
        shape=shape,
    )


def _break_parent_cycles(
    parent: dict[int, int | None], by_ordinal: Mapping[int, Mapping[str, Any]]
) -> None:
    """Make the stored parent links a forest, in place.

    A block that is its own parent, or one of a ring of blocks that are each
    other's ancestors, is in no outline list and would be read by nobody. The
    link that closes each ring is dropped, so that block becomes a root of its
    outline and the rest of the ring hangs under it. A well-formed page — every
    production page — is unchanged. Linear: every block is walked up once.
    """
    settled: set[int] = set()
    for start in list(parent):
        path: list[int] = []
        on_path: set[int] = set()
        node: int | None = start
        while node is not None and node in by_ordinal and node not in settled:
            if node in on_path:
                parent[path[-1]] = None  # the link back into the ring
                break
            path.append(node)
            on_path.add(node)
            node = parent.get(node)
        settled.update(path)


def _note_links(paragraphs: Iterable[str], page_links: Any, ordinals: set[int]) -> tuple:
    """R10: the addresses a note shows — written out in its text, and on an earlier
    capture's page its own link records — each once. Never an `onenote:` link."""
    found: dict[str, str] = {}
    for paragraph in paragraphs:
        for match in _URL.finditer(paragraph):
            shown = match.group(0).rstrip(_URL_TRAILING)
            href = (
                shown if shown.lower().startswith(("http://", "https://")) else f"https://{shown}"
            )
            found.setdefault(href, shown)
    for item in page_links or ():
        if not isinstance(item, Mapping):
            continue
        where = _int(item.get("blockOrdinal"))
        if where is None or where not in ordinals:
            continue
        href = str(item.get("href") or item.get("url") or "").strip()
        if not href.lower().startswith(("http://", "https://")):
            continue
        found.setdefault(href, normalise(item.get("text") or item.get("displayText")) or href)
    return tuple((text, href) for href, text in found.items())


def source_ref(capture_id: str, ordinals: Iterable[int]) -> str:
    """«<capture> · plokk 7» / «<capture> · plokid 0–4»: min–max of the segment (R8)."""
    values = sorted(set(ordinals))
    if not values:
        return capture_id
    if values[0] == values[-1]:
        return f"{capture_id} · plokk {values[0]}"
    return f"{capture_id} · plokid {values[0]}–{values[-1]}"


def files_headline(files: Sequence[HistoricalFile]) -> str:
    """«EELNÕU.pdf ja 3 muud faili»: the first file by name, and how many more."""
    if not files:
        return ""
    first = files[0].original_filename or files[0].title
    others = len(files) - 1
    if others == 0:
        return first
    if others == 1:
        return f"{first} ja 1 muu fail"
    return f"{first} ja {others} muud faili"


def structural_comment(provenance_note: str) -> str:
    """The file's own structural comment(s), verbatim, out of its provenance note.

    The capture import writes them as «Märkus OneNote'is: …» (`ATTACHED_NOTE`)
    and «Pealkiri OneNote'is: …» (`CONTAINER_HEADING`) lines, before the closing
    «Allikas: …» line (app/legacy_import/onenote_capture.py `provenance_note`).
    A comment that ran over several lines keeps them. Shown with the file, as
    Dokumendid shows it; never used to tie the file to anything.
    """
    labels = ("Märkus OneNote'is: ", "Pealkiri OneNote'is: ")
    comments: list[list[str]] = []
    current: list[str] | None = None
    for line in (provenance_note or "").split("\n"):
        label = next((label for label in labels if line.startswith(label)), None)
        if label is not None:
            current = [line[len(label) :]]
            comments.append(current)
            continue
        if line.startswith("Allikas: "):
            current = None
            continue
        if current is not None:
            current.append(line)
    return "\n".join("\n".join(lines).strip() for lines in comments if "".join(lines).strip())


# -- projecting (one page, through one link) -----------------------------------------


@dataclass(frozen=True)
class PageFile:
    """One imported file of the page, as this reader may see it through this link."""

    resource_key: str
    file: HistoricalFile
    version_id: Any
    #: What R9c matches on when the key names no block of this page.
    filename: str = ""
    added_at: Any = None


@dataclass(frozen=True)
class PageResource:
    """One catalogued file of the page (`LegacySourceResource`), for R9c."""

    resource_key: str
    filename: str
    added_at: Any


@dataclass(frozen=True)
class ProjectedNote:
    """A `HistoricalNoteRow` and what the chronology needs to place it."""

    row: HistoricalNoteRow
    date: NoteDate | None
    link_id: Any
    start: int
    #: The versions under this note, which therefore draw no document row.
    direct_versions: frozenset[Any] = field(default_factory=frozenset)


def block_keys_of(
    files: Sequence[PageFile],
    blocks: Sequence[Mapping[str, Any]],
    resources: Sequence[PageResource],
) -> dict[str, PageFile]:
    """Each imported file by the resource key of the block it is (R9c).

    A file whose own key names a file block of the stored page is that block.
    One that does not — a later capture's row on a page whose text is from an
    earlier capture — is matched to a block only through a *unique* catalogued
    resource of that page with the same filename and the same OneNote addition
    time, which is itself a file block's key; otherwise it is page-level. The
    stored block ordinal is never read: a later capture does not keep it in step.
    """
    on_page = {
        str(block.get("resource_key") or "")
        for block in blocks
        if isinstance(block, Mapping) and str(block.get("kind") or "") in FILE_KINDS
    }
    on_page.discard("")
    out: dict[str, PageFile] = {}
    unplaced: list[PageFile] = []
    for item in files:
        if item.resource_key in on_page and item.resource_key not in out:
            out[item.resource_key] = item
        else:
            unplaced.append(item)
    for item in unplaced:
        if item.added_at is None or not item.filename:
            continue
        candidates = [
            resource.resource_key
            for resource in resources
            if resource.resource_key in on_page
            and resource.filename == item.filename
            and resource.added_at is not None
            and resource.added_at == item.added_at
        ]
        if len(candidates) == 1 and candidates[0] not in out:
            out[candidates[0]] = item
    return out


def project_page(
    *,
    link_id: Any,
    page_url: str,
    page_title: str,
    section: str,
    capture_id: str,
    blocks: Sequence[Mapping[str, Any]],
    page_links: Any,
    files: Sequence[PageFile],
    resources: Sequence[PageResource] = (),
    page_modified: Any = None,
    withheld: frozenset[Any] = frozenset(),
    shared_page: bool = False,
) -> tuple[ProjectedNote, ...]:
    """Every segment of one page, for one reader, through one Matter's link.

    ``files`` are the page's imported files this reader may see on this link —
    nothing else is named, counted or linked. ``withheld`` are versions that read
    on a record of their own and so are not repeated under a note.
    """
    parsed = parse_page(blocks, page_links, page_modified=page_modified)
    by_key = block_keys_of(files, blocks, resources)
    taken: set[Any] = set()
    direct: dict[int, tuple[PageFile, ...]] = {}
    for note in parsed.notes:
        mine = tuple(
            by_key[key]
            for key in note.file_keys
            if key in by_key
            and by_key[key].version_id not in taken
            and by_key[key].version_id not in withheld
        )
        taken.update(item.version_id for item in mine)
        direct[note.start] = mine
    page_files = sum(1 for item in files if item.version_id not in taken)
    # A stored ordinal anchors a block only when no other block has it (UI-13):
    # the source page gives that block `id="plokk-<ordinal>"`.
    counts: dict[int, int] = {}
    for block in blocks:
        if isinstance(block, Mapping) and (ordinal := _int(block.get("ordinal"))) is not None:
            counts[ordinal] = counts.get(ordinal, 0) + 1
    out: list[ProjectedNote] = []
    for note in parsed.notes:
        mine = direct[note.start]
        text = " ".join(note.paragraphs)
        row = HistoricalNoteRow(
            date_display=note.date.display if note.date is not None else "",
            precision=note.date.precision if note.date is not None else None,
            headline=headline(text) if text else files_headline([item.file for item in mine]),
            paragraphs=note.paragraphs,
            kind=note.kind,
            links=note.links,
            direct_files=tuple(item.file for item in mine),
            page_files_count=page_files,
            page_url=page_url,
            page_title=page_title,
            section=section,
            source_ref=source_ref(capture_id, note.ordinals),
            date_iso=note.date.iso if note.date is not None else "",
            anchor=f"plokk-{note.start}" if counts.get(note.start) == 1 else "",
            shared_page=shared_page,
        )
        out.append(
            ProjectedNote(
                row=row,
                date=note.date,
                link_id=link_id,
                start=note.start,
                direct_versions=frozenset(item.version_id for item in mine),
            )
        )
    return tuple(out)


def matter_notes(
    *,
    matter: Any,
    user: Any,
    files: Mapping[Any, Sequence[PageFile]],
    withheld: frozenset[Any] = frozenset(),
) -> tuple[ProjectedNote, ...]:
    """Every segment on the pages linked to ``matter`` that ``user`` may open.

    One query for the pages, and one more only when an imported file's key names
    no block of its page (R9c).

    **The page view's own door** (`historical_views.visible_links`): a link this
    reader could not open at `/ajalugu/<link>/` contributes nothing — no row, no
    text, no count. A deleted Teema's links are not a way in. A page linked to
    several Teemad is read through *this* Teema's link only, so it projects once
    per Teema and each row's page link is the one this reader came by.

    ``files`` are this Matter's imported files by link, already narrowed to what
    the reader may see (`Document.visible_to`): the caller reads them once for
    the whole chronology (`app.matters.timeline.imported_document_versions`).
    """
    from django.db.models import Count
    from django.urls import reverse

    from app.legacy_import.historical_views import visible_links
    from app.legacy_import.source_pages import LegacySourceResource, MatterSourcePage

    links = list(
        visible_links(user)
        .filter(matter=matter)
        .select_related(None)
        .select_related("source_page")
        .only(
            "pk",
            "matter_id",
            "source_page__id",
            "source_page__title",
            "source_page__source_section",
            "source_page__capture_id",
            "source_page__source_modified_at",
            "source_page__blocks",
            "source_page__links",
        )
        .order_by("pk")
    )
    unplaced_pages = set()
    for link in links:
        keys = {
            str(block.get("resource_key") or "")
            for block in link.source_page.blocks or []
            if isinstance(block, Mapping)
        }
        if any(item.resource_key not in keys for item in files.get(link.pk, ())):
            unplaced_pages.add(link.source_page_id)
    # How many live Teemad each page is linked to, readable or not: the rule
    # `SharedDossier.shared` reads (docs/adr/0208). One query for every page.
    shared = set(
        MatterSourcePage.objects.filter(
            source_page_id__in={link.source_page_id for link in links},
            matter__deleted_at__isnull=True,
        )
        .values("source_page_id")
        .annotate(teemad=Count("matter_id", distinct=True))
        .filter(teemad__gt=1)
        .values_list("source_page_id", flat=True)
    )
    resources: dict[Any, list[PageResource]] = {}
    if unplaced_pages:
        for page_id, key, filename, added_at in LegacySourceResource.objects.filter(
            source_page_id__in=unplaced_pages
        ).values_list("source_page_id", "resource_key", "original_filename", "source_added_at"):
            resources.setdefault(page_id, []).append(PageResource(key, filename, added_at))
    out: list[ProjectedNote] = []
    for link in links:
        page = link.source_page
        out.extend(
            project_page(
                link_id=link.pk,
                page_url=reverse("legacy_import:source_page", kwargs={"pk": link.pk}),
                page_title=page.title or "",
                section=page.source_section or "",
                capture_id=page.capture_id or "",
                blocks=page.blocks or [],
                page_links=page.links or [],
                files=files.get(link.pk, ()),
                resources=resources.get(page.pk, ()),
                page_modified=page.source_modified_at,
                withheld=withheld,
                shared_page=page.pk in shared,
            )
        )
    return tuple(out)
