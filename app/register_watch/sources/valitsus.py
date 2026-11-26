"""The Government's commented session agenda on valitsus.ee (ADR 0219).

Every week the Government Office publishes «Valitsuse <date> istungi kommenteeritud
päevakord» as a news page, usually on the eve of the Thursday session. The page has
no feed and the site's search answers through script, but the address follows the
session date: ``/uudised/valitsuse-191126-istungi-kommenteeritud-paevakord`` for
19.11.26 (day and month without leading zeros, two-digit year), sometimes
``valitsuse-7-mai-2026-…``. `candidate_paths` lists both shapes for the days a session
can fall on; `parse` reads the page.

Each item is one paragraph that opens with ``<strong>N. Title</strong>``, then
``Esitaja: …`` and ``Tüüp: …``, followed by the Government's explanation in the next
paragraphs, until the next numbered title. robots.txt allows ``/uudised/``.

**Before publication** (r95, 26.11.2026). The page for the coming session can exist
before it is public: valitsus.ee then answers **403** at its address, while an
address with no page answers 404 and a published agenda 200 (seen on 25–26.11.2026
for 26.11.26). A 403 alone does not say which it is — a site refusing us everything
answers 403 too — so `look` asks one control page, the newest agenda already
stored: when the site serves that, the 403 is this page's alone, and for a session
still to come it is «not yet published». When the control is refused too, it is
access refused; when the control cannot be read at all, publication is unconfirmed.
Neither of those is ever reported as a successful check.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.register_watch.sources.http import FetchError, get_text

HOST = "valitsus.ee"
MONTHS = (
    "jaanuar veebruar marts aprill mai juuni juuli august september oktoober november detsember"
).split()


@dataclass
class AgendaItem:
    number: int
    title: str
    presenter: str = ""
    kind: str = ""
    text: str = ""


@dataclass
class Agenda:
    url: str
    title: str
    session_date: dt.date
    published: dt.datetime | None
    items: list[AgendaItem] = field(default_factory=list)


def candidate_paths(day: dt.date) -> list[str]:
    """The addresses an agenda for a session on ``day`` is published under."""
    short = f"{day.day}{day.month}{day.year % 100:02d}"
    long = f"{day.day}-{MONTHS[day.month - 1]}-{day.year}"
    return [
        f"/uudised/valitsuse-{short}-istungi-kommenteeritud-paevakord",
        f"/uudised/valitsuse-{long}-istungi-kommenteeritud-paevakord",
    ]


_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_PUBLISHED = re.compile(
    r'class="[^"]*vp-date[^"]*">\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*\|\s*(\d{1,2}):(\d{2})'
)
_SESSION = re.compile(r"Valitsuse\s+(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{2,4})\s+istungi", re.I)
_SESSION_LONG = re.compile(
    r"Valitsuse\s+(\d{1,2})\.\s+([a-zõäöü]+)\s+(\d{4})\.?\s+(?:a\.\s+)?istungi", re.I
)
_PARAGRAPH = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_HEAD = re.compile(r"^\s*<strong>\s*(\d{1,3})\.\s*(.*?)</strong>(.*)$", re.S)
_PLAIN_HEAD = re.compile(r"^(\d{1,3})\.\s+(\S.*)$")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment.replace("<br>", "\n"))).split())


def _session_date(title: str) -> dt.date | None:
    m = _SESSION.search(title)
    if m:
        year = int(m.group(3))
        year = year + 2000 if year < 100 else year
        try:
            return dt.date(year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    m = _SESSION_LONG.search(title)
    if m and m.group(2).lower() in MONTHS:
        try:
            return dt.date(int(m.group(3)), MONTHS.index(m.group(2).lower()) + 1, int(m.group(1)))
        except ValueError:
            return None
    return None


def parse(url: str, page: str) -> Agenda:
    """The agenda on one page. A page that is not a commented agenda is a `FetchError`."""
    head = _TITLE.search(page)
    title = _text(head.group(1)) if head else ""
    if "kommenteeritud päevakord" not in title.casefold():
        raise FetchError(f"{HOST} leht ei ole kommenteeritud päevakord")
    session = _session_date(title)
    if session is None:
        raise FetchError(f"{HOST} päevakorra pealkirjast ei selgu istungi kuupäev")
    published = None
    m = _PUBLISHED.search(page)
    if m:
        d, mo, y, h, mi = (int(x) for x in m.groups())
        published = dt.datetime(y, mo, d, h, mi)
    article = page[page.find("<article") :] if "<article" in page else page
    items: list[AgendaItem] = []

    def label(item: AgendaItem, line: str) -> bool:
        """«Esitaja: …» / «Tüüp: …» on its own line or paragraph; True when it was one."""
        if line.startswith("Esitaja:") and not item.presenter:
            item.presenter = line.removeprefix("Esitaja:").strip()[:300]
            return True
        if line.startswith("Tüüp:") and not item.kind:
            item.kind = line.removeprefix("Tüüp:").strip()[:120]
            return True
        return False

    for raw in _PARAGRAPH.findall(article):
        found = _HEAD.match(raw)
        if found:
            item = AgendaItem(number=int(found.group(1)), title=_text(found.group(2)))
            for part in found.group(3).split("<br>"):
                label(item, _text(part))
            items.append(item)
            continue
        line = _text(raw)
        # Some items are written without bold: a plain «10. Title» paragraph, then
        # «Esitaja:» and «Tüüp:» each in a paragraph of their own (19.11.26, item 10).
        plain = _PLAIN_HEAD.match(line)
        expected = items[-1].number + 1 if items else 1
        if plain and int(plain.group(1)) == expected:
            items.append(AgendaItem(number=expected, title=plain.group(2).strip()))
            continue
        if items and line:
            if label(items[-1], line):
                continue
            items[-1].text = f"{items[-1].text}\n\n{line}".strip()
    if not items:
        raise FetchError(f"{HOST} päevakorrast ei leitud ühtki punkti")
    return Agenda(
        url=url, title=title[:300], session_date=session, published=published, items=items
    )


#: What one session day's lookup found (`look`).
FOUND = "FOUND"
#: Every address answered 404: no page for that day (yet).
NOT_FOUND = "NOT_FOUND"
#: The expected address answered 403, the control agenda 200, the session is still to
#: come: the page exists and is not published yet.
NOT_PUBLISHED = "NOT_PUBLISHED"
#: As NOT_PUBLISHED, but for a session day already past: the expected page exists and
#: is not readable — temporarily inaccessible, worth a look.
INACCESSIBLE = "INACCESSIBLE"
#: The expected address answered 403 and the control could not be read: no evidence
#: either way.
UNCONFIRMED = "UNCONFIRMED"
#: The control agenda was refused (403) as well: the site refuses this reader.
ACCESS_REFUSED = "ACCESS_REFUSED"
#: 429, 5xx, a timeout, a refused connection, reading switched off.
SOURCE_ERROR = "SOURCE_ERROR"
#: A page came back that is not this session's commented agenda.
PARSE_ERROR = "PARSE_ERROR"


@dataclass
class Lookup:
    """One session day: what was found, with the evidence (status, address)."""

    day: dt.date
    state: str
    agenda: Agenda | None = None
    status: int | None = None
    url: str = ""
    detail: str = ""


def site_answers(path: str) -> bool | None:
    """The control: True when valitsus.ee serves ``path``, False when it refuses it
    (403), None when it cannot be told (any other failure)."""
    try:
        get_text(HOST, path)
    except FetchError as error:
        return False if error.status == 403 else None
    return True


def look(
    day: dt.date,
    *,
    today: dt.date,
    control: Callable[[], bool | None] | None = None,
) -> Lookup:
    """The agenda of a session on ``day``, or why there is none (module docstring).

    ``control`` answers `site_answers` for a page known to be published (the caller
    memoises it: one control read per run). Both address shapes are tried: a 403 on
    the first is not the end, the second may be the published one.
    """
    refused = ""
    for path in candidate_paths(day):
        url = f"https://{HOST}{path}"
        try:
            page = get_text(HOST, path)
        except FetchError as error:
            if error.status == 404:
                continue
            if error.status == 403:
                refused = refused or url
                continue
            return Lookup(day, SOURCE_ERROR, status=error.status, url=url, detail=str(error))
        try:
            agenda = parse(url, page)
        except FetchError as error:
            return Lookup(day, PARSE_ERROR, url=url, detail=str(error))
        if agenda.session_date != day:
            return Lookup(
                day,
                PARSE_ERROR,
                url=url,
                detail=f"{HOST}{path} on teise istungi päevakord ({agenda.session_date})",
            )
        return Lookup(day, FOUND, agenda=agenda, url=url)
    if not refused:
        return Lookup(day, NOT_FOUND)
    served = control() if control is not None else None
    if served is False:
        return Lookup(
            day,
            ACCESS_REFUSED,
            status=403,
            url=refused,
            detail=f"{HOST} vastas 403 ka avaldatud päevakorrale: juurdepääs keelatud",
        )
    if served is None:
        return Lookup(
            day,
            UNCONFIRMED,
            status=403,
            url=refused,
            detail=f"{HOST} vastas 403; avaldamine kinnitamata (kontrolllehte ei saanud lugeda)",
        )
    if day >= today:
        return Lookup(
            day,
            NOT_PUBLISHED,
            status=403,
            url=refused,
            detail=f"{HOST} vastas 403: päevakord on olemas, kuid veel avaldamata",
        )
    return Lookup(
        day,
        INACCESSIBLE,
        status=403,
        url=refused,
        detail=f"{HOST} vastas 403 möödunud istungi päevakorrale: ajutiselt kättesaamatu",
    )
