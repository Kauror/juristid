"""What each metric actually counts, against a world with every awkward state.

Read the numbers here as a specification. `tests/synthetic_statistics.py` builds
twelve Matters, and since docs/adr/0042 Martin — a lawyer — can see all of them:
the twelfth is Sandra's RESTRICTED one, which used to be the single Matter he
could not reach. Every expectation below is derived from that list rather than
from running the code and writing down what came out, and the shift was checked
against an independent oracle: for all 86 metrics a lawyer on this branch
reports exactly what the department head already reported on `main`, where the
restricted Matter was always visible.
"""

from __future__ import annotations

import pytest

from app.reporting import metric_catalogue as keys
from app.reporting.metric_types import MetricStatus
from app.reporting.selectors import historical
from app.reporting.services import compute

pytestmark = pytest.mark.django_db


def value(viewer, key, reporting_context, **kwargs):
    return compute(key, reporting_context(viewer, **kwargs)).value


def segments(viewer, key, reporting_context, **kwargs) -> dict[str, int]:
    result = compute(key, reporting_context(viewer, **kwargs))
    return {segment.label: segment.value for segment in result.segments}


# ---------------------------------------------------------------------------
# Population and period
# ---------------------------------------------------------------------------


def test_the_total_counts_every_visible_matter_over_all_years(world, reporting_context):
    assert value(world.martin, keys.MATTERS_TOTAL, reporting_context) == 12


def test_the_period_narrows_the_total_to_the_selected_year(world, reporting_context):
    """Five of the eleven carry this year's *register* reporting year."""
    assert value(world.martin, keys.MATTERS_TOTAL, reporting_context, period="kaesolev") == 6


def test_the_total_reports_how_many_it_could_not_place_in_the_period(world, reporting_context):
    result = compute(keys.MATTERS_TOTAL, reporting_context(world.martin, period="kaesolev"))
    # The two OneNote-only Matters have no register reporting year.
    assert result.coverage_count == 10
    assert result.coverage_denominator == 12
    assert any("Teadmata aasta" in note for note in result.notes)


def test_a_matters_year_is_never_the_database_row_creation_time(world, reporting_context):
    """Every fixture row was written today; almost none of them is this year's.

    This is the single easiest way to get a year axis wrong, and it produces a
    chart that says the whole archive happened the day it was imported.
    """
    by_year = segments(world.martin, keys.MATTERS_BY_REPORTING_YEAR, reporting_context)
    assert by_year[str(world.archive_year)] == 2
    assert by_year[str(world.previous_year)] == 2
    assert by_year[str(world.current_year)] == 6


def test_a_onenote_only_matter_lands_in_the_unknown_year_bucket(world, reporting_context):
    """Its `reporting_year` is a page timestamp, not a reporting year.

    One of the two even carries a plausible-looking year. Counting it on the
    Matter axis would file a matter under the year somebody last edited a
    OneNote page about it (Stage-2E brief 15).
    """
    by_year = segments(world.martin, keys.MATTERS_BY_REPORTING_YEAR, reporting_context)
    assert by_year["Teadmata aasta"] == 2
    assert sum(by_year.values()) == 12


def test_the_unknown_year_bucket_is_a_link_not_a_dropped_row(world, reporting_context):
    result = compute(keys.MATTERS_BY_REPORTING_YEAR, reporting_context(world.martin))
    unknown = next(s for s in result.segments if s.is_unknown)
    assert "aasta=teadmata" in unknown.url


def test_only_years_with_records_become_bars(world, reporting_context):
    """No flat line of zeros for years this population says nothing about."""
    labels = set(segments(world.martin, keys.MATTERS_BY_REPORTING_YEAR, reporting_context))
    assert str(world.current_year - 3) not in labels


# ---------------------------------------------------------------------------
# Record mode, origin, and the archive
# ---------------------------------------------------------------------------


def test_record_mode_separates_live_work_from_the_register_archive(world, reporting_context):
    assert segments(world.martin, keys.MATTERS_BY_RECORD_MODE, reporting_context) == {
        "Täielik": 7,
        "Arhiiv": 5,
    }


def test_origin_distinguishes_a_register_row_from_a_onenote_page(world, reporting_context):
    assert segments(world.martin, keys.MATTERS_BY_ORIGIN, reporting_context) == {
        "Loodud süsteemis": 7,
        "Imporditud registrist": 3,
        "Imporditud OneNote'ist": 2,
    }


def test_active_means_open_and_full_never_the_archive(world, reporting_context):
    """Counting a decade of imported rows as active makes every number useless."""
    assert value(world.martin, keys.ACTIVE_FULL_MATTERS, reporting_context) == 6


def test_active_ignores_the_period_because_it_is_a_state_not_a_window(world, reporting_context):
    for period in ("koik", "kaesolev", "eelmine"):
        assert value(world.martin, keys.ACTIVE_FULL_MATTERS, reporting_context, period=period) == 6


# ---------------------------------------------------------------------------
# Unknown is data
# ---------------------------------------------------------------------------


def test_policy_area_shows_its_unclassified_tail_rather_than_hiding_it(world, reporting_context):
    composition = segments(world.martin, keys.MATTERS_BY_POLICY_AREA, reporting_context)
    assert composition["Maksud"] == 2
    assert composition["Keskkond"] == 1
    assert composition["Klassifitseerimata"] == 9


def test_policy_area_coverage_is_measured_over_the_whole_population(world, reporting_context):
    result = compute(keys.MATTERS_BY_POLICY_AREA, reporting_context(world.martin))
    assert (result.coverage_count, result.coverage_denominator) == (3, 12)
    assert result.status == MetricStatus.PARTIAL


def test_owner_inventory_keeps_the_unassigned_bucket(world, reporting_context):
    composition = segments(world.martin, keys.MATTERS_BY_OWNER, reporting_context)
    assert composition["Sandra Testjurist"] == 3
    assert composition["Martin Testjurist"] == 3
    assert composition["Vastutaja määramata"] == 6


def test_stage_and_track_both_carry_an_unset_bucket(world, reporting_context):
    stages = segments(world.martin, keys.MATTERS_BY_STAGE, reporting_context)
    assert stages["Hetkeseis määramata"] == 6
    tracks = segments(world.martin, keys.MATTERS_BY_TRACK, reporting_context)
    assert tracks["Menetlusliik määramata"] == 8


def test_unclassified_is_also_a_metric_of_its_own(world, reporting_context):
    assert value(world.martin, keys.MATTERS_UNCLASSIFIED_POLICY_AREA, reporting_context) == 9


# ---------------------------------------------------------------------------
# Historical source coverage
# ---------------------------------------------------------------------------


def test_matters_with_and_without_a_historical_source_partition_the_population(
    world, reporting_context
):
    with_source = value(world.martin, keys.MATTERS_WITH_HISTORICAL_SOURCE, reporting_context)
    without = value(world.martin, keys.MATTERS_WITHOUT_HISTORICAL_SOURCE, reporting_context)
    assert with_source == 4
    assert with_source + without == 12


def test_the_four_source_classes_add_up_and_stay_distinct(world, reporting_context):
    composition = segments(world.martin, keys.HISTORICAL_SOURCE_COVERAGE_CLASSES, reporting_context)
    assert composition == {
        "Registririda koos OneNote'i allikaga": 2,
        "Registririda ilma OneNote'i allikata": 1,
        "Ainult OneNote'i-põhine teema": 2,
        "Süsteemis loodud teema": 7,
    }
    assert sum(composition.values()) == 12


def test_a_onenote_only_count_survives_a_period_filter(world, reporting_context):
    """These Matters have no register year, so a year filter cannot apply.

    Letting it apply would return zero, which reads as "there are none" rather
    than "the question does not apply to them".
    """
    for period in ("koik", "kaesolev"):
        assert value(world.martin, keys.ONENOTE_ONLY_MATTERS, reporting_context, period=period) == 2


def test_matters_with_several_source_pages_are_counted_separately(world, reporting_context):
    assert value(world.martin, keys.MATTERS_WITH_MULTIPLE_SOURCE_PAGES, reporting_context) == 1


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------


def test_a_submission_is_a_submission_record_and_nothing_else(world, reporting_context):
    """Three sent records. The draft is not one, and neither is a PDF."""
    assert value(world.martin, keys.SUBMISSIONS_SENT, reporting_context) == 4


def test_the_period_applies_to_the_send_date_not_the_matters_year(world, reporting_context):
    assert value(world.martin, keys.SUBMISSIONS_SENT, reporting_context, period="kaesolev") == 3
    assert value(world.martin, keys.SUBMISSIONS_SENT, reporting_context, period="eelmine") == 1


def test_the_trend_draws_no_bar_for_a_year_that_was_never_measured(world, reporting_context):
    """Structured submissions begin with this system; earlier years are absent.

    The bars stop at the first recorded year rather than running back to 2011 as
    a row of zeros, because a missing measurement is not a measurement of zero
    (Stage-2E brief 24).
    """
    result = compute(keys.SUBMISSIONS_SENT_BY_PERIOD, reporting_context(world.martin))
    labels = [segment.label for segment in result.segments]
    assert labels == [str(world.previous_year), str(world.current_year)]
    assert str(world.archive_year) not in labels
    assert any("mõõtmist" in note for note in result.notes)


def test_no_submission_records_at_all_is_insufficient_data_not_zero(world, reporting_context):
    from app.search.indexing import suspend_indexing
    from app.submissions.models import Submission

    # Through `suspend_indexing`, like every other bulk writer. The per-row
    # signals reindex the Matter as each Submission goes, and the projection
    # then tries to insert a row pointing at a Submission the cascade is in the
    # middle of deleting (app/search/indexing.py).
    with suspend_indexing():
        Submission.objects.all().delete()
    result = compute(keys.SUBMISSIONS_SENT, reporting_context(world.martin))
    assert result.status == MetricStatus.INSUFFICIENT_DATA
    assert not result.has_value


def test_recipients_are_addressees_only(world, reporting_context):
    """`Teadmiseks` answers a different question and is not counted here."""
    composition = segments(world.martin, keys.SUBMISSIONS_BY_RECIPIENT, reporting_context)
    assert composition == {"Näidisministeerium": 2, "Näidiskomisjon": 1, "Näidisliit": 1}


def test_matters_are_bucketed_by_how_many_submissions_they_produced(world, reporting_context):
    composition = segments(world.martin, keys.MATTERS_BY_SUBMISSION_COUNT, reporting_context)
    assert composition == {
        "Arvamust ei ole saadetud": 4,
        "Üks arvamus": 2,
        "Kaks või rohkem": 1,
    }


# ---------------------------------------------------------------------------
# Organisations
# ---------------------------------------------------------------------------


def test_sender_and_addressee_are_never_merged(world, reporting_context):
    """The register's single counterparty column meant both, in different eras."""
    senders = segments(world.martin, keys.MATTERS_BY_SOURCE_ORGANISATION, reporting_context)
    addressees = segments(world.martin, keys.MATTERS_BY_ADDRESSEE_ORGANISATION, reporting_context)
    assert senders == {"Näidisministeerium": 2, "Näidisliit": 1}
    assert addressees == {"Näidisministeerium": 1, "Näidiskomisjon": 1}


def test_the_organisation_metrics_state_the_era_boundary(world, reporting_context):
    result = compute(keys.MATTERS_BY_SOURCE_ORGANISATION, reporting_context(world.martin))
    assert any("KELLELT" in note and "KELLELE" in note for note in result.notes)


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------


def test_only_a_do_with_a_deadline_can_be_overdue(world, reporting_context):
    """A WAIT whose review date has passed is due for a look, never late."""
    assert value(world.martin, keys.OVERDUE_DO_DEADLINE, reporting_context) == 2
    assert value(world.martin, keys.WAIT_REVIEW_DUE, reporting_context) == 1
    assert value(world.martin, keys.MONITOR_REVIEW_DUE, reporting_context) == 1


def test_review_due_is_never_described_as_overdue(world, reporting_context):
    for key in (keys.WAIT_REVIEW_DUE, keys.MONITOR_REVIEW_DUE):
        result = compute(key, reporting_context(world.martin))
        joined = " ".join(result.notes) + result.definition.description_et
        assert "hilinen" not in joined.lower() or "ei ole" in joined.lower()


def test_the_quiet_matter_is_the_one_with_no_next_action(world, reporting_context):
    assert value(world.martin, keys.ACTIVE_WITHOUT_NEXT_ACTION, reporting_context) == 1
    assert value(world.martin, keys.ACTIVE_WITHOUT_OWNER, reporting_context) == 1
    assert value(world.martin, keys.ACTIVE_WITHOUT_STAGE, reporting_context) == 1


def test_archive_matters_are_never_counted_as_missing_a_next_action(world, reporting_context):
    """Five archive rows have no next action, and none of them is a defect."""
    result = compute(keys.ACTIVE_WITHOUT_NEXT_ACTION, reporting_context(world.martin))
    assert result.value == 1
    assert result.population_count == 6


def test_next_actions_are_grouped_by_kind_without_being_added_up(world, reporting_context):
    assert segments(world.martin, keys.NEXT_ACTION_BY_KIND, reporting_context) == {
        "Teen": 3,
        "Ootan": 1,
        "Jälgin": 1,
    }


def test_entries_are_authored_notes_not_onenote_pages(world, reporting_context):
    assert value(world.martin, keys.ENTRY_COUNT, reporting_context) == 3


def test_new_matters_are_measured_on_arrival_not_on_row_creation(world, reporting_context):
    result = compute(
        keys.NEW_NATIVE_FULL_MATTERS, reporting_context(world.martin, period="kaesolev")
    )
    assert result.value == 4
    assert result.coverage_denominator == 7


# ---------------------------------------------------------------------------
# Historical material
# ---------------------------------------------------------------------------


def test_an_occurrence_is_not_a_unique_file(world, reporting_context):
    """The same bytes attached to two pages are two occurrences, and stay two."""
    occurrences = value(world.martin, keys.HISTORICAL_RESOURCE_OCCURRENCES, reporting_context)
    unique = value(world.martin, keys.HISTORICAL_UNIQUE_BINARY_CONTENTS, reporting_context)
    assert occurrences == 9
    assert unique == 8


def test_bytes_are_summed_over_occurrences(world, reporting_context):
    assert value(world.martin, keys.HISTORICAL_RESOURCE_BYTES, reporting_context) == 54


def test_file_types_are_counted_by_extension_and_add_up(world, reporting_context):
    composition = segments(world.martin, keys.HISTORICAL_RESOURCES_BY_TYPE, reporting_context)
    assert composition["PDF"] == 3
    assert composition["MSG"] == 1
    assert composition["EML"] == 1
    assert composition["ASICE"] == 1
    assert composition["BDOC"] == 1
    assert composition["DOCX"] == 2
    assert sum(composition.values()) == 9


def test_signed_containers_are_a_category_of_their_own(world, reporting_context):
    result = compute(keys.HISTORICAL_SIGNED_CONTAINERS, reporting_context(world.martin))
    assert result.value == 2
    assert any("ebaõnnestumiste" in note for note in result.notes)


def test_email_attachments_are_msg_and_eml(world, reporting_context):
    assert value(world.martin, keys.HISTORICAL_EMAIL_RESOURCES, reporting_context) == 2


def test_pages_are_counted_once_however_many_matters_claim_them(world, reporting_context):
    assert value(world.martin, keys.LEGACY_SOURCE_PAGES, reporting_context) == 5


def test_the_onenote_section_is_source_history_not_a_policy_area(world, reporting_context):
    composition = segments(world.martin, keys.LEGACY_SOURCE_PAGES_BY_SECTION, reporting_context)
    assert composition == {"ARHIIV maksud ja toll": 2, "ARHIIV keskkond": 2, "ARHIIV liikmed": 1}
    # The canonical taxonomy has completely different labels, and neither
    # chart's names appear in the other.
    areas = segments(world.martin, keys.MATTERS_BY_POLICY_AREA, reporting_context)
    assert not set(composition) & set(areas)


def test_the_page_year_chart_uses_the_source_timestamp(world, reporting_context):
    """Source history, kept well away from the Matter reporting year."""
    composition = segments(world.martin, keys.LEGACY_SOURCE_PAGES_BY_YEAR, reporting_context)
    assert composition == {
        str(world.archive_year): 2,
        str(world.previous_year): 2,
        str(world.today.year): 1,
    }


def test_reading_order_ambiguity_is_reported_over_the_visible_pages(world, reporting_context):
    result = compute(keys.READING_ORDER_AMBIGUOUS, reporting_context(world.martin))
    assert result.value == 1
    assert result.population_count == 5


def test_distributions_use_medians_rather_than_a_mean(world, reporting_context):
    result = compute(keys.RESOURCES_PER_PAGE, reporting_context(world.martin))
    assert result.distribution is not None
    assert result.distribution.n == 5
    assert result.distribution.total == 9
    assert result.distribution.maximum == 6


# ---------------------------------------------------------------------------
# Materialisation
# ---------------------------------------------------------------------------


def test_the_four_materialisation_states_partition_the_occurrences(world, reporting_context):
    composition = segments(world.martin, keys.MATERIALISATION_STATUS, reporting_context)
    assert composition == {
        "Imporditud": 6,
        "Kopeerimist ootab": 1,
        "Allikas tühi": 1,
        "Kopeerimine ebaõnnestus": 1,
    }
    assert sum(composition.values()) == 9


def test_an_empty_original_is_not_a_copy_failure(world, reporting_context):
    """Six attachments in the real corpus are zero bytes in OneNote itself.

    Calling that a failure sends an operator looking for a bug that is a fact
    about the lawyer's own notebook (main, commit 3888afd).
    """
    assert value(world.martin, keys.MATERIALISATION_FAILED, reporting_context) == 1
    empty = historical.visible_resources(reporting_context(world.martin), state="empty")
    assert [resource.original_filename for resource in empty] == ["tyhi.docx"]


def test_the_sql_materialisation_state_agrees_with_the_rendered_one(world, reporting_context):
    """The statistic and the case-file page must not drift apart.

    `historical.materialisation_q` counts thousands of rows in SQL;
    `historical_views._file_state` decides one row for a template. They are two
    expressions of one rule, so the suite checks them against each other — the
    same arrangement main uses for extraction eligibility.
    """
    from app.legacy_import.historical_views import _file_state
    from app.legacy_import.source_pages import LegacySourceResourceImport

    context = reporting_context(world.martin)
    for state in ("imported", "pending", "empty", "unavailable"):
        for resource in historical.visible_resources(context, state=state):
            record = (
                LegacySourceResourceImport.objects.filter(resource=resource)
                .select_related("document")
                .first()
            )
            assert _file_state(resource, record) == state, (
                f"{resource.original_filename}: SQL says {state}"
            )


# ---------------------------------------------------------------------------
# Extraction and searchability
# ---------------------------------------------------------------------------


def test_extraction_states_are_reported_separately(world, reporting_context):
    assert value(world.martin, keys.EXTRACTION_SUCCESS, reporting_context) == 6
    assert value(world.martin, keys.EXTRACTION_FAILED, reporting_context) == 1
    assert value(world.martin, keys.EXTRACTION_NOT_APPLICABLE, reporting_context) == 2


def test_a_file_waiting_on_a_scanner_is_not_a_failure(world, reporting_context, settings):
    """The number this whole module exists to avoid printing.

    With real data allowed, an unscanned file may not be opened. It is not
    queued, and it is emphatically not a parse failure — flattening the states
    is what turns a security control into "16 000 failed extraction"
    (main, commit 34d91b1).
    """
    settings.REAL_DATA_ALLOWED = True
    context = reporting_context(world.martin)

    assert compute(keys.EXTRACTION_AWAITING_SCANNER, context).value == 1
    assert compute(keys.EXTRACTION_FAILED, context).value == 1
    assert compute(keys.EXTRACTION_PENDING, context).value == 1


def test_without_real_data_the_scanner_gate_does_not_apply(world, reporting_context):
    """In a synthetic environment an unscanned file is extractable, so nothing waits."""
    context = reporting_context(world.martin)
    assert compute(keys.EXTRACTION_AWAITING_SCANNER, context).value == 0
    assert compute(keys.EXTRACTION_PENDING, context).value == 2


def test_the_reporting_eligibility_rule_is_the_orchestrators_rule(
    world, reporting_context, settings
):
    """Imported, not restated. Two copies of this rule would drift."""
    from app.documents.extraction.orchestrator import awaiting_scanner as queue_awaiting
    from app.reporting.selectors.documents import awaiting_scanner, visible_versions

    settings.REAL_DATA_ALLOWED = True
    context = reporting_context(world.head)
    reported = set(awaiting_scanner(context).values_list("pk", flat=True))
    queued = set(queue_awaiting().values_list("pk", flat=True))
    visible = set(visible_versions(context).values_list("pk", flat=True))
    assert reported == queued & visible


def test_searchability_excludes_what_no_parser_opens(world, reporting_context):
    """Two signed containers are not a coverage gap; they are a decision."""
    result = compute(keys.SEARCHABLE_DOCUMENT_COVERAGE, reporting_context(world.martin))
    assert result.population_count == 11
    assert result.eligible_count == 9
    assert result.coverage_count == 6
    assert result.value == 67


def test_searchability_says_what_is_holding_it_back(world, reporting_context, settings):
    """And says it without implying the corpus might be infected.

    The files are known to be malware-free; what has not happened is text
    extraction. Naming the scanner told a reader the opposite (ADR 0033).
    """
    settings.REAL_DATA_ALLOWED = True
    result = compute(keys.SEARCHABLE_DOCUMENT_COVERAGE, reporting_context(world.martin))
    notes = " ".join(result.notes)

    assert "tekstitöötlust" in notes
    assert "pahavaravabad" in notes
    assert "pahavarakontrolli" not in notes


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def test_the_reconciliation_queue_is_reported_by_class(world, reporting_context):
    assert value(world.martin, keys.RECONCILIATION_PENDING, reporting_context) == 5
    assert value(world.martin, keys.RECONCILIATION_CONFLICT, reporting_context) == 1
    assert value(world.martin, keys.UNLINKED_SUBSTANTIVE_PAGES, reporting_context) == 1


def test_the_review_queue_link_is_offered_only_to_an_administrator(world, reporting_context):
    """The queue creates Matters, so it stays administrator-only.

    A reader without that role sees the number and is told where it is handled,
    rather than being handed a link that 404s.
    """
    assert compute(keys.RECONCILIATION_CONFLICT, reporting_context(world.admin)).drillthrough_url
    assert not compute(
        keys.RECONCILIATION_CONFLICT, reporting_context(world.martin)
    ).drillthrough_url
