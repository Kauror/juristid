"""One run of the register watch, and what a lawyer does with what it found (ADR 0217).

A run takes each open Teema, makes sure the sources its title names are watched
(`ensure_targets`), reads each source (`check_target`), stores what is new, and
lets the model judge what a title search found (`app.register_watch.ai`).

A lawyer then adds a finding to the Teema (`add_to_teema`), which records a
`Menetluse areng` through the same use case as `+ Märge`, or skips it
(`dismiss`). Neither the run nor the model writes anything on a Teema.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import connection, transaction
from django.db.models import QuerySet
from django.utils import timezone

from app.core.errors import DomainError
from app.matters.models import Matter
from app.register_watch import ai, notices, phrases
from app.register_watch.models import (
    FindingStatus,
    Relevance,
    TargetOrigin,
    WatchFinding,
    WatchRun,
    WatchSource,
    WatchTarget,
)
from app.register_watch.sources import adr, http, pdr, riigikogu
from app.register_watch.sources.http import FetchError
from app.register_watch.sources.items import Item

logger = logging.getLogger(__name__)

ALREADY_DECIDED = "See dokument on juba otsustatud."
#: Sources whose items are found by a title search and so may not belong.
REGISTERS = {WatchSource.HTM_REGISTER: pdr.HTM, WatchSource.KLIM_REGISTER: pdr.KLIM}
ADR_REGISTERS = {
    WatchSource.ADR_JDM: adr.JDM,
    WatchSource.ADR_MKM: adr.MKM,
    WatchSource.ADR_RAM: adr.RAM,
    WatchSource.ADR_SOM: adr.SOM,
    WatchSource.ADR_KUM: adr.KUM,
    WatchSource.ADR_KAM: adr.KAM,
}
SEARCHED = set(REGISTERS) | set(ADR_REGISTERS)
#: The search phrases a person can see and change: from the title, or their own.
PHRASE_ORIGINS = (TargetOrigin.PHRASE, TargetOrigin.PERSON)
#: How far back a register search reaches; the first check keeps the older part as seen.
SEARCH_DAYS = 120


def watched_matters() -> QuerySet[Matter]:
    return Matter.objects.filter(is_open=True, deleted_at__isnull=True)


def baseline_days() -> int:
    return int(getattr(settings, "REGISTER_WATCH_BASELINE_DAYS", 45))


# -- sources -----------------------------------------------------------------------


def ensure_targets(matter: Matter) -> list[WatchTarget]:
    """The Teema's sources; the ones its title names are created on first sight."""
    mark = riigikogu.bill_mark(matter.title)
    if mark is not None:
        number, kind = mark
        label = f"{number} {kind}"
        if not matter.watch_targets.filter(
            source=WatchSource.RIIGIKOGU, origin=TargetOrigin.TITLE
        ).exists():
            bill = riigikogu.find_bill(number, kind)
            if bill is not None:
                WatchTarget.objects.get_or_create(
                    matter=matter,
                    source=WatchSource.RIIGIKOGU,
                    identifier=bill["uuid"],
                    defaults={
                        "label": label,
                        "origin": TargetOrigin.TITLE,
                        "url": riigikogu.page_url(bill["uuid"]),
                    },
                )
        for source in sorted(SEARCHED):
            WatchTarget.objects.get_or_create(
                matter=matter,
                source=source,
                identifier=label,
                defaults={"label": label, "origin": TargetOrigin.TITLE},
            )
    else:
        home = phrases.ministry_source(matter)
        text = phrases.words(matter.title)
        # A phrase is created once: a lawyer's own words, or their switching the
        # search off, are never overwritten from the title again.
        if (
            home is not None
            and text
            and not matter.watch_targets.filter(source=home, origin__in=PHRASE_ORIGINS).exists()
        ):
            WatchTarget.objects.get_or_create(
                matter=matter,
                source=home,
                identifier=text,
                defaults={"label": text, "origin": TargetOrigin.PHRASE},
            )
    return list(matter.watch_targets.filter(active=True).order_by("source"))


def _has_every_word(item: Item, text: str) -> bool:
    title = item.title.casefold()
    return all(word.casefold() in title for word in text.split())


def read(target: WatchTarget, *, today: dt.date) -> list[Item]:
    source = WatchSource(target.source)
    if source == WatchSource.RIIGIKOGU:
        return riigikogu.bill_items(target.identifier)
    if source in ADR_REGISTERS:
        since = today - dt.timedelta(days=SEARCH_DAYS)
        return adr.search(ADR_REGISTERS[source], target.identifier, since=since)
    if target.origin == TargetOrigin.TITLE:
        return pdr.search(REGISTERS[source], target.identifier)
    # The PDR search matches the text as one piece: search a phrase's first word
    # and keep the titles that hold every word.
    first = target.identifier.split()[0]
    found = pdr.search(REGISTERS[source], first)
    return [item for item in found if _has_every_word(item, target.identifier)]


def store(target: WatchTarget, items: Iterable[Item], *, today: dt.date) -> tuple[int, int]:
    """Keep what is new; ``(new, offered)``. The first check keeps old items as seen."""
    first = target.checked_at is None
    cutoff = today - dt.timedelta(days=baseline_days())
    known = set(target.findings.values_list("external_id", flat=True))
    new = offered = 0
    for item in items:
        if item.external_id in known:
            continue
        known.add(item.external_id)
        old = first and (item.occurred_on is None or item.occurred_on < cutoff)
        WatchFinding.objects.create(
            target=target,
            matter_id=target.matter_id,
            external_id=item.external_id[:200],
            title=item.title[:500] or "(pealkirjata)",
            occurred_on=item.occurred_on,
            url=item.url[:1000],
            detail=item.detail[:300],
            status=FindingStatus.BASELINE if old else FindingStatus.PENDING,
        )
        new += 1
        offered += 0 if old else 1
    return new, offered


#: One register watch at a time: a session-level advisory lock, released when
#: the run ends or its process dies. The number is this app's own.
RUN_LOCK = 217_0001
ALREADY_RUNNING = "Registrite kontroll juba käib."


def _error_text(error: BaseException) -> str:
    """What a failure says in the run's log: a register's own words, or the class."""
    if isinstance(error, FetchError):
        return str(error)[:300]
    return f"{type(error).__name__}: {error}"[:300]


@contextmanager
def _one_run_at_a_time() -> Iterator[None]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [RUN_LOCK])
        (taken,) = cursor.fetchone()
    if not taken:
        raise DomainError(ALREADY_RUNNING)
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [RUN_LOCK])


def _watch_matter(
    record: WatchRun, matter: Matter, *, use_ai: bool, ai_client: Any, usage: Any, today: dt.date
) -> None:
    """One Teema. Whatever goes wrong here is written down and the run goes on.

    A register that answers something unexpected (ADR 0217 §5) is one Teema's or
    one source's problem: the rest of the night is still read, and the error is
    in the run's log for Haldus and `production_status`.
    """
    try:
        with transaction.atomic():
            targets = ensure_targets(matter)
    except Exception as error:
        if not isinstance(error, FetchError):
            logger.exception("register watch: sources of %s", matter.display_reference)
        record.errors.append({"teema": matter.display_reference, "viga": _error_text(error)})
        return
    for target in targets:
        if http.out_of_time():
            # The nightly step's time is spent (ADR 0234): the rest is read next
            # night; `run` writes the one line that says so.
            return
        try:
            items = read(target, today=today)
            with transaction.atomic():
                new, offered = store(target, items, today=today)
        except Exception as error:
            if not isinstance(error, FetchError):
                logger.exception("register watch: %s", target)
            target.last_error = _error_text(error)[:500]
            target.save(update_fields=["last_error", "updated_at"])
            record.errors.append(
                {
                    "teema": matter.display_reference,
                    "allikas": str(target),
                    "viga": _error_text(error),
                }
            )
            continue
        target.checked_at = timezone.now()
        target.last_error = ""
        target.save(update_fields=["checked_at", "last_error", "updated_at"])
        record.targets_checked += 1
        record.findings_new += new
        record.findings_offered += offered
    try:
        if use_ai:
            unjudged = list(
                WatchFinding.objects.filter(
                    matter=matter,
                    status=FindingStatus.PENDING,
                    relevance=Relevance.UNJUDGED,
                    target__source__in=SEARCHED,
                ).select_related("target")
            )
            ai.judge(matter, unjudged, usage, client=ai_client)
        # The owner's bell: what this run newly offers, less what the model
        # called unrelated (ADR 0217 §6).
        fresh = (
            WatchFinding.objects.filter(
                matter=matter, status=FindingStatus.PENDING, created_at__gte=record.started_at
            )
            .exclude(relevance=Relevance.UNRELATED)
            .count()
        )
        with transaction.atomic():
            notices.notify(matter, fresh)
    except Exception as error:
        logger.exception("register watch: judging or telling %s", matter.display_reference)
        record.errors.append({"teema": matter.display_reference, "viga": _error_text(error)})


def run(
    matters: Iterable[Matter] | None = None,
    *,
    use_ai: bool = True,
    ai_client: Any = None,
    today: dt.date | None = None,
) -> WatchRun:
    """Check every watched Teema once and record the run.

    One run at a time (`ALREADY_RUNNING`). A failure on one Teema or source is
    recorded and the run continues; a failure of the run itself closes it as
    stopped (``katkes``) before it is raised, so the log never shows a stopped
    run as clean.
    """
    today = today or timezone.localdate()
    with _one_run_at_a_time():
        record = WatchRun.objects.create(started_at=timezone.now())
        usage = ai.Usage()
        if use_ai and ai.enabled():
            usage.model = str(getattr(settings, "REGISTER_WATCH_AI_MODEL", ""))
        try:
            for matter in (
                matters
                if matters is not None
                else watched_matters()
                .select_related("classification_suggestion__sender")
                .prefetch_related("source_organisations")
                .order_by("reference_year", "reference_number")
            ):
                if http.out_of_time():
                    record.errors.append(_stopped_at_the_deadline())
                    break
                _watch_matter(
                    record, matter, use_ai=use_ai, ai_client=ai_client, usage=usage, today=today
                )
        except BaseException as error:
            record.errors.append({"viga": f"Kontroll katkes: {_error_text(error)}", "katkes": True})
            _close(record, usage)
            raise
        _close(record, usage)
    return record


def _stopped_at_the_deadline() -> dict[str, Any]:
    """The run's one line when its time ran out (ADR 0234): not a stop (``katkes``)
    — what was read is kept and recorded — but not a whole night either."""
    return {"viga": f"Kontroll peatus: {http.OUT_OF_TIME}", "ajapiir": True}


def _close(record: WatchRun, usage: Any) -> None:
    record.ai_calls = usage.calls
    record.ai_input_tokens = usage.input_tokens
    record.ai_output_tokens = usage.output_tokens
    record.ai_model = usage.model[:80]
    record.ai_judged = usage.judged
    record.ai_failures = usage.failures
    if usage.failures:
        record.errors.append(
            {
                "allikas": "Claude",
                "viga": f"{usage.failures} päringut ebaõnnestus: {usage.last_failure}",
            }
        )
    record.finished_at = timezone.now()
    record.save()


# -- is it working ------------------------------------------------------------------

#: The nightly run is due every 24 hours; a finished run older than this is missed.
STALE_AFTER = dt.timedelta(hours=30)


@dataclass(frozen=True)
class Health:
    ok: bool
    #: For `production_status`: counts, in English like its other rows.
    detail: str = ""
    #: For Haldus: what is wrong, in the page's words; empty when nothing is.
    problem: str = ""


#: The watch is a step of the nightly cycle (ADR 0229, `app.core.nightly_schedule`):
#: the first run is due in the first cycle at or after the moment the watch was
#: switched on, and missed once that cycle's finish-by for the watch has passed.


class WatchSwitchError(ValueError):
    """``JURISTID_REGISTER_WATCH_ENABLED_AT`` is set but unusable: not a moment,
    or a moment still to come (a typo there would hide a missing schedule)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def watch_switched_on_at(now: dt.datetime | None = None) -> dt.datetime | None:
    """When the watch was switched on here: the later of the release that applied
    `register_watch` 0001 and ``REGISTER_WATCH_ENABLED_AT`` (the operator's
    record of turning ``JURISTID_REGISTER_WATCH_FETCH`` on, when that happened
    after the release; the switch is on by default, so normally it is not set)."""
    from django.db.migrations.recorder import MigrationRecorder

    row = (
        MigrationRecorder.Migration.objects.filter(app="register_watch", name="0001_initial")
        .only("applied")
        .first()
    )
    moments = [row.applied] if row is not None else []
    declared = (getattr(settings, "REGISTER_WATCH_ENABLED_AT", "") or "").strip()
    if declared:
        try:
            moment = dt.datetime.fromisoformat(declared.replace("Z", "+00:00"))
        except ValueError as error:
            raise WatchSwitchError("is not a date and time") from error
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=dt.UTC)
        if moment > (now or timezone.now()):
            raise WatchSwitchError("is in the future")
        moments.append(moment)
    return max(moments) if moments else None


def first_run_due(switched_on: dt.datetime) -> dt.datetime:
    """The moment the first scheduled run counts as missed: the finish-by of the
    register watch in the first nightly cycle (00:00 Tallinn) at or after
    ``switched_on`` (`nightly_schedule.step_due`)."""
    from app.core import nightly_schedule

    return nightly_schedule.step_due("register-watch", switched_on)


def watch_health(now: dt.datetime | None = None) -> Health:
    """Did the register watch run, finish and read anything? For `production_status`.

    Counts only, never a Teema or a register's words. Fetching switched off
    (tests, CI, a recovery rehearsal) is green ``off``. With fetching on and no
    run yet, it is green ``not run yet`` until the first scheduled run was due
    (`first_run_due` from `watch_switched_on_at`), then red ``never run``: a
    schedule that was never installed must not stay green for ever, and a watch
    switched on yesterday evening is not late before tonight's run (coordinator
    QA 2026-11-20, QA-NEW-05).
    """
    if not getattr(settings, "REGISTER_WATCH_FETCH", False):
        return Health(True, "off")
    now = now or timezone.now()
    latest = WatchRun.objects.order_by("-started_at").first()
    if latest is not None and latest.aborted:
        return Health(False, "last run stopped before its end", "Viimane kontroll katkes.")
    finished = WatchRun.objects.filter(finished_at__isnull=False).order_by("-finished_at").first()
    if finished is None:
        # No run has finished yet: the first one is not late before it was due —
        # whether it has not started or is still running (review of #659).
        try:
            switched_on = watch_switched_on_at(now)
        except WatchSwitchError as error:
            return Health(
                False,
                f"JURISTID_REGISTER_WATCH_ENABLED_AT {error.reason}",
                "Registrite jälgimise sisselülitamise aeg on vigaselt seadistatud.",
            )
        if switched_on is not None and now < first_run_due(switched_on):
            return Health(True, "first run in progress" if latest else "not run yet")
        return Health(
            False,
            "never run" if latest is None else "no finished run in 30 h",
            "Registrite jälgimine on sisse lülitatud, kuid ükski kontroll pole veel käinud."
            if latest is None
            else "Üle 30 tunni pole ükski kontroll lõpuni jõudnud.",
        )
    if finished.finished_at is None or finished.finished_at < now - STALE_AFTER:
        return Health(
            False, "no finished run in 30 h", "Üle 30 tunni pole ükski kontroll lõpuni jõudnud."
        )
    if finished.targets_checked == 0 and finished.errors:
        return Health(
            False,
            f"last run read no source ({len(finished.errors)} errors)",
            "Viimane kontroll ei saanud lugeda ühtki registrit.",
        )
    parts = []
    if finished.errors:
        parts.append(f"{len(finished.errors)} errors")
    if finished.ai_failures:
        parts.append(f"{finished.ai_failures} AI failures")
    return Health(True, ", ".join(parts))


# -- the lawyer's side -------------------------------------------------------------


def pending_for(user: Any) -> QuerySet[WatchFinding]:
    """Findings waiting on Teemad the reader may see, newest first."""
    return (
        WatchFinding.objects.filter(
            status=FindingStatus.PENDING,
            matter__in=Matter.objects.visible_to(user).filter(
                is_open=True, deleted_at__isnull=True
            ),
        )
        .select_related("matter", "target", "matter__owner")
        .order_by("-occurred_on", "matter__reference_year", "matter__reference_number", "pk")
    )


def own_pending(user: Any, pk: Any) -> WatchFinding | None:
    return pending_for(user).filter(pk=pk).first()


def with_comment(comment: str, provenance: str) -> str:
    """The lawyer's comment, then where the document came from."""
    comment = (comment or "").strip()
    return f"{comment}\n\n{provenance}" if comment else provenance


def _one_line(value: Any) -> str:
    """A file name as one plain line: control characters and line breaks become spaces."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value or ""))
    return " ".join(text.split())[:200]


def _register_url(url: str) -> bool:
    """An address written into a note: https, on one of the register door's own hosts."""
    from urllib.parse import urlsplit

    from app.register_watch.sources.http import ALLOWED_HOSTS

    if any(ch.isspace() or not ch.isprintable() for ch in url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.netloc in ALLOWED_HOSTS


def _provenance(finding: WatchFinding, missed_links: Any = ()) -> str:
    parts = [finding.target.get_source_display()]
    if finding.detail:
        parts.append(finding.detail)
    line = " · ".join(parts)
    text = f"{line}\n{finding.url}" if finding.url else line
    links = []
    for one in missed_links:
        if not isinstance(one, dict):
            continue
        name = _one_line(one.get("name"))
        url = str(one.get("url") or "")
        if name and _register_url(url):
            links.append(f"{name}: {url}")
    if links:
        text += "\n\nRegistri failid, mida ei saanud lisada:\n" + "\n".join(links)
    return text


@transaction.atomic
def add_to_teema(
    finding: WatchFinding,
    *,
    actor: Any,
    comment: str = "",
    uploads: Any = (),
    missed_links: Any = (),
) -> WatchFinding:
    """Record the finding on its Teema as a `Menetluse areng`, through `+ Märge`'s use case.

    ``comment`` is the lawyer's own words, put first in the note, and
    ``uploads`` the register's own files (`register_files.fetch`), attached to
    it as `+ Märge` attaches a person's (owner, 2026-11-22). ``missed_links``
    (``[{name, url}]``) are the register files that did not come — refused,
    too slow or past the time budget (HR-04) — written under the source so the
    note still leads to them. Nothing is fetched here: the caller fetches
    before this transaction takes its row lock.
    """
    from app.matters.workspace import add_procedural_development

    locked = (
        WatchFinding.objects.select_for_update()
        .select_related("target", "matter")
        .get(pk=finding.pk)
    )
    if locked.status != FindingStatus.PENDING:
        raise DomainError(ALREADY_DECIDED)
    result = add_procedural_development(
        matter=locked.matter,
        author=actor,
        title=locked.title,
        occurred_on=locked.occurred_on,
        note=with_comment(comment, _provenance(locked, missed_links)),
        uploads=list(uploads),
    )
    locked.status = FindingStatus.ADDED
    locked.decided_by = actor
    locked.decided_at = timezone.now()
    locked.development = result.record
    locked.save(update_fields=["status", "decided_by", "decided_at", "development", "updated_at"])
    return locked


@transaction.atomic
def dismiss(finding: WatchFinding, *, actor: Any) -> WatchFinding:
    locked = WatchFinding.objects.select_for_update().get(pk=finding.pk)
    if locked.status != FindingStatus.PENDING:
        raise DomainError(ALREADY_DECIDED)
    locked.status = FindingStatus.DISMISSED
    locked.decided_by = actor
    locked.decided_at = timezone.now()
    locked.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
    return locked


# -- the search phrase -------------------------------------------------------------

PHRASE_MAX = 80


def phrase_targets(matter: Any) -> list[WatchTarget]:
    """The Teema's searches by words, the active one first in each register."""
    return list(
        WatchTarget.objects.filter(matter=matter, origin__in=PHRASE_ORIGINS).order_by(
            "source", "-active", "-updated_at"
        )
    )


@transaction.atomic
def set_phrase(matter: Any, *, source: str, text: str) -> WatchTarget | None:
    """A lawyer's words for one register, or none at all (the search off).

    The words replace whatever the Teema was searched by in that register; the
    title is never read for it again. Words used before are taken up again with
    what they had already found, so nothing is offered twice.
    """
    if source not in SEARCHED:
        raise DomainError("Tundmatu register.")
    clean = " ".join((text or "").split())[:PHRASE_MAX]
    now = timezone.now()
    WatchTarget.objects.filter(
        matter=matter, source=source, origin__in=PHRASE_ORIGINS, active=True
    ).update(active=False, updated_at=now)
    if not clean:
        if not WatchTarget.objects.filter(
            matter=matter, source=source, origin__in=PHRASE_ORIGINS
        ).exists():
            # Remember "off", so the title is not searched in this register again.
            WatchTarget.objects.create(
                matter=matter,
                source=source,
                identifier="",
                origin=TargetOrigin.PERSON,
                active=False,
            )
        return None
    target, created = WatchTarget.objects.get_or_create(
        matter=matter,
        source=source,
        identifier=clean,
        defaults={"label": clean, "origin": TargetOrigin.PERSON},
    )
    if not created:
        target.active = True
        target.origin = TargetOrigin.PERSON
        target.label = clean
        target.save(update_fields=["active", "origin", "label", "updated_at"])
    return target
