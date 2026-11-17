"""One run of the register watch, and what a lawyer does with what it found (ADR 0216).

A run takes each open Teema, makes sure the sources its title names are watched
(`ensure_targets`), reads each source (`check_target`), stores what is new, and
lets the model judge what a title search found (`app.register_watch.ai`).

A lawyer then adds a finding to the Teema (`add_to_teema`), which records a
`Menetluse areng` through the same use case as `+ Märge`, or skips it
(`dismiss`). Neither the run nor the model writes anything on a Teema.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from app.core.errors import DomainError
from app.matters.models import Matter
from app.register_watch import ai, notices
from app.register_watch.models import (
    FindingStatus,
    Relevance,
    TargetOrigin,
    WatchFinding,
    WatchRun,
    WatchSource,
    WatchTarget,
)
from app.register_watch.sources import pdr, riigikogu
from app.register_watch.sources.http import FetchError
from app.register_watch.sources.items import Item

ALREADY_DECIDED = "See dokument on juba otsustatud."
#: Sources whose items are found by a title search and so may not belong.
SEARCHED = {WatchSource.HTM_REGISTER, WatchSource.KLIM_REGISTER}
REGISTERS = {WatchSource.HTM_REGISTER: pdr.HTM, WatchSource.KLIM_REGISTER: pdr.KLIM}


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
        for source in SEARCHED:
            WatchTarget.objects.get_or_create(
                matter=matter,
                source=source,
                identifier=label,
                defaults={"label": label, "origin": TargetOrigin.TITLE},
            )
    return list(matter.watch_targets.filter(active=True).order_by("source"))


def read(target: WatchTarget) -> list[Item]:
    if target.source == WatchSource.RIIGIKOGU:
        return riigikogu.bill_items(target.identifier)
    return pdr.search(REGISTERS[WatchSource(target.source)], target.identifier)


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


def run(
    matters: Iterable[Matter] | None = None,
    *,
    use_ai: bool = True,
    ai_client: Any = None,
    today: dt.date | None = None,
) -> WatchRun:
    """Check every watched Teema once and record the run."""
    today = today or timezone.localdate()
    record = WatchRun.objects.create(started_at=timezone.now())
    usage = ai.Usage()
    for matter in (
        matters
        if matters is not None
        else watched_matters().order_by("reference_year", "reference_number")
    ):
        try:
            targets = ensure_targets(matter)
        except FetchError as error:
            record.errors.append({"teema": matter.display_reference, "viga": str(error)[:300]})
            continue
        for target in targets:
            try:
                new, offered = store(target, read(target), today=today)
            except FetchError as error:
                target.last_error = str(error)[:500]
                target.save(update_fields=["last_error", "updated_at"])
                record.errors.append(
                    {
                        "teema": matter.display_reference,
                        "allikas": str(target),
                        "viga": str(error)[:300],
                    }
                )
                continue
            target.checked_at = timezone.now()
            target.last_error = ""
            target.save(update_fields=["checked_at", "last_error", "updated_at"])
            record.targets_checked += 1
            record.findings_new += new
            record.findings_offered += offered
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
        # called unrelated (ADR 0216 §6).
        fresh = (
            WatchFinding.objects.filter(
                matter=matter, status=FindingStatus.PENDING, created_at__gte=record.started_at
            )
            .exclude(relevance=Relevance.UNRELATED)
            .count()
        )
        notices.notify(matter, fresh)
    record.ai_calls = usage.calls
    record.ai_input_tokens = usage.input_tokens
    record.ai_output_tokens = usage.output_tokens
    record.finished_at = timezone.now()
    record.save()
    return record


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


def _provenance(finding: WatchFinding) -> str:
    parts = [finding.target.get_source_display()]
    if finding.detail:
        parts.append(finding.detail)
    line = " · ".join(parts)
    return f"{line}\n{finding.url}" if finding.url else line


@transaction.atomic
def add_to_teema(finding: WatchFinding, *, actor: Any) -> WatchFinding:
    """Record the finding on its Teema as a `Menetluse areng`, through `+ Märge`'s use case."""
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
        note=_provenance(locked),
    )
    locked.status = FindingStatus.ADDED
    locked.decided_by = actor
    locked.decided_at = timezone.now()
    locked.development = result.record
    locked.save(update_fields=["status", "decided_by", "decided_at", "development", "updated_at"])
    return locked


def _link_label(finding: WatchFinding) -> str:
    """Which proceeding: the bill («973 SE») or the register number."""
    if finding.target.source == WatchSource.RIIGIKOGU:
        return finding.target.label[:120]
    return (finding.detail.split(" · ")[0] or finding.title)[:120]


@transaction.atomic
def add_link(finding: WatchFinding, *, actor: Any) -> WatchFinding:
    """«Lisa link»: keep the address as the Teema's `Menetluse link`, nothing more.

    Through `+ Menetluse link`'s own use case, so a link the Teema already holds
    is returned unchanged. No `Menetluse areng` is written.
    """
    from app.matters.enums import ProceduralLinkKind
    from app.matters.workspace import add_matter_procedural_link

    locked = (
        WatchFinding.objects.select_for_update()
        .select_related("target", "matter")
        .get(pk=finding.pk)
    )
    if locked.status != FindingStatus.PENDING:
        raise DomainError(ALREADY_DECIDED)
    if not locked.url:
        raise DomainError("Sellel dokumendil pole linki.")
    kind = (
        ProceduralLinkKind.RIIGIKOGU
        if locked.target.source == WatchSource.RIIGIKOGU
        else ProceduralLinkKind.MINISTRY_REGISTER
    )
    add_matter_procedural_link(
        matter=locked.matter, author=actor, kind=kind, url=locked.url, label=_link_label(locked)
    )
    locked.status = FindingStatus.LINKED
    locked.decided_by = actor
    locked.decided_at = timezone.now()
    locked.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
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
