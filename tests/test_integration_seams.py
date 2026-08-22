"""What only holds once evidence, search, opinions and recovery are in one tree.

Four branches hardened four subsystems in parallel, and each one tested itself.
The failures this file is about are the ones neither branch could have written:
they need two subsystems' changes present at once, and each would be invisible
from inside the branch that caused it.

* Extraction now re-asserts its claim before it writes anything, and search now
  refreshes the document inside that same publish. Both are right alone. What
  has to be true together is that the fence and the refresh sit on the *same*
  side of the transaction: a worker that publishes makes its text findable, and
  a worker that lost its claim changes neither the derivative nor the index.
* The opinion importer now refuses to overturn a person, and search now
  reindexes a submission when its recipients are established. The seam is the
  historical submission — it must become findable under the ministry it was
  sent to, and a rerun after a review decision must leave both the register and
  the index exactly where the reviewer left them.
* Recovery now fingerprints canonical state, and evidence now has its own
  integrity check. They answer different questions about the same objects and
  must agree about what evidence is; and the current register, which is derived,
  must be untouched by rebuilding the other derived projection beside it.

**Lock ordering, since two branches added locking.** Every business path takes
its row lock first and the search advisory lock second: extraction's ``_settle``
performs the conditional UPDATE that locks the ``DocumentVersion`` row, and the
``refresh_document_version`` in the same transaction then takes the shared
advisory lock. Nothing runs the other way. ``rebuild_all`` takes the exclusive
advisory lock and afterwards writes only ``SearchDocument``; it holds no
business row lock a refresh could be waiting on, and it reads canonical tables
with plain ``SELECT``s, which take no row locks at all. There is no cycle to
break, so no ordering is changed here (``app/search/indexing.py``,
``app/documents/extraction/orchestrator.py``).

Synthetic data only. No archive file, register row, person or ministry that
appears here is real.
"""

from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from app.documents.enums import DerivativeKind, DerivativeStatus, ExtractionState
from app.documents.extraction.orchestrator import (
    CLAIM_LOST,
    claim_version,
    extract_document_version,
)
from app.documents.integrity import INTEGRITY_FAILURES, check_evidence
from app.documents.models import DocumentDerivative, DocumentVersion
from app.legacy_import.current_state import CurrentRegisterState, RegisterCurrency
from app.legacy_import.final_cutover import apply_cutover_plan, build_cutover_plan
from app.legacy_import.opinion_apply import apply_plan, open_batch
from app.legacy_import.opinion_archive import (
    OpinionMatchCandidate,
    OpinionSubmissionImport,
)
from app.legacy_import.opinion_enums import OpinionCandidateState
from app.legacy_import.opinion_plan import build_plan
from app.organisations.models import Organisation, OrganisationType
from app.search.indexing import rebuild_all
from app.search.models import SearchDocument, SearchSourceKind
from app.search.services import search
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from tests import synthetic_corpus as corpus
from tests import synthetic_opinions as syn
from tests.synthetic_cutover import CURRENT_DRAFTING, CURRENT_SENT, FINAL_SNAPSHOT, build_world
from tests.test_opinion_apply_state import register_matter

pytestmark = pytest.mark.django_db

PDF = "application/pdf"
MINISTRY = "Näidisministeerium"


def fragments_of(version: DocumentVersion):
    return SearchDocument.objects.filter(
        source_kind=SearchSourceKind.DOCUMENT_FRAGMENT, document_version=version
    )


@pytest.fixture
def pdf_version(normal_matter, capture_evidence):
    return capture_evidence(normal_matter, corpus.government_pdf(), "kaaskiri.pdf", PDF)


def steal_the_claim(version: DocumentVersion, settings) -> DocumentVersion:
    """Age the claim past the window and take it, as a healthy second worker does.

    No OCR job in a test runs for half an hour, so the elapsed time is arranged
    rather than waited for. Nothing else is simulated: the reclaim goes through
    the real ``claim_version``.
    """
    settings.EXTRACTION_STALE_CLAIM_MINUTES = 30
    DocumentVersion.objects.filter(pk=version.pk).update(
        extraction_claimed_at=timezone.now() - timedelta(minutes=31)
    )
    stolen = claim_version(version.pk)
    assert stolen is not None, "the second worker should have been able to reclaim it"
    return stolen


# =========================================================================
# A. Extraction publishes, and the text becomes findable in the same commit
#
# The refresh is inside the publish transaction, after the fence and after the
# derivative. Being *inside* is what makes "committed derivative, no search row"
# impossible; being *after the fence* is section B.
# =========================================================================


def test_a_published_derivative_is_searchable_in_the_same_breath(pdf_version, specialist) -> None:
    worker = claim_version(pdf_version.pk)
    assert worker is not None

    report = extract_document_version(worker)

    assert report.state == ExtractionState.DONE
    assert DocumentDerivative.objects.filter(
        version=pdf_version,
        kind=DerivativeKind.EXTRACTED_TEXT,
        status=DerivativeStatus.ACTIVE,
    ).exists()
    assert fragments_of(pdf_version).exists(), (
        "the derivative committed without its search rows: text that exists and cannot be found"
    )
    results = search(query=corpus.ONLY_ON_PDF_PAGE_4, user=specialist)
    assert any(result.document_version_id == pdf_version.pk for result in results)


# =========================================================================
# B. A worker that lost its claim mutates neither store
#
# The fence is re-asserted first inside the publish transaction, so the whole
# publish — derivative, attachments, and the search refresh at the end of it —
# unwinds together. Asserting only the derivative would miss the half that used
# to be written last.
# =========================================================================


def test_a_lost_claim_leaves_the_search_index_untouched(pdf_version, settings, specialist) -> None:
    slow_worker = claim_version(pdf_version.pk)
    assert slow_worker is not None
    steal_the_claim(pdf_version, settings)

    report = extract_document_version(slow_worker)

    assert report.state == CLAIM_LOST
    assert not DocumentDerivative.objects.filter(version=pdf_version).exists()
    assert not fragments_of(pdf_version).exists(), (
        "a pass that owns nothing indexed a derivative that does not exist"
    )
    assert not search(query=corpus.ONLY_ON_PDF_PAGE_4, user=specialist)


def test_the_worker_that_owns_the_claim_still_publishes_and_indexes(
    pdf_version, settings, specialist
) -> None:
    """The other half: a lost claim must not poison the row for its owner."""
    slow_worker = claim_version(pdf_version.pk)
    assert slow_worker is not None
    owner = steal_the_claim(pdf_version, settings)

    extract_document_version(slow_worker)
    report = extract_document_version(owner)

    assert report.state == ExtractionState.DONE
    assert fragments_of(pdf_version).exists()
    assert any(
        result.document_version_id == pdf_version.pk
        for result in search(query=corpus.ONLY_ON_PDF_PAGE_4, user=specialist)
    )


# =========================================================================
# C, D, E. The historical opinion importer, against the index
#
# The archive does not go through `set_recipients`; it uses `get_or_create` on
# one `SubmissionRecipient`, which does send `post_save` and does reach the
# search handler. That is a legitimate second path, and it is asserted rather
# than assumed: the failure it would otherwise produce is a canonical SENT
# opinion that no search for its ministry can find, which is the whole point of
# importing the archive.
# =========================================================================


@pytest.fixture
def ministry():
    return Organisation.objects.create(name=MINISTRY, organisation_type=OrganisationType.MINISTRY)


@pytest.fixture
def archive_path(tmp_path):
    def build(opinions):
        return syn.write_archive(tmp_path / "Opinions.zip", opinions)

    return build


def strict_pair(number: int):
    """A register row and an archive file matching on three exact signals."""
    matter = register_matter(
        year=2024,
        number=number,
        title="Näidisregistri seaduse muutmise seadus",
        sent="2024-04-10",
        counterparty=MINISTRY,
    )
    item = syn.opinion(
        date="2024-04-10",
        recipient=MINISTRY,
        title="Arvamus näidisregistri seaduse muutmise kohta",
        marker=f"seam-{number}",
    )
    return matter, item


def submission_hits(query: str, user) -> list:
    return [result for result in search(query=query, user=user) if result.submission_id]


def test_an_archived_opinion_is_findable_under_the_ministry_it_was_sent_to(
    archive_path, ministry, specialist
) -> None:
    matter, item = strict_pair(301)
    plan = build_plan(archive_path=archive_path([item]), kodadash_path=None)
    apply_plan(plan, batch=open_batch(plan))

    submission = Submission.objects.get(matter=matter)
    assert submission.status == SubmissionStatus.SENT
    assert submission.recipients.filter(pk=ministry.pk).exists()
    assert any(
        result.submission_id == submission.pk for result in submission_hits(MINISTRY, specialist)
    ), "a canonical SENT opinion that no search for its addressee can find"


def test_a_failed_evidence_write_leaves_no_record_in_either_store(
    archive_path, ministry, specialist, monkeypatch
) -> None:
    """Evidence first; if evidence fails there is no record of any kind.

    The filesystem caveat stands and is not contradicted here: an outer
    rollback can still leave a recoverable orphan object behind, which
    ``prune_orphaned_evidence`` reclaims. What must not survive is anything a
    reader would take for a decision — a Submission, an import row, an APPLIED
    candidate, or a search result.
    """
    from app.legacy_import import opinion_apply

    matter, item = strict_pair(302)
    monkeypatch.setattr(opinion_apply, "_final_version_for", lambda *a, **k: (None, False))

    plan = build_plan(archive_path=archive_path([item]), kodadash_path=None)
    apply_plan(plan, batch=open_batch(plan))

    assert not Submission.objects.filter(matter=matter).exists()
    assert not OpinionSubmissionImport.objects.exists()
    assert OpinionMatchCandidate.objects.get(matter=matter).state == OpinionCandidateState.PENDING
    assert not submission_hits(MINISTRY, specialist)


def test_deleting_an_opinion_that_has_a_recipient_actually_deletes_it(
    archive_path, ministry, specialist
) -> None:
    """The operator flow the review queue is built around, with a real ministry.

    Deleting a wrong Submission is what a reviewer does before recording why in
    the queue, and it used to fail — but only once the recipient resolved to an
    Organisation, which is every real ministry and no synthetic one. The
    cascade deletes the search rows first, then fires the recipient handler
    while the Submission row is still there, and the refresh reinserts a row
    nothing will collect. The foreign key is deferred, so the error arrives at
    COMMIT and takes the delete with it.

    Both halves are asserted: the delete succeeds, and it leaves no row behind
    for the constraint to trip over at the end of the transaction.
    """
    matter, item = strict_pair(304)
    plan = build_plan(archive_path=archive_path([item]), kodadash_path=None)
    apply_plan(plan, batch=open_batch(plan))
    submission = Submission.objects.get(matter=matter)
    assert submission.recipients.filter(pk=ministry.pk).exists(), "no recipient, no regression"

    Submission.objects.filter(pk=submission.pk).delete()

    assert not Submission.objects.filter(pk=submission.pk).exists()
    assert not SearchDocument.objects.filter(submission_id=submission.pk).exists()
    assert not submission_hits(MINISTRY, specialist)
    # The deferred constraint is only checked at COMMIT, and a test transaction
    # never commits. Asking PostgreSQL to check now is what makes this test
    # detect the failure a request would have hit rather than a tidier symptom.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_replacing_recipients_still_reindexes(archive_path, ministry, specialist) -> None:
    """The direction the guard must not break.

    `set_recipients` deletes the recipient rows and bulk-creates the new ones,
    and that deletion *does* originate at the recipients, so the submission
    survives and has to be reprojected. A guard written one step too wide would
    make an opinion unfindable under the ministry it was just addressed to, and
    nothing would say so.
    """
    from app.submissions.services import set_recipients

    matter, item = strict_pair(306)
    plan = build_plan(archive_path=archive_path([item]), kodadash_path=None)
    apply_plan(plan, batch=open_batch(plan))
    submission = Submission.objects.get(matter=matter)
    other = Organisation.objects.create(
        name="Näidiskliimaministeerium", organisation_type=OrganisationType.MINISTRY
    )

    set_recipients(submission=submission, addressees=[other], audit=False)

    assert submission_hits("Näidiskliimaministeerium", specialist), (
        "the new recipient did not reach the index"
    )
    assert not submission_hits(MINISTRY, specialist), "the old recipient stayed findable"


@pytest.mark.parametrize(
    "decision",
    [
        OpinionCandidateState.REJECTED,
        OpinionCandidateState.DUPLICATE,
        OpinionCandidateState.NOT_AN_OPINION,
        OpinionCandidateState.DEFERRED,
    ],
)
def test_a_rerun_resurrects_neither_the_submission_nor_its_search_row(
    archive_path, ministry, specialist, administrator, decision
) -> None:
    """A person answered; a cron job must not answer again, in either store.

    The shape that loses a decision: somebody applied the archive, saw the
    result was wrong, deleted the Submission — which takes the import row with
    it — and recorded why in the queue. Nothing in the sources changed, so the
    next run proposes exactly the same automatic match.
    """
    matter, item = strict_pair(303)
    archive = archive_path([item])
    first = build_plan(archive_path=archive, kodadash_path=None)
    apply_plan(first, batch=open_batch(first))

    Submission.objects.filter(matter=matter).delete()
    OpinionMatchCandidate.objects.filter(matter=matter).update(
        state=decision,
        review_approves_submission=False,
        decided_by=administrator,
        decided_at=timezone.now(),
    )
    assert not submission_hits(MINISTRY, specialist), "the deleted submission is out of the index"
    # The submission rows specifically, rather than every row in the table. The
    # claim is about resurrection, and a projection that legitimately refreshes
    # a Matter row would change primary keys without changing what is findable.
    before = set(
        SearchDocument.objects.filter(source_kind=SearchSourceKind.SUBMISSION).values_list(
            "source_object_id", flat=True
        )
    )
    assert before == set(), "the deleted submission left a search row behind"

    second = build_plan(archive_path=archive, kodadash_path=None)
    apply_plan(second, batch=open_batch(second))

    assert not Submission.objects.filter(matter=matter).exists(), (
        f"a rerun overturned a reviewer's {decision} and filed a canonical record"
    )
    assert not OpinionSubmissionImport.objects.exists()
    assert OpinionMatchCandidate.objects.get(matter=matter).state == decision
    assert (
        set(
            SearchDocument.objects.filter(source_kind=SearchSourceKind.SUBMISSION).values_list(
                "source_object_id", flat=True
            )
        )
        == before
    )
    assert not submission_hits(MINISTRY, specialist)


# =========================================================================
# F. Recovery tooling, evidence integrity and the search rebuild
#
# The real backup and restore run in CI's rehearsal job, against the production
# scripts. What is asserted here is the part that is about the code: the two
# integrity commands agree about the objects they both look at, and the rebuild
# the runbook performs after a restore brings the projection back.
# =========================================================================


def test_the_structural_evidence_check_is_clean_on_a_healthy_store(pdf_version, extract) -> None:
    extract(pdf_version)

    report = check_evidence(verify_sha=False, scan_storage=True)

    assert report.versions_checked >= 1
    assert [finding for finding in report.findings if finding.kind in INTEGRITY_FAILURES] == []


def test_the_two_integrity_commands_agree_about_the_same_evidence(pdf_version) -> None:
    """They are not interchangeable, and they must not disagree.

    ``recovery_fingerprint`` answers "is this the same canonical state as the
    one I fingerprinted before", which needs an earlier fingerprint to mean
    anything. ``check_evidence_integrity`` answers "does the live store match
    the live database right now", which needs nothing but the deployment — and
    finds orphans, foreign current versions and stuck extractions that a
    fingerprint comparison is not looking for. What they must share is the set
    of objects: both walk every ``DocumentVersion``.
    """
    out = StringIO()
    call_command("recovery_fingerprint", stdout=out)
    fingerprint = json.loads(out.getvalue())

    report = check_evidence(verify_sha=True, scan_storage=True)

    assert fingerprint["evidence"]["version_count"] == report.versions_checked
    assert fingerprint["evidence"]["bytes_verified"] is True
    assert [finding for finding in report.findings if finding.kind in INTEGRITY_FAILURES] == []


def test_a_rebuild_after_a_restore_brings_the_projection_back(pdf_version, extract) -> None:
    """What the runbook does with the search index: rebuild it, never restore it."""
    extract(pdf_version)
    assert fragments_of(pdf_version).exists()

    SearchDocument.objects.all().delete()  # what a restored database looks like
    result = rebuild_all()

    assert result.documents > 0
    assert fragments_of(pdf_version).exists()
    assert check_evidence(verify_sha=False, scan_storage=True).versions_checked >= 1


# =========================================================================
# G. The current register survives the recovery tooling
#
# `CurrentRegisterState` is derived and `SearchDocument` is derived. The thing
# integration could quietly do is let a rebuild of the second decide anything
# about the first.
# =========================================================================


@pytest.fixture
def reviewed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.legacy_import.final_cutover.REVIEWED_SNAPSHOT_SHA256", (FINAL_SNAPSHOT,)
    )


def test_a_current_matter_is_still_current_after_a_search_rebuild(reviewed) -> None:
    world = build_world()
    apply_cutover_plan(build_cutover_plan(snapshot_sha256=FINAL_SNAPSHOT))
    before = list(
        CurrentRegisterState.objects.order_by("matter_id").values_list("matter_id", "currency")
    )
    assert before, "the synthetic cutover produced no register state to protect"

    rebuild_all()

    after = list(
        CurrentRegisterState.objects.order_by("matter_id").values_list("matter_id", "currency")
    )
    assert after == before, "rebuilding search moved a Matter in or out of the current register"
    drafting = CurrentRegisterState.objects.get(matter=world[CURRENT_DRAFTING])
    assert drafting.currency == RegisterCurrency.CURRENT
    assert drafting.is_drafting
    sent = CurrentRegisterState.objects.get(matter=world[CURRENT_SENT])
    assert sent.currency == RegisterCurrency.CURRENT
    assert not sent.is_drafting, "VÄLJA is a send date, not a closure"
    assert SearchDocument.objects.filter(
        matter=world[CURRENT_DRAFTING], source_kind=SearchSourceKind.MATTER
    ).exists()


def test_the_current_register_is_not_a_kind_of_search_row() -> None:
    """Derived tables do not feed each other.

    If this ever fails, one projection has been made the other's canonical
    input — which is the failure integration is most likely to introduce and
    least likely to notice, because both tables would still look correct.
    """
    assert {kind.value for kind in SearchSourceKind} == {
        "MATTER",
        "ENTRY",
        "SUBMISSION",
        "DOCUMENT_FRAGMENT",
        "LEGACY_SOURCE_PAGE",
    }
