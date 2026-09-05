"""The archive's own search projection, and the boundary around it.

`SearchDocument.matter` is not nullable, and the first test here is the one that
keeps it that way: the archive is searchable through a separate projection
precisely so the global one never has to accommodate a row with no Matter.

The rest is about the boundary. An unfiled letter has no Matter to inherit
visibility from, so the archive's authorization is a property of the *corpus*
rather than of the row — which means it has to be all-or-nothing, applied before
anything is counted, and identical in the list, the detail page, the header
figures and the file download. Four surfaces deciding for themselves is four
chances for one of them to be generous.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.legacy_import.opinion_archive import (
    OpinionArchiveBatch,
    OpinionArchiveItem,
    OpinionArchiveMetadata,
    OpinionMatchCandidate,
    OpinionSubmissionImport,
)
from app.legacy_import.opinion_binary import (
    OpinionArchiveBinary,
    OpinionArchiveMatterLink,
    OpinionArchiveText,
)
from app.legacy_import.opinion_enums import (
    ArchiveTextState,
    OpinionCandidateState,
    OpinionMatchClass,
    OpinionMetadataSystem,
)
from app.legacy_import.opinion_search import (
    ArchiveFilters,
    ArchiveQueryRefused,
    _candidate_rows,
    _metadata_rows,
    _occurrence_rows,
    archive_counts,
    archive_index_findings,
    rebuild_archive_index,
    search_archive,
    unindexed_binaries,
)
from app.legacy_import.opinion_search_models import (
    ARCHIVE_INDEX_VERSION,
    OpinionArchiveSearchDocument,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# A small held archive
# ---------------------------------------------------------------------------


def hold(
    *,
    sha: str,
    title: str = "Naidisarvamus",
    recipient: str = "Naidisministeerium",
    date: datetime.date | None = None,
    paths: list[str] | None = None,
    body: str = "",
) -> OpinionArchiveBinary:
    """One binary with its occurrences, as materialisation would leave it."""
    batch = OpinionArchiveBatch.objects.create(
        archive_sha256="a" * 64,
        importer_version="test/0",
        started_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    binary = OpinionArchiveBinary.objects.create(
        sha256=sha,
        size_bytes=1024,
        mime_type="application/pdf",
        storage_key=f"opinion-archive/{sha[:2]}/{sha[2:4]}/{sha}",
        source_archive_sha256="a" * 64,
        # Not nullable, and the fixture should not pretend otherwise: a binary
        # row exists because materialisation put bytes in the store, and when
        # it did so is part of the record.
        materialized_at=timezone.now(),
    )
    for path in paths or ["Opinions/naidis.pdf"]:
        OpinionArchiveItem.objects.create(
            batch=batch,
            archive_sha256="a" * 64,
            archive_relative_path=path,
            original_filename=path.rsplit("/", 1)[-1],
            sha256=sha,
            size_bytes=1024,
            detected_type="application/pdf",
            filename_date=date or datetime.date(2024, 4, 10),
            filename_recipient=recipient,
            filename_title=title,
            binary=binary,
        )
    if body:
        OpinionArchiveText.objects.create(
            binary=binary,
            state=ArchiveTextState.DONE,
            body=body,
            characters=len(body),
            parser="test",
            parser_version="1",
        )
    return binary


@pytest.fixture
def held(db):
    first = hold(
        sha="b" * 64,
        title="Ehitusseadustiku muutmine",
        paths=["Opinions/2024/esimene.pdf", "Opinions/koopia/esimene.pdf"],
        body="Käesolevaga esitab Koda arvamuse ehitusseadustiku eelnõu kohta.",
    )
    second = hold(
        sha="c" * 64,
        title="Maksukorralduse seadus",
        recipient="Rahandusministeerium",
        date=datetime.date(2023, 9, 1),
    )
    rebuild_archive_index()
    return first, second


# ---------------------------------------------------------------------------
# The projection stays separate
# ---------------------------------------------------------------------------


def test_the_matter_bound_projection_still_requires_a_matter(db):
    """The invariant the whole separate table exists to preserve."""
    from app.search.models import SearchDocument

    assert not SearchDocument._meta.get_field("matter").null


def test_nothing_from_the_archive_reaches_the_global_search(held):
    from app.search.models import SearchDocument

    assert SearchDocument.objects.count() == 0


def test_one_row_per_binary_not_per_occurrence(held):
    first, _ = held
    assert OpinionArchiveSearchDocument.objects.count() == 2
    row = OpinionArchiveSearchDocument.objects.get(binary=first)
    assert row.occurrence_count == 2
    # Both paths stay findable even though they share one row.
    assert "Opinions/koopia/esimene.pdf" in row.occurrence_paths


def test_a_rebuild_is_idempotent(held):
    before = list(
        OpinionArchiveSearchDocument.objects.order_by("pk").values_list("pk", "index_version")
    )
    report = rebuild_archive_index()
    after = list(
        OpinionArchiveSearchDocument.objects.order_by("pk").values_list("pk", "index_version")
    )
    assert report.written == 0
    assert report.unchanged == 2
    assert before == after


def test_extracting_text_and_rebuilding_puts_the_body_in_the_index(held, administrator):
    """The rebuild rule the runbook's `extract-text`, `rebuild` sequence rests on.

    A rebuild that skipped rows already at the current index version would do
    nothing here and report a clean run, leaving every freshly extracted body
    out of the search.

    Extracted under `suspend_archive_indexing`, because that is now the only
    way to reach the state the rule is about: a row at the current index
    version and stale. An extraction refreshes its own row
    (`refresh_on_text_save`), so the unsuspended sequence converges before the
    rebuild runs, and a test that used it would be asserting the handler
    rather than the rebuild.
    """
    from app.legacy_import.opinion_search import suspend_archive_indexing

    _, second = held
    with suspend_archive_indexing():
        OpinionArchiveText.objects.create(
            binary=second,
            state=ArchiveTextState.DONE,
            body="Riigilõivuseaduse muutmise kohta.",
            characters=33,
            parser="test",
            parser_version="1",
        )

    report = rebuild_archive_index()
    assert report.written == 1
    assert report.unchanged == 1

    rows = search_archive(user=administrator, filters=ArchiveFilters(query="riigilõivuseaduse"))
    assert [row.binary_id for row in rows] == [second.pk]


def test_a_decision_reaches_the_projection_on_the_next_rebuild(held):
    """The projection also moves when nothing about the binary changed."""
    first, _ = held
    row = OpinionArchiveSearchDocument.objects.get(binary=first)
    assert row.is_linked is False

    from app.legacy_import.opinion_enums import ArchiveLinkBasis
    from app.legacy_import.opinion_links import link_matter
    from tests import factories

    # Through the service, not the model: it is what stamps `linked_at`, and a
    # link with no time on it is not a link anybody can audit.
    link_matter(
        binary=first,
        matter=factories.ArchiveMatterFactory(),
        basis=ArchiveLinkBasis.EXACT_BINARY,
    )
    rebuild_archive_index()
    row.refresh_from_db()
    assert row.is_linked is True


def test_a_binary_with_no_occurrences_yet_is_reported_as_unindexed(held):
    """Held bytes the projection does not describe, and the rebuild that fixes it.

    The binary arrives here on its own, which is the state `materialize` really
    passes through: `_binary_for` writes the row and `_link_occurrences` points
    the occurrences at it afterwards, in a second statement. Cataloguing an
    occurrence *does* index its binary now — that is the freshness handler this
    module gained — so a letter held complete is no longer a way to reach this
    state, and pretending otherwise would test the fixture rather than the
    detector.
    """
    OpinionArchiveBinary.objects.create(
        sha256="d" * 64,
        size_bytes=1024,
        mime_type="application/pdf",
        storage_key="opinion-archive/dd/dd/" + "d" * 64,
        source_archive_sha256="a" * 64,
        materialized_at=timezone.now(),
    )
    assert unindexed_binaries().count() == 1
    assert any("otsinguprojektsioonis" in finding for finding in archive_index_findings())

    rebuild_archive_index()
    assert unindexed_binaries().count() == 0
    assert archive_index_findings() == []


def test_an_old_index_version_is_a_finding_and_a_rebuild_fixes_it(held):
    OpinionArchiveSearchDocument.objects.update(index_version="0")
    findings = archive_index_findings()
    assert any("versiooniga" in finding for finding in findings)

    rebuild_archive_index()
    assert set(OpinionArchiveSearchDocument.objects.values_list("index_version", flat=True)) == {
        ARCHIVE_INDEX_VERSION
    }


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------


def test_a_letter_is_found_by_its_title(held, administrator):
    # The word as the title writes it. The Estonian configuration stems both
    # sides the same way; it does not turn a compound's genitive into its
    # nominative, and a test that assumed otherwise was testing the stemmer
    # rather than the projection.
    rows = search_archive(user=administrator, filters=ArchiveFilters(query="ehitusseadustiku"))
    assert rows.count() == 1


def test_a_letter_is_found_by_its_body(held, administrator):
    rows = search_archive(user=administrator, filters=ArchiveFilters(query="eelnõu"))
    assert rows.count() == 1


def test_a_pasted_hash_finds_the_letter_it_names(held, administrator):
    first, _ = held
    rows = search_archive(user=administrator, filters=ArchiveFilters(query=first.sha256))
    # An exact containment test rather than a text search: the Estonian stemmer
    # would make a hash unrecognisable, and somebody pasting one is not
    # searching for words.
    assert [row.binary_id for row in rows] == [first.pk]


def test_a_pasted_archive_path_finds_the_letter_it_names(held, administrator):
    first, _ = held
    rows = search_archive(
        user=administrator, filters=ArchiveFilters(query="Opinions/koopia/esimene.pdf")
    )
    assert [row.binary_id for row in rows] == [first.pk]


def test_filters_narrow_without_searching(held, administrator):
    rows = search_archive(user=administrator, filters=ArchiveFilters(year="2023"))
    assert rows.count() == 1
    rows = search_archive(user=administrator, filters=ArchiveFilters(body="jah"))
    assert rows.count() == 1


def test_an_overlong_query_is_refused_rather_than_answered_with_nothing(held, administrator):
    with pytest.raises(ArchiveQueryRefused):
        search_archive(user=administrator, filters=ArchiveFilters(query="a" * 501))


def test_a_year_that_is_not_a_year_is_refused(held, administrator):
    with pytest.raises(ArchiveQueryRefused):
        search_archive(user=administrator, filters=ArchiveFilters(year="eelmine"))


# ---------------------------------------------------------------------------
# The boundary
# ---------------------------------------------------------------------------


def test_a_reader_sees_no_archive_rows_and_no_totals(held, reader):
    """Not a smaller list: none, and no count either.

    A refused reader who can still read the coverage figures knows how large
    the corpus is, which is most of what the boundary was protecting.

    READER is the refused role since ADR 0042's set reached this module: the
    two lawyer roles read the corpus, and READER is deliberately not one of
    them (app/legacy_import/opinion_access.py).
    """
    assert search_archive(user=reader, filters=ArchiveFilters()).count() == 0
    assert archive_counts(reader) == {
        "total": 0,
        "with_body": 0,
        "linked": 0,
        "with_submission": 0,
    }


def test_an_administrator_sees_the_corpus(held, administrator):
    assert archive_counts(administrator)["total"] == 2


def test_an_anonymous_visitor_sees_nothing(held):
    from django.contrib.auth.models import AnonymousUser

    assert search_archive(user=AnonymousUser(), filters=ArchiveFilters()).count() == 0
    assert archive_counts(None)["total"] == 0


def test_the_shared_gate_opens_the_archive_to_high_authority_personas(
    held, administrator, department_head, settings
):
    """The development-phase widening, pinned where the old refusal was.

    This surface used to refuse the shared gate outright, on the grounds that an
    audit row naming a persona behind one shared password is not a record of who
    read real correspondence. That is still true — and the price was 767 held
    letters nobody could read, for however long Cloudflare Access takes.

    So the corpus is served and the record stays honest instead: every download
    carries `authenticated_via` beside the persona, and the widening reaches
    only the two roles trusted with the whole register (docs/adr/0028).
    """
    from app.accounts.enums import AuthMode

    settings.AUTH_MODE = AuthMode.SHARED_GATE
    assert search_archive(user=administrator, filters=ArchiveFilters()).count() == 2
    assert search_archive(user=department_head, filters=ArchiveFilters()).count() == 2


def test_the_shared_gate_does_not_open_the_archive_to_everybody_behind_it(held, reader, settings):
    """Knowing the shared password is not on its own an archive credential."""
    from app.accounts.enums import AuthMode

    settings.AUTH_MODE = AuthMode.SHARED_GATE
    assert search_archive(user=reader, filters=ArchiveFilters()).count() == 0


def test_an_inactive_administrator_is_refused(held, administrator):
    administrator.is_active = False
    assert search_archive(user=administrator, filters=ArchiveFilters()).count() == 0


# ---------------------------------------------------------------------------
# The screens
# ---------------------------------------------------------------------------


def test_the_browse_screen_refuses_a_reader(client, reader, held):
    client.force_login(reader)
    response = client.get(reverse("legacy_import:opinion_archive_browse"))
    assert response.status_code == 403


def test_the_browse_screen_lists_what_is_held(client, administrator, held):
    client.force_login(administrator)
    response = client.get(reverse("legacy_import:opinion_archive_browse"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Ehitusseadustiku muutmine" in body
    assert "Maksukorralduse seadus" in body


def test_a_refused_query_says_so_instead_of_showing_an_empty_list(client, administrator, held):
    client.force_login(administrator)
    response = client.get(reverse("legacy_import:opinion_archive_browse"), {"q": "a" * 501})
    assert response.status_code == 200
    body = response.content.decode()
    assert "tähemärki" in body
    assert "Ükski arhiivi kiri" not in body


def test_the_detail_screen_refuses_a_reader(client, reader, held):
    first, _ = held
    client.force_login(reader)
    response = client.get(reverse("legacy_import:opinion_archive_detail", kwargs={"pk": first.pk}))
    assert response.status_code == 403


def test_the_detail_screen_lists_every_occurrence(client, administrator, held):
    first, _ = held
    client.force_login(administrator)
    response = client.get(reverse("legacy_import:opinion_archive_detail", kwargs={"pk": first.pk}))
    body = response.content.decode()
    assert "Opinions/2024/esimene.pdf" in body
    assert "Opinions/koopia/esimene.pdf" in body


# ---------------------------------------------------------------------------
# The bytes
# ---------------------------------------------------------------------------


@pytest.fixture
def stored(held, evidence_root, settings):
    """Put real bytes behind the first held letter."""
    from django.core.files.base import ContentFile

    from app.documents.services import evidence_storage

    first, _ = held
    evidence_storage().save(first.storage_key, ContentFile(b"%PDF-1.4 synthetic"))
    return first


def test_a_reader_may_not_download_an_archive_letter(client, reader, stored):
    client.force_login(reader)
    response = client.get(reverse("legacy_import:opinion_archive_file", kwargs={"pk": stored.pk}))
    assert response.status_code == 403


def test_an_administrator_reads_the_letter_inline_under_safe_headers(client, administrator, stored):
    client.force_login(administrator)
    response = client.get(reverse("legacy_import:opinion_archive_file", kwargs={"pk": stored.pk}))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "script-src 'none'" in response["Content-Security-Policy"]
    assert response["Content-Disposition"].startswith("inline")


def test_the_served_filename_is_the_hash_not_the_archive_name(client, administrator, stored):
    """The ZIP's names carry recipients, subjects and mojibake.

    A Content-Disposition is the wrong place to learn who a letter was to.
    """
    client.force_login(administrator)
    response = client.get(reverse("legacy_import:opinion_archive_file", kwargs={"pk": stored.pk}))
    disposition = response["Content-Disposition"]
    assert stored.sha256[:16] in disposition
    assert "esimene" not in disposition


def test_the_storage_key_never_appears_in_a_url(client, administrator, stored):
    url = reverse("legacy_import:opinion_archive_file", kwargs={"pk": stored.pk})
    assert stored.storage_key not in url
    assert str(stored.pk) in url


def test_a_row_whose_bytes_are_gone_is_a_controlled_404(client, administrator, held, evidence_root):
    first, _ = held
    client.force_login(administrator)
    response = client.get(reverse("legacy_import:opinion_archive_file", kwargs={"pk": first.pk}))
    assert response.status_code == 404


def test_reading_a_letter_is_recorded(client, administrator, stored):
    from app.audit.enums import SecurityEventType
    from app.audit.models import SecurityAuditEvent

    client.force_login(administrator)
    client.get(reverse("legacy_import:opinion_archive_file", kwargs={"pk": stored.pk}))

    event = SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.DOCUMENT_DOWNLOADED
    ).first()
    assert event is not None
    assert event.detail["source"] == "opinion_archive"
    assert event.detail["sha256"] == stored.sha256


# ---------------------------------------------------------------------------
# Freshness: the archive projection is inside the ADR 0041 contract (UX-006)
# ---------------------------------------------------------------------------
#
# `is_linked` and `has_submission` are computed at index time from relations
# that nothing about a binary touches. Before these handlers existed, a corpus
# could be fully linked while every row still read `Sidumata` and this module's
# own `archive_index_findings` reported a clean run — which is how 320 links
# stayed invisible on `/arvamused/arhiiv/` for a fortnight (docs/adr/0041).


def _row(binary):
    return OpinionArchiveSearchDocument.objects.get(binary=binary)


def test_linking_a_matter_refreshes_that_row_without_a_rebuild(held, specialist):
    """The defect, in one test: link after indexing, and read the list."""
    from app.legacy_import.opinion_enums import ArchiveLinkBasis
    from app.legacy_import.opinion_links import link_matter
    from app.matters.services import create_matter

    first, second = held
    assert _row(first).is_linked is False

    matter = create_matter(title="Ehitusseadustik", owner=specialist, reference_year=2026)
    link_matter(binary=first, matter=matter, basis=ArchiveLinkBasis.EXACT_BINARY)

    # No rebuild_archive_index() here, deliberately.
    assert _row(first).is_linked is True
    # And only that row: relinking one letter must not touch the corpus.
    assert _row(second).is_linked is False


def test_unlinking_a_matter_takes_the_flag_back(held, specialist):
    from app.legacy_import.opinion_binary import OpinionArchiveMatterLink
    from app.legacy_import.opinion_enums import ArchiveLinkBasis
    from app.legacy_import.opinion_links import link_matter
    from app.matters.services import create_matter

    first, _second = held
    matter = create_matter(title="Ehitusseadustik", owner=specialist, reference_year=2026)
    link_matter(binary=first, matter=matter, basis=ArchiveLinkBasis.EXACT_BINARY)
    assert _row(first).is_linked is True

    # Through the model rather than `unlink_matter`, which refuses a derived
    # basis on purpose. The projection question is the same either way: the
    # relation is gone, so the row must stop claiming it.
    OpinionArchiveMatterLink.objects.get(binary=first, matter=matter).delete()

    assert _row(first).is_linked is False


def test_a_canonical_import_sets_has_submission_on_its_binary(held, specialist):
    """`has_submission` is a fact about the bytes, reached through the item."""
    from app.legacy_import.opinion_archive import (
        OpinionArchiveBatch,
        OpinionArchiveItem,
        OpinionSubmissionImport,
    )
    from app.matters.services import create_matter
    from app.submissions.services import create_submission

    first, second = held
    assert _row(first).has_submission is False

    matter = create_matter(title="Ehitusseadustik", owner=specialist, reference_year=2026)
    submission = create_submission(matter=matter, title="Koja arvamus", actor=specialist)
    OpinionSubmissionImport.objects.create(
        item=OpinionArchiveItem.objects.filter(binary=first).first(),
        submission=submission,
        batch=OpinionArchiveBatch.objects.first(),
    )

    assert _row(first).has_submission is True
    assert _row(second).has_submission is False


def test_verify_reports_a_projection_that_no_longer_matches_canon(held, specialist):
    """The detector, on the exact drift that used to pass silently."""
    from app.legacy_import.opinion_enums import ArchiveLinkBasis
    from app.legacy_import.opinion_links import link_matter
    from app.matters.services import create_matter

    first, _second = held
    assert archive_index_findings() == []

    matter = create_matter(title="Ehitusseadustik", owner=specialist, reference_year=2026)
    link_matter(binary=first, matter=matter, basis=ArchiveLinkBasis.EXACT_BINARY)
    assert archive_index_findings() == []

    # Desynchronise by hand, the way a bulk write that owed a refresh and did
    # not pay it would leave things. `update()` sends no signals, which is
    # precisely the shape being guarded against.
    OpinionArchiveSearchDocument.objects.filter(binary=first).update(is_linked=False)

    findings = archive_index_findings()
    assert any("teemaseose" in finding for finding in findings), findings
    # Detected, not repaired: the row is still wrong after verify has run.
    assert _row(first).is_linked is False


def test_verify_reports_a_row_claiming_a_submission_it_does_not_have(held):
    first, _second = held
    OpinionArchiveSearchDocument.objects.filter(binary=first).update(has_submission=True)

    findings = archive_index_findings()
    assert any("arvamuse olek" in finding for finding in findings), findings


def test_a_bulk_writer_that_suspends_owes_a_bounded_refresh(held, specialist):
    """The other half of the contract: suspend, then pay, and pay only for what you touched."""
    from app.legacy_import.opinion_enums import ArchiveLinkBasis
    from app.legacy_import.opinion_links import link_matter
    from app.legacy_import.opinion_search import (
        refresh_archive_binaries,
        suspend_archive_indexing,
    )
    from app.matters.services import create_matter

    first, second = held
    matter = create_matter(title="Ehitusseadustik", owner=specialist, reference_year=2026)

    with suspend_archive_indexing():
        link_matter(binary=first, matter=matter, basis=ArchiveLinkBasis.EXACT_BINARY)

    # Suspended means stale, and `verify` is what notices.
    assert _row(first).is_linked is False
    assert any("teemaseose" in finding for finding in archive_index_findings())

    written = refresh_archive_binaries([first.pk])
    assert written == 1
    assert _row(first).is_linked is True
    assert archive_index_findings() == []

    # Bounded: a second pass over an already-fresh binary writes nothing, and
    # the untouched letter was never rewritten at all.
    assert refresh_archive_binaries([first.pk, second.pk]) == 0


# ---------------------------------------------------------------------------
# Freshness: the candidate half
# ---------------------------------------------------------------------------
#
# `review_state` and `match_class` come from the same shape of relation and were
# left out of the round that closed the other two. They are the worse omission
# of the three, because `OpinionMatchCandidate` is the relation the product
# actually writes: a reviewer answering the queue, a rerun retiring a proposal
# it no longer believes, an apply marking one finished. Every one of those could
# commit while `/arvamused/arhiiv/` went on labelling and filtering the letter
# by the state before it.


def a_candidate(binary, *, klass=OpinionMatchClass.UNMATCHED, matter=None):
    """One PENDING proposal about the first occurrence of these bytes.

    Always PENDING, and the tests that want another state save it afterwards:
    the transition is what the handler has to notice, and a row created in its
    final state would prove only that creation refreshes.
    """
    item = OpinionArchiveItem.objects.filter(binary=binary).order_by("pk").first()
    assert item is not None
    return OpinionMatchCandidate.objects.create(
        item=item,
        matter=matter,
        batch=item.batch,
        match_class=klass,
        state=OpinionCandidateState.PENDING,
    )


def test_a_new_candidate_reaches_an_already_indexed_row(held):
    """The plainest case: index first, propose second, read the list."""
    first, second = held
    assert _row(first).review_state == ""
    assert _row(first).match_class == ""

    a_candidate(first, klass=OpinionMatchClass.REVIEW_REQUIRED)

    # No rebuild_archive_index() here, deliberately.
    assert _row(first).review_state == OpinionCandidateState.PENDING
    assert _row(first).match_class == OpinionMatchClass.REVIEW_REQUIRED
    # And only that letter.
    assert _row(second).review_state == ""


def test_a_reviewer_rejecting_a_letter_moves_the_projection_at_once(client, administrator, held):
    """Through the real POST the queue submits, not through the model.

    `opinion_decide` is the one writer of every human state, and it saves seven
    fields at once. A handler that guessed which of them mattered would be wrong
    here.
    """
    first, _second = held
    candidate = a_candidate(first, klass=OpinionMatchClass.REVIEW_REQUIRED)
    assert _row(first).review_state == OpinionCandidateState.PENDING

    client.force_login(administrator)
    response = client.post(
        reverse("legacy_import:opinion_decide", kwargs={"pk": candidate.pk}),
        {"decision": "reject", "note": "Ei ole selle teema kohta."},
    )
    assert response.status_code == 302

    assert _row(first).review_state == OpinionCandidateState.REJECTED


def test_a_reviewer_linking_a_letter_moves_the_projection_at_once(
    client, administrator, held, specialist
):
    from app.matters.services import create_matter

    first, _second = held
    candidate = a_candidate(first, klass=OpinionMatchClass.REVIEW_REQUIRED)
    matter = create_matter(title="Ehitusseadustik", owner=specialist, reference_year=2026)

    client.force_login(administrator)
    response = client.post(
        reverse("legacy_import:opinion_decide", kwargs={"pk": candidate.pk}),
        {"decision": "link", "matter": str(matter.pk)},
    )
    assert response.status_code == 302

    assert _row(first).review_state == OpinionCandidateState.LINKED


def test_a_decided_state_outranks_an_undecided_one_on_the_same_letter(held):
    """Two live proposals, and the projection says the one somebody answered."""
    first, _second = held
    a_candidate(first, klass=OpinionMatchClass.UNMATCHED)
    answered = a_candidate(first, klass=OpinionMatchClass.REVIEW_REQUIRED)

    answered.state = OpinionCandidateState.DEFERRED
    answered.save(update_fields=["state", "updated_at"])

    assert _row(first).review_state == OpinionCandidateState.DEFERRED


def test_superseding_a_proposal_leaves_the_row_saying_what_is_left(held, normal_matter):
    """The projection excludes SUPERSEDED, so retiring one has to move it.

    `match_class` is what visibly moves, and it is the only thing that can:
    only a PENDING proposal may be superseded, PENDING is the lowest-ranked
    review state, and a supersession needs a live replacement on the same
    occurrence — so the state the row is filed under is by construction decided
    by somebody else. The class is not, and here the retired row was the one
    naming it.

    No special case inside `supersede_candidate` to make this pass: it saves
    the row like anything else, and the general handler is what notices.
    """
    from app.legacy_import.opinion_supersede import supersede_candidate

    first, _second = held
    old = a_candidate(first, klass=OpinionMatchClass.CONFLICT)
    replacement = a_candidate(first, klass=OpinionMatchClass.REVIEW_REQUIRED, matter=normal_matter)
    assert _row(first).match_class == OpinionMatchClass.CONFLICT

    supersede_candidate(
        superseded=old, replacement=replacement, reason="Hilisem jooks liigitas ümber."
    )

    row = _row(first)
    assert row.match_class == OpinionMatchClass.REVIEW_REQUIRED
    assert row.review_state == OpinionCandidateState.PENDING
    assert archive_index_findings() == []


def test_deleting_a_candidate_cannot_leave_its_state_projected(held):
    first, _second = held
    candidate = a_candidate(first, klass=OpinionMatchClass.REVIEW_REQUIRED)
    candidate.state = OpinionCandidateState.REJECTED
    candidate.save(update_fields=["state", "updated_at"])
    assert _row(first).review_state == OpinionCandidateState.REJECTED

    candidate.delete()

    assert _row(first).review_state == ""
    assert _row(first).match_class == ""
    assert archive_index_findings() == []


def test_a_candidate_deleted_by_a_cascade_still_frees_the_row(held):
    """The case `_started_here` would have got wrong.

    A candidate is `CASCADE` from its Item, and from its Matter as well. The
    binary outlives both: `OpinionArchiveItem.binary` is `PROTECT`, and here the
    letter is catalogued at two paths, so removing one occurrence leaves the
    bytes still represented by the other. Asking "did this delete begin at a
    candidate" answers no and would leave the row projected under a proposal
    that no longer exists.
    """
    first, _second = held
    items = list(OpinionArchiveItem.objects.filter(binary=first).order_by("pk"))
    assert len(items) == 2

    candidate = OpinionMatchCandidate.objects.create(
        item=items[0],
        batch=items[0].batch,
        match_class=OpinionMatchClass.REVIEW_REQUIRED,
        state=OpinionCandidateState.REJECTED,
    )
    assert _row(first).review_state == OpinionCandidateState.REJECTED

    items[0].delete()

    assert not OpinionMatchCandidate.objects.filter(pk=candidate.pk).exists()
    assert OpinionArchiveItem.objects.filter(binary=first).count() == 1
    assert _row(first).review_state == ""
    assert _row(first).match_class == ""


def test_a_candidate_on_an_unmaterialised_occurrence_is_ignored_rather_than_fatal(held):
    """A catalogued letter whose bytes nobody has copied yet has no row to move."""
    first, _second = held
    batch = OpinionArchiveBatch.objects.first()
    assert batch is not None
    orphan = OpinionArchiveItem.objects.create(
        batch=batch,
        archive_sha256="a" * 64,
        archive_relative_path="Opinions/kataloogitud.pdf",
        original_filename="kataloogitud.pdf",
        sha256="e" * 64,
        size_bytes=10,
        detected_type="application/pdf",
        binary=None,
    )
    candidate = OpinionMatchCandidate.objects.create(
        item=orphan,
        batch=batch,
        match_class=OpinionMatchClass.UNMATCHED,
        state=OpinionCandidateState.PENDING,
    )

    candidate.state = OpinionCandidateState.REJECTED
    candidate.save(update_fields=["state", "updated_at"])
    candidate.delete()

    assert OpinionArchiveSearchDocument.objects.count() == 2
    assert _row(first).review_state == ""


def test_a_rolled_back_decision_takes_its_projection_change_with_it(held):
    """One event, both halves. Class A in ADR 0041's terms is what this asserts."""
    from django.db import transaction

    first, _second = held
    candidate = a_candidate(first, klass=OpinionMatchClass.REVIEW_REQUIRED)
    assert _row(first).review_state == OpinionCandidateState.PENDING

    with transaction.atomic():
        candidate.state = OpinionCandidateState.REJECTED
        candidate.save(update_fields=["state", "updated_at"])
        assert _row(first).review_state == OpinionCandidateState.REJECTED
        transaction.set_rollback(True)

    candidate.refresh_from_db()
    assert candidate.state == OpinionCandidateState.PENDING
    assert _row(first).review_state == OpinionCandidateState.PENDING


def test_suspension_holds_candidate_writes_back_and_a_bounded_refresh_pays(held):
    """The bulk half, for the relation `apply_plan` writes with `update()`."""
    from app.legacy_import.opinion_search import (
        refresh_archive_binaries,
        suspend_archive_indexing,
    )

    first, second = held
    candidate = a_candidate(first, klass=OpinionMatchClass.REVIEW_REQUIRED)
    assert _row(first).review_state == OpinionCandidateState.PENDING

    with suspend_archive_indexing():
        candidate.state = OpinionCandidateState.REJECTED
        candidate.save(update_fields=["state", "updated_at"])
        # The shape no signal can see at all, suspended or not.
        OpinionMatchCandidate.objects.filter(pk=candidate.pk).update(
            match_class=OpinionMatchClass.CONFLICT
        )

    # Suspended means stale, and `verify` is what notices.
    assert _row(first).review_state == OpinionCandidateState.PENDING
    findings = archive_index_findings()
    assert any("ülevaatuse olek" in finding for finding in findings), findings
    assert any("sidumise klass" in finding for finding in findings), findings

    assert refresh_archive_binaries([first.pk]) == 1
    row = _row(first)
    assert row.review_state == OpinionCandidateState.REJECTED
    assert row.match_class == OpinionMatchClass.CONFLICT
    assert archive_index_findings() == []

    # Bounded: the untouched letter was never rewritten.
    assert refresh_archive_binaries([first.pk, second.pk]) == 0


def test_verify_reports_a_row_stuck_on_a_review_state_nobody_holds(held):
    first, _second = held
    a_candidate(first, klass=OpinionMatchClass.REVIEW_REQUIRED)
    assert archive_index_findings() == []

    # `update()` sends no signals, which is precisely the shape being guarded
    # against — and both directions matter, so this is the projection claiming a
    # decision no live candidate justifies.
    OpinionArchiveSearchDocument.objects.filter(binary=first).update(
        review_state=OpinionCandidateState.APPLIED
    )

    findings = archive_index_findings()
    assert any("ülevaatuse olek" in finding for finding in findings), findings
    # Detected, not repaired.
    assert _row(first).review_state == OpinionCandidateState.APPLIED


def test_verify_reports_a_row_whose_match_class_no_longer_matches(held):
    first, _second = held
    a_candidate(first, klass=OpinionMatchClass.REVIEW_REQUIRED)
    OpinionArchiveSearchDocument.objects.filter(binary=first).update(
        match_class=OpinionMatchClass.EXACT_BINARY_MATTER
    )

    findings = archive_index_findings()
    assert any("sidumise klass" in finding for finding in findings), findings


def test_a_letter_with_no_candidates_at_all_is_not_a_finding(held):
    """The clean corpus stays clean: absence is a value, not drift."""
    assert archive_index_findings() == []
    assert all(row.review_state == "" for row in OpinionArchiveSearchDocument.objects.all())


def test_the_candidate_findings_report_counts_and_nothing_else(held):
    """Operator output, so it may name a number and a class of problem only."""
    first, _second = held
    a_candidate(first, klass=OpinionMatchClass.REVIEW_REQUIRED)
    OpinionArchiveSearchDocument.objects.filter(binary=first).update(
        review_state=OpinionCandidateState.APPLIED, match_class=OpinionMatchClass.CONFLICT
    )

    findings = archive_index_findings()
    assert len(findings) == 2
    row = _row(first)
    for finding in findings:
        assert first.sha256 not in finding
        assert row.title not in finding
        assert row.occurrence_paths not in finding


def test_a_superseded_candidate_alone_leaves_the_row_empty(held, normal_matter):
    """The exclusion is the projection's, and the drift check shares it."""
    from app.legacy_import.opinion_supersede import supersede_candidate

    first, _second = held
    old = a_candidate(first, klass=OpinionMatchClass.UNMATCHED)
    replacement = a_candidate(first, klass=OpinionMatchClass.REVIEW_REQUIRED, matter=normal_matter)
    supersede_candidate(superseded=old, replacement=replacement, reason="Ümber liigitatud.")
    replacement.delete()

    assert _row(first).review_state == ""
    assert _row(first).match_class == ""
    assert archive_index_findings() == []


def test_a_candidates_register_reference_reaches_the_row_it_is_searchable_from(held, administrator):
    """The reason the handler recomputes the row rather than patching two columns.

    `excel_reference` is indexed among the row's `identifiers`, so a refresh
    narrowed to `review_state` and `match_class` would leave a letter unfindable
    by the register reference the candidate carries. The drift detector does not
    check this — subtracting one identifier from a row cannot be told apart from
    the rest of the column without a full-row comparison — so the freshness path
    has to be the thing that keeps it true.
    """
    first, _second = held
    item = OpinionArchiveItem.objects.filter(binary=first).order_by("pk").first()
    assert item is not None
    OpinionMatchCandidate.objects.create(
        item=item,
        batch=item.batch,
        match_class=OpinionMatchClass.REVIEW_REQUIRED,
        state=OpinionCandidateState.PENDING,
        excel_reference="2024_317",
    )

    assert "2024_317" in _row(first).identifiers
    rows = search_archive(user=administrator, filters=ArchiveFilters(query="2024_317"))
    assert [row.binary_id for row in rows] == [first.pk]


# ---------------------------------------------------------------------------
# Freshness: the occurrence half
# ---------------------------------------------------------------------------
#
# `OpinionArchiveItem` is the relation the other three hang off, and the last to
# be covered. Six columns are read off a binary's live occurrences at index time
# — `occurrence_count`, `occurrence_paths`, `identifiers`, `title`, `recipient`
# and `document_date`, with `source_year` following the date — so removing one
# filing of a letter moves all six at once.
#
# Nothing noticed before these handlers. The candidate handler above *does* fire
# during that cascade, but it fires before the occurrence row goes — the
# collector removes children first — so it recomputed a row that still held the
# occurrence being deleted, and `archive_index_findings` had no check that could
# tell the difference.


def a_filing(binary, *, path: str, filename: str, title: str = "", date=None):
    """One more filing of the same letter, at a new path and under a new name.

    Distinct names as well as distinct paths, deliberately: the fixture's own two
    occurrences share `esimene.pdf`, so `identifiers` collapses them and removing
    one moves nothing there. A copy that was renamed on its way into the second
    folder is the shape that shows the column really is derived from the live set.
    """
    batch = OpinionArchiveBatch.objects.first()
    assert batch is not None
    return OpinionArchiveItem.objects.create(
        batch=batch,
        archive_sha256="a" * 64,
        archive_relative_path=path,
        original_filename=filename,
        sha256=binary.sha256,
        size_bytes=1024,
        detected_type="application/pdf",
        filename_date=date,
        filename_title=title,
        binary=binary,
    )


def test_deleting_one_filing_leaves_the_row_describing_the_others(held):
    """The defect, in one test: remove an occurrence and read the list."""
    first, second = held
    a_filing(first, path="Opinions/kolmas/kolmas.pdf", filename="kolmas.pdf")
    row = _row(first)
    assert row.occurrence_count == 3
    assert "kolmas.pdf" in row.identifiers

    OpinionArchiveItem.objects.get(archive_relative_path="Opinions/kolmas/kolmas.pdf").delete()

    # No rebuild_archive_index() here, deliberately.
    row = _row(first)
    assert row.occurrence_count == 2
    assert "Opinions/kolmas/kolmas.pdf" not in row.occurrence_paths
    assert "Opinions/2024/esimene.pdf" in row.occurrence_paths
    assert "kolmas.pdf" not in row.identifiers
    assert archive_index_findings() == []
    # And only that row: removing one filing must not touch the corpus.
    assert _row(second).occurrence_count == 1


def test_deleting_the_last_filing_leaves_exactly_what_a_rebuild_would(held):
    """The binary outlives its catalogue, and the row stays to say so.

    Not a guess: the expected state is read off `rebuild_archive_index`, which
    writes a row for every held binary whether the archive still lists it or not.
    The bytes are canonical evidence and the catalogue is not what makes them
    real, so a letter whose filings are all gone is still held, still openable
    and still findable by its hash.
    """
    first, _second = held
    OpinionArchiveItem.objects.filter(binary=first).delete()

    row = _row(first)
    assert row.occurrence_count == 0
    assert row.occurrence_paths == ""
    assert row.title == ""
    assert row.recipient == ""
    assert row.document_date is None
    assert row.source_year is None
    assert row.identifiers == first.sha256

    # The invariant that makes a bounded refresh trustworthy at all.
    assert rebuild_archive_index().written == 0
    assert archive_index_findings() == []


def test_cataloguing_a_further_filing_reaches_the_row_at_once(held):
    """A later snapshot finding the same letter at a new path."""
    first, _second = held
    a_filing(
        first,
        path="Opinions/2025/uuesti.pdf",
        filename="uuesti.pdf",
        title="Ehitusseadustiku muutmine",
    )

    row = _row(first)
    assert row.occurrence_count == 3
    assert "Opinions/2025/uuesti.pdf" in row.occurrence_paths
    assert "uuesti.pdf" in row.identifiers
    assert archive_index_findings() == []


def test_renaming_a_filing_moves_what_the_letter_is_found_by(held, administrator):
    """The five columns a save can move, through the model that owns them.

    No production writer edits a catalogued occurrence today — a later snapshot
    adds occurrences rather than rewriting the ones already recorded — so this is
    the shell session and the next writer. The handler covers it because the
    columns are the row builder's, not because a caller exists.

    The renamed filing is dated *earlier* than its sibling on purpose. `title`,
    `recipient` and `document_date` are the first occurrence's, and "first" is
    the model's own ordering — `filename_date`, then `original_filename` — not
    the order the rows were written in. A test that renamed the arbitrary
    first-by-id row would be asserting a coincidence.
    """
    first, _second = held
    item = OpinionArchiveItem.objects.filter(binary=first).order_by("pk").first()
    assert item is not None

    item.archive_relative_path = "Opinions/2024/ymber-nimetatud.pdf"
    item.original_filename = "ymber-nimetatud.pdf"
    item.filename_title = "Ehitusseadustiku muutmise seaduse eelnou"
    item.filename_recipient = "Kliimaministeerium"
    item.filename_date = datetime.date(2024, 1, 5)
    item.save()

    row = _row(first)
    assert "Opinions/2024/ymber-nimetatud.pdf" in row.occurrence_paths
    assert "ymber-nimetatud.pdf" in row.identifiers
    assert row.title == "Ehitusseadustiku muutmise seaduse eelnou"
    assert row.recipient == "Kliimaministeerium"
    assert row.document_date == datetime.date(2024, 1, 5)
    assert row.source_year == 2024
    assert archive_index_findings() == []
    # And the bounded refresh landed where a rebuild would, ordering included.
    assert rebuild_archive_index().written == 0

    found = search_archive(user=administrator, filters=ArchiveFilters(query="ymber-nimetatud.pdf"))
    assert [hit.binary_id for hit in found] == [first.pk]


def test_moving_a_filing_refreshes_the_row_it_left_and_the_row_it_joined(held):
    """Both binaries, because an occurrence takes its columns with it.

    `post_save` can only see where the occurrence ended up, so the one it left is
    captured on `pre_save` and refreshed alongside. Not reachable from the
    product — `materialize` sets `binary` only where it was null, and does it
    with a queryset `update()` no signal sees — and covered because the field is
    mutable and the row it abandons would otherwise go on counting it.
    """
    first, second = held
    item = OpinionArchiveItem.objects.filter(binary=first).order_by("pk").first()
    assert item is not None
    assert _row(first).occurrence_count == 2
    assert _row(second).occurrence_count == 1

    item.binary = second
    item.save(update_fields=["binary", "updated_at"])

    assert _row(first).occurrence_count == 1
    assert _row(second).occurrence_count == 2
    assert "Opinions/2024/esimene.pdf" in _row(second).occurrence_paths
    assert "Opinions/2024/esimene.pdf" not in _row(first).occurrence_paths
    assert archive_index_findings() == []
    assert rebuild_archive_index().written == 0


def test_clearing_a_filings_binary_frees_the_row_it_leaves(held):
    """The same move, to nowhere: an occurrence back to a catalogue row."""
    first, _second = held
    item = OpinionArchiveItem.objects.filter(binary=first).order_by("pk").first()
    assert item is not None

    item.binary = None
    item.save(update_fields=["binary", "updated_at"])

    assert _row(first).occurrence_count == 1
    assert archive_index_findings() == []


def test_a_queryset_delete_frees_every_row_it_touched(held):
    """`QuerySet.delete()` sends `post_delete` per row, unlike `QuerySet.update()`.

    Which is why deletion needs a handler and the `update()` in `materialize`
    needs its caller to pay instead. Across two binaries in one statement, so a
    handler that refreshed only the first would show.
    """
    first, second = held
    OpinionArchiveItem.objects.filter(
        archive_relative_path__in=["Opinions/koopia/esimene.pdf", "Opinions/naidis.pdf"]
    ).delete()

    assert _row(first).occurrence_count == 1
    assert _row(second).occurrence_count == 0
    assert archive_index_findings() == []
    assert rebuild_archive_index().written == 0


def test_no_cascade_can_reach_an_occurrence(held):
    """Why the delete handler re-projects rather than asking where the delete began.

    An occurrence's only foreign keys are its batch and its binary, and both are
    `PROTECT`. So there is no parent whose deletion takes an occurrence with it:
    every deletion begins at the row or at a queryset of them, and the binary is
    always still there afterwards to be refreshed.
    """
    from django.db.models import ForeignKey
    from django.db.models.deletion import PROTECT, ProtectedError

    parents = [
        field
        for field in OpinionArchiveItem._meta.get_fields()
        if isinstance(field, ForeignKey) and field.model is OpinionArchiveItem
    ]
    assert {field.name for field in parents} == {"batch", "binary"}
    assert all(field.remote_field.on_delete is PROTECT for field in parents)

    # And the database says so too, not only the field declarations.
    first, _second = held
    with pytest.raises(ProtectedError):
        first.delete()
    batch = OpinionArchiveBatch.objects.first()
    assert batch is not None
    with pytest.raises(ProtectedError):
        batch.delete()


def test_a_rolled_back_deletion_takes_its_projection_change_with_it(held):
    """One event, both halves. Class A in ADR 0041's terms is what this asserts."""
    from django.db import transaction

    first, _second = held
    assert _row(first).occurrence_count == 2

    with transaction.atomic():
        doomed = OpinionArchiveItem.objects.filter(binary=first).order_by("pk").first()
        assert doomed is not None
        doomed.delete()
        assert _row(first).occurrence_count == 1
        transaction.set_rollback(True)

    assert OpinionArchiveItem.objects.filter(binary=first).count() == 2
    assert _row(first).occurrence_count == 2
    assert archive_index_findings() == []


def test_suspension_holds_occurrence_writes_back_and_a_bounded_refresh_pays(held):
    """The bulk half, and the exact state the corpus was in before the handlers.

    Suspending reproduces the defect rather than simulating it with an `update()`
    on the projection: the canonical rows really move, the row really does not,
    and `verify` is what has to notice.
    """
    from app.legacy_import.opinion_search import (
        refresh_archive_binaries,
        suspend_archive_indexing,
    )

    first, second = held
    with suspend_archive_indexing():
        a_filing(first, path="Opinions/2025/neljas.pdf", filename="neljas.pdf")
        OpinionArchiveItem.objects.get(archive_relative_path="Opinions/koopia/esimene.pdf").delete()

    row = _row(first)
    assert row.occurrence_count == 2
    assert "Opinions/koopia/esimene.pdf" in row.occurrence_paths
    findings = archive_index_findings()
    assert any("arhiiviteed" in finding for finding in findings), findings
    assert any("tunnused" in finding for finding in findings), findings

    assert refresh_archive_binaries([first.pk]) == 1
    row = _row(first)
    assert row.occurrence_count == 2
    assert "Opinions/koopia/esimene.pdf" not in row.occurrence_paths
    assert "Opinions/2025/neljas.pdf" in row.occurrence_paths
    assert archive_index_findings() == []

    # Bounded: the untouched letter was never rewritten.
    assert refresh_archive_binaries([first.pk, second.pk]) == 0


def test_an_incremental_refresh_matches_a_clean_rebuild(held, normal_matter):
    """The key invariant, over a mixture of every mutation this module covers.

    A bounded refresh is only worth having if it lands where a rebuild would.
    `rebuild_archive_index` recomputes every row and writes only the ones that
    differ, so `written == 0` is the whole assertion.
    """
    first, second = held
    a_filing(first, path="Opinions/2025/viies.pdf", filename="viies.pdf", title="Viies")
    OpinionMatchCandidate.objects.create(
        item=OpinionArchiveItem.objects.filter(binary=second).first(),
        batch=OpinionArchiveBatch.objects.first(),
        matter=normal_matter,
        match_class=OpinionMatchClass.REVIEW_REQUIRED,
        state=OpinionCandidateState.PENDING,
        excel_reference="2024_412",
    )
    moved = OpinionArchiveItem.objects.get(archive_relative_path="Opinions/koopia/esimene.pdf")
    moved.binary = second
    moved.save(update_fields=["binary", "updated_at"])
    OpinionArchiveItem.objects.filter(archive_relative_path="Opinions/naidis.pdf").delete()

    report = rebuild_archive_index()
    assert report.binaries == 2
    assert report.written == 0
    assert archive_index_findings() == []


def test_cataloguing_a_filing_pays_nothing_for_a_move_that_cannot_have_happened(
    held, django_assert_num_queries
):
    """The performance guard on `pre_save`, and it caught a real defect.

    A catalogue writes one of these per occurrence — 767 in one pass over the
    real archive — and `pre_save` reads the stored binary to see whether the row
    is moving. It must not do that for a row that is being created, and
    `BaseModel` fills the primary key in from a `uuid7` default, so the usual
    `instance.pk is None` test is false for every unsaved instance here. It has
    to ask `_state.adding`, and this is what says so: one INSERT, no SELECT.
    """
    batch = OpinionArchiveBatch.objects.first()
    assert batch is not None
    with django_assert_num_queries(1):
        OpinionArchiveItem.objects.create(
            batch=batch,
            archive_sha256="a" * 64,
            archive_relative_path="Opinions/kataloogitud/uus.pdf",
            original_filename="uus.pdf",
            sha256="f" * 64,
            size_bytes=10,
            detected_type="application/pdf",
            binary=None,
        )


def test_one_filing_refreshes_one_letter_and_no_others(held):
    """Bounded, which is the other half of putting this in the transaction.

    Three letters held; touch one. The other two must not be rewritten, and a
    rebuild afterwards must find nothing to do — which is the same statement
    made from the other side.
    """
    first, second = held
    third = hold(sha="e" * 64, title="Kolmas", paths=["Opinions/kolmas.pdf"])
    rebuild_archive_index()
    stamps = dict(OpinionArchiveSearchDocument.objects.values_list("binary_id", "indexed_at"))

    a_filing(first, path="Opinions/2025/kuues.pdf", filename="kuues.pdf")

    after = dict(OpinionArchiveSearchDocument.objects.values_list("binary_id", "indexed_at"))
    assert after[first.pk] != stamps[first.pk]
    assert after[second.pk] == stamps[second.pk]
    assert after[third.pk] == stamps[third.pk]
    assert rebuild_archive_index().written == 0


# ---------------------------------------------------------------------------
# Drift: what `verify` can now see
# ---------------------------------------------------------------------------
#
# Desynchronised with `update()` on the projection, which sends no signals —
# precisely the shape of a future write path that bypasses the handlers above.
# Both directions in each case, because a recomputed value differing from a
# stored one is one test whether the stored one claims too much or too little.


def test_verify_reports_a_row_counting_filings_that_are_gone(held):
    first, _second = held
    assert archive_index_findings() == []

    OpinionArchiveSearchDocument.objects.filter(binary=first).update(occurrence_count=7)

    findings = archive_index_findings()
    assert any("esinemiste arv" in finding for finding in findings), findings
    # Detected, not repaired: the row is still wrong after verify has run.
    assert _row(first).occurrence_count == 7


def test_verify_reports_a_row_missing_a_filing_it_should_count(held):
    first, _second = held
    OpinionArchiveSearchDocument.objects.filter(binary=first).update(occurrence_count=1)

    assert any("esinemiste arv" in finding for finding in archive_index_findings())


def test_verify_reports_a_row_whose_archive_paths_no_longer_match(held):
    first, _second = held
    OpinionArchiveSearchDocument.objects.filter(binary=first).update(
        occurrence_paths="Opinions/2024/esimene.pdf"
    )

    findings = archive_index_findings()
    assert any("arhiiviteed" in finding for finding in findings), findings
    assert not any("esinemiste arv" in finding for finding in findings), findings


def test_verify_reports_a_row_carrying_an_identifier_nothing_holds(held):
    """The check the candidate round could not make.

    Subtracting one identifier from the column cannot be told apart from the rest
    of it by inspection; recomputing the whole column from the live occurrences,
    their metadata and their candidates can.
    """
    first, _second = held
    row = _row(first)
    OpinionArchiveSearchDocument.objects.filter(binary=first).update(
        identifiers=row.identifiers + "\n2024_999"
    )

    findings = archive_index_findings()
    assert any("tunnused" in finding for finding in findings), findings


def test_verify_reports_a_row_whose_heading_no_longer_matches_its_filings(held):
    first, _second = held
    OpinionArchiveSearchDocument.objects.filter(binary=first).update(title="Hoopis teine pealkiri")
    assert any("pealkiri, saaja" in finding for finding in archive_index_findings())

    rebuild_archive_index(force=True)
    assert archive_index_findings() == []

    OpinionArchiveSearchDocument.objects.filter(binary=first).update(source_year=1999)
    assert any("pealkiri, saaja" in finding for finding in archive_index_findings())


def test_a_letter_with_nothing_catalogued_is_not_a_finding(held):
    """No false positives, including for a letter whose filings are all gone."""
    first, _second = held
    OpinionArchiveItem.objects.filter(binary=first).delete()
    assert archive_index_findings() == []
    assert rebuild_archive_index().written == 0


def test_the_occurrence_findings_report_counts_and_nothing_else(held):
    """Operator output, so it may name a number and a class of problem only."""
    first, _second = held
    row = _row(first)
    OpinionArchiveSearchDocument.objects.filter(binary=first).update(
        occurrence_count=9,
        occurrence_paths="Opinions/salajane/leke.pdf",
        identifiers="leke.pdf",
        title="Lekkinud pealkiri",
    )

    findings = archive_index_findings()
    assert len(findings) == 4
    for finding in findings:
        assert first.sha256 not in finding
        assert row.title not in finding
        assert row.occurrence_paths not in finding
        assert row.identifiers not in finding
        assert "leke.pdf" not in finding
        assert "Lekkinud" not in finding


# ---------------------------------------------------------------------------
# Freshness: the metadata half
# ---------------------------------------------------------------------------
#
# The fifth relation, and the one that reads as inert until you look at what it
# feeds. `OpinionArchiveMetadata` is KodaDash's reading of one filing, and the
# row builder takes four columns off it: `external_id` is unioned into
# `identifiers` unconditionally, and `title`, `recipient_raw` and
# `document_date` supply the letter's heading wherever the *filename* did not.
#
# Which is why the fixture below exists. `hold()` gives every occurrence a
# filename title, recipient and date, so metadata never wins there and a test
# that changed one would assert that nothing happened — and pass. The archive
# really contains letters whose names carry none of the three.
#
# The write that mattered is `_write_metadata`, shared by `catalogue_plan` and
# `apply_plan` and suspended by only the second. A KodaDash workbook arriving
# after the archive was catalogued and materialised is one `catalogue` run
# writing readings against occurrences already held and already indexed.


def a_nameless_letter(
    sha: str, *, path: str, date: datetime.date | None = None
) -> OpinionArchiveBinary:
    """A held letter whose filename told us nothing about it.

    The only shape in which metadata's title, recipient and date are the values
    being *projected* rather than spares behind the filename's. The parser reads
    those three out of the name where the archive's convention was followed, and
    it was not always followed; this is the other case.

    `date` is the one part a caller may put back, and it is not cosmetic: a letter
    filed twice is ordered by `filename_date` first, so two undated filings fall
    through to a filename comparison and the answer becomes a question about the
    database's collation. A caller that cares which filing is first passes dates.
    """
    batch = OpinionArchiveBatch.objects.create(
        archive_sha256="a" * 64,
        importer_version="test/0",
        started_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    binary = OpinionArchiveBinary.objects.create(
        sha256=sha,
        size_bytes=1024,
        mime_type="application/pdf",
        storage_key=f"opinion-archive/{sha[:2]}/{sha[2:4]}/{sha}",
        source_archive_sha256="a" * 64,
        materialized_at=timezone.now(),
    )
    OpinionArchiveItem.objects.create(
        batch=batch,
        archive_sha256="a" * 64,
        archive_relative_path=path,
        original_filename=path.rsplit("/", 1)[-1],
        sha256=sha,
        size_bytes=1024,
        detected_type="application/pdf",
        filename_date=date,
        filename_recipient="",
        filename_title="",
        binary=binary,
    )
    return binary


def a_reading(
    binary=None,
    *,
    item=None,
    external_id: str = "X-501",
    title: str = "",
    recipient: str = "",
    date: datetime.date | None = None,
):
    """KodaDash's reading of one filing of these bytes."""
    target = item or OpinionArchiveItem.objects.filter(binary=binary).order_by("pk").first()
    assert target is not None
    return OpinionArchiveMetadata.objects.create(
        item=target,
        source_system=OpinionMetadataSystem.KODADASH,
        source_artifact_name="kd.xlsx",
        source_artifact_sha256="d" * 64,
        external_id=external_id,
        captured_at=timezone.now(),
        title=title,
        recipient_raw=recipient,
        document_date=date,
    )


def test_a_reading_written_after_indexing_reaches_the_row(held):
    """The plainest case: index first, read the workbook second."""
    first, second = held
    assert "X-501" not in _row(first).identifiers

    a_reading(first)

    # No rebuild_archive_index() here, deliberately.
    assert "X-501" in _row(first).identifiers
    assert archive_index_findings() == []
    # And only that letter.
    assert "X-501" not in _row(second).identifiers


def test_a_register_handle_becomes_searchable_without_a_rebuild(held, administrator):
    """`external_id` is the letter's KodaDash-side handle and is indexed as one."""
    first, _second = held
    a_reading(first, external_id="KD-2024-88")

    found = search_archive(user=administrator, filters=ArchiveFilters(query="KD-2024-88"))
    assert [hit.binary_id for hit in found] == [first.pk]


def test_editing_an_external_id_takes_the_old_one_out_of_the_row(held):
    """Not only creation: the column is derived, so a change has to move it."""
    first, _second = held
    reading = a_reading(first, external_id="KD-1")
    assert "KD-1" in _row(first).identifiers

    reading.external_id = "KD-2"
    reading.save(update_fields=["external_id", "updated_at"])

    row = _row(first)
    assert "KD-2" in row.identifiers
    assert "KD-1" not in row.identifiers
    assert archive_index_findings() == []


def test_a_reading_supplies_the_heading_the_filename_did_not(held):
    """Title, recipient and date together, because they fall back together."""
    nameless = a_nameless_letter("1" * 64, path="Opinions/nimeta/1.pdf")
    rebuild_archive_index()
    row = _row(nameless)
    assert (row.title, row.recipient, row.document_date, row.source_year) == ("", "", None, None)

    a_reading(
        nameless,
        title="Riigilõivuseaduse muutmine",
        recipient="Rahandusministeerium",
        date=datetime.date(2023, 7, 14),
    )

    row = _row(nameless)
    assert row.title == "Riigilõivuseaduse muutmine"
    assert row.recipient == "Rahandusministeerium"
    assert row.document_date == datetime.date(2023, 7, 14)
    assert row.source_year == 2023
    assert archive_index_findings() == []
    # And it landed where a rebuild would have.
    assert rebuild_archive_index().written == 0


def test_editing_a_reading_moves_the_heading_it_supplied(held):
    """The correction case, which is what a second workbook revision is."""
    nameless = a_nameless_letter("2" * 64, path="Opinions/nimeta/2.pdf")
    rebuild_archive_index()
    reading = a_reading(nameless, title="Esialgne pealkiri", recipient="Esialgne saaja")
    assert _row(nameless).title == "Esialgne pealkiri"

    reading.title = "Parandatud pealkiri"
    reading.recipient_raw = "Parandatud saaja"
    reading.document_date = datetime.date(2022, 2, 2)
    reading.save()

    row = _row(nameless)
    assert row.title == "Parandatud pealkiri"
    assert row.recipient == "Parandatud saaja"
    assert row.document_date == datetime.date(2022, 2, 2)
    assert row.source_year == 2022
    assert archive_index_findings() == []


def test_the_filename_still_outranks_the_reading_and_the_handler_fires_anyway(held):
    """Precedence is the row builder's, and freshness must not quietly change it.

    The letter here *has* a filename title, so KodaDash's differing one must not
    reach `title` — asserting that it did would be asserting a change of
    semantics this round has no business making. What must still happen is the
    refresh: `identifiers` picks the `external_id` up regardless of precedence,
    which is how the row proves the handler ran at all.
    """
    first, _second = held
    assert _row(first).title == "Ehitusseadustiku muutmine"

    a_reading(first, external_id="KD-9", title="KodaDashi oma pealkiri", recipient="Keegi muu")

    row = _row(first)
    assert row.title == "Ehitusseadustiku muutmine"
    assert row.recipient == "Naidisministeerium"
    assert "KD-9" in row.identifiers
    assert archive_index_findings() == []


def test_deleting_a_reading_takes_its_contribution_back(held):
    """Both halves: the handle leaves `identifiers`, the heading falls back."""
    nameless = a_nameless_letter("3" * 64, path="Opinions/nimeta/3.pdf")
    rebuild_archive_index()
    reading = a_reading(
        nameless,
        external_id="KD-77",
        title="Kaob ära",
        recipient="Kaob ka",
        date=datetime.date(2021, 3, 4),
    )
    assert "KD-77" in _row(nameless).identifiers

    reading.delete()

    row = _row(nameless)
    assert "KD-77" not in row.identifiers
    assert row.identifiers == nameless.sha256 + "\n3.pdf"
    assert row.title == ""
    assert row.recipient == ""
    assert row.document_date is None
    assert row.source_year is None
    assert archive_index_findings() == []
    assert rebuild_archive_index().written == 0


def test_a_second_reading_still_covers_a_deleted_one(held):
    """Falling back to the *next* canonical source rather than to nothing.

    Both filings are dated, and differently, because otherwise the answer is not
    this code's. `_metadata_rows` carries the model's `["item", "source_system"]`
    ordering, and Django resolves an ordering *by a relation* through the related
    model's own default — `filename_date`, then `original_filename` — so two
    undated filings are tie-broken by a filename string. Whether `4b.pdf` sorts
    before `4.pdf` is then a question about the collation: glibc `en_US.utf8`
    ignores the dot at the primary level, the local ICU cluster does not, and the
    test passed here and failed in CI. A date decides it in both.
    """
    nameless = a_nameless_letter(
        "4" * 64, path="Opinions/nimeta/4.pdf", date=datetime.date(2021, 1, 11)
    )
    second_filing = a_filing(
        nameless,
        path="Opinions/nimeta/koopia/4.pdf",
        filename="4b.pdf",
        date=datetime.date(2021, 6, 22),
    )
    rebuild_archive_index()

    first_reading = a_reading(nameless, external_id="KD-A", title="Esimene lugem")
    a_reading(item=second_filing, external_id="KD-B", title="Teine lugem")
    assert _row(nameless).title == "Esimene lugem"

    first_reading.delete()

    row = _row(nameless)
    assert row.title == "Teine lugem"
    assert "KD-A" not in row.identifiers
    assert "KD-B" in row.identifiers
    assert archive_index_findings() == []


def test_a_reading_of_an_unmaterialised_filing_is_ignored_rather_than_fatal(held):
    """A catalogued path whose bytes nobody has copied yet has no row to move."""
    first, _second = held
    batch = OpinionArchiveBatch.objects.first()
    assert batch is not None
    orphan = OpinionArchiveItem.objects.create(
        batch=batch,
        archive_sha256="a" * 64,
        archive_relative_path="Opinions/kataloogitud/lugem.pdf",
        original_filename="lugem.pdf",
        sha256="9" * 64,
        size_bytes=10,
        detected_type="application/pdf",
        binary=None,
    )
    reading = a_reading(item=orphan, external_id="KD-ORB")

    reading.title = "Muudetud"
    reading.save(update_fields=["title", "updated_at"])
    reading.delete()

    assert OpinionArchiveSearchDocument.objects.count() == 2
    assert "KD-ORB" not in _row(first).identifiers
    assert archive_index_findings() == []


def test_an_item_cascade_lands_on_the_final_projection_without_an_early_refresh(held):
    """The delete guard, and why it is the link's rather than the candidate's.

    Metadata is `CASCADE` from its occurrence and the collector removes children
    first, so a refresh fired from the metadata handler here would recompute a
    row that still contained the occurrence being deleted — one statement early,
    which is indistinguishable from not running. The occurrence's own
    `post_delete` is what owns the answer, and this asserts the answer rather
    than the mechanism: what is left is exactly what a rebuild would write.
    """
    first, _second = held
    doomed = OpinionArchiveItem.objects.filter(binary=first).order_by("pk").first()
    assert doomed is not None
    a_reading(item=doomed, external_id="KD-CASCADE")
    assert "KD-CASCADE" in _row(first).identifiers

    doomed.delete()

    assert OpinionArchiveMetadata.objects.count() == 0
    row = _row(first)
    assert "KD-CASCADE" not in row.identifiers
    assert row.occurrence_count == 1
    assert archive_index_findings() == []
    assert rebuild_archive_index().written == 0


def test_an_item_cascade_fires_exactly_one_refresh_and_it_is_the_items(held):
    """The delete guard itself, not only what it leaves behind.

    The test above asserts the *outcome*, and the outcome is right under either
    guard: a metadata handler firing mid-cascade would recompute a row that
    still contained the occurrence being deleted, and the occurrence's own
    `post_delete` would then correct it. Right answer, wasted work, and — the
    part that matters — a projection that was briefly wrong inside the
    transaction for no reason.

    So this counts. One refresh, fired after the children are gone, is what
    `_started_here` on the metadata handler buys; `_binary_survives` there would
    make it two.
    """
    from app.legacy_import import opinion_search_signals as signals

    first, _second = held
    doomed = OpinionArchiveItem.objects.filter(binary=first).order_by("pk").first()
    assert doomed is not None
    a_reading(item=doomed, external_id="KD-COUNTED")

    refreshed: list[Any] = []
    real = signals.refresh_archive_binary

    def counting(binary_id: Any) -> int:
        if binary_id is not None:
            refreshed.append(binary_id)
        return real(binary_id)

    signals.refresh_archive_binary = counting  # type: ignore[assignment]
    try:
        doomed.delete()
    finally:
        signals.refresh_archive_binary = real  # type: ignore[assignment]

    assert refreshed == [first.pk], refreshed
    assert "KD-COUNTED" not in _row(first).identifiers


def test_moving_a_reading_refreshes_the_row_it_left_and_the_row_it_joined(held):
    """Both binaries, because a reading takes its four columns with it.

    Nothing in production reassigns `item`: `_write_metadata` only ever creates,
    and a changed workbook writes a new row because the artefact's hash is part
    of the key. The field is an ordinary editable foreign key with no unique
    constraint over it, so an ordinary save can do this — which is the whole
    reason the binary it left is captured on `pre_save`.
    """
    first, second = held
    reading = a_reading(first, external_id="KD-MOVE")
    assert "KD-MOVE" in _row(first).identifiers
    assert "KD-MOVE" not in _row(second).identifiers

    reading.item = OpinionArchiveItem.objects.filter(binary=second).order_by("pk").first()
    reading.save(update_fields=["item", "updated_at"])

    assert "KD-MOVE" not in _row(first).identifiers
    assert "KD-MOVE" in _row(second).identifiers
    assert archive_index_findings() == []
    assert rebuild_archive_index().written == 0


def test_moving_a_reading_between_filings_of_one_letter_moves_no_row(held):
    """The same save, where both filings are the same bytes: one row, unchanged."""
    first, _second = held
    reading = a_reading(first, external_id="KD-SAME")
    before = _row(first).indexed_at

    filings = list(OpinionArchiveItem.objects.filter(binary=first).order_by("pk"))
    reading.item = filings[1]
    reading.save(update_fields=["item", "updated_at"])

    row = _row(first)
    assert "KD-SAME" in row.identifiers
    # `_reindex` compares before it writes, so a save that moved nothing the
    # projection can see does not rewrite the row.
    assert row.indexed_at == before
    assert archive_index_findings() == []


def test_a_rolled_back_reading_takes_its_projection_change_with_it(held):
    """One event, both halves. Class A in ADR 0041's terms is what this asserts."""
    from django.db import transaction

    first, _second = held
    assert "KD-ROLL" not in _row(first).identifiers

    with transaction.atomic():
        a_reading(first, external_id="KD-ROLL")
        assert "KD-ROLL" in _row(first).identifiers
        transaction.set_rollback(True)

    assert OpinionArchiveMetadata.objects.count() == 0
    assert "KD-ROLL" not in _row(first).identifiers
    assert archive_index_findings() == []


def test_suspension_holds_readings_back_and_a_bounded_refresh_pays(held):
    """The bulk half, for the relation `apply_plan` writes under suspension."""
    from app.legacy_import.opinion_search import (
        refresh_archive_binaries,
        suspend_archive_indexing,
    )

    first, second = held
    with suspend_archive_indexing():
        a_reading(first, external_id="KD-BULK")

    # Suspended means stale, and `verify` is what notices.
    assert "KD-BULK" not in _row(first).identifiers
    assert any("tunnused" in finding for finding in archive_index_findings())

    assert refresh_archive_binaries([first.pk]) == 1
    assert "KD-BULK" in _row(first).identifiers
    assert archive_index_findings() == []

    # Bounded: the untouched letter was never rewritten.
    assert refresh_archive_binaries([first.pk, second.pk]) == 0


def test_cataloguing_a_reading_pays_nothing_for_a_move_that_cannot_have_happened(
    held, django_assert_num_queries
):
    """The performance guard on `pre_save`, for the model written 767 times.

    `BaseModel` fills the primary key in from a `uuid7` default, so
    `instance.pk is None` is false for an unsaved row and only `_state.adding`
    can tell a create from a move. One INSERT, no SELECT — and no refresh, since
    the occurrence has no bytes yet.
    """
    batch = OpinionArchiveBatch.objects.first()
    assert batch is not None
    orphan = OpinionArchiveItem.objects.create(
        batch=batch,
        archive_sha256="a" * 64,
        archive_relative_path="Opinions/kataloogitud/loendus.pdf",
        original_filename="loendus.pdf",
        sha256="8" * 64,
        size_bytes=10,
        detected_type="application/pdf",
        binary=None,
    )
    with django_assert_num_queries(2):
        # One INSERT, and the one `_binary_of` lookup that finds no bytes.
        a_reading(item=orphan, external_id="KD-COUNT")


def test_verify_reports_a_row_carrying_a_register_handle_nothing_holds(held):
    """Metadata drift, through the identifier check rather than a second copy of it.

    `_occurrence_values` already unions `external_id` into `identifiers`, so the
    check that recomputes the whole column sees a metadata change without any
    metadata-specific comparison — which is why this round adds no verifier for
    it. Desynchronised with `update()` on the projection, which sends no signals.
    """
    first, _second = held
    a_reading(first, external_id="KD-DRIFT")
    assert archive_index_findings() == []

    row = _row(first)
    OpinionArchiveSearchDocument.objects.filter(binary=first).update(
        identifiers=row.identifiers.replace("KD-DRIFT", "KD-KUSKIL-MUJAL")
    )

    findings = archive_index_findings()
    assert any("tunnused" in finding for finding in findings), findings
    # Detected, not repaired.
    assert "KD-DRIFT" not in _row(first).identifiers


def test_verify_reports_a_heading_that_no_longer_matches_its_reading(held):
    """The other direction of the same fallback, and the date with it."""
    nameless = a_nameless_letter("5" * 64, path="Opinions/nimeta/5.pdf")
    rebuild_archive_index()
    a_reading(nameless, title="Kanooniline pealkiri", date=datetime.date(2020, 5, 6))
    assert archive_index_findings() == []

    OpinionArchiveSearchDocument.objects.filter(binary=nameless).update(title="Vana pealkiri")
    assert any("pealkiri, saaja" in finding for finding in archive_index_findings())

    rebuild_archive_index(force=True)
    assert archive_index_findings() == []

    OpinionArchiveSearchDocument.objects.filter(binary=nameless).update(source_year=1999)
    assert any("pealkiri, saaja" in finding for finding in archive_index_findings())


def test_a_letter_with_no_reading_at_all_is_not_a_finding(held):
    """The clean corpus stays clean: absence is a value, not drift."""
    assert OpinionArchiveMetadata.objects.count() == 0
    assert archive_index_findings() == []


# ---------------------------------------------------------------------------
# Freshness: the text half
# ---------------------------------------------------------------------------
#
# The sixth relation, and the one the archive's full-text search is made of:
# `body_text` is what both search vectors are built from and `has_body_text` is
# what the `Sisuga` filter and the coverage figure read.
#
# Written through `opinion_text._record` rather than through the model, because
# that is the one production writer — an `update_or_create` per binary inside
# its own atomic block, called by `extract_all` for every letter whose text is
# not already current. Testing `OpinionArchiveText.objects.create()` would
# exercise a path nothing runs, and the interesting half is the *update*: a
# re-extraction replacing a body, or a policy change withdrawing one.
#
# The synthetic PDFs in `tests/synthetic_opinions.py` carry no text stream, so
# a body has to be recorded directly here; `tests/test_opinion_archive_evidence.py`
# walks the real `extract_all` end to end for the outcomes it can produce.


def a_parse(binary, *, body: str = "", state: str = ArchiveTextState.DONE, note: str = ""):
    """One extraction outcome, through the function every extraction goes through."""
    from app.legacy_import.opinion_text import _record

    return _record(binary, state=state, body=body, note=note)


def test_extracting_text_reaches_the_row_through_the_writer_that_extracts_it(held):
    """The plainest case: index first, extract second, read the list."""
    first, second = held
    assert _row(second).has_body_text is False
    assert _row(second).body_text == ""

    a_parse(second, body="Riigilõivuseaduse muutmise kohta.")

    # No rebuild_archive_index() here, deliberately.
    row = _row(second)
    assert row.has_body_text is True
    assert row.body_text == "Riigilõivuseaduse muutmise kohta."
    assert archive_index_findings() == []
    # And it landed where a rebuild would have, vectors included.
    assert rebuild_archive_index().written == 0
    # Only that letter.
    assert _row(first).body_text.startswith("Käesolevaga")


def test_a_freshly_extracted_body_is_searchable_without_a_rebuild(held, administrator):
    """Held, extracted and findable are one event rather than three."""
    _first, second = held
    a_parse(second, body="Sünteetiline lõik riigilõivude ümberkorraldamise kohta.")

    found = search_archive(user=administrator, filters=ArchiveFilters(query="ümberkorraldamise"))
    assert [hit.binary_id for hit in found] == [second.pk]


def test_a_re_extraction_replaces_what_the_letter_is_findable_by(held, administrator):
    """The direction the old lag check could not see.

    A body that was replaced left the row serving the previous parse, so the
    letter stayed findable by words that had been superseded and could not be
    found by the ones that replaced them. `update_or_create` is the real writer
    and this is its update branch.
    """
    first, _second = held
    assert "ehitusseadustiku" in _row(first).body_text.lower()

    a_parse(first, body="Hoopis teine sisu: keskkonnatasude arvestus.")

    row = _row(first)
    assert row.body_text == "Hoopis teine sisu: keskkonnatasude arvestus."
    assert row.has_body_text is True
    assert (
        list(search_archive(user=administrator, filters=ArchiveFilters(query="keskkonnatasude")))
        != []
    )
    assert (
        list(search_archive(user=administrator, filters=ArchiveFilters(query="ehitusseadustiku")))
        != []
    ), "still found by its title, which is not the body"
    body_hit = search_archive(user=administrator, filters=ArchiveFilters(query="Käesolevaga"))
    assert list(body_hit) == [], "the superseded body is gone from the index"
    assert archive_index_findings() == []
    assert rebuild_archive_index().written == 0


@pytest.mark.parametrize(
    "state",
    [ArchiveTextState.BLOCKED, ArchiveTextState.NO_TEXT_LAYER, ArchiveTextState.FAILED],
)
def test_an_outcome_that_is_not_a_body_clears_the_searchable_one(held, state):
    """Every honest non-body outcome removes from the corpus, and must say so.

    `BLOCKED` is the real one: turning `REAL_DATA_ALLOWED` on withdraws the
    parser's permission and re-records every row, and a projection that went on
    serving those bodies would be searchable content the policy had just
    forbidden opening. `NO_TEXT_LAYER` and `FAILED` are the same shape.
    """
    first, _second = held
    assert _row(first).has_body_text is True

    a_parse(first, state=state, note="Poliitika või parser.")

    row = _row(first)
    assert row.has_body_text is False
    assert row.body_text == ""
    assert archive_index_findings() == []
    assert rebuild_archive_index().written == 0


def test_a_done_row_that_came_back_empty_is_not_a_body_either(held):
    """`has_body` folds the state question into the emptiness one, in one place."""
    first, _second = held
    a_parse(first, state=ArchiveTextState.DONE, body="")

    row = _row(first)
    assert row.has_body_text is False
    assert row.body_text == ""
    assert archive_index_findings() == []


def test_deleting_a_parse_clears_the_searchable_body(held):
    """Dropping a parse to force a re-extraction, which is a real operator move."""
    first, _second = held
    text = OpinionArchiveText.objects.get(binary=first)

    text.delete()

    row = _row(first)
    assert row.has_body_text is False
    assert row.body_text == ""
    assert archive_index_findings() == []
    assert rebuild_archive_index().written == 0


def test_a_queryset_delete_of_parses_frees_every_row_it_touched(held):
    """`QuerySet.delete()` sends `post_delete` per row, across two letters at once."""
    first, second = held
    a_parse(second, body="Teine keha.")
    assert _row(first).has_body_text is True
    assert _row(second).has_body_text is True

    OpinionArchiveText.objects.all().delete()

    assert _row(first).has_body_text is False
    assert _row(second).has_body_text is False
    assert archive_index_findings() == []


def test_deleting_a_binary_neither_recreates_its_row_nor_fails(held):
    """The reason the delete handler asks `_binary_survives` rather than nothing.

    Text is `CASCADE` from the binary and is the *first* archive relation where
    that path is reachable: the occurrences and candidates are kept off it by
    `OpinionArchiveItem.binary` being `PROTECT`. A handler that re-projected
    mid-cascade would insert a row the collector had already swept past, and the
    binary's own delete would then fail on a foreign key at COMMIT.
    """
    first, _second = held
    stray = OpinionArchiveBinary.objects.create(
        sha256="7" * 64,
        size_bytes=32,
        mime_type="application/pdf",
        storage_key="opinion-archive/77/77/" + "7" * 64,
        source_archive_sha256="a" * 64,
        materialized_at=timezone.now(),
    )
    a_parse(stray, body="Kirja keha, mille kataloogikirjed on kustutatud.")
    assert OpinionArchiveSearchDocument.objects.filter(binary=stray).exists()

    stray.delete()

    assert not OpinionArchiveBinary.objects.filter(pk=stray.pk).exists()
    assert not OpinionArchiveText.objects.filter(binary_id=stray.pk).exists()
    assert not OpinionArchiveSearchDocument.objects.filter(binary_id=stray.pk).exists()
    assert _row(first).has_body_text is True
    assert archive_index_findings() == []


def test_moving_a_parse_refreshes_the_row_it_left_and_the_row_it_joined(held):
    """Both binaries, because a body is only searchable under the bytes it is on.

    `binary` is a `OneToOneField`, so this is legal only onto bytes holding no
    text of their own — and it is legal, which is why the row it left is
    captured on `pre_save`. No production writer does it: `_record` keys
    `update_or_create` on the binary it was handed.
    """
    first, second = held
    assert _row(second).has_body_text is False
    text = OpinionArchiveText.objects.get(binary=first)

    text.binary = second
    text.save(update_fields=["binary", "updated_at"])

    assert _row(first).has_body_text is False
    assert _row(first).body_text == ""
    assert _row(second).has_body_text is True
    assert _row(second).body_text.startswith("Käesolevaga")
    assert archive_index_findings() == []
    assert rebuild_archive_index().written == 0


def test_a_rolled_back_extraction_takes_its_projection_change_with_it(held):
    """One event, both halves — and `_record` already opens the transaction."""
    from django.db import transaction

    _first, second = held
    assert _row(second).has_body_text is False

    with transaction.atomic():
        a_parse(second, body="Kirjutamata keha.")
        assert _row(second).has_body_text is True
        transaction.set_rollback(True)

    assert not OpinionArchiveText.objects.filter(binary=second).exists()
    row = _row(second)
    assert row.has_body_text is False
    assert row.body_text == ""
    assert archive_index_findings() == []


def test_suspension_holds_extractions_back_and_a_bounded_refresh_pays(held):
    """The bulk half, and both directions of it in one run."""
    from app.legacy_import.opinion_search import (
        refresh_archive_binaries,
        suspend_archive_indexing,
    )

    first, second = held
    with suspend_archive_indexing():
        a_parse(second, body="Uus keha, mida projektsioon veel ei tea.")
        a_parse(first, state=ArchiveTextState.BLOCKED, note="Poliitika muutus.")

    # Suspended means stale, and `verify` is what notices — in both directions.
    assert _row(second).has_body_text is False
    assert _row(first).has_body_text is True
    findings = archive_index_findings()
    assert any("otsitav tekst" in finding for finding in findings), findings
    assert any("tekstiolek" in finding for finding in findings), findings

    assert refresh_archive_binaries([first.pk, second.pk]) == 2
    assert _row(second).body_text == "Uus keha, mida projektsioon veel ei tea."
    assert _row(first).body_text == ""
    assert _row(first).has_body_text is False
    assert archive_index_findings() == []

    # Bounded: a second pass over already-fresh rows writes nothing.
    assert refresh_archive_binaries([first.pk, second.pk]) == 0


def test_an_extraction_pays_nothing_for_a_move_it_cannot_have_made(held):
    """The performance guard on `pre_save`, over the real writer.

    `_record` hands `update_or_create` eight concrete columns and none of them
    is `binary`, so Django saves with `update_fields` and `note_text_move`
    returns before its lookup — the same guard `note_occurrence_move` carries,
    for the same reason: an extraction over the whole corpus must not pay a
    SELECT per letter for a reassignment its writer cannot perform.

    Asserted relatively rather than against a number. The bounded refresh's own
    query count belongs to `_row_values` and will move when a column joins it;
    what this is about is the one lookup the guard skips, so it compares a save
    that names its fields against the same save that does not.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    first, _second = held
    recorded = a_parse(first, body="Teine lugem.")
    assert getattr(recorded, "_archive_binary_before", None) is None

    text = OpinionArchiveText.objects.get(binary=first)
    with CaptureQueriesContext(connection) as named:
        text.note = "Nimetatud väljad."
        text.save(update_fields=["note", "updated_at"])
    with CaptureQueriesContext(connection) as everything:
        text.note = "Kõik väljad."
        text.save()

    # Exactly one query apart, and it is the move lookup. Neither save moves a
    # projected column, so both pay the same comparison and neither writes.
    assert len(everything.captured_queries) == len(named.captured_queries) + 1


def test_extraction_is_not_batched_behind_a_suspension(held):
    """The design decision, asserted rather than described.

    Suspending indexing for the whole of `extract_all` and refreshing at the end
    would be cheaper and wrong: hundreds of canonical bodies would commit while
    their rows stayed stale, and an extraction killed halfway would leave
    exactly the search this closes. `_record` is atomic per letter, so each
    committed body is committed with its projection.
    """
    import inspect

    from app.legacy_import import opinion_text

    source = inspect.getsource(opinion_text)
    assert "suspend_archive_indexing" not in source
    assert "refresh_archive_binaries" not in source


# ---------------------------------------------------------------------------
# Drift: what `verify` can now see about text
# ---------------------------------------------------------------------------
#
# The check this replaces was one-directional — canonical `DONE` with a body,
# projection saying it has none — which was the case that had happened and not
# the class of case. Both columns are now recomputed through `_text_values`, the
# row builder's own function, and compared against what is stored.


def test_verify_reports_a_body_the_projection_has_not_picked_up(held):
    """The original finding's case, still detected after the rewrite."""
    _first, second = held
    a_parse(second, body="Eraldatud keha.")
    assert archive_index_findings() == []

    OpinionArchiveSearchDocument.objects.filter(binary=second).update(
        body_text="", has_body_text=False
    )

    findings = archive_index_findings()
    assert any("otsitav tekst" in finding for finding in findings), findings
    assert any("tekstiolek" in finding for finding in findings), findings


def test_verify_reports_a_stale_body_whose_flag_still_says_true(held):
    """The case the flag alone cannot show, and the worse of the two.

    `has_body_text` is right, so every count and filter looks healthy; the
    *searchable* text is the previous extraction's, so the letter answers to
    words nothing holds. A verifier that compared only the flag would report a
    clean corpus.
    """
    first, _second = held
    OpinionArchiveSearchDocument.objects.filter(binary=first).update(
        body_text="Eelmise eraldamise keha, mida enam ei ole."
    )

    findings = archive_index_findings()
    assert any("otsitav tekst" in finding for finding in findings), findings
    assert not any("tekstiolek" in finding for finding in findings), findings
    # Detected, not repaired.
    assert _row(first).body_text.startswith("Eelmise")


def test_verify_reports_a_row_claiming_a_body_after_the_policy_withdrew_it(held):
    """Canonical text is no longer searchable and the row still says it is."""
    first, _second = held
    OpinionArchiveText.objects.filter(binary=first).update(state=ArchiveTextState.BLOCKED)

    findings = archive_index_findings()
    assert any("otsitav tekst" in finding for finding in findings), findings
    assert any("tekstiolek" in finding for finding in findings), findings


def test_verify_reports_a_row_claiming_a_body_that_was_never_extracted(held):
    """No canonical text row at all, and the projection carrying one anyway."""
    _first, second = held
    assert not OpinionArchiveText.objects.filter(binary=second).exists()
    OpinionArchiveSearchDocument.objects.filter(binary=second).update(
        body_text="Välja mõeldud keha.", has_body_text=True
    )

    findings = archive_index_findings()
    assert any("otsitav tekst" in finding for finding in findings), findings
    assert any("tekstiolek" in finding for finding in findings), findings


def test_verify_reports_a_flag_that_has_come_apart_from_its_body(held):
    """The inconsistency in isolation: the body is right, the flag is not."""
    first, _second = held
    OpinionArchiveSearchDocument.objects.filter(binary=first).update(has_body_text=False)

    findings = archive_index_findings()
    assert any("tekstiolek" in finding for finding in findings), findings
    assert not any("otsitav tekst" in finding for finding in findings), findings


def test_a_clean_metadata_and_text_projection_produces_no_finding(held):
    """No false positives over the whole of what this round added.

    A letter with a reading and a body, a letter with a reading and no body, and
    a letter with neither — plus the fallback shape, where metadata is the value
    being projected rather than a spare.
    """
    first, second = held
    nameless = a_nameless_letter("6" * 64, path="Opinions/nimeta/6.pdf")
    a_reading(first, external_id="KD-CLEAN-1")
    a_reading(nameless, external_id="KD-CLEAN-2", title="Puhas", date=datetime.date(2019, 8, 9))
    a_parse(second, body="Puhas keha.")
    a_parse(nameless, state=ArchiveTextState.NO_TEXT_LAYER)

    assert archive_index_findings() == []
    assert rebuild_archive_index().written == 0


def test_the_text_findings_report_counts_and_nothing_else(held):
    """Operator output, so it may name a number and a class of problem only.

    The fixture values are deliberately secret-shaped: this output is meant to
    be pasted into a ticket, and the columns being compared are the contents of
    a letter and the name of a file.
    """
    first, second = held
    leaky_body = "SALAJANE-KEHA-9931 sisemine seisukoht"
    leaky_id = "SALAJANE-TUNNUS-4417"
    leaky_title = "SALAJANE-PEALKIRI-2208"
    a_reading(second, external_id=leaky_id, title=leaky_title)
    a_parse(second, body=leaky_body)
    assert archive_index_findings() == []

    OpinionArchiveSearchDocument.objects.filter(binary=second).update(
        body_text="SALAJANE-VANA-KEHA-7755", has_body_text=False, identifiers="SALAJANE-LEKE-1001"
    )
    OpinionArchiveSearchDocument.objects.filter(binary=first).update(title="SALAJANE-LEKE-1002")

    findings = archive_index_findings()
    assert findings
    leaks = [
        leaky_body,
        leaky_id,
        leaky_title,
        "SALAJANE-VANA-KEHA-7755",
        "SALAJANE-LEKE-1001",
        "SALAJANE-LEKE-1002",
        first.sha256,
        second.sha256,
        "Opinions/",
        ".pdf",
    ]
    for finding in findings:
        for leak in leaks:
            assert leak not in finding, finding


# ---------------------------------------------------------------------------
# The invalidation map, discovered rather than listed
# ---------------------------------------------------------------------------
#
# ADR 0041's contract is about completeness, and a completeness claim written
# down as prose is a claim that stops being true quietly. The three follow-ups
# that brought the archive projection inside the contract each began the same
# way: somebody read `_row_values`, found a relation nobody had noticed, and
# the corpus had been stale on it for a fortnight.
#
# So the map is read off the row builder rather than repeated beside it. What
# the builder actually queries is what it depends on, and every table it
# queries must have something listening for writes to it.


def test_every_relation_the_archive_row_reads_has_a_freshness_owner(held):
    """The whole invalidation map, taken from the queries the builder runs.

    Not from a list, and not from its source text either: `OpinionArchiveText`
    is reached as `binary.text` and is named nowhere in `_row_values`, so a
    scan for model names would have missed the relation the archive's entire
    full-text search is made of. The queries cannot miss it.

    Deliberately a discovery and an assertion in one. A future column computed
    from a seventh relation makes this fail with the table's own name, which is
    the sentence the last three rounds each had to be told by a person.
    """
    import re

    from django.apps import apps
    from django.db import connection
    from django.db.models.signals import post_delete, post_save
    from django.test.utils import CaptureQueriesContext

    from app.legacy_import.opinion_search import _row_values

    first, _second = held

    # Fetched without `select_related`, so every relation the builder reads
    # costs a query and therefore shows up here. `_reindex` selects the text
    # eagerly, which is an optimisation rather than a different set of inputs.
    binary = OpinionArchiveBinary.objects.get(pk=first.pk)
    with CaptureQueriesContext(connection) as captured:
        _row_values(binary)

    tables: set[str] = set()
    for query in captured.captured_queries:
        tables.update(re.findall(r'(?:FROM|JOIN) "([a-z0-9_]+)"', query["sql"]))

    by_table = {model._meta.db_table: model for model in apps.get_models()}
    inputs = {by_table[table] for table in tables if table in by_table}
    # The binary itself is the row's subject rather than one of its inputs: it
    # is already loaded, `sha256` is the only column of it that reaches the row,
    # and no production writer saves one — `materialize` creates them and
    # refreshes, and a binary with no row at all is what `unindexed_binaries`
    # reports.
    inputs.discard(OpinionArchiveBinary)
    inputs.discard(OpinionArchiveSearchDocument)

    assert inputs == {
        OpinionArchiveItem,
        OpinionArchiveMetadata,
        OpinionMatchCandidate,
        OpinionSubmissionImport,
        OpinionArchiveMatterLink,
        OpinionArchiveText,
    }, sorted(model.__name__ for model in inputs)

    unowned = [
        model.__name__
        for model in inputs
        if not (
            post_save.has_listeners(model)
            and post_delete.has_listeners(model)
            and model.__name__ in _signal_source()
        )
    ]
    assert unowned == [], unowned


def _signal_source() -> str:
    import inspect

    from app.legacy_import import opinion_search_signals

    return inspect.getsource(opinion_search_signals)


# ---------------------------------------------------------------------------
# The builder and the verifier have to agree
# ---------------------------------------------------------------------------
#
# Production deployed a release whose `opinion_archive_search verify` reported
# two drifting rows, and a full `rebuild --force` — every row recomputed and
# rewritten — left the same two rows reported by the same finding. That is not
# stale data. A rebuild that cannot converge means the row builder and the drift
# check disagree about what the row should say, and they disagreed because the
# order they read canonical rows in was not defined.
#
# Identity for an occurrence is (archive sha, path, content sha), so one letter
# filed at two paths under the same name is two rows tying on `filename_date`
# and `original_filename` — both of the keys the projection ordered by. The
# builder reads one binary (`WHERE binary_id = %s`), the drift check reads the
# corpus (`WHERE binary_id IS NOT NULL`) and groups it back; two queries, two
# plans, and PostgreSQL owes neither of them a particular order among tied rows.
# `identifiers` and `occurrence_paths` are joined in the order the rows arrive,
# so the two paths can disagree for ever about a value neither is wrong about.


def tied_letter(
    *,
    sha: str,
    paths: list[str],
    filename: str = "sama-nimi.pdf",
    date: datetime.date | None = None,
    external_ids: list[str] | None = None,
    excel_references: list[str] | None = None,
) -> tuple[OpinionArchiveBinary, list[OpinionArchiveItem]]:
    """One letter filed at several paths under one name.

    Deliberately ambiguous where the projection used to order: every occurrence
    carries the same `original_filename` and the same `filename_date`, and
    differs only in the path — which is exactly what the uniqueness constraint
    allows and what the corpus actually holds. The optional readings and
    proposals hang off separate occurrences so their `external_id` and
    `excel_reference` reach `identifiers` through the tie.
    """
    batch = OpinionArchiveBatch.objects.create(
        archive_sha256="a" * 64,
        importer_version="test/0",
        started_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    binary = OpinionArchiveBinary.objects.create(
        sha256=sha,
        size_bytes=1024,
        mime_type="application/pdf",
        storage_key=f"opinion-archive/{sha[:2]}/{sha[2:4]}/{sha}",
        source_archive_sha256="a" * 64,
        materialized_at=timezone.now(),
    )
    items = [
        OpinionArchiveItem.objects.create(
            batch=batch,
            archive_sha256="a" * 64,
            archive_relative_path=path,
            original_filename=filename,
            sha256=sha,
            size_bytes=1024,
            detected_type="application/pdf",
            filename_date=date or datetime.date(2024, 4, 10),
            filename_recipient="Naidisministeerium",
            filename_title="Sama pealkiri",
            binary=binary,
        )
        for path in paths
    ]
    for item, external_id in zip(items, external_ids or [], strict=False):
        OpinionArchiveMetadata.objects.create(
            item=item,
            source_system=OpinionMetadataSystem.KODADASH,
            source_artifact_name="kd.xlsx",
            source_artifact_sha256="d" * 64,
            external_id=external_id,
            captured_at=timezone.now(),
            title="Sama pealkiri",
            recipient_raw="Naidisministeerium",
            document_date=date or datetime.date(2024, 4, 10),
        )
    for item, reference in zip(items, excel_references or [], strict=False):
        OpinionMatchCandidate.objects.create(
            item=item,
            batch=item.batch,
            match_class=OpinionMatchClass.REVIEW_REQUIRED,
            state=OpinionCandidateState.PENDING,
            excel_reference=reference,
        )
    return binary, items


def ambiguous_corpus() -> list[OpinionArchiveBinary]:
    """Several letters, each of them ambiguous in the old ordering keys."""
    return [
        tied_letter(
            sha="d" * 64,
            paths=["Opinions/2024/uks.pdf", "Opinions/koopia/uks.pdf", "Opinions/arh/uks.pdf"],
            external_ids=["KD-3", "KD-1", "KD-2"],
            excel_references=["XL-9", "XL-7", "XL-8"],
        )[0],
        tied_letter(
            sha="e" * 64,
            paths=["Opinions/2023/kaks.pdf", "Opinions/vana/kaks.pdf"],
            filename="teine-nimi.pdf",
            date=datetime.date(2023, 9, 1),
            external_ids=["KD-22", "KD-11"],
            excel_references=["XL-22", "XL-11"],
        )[0],
    ]


def _order_by_clause(sql: str) -> str:
    marker = " ORDER BY "
    index = sql.upper().rfind(marker)
    return "" if index == -1 else sql[index + len(marker) :]


@pytest.mark.parametrize("scoped", [True, False], ids=["per-binary", "corpus-wide"])
def test_the_shared_row_fetches_order_by_something_unique(db, scoped):
    """No tie may be left for the query plan to break.

    The root cause, pinned at the only place it can be pinned deterministically:
    a small test corpus will not reliably reproduce a plan difference, but an
    ordering that ends in a unique column cannot *have* one. Every shared fetch
    ends with its own primary key, so narrowing to one binary provably leaves
    the relative order of that binary's rows unchanged — which is what both
    callers' docstrings already claimed.
    """
    binary, _ = tied_letter(
        sha="d" * 64,
        paths=["Opinions/2024/uks.pdf", "Opinions/koopia/uks.pdf"],
        external_ids=["KD-2", "KD-1"],
        excel_references=["XL-2", "XL-1"],
    )
    for fetch, model in (
        (_occurrence_rows, OpinionArchiveItem),
        (_metadata_rows, OpinionArchiveMetadata),
        (_candidate_rows, OpinionMatchCandidate),
    ):
        with CaptureQueriesContext(connection) as captured:
            fetch(binary=binary) if scoped else fetch()
        ordering = _order_by_clause(captured.captured_queries[-1]["sql"])
        unique = f'"{model._meta.db_table}"."{model._meta.pk.column}"'
        assert unique in ordering, (
            f"{fetch.__name__} orders by {ordering or '(nothing)'}, which is not total: "
            f"two {model.__name__} rows tying on those keys may be returned in either "
            "order, and the builder and the drift check would then disagree for ever"
        )


def test_the_per_binary_and_corpus_wide_fetches_agree(db):
    """The invariant the whole shared-helper arrangement exists to provide.

    The builder asks for one binary; the drift check asks for the corpus and
    groups it back. If those two sequences differ for the same letter, every
    value joined out of them differs, and no amount of rebuilding will reconcile
    them.
    """
    binaries = ambiguous_corpus()
    for fetch, key in (
        (_occurrence_rows, "binary_id"),
        (_metadata_rows, "item__binary_id"),
        (_candidate_rows, "item__binary_id"),
    ):
        corpus = fetch()
        for binary in binaries:
            grouped = [row for row in corpus if row[key] == binary.id]
            assert fetch(binary=binary) == grouped, (
                f"{fetch.__name__} returns a different sequence for {binary.sha256[:8]} "
                "when narrowed to one binary than when the corpus is grouped back"
            )


def test_the_two_fetches_agree_under_either_query_plan(db):
    """The same invariant, with the planner's freedom taken away from it.

    The production divergence was two queries reaching two plans at a scale a
    test corpus does not have. Asking PostgreSQL for the corpus-wide read both
    ways is the closest a small fixture gets to that, and it is the honest
    version of the question: the answer must not depend on how the rows were
    reached.
    """
    binaries = ambiguous_corpus()
    with connection.cursor() as cursor:
        for setting in ("SET LOCAL enable_seqscan = off", "SET LOCAL enable_seqscan = on"):
            cursor.execute(setting)
            for fetch, key in (
                (_occurrence_rows, "binary_id"),
                (_metadata_rows, "item__binary_id"),
                (_candidate_rows, "item__binary_id"),
            ):
                corpus = fetch()
                for binary in binaries:
                    grouped = [row for row in corpus if row[key] == binary.id]
                    assert fetch(binary=binary) == grouped, (
                        f"{fetch.__name__} disagrees with itself under {setting}"
                    )


@pytest.mark.parametrize(
    "paths",
    [
        ["Opinions/2024/uks.pdf", "Opinions/koopia/uks.pdf", "Opinions/arh/uks.pdf"],
        ["Opinions/arh/uks.pdf", "Opinions/2024/uks.pdf", "Opinions/koopia/uks.pdf"],
        ["Opinions/koopia/uks.pdf", "Opinions/arh/uks.pdf", "Opinions/2024/uks.pdf"],
    ],
    ids=["insertion-a", "insertion-b", "insertion-c"],
)
def test_a_forced_rebuild_is_immediately_clean_however_the_rows_were_inserted(db, paths):
    """The production invariant: rebuild, then verify, and find nothing.

    Insertion order is varied because it is what decides physical row order, and
    physical row order is what an ordering with a tie in it falls back on.
    """
    tied_letter(
        sha="d" * 64,
        paths=paths,
        external_ids=["KD-3", "KD-1", "KD-2"],
        excel_references=["XL-9", "XL-7", "XL-8"],
    )
    rebuild_archive_index(force=True)

    assert archive_index_findings() == []


def test_a_rebuild_converges_rather_than_rewriting_the_same_rows_for_ever(db):
    """A second rebuild writes nothing, which is what convergence means.

    The production symptom was the opposite: every rebuild rewrote every row and
    the verifier rejected the same two afterwards, so repeating the remedy was
    not progress. A projection whose builder and verifier agree reaches a fixed
    point on the first pass and stays there.
    """
    ambiguous_corpus()
    rebuild_archive_index(force=True)
    assert archive_index_findings() == []

    again = rebuild_archive_index()

    assert again.written == 0
    assert again.unchanged == again.binaries
    assert archive_index_findings() == []


def test_the_paths_a_letter_is_filed_under_are_stored_in_a_settled_order(db):
    """`occurrence_paths` is the sibling `identifiers` happened not to be.

    Both are `"\\n".join`ed in fetch order, and the tie that reordered one
    reorders the other — with the difference that two occurrences tying on the
    ordering keys are *guaranteed* to differ in their path, because the path is
    part of what makes them two rows rather than one. Whatever order it is
    stored in, the drift check has to compute the same one.
    """
    binary, items = tied_letter(
        sha="d" * 64,
        paths=["Opinions/2024/uks.pdf", "Opinions/koopia/uks.pdf", "Opinions/arh/uks.pdf"],
    )
    rebuild_archive_index(force=True)

    stored = OpinionArchiveSearchDocument.objects.get(binary=binary)
    assert sorted(stored.occurrence_paths.split("\n")) == sorted(
        item.archive_relative_path for item in items
    )
    expected = [row["archive_relative_path"] for row in _occurrence_rows(binary=binary)]
    assert stored.occurrence_paths.split("\n") == expected
    assert archive_index_findings() == []


def test_every_identifier_a_letter_carries_survives_the_tie(db):
    """The column production reported, held to the same contract.

    The SHA, the filename, KodaDash's ids and the register references, each
    contributed through a different tied relation, and all of them compared
    against what the drift check independently computes.
    """
    binary, _ = tied_letter(
        sha="d" * 64,
        paths=["Opinions/2024/uks.pdf", "Opinions/koopia/uks.pdf"],
        external_ids=["KD-2", "KD-1"],
        excel_references=["XL-2", "XL-1"],
    )
    rebuild_archive_index(force=True)

    stored = OpinionArchiveSearchDocument.objects.get(binary=binary)
    assert set(stored.identifiers.split("\n")) == {
        "d" * 64,
        "sama-nimi.pdf",
        "KD-1",
        "KD-2",
        "XL-1",
        "XL-2",
    }
    assert archive_index_findings() == []
