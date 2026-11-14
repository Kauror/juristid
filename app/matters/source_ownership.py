"""What Lovable owns on a synchronised Teema, and the one guard that keeps a person off it.

Since 11.11.2026 Lovable is the source of truth for every Teema the nightly
synchronisation follows (`app.legacy_import.lovable_sync.FIELD_POLICY`, owner
decision 11.11): a value a person changes in Juristid while Lovable did not
change is put back the next night («Lovable'i väärtus taastati»). Offering
those edits made the page dishonest — the work was undone without a word
(audit 2026-11-11 TL-01 = LS-04). So on a synchronised Teema the source-owned
facts are read-only, here and on every surface that writes them:

* the **pages** draw them without an editor, with an `i` that says where they
  are changed (`source_owned` in the Teema contexts,
  `components/source_owned_tip.html`);
* the **services** refuse a person's change (`refuse_source_owned_change`),
  with a sentence that says why — so a stale tab, a crafted POST and an MCP
  write are refused exactly like the page would have been.

**Who is refused.** A change *made by a person*: a call whose ``actor`` is a
user. Every web view and every MCP write passes its user as the actor (the
audit trail requires it); the synchronisation writes with no actor and its
`LOVABLE_SYNC` provenance (`lovable_sync` module docstring), as do the
importers that create and rebase these Teemas. There is no keyword a caller
passes to be let through.

**When.** While the synchronisation follows Lovable — COMPARE as well as APPLY
(owner decision 12.11, docs/open-decisions.md E9): in COMPARE the night only
reports, but Lovable is still the source, and an edit made then would be put
back on the first APPLY night. Not after final cutover (`cut_over`: Juristid
is the source, and a cut over row is always OFF), and not while the switch is
OFF: the operator's pause, where no night runs, is the one lever that unlocks
the Teemad — all of them at once; no setting unlocks a single one (E13). The
cost: an edit made while OFF is put back on the first APPLY night after it.
This exception awaits the owner's confirmation (E9). See `Ownership.active`.
Never a field the night holds with an open technical hold — it is not
«restored over» — which stays editable, with a note why (`Ownership.held`).

**What stays editable** is everything Lovable does not own: notes, documents,
submissions and opinions, evidence, a person's own important and commencement
dates, planned Tegevused beside the current step, related materials, the
summary, the senders and the policy areas. A native Teema, a Teema of a retired
cohort and a deleted one are never restricted.

**Unchanged values are not changes.** Each guarded service returns early when
handed the value it already holds, and the guard sits after that return, so
`Muuda teemat` — which posts every field — still saves a correction of the
summary on a synchronised Teema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.errors import DomainError
from app.legacy_import.lovable_sync_models import SyncMode

#: The page's notice: where these facts come from and where they are changed.
SOURCE_OWNED_NOTICE = (
    "Need andmed pärinevad Lovable'i registrist. Muudatused tuleb teha algses registris."
)
#: The one-sentence `i` beside each source-owned field.
SOURCE_OWNED_TIP = "See väli pärineb Lovable'i registrist ja seda saab muuta ainult seal."
#: Said where the current step would be set on a synchronised Teema whose
#: Lovable `järgmiseks` is blank: a blank there is still Lovable's answer.
NO_SOURCE_STEP_NOTE = (
    "Lovable'is pole järgmist tegevust märgitud; uue tegevuse saab määrata Lovable'is."
)

#: The `LovableSyncControl.mode` values in which Lovable owns its fields (E9,
#: owner decision 12.11): the night compares or writes. OFF and cutover lock
#: nothing (`Ownership.active`).
FOLLOWING_MODES: tuple[str, ...] = (SyncMode.COMPARE, SyncMode.APPLY)

#: Guard keys → what the refusal names (Estonian, the object of «ei saa muuta»).
_REFUSED: dict[str, str] = {
    "title": "Pealkirja ei saa Juristides muuta.",
    "owner": "Vastutajat ei saa Juristides muuta.",
    "stage": "Hetkeseisu ei saa Juristides muuta.",
    "next_action": "Praegust tegevust ei saa Juristides muuta.",
    "opinion_deadline": "Arvamuse tähtaega ei saa Juristides muuta.",
    "track": "Koja ettepaneku märget ei saa Juristides muuta.",
    "legal_act": "Õigusakti ei saa Juristides muuta.",
    "received": "Saabumise kuupäeva ei saa Juristides muuta.",
    "commencement": "Lovable'ist tulnud jõustumist ei saa Juristides muuta ega eemaldada.",
    "close": "Teemat ei saa Juristides lõpetada.",
    "reopen": "Teemat ei saa Juristides uuesti avada.",
}
GUARD_KEYS = tuple(_REFUSED)

#: Every `FIELD_POLICY` field Lovable is authoritative for → the guard key of
#: the Teema surface that would change it, or ``None`` where Juristid offers no
#: person any editor (the reason beside it). `tests/test_source_owned_fields.py`
#: holds this table and `FIELD_POLICY` together, so a field the sync starts to
#: own cannot be left editable by omission.
POLICY_SURFACES: dict[str, str | None] = {
    "title": "title",
    "owner": "owner",
    "next_action_text": "next_action",
    "next_action_date": "next_action",
    "stage": "stage",
    "continuation": "close",
    "opinion_deadline": "opinion_deadline",
    "koda_initiative": "track",
    "legal_act": "legal_act",
    "received": "received",
    "commencements": "commencement",
    # Only the synchronisation writes these: a source reference is appended,
    # never edited, and `LovableOpinionOutcome` has no person-facing writer.
    "onenote_link": None,
    # `Matter.former_owner_name`: a name only the sync writes (owner decision
    # 14.11.2026); no person-facing editor exists.
    "former_owner": None,
    "sent": None,
    "sent_date": None,
    "addressee": None,
}


class SourceOwnedRefusal(DomainError):
    """A person tried to change a value Lovable owns on a synchronised Teema."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(refusal_message(key))


def refusal_message(key: str) -> str:
    """What the person is told: what cannot be changed, and where it is changed."""
    return f"{_REFUSED[key]} {SOURCE_OWNED_NOTICE}"


#: Guard keys → the synchronisation's fields whose open technical hold frees them.
HOLD_FIELDS: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "owner": ("owner",),
    "stage": ("stage", "continuation"),
    "close": ("stage", "continuation"),
    "reopen": ("stage", "continuation"),
    "next_action": ("next_action_text", "next_action_date"),
    "opinion_deadline": ("opinion_deadline",),
    "track": ("koda_initiative",),
    "legal_act": ("legal_act",),
    "received": ("received",),
    "commencement": ("commencements",),
}
#: How a field under an open hold is named in the page's note.
HELD_LABELS: dict[str, str] = {
    "title": "pealkiri",
    "owner": "vastutaja",
    "stage": "hetkeseis",
    "close": "lõpetamine",
    "reopen": "uuesti avamine",
    "next_action": "praegune tegevus",
    "opinion_deadline": "arvamuse tähtaeg",
    "track": "Koja ettepanek",
    "legal_act": "õigusakt",
    "received": "saabumine",
    "commencement": "jõustumised",
}


@dataclass(frozen=True)
class Ownership:
    """What Lovable owns on one Teema now.

    ``active`` — the Teema is synchronised **and the synchronisation follows
    Lovable** (`LovableSyncControl.mode` is COMPARE or APPLY, not cut over):
    Lovable is the source in both, and an edit made in COMPARE would be put
    back on the first APPLY night (docs/open-decisions.md E9). OFF (the
    operator's pause, which unlocks every Teema) and cutover lock nothing.

    ``held`` — guard keys whose field the night holds with an open technical
    hold (`lovable_sync.TECHNICAL_HOLDS`) on its latest reading: it is never
    «restored over», so a person may change it here until the hold clears."""

    active: bool = False
    held: frozenset[str] = frozenset()

    def locks(self, key: str) -> bool:
        return self.active and key not in self.held

    @property
    def locked(self) -> dict[str, bool]:
        """Per guard key, for the templates (`source_locked.owner`)."""
        return {key: self.locks(key) for key in GUARD_KEYS}

    @property
    def held_note(self) -> str:
        """The one line that says why a held field is editable, or ``""``."""
        if not self.active or not self.held:
            return ""
        names = ", ".join(HELD_LABELS[key] for key in GUARD_KEYS if key in self.held)
        return f"Lovable'i väärtus on ootel, seega saab siin praegu muuta: {names}."


NOT_OWNED = Ownership()


def ownership(matter: Any) -> Ownership:
    """Whether Lovable owns this Teema's source fields now: a live Teema with a
    synchronisation state in a cohort that is not retired — exactly the Teemad
    the nightly run follows (`lovable_sync.population`, `is_authoritative`) —
    while the synchronisation is in COMPARE or APPLY. One query, and a second only for a
    Teema that is owned (its open holds)."""
    pk = getattr(matter, "pk", None)
    if pk is None or getattr(matter, "deleted_at", None) is not None:
        return NOT_OWNED
    from django.db.models import Exists

    from app.legacy_import.lovable_sync_models import (
        LovableSyncCohort,
        LovableSyncControl,
        LovableSyncDecision,
        LovableSyncState,
        SyncOutcome,
    )

    retired = LovableSyncCohort.objects.filter(retired_at__isnull=False).values("key")
    following = LovableSyncControl.objects.filter(pk=1, mode__in=FOLLOWING_MODES, cut_over=False)
    state = (
        LovableSyncState.objects.filter(Exists(following), matter_id=pk)
        .exclude(cohort__in=retired)
        .values("pk", "observed_run_id")
        .first()
    )
    if state is None:
        return NOT_OWNED
    held: frozenset[str] = frozenset()
    if state["observed_run_id"] is not None:
        from app.legacy_import.lovable_sync import TECHNICAL_HOLDS

        fields = set(
            LovableSyncDecision.objects.filter(
                state_id=state["pk"],
                run_id=state["observed_run_id"],
                outcome=SyncOutcome.REVIEW_REQUIRED,
                reason__in=list(TECHNICAL_HOLDS),
            ).values_list("field", flat=True)
        )
        held = frozenset(key for key, names in HOLD_FIELDS.items() if fields & set(names))
    return Ownership(active=True, held=held)


def is_source_owned(matter: Any) -> bool:
    """Whether Lovable owns this Teema's source fields now (`ownership`)."""
    return ownership(matter).active


def _is_person(actor: Any) -> bool:
    return getattr(actor, "pk", None) is not None


def refuse_source_owned_change(matter: Any, key: str, *, actor: Any) -> None:
    """Refuse a person's change of ``key`` on a Teema Lovable owns now.

    Called by each guarded service **after** its own «nothing changed» return,
    so only a real change is refused. A call with no person — the
    synchronisation, an importer, an operator's management command
    (`lovable_sync`, `lovable_pilot`, the register tools), each of which writes
    with no actor and its own provenance — is never refused here."""
    if key not in _REFUSED:
        raise ValueError(f"Unknown source-owned field {key!r}.")
    if not _is_person(actor):
        return
    if ownership(matter).locks(key):
        raise SourceOwnedRefusal(key)


def is_lovable_commencement(record: Any) -> bool:
    """Whether a commencement (`MatterEffectiveDate`, or a commencement-kind
    `MatterImportantDate`) came from Lovable: recorded by a Lovable import or
    written by the synchronisation — the rows the night mirrors
    (`lovable_sync._lovable_commencement_records`). A person's own date is
    neither."""
    from app.intelligence.models import MatterImportantDate
    from app.legacy_import.lovable_pilot import COHORTS, T_EFFECTIVE_DATE, T_IMPORTANT_DATE
    from app.legacy_import.lovable_sync import (
        COMMENCEMENT_IMPORTANT_KINDS,
        LOVABLE_COMMENCEMENT_MARK,
    )
    from app.legacy_import.models import EvidencePilotRecord

    if type(record) is MatterImportantDate and record.kind not in COMMENCEMENT_IMPORTANT_KINDS:
        return False
    if getattr(record, "source_text", "") == LOVABLE_COMMENCEMENT_MARK:
        return True
    return EvidencePilotRecord.objects.filter(
        matter_id=record.matter_id,
        pilot__in=COHORTS,
        record_kind__in=(T_EFFECTIVE_DATE, T_IMPORTANT_DATE),
        target_id=record.pk,
    ).exists()


def refuse_lovable_commencement_change(record: Any, *, actor: Any) -> None:
    """Refuse a person's edit, cancellation or removal of a Lovable commencement
    on a synchronised Teema; a person's own commencement stays theirs."""
    if not _is_person(actor):
        return
    if ownership(record.matter).locks("commencement") and is_lovable_commencement(record):
        raise SourceOwnedRefusal("commencement")


def lovable_commencement_ids(matter: Any, *, source_owned: bool | None = None) -> frozenset[str]:
    """The ids of this Teema's commencements that came from Lovable, as strings
    (`is_lovable_commencement`, in two reads) — empty unless Lovable owns the
    Teema. What the chronology draws without `Muuda` / `Kustuta` / `Tühista`."""
    if source_owned is None:
        source_owned = is_source_owned(matter)
    if not source_owned:
        return frozenset()
    from django.db.models import Q

    from app.intelligence.models import MatterEffectiveDate, MatterImportantDate
    from app.legacy_import.lovable_pilot import COHORTS, T_EFFECTIVE_DATE, T_IMPORTANT_DATE
    from app.legacy_import.lovable_sync import (
        COMMENCEMENT_IMPORTANT_KINDS,
        LOVABLE_COMMENCEMENT_MARK,
    )
    from app.legacy_import.models import EvidencePilotRecord

    imported = EvidencePilotRecord.objects.filter(
        matter_id=matter.pk,
        pilot__in=COHORTS,
        record_kind__in=(T_EFFECTIVE_DATE, T_IMPORTANT_DATE),
        target_id__isnull=False,
    ).values("target_id")
    lovable = Q(pk__in=imported) | Q(source_text=LOVABLE_COMMENCEMENT_MARK)
    effective = MatterEffectiveDate.objects.filter(lovable, matter_id=matter.pk)
    important = MatterImportantDate.objects.filter(
        lovable, matter_id=matter.pk, kind__in=COMMENCEMENT_IMPORTANT_KINDS
    )
    return frozenset(
        str(pk)
        for pk in [*effective.values_list("pk", flat=True), *important.values_list("pk", flat=True)]
    )
