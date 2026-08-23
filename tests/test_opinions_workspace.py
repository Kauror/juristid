"""Arvamused — the workspace, its two sources, and the boundaries between them.

Three properties matter more than any individual assertion here.

**The sources never merge.** A canonical ``Submission`` says Koda sent an
opinion; an archive letter says the Chamber holds a file. Production has 767 of
the second and none of the first, and a workspace that blurred them to look
fuller would destroy the only distinction that lets anybody count opinions.

**Reading the archive is not a route into the register.** No list row names a
Matter, so a letter filed against a RESTRICTED entry cannot be used to learn its
title or reference.

**Nothing widened.** ``may_read_archive`` decides the Arhiiv tab exactly as it
decides the administrative browse, and a specialist gets neither.
"""

from __future__ import annotations

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from app.accounts.enums import AuthMode, UserRole
from app.core.enums import Visibility
from app.legacy_import.opinion_archive import OpinionArchiveBatch, OpinionArchiveItem
from app.legacy_import.opinion_binary import OpinionArchiveBinary
from app.legacy_import.opinion_enums import ArchiveLinkBasis
from app.legacy_import.opinion_links import archive_letters_for_matter, link_matter
from app.legacy_import.opinion_search import rebuild_archive_index
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.models import Submission, SubmissionRecipient
from tests import factories

pytestmark = pytest.mark.django_db

PASSWORD = "seda-parooli-ei-ole-kusagil-mujal"  # noqa: S105

SENT_URL = reverse("submissions:sent")
ARCHIVE_URL = reverse("submissions:archive")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def shared(settings):
    settings.AUTH_MODE = AuthMode.SHARED_GATE
    settings.SHARED_GATE_PASSWORD = PASSWORD
    settings.DEV_LOGIN_ENABLED = False
    settings.LOGIN_URL = "accounts:choose_persona"
    return settings


def act_as(client, person):
    client.post(reverse("accounts:shared_gate"), {"password": PASSWORD})
    client.post(reverse("accounts:act_as"), {"user_id": str(person.pk)})
    return client


def hold(*, sha: str = "b" * 64, title: str = "Näidiskiri") -> OpinionArchiveBinary:
    """One held letter. Every string invented; see tests/synthetic_opinions.py."""
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
        archive_relative_path="Opinions/2024/naidis.pdf",
        original_filename="naidis.pdf",
        sha256=sha,
        size_bytes=1024,
        detected_type="application/pdf",
        filename_date=datetime.date(2024, 4, 10),
        filename_recipient="Näidisministeerium",
        filename_title=title,
        binary=binary,
    )
    return binary


def final_evidence(capture, matter, name="arvamus.pdf"):
    """One immutable file on this Matter, to stand as a submission's evidence."""
    return capture(matter, b"%PDF-1.4 synthetic", name, "application/pdf")


def sent_submission(matter, *, evidence, title="Näidisarvamus", when=None, recipient=None):
    """A SENT submission, with the evidence the database insists on.

    ``submissions_sent_requires_timestamp_and_evidence`` is a check constraint,
    not a convention: a sent opinion without immutable final evidence cannot
    exist (ADR 0011). A fixture that set the status directly would be testing a
    row the product can never produce.
    """
    submission = factories.SubmissionFactory(
        matter=matter,
        title=title,
        status=SubmissionStatus.SENT,
        sent_at=when or timezone.now(),
        final_version=evidence,
    )
    if recipient is not None:
        SubmissionRecipient.objects.create(submission=submission, organisation=recipient)
    return submission


# ---------------------------------------------------------------------------
# Saadetud — the canonical list
# ---------------------------------------------------------------------------


def test_the_empty_state_explains_the_boundary_rather_than_a_failed_query(signed_in):
    """Production's actual state, and the one QA will meet first."""
    response = signed_in.get(SENT_URL)

    assert response.status_code == 200
    body = response.content.decode()
    assert "ei ole veel ühtegi arvamust välja saadetud" in body
    # It names why, and it points at where the old letters really are.
    assert "arhiiv" in body.lower()


def test_a_sent_submission_appears_without_anyone_indexing_it(
    signed_in, specialist, capture_evidence
):
    matter = factories.MatterFactory(owner=specialist, title="Näidisteema")
    sent_submission(
        matter,
        evidence=final_evidence(capture_evidence, matter),
        title="Arvamus eelnõu kohta",
    )

    body = signed_in.get(SENT_URL).content.decode()

    assert "Arvamus eelnõu kohta" in body
    assert "Näidisteema" in body


def test_drafts_are_excluded_until_asked_for(signed_in, specialist, capture_evidence):
    """A page headed Saadetud that counted drafts would make the count wrong."""
    matter = factories.MatterFactory(owner=specialist)
    factories.SubmissionFactory(matter=matter, title="Pooleli", status=SubmissionStatus.DRAFT)
    sent_submission(matter, evidence=final_evidence(capture_evidence, matter), title="Valjas")

    default = signed_in.get(SENT_URL).content.decode()
    assert "Valjas" in default
    assert "Pooleli" not in default

    everything = signed_in.get(SENT_URL, {"olek": "KOIK"}).content.decode()
    assert "Pooleli" in everything


def test_a_submission_on_a_matter_the_reader_cannot_see_is_not_listed(
    client, other_specialist, specialist, capture_evidence
):
    """Visibility is inherited, never re-derived on this surface."""
    hidden = factories.MatterFactory(owner=other_specialist, visibility=Visibility.RESTRICTED)
    sent_submission(
        hidden,
        evidence=final_evidence(capture_evidence, hidden),
        title="Piiratud arvamus",
    )

    client.force_login(specialist)
    body = client.get(SENT_URL).content.decode()

    assert "Piiratud arvamus" not in body
    assert "ei ole veel ühtegi arvamust" in body


def test_year_and_recipient_filters_narrow_the_list(
    signed_in, specialist, organisation, capture_evidence
):
    matter = factories.MatterFactory(owner=specialist)
    sent_submission(
        matter,
        evidence=final_evidence(capture_evidence, matter, "vana.pdf"),
        title="Vana arvamus",
        when=datetime.datetime(2024, 5, 1, tzinfo=datetime.UTC),
    )
    sent_submission(
        matter,
        evidence=final_evidence(capture_evidence, matter, "uus.pdf"),
        title="Uus arvamus",
        when=datetime.datetime(2026, 5, 1, tzinfo=datetime.UTC),
        recipient=organisation,
    )

    by_year = signed_in.get(SENT_URL, {"aasta": "2026"}).content.decode()
    assert "Uus arvamus" in by_year
    assert "Vana arvamus" not in by_year

    by_recipient = signed_in.get(SENT_URL, {"saaja": str(organisation.pk)}).content.decode()
    assert "Uus arvamus" in by_recipient
    assert "Vana arvamus" not in by_recipient


def test_a_bad_year_is_refused_rather_than_silently_emptying_the_list(signed_in):
    """ "Nothing matched" and "we did not run your query" mean opposite things."""
    body = signed_in.get(SENT_URL, {"aasta": "eelmine"}).content.decode()

    assert "Aasta peab olema arv" in body


def test_free_text_matches_a_submission_only_once(signed_in, specialist, capture_evidence):
    """A letter to three ministries is one row, not three."""
    matter = factories.MatterFactory(owner=specialist)
    submission = sent_submission(
        matter,
        evidence=final_evidence(capture_evidence, matter),
        title="Ainulaadne pealkiri",
    )
    for _ in range(3):
        SubmissionRecipient.objects.create(
            submission=submission, organisation=factories.OrganisationFactory()
        )

    body = signed_in.get(SENT_URL, {"q": "Ainulaadne"}).content.decode()

    assert body.count("Ainulaadne pealkiri") == 1


def test_kind_is_shown_so_a_supplementary_letter_is_not_read_as_the_opinion(
    signed_in, specialist, capture_evidence
):
    matter = factories.MatterFactory(owner=specialist)
    submission = sent_submission(
        matter, evidence=final_evidence(capture_evidence, matter), title="Täiendus"
    )
    Submission.objects.filter(pk=submission.pk).update(kind=SubmissionKind.SUPPLEMENTARY_OPINION)

    body = signed_in.get(SENT_URL).content.decode()

    assert "Täiendav arvamus" in body


# ---------------------------------------------------------------------------
# Arhiiv — who may open it
# ---------------------------------------------------------------------------


def test_a_specialist_is_refused_the_archive_tab_and_is_not_offered_it(client, shared, specialist):
    """ADR 0028 is not widened to make the workspace look fuller."""
    hold()
    rebuild_archive_index()
    act_as(client, specialist)

    listing = client.get(SENT_URL).content.decode()
    assert ARCHIVE_URL not in listing
    # Told that an archive exists and is administrative, without a count.
    assert "arhiiv" in listing.lower()

    assert client.get(ARCHIVE_URL).status_code == 403


def test_the_department_head_reads_the_archive_behind_the_shared_gate(client, shared):
    head = factories.DepartmentHeadFactory()
    hold(title="Kiri ministeeriumile")
    rebuild_archive_index()
    act_as(client, head)

    listing = client.get(SENT_URL).content.decode()
    assert ARCHIVE_URL in listing

    response = client.get(ARCHIVE_URL)
    assert response.status_code == 200
    assert "Kiri ministeeriumile" in response.content.decode()


def test_the_archive_tab_labels_its_rows_as_evidence_not_as_sent_opinions(client, shared):
    head = factories.DepartmentHeadFactory()
    hold()
    rebuild_archive_index()
    act_as(client, head)

    body = client.get(ARCHIVE_URL).content.decode()

    # The distinction, in the reader's own language, on the page itself.
    assert "mitte kanoonilised arvamuse kirjed" in body
    assert "Sidumata" in body


def test_the_archive_tab_never_names_a_matter(client, shared, other_specialist):
    """A held letter must not become a route into a RESTRICTED register entry.

    The department head may read every letter and may not read this Matter. The
    list shows that a relationship exists and stops there; resolving *which*
    Matter is the detail page's job, and it filters.
    """
    head = factories.DepartmentHeadFactory()
    binary = hold()
    hidden = factories.MatterFactory(
        owner=other_specialist,
        visibility=Visibility.RESTRICTED,
        title="Piiratud teema pealkiri",
        reference_year=2026,
        reference_number=999,
    )
    link_matter(binary=binary, matter=hidden, basis=ArchiveLinkBasis.REVIEWED, actor=head)
    rebuild_archive_index()
    act_as(client, head)

    body = client.get(ARCHIVE_URL).content.decode()

    assert "Piiratud teema pealkiri" not in body
    assert "2026_999" not in body
    # The letter's own state is a fact about the letter and is safe.
    assert "Teemaga seotud" in body


def test_the_archive_tab_explains_metadata_only_search(client, shared):
    """Phase 8: a blocked corpus must not read as "Koda never wrote that"."""
    head = factories.DepartmentHeadFactory()
    hold()
    rebuild_archive_index()
    act_as(client, head)

    body = client.get(ARCHIVE_URL).content.decode()

    assert "Kirjade sisu ei ole veel loetud" in body


def test_linked_and_unlinked_are_both_reachable(client, shared, specialist):
    head = factories.DepartmentHeadFactory()
    linked = hold(sha="c" * 64, title="Seotud kiri")
    hold(sha="d" * 64, title="Sidumata kiri")
    matter = factories.MatterFactory(owner=specialist)
    link_matter(binary=linked, matter=matter, basis=ArchiveLinkBasis.REVIEWED, actor=head)
    rebuild_archive_index()
    act_as(client, head)

    only_linked = client.get(ARCHIVE_URL, {"seotud": "jah"}).content.decode()
    assert "Seotud kiri" in only_linked
    assert "Sidumata kiri" not in only_linked

    only_unlinked = client.get(ARCHIVE_URL, {"seotud": "ei"}).content.decode()
    assert "Sidumata kiri" in only_unlinked
    assert "Seotud kiri" not in only_unlinked


def test_the_archive_tab_offers_no_write_control(client, shared, specialist):
    """Discovery is not filing. The reconciliation surfaces keep their own set."""
    head = factories.DepartmentHeadFactory()
    binary = hold()
    matter = factories.MatterFactory(owner=specialist)
    link_matter(binary=binary, matter=matter, basis=ArchiveLinkBasis.REVIEWED, actor=head)
    rebuild_archive_index()
    act_as(client, head)

    body = client.get(ARCHIVE_URL).content.decode()

    assert reverse("legacy_import:opinion_queue") not in body
    # No filing, no withdrawing, no candidate decision — the write surfaces keep
    # their own reviewer set. (The shell's sign-out form is page furniture and is
    # deliberately not what this asserts about.)
    assert reverse("legacy_import:opinion_archive_link", kwargs={"pk": binary.pk}) not in body
    assert "Seo teemaga" not in body


# ---------------------------------------------------------------------------
# The Matter page
# ---------------------------------------------------------------------------


def test_a_linked_letter_is_visible_from_the_matter_it_concerns(client, shared):
    """The lawyer holding the file was the one person who could not see it."""
    head = factories.DepartmentHeadFactory()
    binary = hold(title="Varasem kiri")
    matter = factories.MatterFactory(owner=head)
    link_matter(binary=binary, matter=matter, basis=ArchiveLinkBasis.REVIEWED, actor=head)
    rebuild_archive_index()
    act_as(client, head)

    body = client.get(reverse("matters:matter_position", kwargs={"pk": matter.pk})).content.decode()

    assert "Seotud arhiivikirjad" in body
    assert "Varasem kiri" in body
    # Worded as evidence, never as a dispatch record.
    assert "mitte kanoonilised arvamuse kirjed" in body


def test_a_reader_who_may_not_open_the_archive_sees_no_letters_on_the_matter(signed_in, specialist):
    head = factories.DepartmentHeadFactory()
    binary = hold()
    matter = factories.MatterFactory(owner=specialist)
    link_matter(binary=binary, matter=matter, basis=ArchiveLinkBasis.REVIEWED, actor=head)

    assert archive_letters_for_matter(matter, reader=specialist) == []

    body = signed_in.get(
        reverse("matters:matter_position", kwargs={"pk": matter.pk})
    ).content.decode()
    assert "Seotud arhiivikirjad" not in body


def test_one_letter_filed_twice_is_listed_once(db, specialist):
    """The page lists letters. The same bytes twice is a duplicate, not a second."""
    head = factories.DepartmentHeadFactory()
    binary = hold()
    matter = factories.MatterFactory(owner=specialist)
    link_matter(binary=binary, matter=matter, basis=ArchiveLinkBasis.REVIEWED, actor=head)
    link_matter(binary=binary, matter=matter, basis=ArchiveLinkBasis.EXACT_BINARY, actor=head)

    assert len(archive_letters_for_matter(matter, reader=head)) == 1


# ---------------------------------------------------------------------------
# The two sources stay apart
# ---------------------------------------------------------------------------


def test_the_archive_tab_creates_no_submission(client, shared, specialist):
    """The whole point: browsing history does not canonicalise it."""
    head = factories.DepartmentHeadFactory()
    binary = hold()
    matter = factories.MatterFactory(owner=specialist)
    link_matter(binary=binary, matter=matter, basis=ArchiveLinkBasis.REVIEWED, actor=head)
    rebuild_archive_index()
    act_as(client, head)

    before = Submission.objects.count()
    client.get(ARCHIVE_URL)
    client.get(SENT_URL)

    assert Submission.objects.count() == before == 0


def test_a_reader_role_gets_neither_the_archive_nor_somebody_elses_submissions(client, shared):
    reader = factories.UserFactory(role=UserRole.READER)
    hold()
    rebuild_archive_index()
    act_as(client, reader)

    assert client.get(ARCHIVE_URL).status_code == 403
    assert client.get(SENT_URL).status_code == 200
