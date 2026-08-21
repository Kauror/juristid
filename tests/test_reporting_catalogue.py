"""Properties of the metric catalogue itself. No database needed.

These are the checks that stop the catalogue becoming decorative: that every
definition has an implementation and every implementation a definition, that
the thresholds are readable, and that nothing the specification forbids has
quietly appeared in a label.
"""

from __future__ import annotations

import re

import pytest

from app.reporting import services
from app.reporting.metric_catalogue import CATALOGUE, DEFERRED_METRICS, definition
from app.reporting.metric_types import (
    MetricDefinition,
    MetricStatus,
    TimeBasis,
    Unit,
    distribution_from,
    grade,
)


def test_every_definition_has_an_implementation_and_the_reverse() -> None:
    """The invariant `services` asserts at import, restated where it is read.

    A definition with no implementation is a metric documented and never shown.
    An implementation with no definition is a number on a page with no reviewed
    meaning. Both are worth failing on.
    """
    assert set(CATALOGUE) == set(services.COMPUTERS)


def test_every_page_only_asks_for_metrics_that_exist() -> None:
    for page_keys in (
        services.OVERVIEW_CARDS,
        services.OVERVIEW_CHARTS,
        services.MATTERS_CARDS,
        services.MATTERS_CHARTS,
        services.MATTERS_TABLES,
        services.ACTIVITY_CARDS,
        services.ACTIVITY_CHARTS,
        services.ACTIVITY_TABLES,
        services.HISTORICAL_CARDS,
        services.HISTORICAL_CHARTS,
        services.HISTORICAL_TABLES,
        services.QUALITY_CARDS,
        services.QUALITY_CHARTS,
    ):
        for key in page_keys:
            assert key in CATALOGUE, key


def test_a_definition_key_matches_its_catalogue_key() -> None:
    for key, spec in CATALOGUE.items():
        assert spec.key == key


def test_every_definition_says_what_it_measures_and_over_what() -> None:
    for spec in CATALOGUE.values():
        assert spec.label_et.strip(), spec.key
        assert spec.description_et.strip(), spec.key
        assert spec.source_population_et.strip(), spec.key
        assert spec.time_basis in TimeBasis.values, spec.key
        assert spec.unit in Unit.values, spec.key


def test_a_coverage_threshold_is_a_fraction_not_a_percentage() -> None:
    """0.95, never 95. A definition written the other way would grade nothing."""
    with pytest.raises(ValueError, match="fraction"):
        MetricDefinition(
            key="BROKEN",
            version=1,
            label_et="Katki",
            description_et="—",
            source_population_et="—",
            time_basis=TimeBasis.POINT_IN_TIME,
            minimum_coverage=95,
        )


def test_a_definition_version_starts_at_one() -> None:
    with pytest.raises(ValueError, match="version"):
        MetricDefinition(
            key="BROKEN",
            version=0,
            label_et="Katki",
            description_et="—",
            source_population_et="—",
            time_basis=TimeBasis.POINT_IN_TIME,
        )


#: Words that must never appear in a metric label or description. Not a
#: substitute for review, but it catches the specific thing the specification
#: forbids from creeping back in through a rename (18.8).
_FORBIDDEN = (
    "tootlikkus",
    "produktiivsus",
    "töökoormus",
    "tookoormus",
    "edetabel",
    "võiduprotsent",
    "voiduprotsent",
    "vastamismäär",
    "vastamismaar",
    "mõjuskoor",
    "reiting",
)


def test_no_metric_is_named_after_a_prohibited_measure() -> None:
    for spec in CATALOGUE.values():
        haystack = f"{spec.label_et} {spec.description_et} {spec.notes_et}".lower()
        for word in _FORBIDDEN:
            # A definition may *say* it is not a workload measure. What it may
            # not do is present itself as one, so the negation is allowed.
            if word in haystack:
                assert "ei ole" in haystack or "mitte" in haystack, f"{spec.key}: {word}"


def test_owner_inventory_says_it_is_inventory() -> None:
    spec = definition("MATTERS_BY_OWNER")
    text = f"{spec.description_et} {spec.notes_et}".lower()
    assert "inventuur" in text
    assert "ei mõõda" in text or "ei järjestata" in text


def test_deferred_metrics_carry_a_reason_and_are_not_implemented() -> None:
    """A metric that is absent on purpose says why, and is absent everywhere."""
    assert DEFERRED_METRICS
    for key, reason in DEFERRED_METRICS.items():
        assert key not in CATALOGUE, key
        assert key not in services.COMPUTERS, key
        assert len(reason) > 30, key


def test_the_deferred_list_covers_the_legacy_conditional_metrics() -> None:
    """The four the brief made conditional on data the corpus does not carry."""
    assert {
        "LEGACY_MEMBER_ASKED_MATTERS",
        "LEGACY_MEMBER_ASKED_COUNT",
        "LEGACY_MEMBER_RESPONSE_COUNT",
        "LEGACY_SENT_DATE_OBSERVATIONS",
    } <= set(DEFERRED_METRICS)


def test_the_member_feedback_reason_refuses_a_response_rate() -> None:
    """Asked and answered are independent observations, not a ratio.

    The register has rows where more members answered than were asked directly,
    so there is no common denominator and never was (docs/adr/0007).
    """
    reason = " ".join(DEFERRED_METRICS[key] for key in DEFERRED_METRICS if "MEMBER" in key)
    assert "vastamismäära" in reason.lower()


def test_a_qualified_key_carries_the_version() -> None:
    spec = definition("MATTERS_TOTAL")
    assert re.fullmatch(rf"{spec.key}@\d+", spec.qualified_key)


# ---------------------------------------------------------------------------
# grade()
# ---------------------------------------------------------------------------


def _spec(**overrides: object) -> MetricDefinition:
    base = {
        "key": "TEST",
        "version": 1,
        "label_et": "Test",
        "description_et": "—",
        "source_population_et": "—",
        "time_basis": TimeBasis.POINT_IN_TIME,
    }
    base.update(overrides)
    return MetricDefinition(**base)  # type: ignore[arg-type]


def test_a_population_below_the_minimum_is_insufficient_data() -> None:
    spec = _spec(minimum_population=5)
    assert grade(spec, population_count=4) == MetricStatus.INSUFFICIENT_DATA
    assert grade(spec, population_count=5) == MetricStatus.AVAILABLE


def test_full_coverage_is_available_and_partial_coverage_says_so() -> None:
    spec = _spec(minimum_coverage=0.5)
    assert (
        grade(spec, population_count=10, coverage_count=10, coverage_denominator=10)
        == MetricStatus.AVAILABLE
    )
    assert (
        grade(spec, population_count=10, coverage_count=6, coverage_denominator=10)
        == MetricStatus.PARTIAL
    )
    assert (
        grade(spec, population_count=10, coverage_count=4, coverage_denominator=10)
        == MetricStatus.INSUFFICIENT_DATA
    )


def test_a_denominator_of_zero_is_not_zero_percent() -> None:
    """Nothing to measure is not the same as measuring nothing.

    With no eligible records there is no coverage fraction to compute, so the
    metric is graded on its population alone rather than being reported as 0 %
    complete — which would read as a data-quality failure that does not exist.
    """
    spec = _spec(minimum_coverage=0.9)
    assert (
        grade(spec, population_count=0, coverage_count=0, coverage_denominator=0)
        == MetricStatus.AVAILABLE
    )


def test_distribution_uses_order_statistics_not_a_mean() -> None:
    """A skewed set: nine small values and one very large one."""
    distribution = distribution_from([1, 1, 1, 1, 2, 2, 2, 3, 4, 400])
    assert distribution.n == 10
    assert distribution.median == 2
    assert distribution.maximum == 400
    assert distribution.total == 417
    # The mean would be 41.7, which describes none of the ten values.
    assert distribution.p90 <= 4 or distribution.p90 == 400


def test_an_empty_distribution_is_empty_rather_than_zero() -> None:
    distribution = distribution_from([])
    assert distribution.is_empty
    assert distribution.n == 0
