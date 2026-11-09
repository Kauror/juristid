"""Integration seams of the live-sync round (2026-11-09, lead).

Each test pins one place where two agents' work meets: the chronology reads a
later OneNote capture's new file version (D → timeline), and the readiness
rules shown in Haldus agree with the engine's field policy (B → observability).
Invented data only.
"""

from __future__ import annotations

import pytest

from app.legacy_import import lovable_backfill, lovable_observability, lovable_reading, lovable_sync
from app.legacy_import.lovable_inventory import build_ledger
from app.legacy_import.onenote_coverage_models import OneNoteResourceRevision, RevisionState
from app.matters.timeline import imported_document_versions
from tests.test_ls_a_backfill import pilot_on, world  # noqa: F401 — fixtures
from tests.test_ls_d_onenote_incremental import (  # noqa: F401 — fixtures
    applied,
    changed_b2,
    enabled,
    later,
    teema,
)
from tests.test_onenote_capture_changes import run


@pytest.mark.django_db
def test_a_later_captures_new_version_is_dated_by_its_onenote_change_never_by_the_import(
    applied,  # noqa: F811 — the imported fixture
):
    run(later(applied, changed_b2()))
    revision = OneNoteResourceRevision.objects.get(resource__resource_key="{B2}")
    assert revision.state == RevisionState.VERSIONED
    shown = imported_document_versions(applied["teema"])
    # Version 1 (the first capture) and version 2 (the later one) are both
    # imported versions, so neither reads as an actorless «lisas dokumendi».
    assert revision.document_version_id in shown
    v2 = shown[revision.document_version_id]
    if revision.block_modified_at is None:
        assert v2.added_at is None and v2.group == "undated"
    else:
        assert v2.added_at == revision.block_modified_at
        assert v2.file is not None and v2.file.added_display.startswith("OneNote'is muudetud ")
    # Never the day the import stored it.
    assert v2.anchor != revision.document_version.created_at or v2.added_at is None


def test_the_readiness_rules_agree_with_the_engines_field_policy():
    automatic = {"AUTO": "EXACT", "CONDITIONAL": "CONDITIONAL"}
    for name, rule in lovable_observability.FIELD_RULES.items():
        policy = lovable_sync.FIELD_POLICY[name].policy
        if policy in automatic:
            assert rule["mapping"] == automatic[policy], name
            assert rule["service"], name
            assert not rule["blocking"], name
        else:
            assert rule["mapping"] in {"NONE", "APPROXIMATE"}, name
            assert rule["blocking"], name


# -- A's backfill with B's real machine reader ------------------------------------------


@pytest.mark.django_db
def test_the_backfill_package_built_by_the_real_reader_validates_and_dry_runs_closed(
    world,  # noqa: F811 — the imported fixture
    pilot_on,  # noqa: F811 — the imported fixture
):
    ledger = build_ledger(world["document"])
    selection = lovable_backfill.build_package(
        world["document"],
        ledger,
        reader=lovable_reading.read_topic,
        reader_version=lovable_reading.READER_VERSION,
    )
    assert lovable_backfill.validate(selection.package) == []
    topics = selection.package["topics"]
    assert topics, "the absent historical topics reach the package"
    for topic in topics:
        assert topic["action"] == "CREATE"
        assert lovable_backfill.history_problems(topic) == []
    report = lovable_backfill.dry_run(selection.package)
    assert report.verify.ok, report.verify.discrepancies
    assert report.written["MATTER"] == len(topics)
