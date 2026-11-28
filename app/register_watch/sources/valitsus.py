"""The Government's commented session agenda on valitsus.ee (ADR 0219).

Every week the Government Office publishes «Valitsuse <date> istungi kommenteeritud
päevakord» as a news page, usually on the eve of the Thursday session.

**Finding it** (r95, ADR 0219 amendment 2026-11-26b). The page's address is not
predictable: it is ``/uudised/valitsuse-<date>-istungi-kommenteeritud-paevakord``,
where ``<date>`` has been ``191126``, ``020726`` (zero-padded), ``2805`` (no year) and
``7-mai-2026``, and Drupal appends ``-0``, ``-1`` … when an earlier page took the
address. On 25.11.2026 an unpublished page held the plain address (403) and the
published agenda went out at ``…-paevakord-0``; r94 guessed only the plain one and
reported the agenda as not yet published. So a run first reads the links the site
itself publishes (`discover`):

* the homepage, which links the newest agendas while they are news;
* the sitemap (``/sitemap.xml``, Drupal's Simple XML Sitemap): its newest page lists
  every published news page, the agenda among them, with no unpublished one.

The site's own search («Kommenteeritud päevakord» on the homepage) answers through a
separate service that serves only browser pages of valitsus.ee; it is not used.
Addresses built from the session date, with ``-0`` and ``-1``, are only a bounded
fallback after the discovered links (`candidate_paths`). Whatever answers, the page is
believed only when `parse` finds a commented agenda for that session in it: its
title, its session date, a publication time, and items numbered 1…N.

Each item is one paragraph that opens with ``<strong>N. Title</strong>``, then
``Esitaja: …`` (or ``Esitajad: …``) and ``Tüüp: …``, followed by the Government's
explanation in the next paragraphs, until the next numbered title. robots.txt allows
``/uudised/`` and the sitemap.

**Telling silence from failure.** A guessed address answering 403 or 404 proves
nothing about publication. A 403 is told apart by one control read of the newest
stored agenda, itself parsed and matched to its session date (`site_answers`); a
page that is not that agenda — a maintenance page, a challenge, anything — is no
control. «Not yet published» is said only when the discovery pages were read and
list nothing for the day; when they could not be read, publication is unconfirmed.

**Deadlines** (SEC-010, ADR 0231). Every read goes through `sources.http`, so the
nightly step's whole-call deadline bounds it. A read stopped by a deadline
(`http.DeadlineExceeded`) is a source failure like a timeout. Once the run's own
deadline has passed, discovery stops (the listings it did not reach count as
unread) and a lookup ends at once as SOURCE_ERROR — never «not yet published»,
never «not found».
"""

from __future__ import annotations

import datetime as dt
import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.register_watch.sources import http
from app.register_watch.sources.http import DeadlineExceeded, FetchError, get_text

HOST = "valitsus.ee"
MONTHS = (
    "jaanuar veebruar marts aprill mai juuni juuli august september oktoober november detsember"
).split()
HOMEPAGE = "/"
SITEMAP = "/sitemap.xml"
#: Drupal's suffixes for a taken address, tried after the plain one (fallback only).
SUFFIXES = ("", "-0", "-1")


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


def date_tokens(day: dt.date) -> set[str]:
    """Every ``<date>`` an agenda address for ``day`` has been seen with (module docstring)."""
    d, dd, m, mm, yy = (
        str(day.day),
        f"{day.day:02d}",
        str(day.month),
        f"{day.month:02d}",
        f"{day.year % 100:02d}",
    )
    return {
        f"{d}{m}{yy}",
        f"{dd}{mm}{yy}",
        f"{d}{mm}{yy}",
        f"{dd}{m}{yy}",
        f"{d}{m}",
        f"{dd}{mm}",
        f"{d}-{MONTHS[day.month - 1]}-{day.year}",
    }


def _base(token: str) -> str:
    return f"/uudised/valitsuse-{token}-istungi-kommenteeritud-paevakord"


def candidate_paths(day: dt.date) -> list[str]:
    """The fallback addresses for a session on ``day``: three date shapes, each plain,
    ``-0`` and ``-1`` (bounded; tried only after the discovered links)."""
    d, m, yy = day.day, day.month, day.year % 100
    shapes = dict.fromkeys(
        (f"{d}{m}{yy:02d}", f"{d:02d}{m:02d}{yy:02d}", f"{d}-{MONTHS[m - 1]}-{day.year}")
    )
    return [f"{_base(token)}{suffix}" for token in shapes for suffix in SUFFIXES]


#: An agenda link on a valitsus.ee page or in its sitemap: the path, then its date token.
#: Anchored on a quote, ``>`` or the site's own origin, so ``/en/uudised/…`` and another
#: host's link never match.
_LINK = re.compile(
    r"""(?:["'>]|https?://(?:www\.)?valitsus\.ee)"""
    r"(/uudised/valitsuse-([a-z0-9-]{2,40}?)-istungi-kommenteeritud-paevakord(?:-\d{1,2})?)"
    r"""(?=["'<?#\s])"""
)
_SITEMAP_PAGE = re.compile(
    r"<loc>\s*https?://(?:www\.)?valitsus\.ee/sitemap\.xml\?page=(\d{1,4})\s*</loc>"
)
_URL_ENTRIES = re.compile(r"<url>")


def agenda_links(page: str) -> list[tuple[str, str]]:
    """``(path, date token)`` of every agenda link on ``page``, first seen first."""
    seen: dict[str, str] = {}
    for path, token in _LINK.findall(page):
        seen.setdefault(path, token)
    return list(seen.items())


@dataclass
class Discovery:
    """What the site's own pages listed this run (`discover`)."""

    links: list[tuple[str, str]] = field(default_factory=list)
    #: The pages read, e.g. ``["/", "/sitemap.xml?page=17"]``.
    read: list[str] = field(default_factory=list)
    #: ``(path, error)`` of every discovery page that could not be read.
    failures: list[tuple[str, FetchError]] = field(default_factory=list)
    #: The run's deadline stopped discovery (SEC-010): what was not read is unread.
    out_of_time: bool = False

    @property
    def ok(self) -> bool:
        """The listing that matters was read: the sitemap's newest page. The homepage
        alone does not list an agenda once it has left the front page."""
        return any(path.startswith(SITEMAP) for path in self.read)

    def for_day(self, day: dt.date) -> list[str]:
        tokens = date_tokens(day)
        return [path for path, token in self.links if token in tokens]

    def summary(self) -> str:
        parts = [f"{path} loetud" for path in self.read]
        parts += [
            f"{path} {('HTTP ' + str(error.status)) if error.status else 'viga'}"
            for path, error in self.failures
        ]
        return "; ".join(parts)[:200]


def _read(path: str, found: Discovery) -> str | None:
    if found.out_of_time:
        return None  # the deadline stopped discovery: the rest is unread, not tried
    try:
        http.check_deadline(f"{HOST} päevakorra otsing")
        page = get_text(HOST, path)
    except FetchError as error:
        found.failures.append((path, error))
        if isinstance(error, DeadlineExceeded) and http.out_of_time():
            found.out_of_time = True
        return None
    found.read.append(path)
    return page


def discover() -> Discovery:
    """Read the homepage and the sitemap's newest page (and the one before it, when the
    newest has only just begun) for agenda links. At most four requests."""
    found = Discovery()
    links: dict[str, str] = {}
    page = _read(HOMEPAGE, found)
    if page is not None:
        links.update(agenda_links(page))
    index = _read(SITEMAP, found)
    if index is not None:
        # The index itself is not the listing: only its pages are (`Discovery.ok`).
        found.read.remove(SITEMAP)
        pages = sorted({int(n) for n in _SITEMAP_PAGE.findall(index)})
        if not pages:
            found.failures.append(
                (SITEMAP, FetchError(f"{HOST} sisukaardi registrist ei leitud ühtki lehte"))
            )
        for number in reversed(pages[-2:]):
            listing = _read(f"{SITEMAP}?page={number}", found)
            if listing is None:
                break
            links.update(agenda_links(listing))
            if len(_URL_ENTRIES.findall(listing)) >= 300:
                break  # a full page: the newest agendas are on it
    found.links = list(links.items())
    return found


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
_PRESENTER = ("Esitajad:", "Esitaja:")


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
    """The agenda on one page. A page that is not a commented agenda is a `FetchError`.

    An HTTP 200 proves nothing by itself: the page must carry the agenda's title with
    a session date, an article, and items numbered 1, 2, … N, each with a title."""
    head = _TITLE.search(page)
    title = _text(head.group(1)) if head else ""
    if "kommenteeritud päevakord" not in title.casefold():
        raise FetchError(f"{HOST} leht ei ole kommenteeritud päevakord")
    session = _session_date(title)
    if session is None:
        raise FetchError(f"{HOST} päevakorra pealkirjast ei selgu istungi kuupäev")
    if "<article" not in page:
        raise FetchError(f"{HOST} päevakorra lehel ei ole artiklit")
    published = None
    m = _PUBLISHED.search(page)
    if m:
        d, mo, y, h, mi = (int(x) for x in m.groups())
        try:
            published = dt.datetime(y, mo, d, h, mi)
        except ValueError:
            published = None
    article = page[page.find("<article") :]
    items: list[AgendaItem] = []

    def label(item: AgendaItem, line: str) -> bool:
        """«Esitaja(d): …» / «Tüüp: …» on its own line or paragraph; True when it was one."""
        for prefix in _PRESENTER:
            if line.startswith(prefix) and not item.presenter:
                item.presenter = line.removeprefix(prefix).strip()[:300]
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
    numbers = [item.number for item in items]
    if numbers != list(range(1, len(items) + 1)):
        raise FetchError(f"{HOST} päevakorra punktide numbrid ei ole järjest ({numbers[:30]})")
    if any(not item.title for item in items):
        raise FetchError(f"{HOST} päevakorras on pealkirjata punkt")
    return Agenda(
        url=url, title=title[:300], session_date=session, published=published, items=items
    )


#: What one session day's lookup found (`look`).
FOUND = "FOUND"
#: Nothing for the day: the site's listings were read and name no agenda, and every
#: fallback address answered 404. Not yet discovered.
NOT_FOUND = "NOT_FOUND"
#: The listings name nothing for a session still to come, a fallback address answered
#: 403 and the control agenda was read: an unpublished page — a likely publication
#: delay, not a failure.
NOT_PUBLISHED = "NOT_PUBLISHED"
#: An address the site lists for the day, or one for a session already past, answers
#: 403 while the control is read: the expected page is inaccessible, worth a look.
INACCESSIBLE = "INACCESSIBLE"
#: The listings could not be read, or a 403 could not be told apart (control
#: unreadable): no evidence either way — never a clean check.
UNCONFIRMED = "UNCONFIRMED"
#: The control agenda was refused (403) as well: the site refuses this reader.
ACCESS_REFUSED = "ACCESS_REFUSED"
#: 429, 5xx, a timeout, a refused connection, reading switched off.
SOURCE_ERROR = "SOURCE_ERROR"
#: A page the site lists for the day is not readable as that session's agenda.
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
    #: Failures met on the way, kept even when the agenda was found in the end, so a
    #: stored agenda never hides another failure of the same run.
    problems: list[str] = field(default_factory=list)


def site_answers(path: str, session: dt.date | None = None) -> bool | None:
    """The control: True when valitsus.ee serves ``path`` as the commented agenda of
    ``session``, False when it refuses it (403), None when it cannot be told — any
    other failure, and any page that is not that agenda (maintenance page,
    challenge, unrelated HTML answering 200)."""
    try:
        page = get_text(HOST, path)
    except FetchError as error:
        return False if error.status == 403 else None
    try:
        agenda = parse(f"https://{HOST}{path}", page)
    except FetchError:
        return None
    if session is not None and agenda.session_date != session:
        return None
    return True


def look(
    day: dt.date,
    *,
    today: dt.date,
    discovery: Discovery | None = None,
    control: Callable[[], bool | None] | None = None,
) -> Lookup:
    """The agenda of a session on ``day``, or why there is none (module docstring).

    The addresses the site listed for the day come first, then the fallback ones;
    the first page that parses as this session's agenda is the answer. ``control``
    answers `site_answers` for a page known to be published (the caller memoises it:
    one control read per run)."""
    discovery = discovery if discovery is not None else Discovery()
    listed = discovery.for_day(day)
    paths = list(dict.fromkeys([*listed, *candidate_paths(day)]))
    refused: list[str] = []
    errors: list[tuple[str, FetchError]] = []
    unreadable: list[tuple[str, str]] = []
    problems: list[str] = []
    for path in paths:
        url = f"https://{HOST}{path}"
        try:
            page = get_text(HOST, path)
        except FetchError as error:
            if isinstance(error, DeadlineExceeded) and http.out_of_time():
                # The run's time is spent (SEC-010): nothing after this can be read,
                # and what was refused or missing so far proves nothing. A source
                # failure, whatever the other addresses said. (One slow call cut at
                # `CALL_SECONDS` with time left is an ordinary error below.)
                problems.append(f"{HOST}{path}: {str(error)[:160]}")
                return Lookup(
                    day,
                    SOURCE_ERROR,
                    url=url,
                    detail=f"{HOST}{path}: {error!s}"[:300],
                    problems=problems,
                )
            if error.status == 404:
                continue
            if error.status == 403:
                refused.append(path)
                continue
            errors.append((path, error))
            problems.append(f"{HOST}{path}: {str(error)[:160]}")
            continue
        try:
            agenda = parse(url, page)
        except FetchError as error:
            if path in listed:
                unreadable.append((path, str(error)))
                problems.append(f"{HOST}{path}: {str(error)[:160]}")
            continue  # an unrelated page at a guessed address proves nothing
        if agenda.session_date != day:
            if path in listed:
                other = f"teise istungi päevakord ({agenda.session_date})"
                unreadable.append((path, other))
                problems.append(f"{HOST}{path}: {other}")
            continue
        return Lookup(day, FOUND, agenda=agenda, url=url, problems=problems)
    if unreadable:
        path, why = unreadable[0]
        return Lookup(
            day,
            PARSE_ERROR,
            url=f"https://{HOST}{path}",
            detail=f"{HOST}{path}: {why}"[:300],
            problems=problems,
        )
    if errors:
        path, first_error = errors[0]
        return Lookup(
            day,
            SOURCE_ERROR,
            status=first_error.status,
            url=f"https://{HOST}{path}",
            detail=str(first_error)[:300],
            problems=problems,
        )
    if refused:
        listed_refused = [path for path in refused if path in listed]
        first = listed_refused[0] if listed_refused else refused[0]
        url = f"https://{HOST}{first}"
        served = control() if control is not None else None
        if served is False:
            return Lookup(
                day,
                ACCESS_REFUSED,
                status=403,
                url=url,
                detail=f"{HOST} vastas 403 ka avaldatud päevakorrale: juurdepääs keelatud",
                problems=problems,
            )
        if served is None:
            return Lookup(
                day,
                UNCONFIRMED,
                status=403,
                url=url,
                detail=(
                    f"{HOST} vastas 403; avaldamine kinnitamata (kontrolllehte ei saanud lugeda)"
                ),
                problems=problems,
            )
        if listed_refused or day < today:
            return Lookup(
                day,
                INACCESSIBLE,
                status=403,
                url=url,
                detail=f"{HOST} vastas 403 oodatud päevakorrale: ajutiselt kättesaamatu",
                problems=problems,
            )
        if not discovery.ok:
            return Lookup(
                day,
                UNCONFIRMED,
                status=403,
                url=url,
                detail=f"{HOST} vastas 403 ja sisukaarti ei saanud lugeda: avaldamine kinnitamata",
                problems=problems,
            )
        return Lookup(
            day,
            NOT_PUBLISHED,
            status=403,
            url=url,
            detail=f"{HOST} vastas 403 ja sisukaardis seda veel ei ole: tõenäoliselt avaldamata",
            problems=problems,
        )
    if not discovery.ok:
        return Lookup(
            day,
            UNCONFIRMED,
            detail=f"{HOST} sisukaarti ei saanud lugeda: avaldamine kinnitamata",
            problems=problems,
        )
    return Lookup(day, NOT_FOUND, problems=problems)
