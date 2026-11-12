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

**A removed record is not live work** (QA-WORKFLOW-01). A `Kaasamine` or a
planned `Kodulehe ülevaade` taken off the file with «Kustuta» keeps its row —
open wait, `PLANNED` plan — but it is on no surface any more, so closure neither
counts it nor ends it (docs/adr/0152 §6).

**The confirmation names the work it was given for** (QA-WORKFLOW-02,
docs/adr/0152 §7). The warning carries a digest of exactly what it counted — the
ids of the live steps, plans and waits — and the confirming box posts that digest
back as its value. Under the Matter's lock the digest is computed again: work a
colleague added, finished or removed after the warning was drawn makes it
differ, and the closure is refused once more with the renewed warning instead of
ending work nobody was shown. The digest is a hash of row ids, so it says nothing
about a restricted record to the person who reads it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import DomainError

#: Kept word for word for the one case ADR 0146 §8 always asked about.
CHECKS_ONLY_LABEL = "Sulge teema ja lõpeta ka järelkontroll"
LIVE_WORK_LABEL = "Sulge teema ja lõpeta pooleli töö"

#: What a confirmation is told when the work changed after its warning was drawn.
#: Put before the renewed warning, which names what is live now.
LIVE_WORK_CHANGED = "Pooleli töö on pärast hoiatust muutunud — vaata see üle ja kinnita uuesti."

#: A confirmation, as a closing operation receives it. ``True`` only from code
#: that closes on nobody's interactive behalf, or after the same transaction has
#: already verified the person's digest under the Matter's lock; a person's
#: confirmation arrives as the digest string the warning drew; ``False`` or ``""``
#: is none.
ClosureConfirmation = bool | str

_DIGEST = re.compile(r"^[0-9a-f]{16}$")


@dataclass(frozen=True)
class LiveWork:
    current: bool = False
    planned: int = 0
    checks: int = 0
    overviews: int = 0
    waits: int = 0
    #: The rows counted above, as sorted id strings, for `digest` and for the
    #: closure's own record of what it ended. Not compared by `==` on counts.
    action_ids: tuple[str, ...] = field(default=(), compare=False)
    overview_ids: tuple[str, ...] = field(default=(), compare=False)
    wait_ids: tuple[str, ...] = field(default=(), compare=False)
    #: A send in the same save schedules a check that is not a row yet.
    check_to_come: bool = field(default=False, compare=False)
    matter_id: str = field(default="", compare=False)

    @property
    def digest(self) -> str:
        """16 hex characters naming exactly this set of live rows on this Matter."""
        tokens = [
            f"matter:{self.matter_id}",
            *(f"action:{pk}" for pk in self.action_ids),
            *(f"overview:{pk}" for pk in self.overview_ids),
            *(f"wait:{pk}" for pk in self.wait_ids),
        ]
        if self.check_to_come:
            tokens.append("check:to-come")
        return hashlib.sha256("\n".join(tokens).encode()).hexdigest()[:16]

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
    rows = list(actions.values_list("pk", "status", "follow_up_id"))
    checks = sum(1 for _pk, _status, follow_up in rows if follow_up is not None)
    overview_ids = _ids(
        MatterWebsiteOverview.objects.live().filter(
            matter_id=matter_id, status=WebsiteOverviewStatus.PLANNED
        )
    )
    wait_ids = _ids(MatterEngagement.objects.live().open_waits().filter(matter_id=matter_id))
    return LiveWork(
        current=any(
            status == ActionStatus.OPEN and follow_up is None for _pk, status, follow_up in rows
        ),
        planned=sum(
            1
            for _pk, status, follow_up in rows
            if status == ActionStatus.PLANNED and follow_up is None
        ),
        checks=checks + (1 if will_check and not checks else 0),
        overviews=len(overview_ids),
        waits=len(wait_ids),
        action_ids=tuple(sorted(str(pk) for pk, _status, _follow_up in rows)),
        overview_ids=overview_ids,
        wait_ids=wait_ids,
        check_to_come=will_check,
        matter_id=str(matter_id),
    )


def _ids(queryset: Any) -> tuple[str, ...]:
    return tuple(sorted(str(pk) for pk in queryset.values_list("pk", flat=True)))


def confirmation_matches(work: LiveWork, confirmed: ClosureConfirmation) -> bool:
    """Whether ``confirmed`` covers exactly ``work``.

    ``True`` covers anything: it is never a person's answer (`ClosureConfirmation`).
    A string covers only the work whose digest it is.
    """
    if confirmed is True:
        return True
    return bool(confirmed) and str(confirmed).strip() == work.digest


def is_stale_confirmation(confirmed: ClosureConfirmation) -> bool:
    """A digest a warning drew, for work that has since changed — not a missing answer."""
    return isinstance(confirmed, str) and bool(_DIGEST.match(confirmed.strip()))


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


# ---------------------------------------------------------------------------
# A closure from Lovable and the work only Juristid holds (TL-02, owner 12.11.2026)
# ---------------------------------------------------------------------------

#: The hold's code in the synchronisation ledger (`lovable_sync.TECHNICAL_HOLDS`).
LOCAL_WORK_AT_CLOSURE = "LOCAL_WORK_AT_CLOSURE"
#: How many days a hold may stand before its owner and the department head are told.
CLOSURE_HOLD_ESCALATION_DAYS = 14


def juristid_only_work(matter: Any) -> LiveWork:
    """The live work on a synchronised Teema that is **Juristid's alone** (TL-02).

    `live_work_at_closure` minus the current step: on a synchronised Teema the
    current step (``OPEN``, not a follow-up check) is Lovable's own `jargmiseks`
    (docs/adr/0213 §10.3), and a closure from Lovable ends it as Lovable says.
    Everything else closure would end is something a person in Juristid planned
    and Lovable never knew of:

    * every **planned Tegevus** (``PLANNED``, no follow-up — docs/adr/0143);
    * every **«Koja arvamuse järelkontroll»**, current or planned (a step with a
      follow-up — docs/adr/0146; only an interactive send schedules one);
    * every live **planned «Kodulehe ülevaade»** (docs/adr/0081 §5);
    * every live **open Kaasamine feedback wait** (docs/adr/0086 §7).

    A removed («Kustuta») overview or Kaasamine is not live work (QA-WORKFLOW-01),
    exactly as for any closure. The digest names these rows only, so a release
    confirmed against them is refused when they change (docs/adr/0152 §7)."""
    from app.workflow.enums import ActionStatus
    from app.workflow.models import NextAction

    work = live_work_at_closure(matter)
    if not work.current:
        return work
    lovables = {
        str(pk)
        for pk in NextAction.objects.filter(
            matter_id=getattr(matter, "pk", matter),
            status=ActionStatus.OPEN,
            follow_up__isnull=True,
        ).values_list("pk", flat=True)
    }
    return LiveWork(
        current=False,
        planned=work.planned,
        checks=work.checks,
        overviews=work.overviews,
        waits=work.waits,
        action_ids=tuple(pk for pk in work.action_ids if pk not in lovables),
        overview_ids=work.overview_ids,
        wait_ids=work.wait_ids,
        matter_id=work.matter_id,
    )


def work_counts(work: LiveWork) -> dict[str, Any]:
    """Counts and the digest only — never a record's words (reader-blind)."""
    return {
        "planned": work.planned,
        "checks": work.checks,
        "overviews": work.overviews,
        "waits": work.waits,
        "digest": work.digest,
    }


def work_from_counts(counts: dict[str, Any] | None) -> LiveWork:
    """A `LiveWork` drawn from `work_counts` (for its `phrases`), ids left out."""
    counts = counts or {}
    return LiveWork(
        planned=int(counts.get("planned") or 0),
        checks=int(counts.get("checks") or 0),
        overviews=int(counts.get("overviews") or 0),
        waits=int(counts.get("waits") or 0),
    )


def joined_phrases(work: LiveWork) -> str:
    parts = work.phrases()
    return _joined(parts) if parts else ""


#: The Teema banner while a closure from Lovable is held (one sentence + the counts).
CLOSURE_HOLD_BANNER = "Lovable on teema sulgenud, kuid Juristides on pooleli"
#: The banner once the work is done and only the night's closure is left.
CLOSURE_HOLD_DONE = (
    "Lovable on teema sulgenud ja Juristides pole enam pooleli tööd; "
    "järgmine öine sünkroonimine suleb teema."
)
#: Said under the banner: how the hold ends.
CLOSURE_HOLD_HELP = (
    "Lõpeta, tühista või vii pooleli töö teisele teemale — siis suleb öine "
    "sünkroonimine teema Lovable'i põhjusega. Teema saab ka ise sulgeda."
)
#: The one-sentence `i` beside the release action.
CLOSURE_HOLD_RELEASE_TIP = (
    "Sulgeb teema kohe Lovable'i põhjusega ja tühistab allpool nimetatud pooleli töö sinu nimel."
)
CLOSURE_HOLD_RELEASE_LABEL = "Sulge teema ja tühista pooleli töö"


class SourceClosureHeld(DomainError):
    """A closure from Lovable refused while Juristid-only work is live (TL-02).

    ``work`` is what holds it; ``stale`` — a release was confirmed for other
    work than is live now."""

    def __init__(self, work: LiveWork, *, stale: bool = False) -> None:
        self.work = work
        self.stale = stale
        text = f"{CLOSURE_HOLD_BANNER}: {joined_phrases(work)}."
        if stale:
            text = f"{LIVE_WORK_CHANGED} {text}"
        super().__init__(text)
