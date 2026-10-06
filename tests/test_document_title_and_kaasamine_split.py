"""Editable document titles, and `+ Kaasamine` as start then feedback (docs/adr/0142).

DOCUMENTS
* the display title changes through `rename_document`, audited, and nothing
  about the evidence moves — filename, bytes, checksum, MIME type, versions;
* Dokumendid and the document page show the new title, and search finds it.

KAASAMINE
* A — no open round: `+ Kaasamine` opens on `Alusta kaasamist`;
* B — an open round: it opens on `Lisa tagasiside`;
* C — both modes are always drawn and chosen by hand;
* D — the start form asks only start questions and writes the round;
* E — the feedback form asks only feedback questions, attaches a received-
  feedback record and its files to the named round;
* F — only completed rounds: back to `Alusta kaasamist`;
* G — several open rounds: nothing preselected, a choice is required;
* H — the lifecycle is unchanged: feedback closes nothing.
"""

from __future__ import annotations

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.documents.enums import DocumentRole
from app.documents.links import DocumentLink
from app.documents.models import Document, DocumentVersion
from app.documents.services import (
    DOCUMENT_ALREADY_REMOVED,
    DOCUMENT_TITLE_REQUIRED,
    add_evidence_version,
    remove_document,
    rename_document,
)
from app.matters.enums import EngagementKind, ExternalPositionProvenance
from app.matters.forms import ENGAGEMENT_FEEDBACK_NEEDS_ROUND
from app.matters.locks import CLOSED_MATTER_REFUSAL
from app.matters.models import MatterEngagement, MatterExternalPosition
from app.matters.services import add_engagement, close_matter, complete_engagement_feedback
from app.search.services import result_count
from app.workflow.enums import Disposition
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db


def _document(matter, *, title: str = "skaneeritud_0412_final_v3.pdf") -> Document:
    document = factories.DocumentFactory(
        matter=matter, role=DocumentRole.MEMBER_FEEDBACK, title=title
    )
    add_evidence_version(
        document=document,
        content=b"%PDF-1.4\nsunteetiline sisu",
        original_filename="skaneeritud_0412_final_v3.pdf",
        mime_type="application/pdf",
    )
    document.refresh_from_db()
    return document


def _version_facts(document) -> list[tuple]:
    return list(
        DocumentVersion.objects.filter(document=document)
        .order_by("version_number")
        .values_list("pk", "original_filename", "sha256", "mime_type", "storage_key", "size_bytes")
    )


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _round(matter, actor, title: str) -> MatterEngagement:
    return add_engagement(
        matter=matter,
        kind=EngagementKind.OTHER,
        title=title,
        occurred_on=timezone.localdate(),
        actor=actor,
    )


def _checked(body: str, radio_id: str) -> bool:
    tag = re.search(rf'<input[^>]*id="{radio_id}"[^>]*>', body)
    assert tag, radio_id
    return "checked" in tag.group(0)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def test_the_title_changes_and_the_evidence_does_not(normal_matter, specialist):
    document = _document(normal_matter)
    before = _version_facts(document)

    renamed = rename_document(
        document=document, title="  Liidu   vastus eelnõule  ", actor=specialist
    )

    assert renamed.title == "Liidu vastus eelnõule"
    document.refresh_from_db()
    assert document.title == "Liidu vastus eelnõule"
    assert document.current_version.original_filename == "skaneeritud_0412_final_v3.pdf"
    assert _version_facts(document) == before
    assert DocumentVersion.objects.filter(document=document).count() == 1


def test_a_rename_is_audited_with_both_titles(normal_matter, specialist):
    document = _document(normal_matter)

    rename_document(document=document, title="Liidu vastus", actor=specialist)
    rename_document(document=document, title="Liidu vastus", actor=specialist)

    (event,) = ChangeEvent.objects.filter(event_type=ChangeEventType.DOCUMENT_TITLE_CHANGED)
    assert event.object_id == document.pk
    assert event.actor == specialist
    assert event.matter_id == normal_matter.pk
    assert event.payload == {"from": "skaneeritud_0412_final_v3.pdf", "to": "Liidu vastus"}


def test_an_empty_title_and_a_removed_document_are_refused(normal_matter, specialist):
    document = _document(normal_matter)

    with refused(DOCUMENT_TITLE_REQUIRED):
        rename_document(document=document, title="   ", actor=specialist)
    remove_document(document=document, actor=specialist)
    with refused(DOCUMENT_ALREADY_REMOVED):
        rename_document(document=document, title="Uus", actor=specialist)


def test_a_closed_matter_refuses_a_rename(normal_matter, specialist):
    document = _document(normal_matter)
    close_matter(matter=normal_matter, disposition=Disposition.COMPLETED, actor=specialist)

    with refused(CLOSED_MATTER_REFUSAL):
        rename_document(document=document, title="Uus", actor=specialist)
    document.refresh_from_db()
    assert document.title == "skaneeritud_0412_final_v3.pdf"


def test_dokumendid_offers_muuda_and_shows_the_new_title(signed_in, normal_matter):
    document = _document(normal_matter)
    documents_url = reverse("matters:matter_documents", kwargs={"pk": normal_matter.pk})

    before = signed_in.get(documents_url).content.decode()
    assert f'action="{reverse("documents:rename", kwargs={"pk": document.pk})}"' in before
    assert "Muuda" in before

    response = signed_in.post(
        reverse("documents:rename", kwargs={"pk": document.pk}),
        {"title": "Liidu vastus eelnõule", "tagasi": "dokumendid"},
    )

    assert response.status_code == 302
    assert response["Location"] == documents_url
    after = signed_in.get(documents_url).content.decode()
    assert "Liidu vastus eelnõule" in after
    # The filename still reads under the title, as the list already showed it.
    assert "skaneeritud_0412_final_v3.pdf" in after
    detail = signed_in.get(
        reverse("documents:document_detail", kwargs={"pk": document.pk})
    ).content.decode()
    assert "Liidu vastus eelnõule" in detail


def test_search_finds_the_new_title(normal_matter, specialist):
    document = _document(normal_matter, title="Vana pealkiri")

    rename_document(document=document, title="Sünteetiline kõrvalnõude vastus", actor=specialist)

    assert result_count(query="kõrvalnõude", user=specialist) >= 1


# ---------------------------------------------------------------------------
# Kaasamine — the default, and both modes
# ---------------------------------------------------------------------------


def test_a_no_open_round_opens_on_alusta_kaasamist(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)

    assert _checked(body, "kaasamine-alusta-valik")
    assert not _checked(body, "kaasamine-tagasiside-valik")


def test_b_an_open_round_opens_on_lisa_tagasiside(signed_in, normal_matter, specialist):
    _round(normal_matter, specialist, "Liikmete küsitlus")

    body = _detail(signed_in, normal_matter)

    assert _checked(body, "kaasamine-tagasiside-valik")
    assert not _checked(body, "kaasamine-alusta-valik")


def test_c_both_modes_are_always_offered(signed_in, normal_matter, specialist):
    for _ in range(2):
        body = _detail(signed_in, normal_matter)
        assert 'for="kaasamine-alusta-valik">Alusta kaasamist</label>' in body
        assert 'for="kaasamine-tagasiside-valik">Lisa tagasiside</label>' in body
        assert 'name="kaasamise-liik"' in body
        _round(normal_matter, specialist, "Liikmete küsitlus")


def test_d_the_start_form_asks_only_start_questions_and_writes_the_round(signed_in, normal_matter):
    body = _detail(signed_in, normal_matter)
    begin = body.index('id="kaasamine-alusta"')
    start = body[begin : body.index('id="kaasamine-tagasiside-valik"', begin)]

    for name in (
        "audience",
        "occurred_on",
        "feedback_deadline",
        "smaily_url",
        "alchemer_url",
        "engagement_note",
    ):
        assert f'name="{name}"' in start, name
    for name in ("feedback_received", "response_count", "attachments"):
        assert f'name="{name}"' not in start, name
    assert "Saadud tagasiside" not in start

    response = signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": normal_matter.pk}),
        {
            "audience": "Liikmed (sünteetiline)",
            "occurred_on": "1.10.2026",
            # Not asked any more; a stale post has nowhere to put them.
            "feedback_received": "Ei tohi salvestuda",
            "response_count": "7",
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    (round_,) = MatterEngagement.objects.filter(matter=normal_matter)
    assert round_.title == "Liikmed (sünteetiline)"
    assert round_.has_open_feedback_wait
    assert round_.feedback_received == ""
    assert round_.response_count is None


def test_e_feedback_attaches_to_the_named_round_with_its_files(
    signed_in, normal_matter, specialist
):
    round_ = _round(normal_matter, specialist, "Liikmete küsitlus")
    body = _detail(signed_in, normal_matter)
    reply = body[body.index('id="kaasamine-tagasiside"') :]
    reply = reply[: reply.index("</form>")]

    for name in ("engagement", "summary", "stated_on", "attachments", "lawyer_note"):
        assert f'name="{name}"' in reply, name
    for name in ("audience", "feedback_deadline", "smaily_url", "organisation"):
        assert f'name="{name}"' not in reply, name
    # One open round: preselected.
    assert re.search(rf'<option value="{round_.pk}"[^>]*selected', reply)

    response = signed_in.post(
        reverse("matters:add_engagement_reply", kwargs={"pk": normal_matter.pk}),
        {
            "engagement": str(round_.pk),
            "summary": "Liikmed toetavad, kaubandus soovib pikemat üleminekut.",
            "stated_on": "3.10.2026",
            "lawyer_note": "Kokkuvõte telefonist.",
            "attachments": SimpleUploadedFile(
                "vastused.pdf", b"%PDF-1.4\nvastused", content_type="application/pdf"
            ),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert MatterEngagement.objects.filter(matter=normal_matter).count() == 1
    (position,) = MatterExternalPosition.objects.filter(matter=normal_matter)
    assert position.engagement_id == round_.pk
    assert position.provenance == ExternalPositionProvenance.RECEIVED
    assert position.summary.startswith("Liikmed toetavad")
    assert position.lawyer_note == "Kokkuvõte telefonist."
    assert position.stated_on.isoformat() == "2026-10-03"
    link = DocumentLink.objects.get(external_position=position)
    assert link.document.matter_id == normal_matter.pk
    # H. The round is still open: feedback is not `Lõpeta kaasamine`.
    round_.refresh_from_db()
    assert round_.has_open_feedback_wait
    assert round_.feedback_closed_at is None


def test_f_only_completed_rounds_opens_on_alusta(signed_in, normal_matter, specialist):
    round_ = _round(normal_matter, specialist, "Möödunud küsitlus")
    complete_engagement_feedback(engagement=round_, actor=specialist)

    body = _detail(signed_in, normal_matter)

    assert _checked(body, "kaasamine-alusta-valik")
    reply = body[body.index('id="kaasamine-tagasiside"') :]
    assert "Avatud kaasamist ei ole" in reply[: reply.index("</div>")]


def test_g_several_open_rounds_require_a_choice(signed_in, normal_matter, specialist):
    first = _round(normal_matter, specialist, "Esimene küsitlus")
    second = _round(normal_matter, specialist, "Teine küsitlus")

    body = _detail(signed_in, normal_matter)
    reply = body[body.index('id="kaasamine-tagasiside"') :]
    reply = reply[: reply.index("</form>")]
    assert f'value="{first.pk}"' in reply and f'value="{second.pk}"' in reply
    assert not re.search(r"<option value=\"[0-9a-f-]{36}\"[^>]*selected", reply)

    refusal = signed_in.post(
        reverse("matters:add_engagement_reply", kwargs={"pk": normal_matter.pk}),
        {"summary": "Kelle kohta?"},
        headers={"HX-Request": "true"},
    )

    assert refusal.status_code == 400
    assert ENGAGEMENT_FEEDBACK_NEEDS_ROUND in refusal.content.decode()
    assert not MatterExternalPosition.objects.filter(matter=normal_matter).exists()
    # The refusal reopens `Lisa tagasiside`, not the start form.
    assert _checked(refusal.content.decode(), "kaasamine-tagasiside-valik")


def test_g_a_completed_or_foreign_round_is_not_accepted(signed_in, normal_matter, specialist):
    _round(normal_matter, specialist, "Avatud küsitlus")
    closed = _round(normal_matter, specialist, "Lõpetatud küsitlus")
    complete_engagement_feedback(engagement=closed, actor=specialist)
    foreign = _round(factories.MatterFactory(owner=specialist), specialist, "Teise teema küsitlus")

    for engagement in (closed, foreign):
        response = signed_in.post(
            reverse("matters:add_engagement_reply", kwargs={"pk": normal_matter.pk}),
            {"engagement": str(engagement.pk), "summary": "Vale voor"},
            headers={"HX-Request": "true"},
        )
        assert response.status_code == 400
    assert not MatterExternalPosition.objects.exists()


def test_h_lopeta_kaasamine_is_unchanged(signed_in, normal_matter, specialist):
    round_ = _round(normal_matter, specialist, "Liikmete küsitlus")

    body = _detail(signed_in, normal_matter)

    assert (
        reverse(
            "matters:complete_engagement_feedback",
            kwargs={"pk": normal_matter.pk, "engagement_id": round_.pk},
        )
        in body
    )
