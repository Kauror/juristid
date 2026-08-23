"""Cataloguing the archive without claiming the Chamber sent anything.

Materialisation needs a catalogue, and until this split the only thing that
produced one was the full apply — which also writes Submissions. So holding a
letter's bytes required first asserting who sent it, which is backwards: the
bytes are the evidence that assertion would rest on. In production that meant
P3 could not start without starting P4.

These tests are about the line between the two, and they are written from the
outside — through the management command, because the phase separation is an
operator contract before it is a service one. The pivotal case is the first: an
occurrence whose match class an apply would execute *without asking anybody*
must still produce no Submission when it is only catalogued. If that ever stops
being true the split has silently collapsed, and nothing else here would notice.

All data is synthetic (`tests/synthetic_opinions.py`). No real archive file,
filename, register row or name appears.
"""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from app.documents.models import Document, DocumentVersion
from app.legacy_import.opinion_archive import (
    OpinionArchiveBatch,
    OpinionArchiveItem,
    OpinionArchiveMetadata,
    OpinionMatchCandidate,
    OpinionSubmissionImport,
)
from app.legacy_import.opinion_binary import OpinionArchiveBinary
from app.legacy_import.opinion_enums import (
    AUTOMATIC_MATCH_CLASSES,
    OpinionCandidateState,
)
from app.legacy_import.opinion_plan import build_plan
from app.submissions.models import Submission, SubmissionRecipient
from tests import synthetic_opinions as syn
from tests.test_opinion_apply_state import strict_pair

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(phase: str, archive, **extra) -> str:
    out = io.StringIO()
    call_command(
        "opinion_archive",
        phase,
        "--opinions",
        str(archive),
        stdout=out,
        stderr=out,
        **extra,
    )
    return out.getvalue()


def canonical_counts() -> tuple[int, ...]:
    """Everything cataloguing must leave at zero, in one tuple.

    A tuple rather than separate asserts so a failure prints the whole picture:
    when this breaks, *which* canonical row appeared is the entire diagnosis.
    """
    return (
        Submission.objects.count(),
        SubmissionRecipient.objects.count(),
        Document.objects.count(),
        DocumentVersion.objects.count(),
        OpinionSubmissionImport.objects.count(),
        OpinionArchiveBinary.objects.count(),
    )


def catalogue_counts() -> tuple[int, int, int]:
    return (
        OpinionArchiveItem.objects.count(),
        OpinionArchiveMetadata.objects.count(),
        OpinionMatchCandidate.objects.count(),
    )


@pytest.fixture
def real_data(settings):
    settings.REAL_DATA_ALLOWED = True
    return settings


@pytest.fixture
def automatic(tmp_path, real_data, evidence_root):
    """An archive holding one letter an apply would file without being asked.

    `strict_pair` is the three-exact-signal shape, which is in
    `AUTOMATIC_MATCH_CLASSES`. The premise is asserted below rather than
    trusted: a catalogue that created nothing because the plan proposed nothing
    would pass every assertion here and prove none of them.
    """
    matter, letter = strict_pair()
    other = syn.opinion(date="2024-09-02", recipient="Teine amet", title="Sidumata kiri")
    path = syn.write_archive(tmp_path / "Opinions.zip", [letter, other])
    return path, matter, letter


# ---------------------------------------------------------------------------
# The premise
# ---------------------------------------------------------------------------


def test_the_fixture_really_would_be_applied_without_a_person(automatic):
    """Asserted, not assumed. Everything below rests on this proposal existing."""
    path, _, _ = automatic
    plan = build_plan(archive_path=path)
    automatic_proposals = [p for p in plan.proposals if p.match_class in AUTOMATIC_MATCH_CLASSES]
    assert len(automatic_proposals) == 1
    assert len(plan.submissions) == 1, "an apply would create exactly one Submission here"


# ---------------------------------------------------------------------------
# Catalogue creates no canonical record
# ---------------------------------------------------------------------------


def test_cataloguing_creates_no_submission_even_for_an_automatic_match(automatic):
    """The whole point of the phase, in one assertion.

    The plan says this occurrence may be filed without a person. Catalogue
    records the proposal and declines to act on it, because deciding that a
    letter was sent is a different authority from recording that the archive
    holds it.
    """
    path, _, _ = automatic
    output = run("catalogue", path)

    items, _metadata, candidates = catalogue_counts()
    assert items == 2, "both occurrences are catalogued, matched or not"
    assert candidates >= 1
    assert canonical_counts() == (0, 0, 0, 0, 0, 0)

    assert OpinionMatchCandidate.objects.filter(state=OpinionCandidateState.APPLIED).count() == 0
    assert "Kanoonilist arvamust ei loodud" in output


def test_the_report_says_zero_rather_than_staying_silent(automatic):
    """An operator's question after this phase is exactly "did it send anything"."""
    path, _, _ = automatic
    output = run("catalogue", path)
    assert "loodud arvamusi" in output
    assert "kataloogimine" in output.lower()
    # And it points at the next phase, which is not the review queue.
    assert "materialize-plan" in output


def test_a_catalogue_batch_says_what_it_was(automatic):
    path, _, _ = automatic
    run("catalogue", path)
    batch = OpinionArchiveBatch.objects.get()
    assert "catalogue" in batch.notes
    assert batch.finished_at is not None
    assert batch.archive_sha256
    assert not OpinionSubmissionImport.objects.filter(batch=batch).exists()


# ---------------------------------------------------------------------------
# Rerunning
# ---------------------------------------------------------------------------


def test_cataloguing_twice_adds_nothing(automatic):
    """Idempotent by identity, not by deleting what the first run wrote."""
    path, _, _ = automatic
    run("catalogue", path)
    first = catalogue_counts()

    output = run("catalogue", path)
    assert catalogue_counts() == first
    assert canonical_counts() == (0, 0, 0, 0, 0, 0)
    assert "juba olemas" in output
    # A run is a run: the second one is recorded even though it wrote no rows.
    assert OpinionArchiveBatch.objects.count() == 2


def test_a_rerun_reports_what_already_existed(automatic):
    path, _, _ = automatic
    run("catalogue", path)
    output = run("catalogue", path)
    assert "juba olemas" in output
    assert "kandidaate juba olemas" in output


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_a_wrong_archive_hash_is_refused_before_any_row_is_written(automatic):
    """Including the batch. A refused run should leave nothing to explain."""
    path, _, _ = automatic
    with pytest.raises(CommandError):
        run("catalogue", path, expect_archive_sha256="0" * 64)
    assert catalogue_counts() == (0, 0, 0)
    assert OpinionArchiveBatch.objects.count() == 0
    assert canonical_counts() == (0, 0, 0, 0, 0, 0)


def test_a_wrong_kodadash_hash_is_refused_the_same_way(tmp_path, real_data, evidence_root):
    letter = syn.opinion(date="2024-04-10", recipient="Näidisministeerium", title="Kiri")
    path = syn.write_archive(tmp_path / "Opinions.zip", [letter])
    book = syn.write_kodadash_workbook(
        tmp_path / "kodadash.xlsx",
        [{"content_id": "SYN-1", "file_sha256": letter.sha256, "document_date": "2024-04-10"}],
    )
    with pytest.raises(CommandError):
        run("catalogue", path, kodadash=book, expect_kodadash_sha256="0" * 64)
    assert catalogue_counts() == (0, 0, 0)
    assert OpinionArchiveBatch.objects.count() == 0


def test_catalogue_is_a_real_data_write_and_says_so(automatic, settings):
    path, _, _ = automatic
    settings.REAL_DATA_ALLOWED = False
    with pytest.raises(CommandError, match="REAL_DATA_ALLOWED"):
        run("catalogue", path)
    assert catalogue_counts() == (0, 0, 0)


def test_plan_stays_read_only_beside_it(automatic):
    """The gate moved onto a new phase; it must not have moved onto an old one."""
    path, _, _ = automatic
    run("plan", path)
    assert catalogue_counts() == (0, 0, 0)
    assert OpinionArchiveBatch.objects.count() == 0


def test_cataloguing_does_not_touch_the_source_files(automatic):
    path, _, _ = automatic
    before = (path.read_bytes(), path.stat().st_size)
    run("catalogue", path)
    assert (path.read_bytes(), path.stat().st_size) == before


# ---------------------------------------------------------------------------
# Catalogue then materialise — the sequence this whole change exists for
# ---------------------------------------------------------------------------


def test_materialize_plan_sees_a_catalogue_that_no_apply_produced(automatic):
    from app.legacy_import.opinion_materialize import plan_materialization

    path, _, _ = automatic
    sha = build_plan(archive_path=path).archive_sha256
    run("catalogue", path)

    report = plan_materialization(archive_path=path, expected_archive_sha256=sha)
    assert report.occurrences == OpinionArchiveItem.objects.count() == 2
    assert canonical_counts() == (0, 0, 0, 0, 0, 0)


def test_catalogue_then_materialize_holds_the_bytes_and_sends_nothing(automatic):
    path, _, _ = automatic
    sha = build_plan(archive_path=path).archive_sha256
    run("catalogue", path)
    run("materialize", path, expect_archive_sha256=sha)

    assert OpinionArchiveBinary.objects.count() == 2
    assert OpinionArchiveItem.objects.filter(binary__isnull=True).count() == 0
    assert Submission.objects.count() == 0
    assert OpinionSubmissionImport.objects.count() == 0


def test_a_windows_separator_survives_catalogue_and_materialisation(
    tmp_path, real_data, evidence_root, posix_zip_names
):
    r"""One canonical path, from reading the archive to opening the file.

    Every member of the approved archive is stored as ``Opinions\<name>.pdf``,
    and ``zipfile`` hands that name back differently on Windows and on Linux.
    Reading, cataloguing and materialisation each turn a member name into a
    path, and the operator only finds out they disagreed at the end: the
    catalogue would hold rows and `materialize` would report every one of them
    as missing from the archive it had just catalogued.

    So this asserts the whole chain on one file rather than the reader alone,
    and it is the only test here that would still pass if `ArchiveReader` kept a
    normalisation of its own.
    """
    letter = syn.opinion(date="2026-07-07", recipient="Näidisamet", title="Seitsmes kiri")
    filename = letter.name.split("/")[-1]
    path = syn.write_raw_archive(
        tmp_path / "Opinions.zip", [("Opinions\\" + filename, letter.data)]
    )
    sha = build_plan(archive_path=path).archive_sha256

    run("catalogue", path)

    item = OpinionArchiveItem.objects.get()
    assert item.archive_relative_path == "Opinions/" + filename
    assert item.binary_id is None

    from app.legacy_import.opinion_materialize import plan_materialization

    plan = plan_materialization(archive_path=path, expected_archive_sha256=sha)
    assert (plan.occurrences, plan.missing_from_archive, plan.hash_mismatch) == (1, 0, 0)

    run("materialize", path, expect_archive_sha256=sha)

    item.refresh_from_db()
    assert item.binary is not None
    assert item.binary.sha256 == letter.sha256
    assert OpinionArchiveBinary.objects.count() == 1
    assert canonical_counts()[:5] == (0, 0, 0, 0, 0)


# ---------------------------------------------------------------------------
# Catalogue then the full apply — the compatibility that matters most
# ---------------------------------------------------------------------------


def test_applying_after_a_catalogue_reuses_every_catalogue_row(automatic):
    """The phase split must cost the later apply nothing and duplicate nothing."""
    path, _, _ = automatic
    run("catalogue", path)
    after_catalogue = catalogue_counts()

    run("apply", path)

    assert catalogue_counts() == after_catalogue, "no second occurrence, metadata or candidate"
    assert Submission.objects.count() == 1
    assert OpinionSubmissionImport.objects.count() == 1
    assert OpinionMatchCandidate.objects.filter(state=OpinionCandidateState.APPLIED).count() == 1


def test_applying_without_a_prior_catalogue_still_works(automatic):
    """`apply` keeps its old meaning: it catalogues what is missing, then applies."""
    path, _, _ = automatic
    run("apply", path)
    assert OpinionArchiveItem.objects.count() == 2
    assert Submission.objects.count() == 1
    assert OpinionSubmissionImport.objects.count() == 1


# ---------------------------------------------------------------------------
# A person's decision outlives the phase split
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "decision",
    [
        OpinionCandidateState.REJECTED,
        OpinionCandidateState.DUPLICATE,
        OpinionCandidateState.NOT_AN_OPINION,
        OpinionCandidateState.DEFERRED,
    ],
)
def test_a_decision_taken_after_cataloguing_is_not_overturned_by_apply(automatic, decision):
    """Stage 2H.1's protection has to survive being reached by a new route.

    The reviewer now sees these rows *before* any apply has run, which is the
    whole benefit — and it would be worthless if the eventual apply treated a
    decided row as fresh.
    """
    path, _, _ = automatic
    run("catalogue", path)
    candidate = OpinionMatchCandidate.objects.filter(match_class__in=AUTOMATIC_MATCH_CLASSES).get()
    candidate.state = decision
    candidate.save(update_fields=["state", "updated_at"])

    run("apply", path)

    candidate.refresh_from_db()
    assert candidate.state == decision
    assert Submission.objects.count() == 0
    assert OpinionSubmissionImport.objects.count() == 0


def test_linked_without_approval_still_sends_nothing(automatic):
    path, _, _ = automatic
    run("catalogue", path)
    candidate = OpinionMatchCandidate.objects.filter(match_class__in=AUTOMATIC_MATCH_CLASSES).get()
    candidate.state = OpinionCandidateState.LINKED
    candidate.review_approves_submission = False
    candidate.save(update_fields=["state", "review_approves_submission", "updated_at"])

    run("apply", path)

    candidate.refresh_from_db()
    assert candidate.state == OpinionCandidateState.LINKED
    assert Submission.objects.count() == 0


def test_a_rerun_of_catalogue_does_not_reset_a_decision(automatic):
    path, _, _ = automatic
    run("catalogue", path)
    candidate = OpinionMatchCandidate.objects.filter(match_class__in=AUTOMATIC_MATCH_CLASSES).get()
    candidate.state = OpinionCandidateState.REJECTED
    candidate.save(update_fields=["state", "updated_at"])

    output = run("catalogue", path)

    candidate.refresh_from_db()
    assert candidate.state == OpinionCandidateState.REJECTED
    assert "inimese otsustatud" in output
