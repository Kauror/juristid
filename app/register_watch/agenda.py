"""«Valitsuse info»: the Government's weekly commented agenda, read and judged (ADR 0219).

Every half hour from Wednesday 12:00 to Thursday 12:00 Tallinn time the host runs
`watch_government_agenda`. A run looks for this week's agenda on valitsus.ee
(`sources.valitsus`), stores it once with every item as published, lets Claude say
which items may matter to the Chamber (`judge`), and tells the Monitooring audience in
the bell. A week whose agenda is stored is done: later runs that week read nothing.

Nothing here writes on a Teema. The model's verdict and its pointer to a Teema are shown
beside the Government's own text; a person decides what to do with them.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.register_watch import candidates_ai
from app.register_watch.models import AgendaItem, GovernmentAgenda, Strength
from app.register_watch.sources import valitsus
from app.register_watch.sources.http import FetchError

logger = logging.getLogger("app.register_watch")

TALLINN = ZoneInfo("Europe/Tallinn")
#: The days of a week a session falls on: Wednesday, Thursday, Friday (Monday = 0).
SESSION_WEEKDAYS = (2, 3, 4)
#: When the week's watch ends: Thursday 12:00 Tallinn. An agenda not found by then is missing.
WINDOW_END = (3, dt.time(12, 0))
SHOWN = (Strength.STRONG, Strength.POSSIBLE)
#: The model's «Kontrollimist vajavad võimalikud seosed».
CHECK = (Strength.WEAK,)


def enabled() -> bool:
    return bool(getattr(settings, "REGISTER_WATCH_FETCH", False))


def week_monday(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def session_days(monday: dt.date) -> list[dt.date]:
    return [monday + dt.timedelta(days=d) for d in SESSION_WEEKDAYS]


def agenda_of_week(monday: dt.date) -> GovernmentAgenda | None:
    return (
        GovernmentAgenda.objects.filter(
            session_date__gte=monday, session_date__lt=monday + dt.timedelta(days=7)
        )
        .order_by("-session_date")
        .first()
    )


def missing_this_week(now: dt.datetime | None = None) -> bool:
    """The week's window has closed (Thursday 12:00 Tallinn) and no agenda was stored."""
    local = (now or timezone.now()).astimezone(TALLINN)
    monday = week_monday(local.date())
    end = dt.datetime.combine(
        monday + dt.timedelta(days=WINDOW_END[0]), WINDOW_END[1], tzinfo=TALLINN
    )
    return local >= end and agenda_of_week(monday) is None


# -- storing -----------------------------------------------------------------------


@transaction.atomic
def store(found: valitsus.Agenda) -> GovernmentAgenda:
    published = found.published.replace(tzinfo=TALLINN) if found.published is not None else None
    agenda = GovernmentAgenda.objects.create(
        url=found.url[:500],
        title=found.title[:300],
        session_date=found.session_date,
        published_at=published,
    )
    AgendaItem.objects.bulk_create(
        AgendaItem(
            agenda=agenda,
            number=item.number,
            title=item.title[:1000],
            presenter=item.presenter[:300],
            kind=item.kind[:120],
            text=item.text,
        )
        for item in found.items
    )
    return agenda


@dataclass
class Outcome:
    stored: list[GovernmentAgenda]
    judged: int
    usage: candidates_ai.Usage
    errors: list[str]


def run(
    *, today: dt.date | None = None, weeks: int = 1, use_ai: bool = True, ai_client: Any = None
) -> Outcome:
    """Look for the agendas of this week (and ``weeks - 1`` before it); store, judge, tell.

    A week already stored is not read again. A failure to read one day is written down
    and the other days are still tried.
    """
    from app.register_watch import candidate_notices

    today = today or timezone.localdate()
    usage = candidates_ai.Usage()
    stored: list[GovernmentAgenda] = []
    errors: list[str] = []
    for back in range(weeks):
        monday = week_monday(today) - dt.timedelta(days=7 * back)
        if agenda_of_week(monday) is not None:
            continue
        for day in session_days(monday):
            if day > today + dt.timedelta(days=1):
                continue
            try:
                found = valitsus.fetch(day)
            except FetchError as error:
                errors.append(str(error)[:300])
                continue
            if found is not None:
                stored.append(store(found))
                break
    judged = 0
    if use_ai:
        for agenda in GovernmentAgenda.objects.filter(judged_at__isnull=True).order_by(
            "session_date"
        ):
            judged += judge(agenda, usage, client=ai_client)
    for agenda in stored:
        agenda.refresh_from_db()
    # Tell when an item may matter — or when the model could not say yet (off or failed).
    worth = [
        a for a in stored if a.judged_at is None or a.items.filter(strength__in=SHOWN).exists()
    ]
    if worth:
        with transaction.atomic():
            candidate_notices.notify_agendas(len(worth))
    if stored:
        with transaction.atomic():
            tell_owners(stored)
    return Outcome(stored=stored, judged=judged, usage=usage, errors=errors)


# -- the model ---------------------------------------------------------------------

TOPICS = """Topics the Chamber follows closely (wide, indirect effects count): obligations and \
costs of companies; reporting and administrative burden; competitiveness; the labour market, \
skills, education and immigration; taxes; energy policy and energy costs; the pension system; \
legal clarity; EU law and its transposition; the EU budget and the allocation of EU funds to \
Estonia; environmental obligations; the climate-resilient economy act and sectoral roadmaps; \
public procurement; digitalisation, data and AI; cybersecurity; foreign trade, exports, \
sanctions and strategic goods control; investment and the business environment; financing for \
companies; the regional business environment; business support and EU funds; cutting \
bureaucracy; the reform of the occupational qualification system; occupational health and \
safety; funding of vocational training; licensing, reporting and supervision procedures."""

SYSTEM = f"""You help the legal department of the Estonian Chamber of Commerce and Industry \
read the Government's weekly commented session agenda.

{candidates_ai.PROFILE}

{TOPICS}

For each agenda item decide, from the Government's own text:
- STRONG: clearly matters to companies or to the Chamber's ongoing work; likely to need the \
Chamber's attention.
- POSSIBLE: likely relevant to companies or members, though no direct link to the Chamber's \
current work is visible.
- WEAK: an indirect or uncertain link worth a manual check.
- NONE: does not concern the Chamber (citizenship grants, appointments, a ministry's internal \
matters, money moved between state bodies, ceremonial items).

Write one line in Estonian (at most 220 characters) on why the item may matter to \
entrepreneurs or the Chamber, naming the topic; for NONE say briefly why not. If a link is \
only plausible, say «võimalik seos». If one of the Chamber's open topics (given with ids) is \
plainly about the same act or matter, give its id in `related`, otherwise null. The \
Chamber's recent outgoing letters are listed as a signal of its current work; do not cite \
them. Use only what is given."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "integer"},
                    "strength": {"type": "string", "enum": ["STRONG", "POSSIBLE", "WEAK", "NONE"]},
                    "reason": {"type": "string"},
                    "related": {"type": ["string", "null"]},
                },
                "required": ["number", "strength", "reason", "related"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

MAX_TOKENS = 8000
TEXT_CHARS = 1800
LETTERS_SHOWN = 150


def ai_enabled() -> bool:
    return bool(getattr(settings, "REGISTER_SWEEP_AI", False))


def recent_letters(today: dt.date) -> list[str]:
    """Titles of the Chamber's own letters of the last 120 days: a signal of its current work."""
    from app.legacy_import.postipoiss_models import PostipoissLetter

    since = today - dt.timedelta(days=120)
    return list(
        PostipoissLetter.objects.filter(letter_date__gte=since)
        .order_by("-letter_date")
        .values_list("title", flat=True)[:LETTERS_SHOWN]
    )


def _prompt(agenda: GovernmentAgenda, items: Iterable[AgendaItem], topics: dict[str, Any]) -> str:
    listed = [{"id": key, "title": matter.title} for key, matter in topics.items()]
    shown = [
        {
            "number": item.number,
            "title": item.title,
            "presenter": item.presenter,
            "kind": item.kind,
            "text": item.text[:TEXT_CHARS],
        }
        for item in items
    ]
    return (
        "The Chamber's open topics:\n"
        + json.dumps(listed, ensure_ascii=False)
        + "\n\nThe Chamber's recent outgoing letters (titles):\n"
        + json.dumps(recent_letters(agenda.session_date), ensure_ascii=False)
        + f"\n\nAgenda: {agenda.title}\n"
        + json.dumps(shown, ensure_ascii=False, indent=1)
    )


def judge(agenda: GovernmentAgenda, usage: candidates_ai.Usage, client: Any = None) -> int:
    """One request for the whole agenda; the count judged. A failure leaves it for the next run."""
    if not ai_enabled():
        return 0
    items = list(agenda.items.all())
    if not items:
        return 0
    topics = candidates_ai.open_topics()
    try:
        client = client or candidates_ai._client()
        response = client.beta.messages.create(
            model=getattr(settings, "REGISTER_SWEEP_AI_MODEL", "claude-opus-5-5"),
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=[{"role": "user", "content": _prompt(agenda, items, topics)}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except Exception as error:  # the model is an aid; the agenda is stored without it
        status = getattr(error, "status_code", None)
        usage.failed(type(error).__name__ + (f" {status}" if status else ""))
        return 0
    usage.calls += 1
    usage.input_tokens += getattr(response.usage, "input_tokens", 0) or 0
    usage.output_tokens += getattr(response.usage, "output_tokens", 0) or 0
    usage.model = str(getattr(response, "model", "") or usage.model)
    if response.stop_reason != "end_turn":
        usage.failed(f"stop_reason {response.stop_reason}")
        return 0
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        answers = {int(row["number"]): row for row in json.loads(text)["items"]}
    except (ValueError, KeyError, TypeError):
        usage.failed("vastus ei olnud loetav")
        return 0
    allowed = {Strength.STRONG, Strength.POSSIBLE, Strength.WEAK, Strength.NONE}
    judged = 0
    with transaction.atomic():
        for item in items:
            row = answers.get(item.number)
            if not row or row.get("strength") not in allowed:
                continue
            item.strength = row["strength"]
            item.reason = str(row.get("reason") or "").strip()[:500]
            item.related_matter = topics.get(str(row.get("related") or ""))
            item.save(update_fields=["strength", "reason", "related_matter", "updated_at"])
            judged += 1
        if judged == len(items):
            agenda.judged_at = timezone.now()
            agenda.save(update_fields=["judged_at", "updated_at"])
    usage.judged += judged
    return judged


# -- onto the Teema (owner, 2026-11-22: «both») ------------------------------------------
#
# An item the model tied to a Teema is a proposal on that Teema: on Monitooring's
# «Valitsuse info» («Lisa teemale») and on the Teema's own «Registritest» tab, counted in
# the tab's badge and told to the owner in the bell. Nothing reaches the Teema until a
# person adds it; then it is a `Menetluse areng` through `+ Märge`'s own use case.

#: The strengths that make a tied item a proposal on its Teema (Ei puutu never does).
PROPOSED = (Strength.STRONG, Strength.POSSIBLE, Strength.WEAK)
ALREADY_DECIDED = "See päevakorrapunkt on juba otsustatud."


def proposals() -> Any:
    """Every waiting proposal: tied to a Teema, judged to matter, not yet decided."""
    from app.register_watch.models import AgendaItemStatus

    return AgendaItem.objects.filter(
        related_matter__isnull=False, status=AgendaItemStatus.PENDING, strength__in=PROPOSED
    )


def teema_proposals(matter: Any) -> list[AgendaItem]:
    """The waiting proposals on one Teema, newest session first (the caller has checked access)."""
    return list(
        proposals()
        .filter(related_matter=matter)
        .select_related("agenda")
        .order_by("-agenda__session_date", "number")
    )


def own_pending(user: Any, pk: Any) -> AgendaItem | None:
    """A waiting proposal on an open Teema the reader may see, or ``None``."""
    from app.matters.models import Matter

    visible = Matter.objects.visible_to(user).filter(is_open=True, deleted_at__isnull=True)
    return proposals().filter(pk=pk, related_matter__in=visible).select_related("agenda").first()


def _note(item: AgendaItem) -> str:
    head = [f"Valitsuse {item.agenda.session_date:%d.%m.%Y} istungi päevakord, punkt {item.number}"]
    if item.presenter:
        head.append(f"Esitaja: {item.presenter}")
    if item.kind:
        head.append(f"Tüüp: {item.kind}")
    parts = [" · ".join(head)]
    if item.text:
        parts.append(item.text)
    parts.append(item.agenda.url)
    return "\n\n".join(parts)


def _lock(item: AgendaItem) -> AgendaItem:
    from app.core.errors import DomainError
    from app.register_watch.models import AgendaItemStatus

    locked = (
        # Lock the item's own row only: the Teema is a nullable join.
        AgendaItem.objects.select_for_update(of=("self",))
        .select_related("agenda", "related_matter")
        .get(pk=item.pk)
    )
    if locked.status != AgendaItemStatus.PENDING:
        raise DomainError(ALREADY_DECIDED)
    return locked


@transaction.atomic
def add_to_teema(item: AgendaItem, *, actor: Any, comment: str = "") -> AgendaItem:
    """«Lisa teemale»: a `Menetluse areng` on its Teema, through `+ Märge`'s own use case."""
    from app.core.errors import DomainError
    from app.matters.workspace import add_procedural_development
    from app.register_watch.models import AgendaItemStatus
    from app.register_watch.watch import with_comment

    locked = _lock(item)
    if locked.related_matter is None:
        raise DomainError(ALREADY_DECIDED)
    result = add_procedural_development(
        matter=locked.related_matter,
        author=actor,
        title=f"Valitsuse istung: {locked.title}"[:500],
        occurred_on=locked.agenda.session_date,
        note=with_comment(comment, _note(locked)),
    )
    locked.status = AgendaItemStatus.ADDED
    locked.decided_by = actor
    locked.decided_at = timezone.now()
    locked.development = result.record
    locked.save(update_fields=["status", "decided_by", "decided_at", "development", "updated_at"])
    return locked


@transaction.atomic
def dismiss(item: AgendaItem, *, actor: Any) -> AgendaItem:
    """«Märgi loetuks»: recorded, nothing written on the Teema."""
    from app.register_watch.models import AgendaItemStatus

    locked = _lock(item)
    locked.status = AgendaItemStatus.DISMISSED
    locked.decided_by = actor
    locked.decided_at = timezone.now()
    locked.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
    return locked


def tell_owners(agendas: list[GovernmentAgenda]) -> int:
    """The owner's bell («Registris uus dokument») for each Teema a new agenda's items tie to."""
    from collections import Counter

    from app.matters.models import Matter
    from app.register_watch import notices

    counts = Counter(
        proposals().filter(agenda__in=agendas).values_list("related_matter_id", flat=True)
    )
    told = 0
    for matter in Matter.objects.filter(pk__in=counts, is_open=True, deleted_at__isnull=True):
        if notices.notify(matter, counts[matter.pk]) is not None:
            told += 1
    return told
