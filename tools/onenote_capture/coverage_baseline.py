"""Read-only operational OneNote coverage, from the records production already holds.

For a database *before* migration ``legacy_import/0025_ls_d`` (no coverage
marks yet): the same counts ``onenote_coverage.coverage_summary()["operational"]``
derives from records, using only tables and functions released before it
(``onenote_capture.register_page_guids`` and ``_stored_onenote_link``, ADR 0205).
SELECTs only; prints one JSON line of counts — no titles, names or text.

    docker run --rm -i --network juristid-main-internal --env-file <juristid.env> \\
      --log-driver none --entrypoint python <serving image> manage.py shell \\
      < tools/onenote_capture/coverage_baseline.py
"""

import json
from collections import Counter

from django.db.models import Count, Q

from app.legacy_import.lovable_sync_models import LovableSyncState
from app.legacy_import.onenote_capture import (
    BATCH_SOURCE_SYSTEM,
    HOLD_SHARED_PAGE,
    REGISTER_COHORT,
    _stored_onenote_link,
    register_page_guids,
)
from app.legacy_import.source_pages import (
    LegacySourcePage,
    LegacySourceResourceImport,
    ResourceImportState,
    SourceSystem,
)


def baseline() -> dict:
    states = LovableSyncState.objects.filter(
        cohort=REGISTER_COHORT, matter__deleted_at__isnull=True
    ).select_related("matter")
    pages: dict[str, list[tuple[str, bool]]] = {}
    no_link = ambiguous = teemad = open_teemad = 0
    for state in states:
        teemad += 1
        open_teemad += int(state.matter.is_open)
        guids = register_page_guids(_stored_onenote_link(str(state.matter_id), state))
        if not guids:
            no_link += 1
        elif len(guids) > 1:
            ambiguous += 1
        else:
            pages.setdefault(next(iter(guids)), []).append(
                (str(state.matter_id), state.matter.is_open)
            )
    review = {
        HOLD_SHARED_PAGE,
        "MOVED_BLOCK_BYTES_CHANGED",
        "AMBIGUOUS_EARLIER_BLOCK",
        "RESEMBLES_EARLIER_FILE_UNPROVEN",
        "SAME_FILE_ON_ANOTHER_PAGE_OF_TEEMA",
        "BYTES_CHANGED_UNDER_SAME_BLOCK",
        "BLOCK_TIME_CHANGED_UNDER_SAME_BLOCK",
        "DOCUMENT_REMOVED_SINCE_IMPORT",
    }
    catalogued = {}
    for key, capture_id, system, links, held in (
        LegacySourcePage.objects.filter(source_system=SourceSystem.ONENOTE_DESKTOP)
        .annotate(
            live_links=Count(
                "matter_links",
                filter=Q(matter_links__matter__deleted_at__isnull=True),
                distinct=True,
            ),
            held=Count(
                "resources__imports",
                filter=Q(
                    resources__imports__state=ResourceImportState.SKIPPED,
                    resources__imports__error_code__in=sorted(review),
                    resources__imports__matter_source_page__matter__deleted_at__isnull=True,
                ),
                distinct=True,
            ),
        )
        .values_list("page_key", "capture_id", "import_batch__source_system", "live_links", "held")
    ):
        if key.lower().startswith("p-"):
            catalogued[key[2:].strip("{}").upper()] = (capture_id, system, links, held)
    operational = set(pages)
    captured = {g for g in operational if g in catalogued}
    states_count: Counter[str] = Counter()
    for guid in operational:
        row = catalogued.get(guid)
        if row is None:
            states_count["NOT_INSPECTED"] += 1
        elif row[2] > 1 or row[3]:
            states_count["AWAITING_ASSOCIATION"] += 1
        else:
            states_count["IMPORTED"] += 1
    matter_ids = [m for v in pages.values() for m, _open in v]
    imports = Counter(
        LegacySourceResourceImport.objects.filter(
            matter_source_page__matter_id__in=matter_ids
        ).values_list("state", "error_code")
    )
    return {
        "archive_complete": False,
        "teemad": teemad,
        "open_teemad": open_teemad,
        "teemad_with_exact_page": len(matter_ids),
        "teemad_without_page_link": no_link,
        "teemad_with_ambiguous_link": ambiguous,
        "teemad_with_captured_page": sum(len(pages[g]) for g in captured),
        "teemad_without_capture": len(matter_ids) - sum(len(pages[g]) for g in captured),
        "open_teemad_without_capture": sum(
            1 for g in operational - captured for _m, is_open in pages[g] if is_open
        ),
        "pages": len(operational),
        "shared_pages": sum(1 for v in pages.values() if len(v) > 1),
        "pages_captured": len(captured),
        "pages_captured_by_a_capture_batch": sum(
            1 for g in captured if catalogued[g][1] == BATCH_SOURCE_SYSTEM
        ),
        "pages_not_captured": len(operational - captured),
        "captured_by": dict(Counter(catalogued[g][0] for g in captured)),
        "states": dict(states_count),
        "attachments_imported": imports.get((ResourceImportState.IMPORTED, ""), 0),
        "attachments_held": {
            code: n for (state, code), n in sorted(imports.items()) if state == "SKIPPED"
        },
        "attachments_failed": sum(n for (state, _c), n in imports.items() if state == "FAILED"),
        "other_catalogued_pages": len(set(catalogued) - operational),
    }


print(json.dumps(baseline(), sort_keys=True))
