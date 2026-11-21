"""The nightly sweep for drafts that are not a Teema yet, and what is done with them (ADR 0218).

`sweep` asks every readable register for what is new — the Riigikogu for the
bills initiated in the last days, each ministry register for the documents whose
title names a draft — leaves out what already belongs to a Teema, and gathers
the rest into `TopicCandidate`s, one per draft. The model then says how the
draft may touch entrepreneurs (`app.register_watch.candidates_ai`).

A person dismisses a candidate (`dismiss`), ties it to an existing Teema
(`attach`) or opens Uus teema from it (`prefill`, then `adopt` once the Teema is
saved). Neither the sweep nor the model creates a Teema or writes on one.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import QuerySet
from django.utils import timezone

from app.core.errors import DomainError
from app.matters.models import Matter
from app.register_watch import phrases
from app.register_watch.models import (
    CandidateItem,
    CandidateStatus,
    Strength,
    SweepRun,
    TopicCandidate,
    WatchFinding,
    WatchSource,
    WatchTarget,
)
from app.register_watch.sources import adr, pdr, riigikogu
from app.register_watch.sources.http import FetchError
from app.register_watch.sources.items import Item

logger = logging.getLogger(__name__)

#: What a register title is searched for: the words a draft's papers carry.
SWEEP_WORDS = ("eelnõu", "väljatöötamiskavatsus")
#: How far back each night reads. Registers enter a document a day or two late,
#: so the window overlaps; a document is stored once (`CandidateItem`'s constraint).
#: It is also all the first sweep brings in: older drafts are not candidates.
SWEEP_DAYS = 4
ALREADY_DECIDED = "See kandidaat on juba otsustatud."
ALREADY_RUNNING = "Uute teemade otsing juba käib."
RUN_LOCK = 218_0001
#: The strengths shown folded at the foot of the list.
FOLDED = (Strength.WEAK, Strength.NONE)
#: At most this many register links are written on a Teema from one candidate.
MAX_LINKS = 5

#: Words that say what a letter does with a draft, not which draft (lower case).
_LETTER = frozenset(
    """
    edastamine esitamine arvamus arvamuse arvamuste avaldamiseks avaldamine kooskõlastamisele
    kooskõlastus kooskõlastuse kiri kirja vastus vastuskiri seisukoht seisukoha seisukohad
    tagasiside palve taotlus märkused märkuste ettepanekud ettepanekute teadmiseks
    saatmine tutvumiseks arutelu kaasamine kaasamiseks täiendav täiendatud
    """.split()
)


def enabled() -> bool:
    return bool(getattr(settings, "REGISTER_SWEEP", False))


def match_key(title: str) -> str:
    """The title's distinctive words, lower case: what says *which* draft.

    «Äriseadustiku muutmise seaduse eelnõu kooskõlastamiseks esitamine» and
    «Arvamus äriseadustiku muutmise seaduse eelnõule» share one key, so the
    second document joins the first one's candidate.
    """
    text = re.sub(r"\([^)]*\)", " ", title or "")
    chosen: list[str] = []
    for word in phrases.WORD.findall(text):
        clean = word.strip("-").casefold()
        if (
            len(clean) < 3
            or clean.isdigit()
            or clean in phrases.GENERIC
            or clean in _LETTER
            or phrases.MINISTER.search(clean)
            or clean in chosen
        ):
            continue
        chosen.append(clean)
        if len(chosen) == 8:
            break
    return (" ".join(chosen) or " ".join((title or "").casefold().split()))[:300]


# -- reading -----------------------------------------------------------------------


def _sources(since: dt.date) -> Iterator[tuple[WatchSource, Any]]:
    """Each source and the call that reads it; the call raises `FetchError`."""
    yield WatchSource.RIIGIKOGU, lambda: riigikogu.recent_drafts(since)
    for source, pdr_register in (
        (WatchSource.HTM_REGISTER, pdr.HTM),
        (WatchSource.KLIM_REGISTER, pdr.KLIM),
    ):
        yield (
            source,
            lambda r=pdr_register: [item for word in SWEEP_WORDS for item in pdr.search(r, word)],
        )
    for source, adr_register in (
        (WatchSource.ADR_JDM, adr.JDM),
        (WatchSource.ADR_MKM, adr.MKM),
        (WatchSource.ADR_RAM, adr.RAM),
        (WatchSource.ADR_SOM, adr.SOM),
        (WatchSource.ADR_KUM, adr.KUM),
        (WatchSource.ADR_KAM, adr.KAM),
    ):
        yield (
            source,
            lambda r=adr_register: [
                item for word in SWEEP_WORDS for item in adr.search_all(r, word, since=since)
            ],
        )


def _watched_marks() -> set[tuple[int, str]]:
    """The bills («973 SE») the open Teemad already name in their titles."""
    marks = set()
    for title in Matter.objects.filter(is_open=True, deleted_at__isnull=True).values_list(
        "title", flat=True
    ):
        mark = riigikogu.bill_mark(title)
        if mark is not None:
            marks.add(mark)
    return marks


def _belongs_to_a_teema(source: WatchSource, item: Item, marks: set[tuple[int, str]]) -> bool:
    """Already the business of the per-Teema watch (ADR 0217): not a new topic."""
    if riigikogu.bill_mark(item.title) in marks:
        return True
    if source == WatchSource.RIIGIKOGU:
        uuid = item.external_id.removeprefix("draft:")
        if WatchTarget.objects.filter(source=source, identifier=uuid).exists():
            return True
    return WatchFinding.objects.filter(
        target__source=source, external_id=item.external_id[:200]
    ).exists()


def _reopen_if_it_moved(candidate: TopicCandidate, source: WatchSource) -> bool:
    """A dismissed draft comes back once: when it reaches the Riigikogu (ADR 0218 §5)."""
    if candidate.status != CandidateStatus.DISMISSED or source != WatchSource.RIIGIKOGU:
        return False
    if candidate.items.filter(source=WatchSource.RIIGIKOGU).exists():
        return False
    now = timezone.now()
    candidate.status = CandidateStatus.NEW
    candidate.strength = Strength.UNJUDGED
    candidate.listed_at = now
    candidate.reopened_at = now
    candidate.decided_by = None
    candidate.decided_at = None
    candidate.save(
        update_fields=[
            "status",
            "strength",
            "listed_at",
            "reopened_at",
            "decided_by",
            "decided_at",
            "updated_at",
        ]
    )
    return True


def store(
    source: WatchSource, items: list[Item], *, since: dt.date, marks: set[tuple[int, str]]
) -> tuple[int, int]:
    """Keep the documents not seen before; ``(new documents, candidates put on the list)``."""
    new = listed = 0
    for item in items:
        if item.occurred_on is None or item.occurred_on < since:
            continue
        external_id = item.external_id[:200]
        if CandidateItem.objects.filter(source=source, external_id=external_id).exists():
            continue
        if _belongs_to_a_teema(source, item, marks):
            continue
        key = match_key(item.title)
        candidate = TopicCandidate.objects.filter(match_key=key).order_by("-listed_at").first()
        if candidate is None:
            candidate = TopicCandidate.objects.create(
                title=item.title[:500] or "(pealkirjata)", match_key=key
            )
            listed += 1
        elif _reopen_if_it_moved(candidate, source):
            listed += 1
        CandidateItem.objects.create(
            candidate=candidate,
            source=source,
            external_id=external_id,
            title=item.title[:500] or "(pealkirjata)",
            occurred_on=item.occurred_on,
            url=item.url[:1000],
            detail=item.detail[:300],
        )
        new += 1
    return new, listed


# -- one run -----------------------------------------------------------------------


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


def _error_text(error: BaseException) -> str:
    if isinstance(error, FetchError):
        return str(error)[:300]
    return f"{type(error).__name__}: {error}"[:300]


def sweep(*, use_ai: bool = True, ai_client: Any = None, today: dt.date | None = None) -> SweepRun:
    """Read every register once for new drafts, judge the unjudged, tell the readers.

    A register that fails is written in the run's log and the others are still
    read. A failure of the run itself closes it as stopped before it is raised.
    """
    from app.register_watch import candidate_notices, candidates_ai

    today = today or timezone.localdate()
    since = today - dt.timedelta(days=SWEEP_DAYS)
    with _one_run_at_a_time():
        record = SweepRun.objects.create(started_at=timezone.now())
        usage = candidates_ai.Usage()
        try:
            marks = _watched_marks()
            for source, read in _sources(since):
                try:
                    items = read()
                    with transaction.atomic():
                        new, listed = store(source, items, since=since, marks=marks)
                except Exception as error:
                    if not isinstance(error, FetchError):
                        logger.exception("register sweep: %s", source)
                    record.errors.append({"allikas": source.label, "viga": _error_text(error)})
                    continue
                record.sources_read += 1
                record.items_new += new
                record.candidates_new += listed
            if use_ai:
                try:
                    candidates_ai.judge_unjudged(usage, client=ai_client)
                except Exception as error:
                    logger.exception("register sweep: judging")
                    record.errors.append({"allikas": "Claude", "viga": _error_text(error)})
            with transaction.atomic():
                candidate_notices.notify(
                    waiting()
                    .filter(listed_at__gte=record.started_at)
                    .exclude(strength__in=FOLDED)
                    .count()
                )
        except BaseException as error:
            record.errors.append({"viga": f"Otsing katkes: {_error_text(error)}", "katkes": True})
            _close(record, usage)
            raise
        _close(record, usage)
    return record


def _close(record: SweepRun, usage: Any) -> None:
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


# -- the reader's side -------------------------------------------------------------


def waiting() -> QuerySet[TopicCandidate]:
    """The list: candidates nobody has decided, newest first."""
    return TopicCandidate.objects.filter(status=CandidateStatus.NEW).order_by("-listed_at", "pk")


def _lock_new(candidate: TopicCandidate) -> TopicCandidate:
    locked = TopicCandidate.objects.select_for_update().get(pk=candidate.pk)
    if locked.status != CandidateStatus.NEW:
        raise DomainError(ALREADY_DECIDED)
    return locked


def _decide(candidate: TopicCandidate, status: CandidateStatus, actor: Any, matter: Any) -> None:
    candidate.status = status
    candidate.matter = matter
    candidate.decided_by = actor
    candidate.decided_at = timezone.now()
    candidate.save(update_fields=["status", "matter", "decided_by", "decided_at", "updated_at"])


@transaction.atomic
def dismiss(candidate: TopicCandidate, *, actor: Any) -> TopicCandidate:
    """«Jäta kõrvale». It returns only if the draft later reaches the Riigikogu."""
    locked = _lock_new(candidate)
    _decide(locked, CandidateStatus.DISMISSED, actor, None)
    return locked


def _link_items(candidate: TopicCandidate, matter: Any, actor: Any) -> int:
    """Write the candidate's register addresses on the Teema as `Menetluse link`s."""
    from app.matters.enums import ProceduralLinkKind
    from app.matters.workspace import add_matter_procedural_link

    written = 0
    seen: set[str] = set()
    for item in candidate.items.exclude(url="").order_by("-occurred_on", "pk"):
        if item.url in seen:
            continue
        seen.add(item.url)
        kind = (
            ProceduralLinkKind.RIIGIKOGU
            if item.source == WatchSource.RIIGIKOGU
            else ProceduralLinkKind.MINISTRY_REGISTER
        )
        label = (item.detail.split(" · ")[0] or item.title)[:120]
        add_matter_procedural_link(
            matter=matter, author=actor, kind=kind, url=item.url, label=label
        )
        written += 1
        if written == MAX_LINKS:
            break
    return written


@transaction.atomic
def attach(candidate: TopicCandidate, matter: Any, *, actor: Any) -> TopicCandidate:
    """«Seo olemasolevaga»: the documents become the existing Teema's `Menetluse link`s."""
    locked = _lock_new(candidate)
    _link_items(locked, matter, actor)
    _decide(locked, CandidateStatus.ATTACHED, actor, matter)
    return locked


@transaction.atomic
def adopt_if_new(candidate: TopicCandidate, matter: Any, *, actor: Any) -> bool:
    """After Uus teema was saved from the candidate: link its documents, close it.

    A candidate somebody decided meanwhile is left as it is (``False``): the
    Teema just saved stands either way. From the next night the per-Teema
    watch (ADR 0217) follows the new Teema by its title like any other.
    """
    locked = TopicCandidate.objects.select_for_update().get(pk=candidate.pk)
    if locked.status != CandidateStatus.NEW:
        return False
    _link_items(locked, matter, actor)
    _decide(locked, CandidateStatus.ADDED, actor, matter)
    return True


def for_create(user: Any, pk: Any) -> TopicCandidate | None:
    """The candidate Uus teema was opened from, if this person may still add it."""
    from app.register_watch import candidate_notices

    if not pk or not candidate_notices.may_decide(user):
        return None
    try:
        return waiting().filter(pk=pk).first()
    except (ValueError, TypeError, ValidationError):
        return None


def _teema_title(candidate: TopicCandidate) -> str:
    """The Riigikogu's name for the bill when it has one: it carries «NNN SE»."""
    bill = candidate.items.filter(source=WatchSource.RIIGIKOGU).order_by("-occurred_on").first()
    return (bill.title if bill is not None else candidate.title)[:1000]


def create_initial(candidate: TopicCandidate) -> dict[str, Any]:
    """Uus teema's starting values: title, sender ministry, the day it appeared."""
    from app.organisations.models import Organisation

    initial: dict[str, Any] = {"title": _teema_title(candidate)}
    name = ministry_name(candidate)
    if name:
        ministry = Organisation.objects.filter(name=name).first()
        if ministry is not None:
            initial["source_organisations"] = [ministry.pk]
    first = candidate.items.exclude(occurred_on=None).order_by("occurred_on").first()
    if first is not None:
        initial["received_date"] = first.occurred_on
    return initial


#: The register -> its ministry's `Organisation` name (the reverse of `phrases`).
SOURCE_MINISTRY = {
    WatchSource.ADR_JDM: "Justiits- ja Digiministeerium",
    WatchSource.ADR_MKM: "Majandus- ja Kommunikatsiooniministeerium",
    WatchSource.ADR_RAM: "Rahandusministeerium",
    WatchSource.ADR_SOM: "Sotsiaalministeerium",
    WatchSource.ADR_KUM: "Kultuuriministeerium",
    WatchSource.ADR_KAM: "Kaitseministeerium",
    WatchSource.HTM_REGISTER: "Haridus- ja Teadusministeerium",
    WatchSource.KLIM_REGISTER: "Kliimaministeerium",
}


def ministry_name(candidate: TopicCandidate) -> str:
    """The ministry whose register first showed the draft, or ``""`` (a Riigikogu bill)."""
    first = candidate.items.exclude(source=WatchSource.RIIGIKOGU).order_by("occurred_on", "pk")
    item = first.first()
    return SOURCE_MINISTRY.get(WatchSource(item.source), "") if item is not None else ""


def at_riigikogu(candidate: TopicCandidate) -> bool:
    return candidate.items.filter(source=WatchSource.RIIGIKOGU).exists()


# -- is it working ------------------------------------------------------------------

#: The host's cron line: every day at 04:20 UTC (deploy/unraid-main/cron).
SWEEP_AT_UTC = dt.time(4, 20)
STALE_AFTER = dt.timedelta(hours=30)
FIRST_RUN_GRACE = dt.timedelta(hours=6)
#: A run unfinished after this long was stopped from outside.
ABANDONED_AFTER = dt.timedelta(hours=3)


def _installed_at() -> dt.datetime | None:
    from django.db.migrations.recorder import MigrationRecorder

    row = (
        MigrationRecorder.Migration.objects.filter(
            app="register_watch", name="0004_topic_candidates"
        )
        .only("applied")
        .first()
    )
    return row.applied if row is not None else None


def _first_run_due(since: dt.datetime) -> dt.datetime:
    moment = since.astimezone(dt.UTC)
    slot = dt.datetime.combine(moment.date(), SWEEP_AT_UTC, tzinfo=dt.UTC)
    if slot < moment:
        slot += dt.timedelta(days=1)
    return slot + FIRST_RUN_GRACE


def sweep_health(now: dt.datetime | None = None) -> Any:
    """Did the sweep run, finish and read anything? For `production_status` (ADR 0218 §1).

    Off (the sweep or fetching switched off) is green ``off``. Red when the newest
    run stopped, when no run finished in 30 hours, or when the last run read no
    register. With no run yet it is green until the first scheduled run after
    the release was due, then red ``never run`` (as ADR 0217 §9).
    """
    from app.register_watch.watch import Health

    if not enabled() or not getattr(settings, "REGISTER_WATCH_FETCH", False):
        return Health(True, "off")
    now = now or timezone.now()
    latest = SweepRun.objects.order_by("-started_at").first()
    if latest is not None:
        stopped = any(isinstance(e, dict) and e.get("katkes") for e in latest.errors) or (
            latest.finished_at is None and latest.started_at < now - ABANDONED_AFTER
        )
        if stopped:
            return Health(False, "last run stopped before its end", "Viimane otsing katkes.")
    finished = SweepRun.objects.filter(finished_at__isnull=False).order_by("-finished_at").first()
    if finished is None:
        installed = _installed_at()
        if installed is not None and now < _first_run_due(installed):
            return Health(True, "first run in progress" if latest else "not run yet")
        return Health(False, "never run", "Uute teemade otsing pole veel käinud.")
    if finished.finished_at is None or finished.finished_at < now - STALE_AFTER:
        return Health(False, "no finished run in 30 h", "Üle 30 tunni pole otsing lõpuni jõudnud.")
    if finished.sources_read == 0 and finished.errors:
        return Health(
            False,
            f"last run read no source ({len(finished.errors)} errors)",
            "Viimane otsing ei saanud lugeda ühtki registrit.",
        )
    parts = []
    if finished.errors:
        parts.append(f"{len(finished.errors)} errors")
    if finished.ai_failures:
        parts.append(f"{finished.ai_failures} AI failures")
    return Health(True, ", ".join(parts))
