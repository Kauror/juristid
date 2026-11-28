"""What Haldus → Lovable says about the synchronisation and the parity check (r98 final).

A pure reader of records that already exist: `LovableSyncRun` (the run ledger and its counts) and
`LovableParityReport` (the independent verification). It calls no source, writes nothing and
names no Teema's words — only references and field names.

Two different things are kept apart on purpose:

* **Synchronisation success** — the run finished and wrote what its breakers allowed (a fact
  about the *process*).
* **Independent verification** — the oracle's comparison of Lovable's values with Juristid's
  (a fact about the *data*), with: which reading it compared and when it was taken, when the
  comparison happened, whether it covered the whole population or a sample (and how large, out
  of how many), the five verdict counts, and whether it is *current*, *outdated* or *incomplete*.

A clean sample is never presented as register parity; a held or unverified difference never
disappears into a green status; an empty comparison is never a success.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

from app.legacy_import.lovable_sync_models import (
    LovableParityReport,
    LovableSyncRun,
    SyncMode,
    SyncRunStatus,
    SyncTrigger,
)

#: A fresh reading older than this is outdated: Lovable has probably moved since.
FRESH_STALE_AFTER = dt.timedelta(hours=36)
#: How many (reference, field) rows one panel lists (the rest are counted).
MAX_LISTED = 100

CURRENT, OUTDATED, INCOMPLETE = "current", "outdated", "incomplete"
#: The panel's headline for one verification, never green unless everything below holds.
VERIFIED, CLEAN_SAMPLE, HELD, MISMATCH, UNVERIFIED, CHANGED, NOT_MADE, EMPTY, STALE_CLEAN = (
    "verified",
    "clean_sample",
    "held",
    "mismatch",
    "unverified",
    "changed",
    "not_made",
    "empty",
    "stale_clean",
)

FIELD_LABELS = {
    "title": "pealkiri",
    "owner": "vastutaja",
    "former_owner": "endine vastutaja",
    "lifecycle": "olek (lahtine / suletud)",
    "stage": "etapp",
    "step": "jooksev samm",
    "opinion": "arvamuse tulemus",
    "opinion_request": "avatud arvamuse taotlus",
    "koda_initiative": "Koja ettepanek",
    "received": "laekumise kuupäev",
    "act": "õigusakt",
    "onenote": "OneNote'i link",
    "commencements": "jõustumised",
    "*": "kogu teema (võrdlust ei saanud teha)",
}
CHANGE_LABELS = {
    "changed": "muutus Lovable'is pärast sünkroonimist",
    "new": "uus Lovable'is, sünkroonimata",
    "gone": "kadunud Lovable'ist (kustutatud või eemaldatud)",
    "reopened": "avati Lovable'is uuesti",
    "closed": "suleti Lovable'is",
}
ERROR_LABELS = {
    "INCONSISTENT": "Lovable muutus lugemise ajal igal katsel",
    "SYNC_RUNNING": "sünkroonimine käis",
    "SYNC_DURING_COMPARISON": "sünkroonimine algas või lõppes võrdluse ajal",
    "TOO_MANY": "liiga palju teemasid ühe lugemise jaoks",
}


@dataclass
class Limit:
    label: str
    used: int
    limit: int | None

    @property
    def level(self) -> str:
        if not self.limit:
            return "ok"
        if self.used > self.limit:
            return "over"
        return "near" if self.used * 5 >= self.limit * 4 else "ok"


@dataclass
class Discrepancy:
    reference: str
    field: str
    label: str
    kind: str  # "mismatch" | "held" | "unverified" | "changed"
    note: str = ""
    #: Set by the view for a reader who may open the Teema (never shown otherwise).
    matter_url: str = ""
    #: The existing, authorised workflows that handle it (the page links; nothing resolves itself).
    hold_url: str = ""


@dataclass
class ParityView:
    report: LovableParityReport
    label: str  # what kind of parity this is
    headline: str
    status: str
    currency: str
    reasons: list[str] = field(default_factory=list)
    scope: str = ""
    discrepancies: list[Discrepancy] = field(default_factory=list)
    more: int = 0
    source_changes: list[tuple[str, int]] = field(default_factory=list)
    compared_reading: str = ""

    @property
    def counts(self) -> dict[str, int]:
        return {k: int(v) for k, v in (self.report.counts or {}).items()}


@dataclass
class Diagnostics:
    attempt: LovableSyncRun | None
    success: LovableSyncRun | None
    scheduled_compare_only: bool
    populations: list[tuple[str, Any]]
    changes: list[tuple[str, Any]]
    problems: list[tuple[str, Any]]
    limits: list[Limit]
    backup: tuple[str, str, str]
    parity: list[ParityView]


def _n(counts: dict[str, Any], key: str) -> int:
    value = counts.get(key)
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _label(name: str) -> str:
    return FIELD_LABELS.get(name, name)


def _view(
    report: LovableParityReport,
    *,
    latest_success: LovableSyncRun | None,
    now: dt.datetime,
) -> ParityView:
    counts = {k: int(v) for k, v in (report.counts or {}).items()}
    compared = sum(
        counts.get(k, 0)
        for k in ("MATCH", "EXPLAINED_DIFFERENCE", "HELD", "MISMATCH", "UNVERIFIED")
    )
    sampled = report.mode != "full"
    changed = list(report.changed_since_sync or [])
    reasons: list[str] = []
    if report.error:
        reasons.append(
            f"võrdlust ei õnnestunud teha: {ERROR_LABELS.get(report.error, report.error)}"
        )
    elif compared == 0:
        reasons.append("võrdlus on tühi: midagi ei võrreldud")
    if sampled:
        reasons.append(
            f"valim {report.sample_size or '?'} teemast {report.eligible or '?'}-st, "
            "mitte kogu register"
        )
    if counts.get("UNVERIFIED"):
        reasons.append(f"{counts['UNVERIFIED']} võrdlemata (kinnitamata)")
    incomplete = bool(reasons)
    outdated = False
    if report.source == "snapshot":
        if latest_success and latest_success.snapshot_taken_at and report.snapshot_taken_at:
            outdated = latest_success.snapshot_taken_at > report.snapshot_taken_at
    else:
        outdated = now - report.at > FRESH_STALE_AFTER or bool(
            latest_success and latest_success.finished_at and latest_success.finished_at > report.at
        )
    currency = INCOMPLETE if incomplete else (OUTDATED if outdated else CURRENT)
    if outdated and not incomplete:
        reasons.append(
            "uuem lugemine on olemas, see võrdlus ei kata seda"
            if report.source == "snapshot"
            else "sünkroonimine või aeg on pärast seda võrdlust edasi läinud"
        )
    # The headline: green only when everything below holds.
    if report.error:
        status, headline = NOT_MADE, "Võrdlust ei tehtud"
    elif compared == 0:
        status, headline = EMPTY, "Tühi võrdlus: see ei ole kooskõla tõend"
    elif counts.get("MISMATCH"):
        status, headline = MISMATCH, f"{counts['MISMATCH']} erinevust"
    elif counts.get("HELD"):
        status, headline = (
            HELD,
            f"{counts['HELD']} välja on ootel (Juristide töö tõttu), mitte kooskõlas",
        )
    elif counts.get("UNVERIFIED"):
        status, headline = UNVERIFIED, f"{counts['UNVERIFIED']} teemat võrdlemata"
    elif changed:
        status, headline = (
            CHANGED,
            f"{len(changed)} välja erineb Lovable'i muutuse tõttu pärast sünkroonimist",
        )
    elif sampled:
        status, headline = CLEAN_SAMPLE, "Valim on kooskõlas; see ei tõenda kogu registri kooskõla"
    elif outdated:
        status, headline = STALE_CLEAN, "Kooskõlas oli, kuid võrdlus on vananenud"
    else:
        status, headline = VERIFIED, "Kogu register on kooskõlas (selle lugemise järgi)"
    found: list[Discrepancy] = []
    for kind, pairs in (
        ("mismatch", report.mismatches),
        ("held", report.held),
        ("unverified", report.unverified),
        ("changed", changed),
    ):
        for pair in pairs or []:
            if isinstance(pair, list | tuple) and len(pair) == 2:
                found.append(Discrepancy(str(pair[0]), str(pair[1]), _label(str(pair[1])), kind))
    sources = [
        (CHANGE_LABELS.get(kind, kind), len(refs))
        for kind, refs in sorted((report.source_changes or {}).items())
        if refs
    ]
    return ParityView(
        report=report,
        label=(
            "Sõltumatu võrdlus: uus lugemine Lovable'ist (praegune allikas)"
            if report.source == "fresh"
            else "Sõltumatu võrdlus: öösel salvestatud lugemine (sünkroonimise hetk)"
        ),
        headline=headline,
        status=status,
        currency=currency,
        reasons=reasons,
        scope=(
            f"valim {report.sample_size} / {report.eligible}"
            + (f", seeme «{report.seed}»" if report.seed else "")
            if sampled
            else f"kogu populatsioon ({report.eligible or report.teemad} teemat)"
        ),
        discrepancies=found[:MAX_LISTED],
        more=max(0, len(found) - MAX_LISTED),
        source_changes=sources,
        compared_reading=(
            f"{report.run_key} · {report.snapshot_sha256[:12]}"
            if report.snapshot_sha256
            else report.run_key
        ),
    )


def collect(now: dt.datetime | None = None) -> Diagnostics:
    now = now or timezone.now()
    runs = LovableSyncRun.objects.exclude(trigger=SyncTrigger.CHECK).order_by("-started_at")
    applied = runs.filter(mode=SyncMode.APPLY)
    attempt = applied.first()
    success = applied.filter(status=SyncRunStatus.SUCCEEDED).first()
    latest = runs.first()
    c: dict[str, Any] = dict((attempt.counts if attempt else None) or {})
    populations = [
        ("Lovable'is elavaid teemasid", _n(c, "source_live")),
        ("Neist operatiivseid", _n(c, "source_operational")),
        ("Juristides sünkroonitud teemasid (kontrollitud)", _n(c, "checked")),
        ("Väljaspool sünkroonitavaid", _n(c, "outside_cohort")),
        ("Operatiivsed, Juristides puudu", _n(c, "operational_not_in_juristid")),
    ]
    changes = [
        ("Muutusteta", _n(c, "unchanged")),
        ("Allika muudatusega (kirjutatud)", _n(c, "changed_source")),
        ("Ainult taastatud (restore)", _n(c, "changed_restores")),
        ("Ainult üleandmised", _n(c, "changed_handovers")),
        ("Juba kooskõlas", _n(c, "converged")),
        ("Uued eelmisest korrast", _n(c, "new_since_previous")),
        ("Uusi teemasid loodud", _n(c, "new_topics_created")),
        ("Uusi teemasid ootel", _n(c, "new_topics_held")),
        ("Eemaldatud (pensionile)", _n(c, "retired")),
    ]
    problems = [
        ("Vastuolud", _n(c, "conflicts")),
        ("Vajab ülevaatamist", _n(c, "review")),
        ("Lovable'is suletud, Juristides ootel", _n(c, "held_local_work")),
        ("Ootel väljad (kokku)", _n(c, "held_fields")),
        ("Impordi ülevaatuse hoidmised", _n(c, "import_held")),
        ("Ebaõnnestumised", _n(c, "failed")),
        ("Tagasilugemise ebaõnnestumised", _n(c, "verify_failed")),
        ("Puudub allikast", _n(c, "missing")),
        ("Tunnus muutunud", _n(c, "identifier_changed")),
        ("Rakendamata", _n(c, "not_executed")),
        ("Otsingus ajakohastamata", _n(c, "search_refresh_pending")),
    ]
    limits = [
        Limit("Tavaline piir: muudetud teemasid", _n(c, "changed_source"), c.get("breaker_limit")),
        Limit("Taastamiste piir", _n(c, "changed_restores"), c.get("restore_limit")),
        Limit("Üleandmiste piir", _n(c, "changed_handovers"), c.get("handover_limit")),
        Limit("Uute teemade piir", _n(c, "new_topics_planned"), c.get("new_topic_limit")),
        Limit(
            "Uute sulgemise hoidmiste piir", _n(c, "new_closure_holds"), c.get("closure_hold_limit")
        ),
    ]
    if attempt is None:
        backup = ("—", "unknown", "Sünkroonimise käivitust pole.")
    elif not attempt.backup_set:
        backup = ("—", "unknown", "Käivitusel ei ole varukoopia komplekti.")
    elif attempt.verified:
        backup = (attempt.backup_set, "ok", "Komplekt on 2. tasemel kinnitatud.")
    else:
        backup = (attempt.backup_set, "problem", "Komplekt ei ole 2. tasemel kinnitatud.")
    views = []
    seen: set[str] = set()
    for report in LovableParityReport.objects.order_by("-at")[:20]:
        # the newest of each kind: stored-reading parity and current-source parity
        if report.source in seen:
            continue
        seen.add(report.source)
        views.append(_view(report, latest_success=success, now=now))
    return Diagnostics(
        attempt=attempt,
        success=success,
        scheduled_compare_only=bool(
            latest is not None
            and latest.mode != SyncMode.APPLY
            and latest.trigger == SyncTrigger.SCHEDULED
        ),
        populations=populations,
        changes=changes,
        problems=problems,
        limits=limits,
        backup=backup,
        parity=views,
    )
