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

import pytest
from django.urls import reverse
from django.utils import timezone

from app.legacy_import.opinion_archive import (
    OpinionArchiveBatch,
    OpinionArchiveItem,
    OpinionMatchCandidate,
)
from app.legacy_import.opinion_binary import OpinionArchiveBinary, OpinionArchiveText
from app.legacy_import.opinion_enums import (
    ArchiveTextState,
    OpinionCandidateState,
    OpinionMatchClass,
)
from app.legacy_import.opinion_search import (
    ArchiveFilters,
    ArchiveQueryRefused,
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
    """The one sequence the runbook actually performs.

    A rebuild that skipped rows already at the current index version would do
    nothing here and report a clean run, leaving every freshly extracted body
    out of the search.
    """
    _, second = held
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


def test_a_new_binary_is_reported_as_unindexed_until_it_is_rebuilt(held):
    hold(sha="d" * 64, title="Kolmas")
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


def a_candidate(binary, *, klass=OpinionMatchClass.UNMATCHED, state=None, matter=None):
    """One proposal about the first occurrence of these bytes."""
    item = OpinionArchiveItem.objects.filter(binary=binary).order_by("pk").first()
    assert item is not None
    return OpinionMatchCandidate.objects.create(
        item=item,
        matter=matter,
        batch=item.batch,
        match_class=klass,
        state=state or OpinionCandidateState.PENDING,
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
