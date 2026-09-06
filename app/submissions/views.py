"""Submission actions.

All of these are full-page posts that return to the Matter's Dokumendid page,
which is where a Matter's opinions live since the separate per-Matter Arvamused
surface was retired (docs/adr/0061). Sending an opinion is a deliberate act with
legal weight; it does not belong behind an inline control that could be
triggered by a mis-click, and withdrawing one is a POST behind a secondary
disclosure rather than a button under every row somebody is reading.

Every one of them lands on the opinion-filtered file list, and the two that know
which file they changed land on that row. A redirect that went to the retired
address would only be redirected again, which is a hop the reader pays for and
nobody needs.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from app.core.decorators import business_write_required
from app.core.errors import DomainError
from app.documents.models import Document, DocumentVersion
from app.documents.uploads import UploadRejected, read_upload
from app.matters.views import get_visible_matter, opinions_url
from app.submissions.enums import SentAtPrecision, SubmissionStatus
from app.submissions.forms import (
    CREATE_PREFIX,
    REGISTER_PREFIX,
    FinalEvidenceForm,
    MarkSentForm,
    RegisterSentOpinionForm,
    SubmissionCreateForm,
)
from app.submissions.models import Submission
from app.submissions.opinions import opinion_documents, sent_submission_by_document
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent,
    register_sent_opinion,
    select_final_evidence,
    withdraw_submission,
)


def _visible_submission(request: HttpRequest, pk: Any) -> Submission:
    return get_object_or_404(
        Submission.objects.visible_to(request.user).select_related("matter"), pk=pk
    )


def _back(submission: Submission) -> HttpResponse:
    """Dokumendid, on whichever row this submission is actually on.

    Two rows, because a submission has two shapes. A **sent** one is its file,
    so it is a row in the table and the anchor is that document — the whole
    point being that somebody who has just pressed send lands on the row they
    changed rather than at the top of a ninety-file list.

    Anything else — a draft, one that was just withdrawn, one whose send was
    refused — is *not* in the opinion-filtered table, and anchoring on its
    evidence would point at a row the filter has removed. A draft has its own id
    in the `Arvamused` block; a withdrawal lands on the list, because whether
    its file is still an opinion depends on a role this view has no business
    asking about (docs/adr/0061).
    """
    version = submission.final_version
    if submission.status == SubmissionStatus.SENT and version is not None:
        return redirect(opinions_url(submission.matter, anchor=f"dokument-{version.document_id}"))
    if submission.status == SubmissionStatus.DRAFT:
        return redirect(opinions_url(submission.matter, anchor=f"arvamus-{submission.pk}"))
    return redirect(opinions_url(submission.matter))


@login_required
@business_write_required
@require_http_methods(["POST"])
def create(request: HttpRequest, matter_id: Any) -> HttpResponse:
    matter = get_visible_matter(request, matter_id)
    form = SubmissionCreateForm(request.POST, prefix=CREATE_PREFIX)

    if not form.is_valid():
        messages.error(request, "Arvamuse loomine ebaõnnestus. Kontrolli välju.")
        return redirect(opinions_url(matter))

    submission = create_submission(
        matter=matter,
        title=form.cleaned_data["title"],
        kind=form.cleaned_data["kind"],
        actor=request.user,
        recipients=list(form.cleaned_data["recipients"]),
        for_information=list(form.cleaned_data["for_information"]),
        joint_submitters=list(form.cleaned_data["joint_submitters"]),
        channel=form.cleaned_data["channel"],
    )
    messages.success(request, f"Arvamus „{submission.title}“ on loodud.")
    return _back(submission)


@login_required
@business_write_required
@require_http_methods(["POST"])
def attach_evidence(request: HttpRequest, pk: Any) -> HttpResponse:
    """Capture the exact final text, by upload or by selecting existing evidence."""
    submission = _visible_submission(request, pk)
    form = FinalEvidenceForm(request.POST, request.FILES)

    if not form.is_valid():
        messages.error(request, "Vali fail või olemasolev tõend.")
        return _back(submission)

    try:
        if version_id := form.cleaned_data.get("existing_version"):
            # Through the authorization chokepoint, not `document__matter=`.
            # A Matter this reader can open may still hold a document they may
            # not — a child override only ever restricts further — and the
            # Matter-only filter let a crafted post bind one as a submission's
            # final evidence, after which the card printed its filename, size
            # and SHA-256 to everybody who could see the submission.
            #
            # `check_evidence_is_usable` does not close this: it refuses
            # evidence *less* restricted than the submission, which is the
            # other direction (app/core/authorization.py).
            version = get_object_or_404(
                DocumentVersion.objects.filter(
                    document__in=Document.objects.visible_to(request.user).filter(
                        matter=submission.matter
                    )
                ),
                pk=version_id,
            )
            select_final_evidence(submission=submission, version=version, actor=request.user)
        else:
            upload = read_upload(form.cleaned_data["upload"])
            attach_final_evidence(
                submission=submission,
                content=upload.content,
                original_filename=upload.filename,
                mime_type=upload.mime_type,
                actor=request.user,
            )
        messages.success(request, "Lõplik tõend on lisatud.")
    except (DomainError, UploadRejected) as error:
        messages.error(request, str(error))

    submission.refresh_from_db()
    return _back(submission)


@login_required
@business_write_required
@require_http_methods(["POST"])
def mark_sent(request: HttpRequest, pk: Any) -> HttpResponse:
    submission = _visible_submission(request, pk)
    form = MarkSentForm(request.POST)
    form.is_valid()

    try:
        mark_submission_sent(
            submission=submission,
            actor=request.user,
            channel=form.cleaned_data.get("channel", "") if form.is_bound else "",
            reference=form.cleaned_data.get("reference", "") if form.is_bound else "",
        )
        messages.success(request, "Arvamus on märgitud saadetuks.")
    except DomainError as error:
        messages.error(request, str(error))

    submission.refresh_from_db()
    return _back(submission)


@login_required
@business_write_required
@require_http_methods(["POST"])
def register_sent(request: HttpRequest, matter_id: Any) -> HttpResponse:
    """One opinion file on this Matter, recorded as having been sent.

    The act the retired page made a four-step errand: create an empty draft,
    find it again, bind it to a file that is already on the record, send it. It
    is one form and one transaction now, and every rule it touches is still
    decided in the service that owns it (`register_sent_opinion`).

    **The document is resolved twice on purpose.** The form's `document` choices
    come from the same selector as this — opinion files on this Matter, visible
    to this reader, with no canonical send yet — but a browser submits whatever
    it likes, so the identifier is looked up again in a set built here rather
    than trusted because it validated against a list the response happened to
    render. A crafted post naming a document on another Matter, one this reader
    may not see, or one that is already accounted for, finds nothing and is
    refused (AUTH-003 §21).

    It never reads `Document.current_version` off a document the caller passed:
    the version is taken from the row this view resolved, which is the exact
    binary the page offered.
    """
    matter = get_visible_matter(request, matter_id)
    sends = sent_submission_by_document(matter, viewer=request.user)
    candidates = {
        str(document.pk): document
        for document in opinion_documents(matter, viewer=request.user)
        if document.current_version_id and document.pk not in sends
    }
    form = RegisterSentOpinionForm(
        request.POST, prefix=REGISTER_PREFIX, documents=candidates.values()
    )

    if not form.is_valid():
        messages.error(request, "Saatmise registreerimine ebaõnnestus. Kontrolli välju.")
        return redirect(opinions_url(matter))

    document = candidates[form.cleaned_data["document"]]
    # Present by construction — `candidates` only holds documents carrying a
    # `current_version_id` — and refused rather than assumed, because the thing
    # on the other side of this line asserts that Koda sent a specific file.
    version = document.current_version
    if version is None:
        messages.error(request, "Sellel arvamusel ei ole faili, mida saadetuks märkida.")
        return redirect(opinions_url(matter))

    sent_on = form.cleaned_data.get("sent_on")
    try:
        register_sent_opinion(
            document=document,
            version=version,
            title=form.cleaned_data["title"],
            kind=form.cleaned_data["kind"],
            actor=request.user,
            recipients=list(form.cleaned_data["recipients"]),
            for_information=list(form.cleaned_data["for_information"]),
            joint_submitters=list(form.cleaned_data["joint_submitters"]),
            channel=form.cleaned_data["channel"],
            reference=form.cleaned_data["reference"],
            # A day the sender typed is a day, and midnight in the department's
            # timezone is the honest reading of it. Empty means now, which is a
            # real moment and is stored as one (app/submissions/enums.py).
            sent_at=_as_midnight(sent_on),
            sent_at_precision=SentAtPrecision.DATE if sent_on else SentAtPrecision.TIMESTAMP,
        )
        messages.success(request, "Arvamus on märgitud saadetuks.")
    except DomainError as error:
        messages.error(request, str(error))
        return redirect(opinions_url(matter))

    return redirect(opinions_url(matter, anchor=f"dokument-{document.pk}"))


def _as_midnight(value: Any) -> Any:
    """A chosen day, as the aware midnight a submission stores.

    The same reading `app/matters/forms.py` gives the closing composer's
    `Saatmise kuupäev`, and for the same reason: `timezone.now()` would stamp
    today onto a letter that went out last month.
    """
    if value is None:
        return None
    return timezone.make_aware(datetime.combine(value, time.min))


@login_required
@business_write_required
@require_http_methods(["POST"])
def withdraw(request: HttpRequest, pk: Any) -> HttpResponse:
    submission = _visible_submission(request, pk)
    try:
        withdraw_submission(
            submission=submission, actor=request.user, reason=request.POST.get("reason", "")
        )
        messages.success(request, "Arvamus on tagasi võetud.")
    except DomainError as error:
        messages.error(request, str(error))
    return _back(submission)
