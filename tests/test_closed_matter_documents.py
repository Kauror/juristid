"""A closed Matter's Dokumendid surface is read-only for normal business work.

#180 taught the Teema workspace that a closed teema accepts no new canonical
content. The Dokumendid tab was not part of that round, and integrated QA found
the three doors it had left open on a Matter somebody had already finished: `↑
Lae dokument` still wrote a `Document`, `+ Uus arvamus` still wrote a DRAFT
`Submission`, and `+ Registreeri saatmine` still wrote a SENT one. None of it was
a regression — the surface had always behaved that way — and all three
contradicted the rule the workspace beside them enforces.

The product decision this module asserts:

    A lawyer reading a closed Matter may read it, browse its chronology, open
    and download its evidence and inspect its opinions. They may not create or
    advance canonical business content on it. If ordinary legal work has to
    continue, the Matter is reopened first.

Four things follow, and each has tests below.

**The refusal is authoritative, not cosmetic.** Hiding the controls on a fresh
GET is right and decides nothing: a browser holding the page from before the
closure still has every field and every button, and its POST reaches a server
with no memory of which page it came from. So every route takes the Matter's own
row lock through `lock_open_matter_for_business_write` and reads the state from
under it (`app/matters/locks.py`).

**A refusal leaves nothing behind.** No `Document`, no `DocumentVersion`, no
stored object, no `Submission`, no recipient row, no `ChangeEvent`, no search
row. The lock is taken before the first write rather than after some of them.

**Advancing an existing draft is business work too.** Attaching final evidence,
choosing it from the files already there, and `Märgi saadetuks` are the ordinary
continuation of an unfinished opinion, not corrections of a historical fact. All
three refuse; the draft stays a draft with everything it already had.

**Reopening restores everything.** The rule is `closed → no normal business
writes`, not `once closed, forever immutable`.

What this module deliberately does *not* decide: whether a *historical*
correction — `Võta tagasi`, superseding, editing a fact already recorded — should
be possible on a closed Matter. That is a separate product question and nothing
here changes it. Nor does it touch personal `Märkmed`, which were outside #180's
canonical-business-write boundary on purpose.

The import side of the same boundary is in
`tests/test_integration_post_qa_sep11.py` §3: the services below these use cases
still file historical letters onto closed Matters, because that is what the
register import does and breaking it would protect nothing.
"""

from __future__ import annotations

import datetime

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.models import Document, DocumentVersion
from app.documents.services import (
    add_evidence_version,
    add_version_on_open_matter,
    capture_evidence_on_open_matter,
    create_document,
)
from app.matters.locks import CLOSED_MATTER_REFUSAL
from app.matters.services import close_matter, reopen_matter
from app.search.models import SearchDocument
from app.submissions.enums import SentAtPrecision, SubmissionKind, SubmissionStatus
from app.submissions.models import Submission, SubmissionRecipient
from app.submissions.services import (
    attach_final_evidence_on_open_matter,
    create_opinion_draft_on_open_matter,
    create_submission,
    mark_submission_sent_on_open_matter,
    register_sent_opinion_on_open_matter,
    select_final_evidence,
    select_final_evidence_on_open_matter,
)
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _documents_page(client, matter) -> str:
    response = client.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _pdf(name: str = "arvamus.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4 synthetic", content_type="application/pdf")


def _opinion_file(matter, *, name: str, actor):
    """A file the application classifies as the Chamber's opinion."""
    document = create_document(
        matter=matter, title=name, role=DocumentRole.KODA_SUBMISSION_FINAL, created_by=actor
    )
    add_evidence_version(
        document=document,
        content=f"%PDF-1.4 {name}".encode(),
        original_filename=name,
        mime_type="application/pdf",
        uploaded_by=actor,
    )
    document.refresh_from_db()
    return document


def _close(matter, actor):
    """Close it the way the other tab does — through the domain service."""
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=actor, reason="QA")
    matter.refresh_from_db()
    return matter


def _stored_objects(evidence_root) -> set[str]:
    """Every file currently in this test's evidence store, by relative path.

    Compared rather than counted, so a refusal that removed one object and wrote
    another could not pass as «unchanged» (`app/documents/integrity.py`).
    """
    tree = evidence_root / "evidence"
    return {str(path.relative_to(tree)) for path in tree.rglob("*") if path.is_file()}


class Census:
    """Everything a refused write must not have moved."""

    def __init__(self, matter, evidence_root):
        self.matter = matter
        self.evidence_root = evidence_root
        self.documents = Document.objects.filter(matter=matter).count()
        self.versions = DocumentVersion.objects.filter(document__matter=matter).count()
        self.submissions = Submission.objects.filter(matter=matter).count()
        self.recipients = SubmissionRecipient.objects.filter(submission__matter=matter).count()
        self.events = ChangeEvent.objects.filter(matter=matter).count()
        self.search_rows = SearchDocument.objects.count()
        self.objects = _stored_objects(evidence_root)

    def assert_unchanged(self) -> None:
        assert Document.objects.filter(matter=self.matter).count() == self.documents
        assert DocumentVersion.objects.filter(document__matter=self.matter).count() == self.versions
        assert Submission.objects.filter(matter=self.matter).count() == self.submissions
        assert (
            SubmissionRecipient.objects.filter(submission__matter=self.matter).count()
            == self.recipients
        )
        assert ChangeEvent.objects.filter(matter=self.matter).count() == self.events
        assert SearchDocument.objects.count() == self.search_rows
        # The orphan check, and the reason the set is compared: a stored object
        # whose row was rolled back is invisible to every constraint in the
        # schema and only `prune_orphaned_evidence` ever finds it again.
        assert _stored_objects(self.evidence_root) == self.objects


# ===========================================================================
# A. The fresh closed page offers no normal write control
# ===========================================================================


def test_a_closed_matters_documents_page_offers_no_upload(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _opinion_file(matter, name="Koja_arvamus.pdf", actor=specialist)
    _close(matter, specialist)

    body = _documents_page(signed_in, matter)

    assert "↑ Lae dokument" not in body
    assert reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}) not in body


def test_a_closed_matters_documents_page_offers_no_opinion_write(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _opinion_file(matter, name="Koja_arvamus.pdf", actor=specialist)
    _close(matter, specialist)

    body = _documents_page(signed_in, matter)

    assert "+ Uus arvamus" not in body
    assert "+ Registreeri saatmine" not in body
    assert reverse("submissions:create", kwargs={"matter_id": matter.pk}) not in body
    assert reverse("submissions:register_sent", kwargs={"matter_id": matter.pk}) not in body


def test_a_closed_matter_shows_no_draft_write_controls(signed_in, specialist):
    """The draft is still listed and still says what it is waiting for."""
    matter = factories.MatterFactory(owner=specialist)
    draft = create_submission(matter=matter, title="Koostatav arvamus", actor=specialist)
    _close(matter, specialist)

    body = _documents_page(signed_in, matter)

    assert "Koostatav arvamus" in body
    assert "ootab faili" in body
    assert reverse("submissions:attach_evidence", kwargs={"pk": draft.pk}) not in body
    assert reverse("submissions:mark_sent", kwargs={"pk": draft.pk}) not in body


def test_a_closed_matter_keeps_every_reading_affordance(signed_in, specialist, organisation):
    """Read-only is not blank: the files, the send and the downloads all stay."""
    matter = factories.MatterFactory(owner=specialist)
    document = _opinion_file(matter, name="Koja_arvamus.pdf", actor=specialist)
    register_sent_opinion_on_open_matter(
        document=document,
        version=document.current_version,
        title="Koja arvamus",
        actor=specialist,
        recipients=[organisation],
        sent_at=timezone.now() - datetime.timedelta(days=10),
        sent_at_precision=SentAtPrecision.DATE,
    )
    _close(matter, specialist)

    body = _documents_page(signed_in, matter)

    assert "Koja_arvamus.pdf" in body
    assert organisation.name in body
    assert reverse("documents:download", kwargs={"pk": document.current_version.pk}) in body
    assert reverse("documents:open", kwargs={"pk": document.current_version.pk}) in body
    assert reverse("documents:document_detail", kwargs={"pk": document.pk}) in body
    # The banner that says why, and the one control that changes it.
    assert "Teema on suletud." in body
    assert reverse("matters:reopen", kwargs={"pk": matter.pk}) in body


# ===========================================================================
# B. A stale upload is refused and leaves nothing
# ===========================================================================


def test_a_stale_upload_after_closure_is_refused(signed_in, specialist, evidence_root):
    """The page was rendered before the closure; the POST arrives after it."""
    matter = factories.MatterFactory(owner=specialist)
    _opinion_file(matter, name="Olemasolev.pdf", actor=specialist)
    _close(matter, specialist)
    census = Census(matter, evidence_root)

    response = signed_in.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}),
        {"upload": _pdf("hiline.pdf"), "role": DocumentRole.INCOMING_AUTHORITY, "title": ""},
        follow=True,
    )

    assert response.status_code == 200
    assert CLOSED_MATTER_REFUSAL in response.content.decode()
    census.assert_unchanged()


def test_a_stale_upload_writes_neither_the_document_nor_its_bytes(specialist, evidence_root):
    """Asserted at the use case, where «all or none» is actually decided."""
    matter = factories.MatterFactory(owner=specialist)
    _close(matter, specialist)
    census = Census(matter, evidence_root)

    with pytest.raises(DomainError) as refusal:
        capture_evidence_on_open_matter(
            matter=matter,
            title="Hiline tõend",
            role=DocumentRole.INCOMING_AUTHORITY,
            content=b"%PDF-1.4 hiline",
            original_filename="hiline.pdf",
            mime_type="application/pdf",
            actor=specialist,
        )

    assert str(refusal.value) == CLOSED_MATTER_REFUSAL
    census.assert_unchanged()


def test_a_further_version_of_an_existing_file_is_refused_too(specialist, evidence_root):
    """New bytes are new evidence, whichever document they land on."""
    matter = factories.MatterFactory(owner=specialist)
    document = _opinion_file(matter, name="Koja_arvamus.pdf", actor=specialist)
    _close(matter, specialist)
    census = Census(matter, evidence_root)

    with pytest.raises(DomainError):
        add_version_on_open_matter(
            document=document,
            content=b"%PDF-1.4 teine versioon",
            original_filename="Koja_arvamus_v2.pdf",
            mime_type="application/pdf",
            uploaded_by=specialist,
        )

    census.assert_unchanged()
    document.refresh_from_db()
    assert document.versions.count() == 1


# ===========================================================================
# C. A stale «+ Uus arvamus» is refused and leaves no Submission
# ===========================================================================


def test_a_stale_new_opinion_is_refused(signed_in, specialist, organisation, evidence_root):
    matter = factories.MatterFactory(owner=specialist)
    _close(matter, specialist)
    census = Census(matter, evidence_root)

    response = signed_in.post(
        reverse("submissions:create", kwargs={"matter_id": matter.pk}),
        {
            "arvamus-title": "Hiline arvamus",
            "arvamus-kind": SubmissionKind.FORMAL_OPINION,
            "arvamus-recipients": [str(organisation.pk)],
            "arvamus-channel": "EIS",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert CLOSED_MATTER_REFUSAL in response.content.decode()
    assert not Submission.objects.filter(matter=matter).exists()
    census.assert_unchanged()


def test_a_stale_new_opinion_writes_no_recipients_and_no_event(specialist, evidence_root):
    matter = factories.MatterFactory(owner=specialist)
    organisation = factories.OrganisationFactory()
    _close(matter, specialist)
    census = Census(matter, evidence_root)

    with pytest.raises(DomainError) as refusal:
        create_opinion_draft_on_open_matter(
            matter=matter,
            title="Hiline arvamus",
            actor=specialist,
            recipients=[organisation],
        )

    assert str(refusal.value) == CLOSED_MATTER_REFUSAL
    census.assert_unchanged()


# ===========================================================================
# D. A stale «Registreeri saatmine» is refused before the SENT record
# ===========================================================================


def test_a_stale_registration_is_refused(signed_in, specialist, organisation, evidence_root):
    matter = factories.MatterFactory(owner=specialist)
    document = _opinion_file(matter, name="Koja_arvamus.pdf", actor=specialist)
    _close(matter, specialist)
    census = Census(matter, evidence_root)

    response = signed_in.post(
        reverse("submissions:register_sent", kwargs={"matter_id": matter.pk}),
        {
            "saadetud-document": str(document.pk),
            "saadetud-title": "Koja arvamus",
            "saadetud-kind": SubmissionKind.FORMAL_OPINION,
            "saadetud-recipients": [str(organisation.pk)],
            "saadetud-sent_on": "2026-06-01",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert CLOSED_MATTER_REFUSAL in response.content.decode()
    assert not Submission.objects.filter(matter=matter).exists()
    census.assert_unchanged()


def test_a_stale_registration_refuses_before_the_submission_exists(
    specialist, organisation, evidence_root
):
    matter = factories.MatterFactory(owner=specialist)
    document = _opinion_file(matter, name="Koja_arvamus.pdf", actor=specialist)
    _close(matter, specialist)
    census = Census(matter, evidence_root)

    with pytest.raises(DomainError) as refusal:
        register_sent_opinion_on_open_matter(
            document=document,
            version=document.current_version,
            title="Koja arvamus",
            actor=specialist,
            recipients=[organisation],
            sent_at=timezone.now() - datetime.timedelta(days=30),
            sent_at_precision=SentAtPrecision.DATE,
        )

    assert str(refusal.value) == CLOSED_MATTER_REFUSAL
    census.assert_unchanged()


def test_the_registration_route_keeps_every_correctness_rule_it_had(
    signed_in, specialist, organisation
):
    """#182's rules are unchanged by the boundary added around them (R2-01).

    An open Matter, so the closed-Matter guard is not what is being measured:
    the missing date and the missing addressee must still each refuse on their
    own, and a supplied date must still be stored as a DATE rather than as now.
    """
    matter = factories.MatterFactory(owner=specialist)
    document = _opinion_file(matter, name="Koja_arvamus.pdf", actor=specialist)
    route = reverse("submissions:register_sent", kwargs={"matter_id": matter.pk})

    no_date = signed_in.post(
        route,
        {
            "saadetud-document": str(document.pk),
            "saadetud-title": "Koja arvamus",
            "saadetud-kind": SubmissionKind.FORMAL_OPINION,
            "saadetud-recipients": [str(organisation.pk)],
        },
        follow=True,
    )
    assert "Saadetud" in no_date.content.decode()
    assert not Submission.objects.filter(matter=matter).exists()

    no_recipient = signed_in.post(
        route,
        {
            "saadetud-document": str(document.pk),
            "saadetud-title": "Koja arvamus",
            "saadetud-kind": SubmissionKind.FORMAL_OPINION,
            "saadetud-sent_on": "2026-06-01",
        },
        follow=True,
    )
    assert "Adressaadid" in no_recipient.content.decode()
    assert not Submission.objects.filter(matter=matter).exists()

    signed_in.post(
        route,
        {
            "saadetud-document": str(document.pk),
            "saadetud-title": "Koja arvamus",
            "saadetud-kind": SubmissionKind.FORMAL_OPINION,
            "saadetud-recipients": [str(organisation.pk)],
            "saadetud-sent_on": "2026-06-01",
        },
    )
    submission = Submission.objects.get(matter=matter)
    assert submission.status == SubmissionStatus.SENT
    assert submission.sent_at_precision == SentAtPrecision.DATE
    assert timezone.localdate(submission.sent_at) == datetime.date(2026, 6, 1)


def test_a_drafts_own_evidence_is_still_not_a_registration_candidate(specialist, organisation):
    """The other half of #182, re-asserted through the guarded route."""
    matter = factories.MatterFactory(owner=specialist)
    document = _opinion_file(matter, name="Koostatav.pdf", actor=specialist)
    draft = create_submission(matter=matter, title="Koostatav arvamus", actor=specialist)
    select_final_evidence(submission=draft, version=document.current_version, actor=specialist)

    with pytest.raises(DomainError) as refusal:
        register_sent_opinion_on_open_matter(
            document=document,
            version=document.current_version,
            title="Sama fail teist korda",
            actor=specialist,
            recipients=[organisation],
            sent_at=timezone.now() - datetime.timedelta(days=1),
            sent_at_precision=SentAtPrecision.DATE,
        )

    assert str(refusal.value) != CLOSED_MATTER_REFUSAL
    assert Submission.objects.filter(matter=matter).count() == 1


# ===========================================================================
# E. An existing draft cannot be advanced
# ===========================================================================


@pytest.fixture
def closed_matter_with_a_draft(specialist):
    matter = factories.MatterFactory(owner=specialist)
    draft = create_submission(matter=matter, title="Koostatav arvamus", actor=specialist)
    _close(matter, specialist)
    return matter, draft


def test_attaching_final_evidence_to_a_draft_is_refused(
    closed_matter_with_a_draft, specialist, evidence_root
):
    matter, draft = closed_matter_with_a_draft
    census = Census(matter, evidence_root)

    with pytest.raises(DomainError) as refusal:
        attach_final_evidence_on_open_matter(
            submission=draft,
            content=b"%PDF-1.4 hiline",
            original_filename="hiline.pdf",
            mime_type="application/pdf",
            actor=specialist,
        )

    assert str(refusal.value) == CLOSED_MATTER_REFUSAL
    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.DRAFT
    assert draft.final_version_id is None
    census.assert_unchanged()


def test_selecting_existing_evidence_for_a_draft_is_refused(specialist, evidence_root):
    matter = factories.MatterFactory(owner=specialist)
    document = _opinion_file(matter, name="Olemasolev.pdf", actor=specialist)
    draft = create_submission(matter=matter, title="Koostatav arvamus", actor=specialist)
    _close(matter, specialist)
    census = Census(matter, evidence_root)

    with pytest.raises(DomainError) as refusal:
        select_final_evidence_on_open_matter(
            submission=draft, version=document.current_version, actor=specialist
        )

    assert str(refusal.value) == CLOSED_MATTER_REFUSAL
    draft.refresh_from_db()
    assert draft.final_version_id is None
    census.assert_unchanged()


def test_marking_a_draft_sent_is_refused(specialist, evidence_root):
    matter = factories.MatterFactory(owner=specialist)
    document = _opinion_file(matter, name="Valmis.pdf", actor=specialist)
    draft = create_submission(matter=matter, title="Valmis arvamus", actor=specialist)
    select_final_evidence(submission=draft, version=document.current_version, actor=specialist)
    _close(matter, specialist)
    census = Census(matter, evidence_root)

    with pytest.raises(DomainError) as refusal:
        mark_submission_sent_on_open_matter(submission=draft, actor=specialist, channel="EIS")

    assert str(refusal.value) == CLOSED_MATTER_REFUSAL
    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.DRAFT
    assert draft.sent_at is None
    # The evidence it already had is untouched: the refusal is about advancing,
    # not about unwinding what was legitimately recorded before the closure.
    assert draft.final_version_id == document.current_version_id
    census.assert_unchanged()


def test_the_stale_draft_routes_refuse_over_http_too(signed_in, specialist, evidence_root):
    """Both draft controls, through the browser's own POST."""
    matter = factories.MatterFactory(owner=specialist)
    document = _opinion_file(matter, name="Valmis.pdf", actor=specialist)
    draft = create_submission(matter=matter, title="Valmis arvamus", actor=specialist)
    _close(matter, specialist)
    census = Census(matter, evidence_root)

    attached = signed_in.post(
        reverse("submissions:attach_evidence", kwargs={"pk": draft.pk}),
        {"existing_version": str(document.current_version.pk)},
        follow=True,
    )
    assert CLOSED_MATTER_REFUSAL in attached.content.decode()

    draft.refresh_from_db()
    assert draft.final_version_id is None

    sent = signed_in.post(
        reverse("submissions:mark_sent", kwargs={"pk": draft.pk}),
        {"channel": "EIS"},
        follow=True,
    )
    assert CLOSED_MATTER_REFUSAL in sent.content.decode()

    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.DRAFT
    census.assert_unchanged()


# ===========================================================================
# F. Reopening restores every one of them
# ===========================================================================


def test_reopening_restores_the_whole_surface(signed_in, specialist, organisation):
    """`closed → no normal business writes`, not `closed → forever immutable`."""
    matter = factories.MatterFactory(owner=specialist)
    _close(matter, specialist)
    reopen_matter(matter=matter, actor=specialist)
    matter.refresh_from_db()
    assert matter.is_open

    body = _documents_page(signed_in, matter)
    assert "↑ Lae dokument" in body
    assert "+ Uus arvamus" in body

    upload = signed_in.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}),
        {"upload": _pdf("koja-arvamus.pdf"), "role": DocumentRole.KODA_SUBMISSION_FINAL},
    )
    assert upload.status_code == 302
    document = Document.objects.get(matter=matter)

    created = signed_in.post(
        reverse("submissions:create", kwargs={"matter_id": matter.pk}),
        {
            "arvamus-title": "Koja arvamus",
            "arvamus-kind": SubmissionKind.FORMAL_OPINION,
            "arvamus-recipients": [str(organisation.pk)],
        },
    )
    assert created.status_code == 302
    draft = Submission.objects.get(matter=matter, status=SubmissionStatus.DRAFT)

    signed_in.post(
        reverse("submissions:attach_evidence", kwargs={"pk": draft.pk}),
        {"upload": _pdf("lõplik.pdf")},
    )
    draft.refresh_from_db()
    assert draft.final_version_id is not None

    signed_in.post(reverse("submissions:mark_sent", kwargs={"pk": draft.pk}), {"channel": "EIS"})
    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.SENT

    # And the registration route, on the file the upload left unaccounted for.
    registered = signed_in.post(
        reverse("submissions:register_sent", kwargs={"matter_id": matter.pk}),
        {
            "saadetud-document": str(document.pk),
            "saadetud-title": "Varem saadetud arvamus",
            "saadetud-kind": SubmissionKind.FORMAL_OPINION,
            "saadetud-recipients": [str(organisation.pk)],
            "saadetud-sent_on": "2026-05-04",
        },
    )
    assert registered.status_code == 302
    assert Submission.objects.filter(matter=matter, status=SubmissionStatus.SENT).count() == 2


def test_reopening_lets_a_draft_left_behind_be_finished(specialist):
    """The route out of the refusal, stated as the product states it."""
    matter = factories.MatterFactory(owner=specialist)
    document = _opinion_file(matter, name="Valmis.pdf", actor=specialist)
    draft = create_submission(matter=matter, title="Valmis arvamus", actor=specialist)
    _close(matter, specialist)

    with pytest.raises(DomainError):
        select_final_evidence_on_open_matter(
            submission=draft, version=document.current_version, actor=specialist
        )

    reopen_matter(matter=matter, actor=specialist)
    select_final_evidence_on_open_matter(
        submission=draft, version=document.current_version, actor=specialist
    )
    mark_submission_sent_on_open_matter(submission=draft, actor=specialist)

    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.SENT

    _close(matter, specialist)
    matter.refresh_from_db()
    assert not matter.is_open
    assert draft.final_version_id == document.current_version_id


# ===========================================================================
# The reader's own boundary is unchanged
# ===========================================================================


def test_an_open_matter_still_offers_everything_to_a_writer(signed_in, specialist):
    """The guard narrowed the closed case and nothing else."""
    matter = factories.MatterFactory(owner=specialist)
    body = _documents_page(signed_in, matter)

    assert "↑ Lae dokument" in body
    assert "+ Uus arvamus" in body
    assert "+ SharePointi viide" in body


def test_a_reader_is_still_offered_nothing_on_an_open_matter(client, reader, specialist):
    """`can_add_content` narrows `can_write`; it does not widen it."""
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(reader)
    body = _documents_page(client, matter)

    assert "↑ Lae dokument" not in body
    assert "+ Uus arvamus" not in body
    assert "+ SharePointi viide" not in body
