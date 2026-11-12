"""A closure from Lovable held for Juristid-only work (TL-02, owner decision 12.11.2026).

docs/adr/0213 §10.13. When Lovable closes a Teema that still holds work only
Juristid knows of (`closure_work.juristid_only_work`: planned Tegevused, «Koja
arvamuse järelkontroll», planned «Kodulehe ülevaade», open Kaasamine waits), the
nightly synchronisation does not cancel that work: it holds the closure with a
`LOCAL_WORK_AT_CLOSURE` technical hold (`lovable_sync._lifecycle_decisions`),
and the current step and the opinion request wait with it. Nothing is written;
the hold counts toward no breaker.

**Where a hold lives.** No table of its own: it is the newest reading's
decision — the ``stage`` decision with that reason in the state's
``observed_run`` — exactly where every other technical hold lives
(`source_ownership.ownership` reads the same row). Its ``current`` carries the
work's counts and digest, the hold's first night (``held_since``, carried night
to night) and the day it escalates (``escalates_on``, 14 days on). A night
without the hold — Lovable reopened, the work is done — ends it; a re-run on
the same night repeats it with the same first night.

**How a hold ends.**

* the lawyer finishes, cancels or moves the work — the next night closes the
  Teema with Lovable's reason, as a source change;
* the lawyer closes the Teema themselves (allowed while the hold stands: the
  lock frees a field under an open technical hold, docs/open-decisions.md E11);
* Lovable reopens it — the hold simply clears;
* **release** (`release_closure_hold`): the Teema's owner, or a technical
  administrator, is shown what would be cancelled and confirms it. The latest
  reading is read again (still closed?) and so is the work (the digest). The
  closure is written now, with Lovable's reason and the run's provenance, and
  the releasing person is the actor of `MATTER_CLOSED` and of every
  cancellation it causes — never a fabricated one.

**Escalation.** A hold still open on its 14th day reaches its owner (an
«Vajab tegevust» row) and, the morning after, every holder of
`department.view_management` — the department head — through the notifications
package (`CLOSURE_HOLD`, docs/adr/0198), keyed on the hold's escalation day, so
a re-run never delivers twice.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import DateField, OuterRef, QuerySet, Subquery
from django.db.models.fields.json import KT
from django.db.models.functions import Cast

from app.core.errors import DomainError
from app.matters.closure_work import (
    LOCAL_WORK_AT_CLOSURE,
    LiveWork,
    joined_phrases,
    juristid_only_work,
    live_work_at_closure,
    work_from_counts,
)

#: Said when the hold is gone by the time somebody releases it.
HOLD_CLEARED = "Lovable'i sulgemine pole enam ootel; midagi ei muudetud."
#: Said when the latest reading of Lovable no longer closes the Teema.
SOURCE_REOPENED = (
    "Lovable'i viimase lugemise järgi pole teema enam suletud; "
    "ootelolek lõpeb ise ja midagi ei muudetud."
)
#: Who may release.
RELEASE_NOT_ALLOWED = (
    "Ootel sulgemise saab vabastada ainult teema vastutaja või süsteemiadministraator."
)


@dataclass(frozen=True)
class ClosureHold:
    """One Teema's standing hold, as its newest reading recorded it."""

    matter_id: Any
    reference: str
    run_id: Any
    since: dt.date
    escalates_on: dt.date
    #: The work as the night counted it (counts only; the live work is re-read
    #: whenever something acts on it).
    counted: LiveWork

    def age_days(self, today: dt.date) -> int:
        return max(0, (today - self.since).days)

    def escalated(self, today: dt.date) -> bool:
        return today >= self.escalates_on

    @property
    def phrases(self) -> str:
        return joined_phrases(self.counted)


def _day(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _hold_decisions() -> Any:
    from app.legacy_import.lovable_sync_models import LovableSyncDecision, SyncOutcome

    return LovableSyncDecision.objects.filter(
        field="stage", outcome=SyncOutcome.REVIEW_REQUIRED, reason=LOCAL_WORK_AT_CLOSURE
    )


def _as_hold(row: dict[str, Any]) -> ClosureHold | None:
    raw = row.get("current")
    current: dict[str, Any] = raw if isinstance(raw, dict) else {}
    since = _day(current.get("held_since"))
    escalates = _day(current.get("escalates_on"))
    if since is None or escalates is None:
        return None
    return ClosureHold(
        matter_id=row["state__matter_id"],
        reference=row["state__reference"],
        run_id=row["run_id"],
        since=since,
        escalates_on=escalates,
        counted=work_from_counts(current.get("local_work")),
    )


def standing_holds(matter_ids: Any = None) -> list[ClosureHold]:
    """Every standing hold — on its state's newest reading, on an open Teema —
    or only those of ``matter_ids``. Two queries, however many."""
    from django.db.models import F

    rows = (
        _hold_decisions()
        .filter(
            run_id=F("state__observed_run_id"),
            state__matter__is_open=True,
            state__matter__deleted_at__isnull=True,
        )
        .values("state__matter_id", "state__reference", "run_id", "current")
        .order_by("state__reference")
    )
    if matter_ids is not None:
        rows = rows.filter(state__matter_id__in=list(matter_ids))
    return [hold for hold in (_as_hold(row) for row in rows) if hold is not None]


def hold_of(matter: Any) -> ClosureHold | None:
    """This Teema's standing hold, or ``None``."""
    found = standing_holds([getattr(matter, "pk", matter)])
    return found[0] if found else None


# ---------------------------------------------------------------------------
# The notifications' population (docs/adr/0198, category CLOSURE_HOLD)
# ---------------------------------------------------------------------------

#: The annotation the `CLOSURE_HOLD` category reads as its date: the hold's escalation day.
ESCALATES_FIELD = "closure_hold_escalates_on"


def with_escalation_day(matters: QuerySet[Any]) -> QuerySet[Any]:
    """``matters`` with a standing hold, annotated with its escalation day."""
    newest = _hold_decisions().filter(
        state__matter_id=OuterRef("pk"),
        run_id=OuterRef("lovable_sync_state__observed_run_id"),
    )
    day = Subquery(
        newest.annotate(day=Cast(KT("current__escalates_on"), DateField())).values("day")[:1],
        output_field=DateField(),
    )
    return (
        matters.filter(is_open=True)
        .annotate(**{ESCALATES_FIELD: day})
        .filter(**{f"{ESCALATES_FIELD}__isnull": False})
    )


def last_escalation_days(matter_ids: list[Any]) -> dict[Any, dt.date]:
    """Each Teema's newest hold's escalation day, standing or not — so a row
    whose hold has ended reads as done (DISCHARGED), never as moved."""
    out: dict[Any, dt.date] = {}
    rows = (
        _hold_decisions()
        .filter(state__matter_id__in=matter_ids)
        .order_by("state__matter_id", "-run__started_at", "-created_at")
        .values("state__matter_id", "current")
    )
    for row in rows:
        if row["state__matter_id"] in out:
            continue
        current = row["current"] if isinstance(row["current"], dict) else {}
        day = _day(current.get("escalates_on"))
        if day is not None:
            out[row["state__matter_id"]] = day
    return out


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------


def _person(actor: Any) -> Any:
    """The account behind ``actor`` (a Haldus `VerifiedActor` wraps one)."""
    return getattr(actor, "user", actor)


def may_release(actor: Any, matter: Any) -> bool:
    """The Teema's owner (an active business writer, from the Teema page) or a
    verified technical administrator (from Haldus, `admin_session.administrator_actor`).

    A person, always: the release is attributed to them, so there is no call
    without an actor (owner decision 12.11.2026, point 4)."""
    from app.accounts.admin_session import is_verified_actor
    from app.accounts.enums import UserRole
    from app.core.authorization import acting_role, may_write_business_content

    person = _person(actor)
    if getattr(person, "pk", None) is None:
        return False
    if is_verified_actor(actor):
        return acting_role(person) == UserRole.ADMINISTRATOR.value
    return may_write_business_content(person) and getattr(matter, "owner_id", None) == person.pk


@dataclass(frozen=True)
class ReleasePreview:
    """What a release would do now: Lovable's reason, and the work it would
    end (counts only), with the digest a confirmation must post back."""

    hold: ClosureHold
    reason_label: str
    #: Everything the closure ends: Lovable's current step and the Juristid-only work.
    ending: LiveWork
    #: The Juristid-only part, whose digest binds the confirmation.
    local: LiveWork

    @property
    def digest(self) -> str:
        return self.local.digest

    @property
    def statement(self) -> str:
        """One sentence: what is closed, and what is cancelled in the releaser's name."""
        ended = joined_phrases(self.ending)
        text = f"Teema suletakse Lovable'i põhjusega «{self.reason_label}»."
        if ended:
            text += f" Sinu nimel tühistatakse: {ended}."
        return text


def _desired_now(matter: Any) -> tuple[Any, dict[str, Any] | None]:
    """The state, and what the latest reading of Lovable says the lifecycle is."""
    from app.legacy_import import lovable_sync
    from app.legacy_import.lovable_sync_models import LovableSyncState

    state = (
        LovableSyncState.objects.select_related("observed_run")
        .filter(matter_id=getattr(matter, "pk", matter))
        .first()
    )
    if state is None or not state.observed:
        return state, None
    desired, _hold = lovable_sync.desired_lifecycle(
        state.observed.get("stage"),
        list(state.observed.get(lovable_sync.CONTINUATION) or []),
        matter_id=state.matter_id,
    )
    if desired is None or desired["open"] or not desired.get("disposition"):
        return state, None
    return state, desired


def release_preview(matter: Any) -> ReleasePreview | None:
    """What releasing this Teema's hold would do now, or ``None`` when there is
    no standing hold or the latest reading no longer closes it."""
    from app.workflow.enums import Disposition

    hold = hold_of(matter)
    if hold is None:
        return None
    _state, desired = _desired_now(matter)
    if desired is None:
        return None
    return ReleasePreview(
        hold=hold,
        reason_label=str(Disposition(desired["disposition"]).label),
        ending=live_work_at_closure(matter),
        local=juristid_only_work(matter),
    )


@dataclass(frozen=True)
class ReleaseResult:
    closed: bool
    message: str


def release_closure_hold(*, matter: Any, actor: Any, confirmed: str) -> ReleaseResult:
    """Release a standing hold: close the Teema now as the latest reading of
    Lovable says, ending the work ``confirmed`` names, in ``actor``'s name.

    * Who: `may_release`, refused otherwise.
    * The hold is gone, the Teema is already closed, or Lovable's latest reading
      no longer closes it → nothing is written, and the result says so («the
      hold simply clears»).
    * ``confirmed`` must be the digest of the Juristid-only work live now
      (`ReleasePreview.digest`); work that changed since the person was shown it
      is refused with the renewed counts (`closure_work.SourceClosureHeld`).
    * The closure: `close_matter_from_source` with Lovable's reason and stage,
      the hold's run provenance (``closure_hold: RELEASED``) and the actor —
      one transaction, no half-applied closure: the current step is cancelled
      with it and the request stays, exactly as a source closure leaves it.
    """
    from app.legacy_import import lovable_sync
    from app.matters.locks import lock_matter_for_write
    from app.matters.models import Matter
    from app.matters.services import close_matter_from_source
    from app.workflow.models import StageVocabulary

    if not may_release(actor, matter):
        raise DomainError(RELEASE_NOT_ALLOWED)
    with transaction.atomic():
        locked = lock_matter_for_write(matter.pk)
        if not locked.is_open:
            return ReleaseResult(False, "Teema on juba suletud; midagi ei muudetud.")
        if hold_of(locked) is None:
            return ReleaseResult(False, HOLD_CLEARED)
        state, desired = _desired_now(locked)
        if state is None or desired is None:
            return ReleaseResult(False, SOURCE_REOPENED)
        run = state.observed_run
        provenance = {
            **lovable_sync._provenance(
                run, state, state.observed_version, getattr(run, "snapshot_sha256", "")
            ),
            "closure_hold": "RELEASED",
        }
        stage_key = locked.stage.key if locked.stage is not None else None
        stage = (
            StageVocabulary.objects.get(key=desired["stage"])
            if desired.get("stage") and desired["stage"] != stage_key
            else None
        )
        successor = (
            Matter.objects.get(pk=desired["successor"]) if desired.get("successor") else None
        )
        close_matter_from_source(
            matter=locked,
            disposition=desired["disposition"],
            stage=stage,
            successor=successor,
            reason=lovable_sync._lifecycle_reason(desired, state.observed),
            provenance=provenance,
            released_by=_person(actor),
            confirmed=str(confirmed or "").strip() or False,
        )
    if hasattr(matter, "is_open"):
        matter.is_open = False
    return ReleaseResult(
        True, "Teema suleti Lovable'i põhjusega; pooleli töö tühistati sinu nimel."
    )
