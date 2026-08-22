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

from app.legacy_import.opinion_archive import OpinionArchiveBatch, OpinionArchiveItem
from app.legacy_import.opinion_binary import OpinionArchiveBinary, OpinionArchiveText
from app.legacy_import.opinion_enums import ArchiveTextState
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
    rows = search_archive(user=administrator, filters=ArchiveFilters(query="ehitusseadustik"))
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


def test_a_specialist_sees_no_archive_rows_and_no_totals(held, specialist):
    """Not a smaller list: none, and no count either.

    A refused reader who can still read the coverage figures knows how large
    the corpus is, which is most of what the boundary was protecting.
    """
    assert search_archive(user=specialist, filters=ArchiveFilters()).count() == 0
    assert archive_counts(specialist) == {
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


def test_the_shared_gate_is_not_identity_enough_for_the_archive(held, administrator, settings):
    """Stricter than the reconciliation queue, and deliberately so.

    The queue shows filenames and dates. This surface serves the letters, and
    an audit row naming a persona behind one shared department password is not
    a record of who read real correspondence.
    """
    from app.accounts.enums import AuthMode

    settings.AUTH_MODE = AuthMode.SHARED_GATE
    assert search_archive(user=administrator, filters=ArchiveFilters()).count() == 0


def test_an_inactive_administrator_is_refused(held, administrator):
    administrator.is_active = False
    assert search_archive(user=administrator, filters=ArchiveFilters()).count() == 0


# ---------------------------------------------------------------------------
# The screens
# ---------------------------------------------------------------------------


def test_the_browse_screen_is_administrative(client, specialist, held):
    client.force_login(specialist)
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


def test_the_detail_screen_is_administrative(client, specialist, held):
    first, _ = held
    client.force_login(specialist)
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


def test_a_specialist_may_not_download_an_archive_letter(client, specialist, stored):
    client.force_login(specialist)
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
