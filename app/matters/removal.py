"""`Kustuta` on a user-created block of `Teema käik`.

A lawyer files a `Märge` on the wrong Matter, records the same `Kaasamine`
twice, or types an `Oluline tähtaeg` into a file it has nothing to do with.
Until this existed the only repair was `Muuda` — rewriting the mistake into
something else — which leaves a row nobody made, dated a day nobody chose, and
attributed to whoever happened to be fixing it (OWNER-04, docs/adr/0102).

**Three meanings, kept apart.** Confusing any two of them is how a file starts
lying, so each keeps its own column, its own service and its own audit event:

* **cancelled** (`FactStatus.CANCELLED`, `WebsiteOverviewStatus.CANCELLED`) —
  the plan changed. The expectation was real and somebody called it off, so the
  row stays in the chronology saying `Tühistatud`.
* **withdrawn** (`SubmissionStatus.WITHDRAWN`) — the letter went out and was
  taken back. The sending is still a fact about the world.
* **removed**, here — this record should never have been on this file. There is
  nothing to say in the chronology, because nothing happened.

**Not a delete, and not a workflow engine.** `removed_at` and `removed_by` on
the record (`app.core.models.RemovableRecord`), one audit event per record kind,
and nothing else. No row is destroyed, no evidence byte is touched, no
append-only table is written to twice. :data:`REMOVABLE` is a table of eight
explicit facts rather than a registry a later record kind joins by declaring an
attribute: adding a ninth is a deliberate line in this file, reviewed with the
model it names.

**Why the removal filter lives in `visible_to` and not here.** Every removable
model already routes its business reads through one chokepoint, because that is
where authorization is applied before any grouping, counting or ranking. A
removed row must be absent from exactly the same places for exactly the same
reason, and a filter written at a call site is a filter the next call site
forgets. So this module *writes* the columns and the readers need no changes at
all — the chronology, the rail, `Minu asjad`, the register, the statistics and
the search projection all pass through `visible_to` already.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from django.db import models, transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.errors import DomainError
from app.matters.locks import lock_open_matter_for_business_write
from app.matters.models import (
    Entry,
    MatterEngagement,
    MatterExternalPosition,
    MatterProceduralDevelopment,
    MatterWebsiteOverview,
)

#: What a caller is told when the record it named is not on the Matter it named
#: — a crafted identifier, or a row that a colleague removed and this request
#: is the second half of a double submit.
RECORD_NOT_ON_MATTER = "Seda kirjet ei ole sellel teemal."

#: What a stale second tab is told. The wording is the product's own sentence
#: for a conflict, said about a record rather than about the Teema.
REMOVAL_CONFLICT = "Kirjet on vahepeal mujal muudetud. Värskenda lehte ja proovi uuesti."


class RecordRemovalConflict(DomainError):
    """The record moved between the page being drawn and the button being pressed.

    Carries the committed row, so a caller can redraw the record as it now is
    rather than as the stale page remembered it — the shape
    `ProceduralDevelopmentConflict` established and the reason a conflict is an
    exception with a payload instead of a boolean.
    """

    def __init__(self, current: Any) -> None:
        super().__init__(REMOVAL_CONFLICT)
        self.current = current


@dataclass(frozen=True)
class RemovableKind:
    """One record family a lawyer may take off the active file.

    ``key`` is what the URL carries, and it is the Estonian word the launcher
    and the row already use rather than a model name: the address a lawyer
    copies out of the browser should read like the product, and an English
    class name in it is a leak of the schema into the UI.
    """

    key: str
    model: type[models.Model]
    event_type: str
    #: What kind of record this is, nominative. Read out after the button's own
    #: word, so a screen reader hears «Kustuta — oluline tähtaeg» rather than
    #: twelve identical buttons.
    label: str
    #: The same word in the genitive, which is the case the confirmation
    #: sentence needs: «Eemaldan selle *olulise tähtaja* teema käigust». Stored
    #: rather than derived, because Estonian declension is not a string
    #: operation and a template that guessed would be wrong on most of these.
    genitive: str
    #: Whether taking this record off the file also takes it out of the search
    #: corpus. Four kinds have a row of their own there; the structured facts
    #: and the overview have never been projected at all, so there is nothing
    #: to withdraw and claiming otherwise in a comment would be worse than
    #: saying nothing.
    reindexes: bool = False

    @property
    def rows(self) -> Any:
        """This family's own reading manager.

        ``_default_manager`` rather than ``objects``: the attribute is what
        Django itself uses to reach a model's manager generically, and it is
        the one spelling that type-checks through a `type[Model]` the table
        stores rather than a concrete class per row. Every model named here
        declares `objects` from its own `QuerySet`, so this *is* that manager —
        `visible_to` included, which is the only entry point callers use.
        """
        return self.model._default_manager


def _fact(key: str, model_path: str, event_type: str, label: str, genitive: str) -> RemovableKind:
    """One of the three `app.intelligence` facts, imported at call time.

    `app.matters` may not import `app.intelligence` at module scope — the
    dependency runs the other way — so the model is resolved through the app
    registry when the table is first read.
    """
    from django.apps import apps

    app_label, model_name = model_path.split(".")
    return RemovableKind(
        key=key,
        model=apps.get_model(app_label, model_name),
        event_type=event_type,
        label=label,
        genitive=genitive,
    )


def _table() -> tuple[RemovableKind, ...]:
    return (
        RemovableKind(
            key="sissekanne",
            model=Entry,
            event_type=ChangeEventType.ENTRY_REMOVED,
            label="sissekanne",
            genitive="sissekande",
            reindexes=True,
        ),
        RemovableKind(
            key="marge",
            model=MatterProceduralDevelopment,
            event_type=ChangeEventType.PROCEDURAL_DEVELOPMENT_REMOVED,
            label="märge",
            genitive="märke",
            reindexes=True,
        ),
        RemovableKind(
            key="kaasamine",
            model=MatterEngagement,
            event_type=ChangeEventType.ENGAGEMENT_REMOVED,
            label="kaasamine",
            genitive="kaasamise",
            reindexes=True,
        ),
        RemovableKind(
            key="seisukoht",
            model=MatterExternalPosition,
            event_type=ChangeEventType.EXTERNAL_POSITION_REMOVED,
            label="seisukoht",
            genitive="seisukoha",
            reindexes=True,
        ),
        RemovableKind(
            key="ulevaade",
            model=MatterWebsiteOverview,
            event_type=ChangeEventType.WEBSITE_OVERVIEW_REMOVED,
            label="ülevaade / uudis",
            genitive="ülevaate / uudise",
        ),
        _fact(
            "tahtaeg",
            "intelligence.MatterImportantDate",
            ChangeEventType.IMPORTANT_DATE_REMOVED,
            "oluline tähtaeg",
            "olulise tähtaja",
        ),
        _fact(
            "joustumine",
            "intelligence.MatterEffectiveDate",
            ChangeEventType.EFFECTIVE_DATE_REMOVED,
            "jõustumine",
            "jõustumise",
        ),
        _fact(
            "toovoit",
            "intelligence.MatterWorkVictory",
            ChangeEventType.WORK_VICTORY_REMOVED,
            "töövõidu kirje",
            "töövõidu kirje",
        ),
    )


_CACHE: dict[str, RemovableKind] | None = None


def removable_kinds() -> dict[str, RemovableKind]:
    """The table, keyed by what a URL carries. Built once, on first read."""
    global _CACHE
    if _CACHE is None:
        _CACHE = {kind.key: kind for kind in _table()}
    return _CACHE


def kind_for(key: str) -> RemovableKind:
    try:
        return removable_kinds()[key]
    except KeyError:
        raise DomainError(RECORD_NOT_ON_MATTER) from None


def kind_of(record: Any) -> RemovableKind | None:
    """Which family a record belongs to, or `None` if it is not removable.

    Matched on the exact class rather than on `isinstance`, because the point
    of the table is that a family is removable when somebody wrote it down —
    a future subclass inheriting the capability silently is the generic engine
    this module refuses to be.
    """
    for kind in removable_kinds().values():
        if type(record) is kind.model:
            return kind
    return None


def record_revision(record: Any) -> str:
    """Which version of a row the page that drew the button was holding.

    ``updated_at``, the token every other optimistic-concurrency seam in this
    product uses — set by `auto_now` on every write, stored to the microsecond
    so two saves cannot share one, and costing no migration.
    """
    return record.updated_at.isoformat()


@transaction.atomic
def remove_matter_record(
    *,
    matter_id: Any,
    kind_key: str,
    record_id: Any,
    actor: Any,
    expected_revision: str | None = None,
) -> Any:
    """Take one user-created record off the active file.

    **Refused on a closed Matter**, through the same lock every other
    interactive business write takes. Removing a row is editing the file's
    account of what happened, which is exactly what closure stops; reopening is
    the way out and it leaves somebody's name on both decisions (docs/adr/0076
    §2).

    **The row is located through the locked Matter**, so a crafted identifier
    belonging to another Teema is a refusal rather than a cross-file write, and
    the caller's authorization — checked by the view before this is reached —
    cannot be widened by the identifier it passes.

    **Optimistic concurrency, compared under the row lock.** The token is read
    from the *locked* row, so what it is compared against is the committed
    version rather than whatever the caller's instance remembers.

    **Removing a removed record is not an error.** A double submit, a second
    tab, a retried request: the second call finds the row already gone and
    returns it unchanged. The alternative — a refusal — would print an alarming
    sentence about a state that is exactly what the person asked for.
    """
    kind = kind_for(kind_key)
    locked_matter = lock_open_matter_for_business_write(matter_id)
    try:
        current = kind.rows.select_for_update(no_key=True).get(pk=record_id, matter=locked_matter)
    except (ObjectDoesNotExist, ValueError, TypeError):
        raise DomainError(RECORD_NOT_ON_MATTER) from None

    if current.removed_at is not None:
        return current
    if expected_revision is not None and record_revision(current) != expected_revision:
        raise RecordRemovalConflict(current)

    current.removed_at = timezone.now()
    current.removed_by = actor
    current.save(update_fields=["removed_at", "removed_by", "updated_at"])

    record_change_event(
        event_type=kind.event_type,
        matter=locked_matter,
        actor=actor,
        obj=current,
        summary=removal_summary(kind, current),
    )
    if kind.reindexes:
        _reproject(current)
    return current


def removal_summary(kind: RemovableKind, record: Any) -> str:
    """What the audit line says went, in the words the page used.

    The record's own headline where it has one, because «Märge eemaldatud» with
    no object is a line a reader cannot match to anything they remember. Where
    a family has no single headline field the kind's label carries the sentence
    on its own, which is truthful and short rather than a composed guess.
    """
    for field in ("title", "description", "summary"):
        value = (getattr(record, field, "") or "").strip()
        if value:
            return value[:200]
    return kind.label


def _reproject(record: Any) -> None:
    """Withdraw the record from the search corpus, on the write that removed it.

    Synchronous and per-write, the rule `refresh_engagement` established: «found
    only after an operator runs a command» is the same defect as «not indexed»
    with a longer fuse. The refresh functions read the row, see `removed_at`
    set, delete the projection and insert nothing — so this needs no branch of
    its own and a restore, if this product ever grows one, would converge
    through the same call.
    """
    from app.search import indexing

    if isinstance(record, Entry):
        indexing.refresh_entry(record)
    elif isinstance(record, MatterProceduralDevelopment):
        indexing.refresh_development(record)
    elif isinstance(record, MatterEngagement):
        indexing.refresh_engagement(record)
    elif isinstance(record, MatterExternalPosition):
        indexing.refresh_external_position(record)
