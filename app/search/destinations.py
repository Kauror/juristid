"""The application's own pages and settings, for the header search (owner, 2026-11-23).

The header search found Teemad only, so «tume», «juhatuse ülevaade» or
«teavitused» answered nothing although the app has a page or a switch for each.
This is the short catalogue of those destinations, shown in the dropdown as a
«Rakendus» group above the Teema rows.

**Each entry is offered only to a reader who may open it.** The gate is the
same predicate the page or its link already uses — `may_see` for Monitooring,
the Haldus section registry for Haldus, `may_write_business_content` for «Uus
teema» — so the search never lists a door that would refuse. The pages still
check for themselves; this only decides what to show.

**Matching is by words, not by the database.** A query matches an entry when
every word in it is the start of a word in the entry's name or its search words,
with Estonian letters folded (`ö` = `o`), so «juhatus», «tume», «dark» and
«teavit» all find what a reader means.

An entry with an ``action`` is not a page: «Hele või tume vaade» flips the
colour scheme where the reader is (`static/js/theme.js`'s `data-theme-toggle`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse

#: At most this many app rows above the Teema rows.
LIMIT = 4

_FOLD = str.maketrans("õäöüšž", "oaousz")
_WORD = re.compile(r"[\w]+")


def _signed_in(request: HttpRequest) -> bool:
    return bool(getattr(getattr(request, "user", None), "is_authenticated", False))


def _reads(request: HttpRequest) -> bool:
    from app.core.authorization import may_read_business_content

    return may_read_business_content(getattr(request, "user", None))


def _writes(request: HttpRequest) -> bool:
    from app.core.authorization import may_write_business_content

    return may_write_business_content(getattr(request, "user", None))


def _monitooring(request: HttpRequest) -> bool:
    from app.register_watch import candidate_notices

    return bool(candidate_notices.may_see(getattr(request, "user", None)))


@dataclass(frozen=True)
class Destination:
    title: str
    url_name: str
    words: str
    visible: Callable[[HttpRequest], bool] = _signed_in
    context: str = ""
    action: str = ""

    def url(self) -> str:
        return reverse(self.url_name)


THEME = "theme"

DESTINATIONS: tuple[Destination, ...] = (
    Destination(
        "Hele või tume vaade",
        "haldus:index",
        "hele tume vaade režiim värv värvid öörežiim dark light mode teema seaded",
        context="Lülitab värviskeemi",
        action=THEME,
    ),
    Destination("Minu asjad", "matters:my_work", "minu töö töölaud ülesanded tähtajad kalender"),
    Destination("Osakond", "matters:department", "osakonna ülevaade kalender osakonna kalender"),
    Destination("Teemad", "matters:matter_list", "kõik teemad register nimekiri"),
    Destination("Uus teema", "matters:matter_create", "lisa loo uus teema", _writes),
    Destination("Statistika", "reporting:dashboard", "statistika arvamused aruandlus numbrid"),
    Destination(
        "Majanduspoliitika ülevaade",
        "reporting:report_builder",
        "juhatuse ülevaade juhatus juhatusele aruanne majanduspoliitika ülevaade",
        context="Osakond",
    ),
    Destination(
        "Uued eelnõud",
        "register_watch:candidates",
        "monitooring eelnõud uued",
        _monitooring,
        context="Monitooring",
    ),
    Destination(
        "Valitsuse info",
        "register_watch:agendas",
        "monitooring valitsus istung päevakord",
        _monitooring,
        context="Monitooring",
    ),
    Destination(
        "Registrite dokumendid",
        "register_watch:review",
        "monitooring registrid dokumendid registritest",
        _monitooring,
        context="Monitooring",
    ),
    Destination(
        "Teavituste seaded",
        "notifications:settings",
        "teavitused teavitus seaded kell e-kiri",
        _reads,
        context="Seaded",
    ),
    Destination("Uuendused", "core:release_notes", "uuendused muudatused versioon uut"),
    Destination("Kiirklahvid", "core:keyboard_shortcuts", "kiirklahvid klaviatuur otseteed"),
    Destination("Haldus", "haldus:index", "haldus seaded seadistused rakendus versioon"),
    Destination(
        "Ajaloolised materjalid",
        "reporting:historical",
        "ajalugu ajaloolised onenote arhiiv",
        context="Haldus",
    ),
    Destination(
        "Andmekvaliteet",
        "reporting:quality",
        "andmekvaliteet kvaliteet vead puudused",
        context="Haldus",
    ),
    Destination(
        "Statistika definitsioonid",
        "reporting:definitions",
        "definitsioonid mõisted näitajad arvutus",
        context="Haldus",
    ),
)


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower().translate(_FOLD))


def _matches(query_words: list[str], entry_words: list[str]) -> bool:
    return all(any(word.startswith(part) for word in entry_words) for part in query_words)


def _haldus_sections(request: HttpRequest) -> list[Destination]:
    """The Haldus sections this reader may open, through their own registry."""
    from app.haldus.sections import visible_sections

    return [
        Destination(
            section.label,
            section.url_name,
            f"haldus {section.description}",
            context="Haldus",
        )
        for section in visible_sections(request)
        if section.key not in ("ulevaade", "uuendused")
    ]


def find(request: HttpRequest, query: str, *, limit: int = LIMIT) -> list[dict[str, Any]]:
    """The app destinations matching ``query`` that this reader may open, best first."""
    query_words = _words(query)
    if not query_words or not _signed_in(request):
        return []
    found: list[tuple[int, dict[str, Any]]] = []
    for entry in (*DESTINATIONS, *_haldus_sections(request)):
        title_words = _words(entry.title)
        if not _matches(query_words, title_words + _words(entry.words)):
            continue
        if not entry.visible(request):
            continue
        try:
            url = entry.url()
        except NoReverseMatch:
            continue
        # A hit in the name itself outranks one in the search words only.
        rank = 0 if _matches(query_words, title_words) else 1
        found.append(
            (
                rank,
                {
                    "title": entry.title,
                    "url": url,
                    "context": entry.context,
                    "action": entry.action,
                },
            )
        )
    found.sort(key=lambda pair: pair[0])
    return [row for _, row in found[:limit]]
