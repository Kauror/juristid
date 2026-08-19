"""Submission actions.

All of these are full-page posts that redirect back to the position tab.
Sending an opinion is a deliberate act with legal weight; it does not belong
behind an inline control that could be triggered by a mis-click.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_http_methods

from app.core.errors import DomainError
from app.documents.models import DocumentVersion
from app.documents.uploads import UploadRejected, read_upload
from app.matters.views import get_visible_matter
from app.submissions.forms import FinalEvidenceForm, MarkSentForm, SubmissionCreateForm
from app.submissions.models import Submission
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent,
    select_final_evidence,
    withdraw_submission,
)


def _visible_submission(request: HttpRequest, pk: Any) -> Submission:
    return get_object_or_404(
        Submission.objects.visible_to(request.user).select_related("matter"), pk=pk
    )


@login_required
@require_http_methods(["POST"])
def create(request: HttpRequest, matter_id: Any) -> HttpResponse:
    matter = get_visible_matter(request, matter_id)
    form = SubmissionCreateForm(request.POST)

    if not form.is_valid():
        messages.error(request, "Arvamuse loomine ebaõnnestus. Kontrolli välju.")
        return redirect("matters:matter_position", pk=matter.pk)

    submission = create_submission(
        matter=matter,
        title=form.cleaned_data["title"],
        kind=form.cleaned_data["kind"],
        actor=request.user,
        recipients=list(form.cleaned_data["recipients"]),
        joint_submitters=list(form.cleaned_data["joint_submitters"]),
        channel=form.cleaned_data["channel"],
    )
    messages.success(request, f"Arvamus „{submission.title}“ on loodud.")
    return redirect("matters:matter_position", pk=matter.pk)


@login_required
@require_http_methods(["POST"])
def attach_evidence(request: HttpRequest, pk: Any) -> HttpResponse:
    """Capture the exact final text, by upload or by selecting existing evidence."""
    submission = _visible_submission(request, pk)
    form = FinalEvidenceForm(request.POST, request.FILES)

    if not form.is_valid():
        messages.error(request, "Vali fail või olemasolev tõend.")
        return redirect("matters:matter_position", pk=submission.matter_id)

    try:
        if version_id := form.cleaned_data.get("existing_version"):
            version = get_object_or_404(
                DocumentVersion.objects.filter(document__matter=submission.matter), pk=version_id
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

    return redirect("matters:matter_position", pk=submission.matter_id)


@login_required
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

    return redirect("matters:matter_position", pk=submission.matter_id)


@login_required
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
    return redirect("matters:matter_position", pk=submission.matter_id)
