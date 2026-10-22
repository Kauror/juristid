"""Answers to a `Kaasamine`, and the source a received answer keeps (docs/adr/0167).

QA remediation 2026-10-14, Agent E.

§1 — QA-SUBDOCS-02: `Eemalda dokument` refuses the last live file of a
recorded position that has no words and no address; a correction counts only
live files; a new version of the file keeps it a source.

§2 — owner decision D8: the first answer finishes the round's wait; later
answers are recorded against the same round, with their own dates and files,
without touching the first answer or the closure; a repeated submit of the
same answer is refused; permissions and restriction are the round's.

§3 — the same feedback is searchable and readable on the round whichever form
stored it (`Lisa tagasiside` or `Lõpeta kaasamine` / `Muuda`).
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.documents.links import DocumentLink
from app.documents.services import (
    DOCUMENT_IS_LAST_POSITION_SOURCE,
    add_evidence_version,
    removal_refusal,
    remove_document,
)
from app.matters.engagement_replies import (
    REPLY_ALREADY_RECORDED,
    answerable_rounds,
    finish_wait_on_reply,
)
from app.matters.enums import EngagementKind
from app.matters.models import MatterEngagement, MatterExternalPosition
from app.matters.removal import remove_matter_record
from app.matters.services import (
    add_engagement,
    complete_engagement_feedback,
    correct_engagement,
    engagement_revision_token,
    record_external_position,
)
from app.matters.work_items import open_feedback_waits
from app.search.services import search_documents
from tests import factories
from tests.follow_ups import PDF, day, et
from tests.refusals import refused

pytestmark = pytest.mark.django_db


def _round(matter, actor, title="Liikmete küsitlus", **extra):
    return add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title=title,
        occurred_on=day(-5),
        feedback_deadline=day(5),
        actor=actor,
        **extra,
    )


def _pdf(name="vastus.pdf", body=b""):
    return SimpleUploadedFile(name, PDF + body, content_type="application/pdf")


def _reply(client, matter, round_, summary="", *, stated_on=None, files=()):
    payload = {
        "engagement": str(round_.pk),
        "summary": summary,
        "stated_on": et(stated_on) if stated_on else "",
    }
    if files:
        payload["attachments"] = list(files)
    return client.post(
        reverse("matters:add_engagement_reply", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _replies(round_):
    return list(
        MatterExternalPosition.objects.filter(engagement=round_)
        .order_by("created_at")
        .values_list("summary", flat=True)
    )


def _closed_events(round_):
    return ChangeEvent.objects.filter(
        event_type=ChangeEventType.ENGAGEMENT_FEEDBACK_CLOSED, object_id=round_.pk
    ).count()


def _detail(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _only_document(position):
    return DocumentLink.objects.get(external_position=position).document


# ---------------------------------------------------------------------------
# §1 — the last live file of a position with no words and no address stays
# ---------------------------------------------------------------------------


def test_the_only_file_of_a_file_only_reply_is_refused_with_the_way_out(
    signed_in, normal_matter, specialist
):
    round_ = _round(normal_matter, specialist)
    _reply(signed_in, normal_matter, round_, files=[_pdf()])
    position = MatterExternalPosition.objects.get(engagement=round_)
    document = _only_document(position)

    assert removal_refusal(document) == DOCUMENT_IS_LAST_POSITION_SOURCE
    with refused(DOCUMENT_IS_LAST_POSITION_SOURCE):
        remove_document(document=document, actor=specialist)

    document.refresh_from_db()
    assert document.removed_at is None
    assert not ChangeEvent.objects.filter(
        event_type=ChangeEventType.DOCUMENT_REMOVED, object_id=document.pk
    ).exists()


def test_the_document_page_states_the_refusal_and_the_route_keeps_the_file(
    signed_in, normal_matter, specialist
):
    round_ = _round(normal_matter, specialist)
    _reply(signed_in, normal_matter, round_, files=[_pdf()])
    document = _only_document(MatterExternalPosition.objects.get(engagement=round_))

    page = signed_in.get(reverse("documents:document_detail", kwargs={"pk": document.pk}))
    assert "ainus allikas" in page.content.decode()

    response = signed_in.post(reverse("documents:remove", kwargs={"pk": document.pk}), follow=True)

    assert "ainus allikas" in response.content.decode()
    document.refresh_from_db()
    assert document.removed_at is None


@pytest.mark.parametrize("source", ["summary", "url"])
def test_a_file_may_go_when_the_position_has_words_or_an_address(normal_matter, specialist, source):
    position = record_external_position(
        matter=normal_matter,
        organisation=None,
        provenance="RECEIVED",
        summary="Liige toetab." if source == "summary" else "",
        url="https://example.org/seisukoht" if source == "url" else "",
        attachment_count=1,
        actor=specialist,
    )
    document = factories.DocumentFactory(matter=normal_matter)
    add_evidence_version(
        document=document, content=PDF, original_filename="a.pdf", mime_type="application/pdf"
    )
    DocumentLink.objects.create(document=document, external_position=position)

    assert removal_refusal(document) == ""
    remove_document(document=document, actor=specialist)
    document.refresh_from_db()
    assert document.removed_at is not None


def test_with_two_files_the_first_may_go_and_the_last_may_not(signed_in, normal_matter, specialist):
    round_ = _round(normal_matter, specialist)
    _reply(signed_in, normal_matter, round_, files=[_pdf("a.pdf"), _pdf("b.pdf", b"kaks")])
    position = MatterExternalPosition.objects.get(engagement=round_)
    first, second = (
        link.document for link in DocumentLink.objects.filter(external_position=position)
    )

    remove_document(document=first, actor=specialist)

    with refused(DOCUMENT_IS_LAST_POSITION_SOURCE):
        remove_document(document=second, actor=specialist)
    assert (
        DocumentLink.objects.visible_to(specialist).filter(external_position=position).count() == 1
    )


def test_a_new_version_of_the_file_keeps_it_the_source(signed_in, normal_matter, specialist):
    """Replacement: `Lisa uus versioon` changes the current bytes, not the tie."""
    round_ = _round(normal_matter, specialist)
    _reply(signed_in, normal_matter, round_, files=[_pdf()])
    document = _only_document(MatterExternalPosition.objects.get(engagement=round_))

    add_evidence_version(
        document=document,
        content=PDF + b"parandatud",
        original_filename="vastus-v2.pdf",
        mime_type="application/pdf",
    )

    with refused(DOCUMENT_IS_LAST_POSITION_SOURCE):
        remove_document(document=document, actor=specialist)


def test_once_the_position_has_words_the_file_may_go(signed_in, normal_matter, specialist):
    """The way out the refusal names: `Muuda` on the record, then the file."""
    from app.matters.services import correct_external_position, external_position_revision

    round_ = _round(normal_matter, specialist)
    _reply(signed_in, normal_matter, round_, files=[_pdf()])
    position = MatterExternalPosition.objects.get(engagement=round_)
    document = _only_document(position)

    correct_external_position(
        position=position,
        organisation=None,
        url="",
        stated_on=None,
        stated_on_precision="EXACT",
        summary="Liige toetab, fail oli vale.",
        engagement=round_,
        actor=specialist,
        expected_revision=external_position_revision(position),
    )
    remove_document(document=document, actor=specialist)

    document.refresh_from_db()
    assert document.removed_at is not None


def test_a_removed_position_does_not_hold_its_file(signed_in, normal_matter, specialist):
    """`Kustuta` took the record off the file; its file is no longer anybody's source."""
    round_ = _round(normal_matter, specialist)
    _reply(signed_in, normal_matter, round_, files=[_pdf()])
    position = MatterExternalPosition.objects.get(engagement=round_)
    document = _only_document(position)
    remove_matter_record(
        matter_id=normal_matter.pk, kind_key="seisukoht", record_id=position.pk, actor=specialist
    )

    assert removal_refusal(document) == ""


def test_a_file_shared_with_another_record_is_held_only_by_the_position(
    signed_in, normal_matter, specialist
):
    """The refusal is about the position: the same file linked to the round as
    well changes nothing, because the round is not a source of the answer."""
    round_ = _round(normal_matter, specialist)
    _reply(signed_in, normal_matter, round_, files=[_pdf()])
    document = _only_document(MatterExternalPosition.objects.get(engagement=round_))
    DocumentLink.objects.create(document=document, engagement=round_)

    assert removal_refusal(document) == DOCUMENT_IS_LAST_POSITION_SOURCE


# ---------------------------------------------------------------------------
# §2 — the first answer finishes the wait, later answers still belong
# ---------------------------------------------------------------------------


def test_the_first_answer_finishes_the_wait_and_later_ones_keep_it_finished(
    signed_in, normal_matter, specialist
):
    round_ = _round(normal_matter, specialist)
    assert open_feedback_waits(specialist).filter(pk=round_.pk).exists()

    assert _reply(signed_in, normal_matter, round_, "Esimene", stated_on=day(-3)).status_code == 200
    round_.refresh_from_db()
    closed_at, closed_by = round_.feedback_closed_at, round_.feedback_closed_by_id
    assert closed_at is not None
    assert not open_feedback_waits(specialist).filter(pk=round_.pk).exists()

    assert _reply(signed_in, normal_matter, round_, "Teine", stated_on=day(-1)).status_code == 200
    assert _reply(signed_in, normal_matter, round_, "Kolmas", stated_on=day(0)).status_code == 200

    round_.refresh_from_db()
    assert (round_.feedback_closed_at, round_.feedback_closed_by_id) == (closed_at, closed_by)
    assert _closed_events(round_) == 1
    assert _replies(round_) == ["Esimene", "Teine", "Kolmas"]
    assert list(
        MatterExternalPosition.objects.filter(engagement=round_)
        .order_by("created_at")
        .values_list("stated_on", flat=True)
    ) == [day(-3), day(-1), day(0)]
    assert (
        ChangeEvent.objects.filter(
            matter=normal_matter, event_type=ChangeEventType.EXTERNAL_POSITION_RECORDED
        ).count()
        == 3
    )


def test_a_later_answer_carries_its_own_files_and_leaves_the_first_ones(
    signed_in, normal_matter, specialist
):
    round_ = _round(normal_matter, specialist)
    _reply(signed_in, normal_matter, round_, "Esimene", files=[_pdf("esimene.pdf")])
    _reply(
        signed_in,
        normal_matter,
        round_,
        "Teine",
        files=[_pdf("teine-a.pdf", b"a"), _pdf("teine-b.pdf", b"b")],
    )

    first, second = MatterExternalPosition.objects.filter(engagement=round_).order_by("created_at")
    assert sorted(
        DocumentLink.objects.filter(external_position=first).values_list(
            "document__current_version__original_filename", flat=True
        )
    ) == ["esimene.pdf"]
    assert sorted(
        DocumentLink.objects.filter(external_position=second).values_list(
            "document__current_version__original_filename", flat=True
        )
    ) == ["teine-a.pdf", "teine-b.pdf"]


def test_a_round_finished_by_lopeta_kaasamine_takes_a_later_answer_and_keeps_its_text(
    signed_in, normal_matter, specialist
):
    round_ = _round(normal_matter, specialist)
    complete_engagement_feedback(
        engagement=round_, actor=specialist, feedback_received="Koondvastus liikmetelt."
    )
    round_.refresh_from_db()
    closed_at = round_.feedback_closed_at

    assert _reply(signed_in, normal_matter, round_, "Hiline liige").status_code == 200

    round_.refresh_from_db()
    assert round_.feedback_received == "Koondvastus liikmetelt."
    assert round_.feedback_closed_at == closed_at
    assert _closed_events(round_) == 1
    assert _replies(round_) == ["Hiline liige"]


def test_the_same_answer_twice_is_refused_and_writes_nothing(signed_in, normal_matter, specialist):
    round_ = _round(normal_matter, specialist)
    _reply(signed_in, normal_matter, round_, "Toetame.", stated_on=day(-1), files=[_pdf()])

    again = _reply(signed_in, normal_matter, round_, "Toetame.", stated_on=day(-1), files=[_pdf()])

    assert again.status_code == 400
    assert REPLY_ALREADY_RECORDED in again.content.decode()
    assert _replies(round_) == ["Toetame."]
    assert DocumentLink.objects.filter(external_position__engagement=round_).count() == 1


def test_the_same_words_on_another_day_or_with_another_file_are_another_answer(
    signed_in, normal_matter, specialist
):
    round_ = _round(normal_matter, specialist)
    _reply(signed_in, normal_matter, round_, "Toetame.", stated_on=day(-2))

    assert (
        _reply(signed_in, normal_matter, round_, "Toetame.", stated_on=day(-1)).status_code == 200
    )
    assert (
        _reply(
            signed_in, normal_matter, round_, "Toetame.", stated_on=day(-1), files=[_pdf()]
        ).status_code
        == 200
    )
    assert _replies(round_) == ["Toetame.", "Toetame.", "Toetame."]


def test_a_removed_answer_does_not_block_recording_it_again(signed_in, normal_matter, specialist):
    round_ = _round(normal_matter, specialist)
    _reply(signed_in, normal_matter, round_, "Toetame.")
    remove_matter_record(
        matter_id=normal_matter.pk,
        kind_key="seisukoht",
        record_id=MatterExternalPosition.objects.get(engagement=round_).pk,
        actor=specialist,
    )

    assert _reply(signed_in, normal_matter, round_, "Toetame.").status_code == 200


def test_finish_wait_on_reply_reports_whether_it_finished(normal_matter, specialist):
    """The contract `Ootame tagasisidet → Tehtud` relies on (docs/adr/0167 §2)."""
    round_ = _round(normal_matter, specialist)

    assert finish_wait_on_reply(engagement=round_, actor=specialist) is True
    assert round_.feedback_closed_at is not None
    assert finish_wait_on_reply(engagement=round_, actor=specialist) is False
    assert _closed_events(round_) == 1


def test_the_reply_panel_offers_open_rounds_first_and_marks_finished_ones(
    signed_in, normal_matter, specialist
):
    finished = _round(normal_matter, specialist, "Vana küsitlus")
    complete_engagement_feedback(engagement=finished, actor=specialist)
    open_ = _round(normal_matter, specialist, "Uus küsitlus")

    body = _detail(signed_in, normal_matter)
    reply = body[body.index('id="kaasamine-tagasiside"') :]
    reply = reply[: reply.index("</form>")]

    assert reply.index(f'value="{open_.pk}"') < reply.index(f'value="{finished.pk}"')
    finished_option = reply[reply.index(f'value="{finished.pk}"') :]
    assert "lõpetatud" in finished_option[: finished_option.index("</option>")]
    # Never preselected, not even the one open round: the form also records
    # positions that answer no round, and a guessed round would end its wait
    # (docs/adr/0170 §3). Only `Tehtud` on a wait names one.
    assert f'value="{open_.pk}" selected' not in reply
    assert f'value="{finished.pk}" selected' not in reply


def test_a_round_on_a_closed_matter_takes_no_answer(signed_in, normal_matter, specialist):
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    round_ = _round(normal_matter, specialist)
    _reply(signed_in, normal_matter, round_, "Esimene")
    close_matter(
        matter=normal_matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        reason="Valmis",
        follow_ups_confirmed=True,
    )

    response = _reply(signed_in, normal_matter, round_, "Liiga hilja")

    assert response.status_code in {400, 404}
    assert _replies(round_) == ["Esimene"]


def test_a_reader_may_neither_answer_nor_be_offered_a_round(
    client, reader, normal_matter, specialist
):
    round_ = _round(normal_matter, specialist)
    complete_engagement_feedback(engagement=round_, actor=specialist)
    client.force_login(reader)

    assert _reply(client, normal_matter, round_, "Võõras").status_code == 404
    assert _replies(round_) == []


def test_a_removed_round_is_neither_offered_nor_accepted(signed_in, normal_matter, specialist):
    round_ = _round(normal_matter, specialist)
    complete_engagement_feedback(engagement=round_, actor=specialist)
    remove_matter_record(
        matter_id=normal_matter.pk, kind_key="kaasamine", record_id=round_.pk, actor=specialist
    )

    assert not answerable_rounds(specialist, normal_matter).filter(pk=round_.pk).exists()
    assert _reply(signed_in, normal_matter, round_, "Kustutatud ringile").status_code == 400
    assert _replies(round_) == []


def test_a_restricted_round_is_offered_only_to_whom_it_is_visible(
    normal_matter, specialist, other_specialist, administrator
):
    # docs/adr/0181: every internal user sees it (a colleague not on the file
    # too); the administrator without a grant does not.
    round_ = _round(normal_matter, specialist)
    complete_engagement_feedback(engagement=round_, actor=specialist)
    MatterEngagement.objects.filter(pk=round_.pk).update(visibility_override=Visibility.RESTRICTED)

    assert answerable_rounds(specialist, normal_matter).filter(pk=round_.pk).exists()
    assert answerable_rounds(other_specialist, normal_matter).filter(pk=round_.pk).exists()
    assert not answerable_rounds(administrator, normal_matter).filter(pk=round_.pk).exists()


def test_every_later_answer_to_a_restricted_round_is_restricted(
    signed_in, normal_matter, specialist
):
    round_ = _round(normal_matter, specialist)
    MatterEngagement.objects.filter(pk=round_.pk).update(visibility_override=Visibility.RESTRICTED)
    _reply(signed_in, normal_matter, round_, "Esimene", files=[_pdf()])
    _reply(signed_in, normal_matter, round_, "Teine", files=[_pdf("teine.pdf", b"2")])

    positions = MatterExternalPosition.objects.filter(engagement=round_)
    assert positions.count() == 2
    assert set(positions.values_list("visibility_override", flat=True)) == {Visibility.RESTRICTED}
    assert set(
        DocumentLink.objects.filter(external_position__engagement=round_).values_list(
            "document__visibility_override", flat=True
        )
    ) == {Visibility.RESTRICTED}


# ---------------------------------------------------------------------------
# §3 — one answer, findable and readable whichever form stored it
# ---------------------------------------------------------------------------


def test_lopeta_kaasamine_feedback_is_found_by_search(normal_matter, specialist):
    round_ = _round(normal_matter, specialist)
    complete_engagement_feedback(
        engagement=round_, actor=specialist, feedback_received="Kaelkirjakuline vastus"
    )

    hits = search_documents(query="Kaelkirjakuline", user=specialist)
    assert list(hits.values_list("source_kind", "engagement_id")) == [("ENGAGEMENT", round_.pk)]


def test_a_correction_of_the_round_feedback_moves_its_search_row(normal_matter, specialist):
    round_ = _round(normal_matter, specialist)
    complete_engagement_feedback(
        engagement=round_, actor=specialist, feedback_received="Kaelkirjakuline vastus"
    )
    round_.refresh_from_db()
    correct_engagement(
        engagement=round_,
        actor=specialist,
        feedback_received="Ninasarvikuline vastus",
        expected_revision=engagement_revision_token(round_),
    )

    assert not search_documents(query="Kaelkirjakuline", user=specialist).exists()
    assert search_documents(query="Ninasarvikuline", user=specialist).exists()


def test_a_restricted_round_feedback_is_found_by_a_reader_and_not_an_outsider(
    normal_matter, specialist, reader, administrator
):
    # docs/adr/0181: a READER finds it as the owner does; an administrator never.
    round_ = _round(normal_matter, specialist)
    complete_engagement_feedback(
        engagement=round_, actor=specialist, feedback_received="Kaelkirjakuline vastus"
    )
    MatterEngagement.objects.filter(pk=round_.pk).update(visibility_override=Visibility.RESTRICTED)

    assert search_documents(query="Kaelkirjakuline", user=specialist).exists()
    assert search_documents(query="Kaelkirjakuline", user=reader).exists()
    assert not search_documents(query="Kaelkirjakuline", user=administrator).exists()


def test_every_answer_reads_on_the_round_row_in_the_order_stated(
    signed_in, normal_matter, specialist
):
    round_ = _round(normal_matter, specialist)
    _reply(signed_in, normal_matter, round_, "Hilisem vastus", stated_on=day(-1))
    _reply(signed_in, normal_matter, round_, "Varasem vastus", stated_on=day(-3))

    body = _detail(signed_in, normal_matter)
    start = body.index(f'id="kaasamine-{round_.pk}-sisu"')
    row = body[start : body.index("</article>", start)]

    assert "Seotud seisukohti 2" in row
    assert row.index("Varasem vastus") < row.index("Hilisem vastus")
