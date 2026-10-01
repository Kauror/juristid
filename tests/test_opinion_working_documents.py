"""A `Koja arvamus` keeps its working documents (docs/adr/0129, JUR-CASE-06).

The opinion's sent letter is `Submission.final_version` and nothing else; its
editable DOCX is an ordinary `Document` filed as `Töödokument` and tied to the
exact `Submission` by a `DocumentLink`. Asserted here, each where it is decided:

* **the record** — zero, one or several working documents, each a canonical
  `Document` + `DocumentVersion` with the role `Töödokument`, linked to that
  opinion and never its evidence;
* **the evidence invariant, both ways** — a working document cannot be bound as
  what was sent, and a letter that was sent cannot be filed as a working one;
* **after the send** — `+ Lisa töödokument` adds to that exact opinion and moves
  nothing about the send;
* **versioning** — a working document versions like any file and keeps its
  opinion;
* **the step and the transaction** — 0126's completion still lands, one row
  stays one row, and a refused DOCX refuses everything before any bytes are kept;
* **visibility** — a restricted opinion's working documents are restricted, and
  no link, chronology row, rail line, search hit or count names them to a reader
  who may not see the letter;
* **legacy** — older opinions read as they did, and the dormant
  `Submission.working_document` carry-over is deterministic and idempotent.
"""

from __future__ import annotations

import datetime as dt
import importlib
import re
from pathlib import Path

import pytest
from django.apps import apps as global_apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.links import TARGET_FIELDS, DocumentLink, _removable_target_fields
from app.documents.models import Document, DocumentVersion
from app.documents.services import (
    OPINION_EVIDENCE_IS_NOT_A_WORKING_DOCUMENT,
    add_evidence_version,
    add_version_on_open_matter,
    create_document,
    link_document_to_record,
)
from app.documents.uploads import UploadRejected
from app.matters.services import close_matter
from app.matters.timeline import OPINION_SENT_FILE, OPINION_WORKING_FILE, matter_timeline
from app.matters.workspace import (
    OPINION_WORKING_DOCUMENTS_NEED_A_SEND,
    add_matter_koda_opinion,
    add_opinion_working_documents,
)
from app.search.services import result_count
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.models import Submission
from app.submissions.services import (
    WORKING_DOCUMENT_IS_NOT_EVIDENCE,
    create_submission,
    select_final_evidence,
    withdraw_submission,
)
from app.workflow.enums import ActionStatus, Disposition
from app.workflow.models import NextAction
from app.workflow.services import set_next_action_for_new_work
from tests import factories
from tests.synthetic_containers import signed_container

pytestmark = pytest.mark.django_db

DOCX_NAME = "16 03 2023 arvamus seoses juristieksami seaduse eelnõuga.docx"
SECOND_DOCX = "Lisa 1 tabel.docx"
STEP = "Vormista ja saada Koja seisukoht"


def _docx(name: str = DOCX_NAME, body: bytes = b"synthetic word document") -> SimpleUploadedFile:
    """An OOXML-shaped upload: the ZIP signature the content check reads."""
    return SimpleUploadedFile(
        name,
        b"PK\x03\x04" + body,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def _asice(name: str = "koda_opinion.asice") -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name, signed_container(), content_type="application/vnd.etsi.asic-e+zip"
    )


def _pdf(name: str = "Koja_arvamus.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, f"%PDF-1.4 {name}".encode(), content_type="application/pdf")


def _not_a_docx(name: str = "katki.docx") -> SimpleUploadedFile:
    """A file whose bytes do not match its extension — refused at the door."""
    return SimpleUploadedFile(name, b"%PDF-1.4 not a word file", content_type="application/pdf")


@pytest.fixture
def matter(specialist):
    return factories.MatterFactory(owner=specialist, stage=None)


def _opinion(matter, author, organisation, *, upload=None, working=(), sent_on=None, **kwargs):
    return add_matter_koda_opinion(
        matter=matter,
        author=author,
        upload=upload or _asice(),
        recipients=[organisation],
        sent_on=sent_on or timezone.localdate(),
        working_uploads=list(working),
        **kwargs,
    )


def _working_links(submission):
    return DocumentLink.objects.filter(submission=submission).select_related(
        "document__current_version"
    )


def _rows(matter, user):
    items, _ = matter_timeline(matter=matter, user=user, limit=200)
    return items


def _teema(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _dokumendid(client, matter) -> str:
    return client.get(
        reverse("matters:matter_documents", kwargs={"pk": matter.pk})
    ).content.decode()


def _stored_objects(evidence_root) -> list[Path]:
    return [path for path in (evidence_root / "evidence").rglob("*") if path.is_file()]


# ---------------------------------------------------------------------------
# The relation itself
# ---------------------------------------------------------------------------


def test_submission_is_the_eighth_target_and_the_one_that_is_not_removable():
    """The removal clause is read off the models, so a `Submission` — withdrawn,
    never taken off the file — is neither filtered by `removed_at` nor a
    `FieldError` on every read (docs/adr/0129 §4)."""
    assert TARGET_FIELDS[-1] == "submission"
    assert set(_removable_target_fields()) == set(TARGET_FIELDS) - {"submission"}


def test_the_check_still_refuses_a_link_naming_two_records(matter, specialist, organisation):
    submission = _opinion(matter, specialist, organisation, working=[_docx()]).record
    link = _working_links(submission).get()
    with pytest.raises(IntegrityError), transaction.atomic():
        DocumentLink.objects.filter(pk=link.pk).update(entry=factories.EntryFactory(matter=matter))


def test_one_document_links_to_one_opinion_once(matter, specialist, organisation):
    submission = _opinion(matter, specialist, organisation, working=[_docx()]).record
    document = _working_links(submission).get().document

    again = link_document_to_record(document=document, record=submission, actor=specialist)

    assert _working_links(submission).count() == 1
    assert again.document_id == document.pk


# ---------------------------------------------------------------------------
# A. An opinion with working documents
# ---------------------------------------------------------------------------


def test_an_opinion_may_have_no_working_documents(matter, specialist, organisation):
    result = _opinion(matter, specialist, organisation)

    submission = result.record
    assert submission.status == SubmissionStatus.SENT
    assert not _working_links(submission).exists()
    assert [document.role for document in result.documents] == [DocumentRole.KODA_SUBMISSION_FINAL]


def test_one_working_document_is_a_canonical_document_filed_as_toodokument(
    matter, specialist, organisation
):
    result = _opinion(matter, specialist, organisation, working=[_docx()])

    submission = result.record
    link = _working_links(submission).get()
    document = link.document
    version = document.current_version
    assert document.matter_id == matter.pk
    assert document.role == DocumentRole.WORKING_DOCUMENT
    assert document.title == DOCX_NAME
    assert version.version_number == 1
    assert version.original_filename == DOCX_NAME
    assert version.mime_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert version.sha256 and version.size_bytes == len(b"PK\x03\x04synthetic word document")
    # Not a SharePoint reference: real bytes, held as evidence of the file.
    assert not document.has_working_document
    assert link.created_by == specialist


def test_several_working_documents_all_belong_to_the_same_opinion(matter, specialist, organisation):
    submission = _opinion(
        matter, specialist, organisation, working=[_docx(), _docx(SECOND_DOCX, b"tabel")]
    ).record

    links = list(_working_links(submission))
    assert {link.document.title for link in links} == {DOCX_NAME, SECOND_DOCX}
    assert all(link.document.role == DocumentRole.WORKING_DOCUMENT for link in links)
    assert len({link.document_id for link in links}) == 2


def test_the_sent_letter_stays_separate_and_exactly_as_sent(
    matter, specialist, organisation, evidence_root
):
    container = signed_container()
    upload = SimpleUploadedFile(
        "koda_opinion.asice", container, content_type="application/vnd.etsi.asic-e+zip"
    )

    submission = _opinion(matter, specialist, organisation, upload=upload, working=[_docx()]).record

    letter = submission.final_version
    assert letter.original_filename == "koda_opinion.asice"
    assert letter.mime_type == "application/vnd.etsi.asic-e+zip"
    assert letter.document.role == DocumentRole.KODA_SUBMISSION_FINAL
    # The exact container that arrived — not unpacked, not replaced.
    assert (evidence_root / "evidence" / letter.storage_key).read_bytes() == container
    # The letter is not one of the links, and no working document is the letter.
    assert not DocumentLink.objects.filter(document=letter.document).exists()
    working = _working_links(submission).get().document
    assert working.pk != letter.document_id
    assert not Submission.objects.filter(final_version__document=working).exists()


def test_a_working_document_cannot_be_chosen_as_what_was_sent(matter, specialist, organisation):
    """Evidence-before-SENT cannot be satisfied by a working DOCX (docs/adr/0129 §5)."""
    sent = _opinion(matter, specialist, organisation, working=[_docx()]).record
    working = _working_links(sent).get().document
    draft = create_submission(
        matter=matter, title="Teine arvamus", actor=specialist, recipients=[organisation]
    )

    with pytest.raises(DomainError) as refused:
        select_final_evidence(submission=draft, version=working.current_version, actor=specialist)

    assert str(refused.value) == WORKING_DOCUMENT_IS_NOT_EVIDENCE
    draft.refresh_from_db()
    assert draft.final_version_id is None
    assert draft.status == SubmissionStatus.DRAFT


def test_the_database_still_refuses_a_send_with_only_a_working_document(matter, specialist):
    """The `CHECK` is the floor: a `SENT` row needs `final_version`, and a link is not one."""
    with pytest.raises(IntegrityError), transaction.atomic():
        Submission.objects.create(
            matter=matter,
            title="Ainult töödokument",
            kind=SubmissionKind.FORMAL_OPINION,
            status=SubmissionStatus.SENT,
            sent_at=timezone.now(),
        )


def test_a_sent_letter_cannot_be_filed_as_a_working_document(matter, specialist, organisation):
    first = _opinion(matter, specialist, organisation).record
    second = _opinion(matter, specialist, organisation, upload=_pdf("Teine.pdf")).record

    with pytest.raises(DomainError) as refused:
        link_document_to_record(
            document=first.final_version.document, record=second, actor=specialist
        )

    assert str(refused.value) == OPINION_EVIDENCE_IS_NOT_A_WORKING_DOCUMENT
    assert not DocumentLink.objects.filter(submission=second).exists()


def test_a_file_bound_as_evidence_under_another_role_is_refused_as_well(
    matter, specialist, organisation
):
    """Evidence is decided by the send, not only by the role (UX-005's lesson)."""
    incoming = create_document(
        matter=matter, title="Ministeeriumi kiri", role=DocumentRole.INCOMING_AUTHORITY
    )
    version = add_evidence_version(
        document=incoming,
        content=b"%PDF-1.4 kiri",
        original_filename="kiri.pdf",
        mime_type="application/pdf",
    )
    draft = create_submission(matter=matter, title="Mustand", actor=specialist)
    select_final_evidence(submission=draft, version=version, actor=specialist)
    other = _opinion(matter, specialist, organisation).record

    with pytest.raises(DomainError):
        link_document_to_record(document=incoming, record=other, actor=specialist)


def test_existing_opinions_are_untouched_and_read_as_before(
    signed_in, matter, specialist, organisation
):
    submission = _opinion(matter, specialist, organisation, upload=_pdf()).record

    rows = [row for row in _rows(matter, specialist) if row.submission is not None]
    assert len(rows) == 1
    assert [file.kind for file in rows[0].files] == [OPINION_SENT_FILE]
    assert rows[0].opinion_working_files == ()

    body = _teema(signed_in, matter)
    kaik = body[body.index('id="ajajoon"') :]
    assert "Koja_arvamus.pdf" in kaik
    assert "uxtl__filegroup" not in kaik
    assert submission.final_version.original_filename == "Koja_arvamus.pdf"


# ---------------------------------------------------------------------------
# B. After the send: `+ Lisa töödokument`
# ---------------------------------------------------------------------------


def test_a_working_document_can_be_added_to_a_sent_opinion(matter, specialist, organisation):
    submission = _opinion(matter, specialist, organisation).record
    before = Submission.objects.get(pk=submission.pk)
    addressees = list(before.recipient_rows.values_list("organisation_id", "role"))

    result = add_opinion_working_documents(
        submission=submission, author=specialist, uploads=[_docx()]
    )

    after = Submission.objects.get(pk=submission.pk)
    assert [document.role for document in result.documents] == [DocumentRole.WORKING_DOCUMENT]
    assert _working_links(submission).get().document_id == result.documents[0].pk
    # Nothing about the send moved.
    assert after.final_version_id == before.final_version_id
    assert after.sent_at == before.sent_at
    assert after.status == SubmissionStatus.SENT
    assert after.summary == before.summary
    assert list(after.recipient_rows.values_list("organisation_id", "role")) == addressees
    assert Submission.objects.filter(matter=matter).count() == 1
    assert not ChangeEvent.objects.filter(
        matter=matter,
        event_type__in=(
            ChangeEventType.SUBMISSION_CORRECTED,
            ChangeEventType.SUBMISSION_RECIPIENTS_CHANGED,
            ChangeEventType.SUBMISSION_SENT,
        ),
        operation_id=result.operation_id,
    ).exists()


def test_adding_through_the_row_answers_with_the_column(
    signed_in, matter, specialist, organisation
):
    submission = _opinion(matter, specialist, organisation).record
    url = reverse(
        "matters:add_opinion_working_documents",
        kwargs={"pk": matter.pk, "submission_id": submission.pk},
    )

    picker = signed_in.get(url, headers={"HX-Request": "true"})
    assert picker.status_code == 200
    assert "Lisa töödokument" in picker.content.decode()

    response = signed_in.post(
        url, {"attachments": [_docx(), _docx(SECOND_DOCX, b"x")]}, headers={"HX-Request": "true"}
    )

    assert response.status_code == 200
    assert _working_links(submission).count() == 2
    body = response.content.decode()
    assert 'id="teema-vaade"' in body
    assert DOCX_NAME in body and SECOND_DOCX in body


def test_an_empty_picker_is_refused_and_writes_nothing(signed_in, matter, specialist, organisation):
    submission = _opinion(matter, specialist, organisation).record
    url = reverse(
        "matters:add_opinion_working_documents",
        kwargs={"pk": matter.pk, "submission_id": submission.pk},
    )

    response = signed_in.post(url, {}, headers={"HX-Request": "true"})

    assert response.status_code == 400
    assert "Vali vähemalt üks fail." in response.content.decode()
    assert not _working_links(submission).exists()
    with pytest.raises(DomainError):
        add_opinion_working_documents(submission=submission, author=specialist, uploads=[])


def test_a_draft_is_refused(matter, specialist, organisation):
    draft = create_submission(matter=matter, title="Mustand", actor=specialist)

    with pytest.raises(DomainError) as refused:
        add_opinion_working_documents(submission=draft, author=specialist, uploads=[_docx()])

    assert str(refused.value) == OPINION_WORKING_DOCUMENTS_NEED_A_SEND
    assert not Document.objects.filter(matter=matter, role=DocumentRole.WORKING_DOCUMENT).exists()


def test_a_withdrawn_opinion_still_takes_its_working_document(matter, specialist, organisation):
    submission = _opinion(matter, specialist, organisation).record
    withdraw_submission(submission=submission, actor=specialist)

    add_opinion_working_documents(submission=submission, author=specialist, uploads=[_docx()])

    assert _working_links(submission).count() == 1


def test_a_closed_matter_refuses_a_new_working_document(matter, specialist, organisation):
    submission = _opinion(matter, specialist, organisation).record
    close_matter(matter=matter, disposition=Disposition.OTHER, actor=specialist)

    with pytest.raises(DomainError):
        add_opinion_working_documents(submission=submission, author=specialist, uploads=[_docx()])

    assert not _working_links(submission).exists()


def test_an_opinion_on_another_matter_is_a_404(signed_in, matter, specialist, organisation):
    other = factories.MatterFactory(owner=specialist, title="Teine teema")
    submission = _opinion(other, specialist, organisation).record

    response = signed_in.post(
        reverse(
            "matters:add_opinion_working_documents",
            kwargs={"pk": matter.pk, "submission_id": submission.pk},
        ),
        {"attachments": [_docx()]},
    )

    assert response.status_code == 404
    assert not _working_links(submission).exists()


def test_a_reader_may_not_add_one(client, reader, matter, specialist, organisation):
    submission = _opinion(matter, specialist, organisation).record
    client.force_login(reader)

    response = client.post(
        reverse(
            "matters:add_opinion_working_documents",
            kwargs={"pk": matter.pk, "submission_id": submission.pk},
        ),
        {"attachments": [_docx()]},
    )

    assert response.status_code in (403, 404)
    assert not _working_links(submission).exists()


def test_the_row_offers_the_button_only_on_an_open_matter(
    signed_in, matter, specialist, organisation
):
    submission = _opinion(matter, specialist, organisation).record
    button = f'id="koja-arvamus-{submission.pk}-toodokument"'

    assert button in _teema(signed_in, matter)

    close_matter(matter=matter, disposition=Disposition.OTHER, actor=specialist)
    assert button not in _teema(signed_in, matter)


# ---------------------------------------------------------------------------
# C. Versioning
# ---------------------------------------------------------------------------


def test_a_working_document_versions_like_any_file_and_keeps_its_opinion(
    matter, specialist, organisation, evidence_root
):
    submission = _opinion(matter, specialist, organisation, working=[_docx()]).record
    document = _working_links(submission).get().document
    first = document.current_version
    first_bytes = (evidence_root / "evidence" / first.storage_key).read_bytes()

    second = add_version_on_open_matter(
        document=document,
        content=b"PK\x03\x04revised draft",
        original_filename="arvamus v2.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        uploaded_by=specialist,
    )

    document.refresh_from_db()
    assert document.current_version_id == second.pk
    assert second.version_number == 2
    # The earlier version is exactly what it was.
    unchanged = DocumentVersion.objects.get(pk=first.pk)
    assert (unchanged.sha256, unchanged.storage_key) == (first.sha256, first.storage_key)
    assert (evidence_root / "evidence" / first.storage_key).read_bytes() == first_bytes
    # Still that opinion's working document, and the letter is still the letter.
    assert _working_links(submission).get().document_id == document.pk
    submission.refresh_from_db()
    assert submission.final_version.document.role == DocumentRole.KODA_SUBMISSION_FINAL
    # The chronology reads the current version under the opinion's row.
    row = next(row for row in _rows(matter, specialist) if row.submission is not None)
    assert [file.label for file in row.opinion_working_files] == ["arvamus v2.docx"]


def test_the_letter_still_refuses_a_new_version(matter, specialist, organisation):
    submission = _opinion(matter, specialist, organisation, working=[_docx()]).record

    with pytest.raises(DomainError):
        add_version_on_open_matter(
            document=submission.final_version.document,
            content=signed_container(),
            original_filename="koda_opinion.asice",
            mime_type="application/vnd.etsi.asic-e+zip",
            uploaded_by=specialist,
        )


# ---------------------------------------------------------------------------
# D. The step, the transaction and the chronology
# ---------------------------------------------------------------------------


def _step(matter, actor) -> NextAction:
    return set_next_action_for_new_work(
        matter=matter,
        text=STEP,
        target_date=timezone.localdate() - dt.timedelta(days=1),
        actor=actor,
    )


def test_the_tick_still_completes_the_step_with_working_documents(matter, specialist, organisation):
    step = _step(matter, specialist)

    result = _opinion(
        matter, specialist, organisation, working=[_docx()], complete_action_id=step.pk
    )

    step.refresh_from_db()
    assert step.status == ActionStatus.COMPLETED
    assert step.replaced_by is None
    assert result.action == step
    # One operation for all of it.
    events = ChangeEvent.objects.filter(matter=matter, operation_id=result.operation_id)
    kinds = set(events.values_list("event_type", flat=True))
    assert ChangeEventType.SUBMISSION_SENT in kinds
    assert ChangeEventType.NEXT_ACTION_COMPLETED in kinds
    assert events.filter(event_type=ChangeEventType.EVIDENCE_VERSION_ADDED).count() == 2


def test_one_save_is_one_row_with_its_files_grouped(matter, specialist, organisation):
    step = _step(matter, specialist)
    _opinion(
        matter,
        specialist,
        organisation,
        working=[_docx(), _docx(SECOND_DOCX, b"x")],
        complete_action_id=step.pk,
    )

    rows = _rows(matter, specialist)

    sends = [row for row in rows if row.submission is not None]
    assert len(sends) == 1
    assert [file.label for file in sends[0].opinion_sent_files] == ["koda_opinion.asice"]
    assert sorted(file.label for file in sends[0].opinion_working_files) == sorted(
        [DOCX_NAME, SECOND_DOCX]
    )
    assert all(file.kind == OPINION_WORKING_FILE for file in sends[0].opinion_working_files)
    assert sends[0].completed_step is not None
    # No «lisas dokumendi» row of its own for any of the three files.
    assert not [
        row
        for row in rows
        if row.record is None
        and any(event.event_type == ChangeEventType.EVIDENCE_VERSION_ADDED for event in row.events)
    ]
    assert not [row for row in rows if row.is_entry]


def test_a_later_working_document_adds_no_row(matter, specialist, organisation):
    submission = _opinion(matter, specialist, organisation).record
    before = len(_rows(matter, specialist))

    add_opinion_working_documents(submission=submission, author=specialist, uploads=[_docx()])

    rows = _rows(matter, specialist)
    assert len(rows) == before
    send = next(row for row in rows if row.submission is not None)
    assert [file.label for file in send.opinion_working_files] == [DOCX_NAME]


def test_a_refused_working_document_refuses_everything(
    matter, specialist, organisation, evidence_root
):
    """No opinion, no letter, no completed step and no stored bytes (docs/adr/0129 §6)."""
    step = _step(matter, specialist)

    with pytest.raises(UploadRejected) as refused:
        _opinion(
            matter,
            specialist,
            organisation,
            working=[_docx(), _not_a_docx()],
            complete_action_id=step.pk,
        )

    assert str(refused.value).startswith("Töödokumendid:")
    assert "katki.docx" in str(refused.value)
    assert not Submission.objects.filter(matter=matter).exists()
    assert not Document.objects.filter(matter=matter).exists()
    step.refresh_from_db()
    assert step.status == ActionStatus.OPEN
    assert _stored_objects(evidence_root) == []


def test_the_panel_refusal_names_the_working_document_box(
    signed_in, matter, specialist, organisation
):
    response = signed_in.post(
        reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}),
        {
            "upload": _asice(),
            "working_files": [_not_a_docx()],
            "recipients": [str(organisation.pk)],
            "sent_on": timezone.localdate().strftime("%d.%m.%Y"),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    assert "Töödokumendid:" in response.content.decode()
    assert not Submission.objects.filter(matter=matter).exists()


def test_the_panel_saves_both_kinds_in_one_press(signed_in, matter, specialist, organisation):
    step = _step(matter, specialist)

    response = signed_in.post(
        reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}),
        {
            "upload": _asice(),
            "working_files": [_docx()],
            "recipients": [str(organisation.pk)],
            "sent_on": timezone.localdate().strftime("%d.%m.%Y"),
            "complete_action": str(step.pk),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200, response.content.decode()[:600]
    submission = Submission.objects.get(matter=matter)
    assert submission.final_version.original_filename == "koda_opinion.asice"
    assert _working_links(submission).get().document.title == DOCX_NAME
    step.refresh_from_db()
    assert step.status == ActionStatus.COMPLETED
    body = response.content.decode()
    kaik = body[body.index('id="ajajoon"') :]
    assert kaik.count("Arvamus välja") == 1
    groups = re.findall(r'<span class="uxtl__filegrouplabel">([^<]+)</span>', kaik)
    assert groups == ["Saadetud", "Töödokumendid"]
    saadetud = kaik[kaik.index(">Saadetud<") : kaik.index(">Töödokumendid<")]
    assert "koda_opinion.asice" in saadetud and DOCX_NAME not in saadetud


def test_the_panel_offers_the_working_document_box_unticked_and_optional(
    signed_in, matter, specialist
):
    body = _teema(signed_in, matter)
    panel = body[body.index('id="arvamus-koja"') :]
    panel = panel[: panel.index("</form>")]

    assert "Töödokumendid" in panel
    box = re.search(r'<input type="file" name="working_files"[^>]*>', panel)
    assert box is not None
    assert "multiple" in box.group(0)
    assert 'id="id_koja_arvamus_toodokumendid"' in box.group(0)
    assert ".docx" in box.group(0) and ".asice" in box.group(0)


# ---------------------------------------------------------------------------
# E. Visibility
# ---------------------------------------------------------------------------


def _restricted_opinion(matter, specialist, organisation):
    """A send restricted below its normal Matter, as the archive apply writes one."""
    submission = _opinion(matter, specialist, organisation).record
    # The letter first: the evidence trigger refuses a submission restricted
    # above the bytes it stands on, in either order of writing.
    Document.objects.filter(pk=submission.final_version.document_id).update(
        visibility_override=Visibility.RESTRICTED
    )
    Submission.objects.filter(pk=submission.pk).update(visibility_override=Visibility.RESTRICTED)
    submission.refresh_from_db()
    return submission


def test_a_restricted_opinions_working_document_is_restricted_with_it(
    matter, specialist, reader, organisation
):
    submission = _restricted_opinion(matter, specialist, organisation)

    document = add_opinion_working_documents(
        submission=submission, author=specialist, uploads=[_docx()]
    ).documents[0]

    assert document.visibility_override == Visibility.RESTRICTED
    assert not Document.objects.visible_to(reader).filter(pk=document.pk).exists()
    assert Document.objects.visible_to(specialist).filter(pk=document.pk).exists()
    assert not DocumentLink.objects.visible_to(reader).filter(submission=submission).exists()
    assert DocumentLink.objects.visible_to(specialist).filter(submission=submission).exists()


def test_a_reader_sees_neither_the_file_nor_the_relation(
    client, matter, specialist, reader, organisation
):
    submission = _restricted_opinion(matter, specialist, organisation)
    add_opinion_working_documents(submission=submission, author=specialist, uploads=[_docx()])
    client.force_login(reader)

    teema = _teema(client, matter)
    dokumendid = _dokumendid(client, matter)

    for body in (teema, dokumendid):
        assert DOCX_NAME not in body
        assert "Koja arvamus ·" not in body
        assert organisation.name not in body
    assert "Arvamus välja" not in teema[teema.index('id="ajajoon"') :]


def test_a_normal_file_linked_to_a_restricted_opinion_discloses_no_relation(
    client, matter, specialist, reader, organisation
):
    """The pinned gap 0075's links keep — a normal file on a restricted record —
    reached by hand here: the file may be listed, the relation may not."""
    submission = _restricted_opinion(matter, specialist, organisation)
    document = create_document(matter=matter, title="Avalik eelnõu.docx")
    add_evidence_version(
        document=document,
        content=b"PK\x03\x04avalik",
        original_filename="Avalik eelnõu.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    link_document_to_record(document=document, record=submission, actor=specialist)
    client.force_login(reader)

    body = _dokumendid(client, matter)

    assert "Avalik eelnõu.docx" in body
    row = body[body.index(f'id="dokument-{document.pk}"') :]
    row = row[: row.index("</tr>")]
    assert "Seotud kirje" not in row
    assert "Koja arvamus" not in row
    assert organisation.name not in row


def test_search_finds_a_restricted_working_document_only_for_who_may_see_it(
    matter, specialist, reader, organisation
):
    submission = _restricted_opinion(matter, specialist, organisation)
    add_opinion_working_documents(
        submission=submission, author=specialist, uploads=[_docx("Salajane tööversioon.docx")]
    )

    assert result_count(query="tööversioon", user=specialist) >= 1
    assert result_count(query="tööversioon", user=reader) == 0


def test_counts_do_not_move_for_a_reader(client, matter, specialist, reader, organisation):
    """A count that moves is a disclosure (docs/adr/0038)."""
    _opinion(matter, specialist, organisation, upload=_pdf("Avalik.pdf"))
    client.force_login(reader)
    before = _dokumendid(client, matter)
    before_count = Document.objects.visible_to(reader).filter(matter=matter).count()

    submission = _restricted_opinion(matter, specialist, organisation)
    add_opinion_working_documents(submission=submission, author=specialist, uploads=[_docx()])

    after_count = Document.objects.visible_to(reader).filter(matter=matter).count()
    # The restricted letter and its working document add nothing the reader can count.
    assert after_count == before_count
    after = _dokumendid(client, matter)
    tab = re.compile(r"Dokumendid\s*<span[^>]*>(\d+)</span>")
    if tab.search(before) and tab.search(after):
        assert tab.search(before).group(1) == tab.search(after).group(1)


def test_a_restricted_matter_is_a_404_for_a_reader(client, specialist, reader, organisation):
    restricted = factories.MatterFactory(owner=specialist, visibility=Visibility.RESTRICTED)
    submission = _opinion(restricted, specialist, organisation, working=[_docx()]).record
    client.force_login(reader)

    assert (
        client.get(reverse("matters:matter_detail", kwargs={"pk": restricted.pk})).status_code
        == 404
    )
    assert (
        client.get(
            reverse(
                "matters:add_opinion_working_documents",
                kwargs={"pk": restricted.pk, "submission_id": submission.pk},
            )
        ).status_code
        == 404
    )
    assert not DocumentLink.objects.visible_to(reader).filter(submission=submission).exists()


def test_the_owner_sees_the_working_document_everywhere(
    signed_in, matter, specialist, organisation
):
    submission = _opinion(matter, specialist, organisation, working=[_docx()]).record
    document = _working_links(submission).get().document

    teema = _teema(signed_in, matter)
    dokumendid = _dokumendid(signed_in, matter)

    download = reverse("documents:download", kwargs={"pk": document.current_version.pk})
    assert download in teema
    assert (
        download in dokumendid
        or reverse("documents:document_detail", kwargs={"pk": document.pk}) in dokumendid
    )
    detail = signed_in.get(reverse("documents:document_detail", kwargs={"pk": document.pk}))
    assert detail.status_code == 200
    assert signed_in.get(download).status_code == 200


# ---------------------------------------------------------------------------
# The rail (UQ-28, docs/adr/0129 §9)
# ---------------------------------------------------------------------------


def _rail_block(body: str) -> str:
    """The `Koja arvamus` rail card, up to the start of the next card."""
    rest = body[body.index('id="koja-arvamus"') :]
    end = rest.find('class="railcard"', 1)
    return rest if end == -1 else rest[:end]


def test_two_opinions_are_told_apart_in_the_rail(signed_in, matter, specialist, organisation):
    ministry = factories.OrganisationFactory(name="Justiits- ja Digiministeerium")
    committee = factories.OrganisationFactory(name="Riigikogu õiguskomisjon")
    early = timezone.localdate() - dt.timedelta(days=23)
    first = add_matter_koda_opinion(
        matter=matter,
        author=specialist,
        upload=_asice(),
        recipients=[ministry],
        sent_on=early,
        working_uploads=[_docx("esimene.docx")],
    ).record
    second = add_matter_koda_opinion(
        matter=matter,
        author=specialist,
        upload=_asice(),
        recipients=[committee],
        sent_on=timezone.localdate(),
        working_uploads=[_docx("teine.docx")],
    ).record

    block = _rail_block(_teema(signed_in, matter))

    from app.core.dates import format_estonian_date

    first_label = f"{format_estonian_date(early)} · Justiits- ja Digiministeerium"
    second_label = f"{format_estonian_date(timezone.localdate())} · Riigikogu õiguskomisjon"
    assert first_label in block and second_label in block
    # Each working document under its own opinion.
    first_at = block.index(first_label)
    second_at = block.index(second_label)
    lines = sorted([(first_at, "esimene.docx"), (second_at, "teine.docx")])
    for (start, name), (end, _) in zip(lines, [*lines[1:], (len(block), "")], strict=True):
        assert name in block[start:end]
    assert first.pk != second.pk


def test_a_withdrawn_opinion_says_so_in_the_rail(signed_in, matter, specialist, organisation):
    submission = _opinion(matter, specialist, organisation).record
    withdraw_submission(submission=submission, actor=specialist)

    block = _rail_block(_teema(signed_in, matter))

    assert "tagasi võetud" in block


def test_the_rail_names_no_working_document_of_a_restricted_opinion(
    client, matter, specialist, reader, organisation
):
    _opinion(matter, specialist, organisation, upload=_pdf("Avalik.pdf"))
    submission = _restricted_opinion(matter, specialist, organisation)
    add_opinion_working_documents(submission=submission, author=specialist, uploads=[_docx()])
    client.force_login(reader)

    block = _rail_block(_teema(client, matter))

    assert "Avalik.pdf" in block
    assert DOCX_NAME not in block
    assert block.count("railcard__row--file") == 1


# ---------------------------------------------------------------------------
# F. Legacy: older opinions and the dormant column
# ---------------------------------------------------------------------------


def _carry():
    module = importlib.import_module(
        "app.documents.migrations.0014_carry_dormant_working_documents"
    )
    module.carry_dormant_working_documents(global_apps, None)


def test_the_dormant_pointer_is_carried_over_once(matter, specialist, organisation):
    submission = _opinion(matter, specialist, organisation).record
    draft_file = create_document(matter=matter, title="Mustandi fail.docx", role=DocumentRole.OTHER)
    Submission.objects.filter(pk=submission.pk).update(working_document=draft_file)

    _carry()
    _carry()

    link = DocumentLink.objects.get(submission=submission)
    assert link.document_id == draft_file.pk
    assert link.created_by is None
    # The column itself is untouched — preserved, not cleared.
    submission.refresh_from_db()
    assert submission.working_document_id == draft_file.pk


def test_the_carry_over_keeps_the_relations_rules(matter, specialist, organisation):
    elsewhere = factories.MatterFactory(owner=specialist, title="Muu teema")
    foreign = create_document(matter=elsewhere, title="Teise teema fail")
    first = _opinion(matter, specialist, organisation).record
    second = _opinion(matter, specialist, organisation, upload=_pdf("Teine.pdf")).record
    Submission.objects.filter(pk=first.pk).update(working_document=foreign)
    # A pointer at an opinion's own letter is evidence, never a working document.
    Submission.objects.filter(pk=second.pk).update(working_document=first.final_version.document)

    _carry()

    assert not DocumentLink.objects.filter(submission__in=[first, second]).exists()


def test_the_carry_over_writes_nothing_on_an_empty_column(matter, specialist, organisation):
    _opinion(matter, specialist, organisation)
    before = DocumentLink.objects.count()

    _carry()

    assert DocumentLink.objects.count() == before


def test_an_imported_dated_opinion_reads_its_day_and_addressee(
    signed_in, matter, specialist, organisation
):
    """The archive apply's shape — a DATE-precision send of an `.asice` — reads as
    any other letter: told apart in the rail, one plain file under its row."""
    from app.submissions.enums import SentAtPrecision
    from app.submissions.services import register_sent_opinion

    document = create_document(
        matter=matter, title="2019 arvamus", role=DocumentRole.KODA_SUBMISSION_FINAL
    )
    version = add_evidence_version(
        document=document,
        content=signed_container(),
        original_filename="2019_arvamus.asice",
        mime_type="application/vnd.etsi.asic-e+zip",
    )
    day = dt.date(2019, 5, 14)
    register_sent_opinion(
        document=document,
        version=version,
        title="2019 arvamus",
        actor=specialist,
        recipients=[organisation],
        sent_at=timezone.make_aware(dt.datetime.combine(day, dt.time.min)),
        sent_at_precision=SentAtPrecision.DATE,
    )

    body = _teema(signed_in, matter)

    assert f"14.5.2019 · {organisation.name}" in _rail_block(body)
    kaik = body[body.index('id="ajajoon"') :]
    assert "2019_arvamus.asice" in kaik
    assert "uxtl__filegroup" not in kaik
