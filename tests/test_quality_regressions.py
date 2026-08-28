"""Regressions from the final full-codebase quality pass.

Four defects, each found by asking a question of the integrated product rather
than of the feature that introduced it. They have nothing in common except the
shape of their cause: a rule that already existed somewhere, re-derived locally
in a place that did not know about it.

Every test here fails on the commit before its fix.
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse

from app.core.enums import Visibility
from app.documents.services import add_evidence_version, create_document
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent,
)
from tests import factories

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4 synthetic quality-pass evidence"
PLAIN = "application/pdf"


# ---------------------------------------------------------------------------
# P1 — evidence a reader may not see cannot be bound to their submission
# ---------------------------------------------------------------------------


@pytest.fixture
def restricted_evidence(normal_matter, department_head, evidence_root):
    """A document inside an ordinary Matter that not everyone may read.

    The interesting shape, and a legitimate one: a child override may restrict
    further than its Matter, so "can open the Matter" never implies "can open
    everything in it".

    The reader who must not see it is `other_specialist`, not `specialist`:
    `normal_matter` is owned by the latter, and participation in a Matter
    unlocks its restricted children by design. Writing the test the other way
    round asserted nothing — which the premise test below caught.
    """
    document = create_document(
        matter=normal_matter,
        title="Liikme konfidentsiaalne lisa",
        created_by=department_head,
        visibility_override=Visibility.RESTRICTED,
    )
    return add_evidence_version(
        document=document,
        content=PDF,
        original_filename="konfidentsiaalne-lisa.pdf",
        mime_type=PLAIN,
        uploaded_by=department_head,
    )


def test_the_restricted_document_really_is_invisible_to_the_reader(
    restricted_evidence, specialist, reader, department_head
):
    """The fixture's premise, asserted rather than assumed.

    Without this the interesting test could pass because the document was
    readable all along — which is exactly what happened the first time this was
    written against the Matter's own owner.
    """
    from app.documents.models import Document

    assert Document.objects.visible_to(reader).count() == 0
    # The owner participates, so they do see it. Stated here so the next reader
    # does not "simplify" the test back to the specialist who owns the Matter.
    assert Document.objects.visible_to(specialist).count() == 1
    assert Document.objects.visible_to(department_head).count() == 1


def test_a_specialist_cannot_bind_evidence_they_may_not_read(
    client, reader, normal_matter, restricted_evidence
):
    """The post the interface never offers, which is not the same as refused.

    Selecting existing evidence was filtered by Matter alone. A Matter the
    reader can open may hold a document they cannot, so the filter admitted one
    — and the submission card then printed its filename, size and SHA-256 to
    everybody who could see the submission.
    """
    from app.documents.models import Document, DocumentVersion

    submission = create_submission(matter=normal_matter, title="Arvamus", actor=reader)

    # The two halves of the difference, pinned rather than assumed. The rule
    # this view used to apply would have found the version; the rule it applies
    # now does not. Without both assertions the test below could pass because
    # the version was unreachable for some unrelated reason.
    assert DocumentVersion.objects.filter(document__matter=normal_matter).contains(
        restricted_evidence
    )
    assert not DocumentVersion.objects.filter(
        document__in=Document.objects.visible_to(reader).filter(matter=normal_matter)
    ).contains(restricted_evidence)

    client.force_login(reader)
    response = client.post(
        reverse("submissions:attach_evidence", kwargs={"pk": submission.pk}),
        {"existing_version": str(restricted_evidence.pk)},
    )

    assert response.status_code == 404
    submission.refresh_from_db()
    assert submission.final_version_id is None


def test_a_department_head_may_still_select_what_they_can_read(
    client, department_head, normal_matter, restricted_evidence
):
    """The fix is a visibility filter, not a new prohibition."""
    submission = create_submission(matter=normal_matter, title="Arvamus", actor=department_head)

    client.force_login(department_head)
    client.post(
        reverse("submissions:attach_evidence", kwargs={"pk": submission.pk}),
        {"existing_version": str(restricted_evidence.pk)},
        follow=True,
    )

    submission.refresh_from_db()
    assert submission.final_version_id == restricted_evidence.pk


def test_ordinary_evidence_selection_is_unaffected(
    client, specialist, normal_matter, evidence_root
):
    document = create_document(matter=normal_matter, title="Saadetud kiri", created_by=specialist)
    version = add_evidence_version(
        document=document,
        content=PDF,
        original_filename="kiri.pdf",
        mime_type=PLAIN,
        uploaded_by=specialist,
    )
    submission = create_submission(matter=normal_matter, title="Arvamus", actor=specialist)

    client.force_login(specialist)
    client.post(
        reverse("submissions:attach_evidence", kwargs={"pk": submission.pk}),
        {"existing_version": str(version.pk)},
        follow=True,
    )

    submission.refresh_from_db()
    assert submission.final_version_id == version.pk


# ---------------------------------------------------------------------------
# P2 — a finished opinion candidate is not re-decided
# ---------------------------------------------------------------------------


@pytest.fixture
def archive_candidate(db):
    """One catalogued occurrence and a proposal on it."""
    from app.legacy_import.opinion_archive import (
        OpinionArchiveBatch,
        OpinionArchiveItem,
        OpinionMatchCandidate,
    )
    from app.legacy_import.opinion_enums import OpinionMatchClass

    batch = OpinionArchiveBatch.objects.create(
        archive_sha256="a" * 64,
        importer_version="test/0",
        started_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    item = OpinionArchiveItem.objects.create(
        batch=batch,
        archive_sha256="a" * 64,
        archive_relative_path="Opinions/naidis.pdf",
        original_filename="naidis.pdf",
        sha256="b" * 64,
        size_bytes=512,
        detected_type="application/pdf",
        filename_date=datetime.date(2024, 4, 10),
        filename_recipient="Naidisministeerium",
        filename_title="Naidisarvamus",
    )

    def build(*, state, matter=None):
        return OpinionMatchCandidate.objects.create(
            item=item,
            matter=matter,
            batch=batch,
            match_class=OpinionMatchClass.REVIEW_REQUIRED,
            state=state,
        )

    return build


@pytest.mark.parametrize("state", ["APPLIED", "SUPERSEDED"])
def test_a_finished_candidate_is_not_re_decided(
    client, administrator, archive_candidate, normal_matter, state
):
    """APPLIED is named by a canonical Submission; SUPERSEDED is history.

    The queue renders decision controls only on a PENDING row, so nothing in
    the interface offered this — which is not a guard. A decision written over
    either leaves the provenance chain describing something untrue.
    """
    candidate = archive_candidate(state=state, matter=normal_matter)

    client.force_login(administrator)
    response = client.post(
        reverse("legacy_import:opinion_decide", kwargs={"pk": candidate.pk}),
        {"decision": "reject"},
        follow=True,
    )

    candidate.refresh_from_db()
    assert candidate.state == state
    assert candidate.decided_by_id is None
    assert "ei saa uuesti" in response.content.decode()


def test_a_reviewer_may_still_correct_their_own_earlier_answer(
    client, administrator, archive_candidate, normal_matter
):
    """The guard is about finished rows, not about changing one's mind."""
    from app.legacy_import.opinion_enums import OpinionCandidateState

    candidate = archive_candidate(state=OpinionCandidateState.REJECTED, matter=normal_matter)

    client.force_login(administrator)
    client.post(
        reverse("legacy_import:opinion_decide", kwargs={"pk": candidate.pk}),
        {"decision": "defer"},
        follow=True,
    )

    candidate.refresh_from_db()
    assert candidate.state == OpinionCandidateState.DEFERRED


def test_a_pending_candidate_is_still_decidable(
    client, administrator, archive_candidate, normal_matter
):
    from app.legacy_import.opinion_enums import OpinionCandidateState

    candidate = archive_candidate(state=OpinionCandidateState.PENDING, matter=normal_matter)

    client.force_login(administrator)
    client.post(
        reverse("legacy_import:opinion_decide", kwargs={"pk": candidate.pk}),
        {"decision": "reject"},
        follow=True,
    )

    candidate.refresh_from_db()
    assert candidate.state == OpinionCandidateState.REJECTED


# ---------------------------------------------------------------------------
# P2 — a link a canonical Submission stands on is not withdrawn from the archive
# ---------------------------------------------------------------------------


@pytest.fixture
def filed_letter(db, normal_matter, department_head, evidence_root):
    """An archive binary, a reviewed link, and the Submission filed from it."""
    from django.utils import timezone

    from app.legacy_import.opinion_archive import (
        OpinionArchiveBatch,
        OpinionArchiveItem,
        OpinionSubmissionImport,
    )
    from app.legacy_import.opinion_binary import OpinionArchiveBinary
    from app.legacy_import.opinion_enums import ArchiveLinkBasis
    from app.legacy_import.opinion_links import link_matter

    batch = OpinionArchiveBatch.objects.create(
        archive_sha256="a" * 64,
        importer_version="test/0",
        started_at=timezone.now(),
    )
    binary = OpinionArchiveBinary.objects.create(
        sha256="c" * 64,
        size_bytes=512,
        mime_type="application/pdf",
        storage_key="opinion-archive/cc/cc/" + "c" * 64,
        source_archive_sha256="a" * 64,
        materialized_at=timezone.now(),
    )
    item = OpinionArchiveItem.objects.create(
        batch=batch,
        archive_sha256="a" * 64,
        archive_relative_path="Opinions/filed.pdf",
        original_filename="filed.pdf",
        sha256=binary.sha256,
        size_bytes=512,
        detected_type="application/pdf",
        binary=binary,
    )
    # Reviewed first, filed second — the order that produces the defect. A
    # person's decision is never downgraded to a derived basis, so the link
    # keeps saying REVIEWED after the Submission exists.
    link_matter(
        binary=binary,
        matter=normal_matter,
        basis=ArchiveLinkBasis.REVIEWED,
        actor=department_head,
    )
    # Sent the way the application sends: a SENT row requires a timestamp and
    # final evidence, and the database says so. Forcing the status with an
    # `update()` produced a fixture the constraint rejected — the test would
    # then have "passed" on an error raised before the guard it was written for.
    submission = create_submission(
        matter=normal_matter, title="Taastatud arvamus", actor=department_head
    )
    attach_final_evidence(
        submission=submission,
        content=PDF,
        original_filename="taastatud.pdf",
        mime_type=PLAIN,
        actor=department_head,
    )
    mark_submission_sent(submission=submission, actor=department_head)
    OpinionSubmissionImport.objects.create(
        item=item, submission=submission, batch=batch, created_submission=True
    )
    assert Submission.objects.get(pk=submission.pk).status == SubmissionStatus.SENT
    return binary


def test_a_link_a_submission_stands_on_cannot_be_withdrawn(
    filed_letter, normal_matter, department_head
):
    from app.legacy_import.opinion_binary import OpinionArchiveMatterLink
    from app.legacy_import.opinion_enums import ArchiveLinkBasis
    from app.legacy_import.opinion_links import PROTECTED_BASES, LinkRefused, unlink_matter

    # The reason the basis check missed this one, pinned: a person's decision is
    # never downgraded to a derived basis, so the link that the Submission rests
    # on is precisely the link the old guard would have let go.
    link = OpinionArchiveMatterLink.objects.get()
    assert link.basis == ArchiveLinkBasis.REVIEWED
    assert link.basis not in PROTECTED_BASES

    with pytest.raises(LinkRefused):
        unlink_matter(binary=filed_letter, matter=normal_matter, actor=department_head)
    assert OpinionArchiveMatterLink.objects.count() == 1


def test_a_reviewed_link_with_no_submission_is_still_withdrawable(
    db, normal_matter, department_head, evidence_root
):
    """The guard asks the register, and the register has nothing to say here."""
    from django.utils import timezone

    from app.legacy_import.opinion_binary import OpinionArchiveBinary, OpinionArchiveMatterLink
    from app.legacy_import.opinion_enums import ArchiveLinkBasis
    from app.legacy_import.opinion_links import link_matter, unlink_matter

    binary = OpinionArchiveBinary.objects.create(
        sha256="d" * 64,
        size_bytes=512,
        mime_type="application/pdf",
        storage_key="opinion-archive/dd/dd/" + "d" * 64,
        source_archive_sha256="a" * 64,
        materialized_at=timezone.now(),
    )
    link_matter(
        binary=binary,
        matter=normal_matter,
        basis=ArchiveLinkBasis.REVIEWED,
        actor=department_head,
    )
    unlink_matter(binary=binary, matter=normal_matter, actor=department_head)
    assert OpinionArchiveMatterLink.objects.count() == 0


# ---------------------------------------------------------------------------
# P2 — indexed text that has gone stale is reported
# ---------------------------------------------------------------------------


def test_renaming_an_organisation_is_reported_as_stale_index(db, specialist):
    """The gap `app/search/signals.py` documents, made visible.

    Fanning out from a rename would reindex the corpus inside a form
    submission, so the design defers it to `rebuild_search_index`. Nothing told
    an operator the rebuild was owed, which turned a deferred cost into a silent
    one: every structural check passes on a row whose text is simply wrong.
    """
    from app.matters.models import Matter
    from app.organisations.models import Organisation
    from app.search.indexing import refresh_matters
    from app.search.management.commands.check_search_integrity import build_report

    organisation = factories.OrganisationFactory(name="Näidisministeerium")
    matter = factories.MatterFactory(owner=specialist, source_organisations=[organisation])
    refresh_matters(Matter.objects.filter(pk=matter.pk))

    assert build_report().ok, "a freshly indexed corpus is not stale"

    # A rename reaches no signal by design; the index still says the old name.
    Organisation.objects.filter(pk=organisation.pk).update(name="Kliimaministeerium")

    report = build_report()
    assert not report.ok
    assert any(finding.label == "Vananenud tekst" for finding in report.findings)


def test_a_rebuild_clears_the_staleness(db, specialist):
    from app.matters.models import Matter
    from app.organisations.models import Organisation
    from app.search.indexing import rebuild_all, refresh_matters
    from app.search.management.commands.check_search_integrity import build_report

    organisation = factories.OrganisationFactory(name="Näidisministeerium")
    matter = factories.MatterFactory(owner=specialist, source_organisations=[organisation])
    refresh_matters(Matter.objects.filter(pk=matter.pk))
    Organisation.objects.filter(pk=organisation.pk).update(name="Kliimaministeerium")

    rebuild_all()
    assert build_report().ok


def test_the_drift_check_can_be_skipped(db, specialist):
    """`--drift-sample 0` is the escape hatch for a very large corpus."""
    from app.matters.models import Matter
    from app.organisations.models import Organisation
    from app.search.indexing import refresh_matters
    from app.search.management.commands.check_search_integrity import build_report

    organisation = factories.OrganisationFactory(name="Näidisministeerium")
    matter = factories.MatterFactory(owner=specialist, source_organisations=[organisation])
    refresh_matters(Matter.objects.filter(pk=matter.pk))
    Organisation.objects.filter(pk=organisation.pk).update(name="Kliimaministeerium")

    assert build_report(sample=0).ok


def test_the_indexer_and_the_check_share_one_composition(db, specialist):
    """Two copies of "what text represents a Matter" would drift apart.

    The check would then report on a rule the indexer no longer follows, which
    is worse than not checking.
    """
    from app.matters.models import Matter
    from app.search.indexing import indexed_text_for, refresh_matters
    from app.search.models import SearchDocument, SearchSourceKind

    matter = factories.MatterFactory(owner=specialist, title="Ehitusseadustiku muutmine")
    refresh_matters(Matter.objects.filter(pk=matter.pk))

    row = SearchDocument.objects.get(source_kind=SearchSourceKind.MATTER, matter=matter)
    for field, value in indexed_text_for(matter).items():
        assert getattr(row, field) == value
