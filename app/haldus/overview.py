"""Haldus → Ülevaade: what needs attention, for this reader only (docs/adr/0221).

One short line per thing this reader administers: the integrations, the
department's waiting work, the two-administrator rule, the system's state.
Each line is asked only when the reader may open the item it points at
(`app.haldus.sections`), so the overview never says more than the pages behind
it would — counts and verdicts, never a Teema, a person's work or a secret.

Every read goes through the service its own page uses (`sync_admin`,
`register_watch.watch`, `held_work`, `administration`, the Süsteemi seis
helpers); nothing is computed here a second way. A read that fails is shown as
unknown, never as healthy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db import DatabaseError
from django.http import HttpRequest
from django.urls import reverse

from app.haldus.sections import visible_sections

#: The overview's three states: needs attention, fine, could not be read.
PROBLEM = "problem"
OK = "ok"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Signal:
    """One line: what it is about, its state, one sentence, and where to act."""

    label: str
    state: str
    text: str
    url: str

    @property
    def attention(self) -> bool:
        return self.state != OK


def _lovable(request: HttpRequest) -> Signal:
    from app.legacy_import import sync_admin

    panel = sync_admin.lovable_panel(request.user)
    url = reverse("haldus:data_sync")
    if not panel.ok:
        text = panel.warnings[0] if panel.warnings else "Sünkroonimine vajab tähelepanu."
        return Signal("Lovable", PROBLEM, text, url)
    if panel.pending_fields:
        return Signal(
            "Lovable",
            PROBLEM,
            f"Ülevaatust ootab {panel.pending_fields} erinevust.",
            reverse("haldus:data_sync_differences"),
        )
    return Signal("Lovable", OK, f"Korras · režiim {panel.mode_label}", url)


def _onenote(request: HttpRequest) -> Signal:
    from app.legacy_import import onenote_coverage

    _items, _hidden, counts = onenote_coverage.held_items(viewer=request.user)
    waiting = sum(counts.values())
    if waiting:
        return Signal(
            "OneNote",
            PROBLEM,
            f"Otsust ootab {waiting} kirjet.",
            reverse("haldus:onenote_holds"),
        )
    return Signal("OneNote", OK, "Ootel kirjeid pole", reverse("haldus:onenote"))


def _register_watch(request: HttpRequest) -> Signal:
    from app.register_watch.watch import watch_health

    health = watch_health()
    url = reverse("haldus:register_watch")
    if not health.ok:
        return Signal(
            "Registrite jälgimine", PROBLEM, health.problem or "Kontroll vajab tähelepanu.", url
        )
    return Signal("Registrite jälgimine", OK, "Korras", url)


def _department(request: HttpRequest) -> Signal | None:
    from app.haldus.views import _candidate_victories

    waiting = _candidate_victories(request.user)
    if not waiting:
        return None
    return Signal(
        "Töövõidud",
        PROBLEM,
        f"Ülevaatust ootab {waiting} töövõidu kandidaati.",
        reverse("haldus:osakond"),
    )


def _without_owner(request: HttpRequest) -> Signal:
    from app.matters.held_work import work_without_active_owner

    orphaned = work_without_active_owner()
    url = reverse("account_admin:without_owner")
    if orphaned.any:
        parts = []
        if orphaned.holders:
            parts.append(f"{len(orphaned.holders)} väljalülitatud inimesel on avatud tööd")
        if orphaned.unowned_teemad:
            parts.append(f"{orphaned.unowned_teemad} avatud teemal pole vastutajat")
        return Signal("Töö ilma vastutajata", PROBLEM, "; ".join(parts) + ".", url)
    return Signal("Töö ilma vastutajata", OK, "Kõigil avatud töödel on aktiivne vastutaja", url)


def _continuity(request: HttpRequest) -> Signal:
    from app.accounts import administration

    status = administration.continuity_status()
    url = reverse("haldus:index") + "#halduse-jarjepidevus"
    if status.satisfied:
        return Signal("Halduse järjepidevus", OK, "Tagatud", url)
    # One line; the panel below the list says the whole rule.
    text = f"Täishaldureid {status.count}, vaja vähemalt {status.required}."
    return Signal("Halduse järjepidevus", PROBLEM, text, url)


def _system(request: HttpRequest) -> Signal:
    from app.haldus.views import _processing_errors, _readiness, _search_freshness

    url = reverse("haldus:susteem")
    problems = []
    readiness = _readiness()
    if readiness is None:
        return Signal("Süsteemi seis", UNKNOWN, "Valmisolekut ei õnnestunud lugeda.", url)
    if not readiness["ok"]:
        problems.append(f"valmisolekul {readiness['problems']} probleemi")
    freshness = _search_freshness()
    if freshness is not None and not freshness["ok"]:
        problems.append("otsinguindeks pole värske")
    errors = _processing_errors()
    failed = sum(n for n in errors.values() if n)
    if failed:
        problems.append(f"failitöötlusel {failed} viga")
    if problems:
        return Signal("Süsteemi seis", PROBLEM, "; ".join(problems).capitalize() + ".", url)
    return Signal("Süsteemi seis", OK, "Korras", url)


#: Item key → its line's label and reader. Asked in this order, only for visible items.
READERS: tuple[tuple[str, str, Callable[[HttpRequest], Signal | None]], ...] = (
    ("lovable", "Lovable", _lovable),
    ("onenote", "OneNote", _onenote),
    ("registrid", "Registrite jälgimine", _register_watch),
    ("osakond", "Töövõidud", _department),
    ("vastutajata", "Töö ilma vastutajata", _without_owner),
    ("susteem", "Süsteemi seis", _system),
)


def _read(
    label: str, url_key: str, read: Callable[[HttpRequest], Signal | None], request: HttpRequest
) -> Signal | None:
    try:
        return read(request)
    except DatabaseError:
        from app.haldus.sections import section

        return Signal(label, UNKNOWN, "Seisu ei õnnestunud lugeda.", section(url_key).url())


def signals(request: HttpRequest, *, administrator: bool) -> list[Signal]:
    """Every line this reader may be shown, attention first, in registry order.

    ``administrator`` adds the two-administrator rule: it is said only to a
    verified administrator session, never to a persona (docs/adr/0186).
    """
    shown = {item.key for item in visible_sections(request)}
    found: list[Signal] = []
    for key, label, read in READERS:
        if key in shown:
            signal = _read(label, key, read, request)
            if signal is not None:
                found.append(signal)
    if administrator:
        # Its own panel on the page says it when it holds; a line only when it does not.
        continuity = _read("Halduse järjepidevus", "ulevaade", _continuity, request)
        if continuity is not None and continuity.attention:
            found.append(continuity)
    return sorted(found, key=lambda s: not s.attention)


def summary(found: list[Signal]) -> dict[str, Any]:
    return {
        "attention": [s for s in found if s.attention],
        "healthy": [s for s in found if not s.attention],
    }
