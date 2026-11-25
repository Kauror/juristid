"""Park and restore the parliamentary `Oluline tähtaeg` rows around a rollback to r92.

    manage.py riigikogu_rollback_guard --check     # exit 1 while any is unparked
    manage.py riigikogu_rollback_guard --park      # before r92 runs against the database
    manage.py riigikogu_rollback_guard --restore   # after r93 (or later) runs again

**Why.** r93 adds three parliamentary kinds of `MatterImportantDate`
(`RK_OPINION_REQUEST`, `RK_AMENDMENTS_DEADLINE`, `RK_COMMITTEE_SITTING`) and puts them
in `INFORMATIONAL_IMPORTANT_DATE_KINDS`. r92's code does not know them, so to r92 an
accepted parliamentary date is an ordinary milestone: a `Järgmiseks` fallback, late
work once its day passes, a workload row and a deadline notification
(C:/CC/_worklogs/release-93/ROLLBACK-AFTER-ACTIVATION.md has the r92 lines).

**How.** Parking takes each such row off the active file the one way r92 honours on
every one of those reads: ``removed_at`` (`RemovableRecord`, docs/adr/0102), which
r92's ``visible_to`` and ``live()`` both drop. The stamp is the fixed `PARKED_AT`, not
the moment of parking, so a parked row is recognisable without any other storage and
never confused with a row a lawyer removed (whose ``removed_at`` is a real moment and
whose ``removed_by`` is set). Restoring clears exactly the rows carrying that stamp.

Nothing is deleted, no schema changes, no `ChangeEvent` is written (a removal event
would read in `Teema käik` as a lawyer's act). Each run that changes rows writes one
`SecurityAuditEvent` (``IMPORT_RUN``, system provenance) listing every row id. The
writes use ``QuerySet.update``, so ``updated_at`` is untouched and a restored row is
byte-for-byte the row that was parked.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

#: The ``removed_at`` a parked row carries. Fixed and impossible for a real removal
#: (the application did not exist in 2000), so `restore` touches nothing else.
PARKED_AT = dt.datetime(2000, 1, 1, tzinfo=dt.UTC)

ACTION_PARK = "RIIGIKOGU_ROLLBACK_GUARD_PARK"
ACTION_RESTORE = "RIIGIKOGU_ROLLBACK_GUARD_RESTORE"


def _parliamentary() -> Any:
    from app.intelligence.enums import PARLIAMENTARY_IMPORTANT_DATE_KINDS
    from app.intelligence.models import MatterImportantDate

    # The plain manager: a reader-blind operator read, which must see every row.
    return MatterImportantDate.objects.filter(kind__in=PARLIAMENTARY_IMPORTANT_DATE_KINDS)


@dataclass(frozen=True)
class State:
    #: On the active file: r92 would read these as milestones.
    unparked: int
    parked: int
    #: Taken off by a person; r92 ignores them already and the guard never touches them.
    removed_by_person: int

    @property
    def safe_for_r92(self) -> bool:
        return self.unparked == 0


def check() -> State:
    rows = _parliamentary()
    return State(
        unparked=rows.filter(removed_at__isnull=True).count(),
        parked=rows.filter(removed_at=PARKED_AT).count(),
        removed_by_person=rows.filter(removed_at__isnull=False)
        .exclude(removed_at=PARKED_AT)
        .count(),
    )


def _audit(action: str, ids: list[str]) -> None:
    from app.audit.enums import SecurityEventType
    from app.audit.services import record_security_event

    record_security_event(
        event_type=SecurityEventType.IMPORT_RUN,
        detail={
            "action": action,
            "model": "intelligence.MatterImportantDate",
            "count": len(ids),
            "ids": ids,
            "parked_at": PARKED_AT.isoformat(),
        },
    )


@transaction.atomic
def park() -> list[str]:
    """Park every parliamentary date still on the active file; the ids parked."""
    rows = _parliamentary().select_for_update().filter(removed_at__isnull=True)
    # A row with no removal moment but a remover is not one this guard can restore
    # exactly (restore clears both); none is expected, so it stops the run.
    if rows.filter(removed_by__isnull=False).exists():
        raise CommandError("Mõnel Riigikogu tähtajal on eemaldaja, kuid eemaldamise aega pole.")
    ids = sorted(str(pk) for pk in rows.values_list("pk", flat=True))
    if ids:
        _parliamentary().filter(pk__in=ids).update(removed_at=PARKED_AT, removed_by=None)
        _audit(ACTION_PARK, ids)
    return ids


@transaction.atomic
def restore() -> list[str]:
    """Return every parked row to the active file; the ids restored."""
    rows = (
        _parliamentary().select_for_update().filter(removed_at=PARKED_AT, removed_by__isnull=True)
    )
    ids = sorted(str(pk) for pk in rows.values_list("pk", flat=True))
    if ids:
        _parliamentary().filter(pk__in=ids).update(removed_at=None, removed_by=None)
        _audit(ACTION_RESTORE, ids)
    return ids


class Command(BaseCommand):
    help = (
        "Riigikogu tähtajad r92-le tagasimineku eel parklasse (--park) ja pärast tagasi "
        "(--restore); --check ütleb, kas r92 võib andmebaasiga töötada."
    )

    def add_arguments(self, parser: Any) -> None:
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--check", action="store_true")
        mode.add_argument("--park", action="store_true")
        mode.add_argument("--restore", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["park"]:
            ids = park()
            self.stdout.write(f"park: {len(ids)} Riigikogu tähtaega parklasse")
        elif options["restore"]:
            ids = restore()
            self.stdout.write(f"restore: {len(ids)} Riigikogu tähtaega tagasi")
        state = check()
        self.stdout.write(
            f"check: aktiivsel failil {state.unparked}, parklas {state.parked}, "
            f"inimese eemaldatud {state.removed_by_person}"
        )
        if options["check"] and not state.safe_for_r92:
            raise CommandError(
                "r92-le tagasiminek pole ohutu: aktiivsel failil on Riigikogu tähtaegu "
                "(käivita --park)."
            )
