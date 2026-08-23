"""Where the three Wave 1 branches actually meet.

Each branch was reviewed on its own and each is correct on its own. What no
branch could test is the seam between them, and there is exactly one that
carries a decision:

**A published statistic counts real work. An operational list shows every
record the viewer may read, including the development ones.**

TEST classification (C) introduced `Matter.data_class` and deliberately left
the reporting population alone, because Statistics 2.0 (D) owned that file in
parallel. Closing that handoff is this integration's job, and it is closed in
one place — `app.reporting.selectors.base.visible_matters` — rather than metric
by metric. These tests are what stops it being reopened by a metric added later
that starts from `Matter.objects` directly.

The second seam is quieter. The opinions archive is real evidence whatever it
is linked to, so the corpus inventory is never filtered by data class. But
*link coverage* is a claim about the department's Matters, so a letter attached
only to a development Matter must not count as linked. Both halves are asserted
below, because fixing one by breaking the other would look like a pass.

Everything is synthetic.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.matters.enums import MatterDataClass, MatterOrigin, RecordMode
from app.matters.models import Matter
from app.reporting import metric_catalogue as keys
from app.reporting.selectors.base import active_full, visible_matters
from app.reporting.services import compute
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture
def real_and_test(world):
    """One ordinary Matter and one development Matter, both readable.

    Both are NATIVE and open, because `matters_test_data_is_native` refuses a
    TEST classification anywhere else — a development record is something
    somebody made here, never something the register imported.
    """
    real = factories.MatterFactory(
        title="Päris teema",
        origin=MatterOrigin.NATIVE,
        record_mode=RecordMode.FULL,
        is_open=True,
        owner=world.martin,
        data_class=MatterDataClass.REAL,
    )
    test = factories.MatterFactory(
        title="Testandmete teema",
        origin=MatterOrigin.NATIVE,
        record_mode=RecordMode.FULL,
        is_open=True,
        owner=world.martin,
        data_class=MatterDataClass.TEST,
    )
    return real, test


# ---------------------------------------------------------------------------
# The operational surfaces still show development records
# ---------------------------------------------------------------------------


def _titles(client, viewer, query: str = "") -> str:
    client.force_login(viewer)
    url = reverse("matters:matter_list") + (f"?{query}" if query else "")
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def test_the_register_shows_both_kinds_by_default(client, world, real_and_test):
    """`Kõik` is the default on purpose while the department is still building.

    A developer who cannot see the record they just made will make another one.
    """
    body = _titles(client, world.admin)

    assert "Päris teema" in body
    assert "Testandmete teema" in body


def test_the_register_can_be_narrowed_to_real_work(client, world, real_and_test):
    body = _titles(client, world.admin, "andmed=paris")

    assert "Päris teema" in body
    assert "Testandmete teema" not in body


def test_the_register_can_be_narrowed_to_development_records(client, world, real_and_test):
    body = _titles(client, world.admin, "andmed=test")

    assert "Testandmete teema" in body
    assert "Päris teema" not in body


def test_authorization_is_not_the_data_class(client, world, real_and_test):
    """Two dimensions, narrowing the same rows, answering different questions.

    A restricted Matter is hidden from someone who may not read it. A TEST
    Matter is shown to everyone who may read it and left out of the published
    figures. Merging the two would make one of them silently mean the other.
    """
    real, test = real_and_test

    assert Matter.objects.visible_to(world.admin).filter(pk=test.pk).exists()
    assert Matter.objects.visible_to(world.admin).real_data().filter(pk=test.pk).exists() is False
    assert Matter.objects.visible_to(world.admin).real_data().filter(pk=real.pk).exists()


# ---------------------------------------------------------------------------
# Published statistics count real work only
# ---------------------------------------------------------------------------


def test_the_central_reporting_population_excludes_development_records(
    world, reporting_context, real_and_test
):
    """One place, so a metric written next month inherits the rule.

    Asserted on the population itself rather than through a metric, because
    this is the line every other selector in the package starts from.
    """
    real, test = real_and_test
    context = reporting_context(world.admin)

    population = visible_matters(context)

    assert population.filter(pk=real.pk).exists()
    assert population.filter(pk=test.pk).exists() is False
    assert active_full(context).filter(pk=test.pk).exists() is False


@pytest.mark.parametrize(
    "key",
    [
        keys.MATTERS_TOTAL,
        keys.ACTIVE_FULL_MATTERS,
        keys.MATTERS_BY_REPORTING_YEAR,
        keys.MATTERS_BY_OWNER,
        keys.MATTERS_BY_RECORD_MODE,
    ],
)
def test_a_development_record_moves_no_published_number(world, reporting_context, key):
    """Measured as a difference, not against a hand-derived constant.

    The world these run against is large, and a test that asserted "the total is
    N" would need rewriting every time the fixture grows — and would pass just
    as happily if the population rule were removed and N updated.
    """
    context = reporting_context(world.admin)
    before = compute(key, context).value

    factories.MatterFactory(
        title="Testandmete teema",
        origin=MatterOrigin.NATIVE,
        record_mode=RecordMode.FULL,
        is_open=True,
        owner=world.martin,
        data_class=MatterDataClass.TEST,
    )

    assert compute(key, context).value == before


def test_a_real_record_does_move_the_number(world, reporting_context):
    """The premise. Without this the test above passes on a broken metric."""
    context = reporting_context(world.admin)
    before = compute(keys.ACTIVE_FULL_MATTERS, context).value

    factories.MatterFactory(
        title="Päris teema",
        origin=MatterOrigin.NATIVE,
        record_mode=RecordMode.FULL,
        is_open=True,
        owner=world.martin,
        data_class=MatterDataClass.REAL,
    )

    assert compute(keys.ACTIVE_FULL_MATTERS, context).value == before + 1


# ---------------------------------------------------------------------------
# The archive is evidence; its link coverage is a claim about Matters
# ---------------------------------------------------------------------------


def test_a_letter_linked_only_to_a_development_matter_is_not_linked_work(
    world, reporting_context, archive_world
):
    """Both halves, because fixing either one alone would look like a pass.

    The archive file stays in the corpus — it is a real letter, and nothing
    about a development Matter makes the evidence less real. What it must not
    do is raise the department's link coverage, which is a statement about the
    Chamber's own work.
    """
    from tests.synthetic_statistics import _archive_binary, _archive_item, _archive_link

    context = reporting_context(world.admin, period="koik")
    before = compute(keys.OPINION_ARCHIVE_LINK_COVERAGE, context)

    test_matter = factories.MatterFactory(
        title="Testandmete teema",
        origin=MatterOrigin.NATIVE,
        record_mode=RecordMode.FULL,
        is_open=True,
        owner=world.martin,
        data_class=MatterDataClass.TEST,
    )
    payload = "wave1-integration-test-only-letter"
    _archive_item(
        archive_world.batch,
        when=archive_world.cutoff,
        title="Ainult testteemaga seotud",
        payload=payload,
    )
    _archive_link(_archive_binary(payload), test_matter)

    after = compute(keys.OPINION_ARCHIVE_LINK_COVERAGE, context)

    assert after.coverage_count == before.coverage_count, (
        "a development Matter cannot raise link coverage"
    )
    assert after.coverage_denominator == before.coverage_denominator + 1, (
        "the letter is still in the corpus; only the claim about Matters excludes it"
    )


def test_a_letter_linked_to_a_real_matter_is_linked_work(world, reporting_context, archive_world):
    """The premise for the test above."""
    from tests.synthetic_statistics import _archive_binary, _archive_item, _archive_link

    context = reporting_context(world.admin, period="koik")
    before = compute(keys.OPINION_ARCHIVE_LINK_COVERAGE, context)

    payload = "wave1-integration-real-letter"
    _archive_item(
        archive_world.batch,
        when=archive_world.cutoff,
        title="Paris teemaga seotud",
        payload=payload,
    )
    _archive_link(_archive_binary(payload), world.native_open)

    after = compute(keys.OPINION_ARCHIVE_LINK_COVERAGE, context)

    assert after.coverage_count == before.coverage_count + 1
    assert after.coverage_denominator == before.coverage_denominator + 1
