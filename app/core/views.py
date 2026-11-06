"""Cross-cutting surfaces: the health probe, the entry redirect and the design
token reference. Product screens live in their own domain modules.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, connection
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.views.decorators.http import require_safe

from app.core.authorization import is_department_head, may_read_business_content
from app.core.development_status import ITEMS as DEVELOPMENT_STATUS_ITEMS
from app.core.holidays import estonian_public_holidays
from app.core.keyboard_shortcuts import GROUPS as KEYBOARD_SHORTCUT_GROUPS
from app.core.middleware import FAILURE_HEADER, NO_STORE, is_htmx
from app.core.release_notes import load_release_notes
from app.legacy_import.lovable_sync_labels import LOVABLE_FIELD_LABELS
from app.workflow.dates import EARLIEST_DAY, LATEST_DAY


def healthz(request: HttpRequest) -> JsonResponse:
    database = "ok"
    status = 200
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError as exc:  # pragma: no cover - exercised by compose smoke test
        database = f"error: {exc.__class__.__name__}"
        status = 503

    return JsonResponse(
        {
            "status": "ok" if status == 200 else "degraded",
            "database": database,
            "environment": settings.APPLICATION_ENVIRONMENT,
            "stage": settings.APPLICATION_STAGE,
            "revision": settings.APPLICATION_REVISION,
        },
        status=status,
    )


def favicon(request: HttpRequest) -> HttpResponse:
    """Answer `/favicon.ico` with the brand mark.

    Browsers request this path whether or not the page links an icon, so
    without it every single visit logged a `Not Found: /favicon.ico` warning —
    noise that makes a real 404 harder to notice in the log.

    A redirect to the round emblem's .ico (UI-02), which is cut from the same
    artwork the top bar shows (`tools/brand_favicons.py`) and is already served
    as a static file with the right cache headers. Resolved per request rather
    than at import, so the hashed static name is looked up from whatever
    manifest the running process actually has — and a temporary redirect, so a
    browser that cached the old target asks again.
    """
    return redirect(static("brand/koda-round-favicon.ico"), permanent=False)


def home(request: HttpRequest) -> HttpResponse:
    """The root is a doorway, not a page.

    A signed-in lawyer wants the work list, not a welcome screen; anyone else
    needs to sign in first.

    **Minu asjad is a person's default home.** The first question on opening
    the application is "what do I have to do now", and the department view is
    one click away on the bar — the reverse of the Stage-2D arrangement, which
    opened Ülevaade and left the personal queue to be found. Ülevaade is
    unchanged and still first-class; only what `/` *chooses* moved.

    That destination needs a person, so the shared gate gets the step it was
    missing: somebody who has typed the department password but not said whose
    work they are looking at is sent to the persona selector rather than to a
    page built for nobody. Password → choose a persona → Minu asjad, with no
    detour through a page they did not ask for.

    Deliberately a redirect to the canonical route rather than a second copy of
    the page: `/minu-asjad/` stays the address that gets bookmarked, linked and
    resolved (docs/adr/0016, "the landing page" section).
    """
    from app.accounts import shared_gate

    if request.user.is_authenticated:
        return redirect("matters:my_work")
    if shared_gate.has_passed(request):
        # Not `matters:my_work`: that page is one person's desk and there is no
        # person yet. Not `matters:overview` either — the department view is a
        # destination somebody chooses, not the thing standing between them and
        # their own work.
        return redirect("accounts:choose_persona")
    return render(request, "core/home.html")


def release_notes(request: HttpRequest) -> HttpResponse:
    """What has changed in Juristid, grouped by the day it became available.

    Deliberately **not** `login_required`. The application's door is the shared
    gate, applied to every path by `AuthenticationModeMiddleware`; a persona is a
    second, different question — *whose work are you looking at* — and there is
    no work on this page to look at. Somebody who has typed the department
    password and not yet said who they are can read what changed, which is
    exactly the moment they are most likely to want to (docs/adr/0034).

    So the gate is untouched and nothing is exempted from it: in shared-gate mode
    an unauthenticated request is redirected to the password exactly as it is for
    every other route, and the reader arrives here only after passing it.

    No database query and no network call. The notes are a file that was baked
    into this image beside the code they describe, parsed once per process
    (`app/core/release_notes.py`).
    """
    return render(
        request,
        "core/release_notes.html",
        {"release_days": load_release_notes(), "uuendused_tab": "juristid"},
    )


#: Said to a signed-in account that may not read business content — the
#: technical administrator without a business role (docs/adr/0181).
LOVABLE_NEEDS_A_READER = (
    "Lovable'i muudatused on teemade sisu. Neid näeb ainult juriidilise osakonna kasutaja."
)

# ---------------------------------------------------------------------------
# Uuendused → Lovable (docs/adr/0192 §11, docs/adr/0201)
#
# Six sub-sections of one page, chosen with ``?vaade=``; each is a GET with its
# filters in the address, so a view can be bookmarked and shared. Every number,
# title and value comes from `app.legacy_import.lovable_observability` — the one
# read service — and nothing here compares, counts or decides visibility on its
# own: it reads the filter form, asks the service, and lays the answer out.
# ---------------------------------------------------------------------------

#: ``?vaade=`` → the tab's label. The first is the default.
LOVABLE_VIEWS = {
    "ulevaade": "Ülevaade",
    "oised": "Öised võrdlused",
    "ajalugu": "Lovable'i muutmisajalugu",
    "ootel": "Ülevaatust ootavad muudatused",
    "vaadeldud": "Vaadeldud muudatused",
    "valmisolek": "Valmisolek",
}
#: The views whose filtered rows `Laadi Excel` writes.
LOVABLE_EXPORTS = ("oised", "ootel", "vaadeldud")
#: Which filters each view offers: only what its service question takes.
LOVABLE_FILTERS = {
    "oised": ("dates", "population", "outcome", "field", "attention", "reference", "text"),
    "ajalugu": ("dates", "field", "reference"),
    "ootel": ("queue_outcome", "field", "reference", "text"),
    "vaadeldud": ("dates", "population", "kind", "mapping", "field", "reference", "text"),
}
#: The address parameter each of those filters reads (`_lovable_filters`).
LOVABLE_FILTER_PARAMS = {
    "dates": ("algus", "lopp"),
    "population": ("ulatus",),
    "outcome": ("tulemus",),
    "queue_outcome": ("tulemus",),
    "kind": ("liik",),
    "mapping": ("seos",),
    "field": ("vali",),
    "reference": ("viide",),
    "text": ("otsi",),
    "attention": ("tahelepanu",),
}


def lovable_export_filters(view: str) -> frozenset[str]:
    """The parameter names `Laadi Excel` honours on ``view``, for its audit row:
    the view itself and the filters that view offers. The page number is not in
    it, since the file is every page; anything else is counted on the row, never
    named (`app.audit.access`, B-01)."""
    return frozenset(
        {"vaade"}.union(*(LOVABLE_FILTER_PARAMS[kind] for kind in LOVABLE_FILTERS.get(view, ())))
    )


#: Estonian slugs in the address → (the service's value, the label).
LOVABLE_POPULATIONS = {
    "piloot": ("PILOT", "Sünkroonitavad teemad"),
    "operatiivsed": ("OPERATIONAL", "Operatiivsed teemad"),
    "koik": ("ALL_LIVE", "Kõik elavad teemad"),
}
#: A night's result for a field (`lovable_review` result keys).
LOVABLE_RESULTS = {
    "uuendatud": ("APPLIED", "Uuendatud"),
    "rakendamata": ("NOT_APPLIED", "Rakendamata"),
    "ulevaatus": ("REVIEW", "Vajab ülevaatamist"),
    "vastuolu": ("CONFLICT", "Vastuolu"),
    "sama": ("SAME", "Juristides juba sama"),
    "lahendatud": ("RESOLVED", "Lahendatud käsitsi"),
    "kohalik": ("LOCAL", "Juristides muudetud"),
    "vordlemata": ("NOT_COMPARED", "Ei võrreldud"),
    "viga": ("FAILED", "Viga"),
    "teadmata": ("UNKNOWN", "Teadmata"),
}
#: A pending field's newest decision (`SyncOutcome`).
LOVABLE_QUEUE_OUTCOMES = {
    "ohutu": ("ELIGIBLE", "Ohutu uuendus (rakendamata)"),
    "ulevaatus": ("REVIEW_REQUIRED", "Vajab ülevaatamist"),
    "vastuolu": ("CONFLICT", "Vastuolu"),
    "kohalik": ("LOCAL_TEST_CHANGE", "Kohalik testmuudatus"),
    "viga": ("FAILED", "Viga"),
}
LOVABLE_KINDS = {
    "uus": ("NEW", "Uus teema"),
    "muutunud": ("CHANGED", "Muutunud"),
    "kadunud": ("MISSING", "Kadus lugemisest"),
    "tagasi": ("BACK", "Taas lugemises"),
    "number": ("RENUMBERED", "Number muutus"),
}
LOVABLE_MAPPINGS = {
    "piloot": ("PILOT_LINKED", "Piloodi teema"),
    "kandidaat": ("REFERENCE_CANDIDATE", "Sama numbriga teema, seos kinnitamata"),
    "vasteta": ("UNMATCHED", "Juristides vastet pole"),
}
#: Results that ask a person to look («Ainult tähelepanu vajavad»).
LOVABLE_ATTENTION_RESULTS = frozenset({"REVIEW", "CONFLICT", "FAILED"})

#: The overview's indicators, grouped by the kind of fact they count — the
#: four the read service keeps apart — after the state of the job itself.
LOVABLE_INDICATOR_GROUPS = (
    (
        "Sünkroonimise seis",
        (
            "latest_scheduled",
            "latest_success",
            "mode",
            "snapshot_freshness",
            "pilot_coverage",
            "operational_observed",
            "live_indexed",
            "retention_freshness",
        ),
    ),
    ("Lovable'i muutmisajalugu (allika sündmused)", ("audit_freshness", "source_events")),
    (
        "Lugemiste vahelised erinevused",
        (
            "pilot_topics_changed",
            "changed_fields",
            "observed_topics_changed",
            "new_source_topics",
            "unmatched_operational",
            "missing",
            "restored",
            "renumbered",
        ),
    ),
    (
        "Juristide otsused (sünkroonitavad teemad)",
        ("eligible", "review_required", "conflicts", "already_converged"),
    ),
    # Shown only once a cohort has joined the pilot (docs/adr/0202 §6).
    ("Sünkroonitavad kohordid", ("cohort_lovable_pilot", "cohort_lovable_operational")),
    ("Rakendatud kirjutused", ("applied",)),
)

#: Text fields of the filter form are bounded like every other query here.
LOVABLE_TEXT_LIMIT = 200
LOVABLE_REFERENCE_LIMIT = 20
LOVABLE_PAGE_LIMIT = 10_000
#: Pages `Laadi Excel` collects at most (`lovable_export.MAX_ROWS` bounds rows).
LOVABLE_EXPORT_PAGES = 100


def _lovable_now() -> dt.datetime:
    """The moment every Lovable question is asked at: one place, so a test can
    hold the clock still."""
    from django.utils import timezone

    return timezone.now()


def _slug_choices(table: dict[str, tuple[str, str]]) -> list[tuple[str, str]]:
    return [(slug, label) for slug, (_value, label) in table.items()]


def _lovable_field_choices() -> list[tuple[str, str]]:
    return [(k, v) for k, v in LOVABLE_FIELD_LABELS.items() if k not in ("*", "reference")]


@dataclass
class LovableFilters:
    """What the filter form asked for, read and bounded. ``slugs`` is what the
    address carries; the other attributes are the service's own values."""

    view: str
    since: dt.date | None = None
    until: dt.date | None = None
    population: str = ""
    outcome: str = ""
    kind: str = ""
    mapping: str = ""
    field_name: str = ""
    reference: str = ""
    text: str = ""
    attention: bool = False
    page: int = 1
    slugs: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return any(self.slugs.values())

    def query(self) -> str:
        """The view and its filters as a query string, without the page number."""
        params = {"vaade": self.view}
        params.update({k: v for k, v in self.slugs.items() if v})
        return urlencode(params)

    def describe(self) -> list[str]:
        """The active filters in words, for the workbook's information sheet."""
        labels = {
            "algus": "Algus",
            "lopp": "Lõpp",
            "ulatus": "Hulk",
            "tulemus": "Tulemus",
            "liik": "Muutuse liik",
            "seos": "Seos Juristidega",
            "vali": "Väli",
            "viide": "Teema number",
            "otsi": "Otsing",
            "tahelepanu": "Ainult tähelepanu vajavad",
        }
        named = {
            "ulatus": LOVABLE_POPULATIONS,
            "tulemus": LOVABLE_QUEUE_OUTCOMES if self.view == "ootel" else LOVABLE_RESULTS,
            "liik": LOVABLE_KINDS,
            "seos": LOVABLE_MAPPINGS,
        }
        lines = []
        for key, value in self.slugs.items():
            if not value:
                continue
            shown = value
            if key in named and value in named[key]:
                shown = named[key][value][1]
            elif key == "vali":
                shown = LOVABLE_FIELD_LABELS.get(value, value)
            elif key == "tahelepanu":
                shown = "jah"
            lines.append(f"{labels.get(key, key)}: {shown}")
        return lines


def _lovable_filters(request: HttpRequest, view: str) -> LovableFilters:
    """Read the filter form. Anything unknown is ignored, never an error page;
    an unreadable date is said above the results and left out."""
    from app.core.dates import format_estonian_date, read_flexible_date
    from app.core.request_params import bounded_int, choice_or_default, text_or_empty
    from app.legacy_import.lovable_observability import MAX_WINDOW_DAYS
    from app.legacy_import.lovable_sync import TALLINN

    get = request.GET
    offered = LOVABLE_FILTERS.get(view, ())
    found = LovableFilters(
        view=view,
        page=bounded_int(get.get("leht"), default=1, minimum=1, maximum=LOVABLE_PAGE_LIMIT),
    )

    def pick(key: str, table: dict[str, tuple[str, str]], default: str = "") -> str:
        slug = str(choice_or_default(get.get(key), table, ""))
        found.slugs[key] = slug
        return table[slug][0] if slug else default

    if "dates" in offered:
        for key, label in (("algus", "Algus"), ("lopp", "Lõpp")):
            typed = text_or_empty(get.get(key))[:40]
            reading = read_flexible_date(typed)
            if reading.invalid:
                found.errors.append(
                    f"{label}: «{typed}» ei ole kuupäev; kirjuta kujul 7.9.2026. "
                    "Seda ei arvestatud."
                )
            elif reading.value is not None:
                found.slugs[key] = format_estonian_date(reading.value)
                if key == "algus":
                    found.since = reading.value
                else:
                    found.until = reading.value
        if found.since is not None and found.until is not None and found.since > found.until:
            found.since, found.until = found.until, found.since
            found.slugs["algus"], found.slugs["lopp"] = found.slugs["lopp"], found.slugs["algus"]
            found.notes.append("Algus oli hiljem kui lõpp: kuupäevad vahetati.")
        last = found.until or _lovable_now().astimezone(TALLINN).date()
        if found.since is not None and abs((last - found.since).days) > MAX_WINDOW_DAYS:
            found.notes.append(
                f"Periood on kuni {MAX_WINDOW_DAYS} päeva: näidatakse {MAX_WINDOW_DAYS} päeva "
                "lõppkuupäevani."
            )
    if "population" in offered:
        found.population = pick(
            "ulatus", LOVABLE_POPULATIONS, "PILOT" if view == "oised" else "ALL_LIVE"
        )
    if "outcome" in offered:
        found.outcome = pick("tulemus", LOVABLE_RESULTS)
    if "queue_outcome" in offered:
        found.outcome = pick("tulemus", LOVABLE_QUEUE_OUTCOMES)
    if "kind" in offered:
        found.kind = pick("liik", LOVABLE_KINDS)
    if "mapping" in offered:
        found.mapping = pick("seos", LOVABLE_MAPPINGS)
    if "field" in offered:
        found.field_name = str(
            choice_or_default(get.get("vali"), dict(_lovable_field_choices()), "")
        )
        found.slugs["vali"] = found.field_name
    if "reference" in offered:
        found.reference = text_or_empty(get.get("viide"))[:LOVABLE_REFERENCE_LIMIT]
        found.slugs["viide"] = found.reference
    if "text" in offered:
        found.text = text_or_empty(get.get("otsi"))[:LOVABLE_TEXT_LIMIT]
        found.slugs["otsi"] = found.text
    if "attention" in offered:
        found.attention = get.get("tahelepanu") == "1"
        found.slugs["tahelepanu"] = "1" if found.attention else ""
    return found


def _pager(page: Any, filters: LovableFilters) -> dict[str, Any]:
    """Links for a service `Page`: every number when there are few, else the
    first, the last and two either side of the current one."""
    pages, current = page.pages, page.page
    numbers: list[int | None] = []
    keep = {1, pages, *range(max(1, current - 2), min(pages, current + 2) + 1)}
    for number in range(1, pages + 1):
        if pages <= 9 or number in keep:
            numbers.append(number)
        elif numbers and numbers[-1] is not None:
            numbers.append(None)
    return {
        "page": current,
        "pages": pages,
        "total": page.total,
        "numbers": numbers,
        "query": filters.query(),
        "previous": current - 1 if current > 1 else None,
        "next": current + 1 if current < pages else None,
    }


def _unlinked_rows(summary: Any, *, events: bool = False) -> dict[str, Any]:
    """An `UnlinkedSummary` laid out: the sentence, and counts by kind (or
    action), mapping and field name with Estonian labels. Counts only."""
    from app.legacy_import.lovable_sync_labels import (
        LOVABLE_OBSERVED_KINDS,
        LOVABLE_SOURCE_ACTIONS,
        LOVABLE_SOURCE_COLUMNS,
    )

    if summary is None or not summary.total:
        return {}
    mappings = dict(LOVABLE_MAPPINGS.values())

    def field_label(name: str) -> str:
        canonical = LOVABLE_SOURCE_COLUMNS.get(name, name) if events else name
        return LOVABLE_FIELD_LABELS.get(canonical, canonical)

    def kind_label(name: str) -> str:
        if events:
            return LOVABLE_SOURCE_ACTIONS.get(name.lower(), name)
        return LOVABLE_OBSERVED_KINDS.get(name, name)

    return {
        "note": summary.note,
        "kinds": [(kind_label(k), n) for k, n in summary.kind.items()],
        "mappings": [(mappings.get(k, k), n) for k, n in summary.mapping.items()],
        "fields": [(field_label(k), n) for k, n in summary.field.items()],
    }


def _topic_needs_attention(topic: Any) -> bool:
    """As `lovable_review.Night.attention` counts it: a Teema entering the
    comparison (``ENROLLED``, docs/adr/0202 §6) is not a change to look at."""
    from app.legacy_import.lovable_review import ENROLLED

    return topic.kind not in ("CHANGED", ENROLLED) or any(
        f.result in LOVABLE_ATTENTION_RESULTS for f in topic.fields
    )


def _night_rows(readings: list[Any], *, attention: bool) -> list[dict[str, Any]]:
    """Each reading with what its header shows. Counts are the service's; the
    «Ainult tähelepanu vajavad» filter only narrows which topics are listed."""
    from app.legacy_import.lovable_review import RESULT_LABELS
    from app.legacy_import.lovable_sync_labels import LOVABLE_OBSERVED_KINDS

    rows = []
    for reading in readings:
        pilot = reading.kind == "PILOT_NIGHT"
        labels = RESULT_LABELS if pilot else LOVABLE_OBSERVED_KINDS
        rows.append(
            {
                "reading": reading,
                "pilot": pilot,
                "topics": [t for t in reading.topics if _topic_needs_attention(t)]
                if attention
                else reading.topics,
                "applied": reading.outcomes.get("APPLIED", 0),
                "attention": sum(
                    n for k, n in reading.outcomes.items() if k in LOVABLE_ATTENTION_RESULTS
                ),
                "outcomes": [(labels.get(k, k), n) for k, n in reading.outcomes.items()],
                "unlinked": _unlinked_rows(reading.unlinked),
            }
        )
    return rows


def _lovable_overview(user: Any, filters: LovableFilters) -> dict[str, Any]:
    from app.legacy_import import lovable_observability as obs

    status = obs.status(user, now=_lovable_now())
    by_key = {i.key: i for i in status.indicators}
    groups, used = [], set()
    for title, keys in LOVABLE_INDICATOR_GROUPS:
        shown = [by_key[k] for k in keys if k in by_key]
        used |= {i.key for i in shown}
        if shown:
            groups.append((title, shown))
    rest = [i for i in status.indicators if i.key not in used]
    if rest:
        groups.append(("Muud näitajad", rest))
    return {"status": status, "indicator_groups": groups}


def _lovable_nights(user: Any, filters: LovableFilters, page: int = 0) -> Any:
    from app.legacy_import import lovable_observability as obs

    return obs.nights(
        user,
        since=filters.since,
        until=filters.until,
        population=filters.population,
        outcome=filters.outcome,
        field_name=filters.field_name,
        reference=filters.reference,
        text=filters.text,
        page=page or filters.page,
        now=_lovable_now(),
        full=True,
    )


def _lovable_night_view(user: Any, filters: LovableFilters) -> dict[str, Any]:
    from app.legacy_import.lovable_sync import SNAPSHOT_RETENTION_DAYS

    page = _lovable_nights(user, filters)
    return {
        "page": page,
        "pager": _pager(page, filters),
        "readings": _night_rows(page.items, attention=filters.attention),
        "retention_days": SNAPSHOT_RETENTION_DAYS,
    }


def _lovable_history(user: Any, filters: LovableFilters) -> dict[str, Any]:
    from app.legacy_import import lovable_observability as obs

    audit, page = obs.source_events(
        user,
        since=filters.since,
        until=filters.until,
        reference=filters.reference,
        field_name=filters.field_name,
        page=filters.page,
        now=_lovable_now(),
    )
    return {
        "audit": audit,
        "page": page,
        "pager": _pager(page, filters),
        "unlinked": _unlinked_rows(page.unlinked, events=True),
        "unlinked_listed": obs.unlinked_detail() == obs.FIELD_NAMES,
    }


def _lovable_queue(user: Any, filters: LovableFilters, page: int = 0) -> Any:
    from app.legacy_import import lovable_observability as obs

    return obs.review_queue(
        user,
        field_name=filters.field_name,
        outcome=filters.outcome,
        reference=filters.reference,
        text=filters.text,
        page=page or filters.page,
        now=_lovable_now(),
    )


def _lovable_queue_view(user: Any, filters: LovableFilters) -> dict[str, Any]:
    page = _lovable_queue(user, filters)
    return {"page": page, "pager": _pager(page, filters)}


def _lovable_observed(user: Any, filters: LovableFilters, page: int = 0) -> Any:
    from app.legacy_import import lovable_observability as obs

    return obs.observed_changes(
        user,
        since=filters.since,
        until=filters.until,
        population=filters.population,
        kind=filters.kind,
        field_name=filters.field_name,
        mapping=filters.mapping,
        reference=filters.reference,
        text=filters.text,
        page=page or filters.page,
        now=_lovable_now(),
        full=True,
    )


def _lovable_observed_view(user: Any, filters: LovableFilters) -> dict[str, Any]:
    page = _lovable_observed(user, filters)
    kinds = dict(LOVABLE_KINDS.values())
    mappings = dict(LOVABLE_MAPPINGS.values())
    return {
        "page": page,
        "pager": _pager(page, filters),
        "kind_totals": [(kinds.get(k, k), n) for k, n in (page.totals.get("kind") or {}).items()],
        "mapping_totals": [
            (mappings.get(k, k), n) for k, n in (page.totals.get("mapping") or {}).items()
        ],
        "unlinked": _unlinked_rows(page.unlinked),
    }


def _shown_value(value: Any) -> str:
    """A readiness fact as page text: never ``None``, ``True`` or a raw moment."""
    from django.utils import timezone

    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "jah" if value else "ei"
    if isinstance(value, dt.datetime):
        return timezone.localtime(value).strftime("%d.%m.%Y kell %H.%M")
    return str(value)


#: How the readiness assessment's source and identity facts are named.
READINESS_PILOT = (
    ("last_run_at", "Viimane käivitus"),
    ("last_run_status", "Viimase käivituse seis"),
    ("last_run_failure_code", "Viimase ebaõnnestumise kood"),
    ("snapshot_taken_at", "Viimane võrreldav lugemine"),
    ("snapshot_freshness", "Lugemise värskus"),
    ("snapshot_verified", "Lugemine on kontrollitud"),
    ("snapshot_sha256_present", "Lugemisel on kontrollsumma"),
    ("topics_in_snapshot", "Teemasid lugemises"),
    ("topics_with_child_records", "Seotud kirjetega teemasid"),
    ("child_records", "Seotud kirjeid"),
)
READINESS_OBSERVATION = (
    ("enabled", "Laiem vaatlus sisse lülitatud"),
    ("last_run_at", "Viimane laiem lugemine"),
    ("last_run_status", "Viimase lugemise seis"),
    ("last_run_failure_code", "Viimase ebaõnnestumise kood"),
    ("last_success_at", "Viimane õnnestunud lugemine"),
    ("freshness", "Lugemise värskus"),
    ("sha256_present", "Lugemisel on kontrollsumma"),
    ("has_baseline", "Võrreldav eelmine lugemine olemas"),
    ("live", "Elavaid teemasid"),
    ("operational", "Operatiivseid teemasid"),
    ("detailed", "Üksikasjadega loetud teemasid"),
    ("index_only", "Ainult indeksis olevaid teemasid"),
)
READINESS_IDENTITY = (
    ("index_from", "Teemade indeksi allikas"),
    ("index_topics", "Teemasid indeksis"),
    ("pilot_linked", "Piloodi seotud teemasid"),
    ("pilot_deleted_in_juristid", "Piloodi teemasid, mis on Juristides kustutatud"),
    ("pilot_closed_in_juristid", "Piloodi teemasid, mis on Juristides suletud"),
    ("operational", "Operatiivseid teemasid"),
    ("operational_pilot_linked", "neist piloodis"),
    ("operational_reference_candidates", "neist sama numbriga teema, seos kinnitamata"),
    ("operational_unmatched", "neist Juristides vastet pole"),
    ("duplicate_source_numbers", "Korduvaid numbreid Lovable'is"),
    ("duplicate_juristid_references", "Korduvaid numbreid Juristides"),
    ("new", "Uusi teemasid viimases laiemas lugemises"),
    ("missing", "Lugemisest kadunud teemasid"),
    ("back", "Lugemisse naasnud teemasid"),
    ("renumbered", "Numbri muutnud teemasid"),
    ("pilot_missing_last_night", "Piloodi teemasid, mis viimasel ööl puudusid"),
    ("pilot_identifier_changed_last_night", "Piloodi teemasid, mille tunnus muutus"),
    ("manual_confirmation", "Inimese kinnitust vajavaid seoseid"),
)
_READINESS_WORDS = {
    "FRESH": "värske (kuni 26 tundi)",
    "STALE": "vananenud",
    "NONE": "puudub",
    "OBSERVATION": "laiem lugemine",
    "PILOT": "piloodi lugemine",
    "SUCCEEDED": "õnnestus",
    "FAILED": "ebaõnnestus",
    "RUNNING": "käib",
    "REVIEW_ONLY": "peatatud ülevaatuseks",
}
READINESS_MAPPING = {"EXACT": "täpne", "APPROXIMATE": "ligikaudne", "NONE": "puudub"}


def _readiness_facts(spec: tuple[tuple[str, str], ...], data: dict[str, Any]) -> list[Any]:
    rows = []
    for key, label in spec:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, str) and value in _READINESS_WORDS:
            value = _READINESS_WORDS[value]
        rows.append((label, _shown_value(value)))
    return rows


def readiness_summary(readiness: Any) -> dict[str, Any]:
    """The readiness assessment's facts and counts, named. Nothing here is a
    Teema's title or value (the per-Teema plan rows are left out), so Haldus
    shows it to a technical administrator as well."""
    from app.legacy_import.lovable_sync_labels import LOVABLE_SIMULATION_LABELS

    source = readiness.source or {}
    return {
        "pilot": _readiness_facts(READINESS_PILOT, source.get("pilot") or {}),
        "observation": _readiness_facts(READINESS_OBSERVATION, source.get("observation") or {}),
        "audit": source.get("audit") or {},
        "identity": _readiness_facts(READINESS_IDENTITY, readiness.identity or {}),
        "simulation": [
            (LOVABLE_SIMULATION_LABELS.get(k, k), n) for k, n in readiness.simulation.items()
        ],
        "fields": [
            {
                "field": f,
                "mapping": READINESS_MAPPING.get(f.mapping, f.mapping),
                "simulated": [
                    (LOVABLE_SIMULATION_LABELS.get(k, k), n) for k, n in f.simulated.items()
                ],
            }
            for f in readiness.fields
        ],
    }


def _lovable_readiness(user: Any, filters: LovableFilters) -> dict[str, Any]:
    from app.legacy_import import lovable_observability as obs

    readiness = obs.readiness(user, now=_lovable_now())
    return {"readiness": readiness, "ready": readiness_summary(readiness)}


_LOVABLE_BUILDERS = {
    "ulevaade": _lovable_overview,
    "oised": _lovable_night_view,
    "ajalugu": _lovable_history,
    "ootel": _lovable_queue_view,
    "vaadeldud": _lovable_observed_view,
    "valmisolek": _lovable_readiness,
}


def _lovable_view(request: HttpRequest) -> str:
    from app.core.request_params import choice_or_default

    return str(choice_or_default(request.GET.get("vaade"), LOVABLE_VIEWS, "ulevaade"))


def _require_lovable_reader(request: HttpRequest) -> None:
    if not may_read_business_content(request.user):
        raise PermissionDenied(LOVABLE_NEEDS_A_READER)


@require_safe
@login_required
def release_notes_lovable(request: HttpRequest) -> HttpResponse:
    """Uuendused → Lovable: what Lovable changed, what Juristid saw and what it
    decided (docs/adr/0192 §11, docs/adr/0201).

    Unlike the Juristid section beside it this is business content — Teema
    titles, owners, dates — so it asks the question every business read asks.
    `login_required` sends a caller with no persona (anonymous, or a shared-gate
    session that has not chosen who is reading) to the doorway; a signed-in
    account without a business role, the technical administrator, is refused
    here. Every Teema the page names has passed `visible_to` inside the read
    service (`app.legacy_import.lovable_observability`), the only thing this
    view asks.

    GET and HEAD only, and read-only all the way down: the service reads the
    readings, decisions and events Juristid already stored. It never reaches
    Lovable.
    """
    _require_lovable_reader(request)
    view = _lovable_view(request)
    filters = _lovable_filters(request, view)
    context: dict[str, Any] = {
        "uuendused_tab": "lovable",
        "lovable_view": view,
        "lovable_views": LOVABLE_VIEWS,
        "filters": filters,
        "offered": LOVABLE_FILTERS.get(view, ()),
        "exportable": view in LOVABLE_EXPORTS,
        "choices": {
            "population": _slug_choices(LOVABLE_POPULATIONS),
            "outcome": _slug_choices(
                LOVABLE_QUEUE_OUTCOMES if view == "ootel" else LOVABLE_RESULTS
            ),
            "kind": _slug_choices(LOVABLE_KINDS),
            "mapping": _slug_choices(LOVABLE_MAPPINGS),
            "field": _lovable_field_choices(),
        },
    }
    context.update(_LOVABLE_BUILDERS[view](request.user, filters))
    return render(request, "core/release_notes_lovable.html", context)


def _all_pages(fetch: Any, info: list[str]) -> tuple[list[Any], Any]:
    """Every page of a service list, up to `LOVABLE_EXPORT_PAGES` pages; the
    information sheet says when there were more."""
    first = fetch(1)
    items = list(first.items)
    for number in range(2, min(first.pages, LOVABLE_EXPORT_PAGES) + 1):
        items.extend(fetch(number).items)
    if first.pages > LOVABLE_EXPORT_PAGES:
        info.append(
            f"Väljavõttes on esimesed {LOVABLE_EXPORT_PAGES} lehekülge {first.pages}-st; "
            "kitsenda filtrit, et näha ülejäänut."
        )
    return items, first


def _unlinked_policy_line() -> str:
    """The information sheet's sentence on unlinked topics (docs/adr/0201 §6)."""
    from app.legacy_import import lovable_observability as obs

    if obs.unlinked_detail() == obs.AGGREGATE:
        return (
            "Juristidega sidumata Lovable'i teemade muudatusi ei loetleta: "
            "ainult nende koguarv (omaniku otsuseni)."
        )
    return (
        "Juristidega sidumata Lovable'i teemade pealkirju ja väärtusi ei näidata "
        "(ainult number, muutuse liik ja väljade nimed)."
    )


def _unlinked_info(info: list[str], total: int) -> None:
    from app.legacy_import import lovable_observability as obs

    if total:
        info.append(obs.unlinked_note(total))


@require_safe
@login_required
def release_notes_lovable_export(request: HttpRequest) -> HttpResponse:
    """`Laadi Excel`: the current view's filtered rows as a workbook.

    The same door as the page — a business reader, GET and HEAD only — and the
    same answers: the rows are exactly what the read service gave this reader,
    over every page, so a Teema the page would not name is not in the file
    (only counted on its information sheet) and a withheld value stays empty.
    Private and never stored (`xlsx_safety.workbook_response`). A GET writes
    one ``EXPORT_GENERATED`` row — the view, the row count and the filter
    names, never a topic or the search text; a HEAD writes none
    (`app.audit.access`, B-01)."""
    from django.utils import timezone

    from app.audit.access import record_export
    from app.core.xlsx_safety import workbook_response
    from app.legacy_import import lovable_export

    _require_lovable_reader(request)
    view = _lovable_view(request)
    if view not in LOVABLE_EXPORTS:
        raise Http404("Sellel vaatel pole Exceli väljavõtet.")
    filters = _lovable_filters(request, view)
    user = request.user
    moment = _lovable_now()
    info = [
        f"Uuendused → Lovable: {LOVABLE_VIEWS[view]}",
        f"Koostatud {lovable_export.moment(moment)} (Tallinna aeg).",
        "Koostatud Juristides salvestatud lugemistest; Lovable'it ei loetud ega muudetud.",
        _unlinked_policy_line(),
        "Pikad väärtused on loetelus lühendatud (veerg «Lühendatud»).",
        *(filters.describe() or ["Filtreid pole."]),
        *filters.errors,
        *filters.notes,
    ]
    if view == "oised":
        readings, first = _all_pages(lambda n: _lovable_nights(user, filters, n), info)
        if filters.attention:
            for reading in readings:
                reading.topics = [t for t in reading.topics if _topic_needs_attention(t)]
        hidden = sum(r.hidden for r in readings)
        if hidden:
            info.append(f"Lisaks {hidden} muudetud teemat, mida sa ei näe: ainult arv.")
        _unlinked_info(info, sum(r.unlinked.total for r in readings))
        workbook = lovable_export.nights_workbook(readings, info=info)
        selected = sum(len(reading.topics) for reading in readings)
    elif view == "ootel":
        items, first = _all_pages(lambda n: _lovable_queue(user, filters, n), info)
        if first.hidden:
            info.append(f"Lisaks {first.hidden} ootel välja teemadel, mida sa ei näe: ainult arv.")
        workbook = lovable_export.queue_workbook(items, info=info)
        selected = len(items)
    else:
        items, first = _all_pages(lambda n: _lovable_observed(user, filters, n), info)
        if first.hidden:
            info.append(f"Lisaks {first.hidden} muutust teemadel, mida sa ei näe: ainult arv.")
        _unlinked_info(info, first.unlinked.total)
        workbook = lovable_export.observed_workbook(items, info=info)
        selected = len(items)
    response = workbook_response(
        workbook, lovable_export.filename(view, timezone.localdate(moment))
    )
    record_export(
        request,
        export=f"release_notes.lovable.{view}",
        file_format="xlsx",
        rows=selected,
        params=request.GET,
        honoured=lovable_export_filters(view),
    )
    return response


@require_safe
def keyboard_shortcuts(request: HttpRequest) -> HttpResponse:
    """Kiirklahvid: every keyboard shortcut Juristid has, on one page.

    The same door as Uuendused and for the same reason: no business data, so no
    persona is needed and none is asked for. The shared gate still stands in
    front of it like every other path; nothing is exempted
    (`tests/test_business_routes_need_a_reader.py`).

    The rows are `app/core/keyboard_shortcuts.py`, where each names the script
    that answers its key — the screens no longer carry hint strips, so this is
    the one place the keys are written down and it must not promise one that
    is gone. GET and HEAD only: there is nothing here to post to.
    """
    return render(
        request,
        "core/keyboard_shortcuts.html",
        {"shortcut_groups": KEYBOARD_SHORTCUT_GROUPS},
    )


SURFACE_TOKENS = (
    "--surface-base",
    "--surface-nav",
    "--surface-raised",
    "--surface-panel",
    "--surface-elevated",
    "--surface-hover",
    "--surface-selected",
    "--surface-document",
)

TEXT_TOKENS = (
    "--text-primary",
    "--text-body",
    "--text-secondary",
    "--text-muted",
    "--accent-link",
)


@login_required
def design_tokens(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "core/design_tokens.html",
        {"surface_tokens": SURFACE_TOKENS, "text_tokens": TEXT_TOKENS},
    )


@login_required
def development_status(request: HttpRequest) -> HttpResponse:
    """What the v2 rebuild could not settle, for the people who can settle it.

    Under `/haldus/`, with the rest of the internal tooling, and not on any
    navigation bar: this is a worklist about the build, not a product screen. It
    renders `app/core/development_status.py` and nothing else — there is no
    Matter, no member and no company content on it, so it needs no visibility
    scoping of its own.

    The gate is the department head or the technical administrator, both read
    from rules that already exist. 404 rather than 403, the convention every
    other restricted surface in this application follows: a 403 would confirm
    the page is there and that somebody else may read it.
    """
    from app.legacy_import.opinion_access import may_use_opinion_queue

    if not (is_department_head(request.user) or may_use_opinion_queue(request.user)):
        raise Http404("Arendusseis on halduse töövahend.")

    return render(
        request,
        "core/development_status.html",
        {"items": DEVELOPMENT_STATUS_ITEMS, "nav_active": "haldus"},
    )


#: How long a browser may keep one year's holidays. The list changes only
#: when the law does, and a release that changes it ships a new `app.js` too.
PUBLIC_HOLIDAYS_CACHE = "private, max-age=86400"


@require_safe
@login_required
def public_holidays(request: HttpRequest, year: int) -> JsonResponse:
    """One year's riigipühad as JSON, for the date boxes' hint (docs/adr/0197).

    The browser asks this rather than knowing the rules itself, so
    `app.core.holidays` stays the one place a holiday is defined. No business
    content: the same answer for every reader, the READER role included, so
    `login_required` is the whole gate — like Disainisüsteem beside it.

    Only the years a date box accepts (`EstonianDateField`, 1990–2100); any
    other year is a 404, so a typo in a script cannot make the server compute
    Easter for year 40000. ``private`` caching, because the response is still
    behind the door: the middleware's `no-store` default is for pages that
    carry records, and this one would otherwise be fetched again on every page.
    """
    if not EARLIEST_DAY.year <= year <= LATEST_DAY.year:
        raise Http404("Riigipühad on teada aastateks 1990–2100.")
    response = JsonResponse(
        {
            "year": year,
            "holidays": {
                day.isoformat(): name for day, name in estonian_public_holidays(year).items()
            },
        },
        json_dumps_params={"ensure_ascii": False},
    )
    response["Cache-Control"] = PUBLIC_HOLIDAYS_CACHE
    return response


def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    """What a refused CSRF check looks like (`CSRF_FAILURE_VIEW`).

    The check is Django's own and is not touched: this runs only after it has
    already refused, and it decides nothing but the answer's shape. Two shapes,
    because there are two kinds of caller.

    **An htmx request gets an empty 403 marked as a CSRF refusal.** The page
    that sent it is still on screen with the text in it, and `static/js/app.js`
    turns the marker into an Estonian sentence beside the form — the page is out
    of date, copy what you typed and reload. It used to get Django's English
    debug-or-plain 403, which htmx discarded, so `Salvesta` did nothing at all
    (ENG-012). It is not re-sent with a fresh token, and the page is not
    reloaded for the person: the usual cause is a persona chosen in another
    tab, and a quiet retry would save this tab's text under somebody else's
    name.

    **A navigation gets the same explanation as a page** (`403_csrf.html`).

    ``reason`` is Django's diagnostic. It is already in the security log that
    `django.security.csrf` writes, and it is shown to nobody.
    """
    if is_htmx(request):
        response = HttpResponse(status=403)
        response[FAILURE_HEADER] = "csrf"
        response["Cache-Control"] = NO_STORE
        return response
    return render(request, "403_csrf.html", status=403)
