"""Independent QA 2026-10-14 — subdocs area: Kaasamine (ADR 0027, 0086, 0132, 0142).

`+ Kaasamine` → `Lisa tagasiside` → the round's state, through the real views,
against the database and the work surfaces. Probes against main 81a7d6e4.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.enums import EngagementKind
from app.matters.models import MatterEngagement, MatterExternalPosition
from app.matters.services import add_engagement, create_matter
from app.matters.work_items import open_feedback_waits
from app.workflow.models import StageVocabulary
from tests.follow_ups import PDF, day, et

pytestmark = pytest.mark.django_db


def _matter(owner, **extra):
    return create_matter(
        title="QA kaasamine",
        actor=owner,
        owner=owner,
        stage=StageVocabulary.objects.get(key="consultation"),
        **extra,
    )


def _round(matter, actor, title="liikmed", deadline=None):
    return add_engagement(
        matter=matter,
        kind=EngagementKind.OTHER,
        title=title,
        occurred_on=day(-2),
        feedback_deadline=deadline,
        actor=actor,
    )


def _reply(client, matter, data, files=None):
    payload = {"stated_on": "", **data}
    if files:
        payload["attachments"] = files
    return client.post(
        reverse("matters:add_engagement_reply", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _start(client, matter, **data):
    payload = {
        "audience": "Liikmed",
        "occurred_on": et(day(0)),
        "feedback_deadline": "",
        **data,
    }
    return client.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_start_round_without_deadline_is_an_open_wait(signed_in, specialist):
    """ADR 0132: open until finished; the deadline is optional."""
    matter = _matter(specialist)

    response = _start(signed_in, matter)

    assert response.status_code == 200
    engagement = MatterEngagement.objects.get(matter=matter)
    assert engagement.feedback_deadline is None
    assert engagement.occurred_on == day(0)
    assert engagement.lifecycle_tracked is True
    assert engagement.feedback_closed_at is None
    assert open_feedback_waits(specialist).filter(pk=engagement.pk).exists()


def test_start_round_deadline_before_its_own_day_is_refused(signed_in, specialist):
    matter = _matter(specialist)

    _start(signed_in, matter, occurred_on=et(day(0)), feedback_deadline=et(day(-3)))

    assert not MatterEngagement.objects.filter(matter=matter).exists()


def test_start_round_with_a_javascript_link_is_refused(signed_in, specialist):
    matter = _matter(specialist)

    _start(signed_in, matter, smaily_url="javascript:alert(1)")

    assert not MatterEngagement.objects.filter(matter=matter).exists()


def test_reader_cannot_start_a_round(client, reader, specialist):
    matter = _matter(specialist)
    client.force_login(reader)

    assert _start(client, matter).status_code == 404
    assert not MatterEngagement.objects.filter(matter=matter).exists()


# ---------------------------------------------------------------------------
# Lisa tagasiside
# ---------------------------------------------------------------------------


def test_reply_records_feedback_and_closes_exactly_that_round(signed_in, specialist):
    matter = _matter(specialist)
    answered = _round(matter, specialist, "liikmed", deadline=day(5))
    other = _round(matter, specialist, "partnerid", deadline=day(5))

    _reply(
        signed_in,
        matter,
        {"engagement": str(answered.pk), "summary": "Liikmed toetavad."},
        files=[SimpleUploadedFile("vastus.pdf", PDF, content_type="application/pdf")],
    )

    position = MatterExternalPosition.objects.get(matter=matter)
    assert position.engagement_id == answered.pk
    assert position.summary == "Liikmed toetavad."
    assert position.lawyer_note == ""
    answered.refresh_from_db()
    other.refresh_from_db()
    assert answered.feedback_closed_at is not None
    assert other.feedback_closed_at is None
    assert (
        ChangeEvent.objects.filter(
            matter=matter, event_type=ChangeEventType.EXTERNAL_POSITION_RECORDED
        ).count()
        == 1
    )


def test_reply_without_a_round_is_refused_and_writes_nothing(signed_in, specialist):
    matter = _matter(specialist)
    _round(matter, specialist, "liikmed")
    _round(matter, specialist, "partnerid")

    _reply(signed_in, matter, {"summary": "Kellele see oli?"})

    assert not MatterExternalPosition.objects.filter(matter=matter).exists()
    assert (
        MatterEngagement.objects.filter(matter=matter, feedback_closed_at__isnull=True).count() == 2
    )


def test_reply_naming_a_round_on_another_matter_is_refused(signed_in, specialist):
    matter = _matter(specialist)
    elsewhere = _matter(specialist)
    foreign = _round(elsewhere, specialist, "võõras")
    _round(matter, specialist, "oma")

    _reply(signed_in, matter, {"engagement": str(foreign.pk), "summary": "x"})

    assert not MatterExternalPosition.objects.exists()
    foreign.refresh_from_db()
    assert foreign.feedback_closed_at is None


def test_a_second_reply_to_a_finished_round_is_recorded_and_keeps_the_first(signed_in, specialist):
    """Inverted 2026-10-14 (owner decision D8, docs/adr/0167 §2): it pinned the
    refusal of every answer after the first. A later answer is now recorded
    against the same round; the first answer and the round's closure stay."""
    matter = _matter(specialist)
    round_ = _round(matter, specialist, "liikmed")
    _reply(signed_in, matter, {"engagement": str(round_.pk), "summary": "Esimene vastus"})
    round_.refresh_from_db()
    closed_at = round_.feedback_closed_at

    _reply(signed_in, matter, {"engagement": str(round_.pk), "summary": "Teine vastus"})

    assert list(
        MatterExternalPosition.objects.filter(matter=matter)
        .order_by("created_at")
        .values_list("summary", flat=True)
    ) == ["Esimene vastus", "Teine vastus"]
    round_.refresh_from_db()
    assert round_.feedback_closed_at == closed_at


def test_reply_with_an_unacceptable_file_unwinds_everything(signed_in, specialist):
    matter = _matter(specialist)
    round_ = _round(matter, specialist, "liikmed")

    _reply(
        signed_in,
        matter,
        {"engagement": str(round_.pk), "summary": "Fail on vale"},
        files=[SimpleUploadedFile("vastus.pdf", b"MZ not pdf", content_type="application/pdf")],
    )

    assert not MatterExternalPosition.objects.filter(matter=matter).exists()
    round_.refresh_from_db()
    assert round_.feedback_closed_at is None


def test_reply_on_a_closed_matter_is_refused(signed_in, specialist):
    from app.matters.services import close_matter
    from app.workflow.enums import Disposition

    matter = _matter(specialist)
    round_ = _round(matter, specialist, "liikmed")
    close_matter(
        matter=matter,
        disposition=Disposition.COMPLETED,
        actor=specialist,
        reason="QA",
        follow_ups_confirmed=True,
    )

    _reply(signed_in, matter, {"engagement": str(round_.pk), "summary": "Hilja"})

    assert not MatterExternalPosition.objects.filter(matter=matter).exists()


def test_reader_cannot_reply(client, reader, specialist):
    matter = _matter(specialist)
    round_ = _round(matter, specialist)
    client.force_login(reader)

    response = _reply(client, matter, {"engagement": str(round_.pk), "summary": "x"})

    assert response.status_code == 404
    assert not MatterExternalPosition.objects.exists()


def test_a_later_answer_to_a_finished_round_is_tied_to_that_round(signed_in, specialist):
    """DC-02, resolved 2026-10-14 (owner decision D8, docs/adr/0167 §2). It pinned
    that a second member's answer could not be tied to the consultation once the
    first `Lisa tagasiside` had closed it. `Lisa tagasiside` now offers the
    finished round and ties the later answer to it. A position that names no
    round — what `+ Arvamus / tagasiside` filed until docs/adr/0170 merged it
    into this one form — is still a loose record beside it, never tied by
    guess (docs/adr/0170 §3)."""
    matter = _matter(specialist)
    round_ = _round(matter, specialist, "liikmed")
    _reply(signed_in, matter, {"engagement": str(round_.pk), "summary": "Esimene liige"})

    loose = _reply(signed_in, matter, {"provenance": "RECEIVED", "summary": "Lahtine vastus"})
    _reply(signed_in, matter, {"engagement": str(round_.pk), "summary": "Teine liige"})

    assert loose.status_code == 200
    assert MatterExternalPosition.objects.get(summary="Lahtine vastus").engagement_id is None
    tied = MatterExternalPosition.objects.filter(matter=matter, engagement=round_)
    assert list(tied.order_by("created_at").values_list("summary", flat=True)) == [
        "Esimene liige",
        "Teine liige",
    ]


# ---------------------------------------------------------------------------
# A received answer whose only source is its file, and `Eemalda dokument`
# ---------------------------------------------------------------------------


def _file_only_reply(client, matter, round_):
    _reply(
        client,
        matter,
        {"engagement": str(round_.pk), "summary": ""},
        files=[SimpleUploadedFile("liikme-vastus.pdf", PDF, content_type="application/pdf")],
    )
    return MatterExternalPosition.objects.get(matter=matter)


def test_a_file_only_reply_is_accepted_and_its_file_is_its_source(signed_in, specialist):
    from app.documents.links import DocumentLink

    matter = _matter(specialist)
    position = _file_only_reply(signed_in, matter, _round(matter, specialist))

    assert position.summary == "" and position.url == ""
    assert (
        DocumentLink.objects.visible_to(specialist).filter(external_position=position).count() == 1
    )


def test_removing_the_only_file_of_a_received_answer_leaves_it_with_a_source(signed_in, specialist):
    from app.documents.links import DocumentLink

    matter = _matter(specialist)
    position = _file_only_reply(signed_in, matter, _round(matter, specialist))
    document_id = DocumentLink.objects.get(external_position=position).document_id

    signed_in.post(reverse("documents:remove", kwargs={"pk": document_id}))

    position.refresh_from_db()
    visible_files = (
        DocumentLink.objects.visible_to(specialist).filter(external_position=position).count()
    )
    # Either the removal was refused, or the record is not left holding nothing.
    assert position.summary or position.url or visible_files >= 1


def test_a_correction_counts_only_live_files_as_a_source(signed_in, specialist):
    """Inverted 2026-10-14 (QA-SUBDOCS-02 fixed, docs/adr/0167 §1). It pinned the
    mechanism: `correct_external_position` counted `document_links` without the
    removal filter, so an empty record kept saving. A position with words and a
    file loses the file (allowed: the words are a source); a correction that
    then empties the words is refused, because the removed file is no longer
    anything a reader can open."""
    from app.documents.links import DocumentLink
    from app.matters.services import (
        EXTERNAL_POSITION_NEEDS_SOURCE,
        correct_external_position,
        external_position_revision,
    )
    from tests.refusals import refused

    matter = _matter(specialist)
    _reply(
        signed_in,
        matter,
        {"engagement": str(_round(matter, specialist).pk), "summary": "Liige toetab."},
        files=[SimpleUploadedFile("liikme-vastus.pdf", PDF, content_type="application/pdf")],
    )
    position = MatterExternalPosition.objects.get(matter=matter)
    document_id = DocumentLink.objects.get(external_position=position).document_id
    signed_in.post(reverse("documents:remove", kwargs={"pk": document_id}))
    position.refresh_from_db()
    assert (
        DocumentLink.objects.visible_to(specialist).filter(external_position=position).count() == 0
    )

    with refused(EXTERNAL_POSITION_NEEDS_SOURCE):
        correct_external_position(
            position=position,
            organisation=None,
            url="",
            stated_on=None,
            stated_on_precision="EXACT",
            summary="",
            engagement=position.engagement,
            actor=specialist,
            expected_revision=external_position_revision(position),
        )

    position.refresh_from_db()
    assert position.summary == "Liige toetab."
