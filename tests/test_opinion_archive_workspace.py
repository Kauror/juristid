"""The development archive workspace: who may open it, and what it may say.

P3.3 turns the held archive from something only a non-shared administrator could
reach into a workspace the department can actually use while it is still behind
one shared password. That is a widening, so this module is mostly about the
things that did *not* widen with it.

Three boundaries are asserted here and they are genuinely different questions:

**The corpus.** May this person open the archive at all — browse it, count it,
search it, download a letter? Answered about the *corpus*, because an unfiled
letter has no Matter to inherit from. Under the shared gate that is the
department head and the administrator; outside it, the administrator alone, as
before.

**The register.** Once inside, what may the page say about a *Matter*? That is
the ordinary question, answered by `Matter.objects.visible_to` exactly as
everywhere else. Reading the archive is therefore not a back door: an
administrator who may open every letter still may not learn the title of a
RESTRICTED Matter one of them is filed against, and may not confirm one by
typing its reference into the link form.

**The judgement.** Recording that a letter concerns a Matter is a business
claim, and under the shared gate it belongs to the department head. Deciding
that Koda *sent* it is a third thing again, it lives in the reconciliation
queue, and nothing here moved it (docs/adr/0028).
"""

from __future__ import annotations

import datetime
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from app.accounts.enums import AuthMode, UserRole
from app.core.enums import Visibility
from app.legacy_import.opinion_access import (
    may_manage_archive_links,
    may_read_archive,
    may_use_opinion_queue,
)
from app.legacy_import.opinion_archive import (
    OpinionArchiveBatch,
    OpinionArchiveItem,
    OpinionMatchCandidate,
)
from app.legacy_import.opinion_binary import OpinionArchiveBinary, OpinionArchiveMatterLink
from app.legacy_import.opinion_enums import (
    ArchiveLinkBasis,
    OpinionCandidateState,
    OpinionMatchClass,
)
from app.legacy_import.opinion_links import link_matter
from app.legacy_import.opinion_search import archive_counts, rebuild_archive_index, search_archive
from tests import factories

pytestmark = pytest.mark.django_db

PASSWORD = "seda-parooli-ei-ole-kusagil-mujal"  # noqa: S105


# ---------------------------------------------------------------------------
# A held letter, its occurrences and its evidence
# ---------------------------------------------------------------------------


def hold(*, sha: str = "b" * 64, title: str = "Näidisarvamus") -> OpinionArchiveBinary:
    """One binary as materialisation would leave it. No real correspondence.

    Every string here is invented. The archive holds letters to named ministries
    about real files, and a fixture is not the place for any of it (brief 79).
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


@pytest.fixture
def binary(db):
    held = hold()
    rebuild_archive_index()
    return held


@pytest.fixture
def stored(binary, evidence_root):
    """The same letter with real bytes behind it."""
    from django.core.files.base import ContentFile

    from app.documents.services import evidence_storage

    evidence_storage().save(binary.storage_key, ContentFile(b"%PDF-1.4 synthetic"))
    return binary


@pytest.fixture
def reader(db):
    """The role that owns the register but not the machine."""
    return factories.DepartmentHeadFactory()


@pytest.fixture
def library_reader(db):
    """A READER-role account, which this surface never admits."""
    return factories.UserFactory(role=UserRole.READER)


@pytest.fixture
def shared(settings):
    """The deployment as it currently runs: one password, then a persona."""
    settings.AUTH_MODE = AuthMode.SHARED_GATE
    settings.SHARED_GATE_PASSWORD = PASSWORD
    settings.DEV_LOGIN_ENABLED = False
    settings.LOGIN_URL = "accounts:choose_persona"
    return settings


@pytest.fixture
def behind_the_gate(client, shared):
    """A client that has typed the shared password and chosen nobody yet."""
    response = client.post(reverse("accounts:shared_gate"), {"password": PASSWORD})
    assert response.status_code == 302
    return client


def act_as(gate_client, person):
    gate_client.post(reverse("accounts:act_as"), {"user_id": str(person.pk)})
    return gate_client


@pytest.fixture
def individually(client, settings):
    """Sign in as one person, with no shared door in front.

    ADMINISTRATOR and READER are no longer persona candidates: behind the shared
    gate only the department's own work roles may be selected, and the endpoint
    refuses a crafted POST for anybody else (docs/adr/0034).

    The archive's rules *about* those roles have not changed and still have to
    be proven. They are properties of `request.user`, not of how the request
    established one — so the cases below sign in the way a deployment that
    authenticates individuals would, and the persona rule is asserted
    separately, where it belongs.
    """
    settings.AUTH_MODE = AuthMode.NONE
    settings.LOGIN_URL = "accounts:dev_login"

    def sign_in(person):
        client.force_login(person)
        return client

    return sign_in


def item_of(binary) -> OpinionArchiveItem:
    return OpinionArchiveItem.objects.get(binary=binary)


def propose(binary, matter, *, explanation: str = "") -> OpinionMatchCandidate:
    """One reconciliation proposal against this letter.

    The batch comes from the occurrence rather than being invented, because a
    candidate belongs to the run that produced it and a fixture that made up its
    own would be describing a run that never happened.
    """
    item = item_of(binary)
    return OpinionMatchCandidate.objects.create(
        item=item,
        batch=item.batch,
        matter=matter,
        match_class=OpinionMatchClass.REVIEW_REQUIRED,
        state=OpinionCandidateState.PENDING,
        explanation=explanation,
    )


def browse_url():
    return reverse("legacy_import:opinion_archive_browse")


def detail_url(binary):
    return reverse("legacy_import:opinion_archive_detail", kwargs={"pk": binary.pk})


def file_url(binary):
    return reverse("legacy_import:opinion_archive_file", kwargs={"pk": binary.pk})


def link_url(binary):
    return reverse("legacy_import:opinion_archive_link", kwargs={"pk": binary.pk})


# ---------------------------------------------------------------------------
# The predicate itself
# ---------------------------------------------------------------------------


def test_the_shared_gate_admits_exactly_two_roles_to_the_corpus(
    shared, reader, administrator, specialist, library_reader
):
    assert may_read_archive(reader)
    assert may_read_archive(administrator)
    assert not may_read_archive(specialist)
    assert not may_read_archive(library_reader)


def test_nobody_without_a_persona_reads_the_archive(shared):
    """Knowing the shared password is not on its own an archive credential.

    The sentinel a gate-passed session carries has no role, cannot be an audit
    actor, and must not be able to open real outgoing correspondence — which is
    the whole reason `DepartmentViewer` is not a `User`.
    """
    from app.core.authorization import DEPARTMENT_VIEWER

    assert not may_read_archive(DEPARTMENT_VIEWER)
    assert not may_read_archive(None)


def test_an_inactive_high_authority_account_is_refused(shared, reader, administrator):
    """The role string is not the whole question, in either mode."""
    reader.is_active = False
    administrator.is_active = False
    assert not may_read_archive(reader)
    assert not may_read_archive(administrator)


def test_outside_the_shared_gate_nothing_widened(settings, reader, administrator):
    """Cloudflare behaviour is decided later and on purpose, not as a side effect.

    The head gets the archive because this deployment currently has no way to
    say who is at the keyboard. Under an identity provider that question has a
    real answer, and the department-head grant should be argued on its own
    merits rather than inherited from a workaround (brief 9, docs/adr/0028).
    """
    for mode in (AuthMode.NONE, AuthMode.CLOUDFLARE_ACCESS):
        settings.AUTH_MODE = mode
        assert may_read_archive(administrator)
        assert not may_read_archive(reader)
        assert may_manage_archive_links(administrator)
        assert not may_manage_archive_links(reader)


def test_under_the_shared_gate_the_administrator_reads_but_does_not_file(
    shared, reader, administrator
):
    """Technical administration is not business authorship.

    The same separation `ROLES_WITH_BUSINESS_WRITE` makes everywhere else: the
    administrator operates the migration and may read every letter it holds,
    and saying what the Chamber's correspondence *concerns* stays with the
    person answerable for the register.
    """
    assert may_read_archive(administrator)
    assert not may_manage_archive_links(administrator)
    assert may_read_archive(reader)
    assert may_manage_archive_links(reader)


def test_managing_links_is_never_wider_than_reading(shared, specialist, library_reader):
    for person in (specialist, library_reader, None):
        assert not may_manage_archive_links(person)


def test_the_reconciliation_queue_did_not_widen_with_the_archive(
    shared, reader, administrator, specialist
):
    """A different surface, a stronger claim, and an unchanged answer.

    The queue records decisions a later apply may turn into canonical
    Submissions. A department head who may read the archive and file its letters
    still does not get that (brief 15, 66).
    """
    assert may_use_opinion_queue(administrator)
    assert not may_use_opinion_queue(reader)
    assert not may_use_opinion_queue(specialist)


# ---------------------------------------------------------------------------
# The screens, under the shared gate
# ---------------------------------------------------------------------------


def test_a_department_head_persona_reaches_every_archive_surface(behind_the_gate, reader, stored):
    act_as(behind_the_gate, reader)
    assert behind_the_gate.get(browse_url()).status_code == 200
    assert behind_the_gate.get(detail_url(stored)).status_code == 200
    assert behind_the_gate.get(file_url(stored)).status_code == 200


def test_the_shared_gate_lets_an_administrator_read_the_corpus_but_not_file_it(
    shared, administrator
):
    """The rule, at the predicate, because no screen can reach it any more.

    Under the shared gate the archive widens to two roles and filing narrows to
    one: an administrator may open every letter Koda holds and may not say what
    the Chamber's correspondence concerns (docs/adr/0028). That asymmetry used
    to be asserted through the workspace, by selecting an administrator persona
    and reading the page — and an administrator is not a persona candidate any
    anymore, so that route is gone (docs/adr/0034).

    Asserting it here rather than deleting it. The rule has not changed, the
    module still implements it, and the day this deployment moves to Cloudflare
    Access — where an administrator *is* an identity the system can name — it
    starts mattering again. A rule with no test is a rule that drifts while
    nobody is looking.
    """
    assert may_read_archive(administrator)
    assert not may_manage_archive_links(administrator)


def test_an_administrator_who_is_named_individually_may_file_a_letter(
    individually, administrator, binary, normal_matter
):
    """And outside the shared gate the widening does not apply.

    The mode is the whole difference: `may_manage_archive_links` narrows to the
    department head only while the department is behind one password, because
    that is the mode in which the system cannot say who acted. This is the other
    branch, and it is the one the deployment is heading for.
    """
    client = individually(administrator)
    response = client.post(
        link_url(binary), {"action": "link", "viide": normal_matter.display_reference}
    )

    assert response.status_code in (200, 302)
    assert OpinionArchiveMatterLink.objects.count() == 1


@pytest.mark.parametrize("role", [UserRole.ADMINISTRATOR, UserRole.READER])
def test_a_non_department_account_cannot_become_a_persona_and_reach_the_letters(
    behind_the_gate, stored, role
):
    """The stronger guarantee, behind the door where it matters.

    An administrator may read the archive when a deployment can say who they
    are. Behind one shared password it cannot, so the account is not selectable
    and the session stays the department viewer — which reaches no letters at
    all (docs/adr/0034, docs/adr/0028).
    """
    person = factories.UserFactory(role=role, is_staff=role == UserRole.ADMINISTRATOR)
    act_as(behind_the_gate, person)

    assert not behind_the_gate.session.get("_auth_user_id")
    assert behind_the_gate.get(browse_url()).status_code in (302, 403)
    assert behind_the_gate.get(detail_url(stored)).status_code in (302, 403)


def test_an_ordinary_persona_is_refused_every_archive_surface(
    behind_the_gate, stored, normal_matter
):
    person = factories.UserFactory(role=UserRole.SPECIALIST)
    act_as(behind_the_gate, person)

    assert behind_the_gate.get(browse_url()).status_code == 403
    assert behind_the_gate.get(detail_url(stored)).status_code == 403
    assert behind_the_gate.get(file_url(stored)).status_code == 403
    assert (
        behind_the_gate.post(
            link_url(stored), {"action": "link", "viide": normal_matter.display_reference}
        ).status_code
        == 403
    )
    assert OpinionArchiveMatterLink.objects.count() == 0


def test_a_session_with_no_persona_is_refused_the_letters(behind_the_gate, stored):
    """The critical one. The shared password alone reaches no correspondence.

    Everybody in the department knows this password; that is what makes it a
    door rather than an identity. A session that has opened the door and named
    nobody gets the department dashboard, and not 767 real letters.
    """
    assert behind_the_gate.get(browse_url()).status_code in (302, 403)
    assert behind_the_gate.get(detail_url(stored)).status_code in (302, 403)

    response = behind_the_gate.get(file_url(stored))
    assert response.status_code in (302, 403)
    assert b"%PDF" not in response.content


def test_a_refused_persona_cannot_learn_the_size_of_the_corpus(shared, specialist, binary):
    """Authorization comes before counting, not after it.

    A refusal that still returned totals would tell a specialist how many
    letters Koda holds and how many remain unfiled — which is most of what the
    boundary is protecting.
    """
    from app.legacy_import.opinion_search import ArchiveFilters

    counts = archive_counts(specialist)
    assert counts == {"total": 0, "with_body": 0, "linked": 0, "with_submission": 0}
    assert search_archive(user=specialist, filters=ArchiveFilters()).count() == 0


def test_the_direct_file_url_serves_nothing_to_a_refused_persona(
    behind_the_gate, stored, specialist
):
    """Knowing the exact binary UUID is not a credential either."""
    from app.audit.enums import SecurityEventType
    from app.audit.models import SecurityAuditEvent

    act_as(behind_the_gate, specialist)
    response = behind_the_gate.get(file_url(stored))

    assert response.status_code == 403
    assert not SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.DOCUMENT_DOWNLOADED
    ).exists()


# ---------------------------------------------------------------------------
# Navigation is presentation, and the route is the boundary
# ---------------------------------------------------------------------------


def test_the_archive_browse_is_no_longer_a_navigation_destination(
    behind_the_gate, reader, specialist, binary
):
    """The bar carried two answers to one question, and now carries one.

    «Arvamused» and «Arvamuste arhiiv» asked a reader to tell a canonical
    Submission from a held historical letter before they could choose where to
    go. The distinction is kept where it can be captioned — inside the
    workspace — and the bar stops asking (docs/adr/0044).

    Asserted for the persona `may_read_archive` admits as well as for the one
    it refuses. The reader is the one who used to get the second item, so if it
    is gone for them it is gone; the specialist never had it and still does
    not.
    """
    assert may_read_archive(reader)

    act_as(behind_the_gate, reader)
    assert browse_url() not in behind_the_gate.get(reverse("matters:department")).content.decode()

    act_as(behind_the_gate, specialist)
    assert browse_url() not in behind_the_gate.get(reverse("matters:department")).content.decode()


def test_the_workspace_offers_the_archive_tab_to_a_reader(behind_the_gate, reader, binary):
    """Where the presentation decision moved to, and still presentation.

    `may_read_archive` decides whether the tab is drawn exactly as it decided
    whether the bar item was. The route is still the boundary, and the tests
    above still prove it refuses a crafted URL.
    """
    act_as(behind_the_gate, reader)
    body = behind_the_gate.get(reverse("submissions:sent")).content.decode()
    assert reverse("submissions:archive") in body
    assert ">Arhiiv" in body


def test_the_workspace_does_not_offer_the_archive_tab_to_anybody_else(behind_the_gate, specialist):
    """And says the corpus exists rather than pretending it does not.

    A refused reader is told the archive is held and administratively read,
    which is a different statement from a page that silently omits it — and no
    count appears with it (docs/adr/0028).
    """
    act_as(behind_the_gate, specialist)
    body = behind_the_gate.get(reverse("submissions:sent")).content.decode()
    assert reverse("submissions:archive") not in body
    assert "arvamuste arhiiv on eraldi hoiul" in body


def test_the_candidate_queue_is_not_offered_to_a_reader_who_cannot_use_it(
    behind_the_gate, reader, binary
):
    """A button that can only ever produce a 403 is worse than no button."""
    act_as(behind_the_gate, reader)
    body = behind_the_gate.get(browse_url()).content.decode()
    assert reverse("legacy_import:opinion_queue") not in body


def test_the_candidate_queue_is_still_offered_to_the_administrator(
    individually, administrator, binary
):
    client = individually(administrator)
    body = client.get(browse_url()).content.decode()
    assert reverse("legacy_import:opinion_queue") in body


# ---------------------------------------------------------------------------
# The register boundary: what the archive may say about a Matter
# ---------------------------------------------------------------------------


@pytest.fixture
def two_matters(db):
    """One ordinary Matter and one RESTRICTED Matter, both filed to one letter.

    Owned by a third person, so neither the head's role grant nor the
    administrator's lack of one is confused with ownership.
    """
    owner = factories.UserFactory()
    normal = factories.MatterFactory(owner=owner, title="Avalik näidisteema")
    restricted = factories.MatterFactory(
        owner=owner,
        title="Piiratud näidisteema",
        visibility=Visibility.RESTRICTED,
    )
    return normal, restricted


@pytest.fixture
def filed(binary, two_matters):
    normal, restricted = two_matters
    for matter in (normal, restricted):
        link_matter(
            binary=binary,
            matter=matter,
            basis=ArchiveLinkBasis.EXACT_BINARY,
            note="Sünteetiline seos.",
        )
    rebuild_archive_index()
    return binary


def test_an_archive_reader_is_told_a_hidden_relationship_exists_and_nothing_else(
    individually, administrator, filed, two_matters
):
    """The load-bearing one. Archive access is not a route into the register.

    The administrator may open every letter Koda holds. That says nothing about
    which register entries they may read, and P3.3 must not let one become the
    other: the RESTRICTED Matter's title, reference and detail URL stay behind
    the ordinary rule, and only the archive-side fact that *something* is filed
    here survives.
    """
    normal, restricted = two_matters
    client = individually(administrator)
    body = client.get(detail_url(filed)).content.decode()

    assert normal.title in body
    assert reverse("matters:matter_detail", kwargs={"pk": normal.pk}) in body

    assert restricted.title not in body
    assert restricted.display_reference not in body
    assert reverse("matters:matter_detail", kwargs={"pk": restricted.pk}) not in body

    # And it does not claim the letter is unfiled while the projection records
    # that it is not.
    assert "Ükski teema ei ole selle kirjaga seotud" not in body
    assert "ei kuvata" in body


def test_a_department_head_reaches_both_through_the_ordinary_role_grant(
    behind_the_gate, reader, filed, two_matters
):
    """No archive-specific exception, and none needed.

    The head already reads RESTRICTED content through
    `ROLES_WITH_RESTRICTED_ACCESS`. The archive page inherits that by asking the
    same predicate as every other surface rather than by knowing anything about
    roles itself.
    """
    normal, restricted = two_matters
    act_as(behind_the_gate, reader)
    body = behind_the_gate.get(detail_url(filed)).content.decode()

    assert normal.title in body
    assert restricted.title in body


def test_an_administrator_cannot_link_a_matter_they_may_not_read(
    client, administrator, binary, two_matters
):
    """Non-shared mode, where the administrator is the link reviewer.

    They may open every letter in the archive and may not read a RESTRICTED
    register entry. Linking must not become the way to confirm one exists.
    """
    _, restricted = two_matters
    client.force_login(administrator)
    response = client.post(
        link_url(binary),
        {"action": "link", "viide": restricted.display_reference},
        follow=True,
    )
    body = response.content.decode()

    assert restricted.title not in body
    assert "ei leitud" in body
    assert not OpinionArchiveMatterLink.objects.filter(matter=restricted).exists()


def test_a_hidden_candidate_shows_its_class_and_not_its_matter(
    individually, administrator, binary, two_matters
):
    """Archive-side evidence is the archive's to show; the Matter is not."""
    _, restricted = two_matters
    propose(binary, restricted, explanation="Sünteetiline selgitus.")
    client = individually(administrator)
    body = client.get(detail_url(binary)).content.decode()

    assert "Sünteetiline selgitus." in body
    assert restricted.title not in body
    assert reverse("matters:matter_detail", kwargs={"pk": restricted.pk}) not in body


# ---------------------------------------------------------------------------
# Filing a letter: what the weak link does and does not mean
# ---------------------------------------------------------------------------


def test_a_department_head_files_a_letter_and_creates_nothing_canonical(
    behind_the_gate, reader, binary, two_matters
):
    from app.legacy_import.opinion_archive import OpinionSubmissionImport
    from app.submissions.models import Submission

    normal, _ = two_matters
    act_as(behind_the_gate, reader)
    behind_the_gate.post(
        link_url(binary),
        {"action": "link", "viide": normal.display_reference, "markus": "Kuulub siia."},
        follow=True,
    )

    link = OpinionArchiveMatterLink.objects.get()
    assert link.matter_id == normal.pk
    assert link.basis == ArchiveLinkBasis.REVIEWED
    assert link.linked_by_id == reader.pk
    assert link.linked_at is not None
    assert link.note == "Kuulub siia."

    # The archive says the letter concerns the Matter. It does not say Koda sent
    # it, and P4 is what would.
    assert Submission.objects.count() == 0
    assert OpinionSubmissionImport.objects.count() == 0


def test_the_quick_link_beside_a_candidate_files_without_deciding_it(
    behind_the_gate, reader, binary, two_matters
):
    """One click for the common case, and it stays a weak claim.

    The candidate keeps its state: accepting the archive's suggestion about
    *which* Matter is not the same act as confirming the letter was sent, and
    that decision lives in the reconciliation queue.
    """
    from app.submissions.models import Submission

    normal, _ = two_matters
    candidate = propose(binary, normal)
    act_as(behind_the_gate, reader)

    body = behind_the_gate.get(detail_url(binary)).content.decode()
    assert "Seo selle teemaga" in body

    behind_the_gate.post(link_url(binary), {"action": "link", "teema": str(normal.pk)}, follow=True)

    link = OpinionArchiveMatterLink.objects.get()
    assert link.basis == ArchiveLinkBasis.REVIEWED
    candidate.refresh_from_db()
    assert candidate.state == OpinionCandidateState.PENDING
    assert Submission.objects.count() == 0


def test_the_quick_link_is_not_offered_once_the_letter_is_filed(
    behind_the_gate, reader, binary, two_matters
):
    normal, _ = two_matters
    propose(binary, normal)
    link_matter(binary=binary, matter=normal, basis=ArchiveLinkBasis.REVIEWED, actor=reader)

    act_as(behind_the_gate, reader)
    body = behind_the_gate.get(detail_url(binary)).content.decode()
    assert "Seo selle teemaga" not in body


def test_a_reviewed_link_can_be_withdrawn_and_withdrawing_it_twice_is_harmless(
    behind_the_gate, reader, binary, two_matters
):
    normal, _ = two_matters
    link_matter(binary=binary, matter=normal, basis=ArchiveLinkBasis.REVIEWED, actor=reader)
    act_as(behind_the_gate, reader)

    payload = {"action": "unlink", "teema": str(normal.pk)}
    behind_the_gate.post(link_url(binary), payload, follow=True)
    assert OpinionArchiveMatterLink.objects.count() == 0

    response = behind_the_gate.post(link_url(binary), payload, follow=True)
    assert response.status_code == 200
    assert OpinionArchiveMatterLink.objects.count() == 0


def test_a_derived_link_is_not_offered_for_withdrawal(behind_the_gate, reader, filed, two_matters):
    """The 244 links production already holds are derived evidence.

    They follow from an exact identity — the same bytes — so a reviewer who
    disagrees is disagreeing with the archive rather than with a judgement, and
    the archive workspace is not where that gets corrected.
    """
    act_as(behind_the_gate, reader)
    body = behind_the_gate.get(detail_url(filed)).content.decode()
    assert "Eemalda" not in body
    assert OpinionArchiveMatterLink.objects.count() == 2


def test_a_link_a_canonical_submission_stands_on_cannot_be_withdrawn(
    behind_the_gate, reader, binary, two_matters
):
    """The rule P4 will need, asserted before P4 exists.

    Production has no canonical historical Submissions yet, so this is the one
    invariant here with nothing to defend today and everything to defend later:
    withdrawing a reviewer's judgement is undoing an opinion, not a link.
    """
    from app.legacy_import.opinion_archive import OpinionSubmissionImport
    from app.submissions.models import Submission

    normal, _ = two_matters
    link_matter(binary=binary, matter=normal, basis=ArchiveLinkBasis.REVIEWED, actor=reader)
    # Left at its default status. `submissions_sent_requires_timestamp_and_evidence`
    # refuses a SENT row without its final text, and building one would be
    # fixture machinery for a rule that does not read the status: what protects
    # the link is the import record naming these bytes as the Submission's
    # source.
    submission = Submission.objects.create(matter=normal, title="Sünteetiline arvamus")
    item = item_of(binary)
    OpinionSubmissionImport.objects.create(item=item, batch=item.batch, submission=submission)

    act_as(behind_the_gate, reader)
    response = behind_the_gate.post(
        link_url(binary), {"action": "unlink", "teema": str(normal.pk)}, follow=True
    )

    assert "kanoonilisele arvamusele" in response.content.decode()
    assert OpinionArchiveMatterLink.objects.count() == 1


def test_the_workspace_never_opens_a_register_entry(behind_the_gate, reader, binary):
    from app.matters.models import Matter

    before = Matter.objects.count()
    act_as(behind_the_gate, reader)
    response = behind_the_gate.post(
        link_url(binary), {"action": "link", "viide": "1999_999"}, follow=True
    )

    assert Matter.objects.count() == before
    assert OpinionArchiveMatterLink.objects.count() == 0
    assert "ei leitud" in response.content.decode()


# ---------------------------------------------------------------------------
# Searching, filtering and what the page promises
# ---------------------------------------------------------------------------


def test_the_quick_filters_count_what_they_will_show(behind_the_gate, reader, filed, two_matters):
    act_as(behind_the_gate, reader)
    response = behind_the_gate.get(browse_url(), {"seotud": "ei"})

    assert response.status_code == 200
    assert response.context["filters"].linked == "ei"
    assert response.context["unlinked_count"] == 0
    assert response.context["counts"]["linked"] == 1


def test_the_quick_filters_keep_the_search_and_lead_somewhere_else(behind_the_gate, reader, binary):
    """Two defects in one assertion, and the second one shipped once already.

    `{% querystring %}` returns the empty string once every parameter has been
    dropped, and an empty `href` means *this document including its query
    string* — so "Kõik" clicked while filtered would have re-rendered the filter
    it was supposed to clear, silently and only in that one state.
    """
    act_as(behind_the_gate, reader)
    body = behind_the_gate.get(browse_url(), {"q": "naidis", "seotud": "ei"}).content.decode()
    hrefs = re.findall(r'class="segmented__option[^"]*"[^>]*href="([^"]*)"', body)

    assert len(hrefs) == 3
    assert all(href.startswith(browse_url()) for href in hrefs), hrefs
    assert all("q=naidis" in href for href in hrefs), hrefs
    # Kõik drops the filter rather than restating it.
    assert "seotud" not in hrefs[0]
    assert "seotud=ei" in hrefs[1]
    assert "seotud=jah" in hrefs[2]


def test_the_page_does_not_promise_body_search_when_no_text_has_been_read(
    behind_the_gate, reader, binary
):
    """Production holds 767 letters and 0 readable bodies.

    A search box offering "sisu" would invite somebody to search a phrase, find
    nothing, and conclude the Chamber never wrote it (brief 35, 37).
    """
    act_as(behind_the_gate, reader)
    response = behind_the_gate.get(browse_url())
    body = response.content.decode()

    assert response.context["body_search_available"] is False
    assert "Sisu olemas" not in body
    assert "metaandmete järgi" in body


def test_the_body_controls_come_back_where_text_has_been_extracted(behind_the_gate, reader, binary):
    """Not a permanent removal — a truthful one, driven by the corpus."""
    from app.legacy_import.opinion_binary import OpinionArchiveText
    from app.legacy_import.opinion_enums import ArchiveTextState

    OpinionArchiveText.objects.create(
        binary=binary,
        state=ArchiveTextState.DONE,
        body="Sünteetiline sisu otsimiseks.",
        characters=29,
        parser="test",
        parser_version="1",
    )
    rebuild_archive_index()

    act_as(behind_the_gate, reader)
    response = behind_the_gate.get(browse_url())

    assert response.context["body_search_available"] is True
    assert "Sisu olemas" in response.content.decode()


def test_the_coverage_strip_keeps_its_dimensions_apart(behind_the_gate, reader, filed):
    """Held, filed and sent are three different numbers.

    Production holds 767 letters, 244 of them filed onto a Matter, and zero
    canonical opinions. A strip that let any of those read as "sent opinions"
    would convert an unfinished migration into a claim about advocacy.
    """
    act_as(behind_the_gate, reader)
    counts = behind_the_gate.get(browse_url()).context["counts"]

    assert counts["total"] == 1
    assert counts["linked"] == 1
    assert counts["with_submission"] == 0


def test_filtering_and_paging_still_work_for_the_reader(behind_the_gate, reader, binary):
    act_as(behind_the_gate, reader)

    assert behind_the_gate.get(browse_url(), {"aasta": "2024"}).context["total"] == 1
    assert behind_the_gate.get(browse_url(), {"aasta": "2023"}).context["total"] == 0
    # By the archive path rather than by a stemmed word: what this test is
    # about is that the reader's filters still run, and the full-text route has
    # its own suite in `tests/test_opinion_archive_search.py`.
    assert behind_the_gate.get(browse_url(), {"q": "naidis.pdf"}).context["total"] == 1
    assert behind_the_gate.get(browse_url(), {"q": "ei-ole-kusagil"}).context["total"] == 0
    assert behind_the_gate.get(browse_url(), {"seotud": "ei"}).context["total"] == 1
    assert behind_the_gate.get(browse_url(), {"seotud": "jah"}).context["total"] == 0


# ---------------------------------------------------------------------------
# The bytes, and what the record says about who read them
# ---------------------------------------------------------------------------


def test_opening_a_letter_records_how_much_identity_stood_behind_it(
    behind_the_gate, reader, stored
):
    """The condition on which this mode may serve the archive at all.

    `acting_as_user` is the persona somebody selected; `authenticated_via` says
    what that is worth. Recording only the first would be the system telling a
    lie about itself — and it is exactly the lie that made refusing the shared
    gate look like the safe option (docs/adr/0016, 0027).
    """
    from app.audit.enums import SecurityEventType
    from app.audit.models import SecurityAuditEvent

    act_as(behind_the_gate, reader)
    behind_the_gate.get(file_url(stored))

    event = SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.DOCUMENT_DOWNLOADED
    ).get()
    assert event.detail["authenticated_via"] == "SHARED_GATE"
    assert event.detail["acting_as_user"] == str(reader.pk)
    assert event.detail["source"] == "opinion_archive"
    assert event.detail["sha256"] == stored.sha256
    assert event.detail["disposition"] == "inline"
    # The record says a letter was read, never what it said.
    assert "body" not in event.detail
    assert "%PDF" not in str(event.detail)


def test_the_storage_key_is_never_in_the_url_and_never_accepted_from_one(stored):
    assert stored.storage_key not in file_url(stored)
    assert str(stored.pk) in file_url(stored)


def test_a_crafted_storage_key_reaches_nothing(behind_the_gate, reader, stored):
    """The route names a row; the key is read from it.

    A query parameter naming a path would make this an arbitrary file reader
    over the evidence store, so the only thing a caller may choose is which
    archive row they want.
    """
    act_as(behind_the_gate, reader)
    response = behind_the_gate.get(file_url(stored), {"storage_key": "../../etc/passwd"})
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"%PDF-1.4 synthetic"


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


def test_the_detail_page_asks_about_visibility_once(
    behind_the_gate, reader, binary, django_assert_max_num_queries
):
    """Not once per relationship.

    A letter can concern several Matters — that is why the link table is plural
    — so the natural per-row `visible_to(...).exists()` would scale the page
    with the thing the page exists to show.
    """
    owner = factories.UserFactory()
    matters = [factories.MatterFactory(owner=owner, title=f"Näidisteema {n}") for n in range(6)]
    for matter in matters:
        link_matter(binary=binary, matter=matter, basis=ArchiveLinkBasis.EXACT_BINARY)
        propose(binary, matter)
    act_as(behind_the_gate, reader)

    with django_assert_max_num_queries(20):
        assert behind_the_gate.get(detail_url(binary)).status_code == 200


def test_the_browse_page_does_not_grow_a_query_per_row(
    behind_the_gate, reader, django_assert_max_num_queries
):
    for index in range(12):
        hold(sha=f"{index:02d}" + "d" * 62, title=f"Näidiskiri {index}")
    rebuild_archive_index()
    act_as(behind_the_gate, reader)

    with django_assert_max_num_queries(20):
        assert behind_the_gate.get(browse_url()).status_code == 200
