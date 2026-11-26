"""«Valitsuse info»: the Government's weekly commented agenda, read and judged (ADR 0219).

Every night, in its own lane from 01:00 Tallinn time, the nightly cycle (ADR 0229)
runs `watch_government_agenda`; there is no daytime reading. A run finds this week's
agenda through the links valitsus.ee publishes (`sources.valitsus.discover`), stores
it once with every item as published, lets Claude say which items may matter to the
Chamber (`judge`), and tells the Monitooring audience in the bell. Until the week's
last cycle (Friday's) a stored agenda is read once more each night: a change the
Government makes is a revision (`revise`) — kept with the earlier text, never over a
person's decision, and only new or changed items are judged and told again.

Nothing here writes on a Teema. The model's verdict and its pointer to a Teema are shown
beside the Government's own text; a person decides what to do with them.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from app.register_watch import ai_call, candidates_ai, outbound
from app.register_watch.house_style import KODA_STYLE
from app.register_watch.models import (
    AgendaCheckState,
    AgendaItem,
    AgendaItemStatus,
    GovernmentAgenda,
    GovernmentAgendaCheck,
    GovernmentAgendaRevision,
    Strength,
)
from app.register_watch.sources import http, valitsus

logger = logging.getLogger("app.register_watch")

TALLINN = ZoneInfo("Europe/Tallinn")
#: The days of a week a session falls on: Wednesday, Thursday, Friday (Monday = 0).
SESSION_WEEKDAYS = (2, 3, 4)
#: The last nightly cycle that reads a week's agenda for its session days starts on
#: Friday (Monday = 0): an agenda published on Wednesday is found by the cycle going
#: into Thursday, one published later by the one going into Friday (owner, 25.11.2026:
#: accepted, no daytime reading). The week's agenda is missing only once that
#: Friday cycle's finish-by for the agenda has passed.
LAST_CYCLE_WEEKDAY = 4
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


def window_end(monday: dt.date) -> dt.datetime:
    """When the week's last nightly reading should have finished: the agenda step's
    finish-by in the cycle starting at 00:00 on the week's Friday (Tallinn)."""
    from app.core import nightly_schedule

    friday = nightly_schedule.cycle_start_on(monday + dt.timedelta(days=LAST_CYCLE_WEEKDAY))
    return nightly_schedule.after_start(friday, nightly_schedule.FINISH_BY["government-agenda"])


def missing_this_week(now: dt.datetime | None = None) -> bool:
    """The week's last nightly reading is over (`window_end`) and no agenda was stored."""
    local = (now or timezone.now()).astimezone(TALLINN)
    monday = week_monday(local.date())
    return local >= window_end(monday) and agenda_of_week(monday) is None


# -- storing -----------------------------------------------------------------------


def _norm(text: str) -> str:
    return " ".join(text.split())


def _row(number: int, title: str, presenter: str, kind: str, text: str) -> tuple[Any, ...]:
    """One item as stored: the same truncation as `store`, whitespace normalised."""
    return (number, _norm(title[:1000]), _norm(presenter[:300]), _norm(kind[:120]), _norm(text))


def fingerprint(rows: Iterable[tuple[Any, ...]]) -> str:
    return hashlib.sha256(json.dumps(sorted(rows), ensure_ascii=False).encode("utf-8")).hexdigest()


def source_rows(found: valitsus.Agenda) -> list[tuple[Any, ...]]:
    return [_row(i.number, i.title, i.presenter, i.kind, i.text) for i in found.items]


def stored_rows(agenda: GovernmentAgenda) -> list[tuple[Any, ...]]:
    return [
        _row(i.number, i.title, i.presenter, i.kind, i.text)
        for i in agenda.items.filter(source_removed_at__isnull=True)
    ]


def _published(found: valitsus.Agenda) -> dt.datetime | None:
    return found.published.replace(tzinfo=TALLINN) if found.published is not None else None


@transaction.atomic
def store(found: valitsus.Agenda) -> GovernmentAgenda:
    agenda = GovernmentAgenda.objects.create(
        url=found.url[:500],
        title=found.title[:300],
        session_date=found.session_date,
        published_at=_published(found),
        source_fingerprint=fingerprint(source_rows(found)),
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


# -- revisions (r95) ---------------------------------------------------------------


@dataclass
class Revised:
    revision: GovernmentAgendaRevision
    #: Items new to the agenda or reopened for the model: only these are told.
    fresh: list[AgendaItem]


def _snapshot(item: AgendaItem) -> dict[str, Any]:
    return {
        "number": item.number,
        "title": item.title,
        "presenter": item.presenter,
        "kind": item.kind,
        "text": item.text,
        "strength": item.strength,
        "reason": item.reason,
        "status": item.status,
    }


def _source_dict(item: valitsus.AgendaItem) -> dict[str, Any]:
    return {
        "number": item.number,
        "title": item.title[:1000],
        "presenter": item.presenter[:300],
        "kind": item.kind[:120],
        "text": item.text,
    }


def _match(found: valitsus.Agenda, live: list[AgendaItem]) -> list[tuple[Any, AgendaItem | None]]:
    """Pair each published item with a stored one: by title first, then by number (a
    reworded title keeps its place). Unpaired published items are new."""
    by_title: dict[str, list[AgendaItem]] = {}
    for item in live:
        by_title.setdefault(_norm(item.title[:1000]).casefold(), []).append(item)
    taken: set[Any] = set()
    pairs: list[tuple[Any, AgendaItem | None]] = []
    for new in found.items:
        same = by_title.get(_norm(new.title[:1000]).casefold()) or []
        match = next((o for o in same if o.pk not in taken), None)
        if match is not None:
            taken.add(match.pk)
        pairs.append((new, match))
    for index, (new, match) in enumerate(pairs):
        if match is None:
            other = next((o for o in live if o.pk not in taken and o.number == new.number), None)
            if other is not None:
                taken.add(other.pk)
                pairs[index] = (new, other)
    return pairs


def revise(agenda: GovernmentAgenda, found: valitsus.Agenda) -> Revised | None:
    """Bring a stored agenda up to the Government's changed page; None if unchanged.

    * A waiting item takes the new text; when its title or explanation changed, the
      model's verdict is dropped (kept in the revision) so it is judged again.
    * An item a person decided keeps what the person saw; only ``revised_at`` marks it.
    * A new item is added unjudged; an item gone from the page is marked
      ``source_removed_at``, never deleted.
    * The revision row keeps every earlier text. The same page read again is no
      revision (`GovernmentAgenda.source_fingerprint`), so nothing is told twice.
    """
    new_print = fingerprint(source_rows(found))
    with transaction.atomic():
        locked = GovernmentAgenda.objects.select_for_update().get(pk=agenda.pk)
        old_print = locked.source_fingerprint or fingerprint(stored_rows(locked))
        if new_print == old_print:
            if not locked.source_fingerprint:
                locked.source_fingerprint = new_print
                locked.save(update_fields=["source_fingerprint", "updated_at"])
            return None
        now = timezone.now()
        live = list(
            AgendaItem.objects.select_for_update()
            .filter(agenda=locked, source_removed_at__isnull=True)
            .order_by("number")
        )
        pairs = _match(found, live)
        kept = {old.pk for _new, old in pairs if old is not None}
        changes: list[dict[str, Any]] = []
        fresh: list[AgendaItem] = []
        removed = [o for o in live if o.pk not in kept]
        for gone in removed:
            changes.append(
                {
                    "number": gone.number,
                    "title": gone.title,
                    "change": "removed",
                    "before": _snapshot(gone),
                    "after": None,
                    "decided": gone.status != AgendaItemStatus.PENDING,
                }
            )
            gone.source_removed_at = now
            gone.save(update_fields=["source_removed_at", "updated_at"])
        before_numbers = {old.pk: old.number for _new, old in pairs if old is not None}
        for new, old in pairs:  # out of the way first: a live number is unique
            if old is not None and old.number != new.number:
                old.number = old.number + 1000
                old.save(update_fields=["number", "updated_at"])
        added = changed = 0
        for new, old in pairs:
            after = _source_dict(new)
            if old is None:
                fresh.append(
                    AgendaItem.objects.create(
                        agenda=locked,
                        number=new.number,
                        title=after["title"],
                        presenter=after["presenter"],
                        kind=after["kind"],
                        text=new.text,
                    )
                )
                added += 1
                changes.append(
                    {
                        "number": new.number,
                        "title": after["title"],
                        "change": "added",
                        "before": None,
                        "after": after,
                        "decided": False,
                    }
                )
                continue
            before = _snapshot(old)
            before["number"] = before_numbers[old.pk]
            content = _row(0, new.title, new.presenter, new.kind, new.text) != _row(
                0, old.title, old.presenter, old.kind, old.text
            )
            if not content and before["number"] == new.number:
                continue
            decided = old.status != AgendaItemStatus.PENDING
            changes.append(
                {
                    "number": new.number,
                    "title": after["title"],
                    "change": "changed" if content else "renumbered",
                    "before": before,
                    "after": after,
                    "decided": decided,
                }
            )
            fields = ["number", "updated_at"]
            old.number = new.number
            if content:
                changed += 1
                old.revised_at = now
                fields.append("revised_at")
                meaning = (_norm(old.title), _norm(old.text)) != (
                    _norm(after["title"]),
                    _norm(new.text),
                )
                if not decided:
                    old.title = after["title"]
                    old.presenter = after["presenter"]
                    old.kind = after["kind"]
                    old.text = new.text
                    fields += ["title", "presenter", "kind", "text"]
                    if meaning:
                        if old.strength != Strength.UNJUDGED:
                            old.strength, old.reason, old.related_matter = (
                                Strength.UNJUDGED,
                                "",
                                None,
                            )
                            fields += ["strength", "reason", "related_matter"]
                        fresh.append(old)
            old.save(update_fields=fields)
        revision = GovernmentAgendaRevision.objects.create(
            agenda=locked,
            previous_fingerprint=locked.source_fingerprint,
            fingerprint=new_print,
            published_at=_published(found),
            added=added,
            changed=changed,
            removed=len(removed),
            changes=changes,
        )
        locked.source_fingerprint = new_print
        locked.revised_at = now
        update = ["source_fingerprint", "revised_at", "updated_at"]
        if fresh:
            # New or changed text is worth one fresh answer, with its own attempts.
            locked.judged_at = None
            locked.ai_attempts = 0
            update += ["judged_at", "ai_attempts"]
        locked.save(update_fields=update)
    logger.info(
        "government agenda %s revised: +%d ~%d -%d",
        locked.session_date,
        added,
        changed,
        len(removed),
    )
    return Revised(revision=revision, fresh=fresh)


def _reread(agenda: GovernmentAgenda) -> valitsus.Agenda:
    """The stored agenda's page read again, as that session's agenda, or `FetchError`."""
    from urllib.parse import urlsplit

    parts = urlsplit(agenda.url)
    if parts.hostname != valitsus.HOST or not parts.path.startswith("/"):
        raise valitsus.FetchError(f"{agenda.url[:120]} ei ole {valitsus.HOST} aadress")
    found = valitsus.parse(agenda.url, valitsus.get_text(valitsus.HOST, parts.path))
    if found.session_date != agenda.session_date:
        raise valitsus.FetchError(
            f"{valitsus.HOST}{parts.path} on teise istungi päevakord ({found.session_date})"
        )
    return found


@dataclass
class Outcome:
    stored: list[GovernmentAgenda]
    judged: int
    usage: candidates_ai.Usage
    #: Genuine failures only: refused access, 429/5xx/timeouts, unreadable pages, and
    #: discovery pages that could not be read — even in a run that stored an agenda.
    errors: list[str]
    #: Every week's verdict for this run, one ledger row each (r95).
    checks: list[GovernmentAgendaCheck] = field(default_factory=list)
    #: Revisions of stored agendas found this run.
    revised: list[GovernmentAgendaRevision] = field(default_factory=list)
    #: The site's listings were needed and could not be read (`valitsus.Discovery.ok`).
    discovery_failed: bool = False
    #: The nightly step's deadline (SEC-010, ADR 0231) stopped reading or judging:
    #: what was left is read on the next night. Always also among `errors`.
    out_of_time: bool = False

    def count(self, *states: str) -> int:
        return sum(1 for check in self.checks if check.state in states)

    @property
    def missing(self) -> bool:
        return any(check.missing for check in self.checks)

    def note(self) -> str:
        """The evidence of the newest week without an agenda: session day and status."""
        for check in self.checks:
            if check.state not in (
                AgendaCheckState.STORED,
                AgendaCheckState.ALREADY_STORED,
                AgendaCheckState.REVISED,
            ):
                day = check.session_date.isoformat() if check.session_date else str(check.week)
                status = f", HTTP {check.http_status}" if check.http_status else ""
                return f"{day}{status}"
        return ""


#: A week's verdict, worst first: one failure outweighs a waiting day (r95).
_WEEK_ORDER = (
    valitsus.SOURCE_ERROR,
    valitsus.PARSE_ERROR,
    valitsus.ACCESS_REFUSED,
    valitsus.UNCONFIRMED,
    valitsus.INACCESSIBLE,
    valitsus.NOT_PUBLISHED,
    valitsus.NOT_FOUND,
)


def _control() -> tuple[str, dt.date] | None:
    """The newest stored agenda's address on valitsus.ee and its session day: the page
    `look` asks to tell an unpublished page from a refusal (`valitsus.site_answers`)."""
    from urllib.parse import urlsplit

    for url, session in GovernmentAgenda.objects.order_by("-session_date").values_list(
        "url", "session_date"
    )[:1]:
        parts = urlsplit(url)
        if parts.hostname == valitsus.HOST and parts.path.startswith("/"):
            return parts.path, session
    return None


def _last_attempt(monday: dt.date, today: dt.date) -> bool:
    """The night reading ``today`` is the week's last (Friday's cycle), or later."""
    return today >= monday + dt.timedelta(days=LAST_CYCLE_WEEKDAY)


def _add_usage(total: candidates_ai.Usage, part: candidates_ai.Usage) -> None:
    total.calls += part.calls
    total.input_tokens += part.input_tokens
    total.output_tokens += part.output_tokens
    total.judged += part.judged
    total.failures += part.failures
    total.letters_left_out += part.letters_left_out
    total.topics_left_out = max(total.topics_left_out, part.topics_left_out)
    total.model = part.model or total.model
    total.last_failure = part.last_failure or total.last_failure


def run(
    *, today: dt.date | None = None, weeks: int = 1, use_ai: bool = True, ai_client: Any = None
) -> Outcome:
    """Look for the agendas of this week (and ``weeks - 1`` before it); store, judge, tell.

    A week already stored is not looked for again; until its last cycle the stored
    page is re-read once for revisions. A failure to read one day is written down
    and the other days are still tried; a failure never hides behind a stored
    agenda (`Outcome.errors`, `GovernmentAgendaCheck.source_failures`).
    """
    from app.register_watch import candidate_notices

    today = today or timezone.localdate()
    usage = candidates_ai.Usage()
    stored: list[GovernmentAgenda] = []
    errors: list[str] = []
    verdicts: list[dict[str, Any]] = []
    control_answer: list[bool | None] = []
    discovered: list[valitsus.Discovery] = []
    revisions: list[Revised] = []

    def control() -> bool | None:
        # One control read per run, and only when a 403 needs telling apart.
        if not control_answer:
            known = _control()
            control_answer.append(valitsus.site_answers(*known) if known else None)
        return control_answer[0]

    def discovery() -> valitsus.Discovery:
        # The site's listings are read once per run, and only when a week needs them.
        if not discovered:
            found = valitsus.discover()
            discovered.append(found)
            for path, error in found.failures:
                errors.append(f"{valitsus.HOST}{path}: {str(error)[:200]}")
        return discovered[0]

    for back in range(weeks):
        monday = week_monday(today) - dt.timedelta(days=7 * back)
        known = agenda_of_week(monday)
        if known is not None:
            verdict: dict[str, Any] = {
                "week": monday,
                "state": AgendaCheckState.ALREADY_STORED,
                "session_date": known.session_date,
                "url": known.url[:500],
                "agenda": known,
            }
            if back == 0 and not _last_attempt(monday, today - dt.timedelta(days=1)):
                # The week is still being read (through Friday's cycle): the
                # Government may still change its agenda.
                try:
                    changed = revise(known, _reread(known))
                except valitsus.FetchError as error:
                    detail = f"{valitsus.HOST}: muudatuste kontroll: {str(error)[:200]}"
                    errors.append(detail)
                    verdict.update(detail=detail[:300], http_status=error.status, source_failures=1)
                else:
                    if changed is not None:
                        revisions.append(changed)
                        r = changed.revision
                        verdict.update(
                            state=AgendaCheckState.REVISED,
                            detail=f"lisandus {r.added}, muutus {r.changed}, eemaldati {r.removed}",
                        )
            verdicts.append(verdict)
            continue
        found = discovery()
        looks: list[valitsus.Lookup] = []
        week_agenda: GovernmentAgenda | None = None
        for day in session_days(monday):
            if day > today + dt.timedelta(days=1):
                continue
            look = valitsus.look(day, today=today, discovery=found, control=control)
            looks.append(look)
            # Every failure on the way, even when the agenda is found in the end; a
            # source or parse failure is among them already, a refusal is not.
            errors.extend(problem[:300] for problem in look.problems)
            if look.state == valitsus.ACCESS_REFUSED:
                errors.append(look.detail[:300])
            if look.agenda is not None:
                week_agenda = store(look.agenda)
                stored.append(week_agenda)
                break
        verdict = _week_verdict(monday, today, looks, week_agenda)
        verdict.update(
            discovery_ok=found.ok,
            discovery=found.summary(),
            source_failures=min(
                len(found.failures) + sum(len(look.problems) for look in looks), 32767
            ),
        )
        verdicts.append(verdict)
    judged = 0
    # Agendas the model gave verdicts in this run: a retry's proposals reach their
    # owners too, not only a newly stored agenda's (audit HR-02 review).
    answered: list[GovernmentAgenda] = []
    #: The model's use per week, written on that week's row only (r95: a multi-week
    #: run no longer repeats the run's total on every week).
    per_week: dict[dt.date, candidates_ai.Usage] = {}
    if use_ai:
        # Only an agenda no answer was read for, and only `MAX_ATTEMPTS` times: a
        # failing model is not asked about the same week every night forever.
        for agenda in GovernmentAgenda.objects.filter(
            judged_at__isnull=True, ai_attempts__lt=MAX_ATTEMPTS
        ).order_by("session_date"):
            if http.out_of_time():
                # The step's time is spent (ADR 0231): no request is started, and
                # no attempt is charged — the agenda is judged on a later night.
                errors.append(f"{valitsus.HOST}: päevakorra hindamine: {http.OUT_OF_TIME}")
                break
            part = candidates_ai.Usage()
            count = judge(agenda, part, client=ai_client)
            _add_usage(usage, part)
            if not (part.calls or part.failures) and http.out_of_time():
                # The time ran out just before the request (`judge` charged nothing).
                errors.append(f"{valitsus.HOST}: päevakorra hindamine: {http.OUT_OF_TIME}")
                break
            if not (part.calls or part.failures):
                continue  # the model was off or had nothing to be asked
            week = week_monday(agenda.session_date)
            _add_usage(per_week.setdefault(week, candidates_ai.Usage()), part)
            if not any(v["week"] == week for v in verdicts):
                # A retry for a week this run did not look at gets its own row.
                verdicts.append(
                    {
                        "week": week,
                        "state": AgendaCheckState.ALREADY_STORED,
                        "session_date": agenda.session_date,
                        "url": agenda.url[:500],
                        "agenda": agenda,
                    }
                )
            judged += count
            if count:
                answered.append(agenda)
    for agenda in stored:
        agenda.refresh_from_db()
    fresh_pks = {item.pk for revised in revisions for item in revised.fresh}
    revised_agendas = {revised.revision.agenda_id for revised in revisions}
    # Tell when an item may matter — or when the model could not say yet: off,
    # failed, or an answer that left items «Hindamata» (ADR 0219 §5). A revised
    # agenda counts only for its new or changed items.
    worth = sum(1 for a in stored if a.items.filter(_shown_or_waiting()).exists())
    worth += sum(
        1
        for pk in revised_agendas
        if AgendaItem.objects.filter(pk__in=fresh_pks, agenda_id=pk)
        .filter(_shown_or_waiting())
        .exists()
    )
    if worth:
        with transaction.atomic():
            candidate_notices.notify_agendas(worth)
    told = {a.pk: a for a in (*stored, *answered) if a.pk not in revised_agendas}
    with transaction.atomic():
        if told:
            tell_owners(list(told.values()))
        if fresh_pks:
            tell_owners(
                list(GovernmentAgenda.objects.filter(pk__in=revised_agendas)), only=fresh_pks
            )
    checks = []
    for verdict in verdicts:
        used = per_week.get(verdict["week"])
        checks.append(
            GovernmentAgendaCheck.objects.create(
                **verdict,
                ai_calls=min(used.calls, 32767) if used else 0,
                ai_failures=min(used.failures, 32767) if used else 0,
            )
        )
    return Outcome(
        stored=stored,
        judged=judged,
        usage=usage,
        errors=errors,
        checks=checks,
        revised=[revised.revision for revised in revisions],
        discovery_failed=bool(discovered) and not discovered[0].ok,
        out_of_time=http.out_of_time(),
    )


def _shown_or_waiting() -> Any:
    """An item worth telling about: judged to matter, or not judged yet."""
    from django.db.models import Q

    return Q(strength__in=SHOWN) | Q(status=AgendaItemStatus.PENDING, strength=Strength.UNJUDGED)


def _week_verdict(
    monday: dt.date,
    today: dt.date,
    looks: list[valitsus.Lookup],
    agenda: GovernmentAgenda | None,
) -> dict[str, Any]:
    """One week's ledger row from its days' lookups (the worst state wins)."""
    if agenda is not None:
        return {
            "week": monday,
            "state": AgendaCheckState.STORED,
            "session_date": agenda.session_date,
            "url": agenda.url,
            "agenda": agenda,
        }
    worst = min(
        looks,
        key=lambda one: _WEEK_ORDER.index(one.state) if one.state in _WEEK_ORDER else 99,
        default=None,
    )
    if worst is None:
        return {
            "week": monday,
            "state": AgendaCheckState.NOT_FOUND,
            "missing": _last_attempt(monday, today),
        }
    return {
        "week": monday,
        "state": worst.state,
        "session_date": worst.day if worst.state != valitsus.NOT_FOUND else None,
        "http_status": worst.status,
        "url": worst.url[:500],
        "detail": worst.detail[:300],
        # Friday's cycle is the week's last reading (owner, 25.11.2026): after it, a
        # week with nothing stored is missing however it ended.
        "missing": _last_attempt(monday, today),
    }


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
them. Use only what is given.

{KODA_STYLE}"""

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
#: Requests per agenda at most (audit HR-02). A readable answer ends it at once;
#: only a failed request, a cut-off answer or one that could not be read is
#: retried, on the next runs, until this many were made.
MAX_ATTEMPTS = 3


def ai_enabled() -> bool:
    return bool(getattr(settings, "REGISTER_SWEEP_AI", False))


def recent_letters(today: dt.date, usage: candidates_ai.Usage | None = None) -> list[str]:
    """Titles of the Chamber's own letters of the last 120 days: a signal of its current work.

    Only letters reliably tied to a Teema that may be sent (`outbound.letter_titles`):
    never one on a restricted or a TEST Teema, and never one whose Teema is not yet
    decided (audit HR-01). How many were left out is counted on ``usage``."""
    letters = outbound.letter_titles(since=today - dt.timedelta(days=120), limit=LETTERS_SHOWN)
    if usage is not None:
        usage.letters_left_out += letters.left_out
    return letters.titles


def _prompt(
    agenda: GovernmentAgenda,
    items: Iterable[AgendaItem],
    topics: dict[str, Any],
    usage: candidates_ai.Usage | None = None,
) -> str:
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
        + json.dumps(recent_letters(agenda.session_date, usage), ensure_ascii=False)
        + f"\n\nAgenda: {agenda.title}\n"
        + json.dumps(shown, ensure_ascii=False, indent=1)
    )


def _failed_attempt(agenda: GovernmentAgenda, usage: candidates_ai.Usage, reason: str) -> int:
    """Count one request that gave no readable answer; it may be retried (`MAX_ATTEMPTS`)."""
    usage.failed(reason)
    GovernmentAgenda.objects.filter(pk=agenda.pk).update(ai_attempts=F("ai_attempts") + 1)
    logger.warning("government agenda %s: model attempt failed: %s", agenda.session_date, reason)
    return 0


def judge(agenda: GovernmentAgenda, usage: candidates_ai.Usage, client: Any = None) -> int:
    """One request for the agenda's unjudged waiting items; the count judged.

    **Only what nobody has settled is asked about or written** (audit HR-02,
    2026-11-23). An item a person added or marked read, and an item the model
    already judged, is neither sent again nor overwritten — not its strength,
    its reason, nor its Teema.

    **One readable answer is the attempt.** It sets `judged_at` even when it left
    items out or answered some with an unusable value: those stay «Hindamata»,
    shown as such, rather than being asked about every half hour or given a
    verdict nobody made. A failed request, a cut-off answer and an unreadable one
    are counted in `ai_attempts` and retried by later runs, at most
    `MAX_ATTEMPTS` times in all.
    """
    if not ai_enabled():
        return 0
    items = list(
        agenda.items.filter(
            status=AgendaItemStatus.PENDING,
            strength=Strength.UNJUDGED,
            source_removed_at__isnull=True,
        )
    )
    if not items:
        # Nothing left to ask: every item is judged or decided by a person.
        if agenda.judged_at is None:
            agenda.judged_at = timezone.now()
            agenda.save(update_fields=["judged_at", "updated_at"])
        return 0
    topics = candidates_ai.open_topics(usage)
    answers = ai_call.call_items(
        setting="REGISTER_SWEEP_AI_MODEL",
        system=SYSTEM,
        prompt=lambda: _prompt(agenda, items, topics, usage),
        schema=SCHEMA,
        max_tokens=MAX_TOKENS,
        timeout=ai_call.SWEEP_TIMEOUT,
        key=lambda row: int(row["number"]),
        usage=usage,
        client=client,
    )
    if isinstance(answers, ai_call.Failure):
        if answers.kind == "request" and answers.reason == ai_call.OUT_OF_TIME:
            # No request was made: the nightly step's time was spent (ADR 0231). Not
            # a model failure and not an attempt; `run` records the deadline.
            return 0
        # The agenda is stored without the model; a later run may try again.
        return _failed_attempt(agenda, usage, answers.reason)
    allowed = {Strength.STRONG, Strength.POSSIBLE, Strength.WEAK, Strength.NONE}
    if not any(answers.get(item.number, {}).get("strength") in allowed for item in items):
        # A readable answer with no usable verdict for any item asked about (empty,
        # other numbers, unusable strengths) is no answer: a bounded retry, never a
        # closed agenda with everything «Hindamata».
        return _failed_attempt(agenda, usage, "vastuses polnud ühtki kasutatavat hinnangut")
    judged = 0
    with transaction.atomic():
        # Re-read under lock: a person may have decided an item while the model
        # was answering, and that decision is never overwritten.
        still_open = AgendaItem.objects.select_for_update(of=("self",)).filter(
            pk__in=[item.pk for item in items],
            status=AgendaItemStatus.PENDING,
            strength=Strength.UNJUDGED,
            source_removed_at__isnull=True,
        )
        for item in still_open:
            row = answers.get(item.number)
            if not row or row.get("strength") not in allowed:
                continue  # left «Hindamata»: no verdict is invented for it
            item.strength = row["strength"]
            item.reason = ai_call.clean_line(row.get("reason"))
            item.related_matter = topics.get(str(row.get("related") or ""))
            item.save(update_fields=["strength", "reason", "related_matter", "updated_at"])
            judged += 1
        agenda.judged_at = timezone.now()
        agenda.ai_attempts = F("ai_attempts") + 1
        agenda.save(update_fields=["judged_at", "ai_attempts", "updated_at"])
        agenda.refresh_from_db(fields=["ai_attempts"])
    if judged < len(items):
        logger.info(
            "government agenda %s: %d of %d item(s) left unjudged",
            agenda.session_date,
            len(items) - judged,
            len(items),
        )
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
    """Every waiting proposal: tied to a Teema, judged to matter, not yet decided.

    Never on a TEST Teema (audit HR-09): the model is no longer offered one, and a
    proposal stored before that is no proposal — not counted on the tab's badge,
    not rung about, not listed, not decidable. One filter here covers them all."""
    from app.matters.enums import MatterDataClass
    from app.register_watch.models import AgendaItemStatus

    return AgendaItem.objects.filter(
        related_matter__isnull=False,
        related_matter__data_class=MatterDataClass.REAL,
        status=AgendaItemStatus.PENDING,
        # An item the Government took off its agenda is kept, but proposes nothing.
        source_removed_at__isnull=True,
        strength__in=PROPOSED,
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
def add_to_teema(
    item: AgendaItem, *, actor: Any, comment: str = "", uploads: Any = ()
) -> AgendaItem:
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
        uploads=list(uploads),
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


def tell_owners(agendas: list[GovernmentAgenda], *, only: set[Any] | None = None) -> int:
    """The owner's bell («Registris uus dokument») for each Teema a new agenda's items tie to.

    ``only`` limits it to those items: a revised agenda tells about its new or changed
    items, never again about the ones already told (r95)."""
    from collections import Counter

    from app.matters.models import Matter
    from app.register_watch import notices

    waiting = proposals().filter(agenda__in=agendas)
    if only is not None:
        waiting = waiting.filter(pk__in=only)
    counts = Counter(waiting.values_list("related_matter_id", flat=True))
    told = 0
    # Never a TEST Teema's owner, even for a proposal stored before the model was
    # kept from naming one (audit HR-09).
    for matter in Matter.objects.filter(
        pk__in=counts, is_open=True, deleted_at__isnull=True
    ).real_data():
        if notices.notify(matter, counts[matter.pk]) is not None:
            told += 1
    return told
