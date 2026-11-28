"""Minu statistika's «Sissetulev seire»: what the nightly monitoring brought to one's Teemad.

docs/adr/0223 §11. Incoming events, never the person's work, so they sit in a
group of their own and are not added to anything above them:

* **Registrite dokumendid** — `register_watch.WatchFinding`: a new document in a
  ministry's register (or the Riigikogu's) found for a Teema, by the day it was
  found. Documents already in the register before the first check (``BASELINE``)
  were never offered and are not counted;
* **Riigikogu kutsed** — `riigikogu.Invitation` on a bill a Teema follows, by the
  day Juristid recorded it;
* **Riigikogu arutelud** — `riigikogu.CommitteeAgendaItem` on such a bill, by the
  sitting's day (housekeeping items are left out);
* **Koja mainimised Riigikogus** — `riigikogu.StenogramMention` on such a bill, by
  the sitting's day;
* **Valitsuse päevakord** — `register_watch.AgendaItem` tied to a Teema, by the
  session's day.

**Whose Teema.** The Teema's owner on the item's day where the ownership trail
says it (`Ownership.owner_at`), otherwise its owner now. An item on two of one's
Teemad is one item.

**No reading is not zero.** A span in which the source was never read (no
`WatchRun`, no successful `StageRun` covering it, no stored Government agenda for
its week) is unknown: «—». With ``RIIGIKOGU_MONITORING`` off the three Riigikogu
figures are not read at all and say so (`bills.SWITCHED_OFF`).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from django.utils import timezone

from app.matters.my_statistics import UNKNOWN, Ownership, Record, Series, _id

INCOMING = ("registrid", "kutsed", "arutelud", "mainimised", "valitsus")


def _day(moment: datetime | date | None) -> date | None:
    if moment is None:
        return None
    if isinstance(moment, datetime):
        return timezone.localdate(moment) if timezone.is_aware(moment) else moment.date()
    return moment


def _days(start: date, end: date) -> set[date]:
    return {start + timedelta(days=n) for n in range((end - start).days + 1)}


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    first = timezone.make_aware(datetime(start.year, start.month, start.day))
    after = end + timedelta(days=1)
    return first, timezone.make_aware(datetime(after.year, after.month, after.day))


class _Mine:
    """Whether a Teema was this person's on a day: the trail's owner, else the owner now."""

    def __init__(self, ownership: Ownership, person_id: Any) -> None:
        self.ownership = ownership
        self.person = _id(person_id)

    def __call__(self, matter_id: Any, day: date) -> bool:
        if matter_id not in self.ownership.owner_now:
            return False
        owner = self.ownership.owner_at(matter_id, day)
        if owner == UNKNOWN:
            owner = self.ownership.owner_now.get(matter_id)
        return owner == self.person


class _EveryTeema:
    """Every Teema the reader may see, on every day: the department's reading (r97)."""

    def __call__(self, matter_id: Any, day: date) -> bool:
        return True


def _first_stage_reading(stages: list[str]) -> date | None:
    """The first day a Riigikogu stage was ever read: where its figure's coverage begins."""
    from django.db.models import Min

    from app.riigikogu.enums import RunStatus
    from app.riigikogu.models import StageRun

    first = StageRun.objects.filter(
        stage__in=stages, status__in=[RunStatus.SUCCEEDED, RunStatus.PARTIAL]
    ).aggregate(first=Min("started_at"), window=Min("window_start"))
    days = [day for day in (_day(first["first"]), first["window"]) if day is not None]
    return min(days) if days else None


def _stage_readings(stages: list[str], start: date, end: date) -> set[date]:
    """Days a Riigikogu stage read completely or in part: the windows it covered, and
    the days it ran. One query."""
    from app.riigikogu.enums import RunStatus
    from app.riigikogu.models import StageRun

    first, after = _bounds(start - timedelta(days=31), end + timedelta(days=31))
    out: set[date] = set()
    for began, window_start, window_end in StageRun.objects.filter(
        stage__in=stages,
        status__in=[RunStatus.SUCCEEDED, RunStatus.PARTIAL],
        started_at__gte=first,
        started_at__lt=after,
    ).values_list("started_at", "window_start", "window_end"):
        day = _day(began)
        if day is not None:
            out.add(day)
        if window_start and window_end and window_end >= window_start:
            out |= _days(max(window_start, start), min(window_end, end))
    return {day for day in out if start <= day <= end}


def incoming_series(
    person: Any,
    ownership: Ownership,
    start: date,
    end: date,
    *,
    riigikogu_on: bool,
    everyone: bool = False,
) -> dict[str, Series]:
    """The five incoming figures from ``start`` to ``end``, read as ``person``. ~8 queries.

    ``everyone`` reads the department's: every Teema ``person`` may see rather than
    only theirs, from the same records — distinct findings, items and calls, never a
    sum of personal figures (Osakond's overview, r97).
    """
    from app.matters.models import Matter
    from app.register_watch.models import (
        AgendaItem,
        AgendaItemStatus,
        FindingStatus,
        GovernmentAgenda,
        WatchFinding,
        WatchRun,
        WatchSource,
    )
    from app.riigikogu.bills import SWITCHED_OFF

    mine: Any = _EveryTeema() if everyone else _Mine(ownership, person.pk)
    visible = Matter.objects.visible_to(person).real_data().values("pk")
    first, after = _bounds(start, end)
    out: dict[str, Series] = {}

    # -- registers ---------------------------------------------------------------
    sources = dict(WatchSource.choices)
    statuses = dict(FindingStatus.choices)
    found = Series(readings=set())
    for created, matter_id, title, url, status, source in (
        WatchFinding.objects.filter(matter__in=visible, created_at__gte=first, created_at__lt=after)
        .exclude(status=FindingStatus.BASELINE)
        .order_by("created_at")
        .values_list("created_at", "matter_id", "title", "url", "status", "target__source")
    ):
        day = _day(created)
        if day is not None and mine(matter_id, day):
            detail = f"{sources.get(source, source)} · {statuses.get(status, status)}"
            found.records.append(Record(day, matter_id, title, detail, url))
    runs = WatchRun.objects.filter(started_at__gte=first, started_at__lt=after)
    found.since = _day(
        WatchRun.objects.order_by("started_at").values_list("started_at", flat=True).first()
    )
    found.readings = {
        day for run in runs if not run.aborted and (day := _day(run.started_at)) is not None
    }
    out["registrid"] = found

    # -- the Riigikogu -----------------------------------------------------------
    if not riigikogu_on:
        for key in ("kutsed", "arutelud", "mainimised"):
            out[key] = Series(readings=set(), off=SWITCHED_OFF.rstrip("."))
    else:
        out.update(_riigikogu(mine, visible, start, end))

    # -- the Government's agenda ---------------------------------------------------
    agenda = Series(readings=set())
    for session, number, title, matter_id, url, status in (
        AgendaItem.objects.filter(
            related_matter__in=visible,
            agenda__session_date__gte=start,
            agenda__session_date__lte=end,
        )
        .order_by("agenda__session_date", "number")
        .values_list(
            "agenda__session_date", "number", "title", "related_matter_id", "agenda__url", "status"
        )
    ):
        if mine(matter_id, session):
            detail = dict(AgendaItemStatus.choices).get(status, status)
            agenda.records.append(Record(session, matter_id, f"{number}. {title}", detail, url))
    readings: set[date] = set()
    for session in GovernmentAgenda.objects.filter(
        session_date__gte=start - timedelta(days=6), session_date__lte=end + timedelta(days=6)
    ).values_list("session_date", flat=True):
        monday = session - timedelta(days=session.weekday())
        readings |= _days(monday, monday + timedelta(days=6))
    agenda.readings = {day for day in readings if start <= day <= end}
    earliest = (
        GovernmentAgenda.objects.order_by("session_date")
        .values_list("session_date", flat=True)
        .first()
    )
    agenda.since = earliest - timedelta(days=earliest.weekday()) if earliest else None
    out["valitsus"] = agenda
    return out


def _riigikogu(mine: Any, visible: Any, start: date, end: date) -> dict[str, Series]:
    from app.register_watch.models import WatchSource, WatchTarget
    from app.riigikogu.enums import InvitationReview, ResponseCheck, Stage
    from app.riigikogu.models import CommitteeAgendaItem, Invitation, StenogramMention

    tied: dict[str, list[Any]] = defaultdict(list)
    for identifier, matter_id in WatchTarget.objects.filter(
        source=WatchSource.RIIGIKOGU, active=True, matter__in=visible
    ).values_list("identifier", "matter_id"):
        tied[identifier].append(matter_id)
    uuids = list(tied)

    def owner_of(uuid: Any, day: date) -> Any:
        return next((pk for pk in tied.get(str(uuid), []) if mine(pk, day)), None)

    first, after = _bounds(start, end)
    reviews = dict(InvitationReview.choices)
    responses = dict(ResponseCheck.choices)
    invitations = Series(
        readings=_stage_readings([Stage.INVITATIONS], start, end),
        since=_first_stage_reading([Stage.INVITATIONS]),
    )
    for created, uuid, mark, kind, committee, title, url, review, response in (
        Invitation.objects.filter(
            document__bill__uuid__in=uuids, created_at__gte=first, created_at__lt=after
        )
        .order_by("created_at")
        .values_list(
            "created_at",
            "document__bill__uuid",
            "document__bill__mark",
            "document__bill__type_code",
            "committee",
            "document__title",
            "document__url",
            "review",
            "response",
        )
    ):
        day = _day(created)
        teema = owner_of(uuid, day) if day else None
        if teema is None or day is None:
            continue
        detail = reviews.get(review, review)
        if response and response != ResponseCheck.NOT_DUE:
            detail = f"{detail} · {responses.get(response, response)}"
        what = " · ".join(part for part in (f"{mark or ''} {kind}".strip(), committee) if part)
        invitations.records.append(Record(day, teema, what or title, detail, url))

    sittings = Series(
        readings=_stage_readings([Stage.AGENDAS], start, end),
        since=_first_stage_reading([Stage.AGENDAS]),
    )
    for sitting, uuid, committee, title, url in (
        CommitteeAgendaItem.objects.filter(
            bill__uuid__in=uuids, routine=False, sitting_at__gte=first, sitting_at__lt=after
        )
        .order_by("sitting_at")
        .values_list("sitting_at", "bill__uuid", "committee", "title", "bill__url")
    ):
        held = _day(sitting)
        on = owner_of(uuid, held) if held else None
        if on is not None and held is not None:
            sittings.records.append(Record(held, on, committee, title, url))

    mentions = Series(
        readings=_stage_readings([Stage.STENOGRAMS, Stage.PROTOCOLS], start, end),
        since=_first_stage_reading([Stage.STENOGRAMS, Stage.PROTOCOLS]),
    )
    for spoken_on, uuid, source, speaker, title, url in (
        StenogramMention.objects.filter(
            bill__uuid__in=uuids, sitting_on__gte=start, sitting_on__lte=end
        )
        .order_by("sitting_on")
        .values_list("sitting_on", "bill__uuid", "source", "speaker", "agenda_title", "url")
    ):
        where_on = owner_of(uuid, spoken_on)
        if where_on is not None:
            where = "Komisjoni protokoll" if source == "PROTOCOL" else "Täiskogu stenogramm"
            what = f"{where} · {speaker}" if speaker else where
            mentions.records.append(Record(spoken_on, where_on, what, title, url))
    return {"kutsed": invitations, "arutelud": sittings, "mainimised": mentions}
