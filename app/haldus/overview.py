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
    riigikogu = _riigikogu_problem()
    if riigikogu:
        return Signal("Registrite jälgimine", PROBLEM, riigikogu, url + "#regwatchlog-riigikogu")
    return Signal("Registrite jälgimine", OK, "Korras", url)


def _riigikogu_problem() -> str | None:
    """The Riigikogu cycle's first stage that is not ok, once the cycle is switched on.

    Switched off, every stage reads «disabled» by design (docs/adr/0224 §6, §7) and the
    overview says nothing: a line that is always there would teach people to ignore it.
    The analysis stage stays off under its own authorisation (docs/adr/0227) and is
    never a problem here.
    """
    from app.riigikogu import cycle

    if not cycle.enabled():
        return None
    for stage in cycle.health():
        if stage.state not in {"ok", "disabled"}:
            return f"Riigikogu · {stage.label}: {stage.line}."
    return None


def _ai_usage(request: HttpRequest) -> Signal:
    """AI usage: an accounting gap, or a crossed monthly level, or unknown cost.

    A low measured total is never reported as «within budget» while some cost is unknown.
    """
    import datetime as dt

    from django.utils import timezone

    from app.ai_usage import report
    from app.ai_usage.models import AiLedgerGap

    url = reverse("haldus:ai_usage")
    gaps = AiLedgerGap.objects.filter(at__gte=timezone.now() - dt.timedelta(days=7)).count()
    if gaps:
        text = f"Arvestus on puudulik: {gaps} päringu tulemus on salvestamata."
        return Signal("AI kasutus", PROBLEM, text, url)
    month = report.budget()
    known = report.money(month.known_eur)
    if month.level in {"warn", "escalate"}:
        text = (
            f"Kuu teadaolev maksumus ({known}) ületab hoiatuspiiri"
            + ("" if month.complete else "; arvestus on lisaks puudulik")
            + "."
        )
        return Signal("AI kasutus", PROBLEM, text, url)
    if not month.complete:
        why = "; ".join(f"{n} × {report.REASONS[k]}" for k, n in month.reasons.items())
        policy = (
            "Piiri ületamist ei saa kinnitada ega välistada."
            if month.level == "unproven"
            else "Hoiatuspiire pole seatud."
        )
        text = f"Arvestus on puudulik: {why}. Teadaolev alamsumma {known}. {policy}"
        return Signal("AI kasutus", UNKNOWN, text, url)
    if not report.AiOperation.objects.exists():
        return Signal("AI kasutus", OK, "Päringuid pole registreeritud.", url)
    policy = "piirist allpool" if month.level == "ok" else "hoiatuspiire pole seatud"
    return Signal("AI kasutus", OK, f"Arvestus täielik: kuu maksumus {known}; {policy}.", url)


def _nightly(request: HttpRequest) -> Signal:
    """The last recorded cycle: a failed, partial or missing step is the line; else one OK line."""
    from app.core import integration_status

    url = reverse("haldus:nightly_history")
    found = integration_status.history(3)
    due = [n for n in found.nights if n.state != integration_status.PENDING]
    if not due:
        return Signal(
            "Öine tsükkel", UNKNOWN, "Ajastatud tsükli kirjeid ei ole veel salvestatud.", url
        )
    night = due[0]
    day = f"{night.start.day}.{night.start.month}"
    if night.state in {integration_status.FAILED, integration_status.PARTIAL}:
        text = f"Tsükkel {day}: {night.label.lower()}, {night.problems} sammu vajab tähelepanu."
        return Signal("Öine tsükkel", PROBLEM, text, url)
    if night.state == integration_status.MISSING:
        return Signal("Öine tsükkel", PROBLEM, f"Tsükkel {day} ei jätnud ühtegi kirjet.", url)
    if night.state == integration_status.RUNNING:
        return Signal("Öine tsükkel", UNKNOWN, f"Tsükkel {day} käib või ootab veel samme.", url)
    if night.state == integration_status.DISABLED:
        return Signal("Öine tsükkel", UNKNOWN, f"Tsükkel {day} jäeti tahtlikult vahele.", url)
    return Signal("Öine tsükkel", OK, f"Tsükkel {day} lõpetatud", url)


def _freshness(request: HttpRequest) -> Signal:
    from app.core import integration_status

    waiting = integration_status.attention(integration_status.sources())
    url = reverse("haldus:freshness")
    if waiting:
        return Signal("Allikate värskus", PROBLEM, f"{len(waiting)} allikat vajab tähelepanu.", url)
    return Signal("Allikate värskus", OK, "Kõik allikad värsked", url)


def _processing(request: HttpRequest) -> Signal:
    from app.documents import processing_health

    health = processing_health.collect()
    url = reverse("haldus:processing")
    problems = []
    if health.counts.get("FAILED"):
        problems.append(f"{health.counts['FAILED']} ebaõnnestunud")
    if health.interrupted:
        problems.append(f"{health.interrupted} katkenud")
    if problems:
        return Signal("Dokumentide töötlus", PROBLEM, ", ".join(problems).capitalize() + ".", url)
    return Signal("Dokumentide töötlus", OK, "Korras", url)


def _parity(request: HttpRequest) -> Signal:
    from app.legacy_import.lovable_sync_models import LovableParityReport

    url = reverse("haldus:freshness")
    latest = LovableParityReport.objects.order_by("-at").first()
    if latest is None:
        return Signal("Lovable'i võrdlus", UNKNOWN, "Sõltumatut võrdlust pole veel tehtud.", url)
    if latest.mismatches or latest.unverified:
        text = (
            f"Viimane võrdlus: {len(latest.mismatches)} lahknevust, "
            f"{len(latest.unverified)} kontrollimata."
        )
        return Signal("Lovable'i võrdlus", PROBLEM, text, url)
    return Signal(
        "Lovable'i võrdlus", OK, f"Viimane võrdlus {latest.at.day}.{latest.at.month} puhas", url
    )


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
    ("oised_tsuklid", "Öine tsükkel", _nightly),
    ("varskus", "Allikate värskus", _freshness),
    ("toodeldud", "Dokumentide töötlus", _processing),
    ("lovable", "Lovable'i võrdlus", _parity),
    ("ai_kasutus", "AI kasutus", _ai_usage),
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
