"""What closing a Teema would end, said before it is ended (the owner's decision of 2026-10-10).

A `Hetkeseis` that ends a Matter — «Jõustunud», «Rohkem ei tegele» — closes it, and
closure ends everything the Matter owes only while it is current work
(`end_live_work_for_closure`): the current step, every planned step and planned
`Arvamuse järelkontroll`, every planned website overview and every open
`Kaasamine` feedback wait. Each is kept in the history as cancelled or closed;
nothing is deleted.

Until now only a pending follow-up check made the person confirm that
(docs/adr/0146 §8). The owner decided that no outstanding work is ended unseen: a
closure that would end **any** of it stops, says what — in counts, never in the
records' own words, because the closing person may not read every restricted
one — and closes only once the person confirms. A file whose work genuinely goes
on elsewhere — an Estonian implementing act after an EU regulation enters into
force — is the person's to carry onto a linked Teema first (docs/adr/0152).

**Reader-blind**, as `pending_checks` is: closure ends every one of them, whoever
may see them, so the question is asked of the database rather than of a reader's
scope. Asked twice, as before: early by every save that would store files ahead
of a closure, and again by `close_matter` under the Matter's lock.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Kept word for word for the one case ADR 0146 §8 always asked about.
CHECKS_ONLY_LABEL = "Sulge teema ja lõpeta ka järelkontroll"
LIVE_WORK_LABEL = "Sulge teema ja lõpeta pooleli töö"


@dataclass(frozen=True)
class LiveWork:
    current: bool = False
    planned: int = 0
    checks: int = 0
    overviews: int = 0
    waits: int = 0

    @property
    def is_empty(self) -> bool:
        return not (self.current or self.planned or self.checks or self.overviews or self.waits)

    @property
    def checks_only(self) -> bool:
        return bool(self.checks) and not (
            self.current or self.planned or self.overviews or self.waits
        )

    def phrases(self) -> list[str]:
        out: list[str] = []
        if self.current:
            out.append("praegune tegevus")
        if self.planned:
            out.append(_count(self.planned, "planeeritud tegevus", "planeeritud tegevust"))
        if self.checks:
            out.append("Koja arvamuse järelkontroll")
        if self.overviews:
            out.append(
                _count(
                    self.overviews,
                    "planeeritud kodulehe ülevaade",
                    "planeeritud kodulehe ülevaadet",
                )
            )
        if self.waits:
            out.append(
                _count(self.waits, "kaasamise tagasiside ootus", "kaasamise tagasiside ootust")
            )
        return out


def _count(n: int, one: str, many: str) -> str:
    return f"1 {one}" if n == 1 else f"{n} {many}"


def _joined(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " ja " + parts[-1]


def live_work_at_closure(
    matter: Any, *, finishing: Any = None, will_check: bool = False
) -> LiveWork:
    """Everything closure would end on this Matter, counted.

    ``finishing`` is a step the same save completes before it closes — «✓ Tehtud»,
    or a `Koja arvamus` that was the step — so it is not «outstanding».
    ``will_check`` is a send that schedules a check in the same save.
    """
    from app.matters.enums import WebsiteOverviewStatus
    from app.matters.models import MatterEngagement, MatterWebsiteOverview
    from app.workflow.enums import ActionStatus
    from app.workflow.models import NextAction

    matter_id = getattr(matter, "pk", matter)
    actions = NextAction.objects.filter(
        matter_id=matter_id, status__in=(ActionStatus.OPEN, ActionStatus.PLANNED)
    )
    if finishing is not None:
        actions = actions.exclude(pk=getattr(finishing, "pk", finishing))
    rows = list(actions.values_list("status", "follow_up_id"))
    checks = sum(1 for _status, follow_up in rows if follow_up is not None)
    return LiveWork(
        current=any(
            status == ActionStatus.OPEN and follow_up is None for status, follow_up in rows
        ),
        planned=sum(
            1 for status, follow_up in rows if status == ActionStatus.PLANNED and follow_up is None
        ),
        checks=checks + (1 if will_check and not checks else 0),
        overviews=MatterWebsiteOverview.objects.filter(
            matter_id=matter_id, status=WebsiteOverviewStatus.PLANNED
        ).count(),
        waits=MatterEngagement.objects.filter(
            matter_id=matter_id, lifecycle_tracked=True, feedback_closed_at__isnull=True
        ).count(),
    )


def closure_warning(work: LiveWork) -> tuple[str, str] | None:
    """The sentence and the confirming box's words, or ``None`` when nothing would end."""
    from app.workflow.follow_ups import FOLLOW_UP_CLOSURE_WARNING

    if work.is_empty:
        return None
    if work.checks_only:
        return FOLLOW_UP_CLOSURE_WARNING, CHECKS_ONLY_LABEL
    return (
        f"Teema sulgemisel lõpetatakse ka pooleli töö: {_joined(work.phrases())}. "
        "Kui midagi neist tuleb jätkata, vii see enne sulgemist teisele, seotud teemale.",
        LIVE_WORK_LABEL,
    )
