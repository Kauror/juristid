"""Plant two synthetic nights of Lovable history for `e2e/test_lovable_review_page.py`.

Run inside the server's own Django (`manage.py shell`), the way the other
planting helpers in `e2e/conftest.py` are. Every value is invented; the three
Teemad are the first three the e2e seed numbered. Idempotent: a second call
finds its runs and plants nothing.

The shapes are the synchronisation's own (docs/adr/0192): a verified snapshot
per night, a state per Teema and the night's decisions. The page compares the
two snapshots and reads the decisions; nothing here runs the synchronisation.
"""

import datetime as dt
import uuid

from django.utils import timezone

from app.legacy_import import lovable_source
from app.legacy_import.integrity import canonical_sha256
from app.legacy_import.lovable_sync import MAPPING_VERSION, TALLINN
from app.legacy_import.lovable_sync_models import (
    LovableSyncDecision,
    LovableSyncRun,
    LovableSyncSnapshot,
    LovableSyncState,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)
from app.matters.models import Matter

if not LovableSyncRun.objects.filter(run_key__startswith="e2e-").exists():
    matters = list(
        Matter.objects.filter(reference_year__isnull=False).order_by(
            "reference_year", "reference_number"
        )[:3]
    )
    today = timezone.localdate(timezone=TALLINN)
    nights = [
        dt.datetime.combine(today - dt.timedelta(days=d), dt.time(0, 15), tzinfo=TALLINN)
        for d in (1, 0)
    ]

    def row(matter, **values):
        return {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"e2e-lovable/{matter.pk}")),
            "nr": matter.display_reference,
            "aasta": matter.reference_year,
            "teema": matter.title,
            "vastutaja": "Mari",
            "uus_vastutaja": "",
            "hetkeseis": "Ministeeriumis",
            "jargmiseks": "Ootan eelnõu",
            "tegevuse_tahtaeg": "2026-11-12",
            "arvamuse_tahtaeg": None,
            "valja": "",
            "valja_kuupaev": None,
            "kellele": "",
            "koja_ettepanek": False,
            "oigusakt": "",
            "sisse": None,
            "onenote_link": "",
            "operatiivne_ulatus": True,
            **values,
        }

    first = {m.pk: row(m) for m in matters}
    second = {
        matters[0].pk: row(matters[0], teema=matters[0].title + " (täpsustatud)", vastutaja="Jaan"),
        matters[1].pk: row(matters[1], hetkeseis="Riigikogus"),
        matters[2].pk: row(matters[2], tegevuse_tahtaeg="2026-11-20"),
    }

    def document(at, rows):
        cohort = {
            r["id"]: {"row": r, "commencements": [], "responsible_changes": [], "stage_links": []}
            for r in rows.values()
        }
        body = {
            "format": lovable_source.SNAPSHOT_FORMAT,
            "format_version": lovable_source.SNAPSHOT_FORMAT_VERSION,
            "project": lovable_source.LOVABLE_PROJECT,
            "taken_at": at.isoformat(),
            "mapping_version": MAPPING_VERSION,
            "attempts": 1,
            "index": [],
            "cohort": cohort,
        }
        body["sha256"] = canonical_sha256(body)
        return body

    runs = []
    for at, rows, mode in (
        (nights[0], first, SyncMode.COMPARE),
        (nights[1], second, SyncMode.APPLY),
    ):
        run = LovableSyncRun.objects.create(
            run_key=f"e2e-{at:%Y-%m-%d}",
            trigger=SyncTrigger.SCHEDULED,
            mode=mode,
            status=SyncRunStatus.SUCCEEDED,
            started_at=at,
            finished_at=at + dt.timedelta(seconds=7),
            snapshot_taken_at=at,
            mapping_version=MAPPING_VERSION,
            verified=True,
        )
        doc = document(at, rows)
        LovableSyncSnapshot.objects.create(run=run, taken_at=at, sha256=doc["sha256"], document=doc)
        runs.append(run)

    states = {
        m.pk: LovableSyncState.objects.create(
            matter=m,
            source_id=first[m.pk]["id"],
            reference=m.display_reference,
            applied={},
            observed={},
        )
        for m in matters
    }
    night = runs[1]
    for matter, name, outcome, reason, source in (
        (matters[0], "title", SyncOutcome.APPLIED, "", second[matters[0].pk]["teema"]),
        (matters[0], "owner", SyncOutcome.REVIEW_REQUIRED, "UNRESOLVED_OWNER", "Jaan"),
        (matters[1], "stage", SyncOutcome.REVIEW_REQUIRED, "HELD_FIELD", "Riigikogus"),
        (matters[2], "next_action_date", SyncOutcome.CONFLICT, "", "2026-11-20"),
    ):
        LovableSyncDecision.objects.create(
            run=night,
            state=states[matter.pk],
            field=name,
            outcome=outcome,
            reason=reason,
            source=source,
        )
print("planted")
