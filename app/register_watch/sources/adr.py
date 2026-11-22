"""RIK's public document register (ADR) for six ministries, read through its search form.

Justice and Digital (`jm`), Economic Affairs (`mkm`), Finance (`ram`), Social
Affairs (`som`), Culture (`kum`) and Defence (`kaitseministeerium`) publish
their registers on `adr.rik.ee/<code>/` (the Government Office's instance there
is empty: no document types, nothing found — not read). There
is no API and no feed: the advanced search is an HTML form posted in a
session, answered with a results table, newest first, twenty to a page. It
matches whole words, case-insensitively, and all of them (checked
2026-11-17). Documents are public at `/<code>/dokument/<id>`.

Read the first page from a start date; anything beyond it waits for the next
night. A page that does not look like a results page is a `FetchError`, never
an empty answer, so a change of layout shows up as an error in the run.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from dataclasses import dataclass

from app.register_watch.sources.http import FetchError, get_text, post_form
from app.register_watch.sources.items import Item

HOST = "adr.rik.ee"


@dataclass(frozen=True)
class Register:
    code: str

    @property
    def search_path(self) -> str:
        return f"/{self.code}/otsing"

    def document_url(self, number: str) -> str:
        return f"https://{HOST}/{self.code}/dokument/{number}"


JDM = Register("jm")
MKM = Register("mkm")
RAM = Register("ram")
SOM = Register("som")
KUM = Register("kum")
KAM = Register("kaitseministeerium")

_opened: set[str] = set()
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG = re.compile(r"<[^>]+>")


def _clean(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def _day(text: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(text.strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


def parse(register: Register, page: str) -> list[Item]:
    """The rows of one results page."""
    if "Kokku:" not in page or "<table" not in page:
        raise FetchError(f"{HOST}/{register.code} vastus ei ole tulemuste leht")
    link = re.compile(rf'href="/{re.escape(register.code)}/dokument/(\d+)"')
    items: list[Item] = []
    for row in _ROW.findall(page):
        found = link.search(row)
        cells = [_clean(cell) for cell in _CELL.findall(row)]
        if not found or len(cells) < 5:
            continue
        number, day, title, kind, party = cells[0], cells[1], cells[2], cells[3], cells[4]
        if not title:
            continue
        items.append(
            Item(
                external_id=f"adr:{register.code}:{found.group(1)}",
                title=title[:500],
                occurred_on=_day(day),
                url=register.document_url(found.group(1)),
                detail=" · ".join(part for part in (number, kind, party) if part)[:300],
            )
        )
    return items


def search(register: Register, words: str, *, since: dt.date, page_number: int = 1) -> list[Item]:
    """Documents registered since ``since`` whose title has every word in ``words``."""
    if register.code not in _opened:
        get_text(HOST, register.search_path)
        _opened.add(register.code)
    page = post_form(
        HOST,
        register.search_path,
        {
            "title": words,
            "regDateBegin": since.strftime("%d.%m.%Y"),
            "pageNumber": str(page_number),
        },
    )
    return parse(register, page)


#: Results to a page, as the register serves them.
PAGE_SIZE = 20


def search_all(register: Register, words: str, *, since: dt.date, max_pages: int = 3) -> list[Item]:
    """`search`, page after page while pages come back full (the sweep, ADR 0218 §1)."""
    found: list[Item] = []
    for number in range(1, max_pages + 1):
        rows = search(register, words, since=since, page_number=number)
        found.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
    return found
