"""The operator queue and the coverage numbers the archive makes measurable.

Two things are being defended here.

The queue is an *administrative* surface. A specialist opening it would be
reading migration bookkeeping about matters they may have no business seeing,
so the role check is a test rather than a convention.

The metrics have to keep saying what they mean. Stage 2H makes historical
submission counts possible for the first time, and the danger in that is a
dashboard that quietly converts an unresolved review queue into a claim about
how much advocacy happened (Stage-2H brief 53, 55, 71, 80, 81).
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse

from app.core.enums import Visibility
from app.legacy_import.opinion_archive import (
    OpinionArchiveBatch,
    OpinionArchiveItem,
    OpinionMatchCandidate,
)
from app.legacy_import.opinion_enums import OpinionCandidateState, OpinionMatchClass
from app.reporting import metric_catalogue as keys
from app.reporting.services import compute
from app.submissions.enums import (
    RecipientRole,
    SentAtPrecision,
    SubmissionStatus,
)
from app.submissions.models import SubmissionRecipient
from tests import factories
from tests.break_glass import grant_to

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# A small catalogued archive, built directly rather than through the importer
# ---------------------------------------------------------------------------


def catalogue(**overrides):
    batch = OpinionArchiveBatch.objects.create(
        archive_sha256="a" * 64,
        importer_version="test/0",
        started_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    digest = overrides.pop("sha256", "b" * 64)
    item = OpinionArchiveItem.objects.create(
        batch=batch,
        archive_sha256="a" * 64,
        archive_relative_path=overrides.pop("path", "Opinions/naidis.pdf"),
        original_filename="naidis.pdf",
        sha256=digest,
        size_bytes=1024,
        detected_type="application/pdf",
        filename_date=datetime.date(2024, 4, 10),
        filename_recipient="Näidisministeerium",
        filename_title="Näidisarvamus",
    )
    return batch, item


@pytest.fixture
def context(db):
    """A reporting context over an otherwise empty world.

    Deliberately not the Statistika `world` fixture: these assertions are about
    ratios, and a shared world already carrying four sent submissions would
    make every percentage a function of somebody else's fixture.
    """
    from django.utils import timezone

    from app.reporting.context import ReportingContext, parse_period

    today = datetime.date(2026, 6, 1)

    def build(viewer, *, period: str = "koik"):
        return ReportingContext(
            viewer=viewer,
            period=parse_period(period, today),
            today=today,
            now=timezone.now(),
        )

    return build


@pytest.fixture
def break_glass(administrator):
    """An active break-glass grant for the `administrator` fixture.

    Since docs/adr/0181 an administrator reads no business content without one,
    so a queue decision that names a Matter cannot find the Matter without it
    («Valitud teemat ei leitud»). The queue's own gate is unchanged.
    """
    from datetime import timedelta

    from django.utils import timezone

    from app.accounts.models import BreakGlassGrant

    return BreakGlassGrant.objects.create(
        user=administrator,
        granted_by=factories.DepartmentHeadFactory(),
        reason="Arhiivi ülevaatus",
        starts_at=timezone.now() - timedelta(minutes=1),
        expires_at=timezone.now() + timedelta(hours=1),
    )


def candidate(batch, item, *, matter=None, state=OpinionCandidateState.PENDING, klass=None):
    return OpinionMatchCandidate.objects.create(
        item=item,
        matter=matter,
        batch=batch,
        match_class=klass or OpinionMatchClass.REVIEW_REQUIRED,
        state=state,
    )


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_the_queue_is_administrative_and_a_specialist_is_refused(client, specialist):
    client.force_login(specialist)
    response = client.get(reverse("legacy_import:opinion_queue"))
    assert response.status_code == 403


def test_an_administrator_may_open_the_queue(client, administrator):
    batch, item = catalogue()
    candidate(batch, item)
    client.force_login(grant_to(administrator))
    response = client.get(reverse("legacy_import:opinion_queue"))
    assert response.status_code == 200
    body = response.content.decode()
    # The card identifies the file by what it says it is and by its hash, which
    # is what a reviewer needs; the storage filename is not the identity.
    assert "Näidisarvamus" in body
    assert item.sha256[:12] in body


def test_a_decision_requires_a_post(client, administrator):
    batch, item = catalogue()
    row = candidate(batch, item)
    client.force_login(administrator)
    assert client.get(reverse("legacy_import:opinion_decide", args=[row.pk])).status_code == 405


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def test_linking_records_the_matter_without_claiming_the_letter_was_sent(
    client, administrator, break_glass, normal_matter
):
    batch, item = catalogue()
    row = candidate(batch, item, matter=normal_matter)
    client.force_login(administrator)

    client.post(
        reverse("legacy_import:opinion_decide", args=[row.pk]),
        {"decision": "link", "note": "Sünteetiline märkus"},
    )
    row.refresh_from_db()
    assert row.state == OpinionCandidateState.LINKED
    assert row.review_approves_submission is False
    assert row.decided_by_id == administrator.pk


def test_confirming_a_send_without_any_date_is_refused(
    client, administrator, break_glass, normal_matter
):
    batch, item = catalogue()
    row = candidate(batch, item, matter=normal_matter)
    client.force_login(administrator)

    client.post(
        reverse("legacy_import:opinion_decide", args=[row.pk]),
        {"decision": "confirm-sent"},
        follow=True,
    )
    row.refresh_from_db()
    assert row.state == OpinionCandidateState.PENDING
    assert row.review_approves_submission is False


def test_a_reviewed_date_is_recorded_as_a_reviewed_date(
    client, administrator, break_glass, normal_matter
):
    """Never as a register value: a person's judgement must stay legible as one."""
    batch, item = catalogue()
    row = candidate(batch, item, matter=normal_matter)
    client.force_login(administrator)

    client.post(
        reverse("legacy_import:opinion_decide", args=[row.pk]),
        {"decision": "confirm-sent", "sent_date": "2024-04-10"},
    )
    row.refresh_from_db()
    assert row.review_approves_submission is True
    assert row.reviewed_sent_date == datetime.date(2024, 4, 10)
    assert row.excel_sent_date is None


def test_deciding_twice_leaves_one_decision(client, administrator, break_glass, normal_matter):
    batch, item = catalogue()
    row = candidate(batch, item, matter=normal_matter)
    client.force_login(administrator)
    url = reverse("legacy_import:opinion_decide", args=[row.pk])

    client.post(url, {"decision": "link"})
    client.post(url, {"decision": "link"})
    row.refresh_from_db()
    assert row.state == OpinionCandidateState.LINKED
    assert OpinionMatchCandidate.objects.count() == 1


# ---------------------------------------------------------------------------
# Coverage metrics
# ---------------------------------------------------------------------------


def test_occurrences_and_distinct_binaries_are_reported_separately(context, specialist):
    batch, _first = catalogue(sha256="c" * 64, path="Opinions/uks.pdf")
    OpinionArchiveItem.objects.create(
        batch=batch,
        archive_sha256="a" * 64,
        archive_relative_path="Opinions/koopia/uks.pdf",
        original_filename="uks.pdf",
        sha256="c" * 64,
        size_bytes=1024,
        detected_type="application/pdf",
    )
    ctx = context(specialist)
    assert compute(keys.OPINION_ARCHIVE_OCCURRENCES, ctx).value == 2
    assert compute(keys.OPINION_ARCHIVE_DISTINCT_BINARIES, ctx).value == 1


def test_an_unresolved_file_is_reported_as_unresolved_not_as_a_missing_opinion(context, specialist):
    batch, item = catalogue()
    candidate(batch, item)
    ctx = context(specialist)

    assert compute(keys.OPINION_ARCHIVE_UNRESOLVED, ctx).value == 1
    assert compute(keys.OPINION_ARCHIVE_MATTER_COVERAGE, ctx).value == 0
    assert compute(keys.HISTORICAL_SUBMISSION_COVERAGE, ctx).value == 0


def test_matter_coverage_counts_a_settled_file(context, specialist, normal_matter):
    batch, item = catalogue()
    candidate(batch, item, matter=normal_matter, state=OpinionCandidateState.APPLIED)
    assert compute(keys.OPINION_ARCHIVE_MATTER_COVERAGE, context(specialist)).value == 100


def _withheld():
    import re

    from app.reporting.archive_access import ARCHIVE_WITHHELD, ArchiveMetricWithheld

    return pytest.raises(ArchiveMetricWithheld, match=re.escape(ARCHIVE_WITHHELD))


def test_a_candidate_naming_a_restricted_matter_counts_for_every_reader_and_no_outsider(
    context, specialist, reader, administrator
):
    """Since docs/adr/0181 nobody reads the archive and not the Matter.

    Every authorized reader (a READER too) reads both and counts the candidate
    as the owner does; the administrator without a grant reads neither, and every
    archive metric is withheld from it (tests/test_reporting_archive_access.py).
    """
    restricted = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    batch, item = catalogue()
    candidate(batch, item, matter=restricted)
    assert _sees(specialist, restricted) and _sees(reader, restricted)
    assert not _sees(administrator, restricted)

    owner = compute(keys.OPINION_ARCHIVE_UNRESOLVED, context(specialist)).value
    assert compute(keys.OPINION_ARCHIVE_UNRESOLVED, context(reader)).value == owner == 1
    with _withheld():
        compute(keys.OPINION_ARCHIVE_UNRESOLVED, context(administrator))


def _sees(user, matter) -> bool:
    from app.matters.models import Matter

    return Matter.objects.visible_to(user).filter(pk=matter.pk).exists()


@pytest.mark.parametrize("state", [OpinionCandidateState.APPLIED, OpinionCandidateState.LINKED])
def test_matter_coverage_counts_a_restricted_link_only_for_who_may_see_it(
    context, specialist, reader, administrator, state
):
    """AUD-044: the numerator is the reader's; the inventory is every archive reader's.

    Since docs/adr/0181 every archive reader (a READER too) reads the RESTRICTED
    Matter and gets the owner's figure; the administrator without a grant reads
    neither, and the whole metric is withheld from it (docs/adr/0175).
    """
    restricted = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    batch, item = catalogue()
    candidate(batch, item, matter=restricted, state=state)
    assert _sees(specialist, restricted) and _sees(reader, restricted)
    assert not _sees(administrator, restricted)

    participant = compute(keys.OPINION_ARCHIVE_MATTER_COVERAGE, context(specialist))
    colleague = compute(keys.OPINION_ARCHIVE_MATTER_COVERAGE, context(reader))

    assert (participant.value, participant.coverage_count) == (100, 1)
    assert (colleague.value, colleague.coverage_count) == (100, 1)
    assert participant.coverage_denominator == colleague.coverage_denominator == 1
    with _withheld():
        compute(keys.OPINION_ARCHIVE_MATTER_COVERAGE, context(administrator))


def test_matter_coverage_counts_a_file_once_however_many_matters_it_reached(context, specialist):
    batch, item = catalogue()
    for _ in range(2):
        candidate(
            batch,
            item,
            matter=factories.MatterFactory(owner=specialist),
            state=OpinionCandidateState.LINKED,
        )

    result = compute(keys.OPINION_ARCHIVE_MATTER_COVERAGE, context(specialist))

    assert (result.value, result.coverage_count) == (100, 1)


def test_historical_submission_coverage_counts_a_restricted_reconstruction_only_for_who_may_see_it(
    context, specialist, reader, administrator
):
    # docs/adr/0181: a READER gets the owner's figure; the administrator without a
    # grant has the whole archive metric withheld.
    from app.legacy_import.opinion_archive import OpinionSubmissionImport

    restricted = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    batch, item = catalogue()
    submission = sent_submission(restricted, sent=datetime.date(2024, 4, 10))
    OpinionSubmissionImport.objects.create(item=item, batch=batch, submission=submission)

    assert _sees(specialist, restricted) and _sees(reader, restricted)
    assert not _sees(administrator, restricted)

    participant = compute(keys.HISTORICAL_SUBMISSION_COVERAGE, context(specialist))
    colleague = compute(keys.HISTORICAL_SUBMISSION_COVERAGE, context(reader))

    assert (participant.value, participant.coverage_count) == (100, 1)
    assert (colleague.value, colleague.coverage_count) == (100, 1)
    assert participant.coverage_denominator == colleague.coverage_denominator == 1
    with _withheld():
        compute(keys.HISTORICAL_SUBMISSION_COVERAGE, context(administrator))


# ---------------------------------------------------------------------------
# The submission metrics Stage 2H unlocks
# ---------------------------------------------------------------------------


def sent_submission(matter, *, sent: datetime.date, precision=SentAtPrecision.DATE):
    document = factories.DocumentFactory(matter=matter)
    from app.documents.services import add_evidence_version

    version = add_evidence_version(
        document=document,
        content=f"%PDF-1.4\n{matter.pk}{sent}".encode(),
        original_filename="naidis.pdf",
        mime_type="application/pdf",
    )
    return factories.SubmissionFactory(
        matter=matter,
        status=SubmissionStatus.SENT,
        sent_at=datetime.datetime.combine(sent, datetime.time(0, 0), tzinfo=datetime.UTC),
        sent_at_precision=precision,
        final_version=version,
    )


def test_a_reconstructed_submission_is_counted(context, specialist, normal_matter):
    sent_submission(normal_matter, sent=datetime.date(2024, 4, 10))
    assert compute(keys.SUBMISSIONS_SENT, context(specialist)).value == 1


def test_only_addressees_count_towards_the_recipient_metric(
    context, specialist, normal_matter, organisation
):
    submission = sent_submission(normal_matter, sent=datetime.date(2024, 4, 10))
    other = factories.OrganisationFactory()
    SubmissionRecipient.objects.create(
        submission=submission, organisation=organisation, role=RecipientRole.ADDRESSEE
    )
    SubmissionRecipient.objects.create(
        submission=submission, organisation=other, role=RecipientRole.FOR_INFORMATION
    )
    result = compute(keys.SUBMISSIONS_BY_RECIPIENT, context(specialist))
    labels = {segment.label for segment in result.segments}
    assert organisation.name in labels
    assert other.name not in labels


def test_recipient_coverage_counts_a_submission_without_a_resolved_addressee_as_uncovered(
    context, specialist, normal_matter, organisation
):
    with_addressee = sent_submission(normal_matter, sent=datetime.date(2024, 4, 10))
    SubmissionRecipient.objects.create(
        submission=with_addressee, organisation=organisation, role=RecipientRole.ADDRESSEE
    )
    sent_submission(normal_matter, sent=datetime.date(2024, 5, 11))

    assert compute(keys.SUBMISSION_RECIPIENT_COVERAGE, context(specialist)).value == 50


def test_the_submission_metrics_declare_a_new_version(context, specialist, normal_matter):
    """Stage 2H changed what these numbers mean, so the version had to move."""
    sent_submission(normal_matter, sent=datetime.date(2024, 4, 10))
    for key in (
        keys.SUBMISSIONS_SENT,
        keys.SUBMISSIONS_SENT_BY_PERIOD,
        keys.SUBMISSIONS_BY_RECIPIENT,
        keys.SUBMISSIONS_BY_KIND,
        keys.MATTERS_BY_SUBMISSION_COUNT,
        keys.MATTERS_WITH_MULTIPLE_SUBMISSIONS,
    ):
        assert compute(key, context(specialist)).definition_version == 2


def test_the_era_note_names_the_real_coverage_rather_than_denying_measurement(
    context, specialist, normal_matter
):
    sent_submission(normal_matter, sent=datetime.date(2024, 4, 10))
    result = compute(keys.SUBMISSIONS_SENT, context(specialist))
    note = result.definition.source_era_limitations_et
    assert "2020" in note
    assert "mõõtmist ei ole" in note
    assert "ei tähenda, et arvamusi ei saadetud" in note


# ---------------------------------------------------------------------------
# The historical date, as rendered
# ---------------------------------------------------------------------------


def test_a_date_only_submission_never_renders_an_invented_time(client, specialist, normal_matter):
    submission = sent_submission(normal_matter, sent=datetime.date(2024, 4, 10))
    client.force_login(specialist)
    response = client.get(reverse("matters:matter_documents", args=[normal_matter.pk]))
    body = response.content.decode()

    assert "10.4.2024" in body
    # The guard has to follow the format it guards: with the date rendered
    # `10.4.2024`, an invented midnight would read `10.4.2024 00:00`, and the
    # zero-padded spelling would no longer appear whether the bug was there or
    # not (app/core/dates.py).
    assert "10.4.2024 00:00" not in body
    assert "10.04.2024" not in body
    assert submission.sent_at_precision == SentAtPrecision.DATE


@pytest.mark.parametrize("decision", ["link", "confirm-sent"])
def test_an_archive_file_is_never_settled_on_a_test_matter(
    client, administrator, break_glass, decision
):
    """QA-STATS-04: the archive is real correspondence; a TEST Matter is not its home.

    Settling a letter on a development record put real evidence on it and
    counted the test as archive coverage. The refusal names itself, and the
    candidate stays in the queue untouched.
    """
    from django.contrib.messages import get_messages

    from app.legacy_import.opinion_views import TEST_MATTER_REFUSED
    from app.matters.enums import MatterDataClass

    test_matter = factories.MatterFactory(data_class=MatterDataClass.TEST)
    batch, item = catalogue()
    row = candidate(batch, item, matter=test_matter)
    client.force_login(administrator)

    response = client.post(
        reverse("legacy_import:opinion_decide", args=[row.pk]),
        {"decision": decision, "sent_date": "2024-04-10"},
    )
    assert [str(m) for m in get_messages(response.wsgi_request)] == [TEST_MATTER_REFUSED]
    row.refresh_from_db()
    assert row.state == OpinionCandidateState.PENDING
    assert row.review_approves_submission is False
