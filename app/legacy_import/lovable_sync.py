"""The nightly Lovable → Juristid synchronisation (docs/adr/0192).

One way, for the 100 Teemad the Lovable pilot created (docs/adr/0191) and for
nothing else. Every run reads a consistent snapshot of those rows
(`app.legacy_import.lovable_source`), compares three values per field and writes
only what is safe:

* **A** — what Lovable said when Juristid last accepted the field
  (`LovableSyncState.applied`, seeded from the pilot's immutable provenance);
* **B** — what Juristid holds now;
* **C** — what Lovable says tonight.

=============================  ===================================================
A = C, B = A                   UNCHANGED
A = C, B ≠ A                   LOCAL_TEST_CHANGE — the lawyer's edit is kept
A ≠ C, B = A, mapping exact    ELIGIBLE → APPLIED through the canonical service
A ≠ C, B = C                   ALREADY_CONVERGED — baseline advances, no write
A ≠ C, B ≠ A, B ≠ C            CONFLICT — nothing is overwritten
no baseline / no mapping       REVIEW_REQUIRED
=============================  ===================================================

**The allowlist** (`AUTO_FIELDS`) is the title, the owner, the day of an
unchanged current step, and the OneNote link (appended as a new source
observation, never by editing the old one). Everything else is held and
reported: new next-step wording (its DO/WAIT/MONITOR reading is a person's call),
`hetkeseis` (no approved stage mapping exists; 0191 keeps it NEVER_AUTO), the
opinion deadline, `valja`/`valja_kuupaev`, the addressee, commencements, the
received date, the act, and lifecycle. A held change stays pending — the
baseline does not move — until the source returns to it, Juristid converges on
it, or an operator resolves it (`resolve_field`).

Writes go through the domain services with ``actor=None`` and a ``LOVABLE_SYNC``
provenance; nothing here sets a model field directly.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.utils import timezone

from app.legacy_import.lovable_pilot import PILOT, SOURCE_SYSTEM, SOURCE_TABLE
from app.legacy_import.lovable_source import (
    SourceError,
    Transport,
    take_snapshot,
    verify_snapshot,
)
from app.legacy_import.lovable_sync_models import (
    LovableSyncControl,
    LovableSyncDecision,
    LovableSyncRun,
    LovableSyncSnapshot,
    LovableSyncState,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)

logger = logging.getLogger(__name__)

MAPPING_VERSION = "lovable-sync/1"
PROVENANCE_SOURCE = "LOVABLE_SYNC"
TALLINN = ZoneInfo("Europe/Tallinn")
LOCK_NAMESPACE, LOCK_KEY = 24607, 1
EXPECTED_COHORT = 100

#: Circuit breakers (docs/adr/0192 §7): a night that would touch more Teemad than
#: this, or raise more conflicts, writes nothing and waits for a person.
MAX_CHANGED_MATTERS = 20
#: Conflicts stay until a person resolves them, so the breaker is on the *rise*
#: since the previous night, never on the standing total.
MAX_NEW_CONFLICTS = 10
#: How old a backup set may be when the mutating stage starts (the host script
#: takes and verifies one minutes before; a stale or invented name writes nothing).
BACKUP_MAX_AGE = dt.timedelta(hours=2)
#: More cohort rows missing from the source than this is a broken read or a
#: broken source, never a night's work: the run stops.
MAX_MISSING = 5
SNAPSHOT_RETENTION_DAYS = 30
_BACKUP_SET = re.compile(r"^sets/\d{8}T\d{6}Z$")

AUTO_FIELDS = ("title", "owner", "next_action_date", "onenote_link")
HELD_FIELDS = (
    "next_action_text",
    "stage",
    "opinion_deadline",
    "sent",
    "sent_date",
    "addressee",
    "koda_initiative",
    "legal_act",
    "received",
    "commencements",
    "operational_scope",
)
FIELDS = AUTO_FIELDS + HELD_FIELDS

#: Why a source change was held. Codes, never business text.
HELD = "HELD_FIELD"
MATTER_CLOSED = "MATTER_CLOSED"
NO_BASELINE = "NO_BASELINE"
UNRESOLVED_OWNER = "UNRESOLVED_OWNER"
NOT_ASSIGNABLE = "OWNER_NOT_ASSIGNABLE"
TITLE_REVIEWED = "TITLE_WAS_REVIEWED"
TITLE_EMPTY = "TITLE_EMPTY"
TEXT_CHANGED = "NEXT_STEP_TEXT_CHANGED"
WITH_TEXT = "DATE_WITH_TEXT_CHANGE"
NO_STEP = "NO_SOURCE_STEP"
NOT_SOURCE_STEP = "STEP_NOT_FROM_SOURCE_WORDING"
DATE_CLEARED = "DATE_CLEARED"
DATE_PAST = "DATE_IN_PAST"
NOT_EXACT = "STEP_DATE_NOT_EXACT"
LINK_NOT_HTTPS = "LINK_NOT_HTTPS"
BREAKER = "CIRCUIT_BREAKER"
PARTIAL = "PARTIAL_FAILURE"
NO_BACKUP = "NO_VERIFIED_BACKUP"
COMPARE_ONLY = "COMPARE_ONLY"


class SyncRefused(Exception):
    """The run did not start, or stopped before writing. ``code`` goes to the ledger."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Canonical values
# ---------------------------------------------------------------------------


def text_value(raw: Any) -> str:
    """Whitespace-collapsed, NFC text: two spellings of one sentence are one value."""
    return " ".join(unicodedata.normalize("NFC", str(raw or "")).split())


def name_key(raw: Any) -> str:
    """Lovable's own `raw_norm`: lower-cased, collapsed, diacritics folded."""
    return text_value(raw).lower().translate(str.maketrans("õäöüšž", "oaousz"))


def day_value(raw: Any) -> str | None:
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(str(raw)[:10]).isoformat()
    except ValueError:
        return f"?{text_value(raw)}"


def operative_owner(row: dict[str, Any]) -> str:
    """Lovable's `topic_operative_vastutaja`: the new owner if named, else the owner."""
    new = text_value(row.get("uus_vastutaja"))
    if new:
        return "" if name_key(new) in {"vastutaja puudub", "vastutaja puudub."} else new
    return text_value(row.get("vastutaja"))


def _commencements(bundle: dict[str, Any]) -> list[list[Any]]:
    items = bundle.get("commencements") or []
    ordered = sorted(items, key=lambda r: (r.get("position") or 0, str(r.get("liik") or "")))
    return [
        [text_value(r.get("liik")), day_value(r.get("kuupaev")), text_value(r.get("tapsustus"))]
        for r in ordered
    ]


SOURCE_FIELDS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "title": lambda b: text_value(b["row"].get("teema")),
    "owner": lambda b: operative_owner(b["row"]),
    "next_action_text": lambda b: text_value(b["row"].get("jargmiseks")),
    "next_action_date": lambda b: day_value(b["row"].get("tegevuse_tahtaeg")),
    "onenote_link": lambda b: str(b["row"].get("onenote_link") or "").strip(),
    "stage": lambda b: text_value(b["row"].get("hetkeseis")),
    "opinion_deadline": lambda b: day_value(b["row"].get("arvamuse_tahtaeg")),
    "sent": lambda b: text_value(b["row"].get("valja")),
    "sent_date": lambda b: day_value(b["row"].get("valja_kuupaev")),
    "addressee": lambda b: text_value(b["row"].get("kellele")),
    "koda_initiative": lambda b: bool(b["row"].get("koja_ettepanek")),
    "legal_act": lambda b: text_value(b["row"].get("oigusakt")),
    "received": lambda b: day_value(b["row"].get("sisse")),
    "commencements": _commencements,
    "operational_scope": lambda b: bool(b["row"].get("operatiivne_ulatus")),
}


def canonical_source(bundle: dict[str, Any]) -> dict[str, Any]:
    return {key: extract(bundle) for key, extract in SOURCE_FIELDS.items()}


# ---------------------------------------------------------------------------
# The cohort and its baselines
# ---------------------------------------------------------------------------


def cohort_references() -> Any:
    """The pilot's Lovable source references — one per pilot Teema, never one of
    the 13 that were there before (they have no pilot record)."""
    from app.legacy_import.models import EvidencePilotRecord, MatterSourceReference

    pilot_matters = EvidencePilotRecord.objects.filter(pilot=PILOT, record_kind="MATTER").values(
        "matter_id"
    )
    return (
        MatterSourceReference.objects.filter(
            matter_id__in=pilot_matters,
            source_system=SOURCE_SYSTEM,
            source_file_name=SOURCE_TABLE,
            import_batch__isnull=False,
        )
        .exclude(source_contract_version__startswith=MAPPING_VERSION.split("/")[0])
        .select_related("matter")
        .order_by("created_at")
    )


def _action_dict(action: Any) -> dict[str, Any] | None:
    if action is None:
        return None
    return {
        "id": str(action.pk),
        "text": action.text,
        "kind": action.kind,
        "date_semantics": action.date_semantics,
        "target_date": action.target_date.isoformat() if action.target_date else None,
        "date_precision": action.date_precision,
    }


def _imported_action(matter: Any) -> tuple[dict[str, Any] | None, str]:
    """The step the pilot wrote and the treatment its package stated.

    Read off the pilot's own ``NEXT_ACTION_SET`` event, not off the row as it is
    now: «Vaatasin üle» and a rescheduled check change a step's day **in place**,
    keeping its id, and a baseline taken from the edited row would make the
    lawyer's day look like the pilot's and let a source change replace it. A row
    edited in place since gets an unknowable precision, so it reads as a local
    change, never as the baseline."""
    from app.audit.models import ChangeEvent
    from app.legacy_import.models import EvidencePilotRecord
    from app.workflow.models import NextAction

    record = EvidencePilotRecord.objects.filter(
        pilot=PILOT, matter=matter, record_kind="NEXT_ACTION"
    ).first()
    if record is None or record.target_id is None:
        return None, ""
    action = NextAction.objects.filter(pk=record.target_id).first()
    event = (
        ChangeEvent.objects.filter(
            matter=matter, object_id=record.target_id, event_type="NEXT_ACTION_SET"
        )
        .order_by("occurred_at")
        .first()
    )
    if action is None or event is None:
        return None, ""
    payload = event.payload or {}
    treatment = str((payload.get("provenance") or {}).get("treatment") or "")
    written = _action_dict(action) or {}
    written.update(
        kind=payload.get("kind", written["kind"]),
        date_semantics=payload.get("date_semantics", written["date_semantics"]),
        target_date=payload.get("target_date"),
    )
    edited = ChangeEvent.objects.filter(
        matter=matter,
        object_id=record.target_id,
        event_type__in=("NEXT_ACTION_REVIEWED", "NEXT_ACTION_RESCHEDULED"),
    ).exists()
    if edited:
        written["date_precision"] = "?"
    return written, treatment


def _imported_owner_id(matter: Any) -> str | None:
    """Who the pilot made the owner: the first hand-over's ``from``, or the owner
    now if nobody has handed it over since."""
    from app.accounts.models import User
    from app.audit.models import ChangeEvent

    first = (
        ChangeEvent.objects.filter(matter=matter, event_type="MATTER_ASSIGNED")
        .order_by("occurred_at")
        .first()
    )
    if first is None:
        return str(matter.owner_id) if matter.owner_id else None
    name = (first.payload or {}).get("from_name")
    if not name:
        return None
    users = list(User.objects.filter(display_name=name).values_list("pk", flat=True)[:2])
    return str(users[0]) if len(users) == 1 else "?"


def seed_state(reference: Any) -> LovableSyncState:
    """A's first value, from what the pilot wrote — never from the current row,
    which may already hold a lawyer's edit."""
    matter = reference.matter
    raw = reference.source_row_raw or {}
    bundle = {"row": raw.get("row") or {}, "commencements": raw.get("commencements") or []}
    source = canonical_source(bundle)
    action, treatment = _imported_action(matter)
    applied: dict[str, Any] = {key: {"source": value} for key, value in source.items()}
    applied["title"]["target"] = text_value(reference.source_title)
    applied["owner"]["target"] = _imported_owner_id(matter)
    applied["onenote_link"]["target"] = reference.onenote_url
    applied["next_action"] = {"target": action, "treatment": treatment}
    lovable = raw.get("lovable") or {}
    return LovableSyncState.objects.create(
        matter=matter,
        source_id=lovable["id"],
        reference=f"{matter.reference_year}_{matter.reference_number}",
        applied=applied,
        applied_version=lovable.get("version"),
        applied_snapshot_sha256=reference.source_snapshot_sha256,
    )


def ensure_states() -> list[LovableSyncState]:
    existing = set(LovableSyncState.objects.values_list("matter_id", flat=True))
    with transaction.atomic():
        for reference in cohort_references():
            if reference.matter_id not in existing:
                seed_state(reference)
                existing.add(reference.matter_id)
    return list(LovableSyncState.objects.select_related("matter").order_by("reference"))


# ---------------------------------------------------------------------------
# B — what Juristid holds now
# ---------------------------------------------------------------------------


def current_values(matter: Any) -> dict[str, Any]:
    from app.legacy_import.models import MatterSourceReference
    from app.workflow.enums import ActionStatus
    from app.workflow.models import NextAction

    link = (
        MatterSourceReference.objects.filter(matter=matter, source_system=SOURCE_SYSTEM)
        .order_by("-created_at")
        .values_list("onenote_url", flat=True)
        .first()
    )
    return {
        "title": text_value(matter.title),
        "owner": str(matter.owner_id) if matter.owner_id else None,
        "onenote_link": link or "",
        "next_action": _action_dict(
            NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN).first()
        ),
        # A tombstoned Teema is treated as a closed one: nothing is written to it.
        "is_open": matter.is_open and getattr(matter, "deleted_at", None) is None,
    }


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


@dataclass
class FieldDecision:
    field: str
    outcome: str
    reason: str = ""
    baseline: Any = None
    current: Any = None
    source: Any = None
    #: What Juristid should hold for an ELIGIBLE or converged field.
    target: Any = None


@dataclass
class MatterPlan:
    state: LovableSyncState
    status: str = ""  # SOURCE_MISSING / IDENTIFIER_CHANGED, or "" when compared
    source: dict[str, Any] = field(default_factory=dict)
    version: int | None = None
    decisions: list[FieldDecision] = field(default_factory=list)

    def outcomes(self) -> set[str]:
        return {d.outcome for d in self.decisions} | ({self.status} if self.status else set())

    @property
    def eligible(self) -> list[FieldDecision]:
        return [d for d in self.decisions if d.outcome == SyncOutcome.ELIGIBLE]


def _three_way(
    name: str, a_src: Any, c_src: Any, a_tgt: Any, b: Any, c_tgt: Any, held: str = ""
) -> FieldDecision:
    """The table at the top of this module, for one field."""
    common = {"baseline": a_src, "current": b, "source": c_src}
    if c_src == a_src:
        outcome = SyncOutcome.UNCHANGED if b == a_tgt else SyncOutcome.LOCAL_TEST_CHANGE
        return FieldDecision(name, outcome, **common)
    if c_tgt is not None and b == c_tgt:
        # Juristid already says what the source now says — even for a field a
        # machine may not write: the baseline catches up and nothing is written.
        return FieldDecision(name, SyncOutcome.ALREADY_CONVERGED, target=c_tgt, **common)
    if held:
        return FieldDecision(name, SyncOutcome.REVIEW_REQUIRED, held, **common)
    if b == c_tgt:
        return FieldDecision(name, SyncOutcome.ALREADY_CONVERGED, target=c_tgt, **common)
    if b == a_tgt:
        return FieldDecision(name, SyncOutcome.ELIGIBLE, target=c_tgt, **common)
    return FieldDecision(name, SyncOutcome.CONFLICT, **common)


class OwnerResolver:
    """A Lovable owner name → exactly one active, assignable Juristid user.

    First the pairs the pilot's reviewed people map produced (A's source name and
    A's owner, across the cohort), then an exact display name. Two candidates, or
    one who may not hold current work, is no answer."""

    def __init__(self, states: Iterable[LovableSyncState]) -> None:
        pairs: dict[str, set[str]] = {}
        for state in states:
            owner = state.applied.get("owner") or {}
            if owner.get("resolved"):
                continue  # an operator's resolution says who kept the file, not who a name is
            if owner.get("source") and owner.get("target") not in (None, "?"):
                pairs.setdefault(name_key(owner["source"]), set()).add(owner["target"])
        self.pairs = pairs

    def resolve(self, name: str) -> tuple[str | None, str]:
        from app.accounts.models import User
        from app.accounts.selectors import is_assignable_business_user

        if not name:
            return None, UNRESOLVED_OWNER
        known = self.pairs.get(name_key(name), set())
        candidates: list[Any]
        if len(known) == 1:
            candidates = list(User.objects.filter(pk__in=known, is_active=True))
        elif known:
            return None, UNRESOLVED_OWNER
        else:
            candidates = [
                user
                for user in User.objects.filter(is_active=True)
                if name_key(user.display_name) == name_key(name)
            ]
        if len(candidates) != 1:
            return None, UNRESOLVED_OWNER
        if not is_assignable_business_user(candidates[0]):
            return None, NOT_ASSIGNABLE
        return str(candidates[0].pk), ""


def _step_decisions(
    applied: dict[str, Any], src: dict[str, Any], b_step: dict[str, Any] | None, *, today: dt.date
) -> list[FieldDecision]:
    """`jargmiseks` and `tegevuse_tahtaeg` are one operation: a new day for an
    unchanged sentence may move, a new sentence never carries the old reading."""
    a_text = (applied.get("next_action_text") or {}).get("source")
    a_date = (applied.get("next_action_date") or {}).get("source")
    a_step = (applied.get("next_action") or {}).get("target")
    treatment = (applied.get("next_action") or {}).get("treatment") or ""
    c_text, c_date = src["next_action_text"], src["next_action_date"]
    b_view = {k: v for k, v in (b_step or {}).items() if k != "id"}

    out: list[FieldDecision] = []
    text_changed = c_text != a_text
    if text_changed:
        converged = (
            bool(b_step)
            and text_value(b_step["text"]) == c_text
            and b_step["target_date"] == c_date
        )
        out.append(
            FieldDecision(
                "next_action_text",
                SyncOutcome.ALREADY_CONVERGED if converged else SyncOutcome.REVIEW_REQUIRED,
                "" if converged else TEXT_CHANGED,
                a_text,
                b_view or None,
                c_text,
                # The lawyer wrote the source's sentence: that step is now the
                # baseline, and its wording is the source's own.
                target=b_step if converged else None,
            )
        )
    else:
        local = (b_step or None) != a_step
        out.append(
            FieldDecision(
                "next_action_text",
                SyncOutcome.LOCAL_TEST_CHANGE if local else SyncOutcome.UNCHANGED,
                baseline=a_text,
                current=b_view or None,
                source=c_text,
            )
        )
    if c_date == a_date:
        local = (b_step or None) != a_step
        out.append(
            FieldDecision(
                "next_action_date",
                SyncOutcome.LOCAL_TEST_CHANGE if local else SyncOutcome.UNCHANGED,
                baseline=a_date,
                current=(b_step or {}).get("target_date"),
                source=c_date,
            )
        )
        return out
    common = {"baseline": a_date, "current": (b_step or {}).get("target_date"), "source": c_date}
    if text_changed:
        held = out[0].outcome == SyncOutcome.REVIEW_REQUIRED
        out.append(
            FieldDecision(
                "next_action_date",
                SyncOutcome.REVIEW_REQUIRED if held else SyncOutcome.ALREADY_CONVERGED,
                WITH_TEXT if held else "",
                **common,
            )
        )
        return out
    reason = ""
    if not a_step:
        reason = NO_STEP
    elif treatment != "SOURCE_NEXT_STEP":
        reason = NOT_SOURCE_STEP
    elif c_date is None:
        reason = DATE_CLEARED
    elif str(c_date).startswith("?"):
        reason = NO_BASELINE
    elif a_step.get("date_precision") != "EXACT":
        reason = NOT_EXACT
    elif dt.date.fromisoformat(c_date) < today:
        reason = DATE_PAST
    if (
        b_step
        and b_step.get("target_date") == c_date
        and {k: v for k, v in b_step.items() if k not in ("id", "target_date")}
        == {k: v for k, v in (a_step or {}).items() if k not in ("id", "target_date")}
    ):
        out.append(
            FieldDecision(
                "next_action_date", SyncOutcome.ALREADY_CONVERGED, target=b_step, **common
            )
        )
        return out
    if a_step and (b_step or None) != a_step:
        # The lawyer's step differs from what was applied: that is a conflict to
        # show, whatever else would also have held the change.
        out.append(FieldDecision("next_action_date", SyncOutcome.CONFLICT, **common))
        return out
    if reason:
        out.append(FieldDecision("next_action_date", SyncOutcome.REVIEW_REQUIRED, reason, **common))
        return out
    target = {**a_step, "target_date": c_date}
    out.append(FieldDecision("next_action_date", SyncOutcome.ELIGIBLE, target=target, **common))
    return out


def plan_matter(
    state: LovableSyncState,
    bundle: dict[str, Any] | None,
    *,
    resolver: OwnerResolver,
    today: dt.date,
    current: dict[str, Any] | None = None,
) -> MatterPlan:
    plan = MatterPlan(state=state)
    if bundle is None:
        plan.status = SyncOutcome.SOURCE_MISSING
        return plan
    row = bundle["row"]
    if f"{row.get('nr')}" != state.reference or str(row.get("id")) != str(state.source_id):
        plan.status = SyncOutcome.IDENTIFIER_CHANGED
        return plan
    src = canonical_source(bundle)
    plan.source, plan.version = src, row.get("version")
    b = current if current is not None else current_values(state.matter)
    applied = state.applied
    closed = not b["is_open"]

    def a(name: str, part: str = "source") -> Any:
        return (applied.get(name) or {}).get(part)

    # The title: the source's sentence is the Teema's only when the review kept it.
    title_held = MATTER_CLOSED if closed else ""
    if not title_held and a("title", "target") != a("title"):
        title_held = TITLE_REVIEWED
    if not title_held and not src["title"]:
        title_held = TITLE_EMPTY
    plan.decisions.append(
        _three_way(
            "title",
            a("title"),
            src["title"],
            a("title", "target"),
            b["title"],
            src["title"],
            title_held,
        )
    )

    # The owner, only to a person who resolves exactly.
    owner_target, owner_held = (None, MATTER_CLOSED) if closed else (None, "")
    owner_baseline = a("owner", "target")
    if owner_baseline == "?":
        # The pilot's owner could not be named again from the audit: unchanged
        # stays unchanged, and any source change is a person's call.
        owner_baseline, owner_held = b["owner"], owner_held or NO_BASELINE
    if not owner_held and src["owner"] != a("owner"):
        owner_target, owner_held = resolver.resolve(src["owner"])
    plan.decisions.append(
        _three_way(
            "owner", a("owner"), src["owner"], owner_baseline, b["owner"], owner_target, owner_held
        )
    )

    # The current step.
    if closed:
        for name in ("next_action_text", "next_action_date"):
            plan.decisions.append(
                _three_way(name, a(name), src[name], None, None, None, MATTER_CLOSED)
            )
    else:
        plan.decisions.extend(_step_decisions(applied, src, b["next_action"], today=today))

    # The OneNote link: a new observation beside the old, https only.
    link_held = (
        ""
        if src["onenote_link"].startswith("https://") or not src["onenote_link"]
        else LINK_NOT_HTTPS
    )
    if not src["onenote_link"]:
        link_held = HELD  # a removed link is a person's call, never an erasure
    if closed:
        link_held = MATTER_CLOSED
    plan.decisions.append(
        _three_way(
            "onenote_link",
            a("onenote_link"),
            src["onenote_link"],
            a("onenote_link", "target"),
            b["onenote_link"],
            src["onenote_link"],
            link_held,
        )
    )

    for name in HELD_FIELDS:
        if name == "next_action_text":
            continue
        if name not in applied:
            continue  # started below, on first observation
        plan.decisions.append(_three_way(name, a(name), src[name], None, None, None, HELD))
    return plan


# ---------------------------------------------------------------------------
# Applying one Teema
# ---------------------------------------------------------------------------


def _provenance(
    run: LovableSyncRun, state: LovableSyncState, version: int | None, sha: str
) -> dict[str, Any]:
    return {
        "provenance_source": PROVENANCE_SOURCE,
        "sync_run": str(run.pk),
        "source_reference": state.reference,
        "source_version": version,
        "source_snapshot_sha256": sha,
        "mapping_version": MAPPING_VERSION,
    }


def idempotency_key(state: LovableSyncState, decision: FieldDecision, version: int | None) -> str:
    """One applied write per source row version and field. A value may come back
    (12.11 → 15.11 → 12.11 → 15.11); a Lovable version never does, because its
    guard makes every save exactly one higher."""
    material = json.dumps(
        [str(state.source_id), decision.field, version, decision.source],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _apply_field(
    decision: FieldDecision,
    *,
    matter: Any,
    provenance: dict[str, Any],
    run: LovableSyncRun,
    snapshot: dict[str, Any],
) -> Any:
    """One allowlisted write, through its canonical service. Returns the new B."""
    from app.accounts.models import User
    from app.matters.services import assign_matter, set_matter_title
    from app.workflow.models import NextAction
    from app.workflow.services import set_next_action

    if decision.field == "title":
        set_matter_title(matter=matter, value=decision.target, actor=None, provenance=provenance)
        return text_value(matter.title)
    if decision.field == "owner":
        owner = User.objects.get(pk=decision.target, is_active=True)
        assign_matter(matter=matter, owner=owner, actor=None, provenance=provenance)
        return str(owner.pk)
    if decision.field == "next_action_date":
        open_step = NextAction.objects.select_for_update(no_key=True).get(pk=decision.target["id"])
        new = set_next_action(
            matter=matter,
            text=open_step.text,
            kind=open_step.kind,
            date_semantics=open_step.date_semantics,
            target_date=dt.date.fromisoformat(decision.target["target_date"]),
            date_precision=open_step.date_precision,
            source_text=open_step.source_text,
            responsible=open_step.responsible,
            actor=None,
            provenance={**provenance, "kind_basis": "UNCHANGED_SOURCE_WORDING"},
            carry_plan_step=True,
            carry_visibility_override=True,
        )
        return _action_dict(new)
    if decision.field == "onenote_link":
        return _append_link_observation(
            matter=matter, url=decision.target, run=run, snapshot=snapshot, provenance=provenance
        )
    raise SyncRefused("NOT_ALLOWLISTED", f"{decision.field} is not an automatic field.")


def _sync_batch(run: LovableSyncRun, snapshot: dict[str, Any]) -> Any:
    from app.legacy_import.models import ImportBatch, ReconciliationStatus

    batch, _ = ImportBatch.objects.get_or_create(
        source_system=SOURCE_SYSTEM,
        source_file_name=SOURCE_TABLE,
        source_snapshot_sha256=snapshot["sha256"],
        importer_version=MAPPING_VERSION,
        defaults={
            "contract_version": MAPPING_VERSION,
            "started_at": run.started_at,
            "reconciliation_status": ReconciliationStatus.COMPLETED,
            "finished_at": run.started_at,
            "notes": f"Lovable sync run {run.pk}",
        },
    )
    return batch


def _append_link_observation(
    *,
    matter: Any,
    url: str,
    run: LovableSyncRun,
    snapshot: dict[str, Any],
    provenance: dict[str, Any],
) -> str:
    """A new `MatterSourceReference` for the new link; the pilot's stays as it was."""
    from app.legacy_import.enums import OneNoteContentStatus
    from app.legacy_import.models import MatchMethod, MatterSourceReference

    year, number = matter.reference_year, matter.reference_number
    MatterSourceReference.objects.create(
        matter=matter,
        import_batch=_sync_batch(run, snapshot),
        source_system=SOURCE_SYSTEM,
        source_file_name=SOURCE_TABLE,
        source_snapshot_sha256=snapshot["sha256"],
        source_sheet=f"{SOURCE_TABLE}:{year}",
        source_row_number=number,
        source_row_raw={"lovable_sync": provenance, "onenote_link": url},
        source_title=matter.title,
        onenote_url=url,
        onenote_content_status=OneNoteContentStatus.NOT_IMPORTED,
        source_era=str(year),
        source_contract_version=MAPPING_VERSION,
        source_parser_version=MAPPING_VERSION,
        match_method=MatchMethod.REFERENCE_TOKEN,
        conflict_state="NONE",
    )
    return url


def apply_matter(
    plan: MatterPlan,
    *,
    run: LovableSyncRun,
    snapshot: dict[str, Any],
    resolver: OwnerResolver,
    today: dt.date,
) -> MatterPlan:
    """Every eligible field of one Teema, all or nothing, under its lock.

    The plan is made again under the lock: a lawyer's save that landed after the
    first reading turns ELIGIBLE into CONFLICT or LOCAL_TEST_CHANGE here, never
    into an overwrite."""
    from app.matters.locks import lock_matter_for_write

    with transaction.atomic():
        matter = lock_matter_for_write(plan.state.matter_id)
        state = LovableSyncState.objects.select_for_update().get(pk=plan.state.pk)
        state.matter = matter
        bundle = snapshot["cohort"].get(str(state.source_id))
        fresh = plan_matter(state, bundle, resolver=resolver, today=today)
        provenance = _provenance(run, state, fresh.version, snapshot["sha256"])
        applied = dict(state.applied)
        # Title first, then owner, then the step: a hand-over moves the open step
        # to its new owner, and the date edit then carries that responsibility.
        order = {name: i for i, name in enumerate(AUTO_FIELDS)}
        for decision in sorted(fresh.eligible, key=lambda d: order[d.field]):
            new_b = _apply_field(
                decision, matter=matter, provenance=provenance, run=run, snapshot=snapshot
            )
            decision.outcome = SyncOutcome.APPLIED
            decision.current = new_b
            if decision.field == "next_action_date":
                applied["next_action_date"] = {"source": decision.source}
                applied["next_action"] = {**applied.get("next_action", {}), "target": new_b}
            else:
                applied[decision.field] = {"source": decision.source, "target": new_b}
            LovableSyncDecision.objects.create(
                run=run,
                state=state,
                field=decision.field,
                outcome=SyncOutcome.APPLIED,
                baseline=decision.baseline,
                current=new_b,
                source=decision.source,
                idempotency_key=idempotency_key(state, decision, fresh.version),
            )
        for decision in fresh.decisions:
            if decision.outcome == SyncOutcome.ALREADY_CONVERGED:
                _advance(applied, decision)
        if (
            fresh.eligible
            or any(d.outcome == SyncOutcome.APPLIED for d in fresh.decisions)
            or applied != state.applied
        ):
            state.applied = applied
            state.applied_version = fresh.version
            state.applied_snapshot_sha256 = snapshot["sha256"]
            state.save(
                update_fields=[
                    "applied",
                    "applied_version",
                    "applied_snapshot_sha256",
                    "updated_at",
                ]
            )
        fresh.state = state
        return fresh


def _advance(applied: dict[str, Any], decision: FieldDecision) -> None:
    """B already says what C says: the baseline catches up, nothing is written."""
    if decision.field == "next_action_text":
        applied["next_action_text"] = {"source": decision.source}
        if isinstance(decision.target, dict):
            # Juristid's open step now reads exactly as Lovable's sentence: a later
            # day-only change of that sentence may move it.
            applied["next_action"] = {"target": decision.target, "treatment": "SOURCE_NEXT_STEP"}
    elif decision.field == "next_action_date":
        applied["next_action_date"] = {"source": decision.source}
        if isinstance(decision.target, dict):
            applied["next_action"] = {**applied.get("next_action", {}), "target": decision.target}
    else:
        applied[decision.field] = {"source": decision.source, "target": decision.target}


def verify_matter(plan: MatterPlan) -> bool:
    """After commit: B is exactly what the baseline says Juristid was given."""
    from app.matters.models import Matter

    state = LovableSyncState.objects.get(pk=plan.state.pk)
    b = current_values(Matter.objects.get(pk=state.matter_id))
    for decision in plan.decisions:
        if decision.outcome != SyncOutcome.APPLIED:
            continue
        if decision.field == "next_action_date":
            if b["next_action"] != state.applied["next_action"]["target"]:
                return False
        elif b[decision.field] != state.applied[decision.field]["target"]:
            return False
    return True


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def control() -> LovableSyncControl:
    row, _ = LovableSyncControl.objects.get_or_create(pk=1)
    return row


def set_mode(mode: str, *, reason: str) -> LovableSyncControl:
    if mode not in SyncMode.values:
        raise SyncRefused("UNKNOWN_MODE", f"Unknown mode {mode!r}.")
    with transaction.atomic():
        row = LovableSyncControl.objects.select_for_update().get_or_create(pk=1)[0]
        if row.cut_over and mode != SyncMode.OFF:
            raise SyncRefused(
                "CUT_OVER", "Final cutover is recorded; the synchronisation never runs again."
            )
        row.mode, row.changed_at, row.changed_reason = mode, timezone.now(), reason[:300]
        row.save()
    return row


def cut_over(*, reason: str) -> LovableSyncControl:
    """Final cutover: Juristid becomes the source of truth. Irreversible."""
    with transaction.atomic():
        row = LovableSyncControl.objects.select_for_update().get_or_create(pk=1)[0]
        now = timezone.now()
        row.mode, row.cut_over, row.cut_over_at = SyncMode.OFF, True, row.cut_over_at or now
        row.changed_at, row.changed_reason = now, reason[:300]
        row.save()
    return row


@contextmanager
def run_lock() -> Any:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [LOCK_NAMESPACE, LOCK_KEY])
        if not cursor.fetchone()[0]:
            raise SyncRefused("LOCKED", "Another Lovable synchronisation is running.")
    try:
        yield
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [LOCK_NAMESPACE, LOCK_KEY])
        except DatabaseError:
            # The session lock dies with the connection; closing it releases it and
            # never masks the error the run itself raised.
            connection.close()


def run_key_for(trigger: str, now: dt.datetime) -> str:
    local = now.astimezone(TALLINN)
    if trigger == SyncTrigger.SCHEDULED:
        return local.date().isoformat()
    return f"{local:%Y-%m-%dT%H%M%S.%f}-{trigger.lower()}"


@dataclass
class RunCounts:
    checked: int = 0
    unchanged: int = 0
    updated: int = 0
    local_only: int = 0
    conflicts: int = 0
    review: int = 0
    eligible_unapplied: int = 0
    converged: int = 0
    missing: int = 0
    identifier_changed: int = 0
    failed: int = 0
    fields: dict[str, int] = field(default_factory=dict)
    source_live: int = 0
    outside_cohort: int = 0
    new_since_previous: int = 0
    operational_not_in_juristid: int = 0
    duration_seconds: float = 0.0

    def add(self, plan: MatterPlan) -> None:
        outcomes = plan.outcomes()
        self.checked += 1
        if outcomes <= {SyncOutcome.UNCHANGED}:
            self.unchanged += 1
        self.updated += SyncOutcome.APPLIED in outcomes
        self.local_only += SyncOutcome.LOCAL_TEST_CHANGE in outcomes
        self.conflicts += SyncOutcome.CONFLICT in outcomes
        self.review += SyncOutcome.REVIEW_REQUIRED in outcomes
        self.eligible_unapplied += SyncOutcome.ELIGIBLE in outcomes
        self.converged += SyncOutcome.ALREADY_CONVERGED in outcomes
        self.missing += SyncOutcome.SOURCE_MISSING in outcomes
        self.identifier_changed += SyncOutcome.IDENTIFIER_CHANGED in outcomes
        self.failed += SyncOutcome.FAILED in outcomes
        tally = Counter(
            f"{d.field}:{d.outcome}" for d in plan.decisions if d.outcome != SyncOutcome.UNCHANGED
        )
        for key, n in tally.items():
            self.fields[key] = self.fields.get(key, 0) + n

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _observe_index(
    counts: RunCounts,
    snapshot: dict[str, Any],
    cohort_ids: set[str],
    previous: dict[str, Any] | None,
) -> None:
    from app.matters.models import Matter

    index = snapshot.get("index") or []
    counts.source_live = len(index)
    counts.outside_cohort = sum(1 for row in index if str(row["id"]) not in cohort_ids)
    if previous is not None:
        before = {str(row["id"]) for row in previous.get("index") or []}
        counts.new_since_previous = sum(1 for row in index if str(row["id"]) not in before)
    known = set(
        Matter.all_objects.exclude(reference_year__isnull=True).values_list(
            "reference_year", "reference_number"
        )
    )
    operational = 0
    for row in index:
        if not row.get("operatiivne_ulatus") or str(row["id"]) in cohort_ids:
            continue
        match = re.fullmatch(r"(\d{4})_(\d+)", str(row.get("nr") or ""))
        if match is None or (int(match.group(1)), int(match.group(2))) not in known:
            operational += 1
    counts.operational_not_in_juristid = operational


def _record(run: LovableSyncRun, plan: MatterPlan) -> None:
    rows = [
        LovableSyncDecision(
            run=run,
            state=plan.state,
            field=d.field,
            outcome=d.outcome,
            reason=d.reason,
            baseline=d.baseline,
            current=d.current,
            source=d.source,
        )
        for d in plan.decisions
        if d.outcome not in (SyncOutcome.UNCHANGED, SyncOutcome.APPLIED)
    ]
    if plan.status:
        rows.append(LovableSyncDecision(run=run, state=plan.state, field="*", outcome=plan.status))
    LovableSyncDecision.objects.bulk_create(rows)


def _observe(state: LovableSyncState, plan: MatterPlan, run: LovableSyncRun) -> None:
    """Last observed, apart from last applied. Fields the pilot's snapshot did not
    carry start their baseline here, on first sight, as report-only fields."""
    updates = {"observed": plan.source, "observed_version": plan.version, "observed_run": run}
    applied = dict(state.applied)
    started = False
    for name in HELD_FIELDS:
        if name not in applied and name in plan.source:
            applied[name] = {"source": plan.source[name]}
            started = True
    if started:
        updates["applied"] = applied
    LovableSyncState.objects.filter(pk=state.pk).update(**updates, updated_at=timezone.now())


def fresh_backup_set(backup_set: str, started: dt.datetime) -> str:
    """The set's name if it is a set taken shortly before this run, else ``""``."""
    if not _BACKUP_SET.match(backup_set or ""):
        return ""
    taken = dt.datetime.strptime(backup_set[5:], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC)
    return backup_set if dt.timedelta(0) <= started - taken <= BACKUP_MAX_AGE else ""


def run_sync(
    *,
    trigger: str,
    transport: Transport | None = None,
    backup_set: str = "",
    now: Callable[[], dt.datetime] | None = None,
    pause: Callable[[float], None] | None = None,
) -> LovableSyncRun:
    """One run, end to end. Returns the ledger row; never raises after it exists."""
    clock = now or timezone.now
    ctl = control()
    if ctl.cut_over:
        raise SyncRefused(
            "CUT_OVER", "Final cutover is recorded; the synchronisation is disabled for good."
        )
    if trigger != SyncTrigger.CHECK and ctl.mode == SyncMode.OFF:
        raise SyncRefused("PAUSED", "The synchronisation is paused (mode OFF).")
    if trigger != SyncTrigger.CHECK and transport is None:
        raise SyncRefused("NO_SOURCE", "A run that reads Lovable needs the source connection.")
    started = clock()
    key = run_key_for(trigger, started)
    if LovableSyncRun.objects.filter(run_key=key).exists():
        raise SyncRefused(
            "DUPLICATE", f"Run {key} already exists; the nightly run happens once per date."
        )

    with run_lock():
        mode = SyncMode.COMPARE if trigger == SyncTrigger.CHECK else ctl.mode
        try:
            with transaction.atomic():
                run = LovableSyncRun.objects.create(
                    run_key=key,
                    trigger=trigger,
                    mode=mode,
                    started_at=started,
                    mapping_version=MAPPING_VERSION,
                    backup_set=fresh_backup_set(backup_set, started),
                )
        except IntegrityError as error:
            raise SyncRefused("DUPLICATE", f"Run {key} already exists.") from error
        try:
            _execute(run, transport=transport, clock=clock, pause=pause)
        except (SourceError, SyncRefused) as error:
            _fail(run, error.code, str(error), clock)
        except Exception as error:
            logger.error("Lovable sync run %s failed (%s)", run.pk, type(error).__name__)
            _fail(run, "INTERNAL", type(error).__name__, clock)
        return run


def _fail(run: LovableSyncRun, code: str, message: str, clock: Callable[[], dt.datetime]) -> None:
    run.status = SyncRunStatus.FAILED
    run.error = f"{code}: {message}"[:300]
    run.finished_at = clock()
    run.save(update_fields=["status", "error", "finished_at", "updated_at"])


def _execute(
    run: LovableSyncRun,
    *,
    transport: Transport | None,
    clock: Callable[[], dt.datetime],
    pause: Callable[[float], None] | None,
) -> None:
    from app.matters.models import Matter
    from app.search.indexing import refresh_matters

    check = run.trigger == SyncTrigger.CHECK
    # `Kontrolli muudatusi` reads only: it seeds nothing, advances no baseline,
    # observes nothing and prunes nothing; it records its own findings, no more.
    states = (
        list(LovableSyncState.objects.select_related("matter").order_by("reference"))
        if check
        else ensure_states()
    )
    if len(states) != EXPECTED_COHORT:
        raise SyncRefused(
            "COHORT_SIZE",
            f"The synchronised cohort holds {len(states)} Teemad, not {EXPECTED_COHORT}.",
        )
    cohort_ids = {str(s.source_id) for s in states}
    previous_snapshot = LovableSyncSnapshot.objects.order_by("-taken_at").first()

    if run.trigger == SyncTrigger.CHECK:
        if previous_snapshot is None:
            raise SyncRefused("NO_SNAPSHOT", "No stored Lovable snapshot to compare against yet.")
        snapshot = previous_snapshot.document
        verify_snapshot(snapshot)
    else:
        if transport is None:
            raise SyncRefused("NO_SOURCE", "A run that reads Lovable needs the source connection.")
        snapshot = take_snapshot(
            transport,
            sorted(cohort_ids),
            mapping_version=MAPPING_VERSION,
            **({"pause": pause} if pause else {}),
        )
    verify_snapshot(snapshot)
    run.snapshot_sha256 = snapshot["sha256"]
    run.snapshot_taken_at = dt.datetime.fromisoformat(snapshot["taken_at"])
    run.save(update_fields=["snapshot_sha256", "snapshot_taken_at", "updated_at"])
    if run.trigger != SyncTrigger.CHECK:
        LovableSyncSnapshot.objects.create(
            run=run, taken_at=run.snapshot_taken_at, sha256=snapshot["sha256"], document=snapshot
        )

    today = clock().astimezone(TALLINN).date()
    resolver = OwnerResolver(states)
    plans = [
        plan_matter(s, snapshot["cohort"].get(str(s.source_id)), resolver=resolver, today=today)
        for s in states
    ]

    missing = sum(1 for p in plans if p.status == SyncOutcome.SOURCE_MISSING)
    if missing > MAX_MISSING:
        raise SyncRefused(
            "COHORT_MISSING", f"{missing} synchronised Teemad are missing from the source read."
        )

    changed = [p for p in plans if p.eligible]
    conflicts = sum(1 for p in plans if SyncOutcome.CONFLICT in p.outcomes())
    previous_run = (
        LovableSyncRun.objects.exclude(pk=run.pk)
        .exclude(trigger=SyncTrigger.CHECK)
        .exclude(status=SyncRunStatus.FAILED)
        .order_by("-started_at")
        .first()
    )
    conflicts_before = (previous_run.counts or {}).get("conflicts", 0) if previous_run else 0
    hold = ""
    if run.mode != SyncMode.APPLY or check:
        hold = COMPARE_ONLY
    elif len(changed) > MAX_CHANGED_MATTERS or conflicts - conflicts_before > MAX_NEW_CONFLICTS:
        hold = BREAKER
    elif not run.backup_set:
        hold = NO_BACKUP

    applied_ids: list[Any] = []
    verified = True
    final: list[MatterPlan] = []
    for plan in plans:
        if hold or not plan.eligible:
            final.append(plan)
            continue
        try:
            done = apply_matter(plan, run=run, snapshot=snapshot, resolver=resolver, today=today)
        except Exception as error:
            logger.warning(
                "Lovable sync: %s not applied (%s)", plan.state.reference, type(error).__name__
            )
            plan.status = SyncOutcome.FAILED
            final.append(plan)
            continue
        final.append(done)
        if any(d.outcome == SyncOutcome.APPLIED for d in done.decisions):
            applied_ids.append(done.state.matter_id)
            verified = verify_matter(done) and verified

    # Converged fields advance their baseline even on a compare-only night: no
    # business write, only the record of what Lovable and Juristid agree on.
    with transaction.atomic():
        for plan in final:
            if check or plan.status or plan.state.pk is None:
                continue
            state = LovableSyncState.objects.select_for_update().get(pk=plan.state.pk)
            applied = dict(state.applied)
            for decision in plan.decisions:
                if decision.outcome == SyncOutcome.ALREADY_CONVERGED:
                    _advance(applied, decision)
            if applied != state.applied:
                LovableSyncState.objects.filter(pk=state.pk).update(
                    applied=applied, updated_at=timezone.now()
                )
            plan.state.applied = applied

    if applied_ids:
        refresh_matters(Matter.objects.filter(pk__in=applied_ids))

    counts = RunCounts()
    for plan in final:
        counts.add(plan)
        _record(run, plan)
        if not plan.status and not check:
            _observe(LovableSyncState.objects.get(pk=plan.state.pk), plan, run)
    _observe_index(
        counts, snapshot, cohort_ids, previous_snapshot.document if previous_snapshot else None
    )
    finished = clock()
    counts.duration_seconds = round((finished - run.started_at).total_seconds(), 1)

    run.counts = counts.as_dict()
    run.verified = verified
    run.finished_at = finished
    if not verified:
        run.status = SyncRunStatus.FAILED
        run.error = "VERIFY: an applied Teema does not read back as written."
    elif counts.failed:
        # The others were written and verified; the run still is not a success.
        run.status = SyncRunStatus.FAILED
        run.error = f"{PARTIAL}: {counts.failed} Teema(d) not applied; nothing half-written."
    elif hold in (BREAKER, NO_BACKUP):
        run.status = SyncRunStatus.REVIEW_ONLY
        run.error = hold
    else:
        run.status = SyncRunStatus.SUCCEEDED
        run.error = ""
    run.save(update_fields=["counts", "verified", "finished_at", "status", "error", "updated_at"])
    if not check:
        prune_snapshots(now=finished)


def prune_snapshots(*, now: dt.datetime) -> int:
    """Snapshots older than the approved retention go; the run ledger stays."""
    cutoff = now - dt.timedelta(days=SNAPSHOT_RETENTION_DAYS)
    latest = LovableSyncSnapshot.objects.order_by("-taken_at").values_list("pk", flat=True).first()
    deleted, _ = LovableSyncSnapshot.objects.filter(taken_at__lt=cutoff).exclude(pk=latest).delete()
    return deleted


def resolve_field(*, reference: str, field_name: str, note: str) -> LovableSyncDecision:
    """An operator's resolution of a held change: the baseline takes the last
    observed source value, and the Teema keeps whatever Juristid holds."""
    if field_name not in FIELDS:
        raise SyncRefused("UNKNOWN_FIELD", f"Unknown field {field_name!r}.")
    with transaction.atomic():
        state = (
            LovableSyncState.objects.select_for_update()
            .select_related("matter")
            .get(reference=reference)
        )
        if field_name not in state.observed:
            raise SyncRefused("NOT_OBSERVED", "Nothing observed for that field yet.")
        run = state.observed_run
        if run is None:
            raise SyncRefused("NOT_OBSERVED", "Nothing observed yet.")
        applied = dict(state.applied)
        b = current_values(state.matter)
        value = state.observed[field_name]
        before = (applied.get(field_name) or {}).get("source")
        if field_name in ("next_action_text", "next_action_date"):
            applied[field_name] = {"source": value}
            step = b["next_action"]
            # The step Juristid keeps is the source's only if it reads as the
            # source's sentence; otherwise a later day change of that sentence
            # must never move the lawyer's own step.
            source_wording = bool(step) and text_value(step["text"]) == text_value(
                state.observed.get("next_action_text")
            )
            applied["next_action"] = {
                "target": step,
                "treatment": "SOURCE_NEXT_STEP" if source_wording else "RESOLVED_LOCAL",
            }
        elif field_name == "owner":
            # Who kept the file, not who the name is: never taught to the resolver.
            applied[field_name] = {"source": value, "target": b[field_name], "resolved": True}
        elif field_name in ("title", "onenote_link"):
            applied[field_name] = {"source": value, "target": b[field_name]}
        else:
            applied[field_name] = {"source": value}
        state.applied = applied
        state.save(update_fields=["applied", "updated_at"])
        return LovableSyncDecision.objects.create(
            run=run,
            state=state,
            field=field_name,
            outcome=SyncOutcome.ALREADY_CONVERGED,
            reason=f"RESOLVED: {note}"[:60],
            baseline=before,
            source=value,
        )


def summary_lines(run: LovableSyncRun) -> list[str]:
    """The night in a few Estonian lines (docs/adr/0192 §9). Counts only."""
    c = run.counts or {}
    day = run.started_at.astimezone(TALLINN)
    lines = [f"Lovable sünkroonimine — {day:%d.%m.%Y}"]
    if run.status == SyncRunStatus.FAILED:
        lines.append(f"EBAÕNNESTUS: {run.error.split(':')[0]}")
        return lines
    lines += [
        f"Kontrollitud: {c.get('checked', 0)} teemat",
        f"Muutusteta: {c.get('unchanged', 0)}",
        f"Uuendatud: {c.get('updated', 0)}",
        f"Kohalikke testmuudatusi: {c.get('local_only', 0)}",
        f"Vastuolusid: {c.get('conflicts', 0)}",
        f"Vajab ülevaatamist: {c.get('review', 0)}",
        f"Vigu: {c.get('failed', 0)}",
    ]
    if run.mode != SyncMode.APPLY or run.status == SyncRunStatus.REVIEW_ONLY:
        lines.append(f"Ohutuid uuendusi rakendamata: {c.get('eligible_unapplied', 0)}")
    return lines


#: A nightly job that has not succeeded for longer than this is stale, and says so.
STALE_AFTER = dt.timedelta(hours=26)


@dataclass(frozen=True)
class SyncHealth:
    ok: bool
    detail: str
    last_run: LovableSyncRun | None
    last_success: LovableSyncRun | None


def sync_health(*, now: dt.datetime | None = None) -> SyncHealth:
    """The operator's verdict: never an earlier success shown as if it were tonight's."""
    moment = now or timezone.now()
    ctl = control()
    runs = LovableSyncRun.objects.exclude(trigger=SyncTrigger.CHECK).order_by("-started_at")
    last, good = runs.first(), runs.filter(status=SyncRunStatus.SUCCEEDED).first()
    if ctl.cut_over:
        return SyncHealth(True, "cut over: disabled for good", last, good)
    if ctl.mode == SyncMode.OFF:
        return SyncHealth(True, "paused", last, good)
    if last is None:
        return SyncHealth(False, "enabled but has never run", last, good)
    if last.status == SyncRunStatus.FAILED:
        return SyncHealth(False, f"last run failed ({last.error.split(':')[0]})", last, good)
    if last.status == SyncRunStatus.REVIEW_ONLY:
        return SyncHealth(False, f"last run held for review ({last.error})", last, good)
    if last.status == SyncRunStatus.RUNNING and moment - last.started_at > dt.timedelta(hours=1):
        return SyncHealth(False, "a run started over an hour ago and never finished", last, good)
    if good is None or moment - good.started_at > STALE_AFTER:
        return SyncHealth(False, "no successful run in the last 26 hours", last, good)
    return SyncHealth(True, f"{ctl.mode.lower()}, last success {good.run_key}", last, good)


def due(*, trigger: str, now: dt.datetime | None = None) -> str:
    """``DUE APPLY``, ``DUE COMPARE`` or ``NOT_DUE <reason>``.

    The host's cron fires at 21:15 and 22:15 UTC; exactly one of them is 00:15 in
    Tallinn, summer or winter, and only that one is due. The run key then makes a
    second attempt on the same date refuse."""
    moment = now or timezone.now()
    ctl = control()
    if ctl.cut_over:
        return "NOT_DUE CUT_OVER"
    if ctl.mode == SyncMode.OFF:
        return "NOT_DUE PAUSED"
    if trigger == SyncTrigger.SCHEDULED:
        if moment.astimezone(TALLINN).hour != 0:
            return "NOT_DUE NOT_MIDNIGHT_IN_TALLINN"
        if LovableSyncRun.objects.filter(run_key=run_key_for(trigger, moment)).exists():
            return "NOT_DUE ALREADY_RAN"
    return f"DUE {ctl.mode}"
