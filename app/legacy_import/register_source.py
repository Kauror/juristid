"""Which source the operational register follows, and what that refuses.

Under docs/adr/0203 Lovable is the sole source of truth for the operational
register: `lovable_pilot replace` retired every earlier Teema and created
Lovable's whole register as the ``lovable-register`` cohort, which the nightly
comparison follows. From then on the Excel-era write commands — the register
import, its refresh, cutover, promotion, owner backfill, JÄRGMISEKS enrichment,
the 2026 Excel pilot and the historical corpus import — have nothing legitimate
left to do here. Run anyway, they would create workbook Teemad beside Lovable's,
attach workbook source references to Lovable's Teemad or overwrite the fields
Lovable is the source of. Their flags and digests prove a person approved a
workbook; they cannot know that the register no longer comes from one.

This is the one rule they share. Their read-only modes (dry runs, plans,
inspection, status) stay open: they interpret historical provenance and audits
use them.
"""

from __future__ import annotations

from django.core.management.base import CommandError

from app.legacy_import.lovable_pilot import REGISTER_COHORT
from app.legacy_import.lovable_sync_models import LovableSyncCohort, LovableSyncState

#: The refusal every guarded command gives, with the command's name filled in.
EXCEL_ERA_WRITE_REFUSAL = (
    "The operational register's source is Lovable (docs/adr/0203): this database holds the "
    "{cohort} cohort. `{command}` is an Excel-era write and is refused; nothing was written."
)


def excel_era_write_refusal(command: str) -> str:
    """The exact sentence `refuse_excel_era_write` refuses ``command`` with."""
    return EXCEL_ERA_WRITE_REFUSAL.format(cohort=REGISTER_COHORT, command=command)


def lovable_register_is_source() -> bool:
    """Whether Lovable's register replacement (docs/adr/0203) governs this database.

    True when the register cohort is enrolled and not retired, or when any
    Teema's synchronisation state names it — a state without its cohort row is
    still a Lovable-sourced Teema, and the guard fails closed on it.
    """
    return (
        LovableSyncCohort.objects.filter(key=REGISTER_COHORT, retired_at__isnull=True).exists()
        or LovableSyncState.objects.filter(cohort=REGISTER_COHORT).exists()
    )


def refuse_excel_era_write(command: str) -> None:
    """Refuse an Excel-era write before it touches anything, once Lovable is the source."""
    if lovable_register_is_source():
        raise CommandError(excel_era_write_refusal(command))
