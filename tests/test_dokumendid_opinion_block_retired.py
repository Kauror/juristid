"""The Dokumendid `Arvamused` block is retired; one way in is left (2026-09-27).

`Dokumendid` used to end in a generic `Arvamused` accordion — «Saatmise
registreerimine ja koostatavad arvamused» — with `+ Uus arvamus` and
`+ Registreeri saatmine` in it, beside a `Lae dokument` that offered `Arvamus`
as a role. That was a second way to record the Chamber's opinion next to
`Lisa teemale → Koja arvamus`, which stores the exact file and the canonical
send in one transaction. The second way is gone (docs/adr/0061, amendment of
2026-09-27). What this file holds:

1. a normal Matter's `Dokumendid` has no opinion block at all;
2. nothing on it starts a new opinion, for any reader, open or closed;
3. the generic upload cannot create an opinion nobody said was sent;
4. `Koja arvamus` still creates exactly one canonical SENT `Submission`;
5. a sent opinion is still an `Arvamus` row;
6. its send details, evidence, history and workspace listing still read;
7. an older draft or stranded upload is still finishable, and only those;
8. a closed teema and a reader see what they saw before;
9. no send is ever recorded twice.

The service-level rules each of these rests on are older and are tested where
they are decided (`tests/test_opinions_under_documents.py`,
`tests/test_closed_matter_documents.py`, `tests/test_send_requires_addressee.py`).
This file is the product surface after the retirement, stated once.
"""

from __future__ import annotations

import datetime

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.dates import format_estonian_date
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.documents.services import (
    OPINION_UPLOAD_REFUSAL,
    add_evidence_version,
    capture_evidence_on_open_matter,
    create_document,
)
from app.matters.services import close_matter
from app.submissions.enums import SentAtPrecision, SubmissionKind, SubmissionStatus
from app.submissions.models import Submission
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent,
    withdraw_submission,
)
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

#: Every word of the retired surface a reader could meet. None of it may render
#: on a Matter with nothing unfinished.
RETIRED = (
    'id="arvamuste-haldus"',
    "accordion--opinions",
    "Saatmise registreerimine ja koostatavad arvamused",
    "+ Uus arvamus",
    "+ Registreeri saatmine",
    "Loo arvamus",
    "Koostatavaid arvamusi ei ole.",
    "/arvamused/teema/",
)

#: The exceptional block's own anchor.
UNFINISHED = 'id="lopetamata-arvamused"'


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _documents(client, matter) -> str:
    response = client.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk}))
    assert response.status_code == 200
    return response.content.decode()


def _pdf(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, f"%PDF-1.4 {name}".encode(), content_type="application/pdf")


def _file(matter, *, name: str, role: str, actor) -> Document:
    """A file as it lands through the evidence pipeline, under any stored role."""
    document = create_document(matter=matter, title=name, role=role, created_by=actor)
    add_evidence_version(
        document=document,
        content=f"%PDF-1.4 {name}".encode(),
        original_filename=name,
        mime_type="application/pdf",
        uploaded_by=actor,
    )
    document.refresh_from_db()
    return document


def _sent_opinion(matter, *, actor, organisation, filename: str = "Koja_arvamus.pdf"):
    """A sent opinion as every existing one was recorded: bytes, then the send."""
    submission = create_submission(
        matter=matter, title="Koja arvamus", actor=actor, recipients=[organisation]
    )
    attach_final_evidence(
        submission=submission,
        content=f"%PDF-1.4 {filename}".encode(),
        original_filename=filename,
        mime_type="application/pdf",
        actor=actor,
    )
    mark_submission_sent(submission=submission, actor=actor, channel="EIS")
    submission.refresh_from_db()
    return submission


def _koja_arvamus(client, matter, organisation, *, filename: str = "Koja_arvamus.pdf"):
    """`Lisa teemale → Koja arvamus`, posted as the panel posts it."""
    return client.post(
        reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}),
        {
            "upload": _pdf(filename),
            "recipients": [str(organisation.pk)],
            "sent_on": format_estonian_date(timezone.localdate()),
            "summary": "Toetame eelnõu pikema üleminekuajaga.",
        },
    )


def _three_documents(matter, *, actor, organisation):
    """A normal Matter: two ordinary files and one sent opinion."""
    _file(matter, name="Eelnou.pdf", role=DocumentRole.INCOMING_AUTHORITY, actor=actor)
    _file(matter, name="Seletuskiri.pdf", role=DocumentRole.INCOMING_AUTHORITY, actor=actor)
    return _sent_opinion(matter, actor=actor, organisation=organisation)


# ---------------------------------------------------------------------------
# 1–2. No opinion block, and no way to start an opinion, on a normal Matter
# ---------------------------------------------------------------------------


def test_a_normal_matter_ends_after_its_documents(
    signed_in, specialist, organisation, evidence_root
):
    """Three documents, nothing unfinished: no opinion section at all.

    Not an empty accordion, not a collapsed heading, not a sentence saying there
    is nothing to show. The page ends at the file table and `Töödokumendid`.
    """
    matter = factories.MatterFactory(owner=specialist)
    _three_documents(matter, actor=specialist, organisation=organisation)

    body = _documents(signed_in, matter)

    for retired in RETIRED:
        assert retired not in body, retired
    assert UNFINISHED not in body
    assert "Lõpetamata arvamused" not in body
    # The last section on the page is the working documents.
    tail = body.split('id="toodokumendid"', 1)[1]
    assert "draftrow" not in tail
    assert "Registreeri saatmine" not in tail


@pytest.mark.parametrize("persona", ["specialist", "department_head", "administrator", "reader"])
def test_nobody_is_offered_a_new_opinion_on_dokumendid(
    client, request, persona, specialist, organisation, evidence_root
):
    matter = factories.MatterFactory(owner=specialist)
    _three_documents(matter, actor=specialist, organisation=organisation)
    client.force_login(request.getfixturevalue(persona))

    response = client.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk}))
    if response.status_code != 200:
        # A persona that may not open this Matter is offered nothing by
        # definition; the tab's own authorization is untouched.
        assert response.status_code in (302, 403, 404)
        return
    body = response.content.decode()

    assert "+ Uus arvamus" not in body
    assert reverse("submissions:create", kwargs={"matter_id": matter.pk}) not in body
    assert reverse("submissions:register_sent", kwargs={"matter_id": matter.pk}) not in body


def test_an_empty_matter_has_no_opinion_block_either(signed_in, specialist):
    body = _documents(signed_in, factories.MatterFactory(owner=specialist))

    assert "Sellel teemal ei ole veel dokumente." in body
    assert UNFINISHED not in body
    for retired in RETIRED:
        assert retired not in body, retired


# ---------------------------------------------------------------------------
# 3. The generic upload cannot create an unregistered opinion
# ---------------------------------------------------------------------------


def test_the_upload_menu_does_not_offer_arvamus(signed_in, specialist):
    body = _documents(signed_in, factories.MatterFactory(owner=specialist))
    panel = body[body.index('<div class="uploadpanel" id="lae-dokument"') :]
    select = panel[: panel.index("</select>")]

    assert "KODA_SUBMISSION_FINAL" not in select
    assert "Arvamus" not in select


def test_the_upload_service_refuses_the_opinion_role_and_writes_nothing(specialist, evidence_root):
    """The boundary, for a browser still holding the old menu."""
    matter = factories.MatterFactory(owner=specialist)
    events_before = ChangeEvent.objects.filter(matter=matter).count()

    with pytest.raises(DomainError) as refusal:
        capture_evidence_on_open_matter(
            matter=matter,
            title="Koja arvamus",
            role=DocumentRole.KODA_SUBMISSION_FINAL,
            content=b"%PDF-1.4 arvamus",
            original_filename="arvamus.pdf",
            mime_type="application/pdf",
            actor=specialist,
        )

    assert str(refusal.value) == OPINION_UPLOAD_REFUSAL
    assert not Document.objects.filter(matter=matter).exists()
    assert not Submission.objects.filter(matter=matter).exists()
    assert ChangeEvent.objects.filter(matter=matter).count() == events_before
    assert not any(path.is_file() for path in evidence_root.rglob("*"))


def test_a_crafted_upload_post_leaves_no_stranded_opinion(signed_in, specialist, evidence_root):
    matter = factories.MatterFactory(owner=specialist)

    response = signed_in.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}),
        {"upload": _pdf("arvamus.pdf"), "role": DocumentRole.KODA_SUBMISSION_FINAL},
        follow=True,
    )

    assert OPINION_UPLOAD_REFUSAL in response.content.decode()
    assert not Document.objects.filter(matter=matter).exists()
    assert UNFINISHED not in response.content.decode()


def test_every_other_role_still_uploads(signed_in, specialist, evidence_root):
    matter = factories.MatterFactory(owner=specialist)

    signed_in.post(
        reverse("documents:upload_evidence", kwargs={"matter_id": matter.pk}),
        {"upload": _pdf("kiri.pdf"), "role": DocumentRole.INCOMING_AUTHORITY},
    )

    assert Document.objects.get(matter=matter).role == DocumentRole.INCOMING_AUTHORITY


# ---------------------------------------------------------------------------
# 4–5, 9. `Koja arvamus` is the way in, and it records one canonical send
# ---------------------------------------------------------------------------


def test_koja_arvamus_records_exactly_one_canonical_sent_opinion(
    signed_in, specialist, organisation, evidence_root
):
    matter = factories.MatterFactory(owner=specialist)

    response = _koja_arvamus(signed_in, matter, organisation)

    assert response.status_code == 200
    submission = Submission.objects.get(matter=matter)
    assert submission.status == SubmissionStatus.SENT
    assert submission.sent_at_precision == SentAtPrecision.DATE
    assert timezone.localtime(submission.sent_at).date() == timezone.localdate()
    assert [row.organisation for row in submission.recipient_rows.all()] == [organisation]
    assert submission.summary == "Toetame eelnõu pikema üleminekuajaga."
    # The exact bytes, on one document, under the opinion role.
    document = Document.objects.get(matter=matter)
    assert document.role == DocumentRole.KODA_SUBMISSION_FINAL
    assert submission.final_version_id == document.current_version_id
    # One act, one creation, one send.
    kinds = list(
        ChangeEvent.objects.filter(matter=matter, object_id=submission.pk).values_list(
            "event_type", flat=True
        )
    )
    assert kinds.count(ChangeEventType.SUBMISSION_CREATED) == 1
    assert kinds.count(ChangeEventType.SUBMISSION_SENT) == 1


def test_what_koja_arvamus_recorded_is_an_arvamus_row_and_nothing_else(
    signed_in, specialist, organisation, evidence_root
):
    matter = factories.MatterFactory(owner=specialist)
    _koja_arvamus(signed_in, matter, organisation)

    body = _documents(signed_in, matter)

    assert "badge--opinion" in body
    assert "Koja_arvamus.pdf" in body
    assert "Saadetud" in body
    assert organisation.name in body
    # Complete, so nothing is unfinished and nothing offers to register it.
    assert UNFINISHED not in body
    assert "Registreeri saatmine" not in body


def test_several_opinions_on_one_matter_are_several_sends(
    signed_in, specialist, organisation, evidence_root
):
    matter = factories.MatterFactory(owner=specialist)
    _koja_arvamus(signed_in, matter, organisation, filename="Esimene.pdf")
    _koja_arvamus(signed_in, matter, organisation, filename="Teine.pdf")

    assert Submission.objects.filter(matter=matter, status=SubmissionStatus.SENT).count() == 2
    assert _documents(signed_in, matter).count('class="badge badge--opinion"') == 2


# ---------------------------------------------------------------------------
# 5–6. An existing sent opinion still reads everywhere it did
# ---------------------------------------------------------------------------


def test_an_existing_sent_opinion_keeps_its_row_details_and_history(
    signed_in, specialist, organisation, evidence_root
):
    matter = factories.MatterFactory(owner=specialist)
    submission = _sent_opinion(matter, actor=specialist, organisation=organisation)
    document = submission.final_version.document

    body = _documents(signed_in, matter)

    # The row: badge, send line, and the `⋯` with the send's own details.
    assert f'id="dokument-{document.pk}"' in body
    assert "badge--opinion" in body
    assert "Saatmise andmed" in body
    assert "EIS" in body
    assert reverse("submissions:metadata", kwargs={"pk": submission.pk}) in body
    assert reverse("submissions:withdraw", kwargs={"pk": submission.pk}) in body
    # The evidence page and the bytes.
    assert (
        signed_in.get(reverse("documents:document_detail", kwargs={"pk": document.pk})).status_code
        == 200
    )
    download = signed_in.get(
        reverse("documents:download", kwargs={"pk": submission.final_version_id})
    )
    assert download.status_code == 200
    # The workspace across teemad, and the Matter's own change log.
    assert "Koja arvamus" in signed_in.get(reverse("submissions:sent")).content.decode()
    log = signed_in.get(reverse("matters:matter_changes", kwargs={"pk": matter.pk}))
    assert log.status_code == 200


def test_a_withdrawn_opinion_stays_an_arvamus_row_and_is_not_unfinished(
    signed_in, specialist, organisation, evidence_root
):
    matter = factories.MatterFactory(owner=specialist)
    submission = _sent_opinion(matter, actor=specialist, organisation=organisation)
    withdraw_submission(submission=submission, actor=specialist)

    body = _documents(signed_in, matter)

    assert "badge--opinion" in body
    assert UNFINISHED not in body
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.WITHDRAWN


# ---------------------------------------------------------------------------
# 7. Older unfinished records stay finishable — and only they appear
# ---------------------------------------------------------------------------


def test_an_older_draft_without_a_file_is_listed_with_its_one_step(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    draft = create_submission(matter=matter, title="Pooleli arvamus", actor=specialist)

    body = _documents(signed_in, matter)

    assert UNFINISHED in body
    assert "Lõpetamata arvamused" in body
    assert "1 koostamisel" in body
    assert "Pooleli arvamus" in body
    assert reverse("submissions:attach_evidence", kwargs={"pk": draft.pk}) in body
    assert "Lisa teemale → Koja arvamus" in body
    assert "+ Uus arvamus" not in body


def test_an_older_draft_can_still_be_finished(signed_in, specialist, organisation, evidence_root):
    matter = factories.MatterFactory(owner=specialist)
    draft = create_submission(
        matter=matter, title="Pooleli arvamus", actor=specialist, recipients=[organisation]
    )

    signed_in.post(
        reverse("submissions:attach_evidence", kwargs={"pk": draft.pk}),
        {"upload": _pdf("pooleli.pdf")},
    )
    body = _documents(signed_in, matter)
    assert reverse("submissions:mark_sent", kwargs={"pk": draft.pk}) in body

    signed_in.post(
        reverse("submissions:mark_sent", kwargs={"pk": draft.pk}),
        {"recipients": [str(organisation.pk)], "channel": "EIS"},
    )

    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.SENT
    assert Submission.objects.filter(matter=matter).count() == 1
    body = _documents(signed_in, matter)
    assert UNFINISHED not in body
    assert "badge--opinion" in body


def test_a_stranded_upload_can_be_registered_once_and_then_is_complete(
    signed_in, specialist, organisation, evidence_root
):
    """An `Arvamus` filed through `Lae dokument` before the role left the menu."""
    matter = factories.MatterFactory(owner=specialist)
    stranded = _file(
        matter, name="Vana_arvamus.pdf", role=DocumentRole.KODA_SUBMISSION_FINAL, actor=specialist
    )

    body = _documents(signed_in, matter)
    assert UNFINISHED in body
    assert "1 registreerimata" in body
    assert "Registreeri saatmine" in body
    assert "+ Registreeri saatmine" not in body

    payload = {
        "saadetud-document": str(stranded.pk),
        "saadetud-title": "Vana arvamus",
        "saadetud-kind": SubmissionKind.FORMAL_OPINION,
        "saadetud-recipients": [str(organisation.pk)],
        "saadetud-sent_on": "01.06.2026",
    }
    route = reverse("submissions:register_sent", kwargs={"matter_id": matter.pk})
    signed_in.post(route, payload)

    submission = Submission.objects.get(matter=matter)
    assert submission.status == SubmissionStatus.SENT
    assert submission.final_version_id == stranded.current_version_id
    assert timezone.localtime(submission.sent_at).date() == datetime.date(2026, 6, 1)
    assert UNFINISHED not in _documents(signed_in, matter)

    # A second post of the same bytes records nothing.
    signed_in.post(route, payload)
    assert Submission.objects.filter(matter=matter).count() == 1


def test_only_stranded_uploads_are_offered_for_registration(
    signed_in, specialist, organisation, evidence_root
):
    """Sent, withdrawn and a draft's evidence are all accounted for."""
    matter = factories.MatterFactory(owner=specialist)
    sent = _sent_opinion(
        matter, actor=specialist, organisation=organisation, filename="Saadetud.pdf"
    )
    withdrawn = _sent_opinion(
        matter, actor=specialist, organisation=organisation, filename="Tagasi.pdf"
    )
    withdraw_submission(submission=withdrawn, actor=specialist)
    stranded = _file(
        matter, name="Uksik.pdf", role=DocumentRole.KODA_SUBMISSION_FINAL, actor=specialist
    )

    response = signed_in.get(reverse("matters:matter_documents", kwargs={"pk": matter.pk}))
    offered = [document.pk for document in response.context["unregistered_opinions"]]

    assert offered == [stranded.pk]
    assert sent.final_version.document_id not in offered


# ---------------------------------------------------------------------------
# 8. A closed teema and a reader are unchanged
# ---------------------------------------------------------------------------


def test_on_a_closed_teema_a_draft_is_read_only_and_a_stranded_upload_adds_nothing(
    signed_in, specialist, evidence_root
):
    matter = factories.MatterFactory(owner=specialist)
    draft = create_submission(matter=matter, title="Pooleli arvamus", actor=specialist)
    _file(
        matter, name="Vana_arvamus.pdf", role=DocumentRole.KODA_SUBMISSION_FINAL, actor=specialist
    )
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="QA")

    body = _documents(signed_in, matter)

    assert "Pooleli arvamus" in body
    assert "ootab faili" in body
    assert reverse("submissions:attach_evidence", kwargs={"pk": draft.pk}) not in body
    assert reverse("submissions:register_sent", kwargs={"matter_id": matter.pk}) not in body
    assert "registreerimata" not in body


def test_a_closed_teema_with_only_a_stranded_upload_has_no_block(
    signed_in, specialist, evidence_root
):
    matter = factories.MatterFactory(owner=specialist)
    _file(
        matter, name="Vana_arvamus.pdf", role=DocumentRole.KODA_SUBMISSION_FINAL, actor=specialist
    )
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist, reason="QA")

    body = _documents(signed_in, matter)

    assert UNFINISHED not in body
    assert "badge--opinion" in body


def test_a_reader_sees_an_older_draft_and_is_offered_no_step(
    client, reader, specialist, evidence_root
):
    matter = factories.MatterFactory(owner=specialist)
    draft = create_submission(matter=matter, title="Pooleli arvamus", actor=specialist)
    _file(
        matter, name="Vana_arvamus.pdf", role=DocumentRole.KODA_SUBMISSION_FINAL, actor=specialist
    )
    client.force_login(reader)

    body = _documents(client, matter)

    assert "Pooleli arvamus" in body
    assert reverse("submissions:attach_evidence", kwargs={"pk": draft.pk}) not in body
    assert reverse("submissions:metadata", kwargs={"pk": draft.pk}) not in body
    assert reverse("submissions:register_sent", kwargs={"matter_id": matter.pk}) not in body


def test_a_reader_still_cannot_record_an_opinion_by_posting(
    client, reader, specialist, organisation, evidence_root
):
    matter = factories.MatterFactory(owner=specialist)
    client.force_login(reader)

    response = _koja_arvamus(client, matter, organisation)

    assert response.status_code == 404
    assert not Submission.objects.filter(matter=matter).exists()
    assert not Document.objects.filter(matter=matter).exists()
