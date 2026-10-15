"""Independent QA 2026-10-14 — subdocs area: the opinion (Submission) lifecycle.

Probes written against main 81a7d6e4, driven through the real HTTP views and
checked against the database and the audit trail (docs/adr/0011, 0061, 0129,
0144, 0146). `xfail(strict=True)` marks a reproduced defect; plain tests pin
behaviour the suite did not cover in this shape.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.documents.models import Document, DocumentVersion
from app.documents.services import add_evidence_version, create_document
from app.matters.services import create_matter
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.submissions.services import attach_final_evidence, create_submission
from app.workflow.enums import ActionStatus, FollowUpState
from app.workflow.models import NextAction, OpinionFollowUp, StageVocabulary
from tests.follow_ups import PDF, day, et, send_koja_arvamus

pytestmark = pytest.mark.django_db


def _matter(owner, **extra):
    return create_matter(
        title="QA arvamuse elukaar",
        actor=owner,
        owner=owner,
        stage=StageVocabulary.objects.get(key="consultation"),
        **extra,
    )


def _events(matter, event_type) -> int:
    return ChangeEvent.objects.filter(matter=matter, event_type=event_type).count()


def _draft(matter, actor, *, addressees=(), with_file=True, **kw):
    draft = create_submission(
        matter=matter, title="QA mustand", actor=actor, recipients=list(addressees), **kw
    )
    if with_file:
        attach_final_evidence(
            submission=draft,
            content=PDF + b"draft",
            original_filename="mustand.pdf",
            mime_type="application/pdf",
            actor=actor,
        )
    draft.refresh_from_db()
    return draft


def _stranded_opinion_file(matter, actor, content=PDF + b"stranded"):
    document = create_document(
        matter=matter,
        title="Saadetud kiri",
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        created_by=actor,
    )
    add_evidence_version(
        document=document,
        content=content,
        original_filename="kiri.pdf",
        mime_type="application/pdf",
        uploaded_by=actor,
    )
    document.refresh_from_db()
    return document


def _register(client, matter, document, organisation, sent_on):
    return client.post(
        reverse("submissions:register_sent", kwargs={"matter_id": matter.pk}),
        {
            "saadetud-document": str(document.pk),
            "saadetud-title": "Registreeritud arvamus",
            "saadetud-kind": "FORMAL_OPINION",
            "saadetud-recipients": [str(organisation.pk)],
            "saadetud-sent_on": et(sent_on) if sent_on else "",
        },
    )


# ---------------------------------------------------------------------------
# Märgi saadetuks — double POST, missing evidence, missing recipient
# ---------------------------------------------------------------------------


def test_mark_sent_double_post_records_one_send_and_one_follow_up(
    signed_in, specialist, organisation
):
    matter = _matter(specialist)
    draft = _draft(matter, specialist, addressees=[organisation])
    url = reverse("submissions:mark_sent", kwargs={"pk": draft.pk})

    first = signed_in.post(url, {"recipients": [str(organisation.pk)]})
    second = signed_in.post(url, {"recipients": [str(organisation.pk)]})

    assert first.status_code == 302 and second.status_code == 302
    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.SENT
    assert _events(matter, ChangeEventType.SUBMISSION_SENT) == 1
    assert OpinionFollowUp.objects.filter(submission=draft).count() == 1
    checks = NextAction.objects.filter(follow_up__submission=draft)
    assert checks.count() == 1
    assert checks.get().target_date == timezone.localdate() + dt.timedelta(days=30)


def test_mark_sent_without_final_evidence_is_refused_and_writes_nothing(
    signed_in, specialist, organisation
):
    matter = _matter(specialist)
    draft = _draft(matter, specialist, addressees=[organisation], with_file=False)

    signed_in.post(
        reverse("submissions:mark_sent", kwargs={"pk": draft.pk}),
        {"recipients": [str(organisation.pk)]},
    )

    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.DRAFT
    assert draft.sent_at is None
    assert _events(matter, ChangeEventType.SUBMISSION_SENT) == 0
    assert not OpinionFollowUp.objects.filter(submission=draft).exists()


def test_mark_sent_by_reader_is_404_and_writes_nothing(client, reader, specialist, organisation):
    matter = _matter(specialist)
    draft = _draft(matter, specialist, addressees=[organisation])
    client.force_login(reader)

    response = client.post(
        reverse("submissions:mark_sent", kwargs={"pk": draft.pk}),
        {"recipients": [str(organisation.pk)]},
    )

    assert response.status_code == 404
    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.DRAFT


# ---------------------------------------------------------------------------
# Registreeri saatmine — dates on the Tallinn boundary
# ---------------------------------------------------------------------------


def test_register_sent_tomorrow_is_refused_and_creates_nothing(signed_in, specialist, organisation):
    matter = _matter(specialist)
    document = _stranded_opinion_file(matter, specialist)

    _register(signed_in, matter, document, organisation, day(1))

    assert not Submission.objects.filter(matter=matter).exists()
    assert not OpinionFollowUp.objects.filter(submission__matter=matter).exists()


def test_register_sent_with_blank_date_is_refused(signed_in, specialist, organisation):
    matter = _matter(specialist)
    document = _stranded_opinion_file(matter, specialist)

    _register(signed_in, matter, document, organisation, None)

    assert not Submission.objects.filter(matter=matter).exists()


def test_register_sent_impossible_date_is_refused(signed_in, specialist, organisation):
    matter = _matter(specialist)
    document = _stranded_opinion_file(matter, specialist)

    signed_in.post(
        reverse("submissions:register_sent", kwargs={"matter_id": matter.pk}),
        {
            "saadetud-document": str(document.pk),
            "saadetud-title": "Registreeritud arvamus",
            "saadetud-kind": "FORMAL_OPINION",
            "saadetud-recipients": [str(organisation.pk)],
            "saadetud-sent_on": "31.2.2026",
        },
    )

    assert not Submission.objects.filter(matter=matter).exists()


def test_register_sent_backdated_schedules_one_overdue_check_from_the_stated_day(
    signed_in, specialist, organisation
):
    """ADR 0146 §1: due on the recorded sending day + 30 — already overdue on purpose."""
    matter = _matter(specialist)
    document = _stranded_opinion_file(matter, specialist)
    sent_on = day(-45)

    _register(signed_in, matter, document, organisation, sent_on)

    submission = Submission.objects.get(matter=matter)
    assert submission.status == SubmissionStatus.SENT
    assert timezone.localdate(submission.sent_at) == sent_on
    follow_up = OpinionFollowUp.objects.get(submission=submission)
    assert follow_up.sent_on == sent_on
    check = NextAction.objects.get(follow_up=follow_up)
    assert check.target_date == sent_on + dt.timedelta(days=30)
    assert check.status == ActionStatus.PLANNED


def test_register_sent_twice_for_the_same_file_is_refused(signed_in, specialist, organisation):
    matter = _matter(specialist)
    document = _stranded_opinion_file(matter, specialist)

    _register(signed_in, matter, document, organisation, day(-1))
    _register(signed_in, matter, document, organisation, day(-1))

    assert Submission.objects.filter(matter=matter).count() == 1
    assert OpinionFollowUp.objects.filter(submission__matter=matter).count() == 1


# ---------------------------------------------------------------------------
# A second opinion on the same Matter — one follow-up per sent opinion
# ---------------------------------------------------------------------------


def test_second_opinion_on_same_matter_gets_its_own_single_check(
    signed_in, specialist, organisation
):
    matter = _matter(specialist)
    first = send_koja_arvamus(matter, specialist, [organisation], day(-3))
    second = send_koja_arvamus(matter, specialist, [organisation], day(0))

    assert OpinionFollowUp.objects.filter(submission__matter=matter).count() == 2
    assert NextAction.objects.filter(follow_up__submission=first).count() == 1
    assert NextAction.objects.filter(follow_up__submission=second).count() == 1
    assert NextAction.objects.get(follow_up__submission=first).target_date == day(27)
    assert NextAction.objects.get(follow_up__submission=second).target_date == day(30)


# ---------------------------------------------------------------------------
# Withdraw — state machine and the check it ends
# ---------------------------------------------------------------------------


def test_withdraw_of_a_draft_is_refused(signed_in, specialist, organisation):
    matter = _matter(specialist)
    draft = _draft(matter, specialist, addressees=[organisation])

    signed_in.post(reverse("submissions:withdraw", kwargs={"pk": draft.pk}), {"reason": "x"})

    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.DRAFT
    assert _events(matter, ChangeEventType.SUBMISSION_WITHDRAWN) == 0


def test_withdraw_twice_writes_one_event_and_cancels_the_check(signed_in, specialist, organisation):
    matter = _matter(specialist)
    sent = send_koja_arvamus(matter, specialist, [organisation], day(-2))
    url = reverse("submissions:withdraw", kwargs={"pk": sent.pk})

    signed_in.post(url, {"reason": "Saadeti ekslikult"})
    signed_in.post(url, {"reason": "Saadeti ekslikult"})

    sent.refresh_from_db()
    assert sent.status == SubmissionStatus.WITHDRAWN
    assert _events(matter, ChangeEventType.SUBMISSION_WITHDRAWN) == 1
    follow_up = OpinionFollowUp.objects.get(submission=sent)
    assert follow_up.state == FollowUpState.CANCELLED
    assert not NextAction.objects.filter(
        follow_up=follow_up, status__in=(ActionStatus.OPEN, ActionStatus.PLANNED)
    ).exists()
    # The evidence stays: a withdrawal is a fact about the act, not the bytes.
    assert sent.final_version_id is not None
    assert DocumentVersion.objects.filter(pk=sent.final_version_id).exists()


def test_withdrawn_opinion_cannot_be_sent_again(signed_in, specialist, organisation):
    matter = _matter(specialist)
    sent = send_koja_arvamus(matter, specialist, [organisation], day(-2))
    # A reason is required since D7 (2026-10-14); this test is about the
    # re-send refusal, so the withdrawal itself must succeed.
    signed_in.post(
        reverse("submissions:withdraw", kwargs={"pk": sent.pk}), {"reason": "Saadeti ekslikult"}
    )

    signed_in.post(
        reverse("submissions:mark_sent", kwargs={"pk": sent.pk}),
        {"recipients": [str(organisation.pk)]},
    )

    sent.refresh_from_db()
    assert sent.status == SubmissionStatus.WITHDRAWN
    assert _events(matter, ChangeEventType.SUBMISSION_SENT) == 1
    assert OpinionFollowUp.objects.filter(submission=sent).count() == 1


# ---------------------------------------------------------------------------
# Final evidence is immutable through every document route
# ---------------------------------------------------------------------------


def _final_document(sent) -> Document:
    return Document.objects.get(pk=sent.final_version.document_id)


def test_no_document_route_moves_or_removes_a_sent_opinions_final_evidence(
    signed_in, specialist, organisation
):
    matter = _matter(specialist)
    sent = send_koja_arvamus(matter, specialist, [organisation], day(-2))
    document = _final_document(sent)
    version_before = sent.final_version_id
    sha_before = sent.final_version.sha256

    signed_in.post(reverse("documents:remove", kwargs={"pk": document.pk}))
    signed_in.post(
        reverse("documents:change_role", kwargs={"pk": document.pk}),
        {"role": DocumentRole.OTHER},
    )
    signed_in.post(
        reverse("documents:add_version", kwargs={"pk": document.pk}),
        {"upload": SimpleUploadedFile("uus.pdf", PDF + b"new", content_type="application/pdf")},
    )

    document.refresh_from_db()
    sent.refresh_from_db()
    assert document.removed_at is None
    assert document.versions.count() == 1
    assert document.current_version_id == version_before
    assert sent.final_version_id == version_before
    assert DocumentVersion.objects.get(pk=version_before).sha256 == sha_before
    assert _events(matter, ChangeEventType.DOCUMENT_REMOVED) == 0


def test_attach_evidence_cannot_rebind_a_sent_opinion(signed_in, specialist, organisation):
    matter = _matter(specialist)
    sent = send_koja_arvamus(matter, specialist, [organisation], day(-2))
    other = _stranded_opinion_file(matter, specialist, content=PDF + b"other")
    before = sent.final_version_id

    signed_in.post(
        reverse("submissions:attach_evidence", kwargs={"pk": sent.pk}),
        {"existing_version": str(other.current_version_id)},
    )
    signed_in.post(
        reverse("submissions:attach_evidence", kwargs={"pk": sent.pk}),
        {"upload": SimpleUploadedFile("x.pdf", PDF + b"x", content_type="application/pdf")},
    )

    sent.refresh_from_db()
    assert sent.final_version_id == before


def test_attach_evidence_from_another_matter_is_404_and_binds_nothing(
    signed_in, specialist, organisation
):
    matter = _matter(specialist)
    elsewhere = _matter(specialist)
    draft = _draft(matter, specialist, addressees=[organisation], with_file=False)
    foreign = _stranded_opinion_file(elsewhere, specialist)

    response = signed_in.post(
        reverse("submissions:attach_evidence", kwargs={"pk": draft.pk}),
        {"existing_version": str(foreign.current_version_id)},
    )

    assert response.status_code == 404
    draft.refresh_from_db()
    assert draft.final_version_id is None


# ---------------------------------------------------------------------------
# A restricted opinion's check is restricted; readers see neither
# ---------------------------------------------------------------------------


def test_check_of_an_opinion_on_a_restricted_file_inherits_the_restriction(
    signed_in, specialist, organisation
):
    """ADR 0146 §10: a check is never less restricted than its opinion."""
    matter = _matter(specialist)
    document = _stranded_opinion_file(matter, specialist)
    Document.objects.filter(pk=document.pk).update(visibility_override=Visibility.RESTRICTED)

    _register(signed_in, matter, document, organisation, day(-1))

    submission = Submission.objects.get(matter=matter)
    assert submission.visibility_override == Visibility.RESTRICTED
    check = NextAction.objects.get(follow_up__submission=submission)
    assert check.visibility_override == Visibility.RESTRICTED
