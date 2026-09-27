"""Does the stored `CurrentRegisterState` equal what today's code would derive?

`CurrentRegisterState` is derived: `rebuild_current_state` writes it from the
immutable `MatterSourceReference` rows of one approved snapshot, through
`projected_state_rows`. Its only freshness key was the snapshot digest, and that
does not move when a release changes how the register is *read* — a parse rule,
a status mapping, who `KnownPeople` can resolve. So a release could change what
the table ought to say, and the Arvamusi koostamisel card, the `?arvamus=`
filter and the Hetkeseis puudub items would keep saying the old thing until
somebody reran the cutover, with nothing reporting that the rerun was owed
(ENG-144).

This answers it by asking the same function the writer uses. The projection is
built in memory from the snapshot the stored rows name, and compared column by
column with what is stored, keyed by Matter:

* **missing** — the projection has a row the table does not;
* **extra** — the table has a row the projection does not;
* **changed** — both have one and a compared column differs.

Read-only: nothing here saves, deletes or rebuilds. The repair is the operator's
decision and the existing command's (`final_register_cutover --apply`, or the
current-register refresh), because rebuilding moves what the product shows.

What is reported is a Matter's register reference and the *names* of the columns
that differ — never their values. `next_action_text`, `owner_raw` and
`addressee_raw` are the register's own words, and this output goes to terminals
and cron mail.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.legacy_import.current_state import CurrentRegisterState
from app.legacy_import.final_cutover import build_cutover_plan, projected_state_rows

MISSING = "missing"
EXTRA = "extra"
CHANGED = "changed"

#: Not compared: the row's own identity and bookkeeping, the key, and
#: `observed_at`, which is when the row was derived and differs on every run.
NOT_COMPARED = frozenset({"id", "created_at", "updated_at", "matter_id", "observed_at"})

#: Every column a projection sets, read from the model so a column added to the
#: table is compared without anybody remembering to add it here.
COMPARED_FIELDS: tuple[str, ...] = tuple(
    column.attname
    for column in CurrentRegisterState._meta.concrete_fields
    if column.attname not in NOT_COMPARED
)


class SnapshotNotSettled(Exception):
    """The stored rows do not name exactly one snapshot to verify against."""


@dataclass(frozen=True)
class StateDifference:
    kind: str
    matter_id: Any
    reference: str
    fields: tuple[str, ...] = ()


@dataclass
class StateVerification:
    snapshot_sha256: str
    stored: int = 0
    projected: int = 0
    differences: list[StateDifference] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.differences

    def counts(self) -> Counter[str]:
        return Counter(difference.kind for difference in self.differences)


def stored_snapshot() -> str:
    """The one digest the stored rows carry, or `""` when there are none."""
    present = set(
        CurrentRegisterState.objects.order_by()
        .values_list("source_snapshot_sha256", flat=True)
        .distinct()
    )
    if len(present) > 1:
        raise SnapshotNotSettled(
            f"Derived register state carries {len(present)} snapshot digests; "
            "name the approved one with --snapshot."
        )
    return next(iter(present), "")


def verify_current_state(*, snapshot_sha256: str | None = None) -> StateVerification:
    """Compare the stored rows with a fresh projection of one snapshot. Reads only."""
    digest = (snapshot_sha256 if snapshot_sha256 is not None else stored_snapshot()).strip()
    digest = digest.lower()
    verification = StateVerification(snapshot_sha256=digest)

    stored = {
        row.matter_id: row
        for row in CurrentRegisterState.objects.select_related("matter").order_by("matter_id")
    }
    rows = projected_state_rows(build_cutover_plan(snapshot_sha256=digest)) if digest else []
    projected = {row.matter_id: row for row in rows}
    verification.stored = len(stored)
    verification.projected = len(projected)

    for matter_id in sorted(set(stored) | set(projected), key=str):
        have = stored.get(matter_id)
        want = projected.get(matter_id)
        matter = (want or have).matter  # type: ignore[union-attr]
        reference = matter.display_reference or str(matter_id)
        if have is None:
            verification.differences.append(StateDifference(MISSING, matter_id, reference))
        elif want is None:
            verification.differences.append(StateDifference(EXTRA, matter_id, reference))
        else:
            changed = tuple(
                name for name in COMPARED_FIELDS if getattr(have, name) != getattr(want, name)
            )
            if changed:
                verification.differences.append(
                    StateDifference(CHANGED, matter_id, reference, changed)
                )
    return verification
