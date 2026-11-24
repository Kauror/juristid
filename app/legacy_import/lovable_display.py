"""Juristid's own value (B) of a Lovable field, as a person reads it (HR-10).

The engine records B in its own shape (`lovable_sync.current_values`,
`conditional_values`): the owner as a user id, the stage as its vocabulary
key, the opinion outcome as a record ``{"outcome", "date", "addressee"}``.
Every page and tool that shows B — Haldus «Vaata erinevusi» and its
resolve page, the Haldus Lovable panel, MCP
``get_lovable_sync_matter_changes`` — shows the person's name, the stage's
label and the outcome's words instead.

Rendering only: the stored decision, every comparison and every rule keep the
raw value. Where there is nothing to look up, `juristid_text` returns ``None``
and the caller renders the value exactly as before — a missing value stays
missing («—»), an unknown one (an id or key Juristid no longer has) stays as
recorded. A missing owner's representation is owner decision D13 and the date
format D4: neither is decided here, so each surface keeps its own day format
(``day``) and its own «—».
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

#: The fields whose B is a user id.
OWNER_FIELDS = frozenset({"owner"})
#: The fields whose B is a stage key (`StageVocabulary.key`), or — under
#: source authority (docs/adr/0213 §10) — the Teema's lifecycle record
#: ``{"open", "stage", "disposition", "successor"}``.
STAGE_FIELDS = frozenset({"stage"})
#: The fields whose B is the Teema's opinion outcome record (one operation:
#: `lovable_sync._outcome_decisions`).
OUTCOME_FIELDS = frozenset({"sent", "sent_date", "addressee"})


@dataclass(frozen=True)
class JuristidNames:
    """What B's ids and keys read as, fetched once for a whole page."""

    owners: dict[str, str] = field(default_factory=dict)
    stages: dict[str, str] = field(default_factory=dict)


def _uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def juristid_names(pairs: Iterable[tuple[str, Any]]) -> JuristidNames:
    """The names behind every (field, B) in ``pairs``: at most one query for
    the owners and one for the stages, none when no row needs them."""
    owner_ids: set[str] = set()
    stage_keys: set[str] = set()
    for name, value in pairs:
        if name in OWNER_FIELDS and isinstance(value, str) and value:
            found = _uuid(value)
            if found:
                owner_ids.add(found)
        elif name in STAGE_FIELDS and isinstance(value, str) and value:
            stage_keys.add(value)
        elif name in STAGE_FIELDS and isinstance(value, dict):
            if isinstance(value.get("stage"), str) and value["stage"]:
                stage_keys.add(value["stage"])
    owners: dict[str, str] = {}
    stages: dict[str, str] = {}
    if owner_ids:
        from app.accounts.models import User

        owners = {
            str(pk): str(display_name)
            for pk, display_name in User.objects.filter(pk__in=owner_ids).values_list(
                "pk", "display_name"
            )
            if display_name
        }
    if stage_keys:
        from app.workflow.models import StageVocabulary

        stages = {
            str(key): str(label)
            for key, label in StageVocabulary.objects.filter(key__in=stage_keys).values_list(
                "key", "label_et"
            )
            if label
        }
    return JuristidNames(owners=owners, stages=stages)


def juristid_text(
    name: str, value: Any, names: JuristidNames, *, day: Callable[[str], str]
) -> str | None:
    """B of ``name`` in words, or ``None`` for the caller's own rendering.

    ``day`` renders an ISO day the way the calling surface already does."""
    if name in OWNER_FIELDS and isinstance(value, str) and value:
        found = _uuid(value)
        return names.owners.get(found) if found else None
    if name in STAGE_FIELDS and isinstance(value, str) and value:
        return names.stages.get(value)
    if name in STAGE_FIELDS and isinstance(value, dict) and "open" in value:
        return _lifecycle(value, names)
    if name in OUTCOME_FIELDS and isinstance(value, dict) and "outcome" in value:
        from app.legacy_import.lovable_sync_models import OpinionOutcome

        code = str(value.get("outcome") or "")
        parts = [str(OpinionOutcome(code).label) if code in OpinionOutcome.values else code]
        if value.get("date"):
            parts.append(day(str(value["date"])))
        if value.get("addressee"):
            parts.append(str(value["addressee"]))
        return " · ".join(p for p in parts if p) or None
    return None


def _lifecycle(value: dict[str, Any], names: JuristidNames) -> str | None:
    """A lifecycle record: the stage's label, and for a closed Teema why. A
    stage key Juristid no longer knows is shown as recorded. The successor
    stays its recorded id: its reference is another Teema's, which this reader
    may not be allowed to open."""
    from app.workflow.enums import Disposition

    parts: list[str] = []
    stage = value.get("stage")
    if isinstance(stage, str) and stage:
        parts.append(names.stages.get(stage, stage))
    if value.get("open") is False:
        code = str(value.get("disposition") or "")
        label = str(Disposition(code).label) if code in Disposition.values else code
        parts.append(f"suletud: {label}" if label else "suletud")
    successor = value.get("successor")
    if isinstance(successor, str) and successor:
        parts.append(f"jätkub: {successor}")
    return " · ".join(parts) or None
