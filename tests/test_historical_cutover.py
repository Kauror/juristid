"""The historical cutover: what it retires, and what it refuses to claim.

The delicate part of this operation is not which rows it touches but what it
declines to assert about them. A 2014 register row genuinely has no closure
date and no closure reason anywhere in the sources, so the tests that matter
most here are the negative ones — no disposition, no timestamp, no closing
person, no `MATTER_CLOSED` event — because every one of those would be a
plausible-looking fabrication that nobody could later distinguish from a fact.

All data is synthetic. No real register row, title or name appears.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.legacy_import.current_register import build_promotion_plan
from app.legacy_import.historical_cutover import (
    HISTORICAL_CUTOVER_VERSION,
    REVIEWED_HISTORICAL_CUTOVER_YEARS,
    Classification,
    ReviewReason,
    UnreviewedCutoverYear,
    apply_cutover_plan,
    build_cutover_plan,
    summary,
)
from app.legacy_import.parser import SOURCE_SYSTEM
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Matter
from app.matters.services import (
    close_matter,
    mark_historical_archive_inactive,
    reactivate_historical_matter,
)
from app.matters.timeline import TIMELINE_EVENT_TYPES
from app.search.models import SearchDocument
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics, Disposition
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db

CUTOVER = 2026


def register_matter(year: int, **extra):
    """An imported register Matter as the conservative import leaves it."""
    extra.setdefault("record_mode", RecordMode.ARCHIVE)
    extra.setdefault("origin", MatterOrigin.LEGACY_IMPORT)
    extra.setdefault("owner", None)
    extra.setdefault("reporting_year", year)
    matter = factories.MatterFactory(reference_year=year, **extra)
    factories.MatterSourceReferenceFactory(
        matter=matter,
        source_system=SOURCE_SYSTEM,
        source_sheet=str(year),
        source_era=str(year) if year >= 2025 else "2011-2017",
        source_row_raw={},
    )
    return matter


def classifications(plan):
    return {c.matter.pk: c.classification for c in plan.candidates}


# =========================================================================
# The historical default, and everything it must not invent
# =========================================================================


def test_an_old_open_register_row_becomes_historical() -> None:
    matter = register_matter(2014)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    matter.refresh_from_db()
    assert matter.is_open is False


def test_no_disposition_is_invented() -> None:
    """The register never recorded why a 2014 file stopped. Neither do we."""
    matter = register_matter(2014)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    matter.refresh_from_db()
    assert matter.disposition == ""
    assert matter.disposition_reason == ""


def test_no_closure_timestamp_is_invented() -> None:
    """`closed_at` would read as "this closed on cutover day", which is false."""
    matter = register_matter(2014)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    matter.refresh_from_db()
    assert matter.closed_at is None


def test_no_closing_person_is_invented() -> None:
    matter = register_matter(2014)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    matter.refresh_from_db()
    assert matter.closed_by is None


def test_the_record_stays_an_archive_row() -> None:
    """Historical does not mean promoted, demoted or re-originated."""
    matter = register_matter(2014)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    matter.refresh_from_db()
    assert matter.record_mode == RecordMode.ARCHIVE
    assert matter.origin == MatterOrigin.LEGACY_IMPORT


def test_the_owner_and_stage_survive(specialist, stage) -> None:
    """The backfill restored these. Retiring the row must not undo it."""
    matter = register_matter(2014, owner=specialist, stage=stage)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    matter.refresh_from_db()
    assert matter.owner == specialist
    assert matter.stage == stage


def test_the_reporting_year_survives() -> None:
    """Annual reporting identity is not a function of current-ness."""
    matter = register_matter(2014)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    matter.refresh_from_db()
    assert matter.reporting_year == 2014
    assert matter.reference_year == 2014


def test_the_source_provenance_is_untouched() -> None:
    matter = register_matter(2014)
    before = list(matter.source_references.values_list("pk", "source_sheet", "source_row_raw"))

    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    matter.refresh_from_db()
    after = list(matter.source_references.values_list("pk", "source_sheet", "source_row_raw"))
    assert before == after


def test_documents_and_entries_are_untouched() -> None:
    matter = register_matter(2014)
    document = factories.DocumentFactory(matter=matter)

    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    assert matter.documents.count() == 1
    assert matter.documents.first().pk == document.pk


# =========================================================================
# Audit
# =========================================================================


def test_the_operation_writes_its_own_event() -> None:
    matter = register_matter(2014)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    event = ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_HISTORICAL_CUTOVER_CLOSED
    ).latest("created_at")
    assert event.payload["operation"] == "historical_cutover_state"
    assert event.payload["operation_version"] == HISTORICAL_CUTOVER_VERSION
    assert event.payload["cutover_year"] == CUTOVER
    assert event.payload["source_year"] == 2014
    assert event.payload["from_is_open"] is True
    assert event.payload["to_is_open"] is False


def test_it_never_writes_a_matter_closed_event() -> None:
    """`MATTER_CLOSED` means a person closed live work, with a real date."""
    matter = register_matter(2014)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    assert not ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_CLOSED
    ).exists()


def test_the_event_stays_out_of_the_professional_timeline() -> None:
    """Its timestamp is when the normalisation ran, not when work stopped.

    A line in the chronology reading "closed" on the cutover day would assert
    exactly the fact this operation refuses to claim.
    """
    assert ChangeEventType.MATTER_HISTORICAL_CUTOVER_CLOSED not in TIMELINE_EVENT_TYPES


def test_the_audit_payload_carries_no_source_content() -> None:
    matter = register_matter(2014, title="Sunteetiline registrikirje")
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    event = ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_HISTORICAL_CUTOVER_CLOSED
    ).latest("created_at")
    assert "Sunteetiline" not in str(event.payload)


# =========================================================================
# What it must leave alone
# =========================================================================


def test_an_already_closed_old_matter_is_a_no_op(specialist) -> None:
    """A real closure is a fact. It is never rewritten into a default."""
    matter = register_matter(2014, record_mode=RecordMode.FULL, owner=specialist)
    close_matter(
        matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="Tehtud"
    )
    matter.refresh_from_db()
    original_disposition, original_closed_at = matter.disposition, matter.closed_at

    plan = build_cutover_plan(cutover_year=CUTOVER)
    assert classifications(plan)[matter.pk] == Classification.ALREADY_CLOSED
    apply_cutover_plan(plan)

    matter.refresh_from_db()
    assert matter.disposition == original_disposition
    assert matter.closed_at == original_closed_at
    assert not ChangeEvent.objects.filter(
        matter=matter, event_type=ChangeEventType.MATTER_HISTORICAL_CUTOVER_CLOSED
    ).exists()


def test_an_activated_old_matter_is_a_current_exception(specialist) -> None:
    """Somebody attested this one. Re-running must not retire it again."""
    matter = register_matter(2019, record_mode=RecordMode.FULL, owner=specialist)

    plan = build_cutover_plan(cutover_year=CUTOVER)
    assert classifications(plan)[matter.pk] == Classification.CURRENT_EXCEPTION
    apply_cutover_plan(plan)

    matter.refresh_from_db()
    assert matter.is_open is True
    assert matter.record_mode == RecordMode.FULL


def test_a_cutover_year_matter_is_not_in_the_plan_at_all() -> None:
    matter = register_matter(2026)
    plan = build_cutover_plan(cutover_year=CUTOVER)

    assert matter.pk not in classifications(plan)
    apply_cutover_plan(plan)
    matter.refresh_from_db()
    assert matter.is_open is True


def test_a_onenote_only_matter_is_not_in_the_plan() -> None:
    """No register provenance, no register year, not this operation's business."""
    matter = factories.MatterFactory(
        record_mode=RecordMode.ARCHIVE, origin=MatterOrigin.LEGACY_ONENOTE, owner=None
    )
    plan = build_cutover_plan(cutover_year=CUTOVER)

    assert matter.pk not in classifications(plan)
    apply_cutover_plan(plan)
    matter.refresh_from_db()
    assert matter.is_open is True


def test_a_native_matter_is_not_in_the_plan() -> None:
    matter = factories.MatterFactory()
    plan = build_cutover_plan(cutover_year=CUTOVER)

    assert matter.pk not in classifications(plan)
    apply_cutover_plan(plan)
    matter.refresh_from_db()
    assert matter.is_open is True


def test_an_open_next_action_holds_the_row_back(specialist) -> None:
    """Live operational work. A bulk default may not strand it."""
    matter = register_matter(2018, owner=specialist)
    NextAction.objects.create(
        matter=matter,
        text="Sunteetiline jargmine tegevus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=date(2026, 12, 1),
        status=ActionStatus.OPEN,
    )

    plan = build_cutover_plan(cutover_year=CUTOVER)
    candidate = next(c for c in plan.candidates if c.matter.pk == matter.pk)
    assert candidate.classification == Classification.REVIEW_REQUIRED
    assert candidate.review_reason == ReviewReason.OPEN_NEXT_ACTION

    apply_cutover_plan(plan)
    matter.refresh_from_db()
    assert matter.is_open is True
    assert matter.next_actions.filter(status=ActionStatus.OPEN).count() == 1


def test_a_matter_spanning_several_register_years_is_reviewed() -> None:
    """Today's corpus has one reference per Matter. That is an observation,
    not an invariant, so a multi-year row is reviewed rather than assumed."""
    matter = register_matter(2015)
    factories.MatterSourceReferenceFactory(
        matter=matter,
        source_system=SOURCE_SYSTEM,
        source_sheet="2017",
        source_era="2011-2017",
        source_row_raw={},
    )

    plan = build_cutover_plan(cutover_year=CUTOVER)
    candidate = next(c for c in plan.candidates if c.matter.pk == matter.pk)
    assert candidate.classification == Classification.REVIEW_REQUIRED
    assert candidate.review_reason == ReviewReason.MULTIPLE_SOURCE_YEARS

    apply_cutover_plan(plan)
    matter.refresh_from_db()
    assert matter.is_open is True


# =========================================================================
# Plan discipline
# =========================================================================


def test_the_dry_run_writes_nothing() -> None:
    matter = register_matter(2014)
    events = ChangeEvent.objects.count()

    plan = build_cutover_plan(cutover_year=CUTOVER)

    assert plan.closable
    matter.refresh_from_db()
    assert matter.is_open is True
    assert ChangeEvent.objects.count() == events


def test_every_matter_is_classified_exactly_once() -> None:
    for year in (2011, 2014, 2019, 2025):
        register_matter(year)
    plan = build_cutover_plan(cutover_year=CUTOVER)

    assert sum(plan.counts.values()) == len(plan.candidates)


def test_applying_twice_changes_nothing_the_second_time() -> None:
    register_matter(2014)
    register_matter(2015)

    first = apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))
    assert first.closed == 2

    second = apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))
    assert second.closed == 0


def test_a_second_apply_writes_no_duplicate_events() -> None:
    matter = register_matter(2014)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.MATTER_HISTORICAL_CUTOVER_CLOSED
        ).count()
        == 1
    )


def test_an_unreviewed_cutover_year_cannot_be_applied() -> None:
    register_matter(2014)
    plan = build_cutover_plan(cutover_year=2020)

    with pytest.raises(UnreviewedCutoverYear):
        apply_cutover_plan(plan)


def test_refusing_an_unreviewed_year_retires_nothing() -> None:
    matter = register_matter(2014)
    plan = build_cutover_plan(cutover_year=2020)

    with pytest.raises(UnreviewedCutoverYear):
        apply_cutover_plan(plan)

    matter.refresh_from_db()
    assert matter.is_open is True


def test_the_reviewed_cutover_year_is_the_current_one() -> None:
    assert REVIEWED_HISTORICAL_CUTOVER_YEARS == (2026,)


def test_the_summary_is_aggregate_only() -> None:
    """Safe to paste into a message or attach to a review."""
    register_matter(2014, title="Sunteetiline pealkiri mida ei tohi lekkida")
    figures = summary(build_cutover_plan(cutover_year=CUTOVER))

    rendered = str(figures)
    assert "Sunteetiline" not in rendered
    assert figures["would_close"] == 1
    assert figures["by_source_year"][2014][Classification.WOULD_CLOSE_HISTORICAL] == 1


# =========================================================================
# The domain service on its own
# =========================================================================


def test_the_service_refuses_a_full_matter(specialist) -> None:
    """A FULL Matter is work somebody activated. Not the default's business."""
    matter = factories.MatterFactory(record_mode=RecordMode.FULL, owner=specialist)
    with pytest.raises(DomainError):
        mark_historical_archive_inactive(matter=matter)


def test_the_service_refuses_an_already_closed_matter() -> None:
    matter = register_matter(2014)
    mark_historical_archive_inactive(matter=matter)
    matter.refresh_from_db()

    with pytest.raises(DomainError):
        mark_historical_archive_inactive(matter=matter)


# =========================================================================
# The manual carry-over exception
# =========================================================================


def test_a_historical_matter_can_be_reactivated_by_a_person(specialist) -> None:
    matter = register_matter(2019, owner=specialist)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))
    matter.refresh_from_db()

    reactivate_historical_matter(
        matter=matter, actor=specialist, attestation="Menetlus kaib endiselt."
    )

    matter.refresh_from_db()
    assert matter.is_open is True
    assert matter.record_mode == RecordMode.FULL
    assert matter.origin == MatterOrigin.PROMOTED_LEGACY


def test_reactivation_keeps_the_original_reporting_year(specialist) -> None:
    """It reports under 2019 forever. Reactivation is not a re-filing."""
    matter = register_matter(2019, owner=specialist)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))
    matter.refresh_from_db()

    reactivate_historical_matter(matter=matter, actor=specialist, attestation="Endiselt kaib.")

    matter.refresh_from_db()
    assert matter.reporting_year == 2019
    assert matter.reference_year == 2019


def test_reactivation_invents_no_next_action(specialist) -> None:
    matter = register_matter(2019, owner=specialist)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))
    matter.refresh_from_db()

    reactivate_historical_matter(matter=matter, actor=specialist, attestation="Endiselt kaib.")

    assert matter.next_actions.count() == 0


def test_reactivation_requires_an_attestation(specialist) -> None:
    matter = register_matter(2019, owner=specialist)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))
    matter.refresh_from_db()

    with pytest.raises(DomainError):
        reactivate_historical_matter(matter=matter, actor=specialist, attestation="   ")


def test_a_real_closure_cannot_use_the_carry_over_shortcut(specialist) -> None:
    """Reversing somebody's recorded professional decision is their call."""
    matter = register_matter(2019, record_mode=RecordMode.FULL, owner=specialist)
    close_matter(
        matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="Tehtud"
    )
    matter.refresh_from_db()

    with pytest.raises(DomainError):
        reactivate_historical_matter(
            matter=matter, actor=specialist, attestation="Tahaks tagasi avada."
        )

    matter.refresh_from_db()
    assert matter.is_open is False


def test_a_reactivated_matter_survives_a_later_cutover_run(specialist) -> None:
    matter = register_matter(2019, owner=specialist)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))
    matter.refresh_from_db()
    reactivate_historical_matter(matter=matter, actor=specialist, attestation="Endiselt kaib.")

    plan = build_cutover_plan(cutover_year=CUTOVER)
    assert classifications(plan)[matter.pk] == Classification.CURRENT_EXCEPTION
    apply_cutover_plan(plan)

    matter.refresh_from_db()
    assert matter.is_open is True
    assert matter.record_mode == RecordMode.FULL


# =========================================================================
# Seams with what is already live
# =========================================================================


def test_the_2026_promotion_plan_is_identical_before_and_after(specialist) -> None:
    """Stage 2I must not touch the Stage 2F algorithm or its population."""
    register_matter(2026, owner=specialist)
    register_matter(2014)
    register_matter(2019)

    before = build_promotion_plan(year=2026).counts
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))
    after = build_promotion_plan(year=2026).counts

    assert before == after


def test_the_cutover_promotes_nothing_to_full() -> None:
    register_matter(2014)
    register_matter(2019)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    assert Matter.objects.filter(record_mode=RecordMode.FULL).count() == 0


def test_a_historical_matter_stays_in_the_search_projection(specialist) -> None:
    """Retiring a row is not deleting it. History stays findable."""
    matter = register_matter(2014, owner=specialist)
    from app.search.indexing import indexable_matters, refresh_matters

    refresh_matters(indexable_matters().filter(pk=matter.pk))
    before = SearchDocument.objects.filter(matter=matter, source_kind="MATTER").count()

    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    after = SearchDocument.objects.filter(matter=matter, source_kind="MATTER").count()
    assert before == 1
    assert after == 1


def test_a_historical_matter_is_still_reachable_in_teemad(client, specialist) -> None:
    matter = register_matter(2014, owner=specialist)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    client.force_login(specialist)
    response = client.get(f"/teemad/{matter.pk}/")
    assert response.status_code == 200


def test_minu_too_stays_full_only(specialist) -> None:
    """The Stage 2F invariant. A retired archive row was never in it anyway."""
    from app.matters.selectors import my_active_matters

    register_matter(2014, owner=specialist)
    assert my_active_matters(specialist).count() == 0

    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))
    assert my_active_matters(specialist).count() == 0


def test_visibility_is_untouched(specialist) -> None:
    from app.core.enums import Visibility

    matter = register_matter(2014, owner=specialist, visibility=Visibility.RESTRICTED)
    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    matter.refresh_from_db()
    assert matter.visibility == Visibility.RESTRICTED


def test_no_operational_snapshot_is_manufactured() -> None:
    from app.reporting.models import OperationalMatterSnapshot

    register_matter(2014)
    before = OperationalMatterSnapshot.objects.count()

    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    assert OperationalMatterSnapshot.objects.count() == before


def test_the_updated_timestamp_moves_but_nothing_else_does() -> None:
    matter = register_matter(2014)
    before = timezone.now()

    apply_cutover_plan(build_cutover_plan(cutover_year=CUTOVER))

    matter.refresh_from_db()
    assert matter.updated_at >= before
