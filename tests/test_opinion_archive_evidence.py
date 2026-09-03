"""Holding the archive's bytes, and reading them only where policy allows.

Three properties, each of which failed silently in an earlier design:

* **materialisation is pinned and idempotent.** The archive is 105 MB of real
  correspondence and the operation copies it into the evidence store; running
  it twice, or against a different ZIP, must not produce two answers;
* **an unscanned file is not parsed where real data lives.** ADR 0014 says so
  and this is the test that keeps saying it when somebody reasonably observes
  how much more useful the archive would be with its bodies searchable;
* **the pruner knows the archive exists.** Before Stage 2H.2 "referenced" meant
  "a DocumentVersion points at it", and the archive puts canonical bytes in the
  same store. One forgotten holder is one `prune_orphaned_evidence` away from
  deleting 767 letters.

Every byte here is synthetic (`tests/synthetic_opinions.py`).
"""

from __future__ import annotations

import datetime

import pytest

from app.legacy_import.opinion_binary import OpinionArchiveBinary, OpinionArchiveText
from app.legacy_import.opinion_enums import ArchiveTextState
from app.legacy_import.opinion_materialize import (
    OpinionMaterializeError,
    materialize,
    plan_materialization,
    storage_key_for,
)
from tests import synthetic_opinions as syn

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# A catalogued archive, the state materialisation starts from
# ---------------------------------------------------------------------------


@pytest.fixture
def catalogued(tmp_path, evidence_root):
    """Two letters and one duplicate, catalogued and ready to materialise."""
    from app.legacy_import.opinion_apply import apply_plan, open_batch
    from app.legacy_import.opinion_plan import build_plan

    letters = [
        syn.opinion(date="2024-04-10", recipient="Naidisministeerium", title="Esimene"),
        syn.opinion(date="2024-05-11", recipient="Teine amet", title="Teine"),
    ]
    duplicate = syn.SyntheticOpinion(
        name="Opinions/koopia/2024-04-10 - Naidisministeerium - Esimene.pdf",
        data=letters[0].data,
    )
    path = syn.write_archive(tmp_path / "Opinions.zip", [*letters, duplicate])
    plan = build_plan(archive_path=path)
    apply_plan(plan, batch=open_batch(plan))
    return path, plan.archive_sha256, letters


# ---------------------------------------------------------------------------
# Materialisation
# ---------------------------------------------------------------------------


def test_the_archive_must_be_pinned_before_a_single_byte_is_copied(catalogued):
    path, _, _ = catalogued
    with pytest.raises(OpinionMaterializeError):
        plan_materialization(archive_path=path, expected_archive_sha256="")


def test_a_different_archive_is_refused_rather_than_merged(catalogued, tmp_path):
    other = syn.write_archive(
        tmp_path / "other.zip",
        [syn.opinion(date="2020-01-01", recipient="Kolmas", title="Kolmas")],
    )
    with pytest.raises(OpinionMaterializeError):
        materialize(archive_path=other, expected_archive_sha256="f" * 64)
    assert OpinionArchiveBinary.objects.count() == 0


def test_planning_writes_nothing(catalogued):
    path, digest, _ = catalogued
    report = plan_materialization(archive_path=path, expected_archive_sha256=digest)
    assert report.distinct_binaries == 2
    assert OpinionArchiveBinary.objects.count() == 0


def test_identical_bytes_at_two_paths_are_stored_once(catalogued):
    path, digest, letters = catalogued
    materialize(archive_path=path, expected_archive_sha256=digest)

    # Three occurrences, two letters: the duplicate is the same evidence at a
    # second path, not a second piece of evidence.
    assert OpinionArchiveBinary.objects.count() == 2
    binary = OpinionArchiveBinary.objects.get(sha256=letters[0].sha256)
    assert binary.occurrences.count() == 2
    assert binary.storage_key == storage_key_for(binary.sha256)


def test_materialising_twice_changes_nothing(catalogued):
    path, digest, _ = catalogued
    first = materialize(archive_path=path, expected_archive_sha256=digest)
    second = materialize(archive_path=path, expected_archive_sha256=digest)
    assert OpinionArchiveBinary.objects.count() == 2
    assert first.binaries_created == 2
    assert second.binaries_created == 0
    assert second.binaries_reused == 2
    assert second.occurrences_already_linked == 3


def test_a_row_whose_object_vanished_is_reported_rather_than_papered_over(catalogued, settings):
    """The one case a naive "already have it" check gets wrong.

    A row saying the bytes are held, with nothing behind it, makes the archive
    report full coverage over an empty store. The run says so and exits
    non-zero rather than quietly re-writing: the object may have been deleted,
    the storage class may be mounted wrong, and re-writing would hide which.
    """
    path, digest, letters = catalogued
    materialize(archive_path=path, expected_archive_sha256=digest)
    binary = OpinionArchiveBinary.objects.get(sha256=letters[0].sha256)
    (settings.EVIDENCE_ROOT / binary.storage_key).unlink()

    report = materialize(archive_path=path, expected_archive_sha256=digest)
    assert report.missing_stored_object == 1
    assert not report.ok
    assert report.binaries_reused == 1, "the intact letter is still reused"


# ---------------------------------------------------------------------------
# Extraction policy
# ---------------------------------------------------------------------------


def test_real_data_environments_block_extraction_and_say_so(catalogued, settings):
    from app.legacy_import.opinion_text import extract_all

    path, digest, _ = catalogued
    materialize(archive_path=path, expected_archive_sha256=digest)
    settings.REAL_DATA_ALLOWED = True

    report = extract_all()
    assert report.blocked == 2
    assert report.extracted == 0
    states = set(OpinionArchiveText.objects.values_list("state", flat=True))
    # BLOCKED, not FAILED and not left PENDING: a policy decision recorded as
    # one, so a coverage report can tell "we chose not to open these" from "we
    # have not tried" and from "the parser broke".
    assert states == {ArchiveTextState.BLOCKED}


def test_turning_the_policy_off_makes_a_blocked_row_stale_rather_than_permanent(
    catalogued, settings
):
    from app.legacy_import.opinion_text import extract_all

    path, digest, _ = catalogued
    materialize(archive_path=path, expected_archive_sha256=digest)
    settings.REAL_DATA_ALLOWED = True
    extract_all()

    settings.REAL_DATA_ALLOWED = False
    report = extract_all()
    assert report.skipped_up_to_date == 0, "a BLOCKED row must be reconsidered, not kept"


def test_a_pdf_with_no_text_layer_is_recorded_as_such(catalogued, settings):
    """Not a failure, not an absence: a fact about the corpus worth counting."""
    from app.legacy_import.opinion_text import extract_all

    path, digest, _ = catalogued
    settings.REAL_DATA_ALLOWED = False
    materialize(archive_path=path, expected_archive_sha256=digest)

    report = extract_all()
    # The synthetic PDFs carry a signature and no text stream, which is exactly
    # the scanned-letter shape.
    assert report.considered == 2
    assert report.extracted + report.no_text_layer + report.failed == 2


# ---------------------------------------------------------------------------
# The pruner and the restore check
# ---------------------------------------------------------------------------


def test_an_archive_binary_is_not_an_orphan(catalogued, settings):
    from app.documents.references import holder_of, referenced_storage_keys

    path, digest, letters = catalogued
    materialize(archive_path=path, expected_archive_sha256=digest)
    binary = OpinionArchiveBinary.objects.get(sha256=letters[0].sha256)

    assert binary.storage_key in referenced_storage_keys()
    assert holder_of(binary.storage_key) == "OpinionArchiveBinary"


def test_the_pruner_does_not_select_the_archive(catalogued, settings):
    """The pruner reports every unreferenced object it would remove.

    Asserted on the report rather than on a deletion, because a deletion pass
    refuses a grace window short enough to reach an object written seconds ago
    — for the good reason that bytes land before the row describing them. The
    report is the decision; the grace window only delays acting on it.
    """
    from io import StringIO

    from django.core.management import call_command

    path, digest, _ = catalogued
    materialize(archive_path=path, expected_archive_sha256=digest)
    keys = list(OpinionArchiveBinary.objects.values_list("storage_key", flat=True))
    assert keys

    out = StringIO()
    call_command("prune_orphaned_evidence", stdout=out)
    report = out.getvalue()

    for key in keys:
        assert key not in report, "the pruner offered to delete held archive evidence"
        assert (settings.EVIDENCE_ROOT / key).exists()


def test_the_restore_fingerprint_counts_every_holder_of_evidence(catalogued, settings):
    """A restore check that knew about one holder would pass over a lost archive."""
    from app.documents.references import EVIDENCE_REFERENCES

    path, digest, _ = catalogued
    materialize(archive_path=path, expected_archive_sha256=digest)

    labels = {reference.label for reference in EVIDENCE_REFERENCES}
    assert "OpinionArchiveBinary" in labels

    rows = {
        key
        for reference in EVIDENCE_REFERENCES
        if reference.label == "OpinionArchiveBinary"
        for key, _, _ in reference.rows()
    }
    assert rows == set(OpinionArchiveBinary.objects.values_list("storage_key", flat=True))


def test_the_archive_projection_is_declared_rebuildable(db):
    """A restore may leave it empty; comparing it would fail every good restore."""
    from app.core.deployment import canonical_model_labels, rebuildable_model_labels

    assert "legacy_import.OpinionArchiveSearchDocument" in rebuildable_model_labels()
    assert "legacy_import.OpinionArchiveSearchDocument" not in canonical_model_labels()
    # The bytes and the occurrence rows are not: nothing rebuilds those.
    assert "legacy_import.OpinionArchiveBinary" in canonical_model_labels()


def test_text_is_rebuildable_and_the_binary_is_not(db):
    from app.core.deployment import rebuildable_model_labels

    assert "legacy_import.OpinionArchiveText" in rebuildable_model_labels()
    assert "legacy_import.OpinionArchiveBinary" not in rebuildable_model_labels()


def test_a_text_row_records_the_parser_that_produced_it(catalogued, settings):
    from app.legacy_import.opinion_text import PARSER_NAME, PARSER_VERSION, extract_all

    path, digest, _ = catalogued
    settings.REAL_DATA_ALLOWED = False
    materialize(archive_path=path, expected_archive_sha256=digest)
    extract_all()

    text = OpinionArchiveText.objects.first()
    assert text is not None
    assert text.parser == PARSER_NAME
    assert text.parser_version == PARSER_VERSION
    assert text.extracted_at is not None
    assert text.extracted_at.date() >= datetime.date(2020, 1, 1)


# ---------------------------------------------------------------------------
# Materialisation and the archive projection
# ---------------------------------------------------------------------------
#
# `_link_occurrences` points a catalogued occurrence at the bytes it names, and
# does it with a compare-and-set `update()` — it links only a row still holding
# no binary — so no `post_save` handler can see the write. That makes it the bulk
# half of the ADR 0041 contract: the caller owes a refresh bounded by what it
# touched, which here is one binary per group of identical bytes.


def test_materialising_a_letter_leaves_it_findable_without_a_rebuild(catalogued):
    """Held and searchable are one event, not two.

    Before this, materialisation left every new binary out of the projection and
    `verify` said so; converging it needed a rebuild an operator remembered.
    The bytes are the same either way — what changed is that the row describing
    them arrives with them.
    """
    from app.legacy_import.opinion_search import archive_index_findings, rebuild_archive_index
    from app.legacy_import.opinion_search_models import OpinionArchiveSearchDocument

    path, digest, _ = catalogued
    materialize(archive_path=path, expected_archive_sha256=digest)

    assert OpinionArchiveSearchDocument.objects.count() == OpinionArchiveBinary.objects.count() == 2
    # The duplicate filing is on the row, which is the whole point of one row
    # per binary rather than one per occurrence.
    counts = sorted(OpinionArchiveSearchDocument.objects.values_list("occurrence_count", flat=True))
    assert counts == [1, 2]
    assert archive_index_findings() == []
    assert rebuild_archive_index().written == 0


def test_a_later_snapshot_refiling_held_bytes_refreshes_that_row(catalogued, tmp_path):
    """The staling path a signal cannot see, through the command that walks it.

    A second archive holding a letter we already have reuses the existing binary
    — which by now has an indexed row — and links the new occurrence with a
    queryset `update()`. Before the bounded refresh below it, that row went on
    reporting one filing where the archive held two, and `verify` reported a
    clean run throughout.
    """
    from app.legacy_import.opinion_apply import apply_plan, open_batch
    from app.legacy_import.opinion_plan import build_plan
    from app.legacy_import.opinion_search import archive_index_findings, rebuild_archive_index
    from app.legacy_import.opinion_search_models import OpinionArchiveSearchDocument
    from tests import synthetic_opinions as syn

    path, digest, letters = catalogued
    materialize(archive_path=path, expected_archive_sha256=digest)
    binary = OpinionArchiveBinary.objects.get(sha256=letters[1].sha256)
    row = OpinionArchiveSearchDocument.objects.get(binary=binary)
    assert row.occurrence_count == 1

    refiled = syn.SyntheticOpinion(
        name="Opinions/2025 ymberkorraldus/2024-05-11 - Teine amet - Teine.pdf",
        data=letters[1].data,
    )
    later = syn.write_archive(tmp_path / "Opinions-2025.zip", [refiled])
    plan = build_plan(archive_path=later)
    apply_plan(plan, batch=open_batch(plan))
    materialize(archive_path=later, expected_archive_sha256=plan.archive_sha256)

    # No rebuild_archive_index() here, deliberately.
    row.refresh_from_db()
    assert row.occurrence_count == 2
    assert "Opinions/2025 ymberkorraldus" in row.occurrence_paths
    assert archive_index_findings() == []
    # And it landed where a rebuild would have.
    assert rebuild_archive_index().written == 0


def test_extraction_leaves_the_projection_fresh_without_a_rebuild(catalogued, settings):
    """The whole walk, end to end, over stored bytes and the real parser.

    `extract_all` records an outcome for every materialised binary through
    `_record`, and until this round each of those committed while the search
    projection went on describing the letter as it had been. The runbook's
    answer was `rebuild`, run by a person, prompted by a `verify` run by a
    person — which is the shape ADR 0041 exists to remove.

    The synthetic PDFs carry no text stream, so what this proves is the
    *removing* direction, which is the one an operator meets most: a scanned
    letter recorded as `NO_TEXT_LAYER` must leave the row saying it has no
    searchable body, and `verify` must agree.
    """
    from app.legacy_import.opinion_search import (
        archive_index_findings,
        rebuild_archive_index,
        refresh_archive_binaries,
    )
    from app.legacy_import.opinion_search_models import OpinionArchiveSearchDocument
    from app.legacy_import.opinion_text import extract_all

    path, digest, _ = catalogued
    settings.REAL_DATA_ALLOWED = False
    materialize(archive_path=path, expected_archive_sha256=digest)
    assert archive_index_findings() == []

    report = extract_all()
    assert report.considered == 2
    assert report.extracted == 0

    # No rebuild_archive_index() here, deliberately.
    assert set(OpinionArchiveSearchDocument.objects.values_list("has_body_text", flat=True)) == {
        False
    }
    assert archive_index_findings() == []
    assert rebuild_archive_index().written == 0
    assert refresh_archive_binaries(OpinionArchiveBinary.objects.values_list("pk", flat=True)) == 0


def test_the_policy_withdrawing_permission_takes_the_searchable_body_with_it(catalogued, settings):
    """`BLOCKED` is a re-extraction too, and it removes from the corpus.

    `extract-text --force` in an environment where `REAL_DATA_ALLOWED` is on
    re-records every row as `BLOCKED` (ADR 0014). A projection that went on
    serving those bodies would keep the archive searchable by content the
    policy had just forbidden opening — the same defect as a stale body, with a
    policy consequence on top.

    `--force` rather than a plain re-run, and not to make the test pass:
    `_is_current` calls a row written by this parser at this version current
    whatever the policy now says, so a plain re-run skips it. That is the
    existing extraction contract and this round does not touch it; what is
    asserted is that the outcome, once recorded, reaches the projection.

    The body is recorded directly, because the synthetic PDFs have no text
    layer and the state being left behind is what the test is about.
    """
    from app.legacy_import.opinion_search import archive_index_findings, rebuild_archive_index
    from app.legacy_import.opinion_search_models import OpinionArchiveSearchDocument
    from app.legacy_import.opinion_text import _record, extract_all

    path, digest, letters = catalogued
    settings.REAL_DATA_ALLOWED = False
    materialize(archive_path=path, expected_archive_sha256=digest)
    binary = OpinionArchiveBinary.objects.get(sha256=letters[0].sha256)
    _record(binary, state=ArchiveTextState.DONE, body="Sünteetiline eraldatud keha.")
    row = OpinionArchiveSearchDocument.objects.get(binary=binary)
    assert row.has_body_text is True
    assert archive_index_findings() == []

    settings.REAL_DATA_ALLOWED = True
    report = extract_all(force=True)
    assert report.blocked == 2

    row.refresh_from_db()
    assert row.has_body_text is False
    assert row.body_text == ""
    assert archive_index_findings() == []
    assert rebuild_archive_index().written == 0
