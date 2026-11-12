"""A closure from Lovable held for Juristid-only work (TL-02, owner decision 12.11.2026).

docs/adr/0213 §10.13. When Lovable closes a Teema that still holds work only
Juristid knows of (`closure_work.juristid_only_work`: planned Tegevused, «Koja
arvamuse järelkontroll», planned «Kodulehe ülevaade», open Kaasamine waits), the
nightly synchronisation does not cancel that work: it holds the closure with a
`LOCAL_WORK_AT_CLOSURE` technical hold (`lovable_sync._lifecycle_decisions`),
and the current step and the opinion request wait with it. Nothing is written;
the hold counts toward neither Teemad breaker (it has its own cap on how many
may begin in one night, `lovable_sync.MAX_NEW_CLOSURE_HOLDS`).

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
* **release** (`release_closure_hold`): the Teema's owner, or a verified
  technical administrator, is shown what would be cancelled and confirms it.
  The stored reading is read again (still closed?) and so is the work (the
  digest). The closure is written now, with Lovable's reason and the run's
  provenance, and the releasing person is the actor of `MATTER_CLOSED` and of
  every cancellation it causes — never a fabricated one.

**Escalation.** A hold still open on its 14th day reaches its owner (an
«Vajab tegevust» row) and, the morning after, every holder of
`department.view_management` — the department head — through the notifications
package (`CLOSURE_HOLD`, docs/adr/0198), keyed on the hold's escalation day, so
a re-run never delivers twice.

**Only while the synchronisation writes** (lead decision 12.11.2026, review
P2-1/P2-2). A hold leads to a write — a release — or to an escalation only in
APPLY and before the final cutover (`sync_writes`). In COMPARE the night still
records the hold, so it shows what APPLY would do (Haldus lists it as
proposed, the Teema banner is informational only); OFF neither releases nor
escalates; after the cutover, or with the Teema's cohort retired, there is no
standing hold at all.

**No live re-read** (review P2-4). The web process holds no Lovable
credentials — only the host-run synchronisation container does
(`app.haldus.data_sync_views`: the web process never reaches Lovable) — so a
release rereads the **stored** reading: the hold's own, and any newer stored
reading of the same topic (a run that read Lovable but did not get as far as
observing it). A newer reading that no longer closes the Teema the same way
refuses the release. The confirmation pages say when the reading was taken.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import DateField, Exists, F, OuterRef, QuerySet, Subquery
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
#: Said when a newer stored reading disagrees with the hold's own.
NEWER_READING_DISAGREES = (
    "Lovable'i uuem lugemine ei näita teemat enam samamoodi suletuna; "
    "midagi ei muudetud. Oota järgmist sünkroonimist."
)
#: Who may release.
RELEASE_NOT_ALLOWED = (
    "Ootel sulgemise saab vabastada ainult teema vastutaja või süsteemiadministraator."
)
#: Only while the synchronisation writes (APPLY, not cut over).
RELEASE_NOT_WRITING = (
    "Ootel sulgemist saab vabastada ainult siis, kui sünkroonimine kirjutab Lovable'i "
    "muudatused Juristidesse; praegu seda ei tehta."
)


def lock_control_for_share() -> None:
    """``FOR SHARE`` on the control row, inside the caller's transaction.

    A release takes it first, before it asks `sync_writes` and before any
    Matter lock: `set_mode` and `cut_over` take the same row ``FOR UPDATE`` (and
    nothing else), so the mode cannot change under a release, and a release
    cannot change it. The nightly run never locks the row (it reads it, and
    holds its own advisory lock), so the order Matter → state it shares with
    the release is unchanged and nothing waits in a cycle (review P3-D)."""
    from django.db import connection

    from app.legacy_import.lovable_sync_models import LovableSyncControl

    meta = LovableSyncControl._meta
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT 1 FROM {connection.ops.quote_name(meta.db_table)} "  # noqa: S608 - model names
            f"WHERE {connection.ops.quote_name(str(meta.pk.column))} = %s FOR SHARE",
            [1],
        )


def sync_writes() -> bool:
    """Whether the synchronisation writes: APPLY, and not finally cut over.

    Only then may a hold lead to a write (a release) or to an escalation
    (lead decision 12.11.2026, review P2-1)."""
    from app.legacy_import.lovable_sync_models import LovableSyncControl, SyncMode

    return LovableSyncControl.objects.filter(pk=1, mode=SyncMode.APPLY, cut_over=False).exists()


def _writing() -> Exists:
    from app.legacy_import.lovable_sync_models import LovableSyncControl, SyncMode

    return Exists(LovableSyncControl.objects.filter(pk=1, mode=SyncMode.APPLY, cut_over=False))


def _not_cut_over() -> Any:
    from app.legacy_import.lovable_sync_models import LovableSyncControl

    return ~Exists(LovableSyncControl.objects.filter(pk=1, cut_over=True))


def _retired_cohorts() -> Any:
    from app.legacy_import.lovable_sync_models import LovableSyncCohort

    return LovableSyncCohort.objects.filter(retired_at__isnull=False).values("key")


@dataclass(frozen=True)
class ClosureHold:
    """One Teema's standing hold, as its newest reading recorded it."""

    matter_id: Any
    reference: str
    run_id: Any
    since: dt.date
    #: 14 days after the first night the hold stood while the synchronisation
    #: writes; ``None`` while it has stood only in COMPARE (review P2-A).
    escalates_on: dt.date | None
    #: The work as the night counted it (counts only; the live work is re-read
    #: whenever something acts on it).
    counted: LiveWork
    #: When the reading the hold rests on was taken.
    read_at: dt.datetime | None = None

    def age_days(self, today: dt.date) -> int:
        return max(0, (today - self.since).days)

    def escalated(self, today: dt.date) -> bool:
        return self.escalates_on is not None and today >= self.escalates_on

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
    if since is None:
        return None
    return ClosureHold(
        matter_id=row["state__matter_id"],
        reference=row["state__reference"],
        run_id=row["run_id"],
        since=since,
        escalates_on=escalates,
        counted=work_from_counts(current.get("local_work")),
        read_at=row.get("run__snapshot_taken_at"),
    )


def standing_holds(matter_ids: Any = None) -> list[ClosureHold]:
    """Every standing hold — on its state's newest reading, on an open Teema of
    a cohort that is not retired, before the final cutover — or only those of
    ``matter_ids``. One query, however many."""
    rows = (
        _hold_decisions()
        .filter(
            _not_cut_over(),
            run_id=F("state__observed_run_id"),
            state__matter__is_open=True,
            state__matter__deleted_at__isnull=True,
        )
        .exclude(state__cohort__in=_retired_cohorts())
        .values(
            "state__matter_id", "state__reference", "run_id", "current", "run__snapshot_taken_at"
        )
        .order_by("state__reference")
    )
    if matter_ids is not None:
        rows = rows.filter(state__matter_id__in=list(matter_ids))
    return [hold for hold in (_as_hold(row) for row in rows) if hold is not None]


#: The stage holds whose reading has Lovable *closing* the Teema (TL-02,
#: rehearsal 2 D1 review): a closure held for Juristid-only work, «lõpetatud»
#: with no reason, and every successor hold — Lovable says the work goes on
#: elsewhere. While one stands the lock frees «Lõpeta teema» (E11, the stage
#: and continuation fields), and a person's closure is the one Lovable asked
#: for, so ending Lovable's current step with it is not refused. An unmapped
#: stage or a history reopening is not a closure and is not here.
CLOSING_STAGE_HOLDS = frozenset(
    {
        LOCAL_WORK_AT_CLOSURE,
        "CLOSURE_REASON_UNKNOWN",
        "SUCCESSOR_NOT_IN_JURISTID",
        "SUCCESSOR_AMBIGUOUS",
        "SUCCESSOR_DEFERRED_BY_BREAKER",
        "CONTINUATION_CYCLE",
    }
)


def closing_hold_stands(matter: Any) -> bool:
    """Whether the newest reading holds, on this open Teema, a closure Lovable
    states (`CLOSING_STAGE_HOLDS`), on its stage or continuation. One query."""
    from app.legacy_import.lovable_sync_models import LovableSyncDecision, SyncOutcome

    return (
        LovableSyncDecision.objects.filter(
            _not_cut_over(),
            state__matter_id=getattr(matter, "pk", matter),
            run_id=F("state__observed_run_id"),
            field__in=("stage", "continuation"),
            outcome=SyncOutcome.REVIEW_REQUIRED,
            reason__in=sorted(CLOSING_STAGE_HOLDS),
            state__matter__is_open=True,
        )
        .exclude(state__cohort__in=_retired_cohorts())
        .exists()
    )


def hold_of(matter: Any) -> ClosureHold | None:
    """This Teema's standing hold, or ``None``."""
    found = standing_holds([getattr(matter, "pk", matter)])
    return found[0] if found else None


# ---------------------------------------------------------------------------
# What the hold frees (docs/open-decisions.md E11), once the Teema is closed
# ---------------------------------------------------------------------------


def effective_ownership(matter: Any, own: Any) -> Any:
    """``own`` without what only a `LOCAL_WORK_AT_CLOSURE` hold freed, once the
    Teema is closed (review P3-2).

    While the hold stands the Teema is open, and E11 frees the closure (and
    the stage) so the lawyer can close it. After it closed — the lawyer's own
    closure or a release — the hold's decision is still the newest reading's
    until the next night, and E11 would also free «Ava uuesti»: a reopening
    the night would undo. Other technical holds keep freeing what they free."""
    if not own.active or not own.held or getattr(matter, "is_open", True):
        return own
    from app.legacy_import.lovable_sync import TECHNICAL_HOLDS
    from app.legacy_import.lovable_sync_models import (
        LovableSyncDecision,
        LovableSyncState,
        SyncOutcome,
    )
    from app.matters.source_ownership import HOLD_FIELDS, Ownership

    state = (
        LovableSyncState.objects.filter(matter_id=matter.pk).values("pk", "observed_run_id").first()
    )
    if state is None:
        return own
    others = set(
        LovableSyncDecision.objects.filter(
            state_id=state["pk"],
            run_id=state["observed_run_id"],
            outcome=SyncOutcome.REVIEW_REQUIRED,
            reason__in=[r for r in TECHNICAL_HOLDS if r != LOCAL_WORK_AT_CLOSURE],
        ).values_list("field", flat=True)
    )
    held = frozenset(key for key in own.held if set(HOLD_FIELDS[key]) & others)
    return Ownership(active=own.active, held=held)


def refuse_reopen_after_release(matter: Any, *, actor: Any) -> None:
    """A person's reopening of a synchronised Teema that a held closure's
    release (or the lawyer's own closure) has just closed stays refused, as
    every Lovable-closed Teema's reopening is (E4)."""
    from app.matters.source_ownership import SourceOwnedRefusal, ownership

    if getattr(actor, "pk", None) is None:
        return
    if effective_ownership(matter, ownership(matter)).locks("reopen"):
        raise SourceOwnedRefusal("reopen")


# ---------------------------------------------------------------------------
# The notifications' population (docs/adr/0198, category CLOSURE_HOLD)
# ---------------------------------------------------------------------------

#: The annotation the `CLOSURE_HOLD` category reads as its date: the hold's escalation day.
ESCALATES_FIELD = "closure_hold_escalates_on"


def with_escalation_day(matters: QuerySet[Any]) -> QuerySet[Any]:
    """``matters`` with a standing hold, annotated with its escalation day —
    only while the synchronisation writes (`sync_writes`): COMPARE, OFF, the
    cutover and a retired cohort escalate nothing (review P2-1/P2-2)."""
    newest = _hold_decisions().filter(
        state__matter_id=OuterRef("pk"),
        run_id=OuterRef("lovable_sync_state__observed_run_id"),
    )
    day = Subquery(
        newest.annotate(day=Cast(KT("current__escalates_on"), DateField())).values("day")[:1],
        output_field=DateField(),
    )
    return (
        matters.filter(_writing(), is_open=True)
        .exclude(lovable_sync_state__cohort__in=_retired_cohorts())
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


def refuse_release(actor: Any, matter: Any) -> None:
    """The two conditions every release needs, asked by the domain service too."""
    if not may_release(actor, matter):
        raise DomainError(RELEASE_NOT_ALLOWED)
    if not sync_writes():
        raise DomainError(RELEASE_NOT_WRITING)


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
    #: A newer stored reading no longer closes the Teema the same way: the
    #: release is not offered, because it would be refused (review P3-E).
    stale: bool = False

    @property
    def digest(self) -> str:
        return self.local.digest

    @property
    def read_at(self) -> dt.datetime | None:
        return self.hold.read_at

    @property
    def statement(self) -> str:
        """One sentence: what is closed, and what is cancelled in the releaser's name."""
        ended = joined_phrases(self.ending)
        text = f"Teema suletakse Lovable'i põhjusega «{self.reason_label}»."
        if ended:
            text += f" Sinu nimel tühistatakse: {ended}."
        return text


def _desired_now(matter: Any, *, lock: bool = False) -> tuple[Any, dict[str, Any] | None]:
    """The state, and what the hold's reading of Lovable says the lifecycle is."""
    from app.legacy_import import lovable_sync
    from app.legacy_import.lovable_sync_models import LovableSyncState

    states = LovableSyncState.objects.filter(matter_id=getattr(matter, "pk", matter))
    if lock:
        states = states.select_for_update(of=("self",))
    state = states.select_related("observed_run").first()
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


def _newer_reading_disagrees(state: Any, desired: dict[str, Any]) -> bool:
    """Whether a stored reading newer than the hold's own no longer closes the
    Teema the same way (review P2-4: no live re-read is possible here)."""
    from app.legacy_import import lovable_sync
    from app.legacy_import.lovable_sync_models import LovableSyncSnapshot

    run = state.observed_run
    newer = LovableSyncSnapshot.objects.exclude(run_id=state.observed_run_id)
    if run is not None and run.snapshot_taken_at is not None:
        newer = newer.filter(taken_at__gt=run.snapshot_taken_at)
    newest = newer.order_by("-taken_at").first()
    if newest is None:
        return False
    bundle = lovable_sync.bundle_of(newest.document, state.source_id)
    if bundle is None:
        return True
    source = lovable_sync.canonical_source(bundle)
    again, _hold = lovable_sync.desired_lifecycle(
        source.get("stage"),
        lovable_sync.source_continuation(bundle),
        matter_id=state.matter_id,
    )
    return (
        again is None
        or bool(again.get("open"))
        or again.get("disposition") != desired.get("disposition")
        or again.get("successor") != desired.get("successor")
    )


def release_preview(matter: Any) -> ReleasePreview | None:
    """What releasing this Teema's hold would do now, or ``None`` when there is
    no standing hold or the hold's reading no longer closes it."""
    from app.workflow.enums import Disposition

    hold = hold_of(matter)
    if hold is None:
        return None
    state, desired = _desired_now(matter)
    if state is None or desired is None:
        return None
    ending = live_work_at_closure(matter)
    return ReleasePreview(
        hold=hold,
        reason_label=str(Disposition(desired["disposition"]).label),
        ending=ending,
        local=juristid_only_work(matter, work=ending),
        stale=_newer_reading_disagrees(state, desired),
    )


@dataclass(frozen=True)
class ReleaseResult:
    closed: bool
    message: str


def release_closure_hold(*, matter: Any, actor: Any, confirmed: str) -> ReleaseResult:
    """Release a standing hold: close the Teema now as the stored reading of
    Lovable says, ending the work ``confirmed`` names, in ``actor``'s name.

    * Who and when: `refuse_release` — the owner or a verified technical
      administrator, and only while the synchronisation writes (APPLY, not cut
      over); refused otherwise.
    * The hold is gone, the Teema is already closed, or the hold's reading no
      longer closes it → nothing is written, and the result says so («the hold
      simply clears»). A newer stored reading that disagrees refuses it.
    * ``confirmed`` must be the digest of the Juristid-only work live now
      (`ReleasePreview.digest`); work that changed since the person was shown it
      is refused with the renewed counts (`closure_work.SourceClosureHeld`).
    * The closure: `close_matter_from_source` with Lovable's reason and stage,
      the hold's run provenance (``closure_hold: RELEASED``) and the actor —
      one transaction, under the Matter's and the state's locks, no
      half-applied closure: the current step is cancelled with it and the
      request stays, exactly as a source closure leaves it.
    """
    from app.legacy_import import lovable_sync
    from app.matters.locks import lock_matter_for_write
    from app.matters.models import Matter
    from app.matters.services import close_matter_from_source
    from app.workflow.models import StageVocabulary

    if not may_release(actor, matter):
        raise DomainError(RELEASE_NOT_ALLOWED)
    with transaction.atomic():
        # The control row first (FOR SHARE), then the mode, then the Matter.
        lock_control_for_share()
        refuse_release(actor, matter)
        locked = lock_matter_for_write(matter.pk)
        if not locked.is_open:
            return ReleaseResult(False, "Teema on juba suletud; midagi ei muudetud.")
        if hold_of(locked) is None:
            return ReleaseResult(False, HOLD_CLEARED)
        state, desired = _desired_now(locked, lock=True)
        if state is None or desired is None:
            return ReleaseResult(False, SOURCE_REOPENED)
        if _newer_reading_disagrees(state, desired):
            raise DomainError(NEWER_READING_DISAGREES)
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
            released_by=actor,
            confirmed=str(confirmed or "").strip() or False,
        )
    if hasattr(matter, "is_open"):
        matter.is_open = False
    return ReleaseResult(
        True, "Teema suleti Lovable'i põhjusega; pooleli töö tühistati sinu nimel."
    )
