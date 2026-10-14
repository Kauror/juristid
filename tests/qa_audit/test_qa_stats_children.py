"""QA audit 2026-10-14 (area `stats`): restricted and TEST *child* records.

Every "after" figure is computed on a *fresh* ReportingContext: a context
memoises `count()` answers by SQL text for its lifetime, so reusing one across
a data change would compare a figure with itself.

The shared Statistika suite plants a restricted Matter and TEST twin Matters.
What it does not plant is a restricted opinion on an ordinary Matter, or an
archive file reconciled onto a TEST Matter — the two child shapes probed here.

* QA-STATS-03 — `MATTERS_BY_SUBMISSION_COUNT` and
  `MATTERS_WITH_MULTIPLE_SUBMISSIONS` count sent opinions through the
  `submissions` relation with no `Submission.visible_to`: a restricted opinion
  a reader may not see moves the reader's buckets.
* QA-STATS-04 — `HISTORICAL_SUBMISSION_COVERAGE` and
  `OPINION_ARCHIVE_MATTER_COVERAGE` do not apply `real_data()`: an archive
  file reconstructed onto / settled on a TEST Matter raises the published
  coverage percentage.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.core.enums import Visibility
from app.matters.enums import MatterDataClass, MatterOrigin, RecordMode
from app.matters.models import Matter
from app.reporting import metric_catalogue as keys
from app.reporting.services import compute
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from tests.synthetic_statistics import _document, _version

pytestmark = pytest.mark.django_db

TALLINN = ZoneInfo("Europe/Tallinn")


def _sent(matter: Matter, title: str, *, restricted: bool) -> Submission:
    document = _document(matter, f"{title} lõplik")
    if restricted:
        document.visibility_override = Visibility.RESTRICTED
        document.save(update_fields=["visibility_override"])
    version = _version(document, filename=f"{title}.pdf", payload=f"qa {title} {matter.pk}")
    return Submission.objects.create(
        matter=matter,
        title=title,
        status=SubmissionStatus.SENT,
        sent_at=datetime.combine(
            matter.received_date or matter.created_at.date(), time(9), tzinfo=TALLINN
        ),
        final_version=version,
        visibility_override=Visibility.RESTRICTED if restricted else "",
    )


def _buckets(context) -> dict[str, int]:
    return {s.label: s.value for s in compute(keys.MATTERS_BY_SUBMISSION_COUNT, context).segments}


@pytest.fixture
def quiet_matter(world):
    """An ordinary real FULL Matter of this year, with nothing sent yet."""
    return Matter.objects.create(
        title="QA tavaline teema piiratud arvamusega",
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.NATIVE,
        reference_year=world.today.year,
        reference_number=880,
        reporting_year=world.today.year,
        owner=world.sandra,
        received_date=world.today.replace(month=1, day=10),
    )


def test_the_reader_cannot_see_the_restricted_opinion(world, quiet_matter):
    """Precondition probe: the opinion really is hidden from the reader, visible to head."""
    opinion = _sent(quiet_matter, "QA piiratud", restricted=True)
    assert not Submission.objects.visible_to(world.reader).filter(pk=opinion.pk).exists()
    assert Submission.objects.visible_to(world.head).filter(pk=opinion.pk).exists()
    assert Matter.objects.visible_to(world.reader).filter(pk=quiet_matter.pk).exists()


def test_submissions_sent_hides_a_restricted_opinion_from_the_reader(
    world, quiet_matter, reporting_context
):
    """Probe (passes): the canonical submission count is authorized per opinion."""
    context = reporting_context(world.reader)
    before = compute(keys.SUBMISSIONS_SENT, context).value
    _sent(quiet_matter, "QA piiratud", restricted=True)
    assert compute(keys.SUBMISSIONS_SENT, reporting_context(world.reader)).value == before


def test_submission_count_buckets_hide_a_restricted_opinion_from_the_reader(
    world, quiet_matter, reporting_context
):
    context = reporting_context(world.reader)
    before = _buckets(context)
    _sent(quiet_matter, "QA piiratud", restricted=True)
    assert _buckets(reporting_context(world.reader)) == before


def test_multiple_submissions_hides_restricted_opinions_from_the_reader(
    world, quiet_matter, reporting_context
):
    context = reporting_context(world.reader)
    before = compute(keys.MATTERS_WITH_MULTIPLE_SUBMISSIONS, context).value
    _sent(quiet_matter, "QA avalik", restricted=False)
    _sent(quiet_matter, "QA piiratud", restricted=True)
    # The reader sees one opinion on this Matter, so it is not «mitu».
    assert (
        compute(keys.MATTERS_WITH_MULTIPLE_SUBMISSIONS, reporting_context(world.reader)).value
        == before
    )


def test_submission_count_buckets_do_count_it_for_the_head(world, quiet_matter, reporting_context):
    """Probe (passes): an entitled reader does see the opinion counted."""
    context = reporting_context(world.head)
    before = _buckets(context)
    _sent(quiet_matter, "QA piiratud", restricted=True)
    after = _buckets(reporting_context(world.head))
    assert after["Üks arvamus"] == before.get("Üks arvamus", 0) + 1


# ---------------------------------------------------------------------------
# QA-STATS-04 — archive coverage counts TEST Matters
# ---------------------------------------------------------------------------


@pytest.fixture
def test_matter(world):
    return Matter.objects.create(
        title="QA TEST teema arhiivikirjaga",
        data_class=MatterDataClass.TEST,
        record_mode=RecordMode.FULL,
        origin=MatterOrigin.NATIVE,
        reference_year=world.today.year,
        reference_number=881,
        reporting_year=world.today.year,
        owner=world.sandra,
        received_date=world.today.replace(month=1, day=11),
    )


def _reconstruct(archive_world, matter: Matter, payload_sha: str) -> None:
    from app.legacy_import.opinion_archive import OpinionArchiveItem, OpinionSubmissionImport
    from app.legacy_import.opinion_enums import OpinionMatchClass

    item = OpinionArchiveItem.objects.filter(sha256=payload_sha).order_by("created_at").first()
    submission = _sent(matter, f"QA taastatud {payload_sha[:6]}", restricted=False)
    OpinionSubmissionImport.objects.create(
        item=item,
        submission=submission,
        batch=archive_world.batch,
        created_submission=True,
        match_class=OpinionMatchClass.EXACT_BINARY_MATTER,
    )


@pytest.mark.xfail(
    strict=True,
    reason="QA-STATS-04: HISTORICAL_SUBMISSION_COVERAGE counts a reconstruction on a TEST Matter",
)
def test_historical_submission_coverage_ignores_test_matters(
    archive_world, test_matter, reporting_context, world
):
    context = reporting_context(world.head)
    before = compute(keys.HISTORICAL_SUBMISSION_COVERAGE, context)
    _reconstruct(archive_world, test_matter, archive_world.unlinked_sha)
    after = compute(keys.HISTORICAL_SUBMISSION_COVERAGE, reporting_context(world.head))
    assert (after.value, after.coverage_count) == (before.value, before.coverage_count)


def test_historical_submission_coverage_counts_a_real_reconstruction(
    archive_world, reporting_context, world
):
    """Probe (passes): the same reconstruction on a REAL Matter does count — the probe can see."""
    context = reporting_context(world.head)
    before = compute(keys.HISTORICAL_SUBMISSION_COVERAGE, context)
    _reconstruct(archive_world, world.native_open, archive_world.unlinked_sha)
    after = compute(keys.HISTORICAL_SUBMISSION_COVERAGE, reporting_context(world.head))
    assert after.coverage_count == before.coverage_count + 1


@pytest.mark.xfail(
    strict=True,
    reason="QA-STATS-04: OPINION_ARCHIVE_MATTER_COVERAGE counts a file settled on a TEST Matter",
)
def test_archive_matter_coverage_ignores_test_matters(
    archive_world, test_matter, reporting_context, world
):
    from app.legacy_import.opinion_archive import OpinionArchiveItem, OpinionMatchCandidate
    from app.legacy_import.opinion_enums import OpinionCandidateState, OpinionMatchClass

    context = reporting_context(world.head)
    before = compute(keys.OPINION_ARCHIVE_MATTER_COVERAGE, context)
    item = OpinionArchiveItem.objects.get(sha256=archive_world.unlinked_sha)
    OpinionMatchCandidate.objects.create(
        item=item,
        matter=test_matter,
        batch=archive_world.batch,
        match_class=OpinionMatchClass.EXACT_BINARY_MATTER,
        state=OpinionCandidateState.LINKED,
    )
    after = compute(keys.OPINION_ARCHIVE_MATTER_COVERAGE, reporting_context(world.head))
    assert after.coverage_count == before.coverage_count
