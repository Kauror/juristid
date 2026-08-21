"""Document creation, evidence upload and authorized download.

The download route is the authorization boundary. Storage URLs are never handed
to the browser: a signed blob link would outlive the permission that produced it
and would bypass the audit record entirely (master specification 15.6, 5.2).
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, HttpRequest, HttpResponse, HttpResponseBase
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from app.accounts import shared_gate
from app.audit.enums import SecurityEventType
from app.audit.services import record_security_event
from app.core.decorators import gate_required, viewer_for
from app.core.errors import DomainError
from app.core.http import content_disposition
from app.documents import inline
from app.documents.email_intake import attachments_of, parent_email_of
from app.documents.enums import DerivativeKind, DerivativeStatus, DocumentRole
from app.documents.extraction.orchestrator import derivative_storage
from app.documents.models import Document, DocumentDerivative, DocumentVersion
from app.documents.preview import build_preview
from app.documents.services import add_evidence_version, create_document, evidence_storage
from app.documents.uploads import UploadRejected, read_upload
from app.matters.views import get_visible_matter


class DocumentUploadForm(forms.Form):
    title = forms.CharField(label="Pealkiri", max_length=400, required=False)
    role = forms.ChoiceField(
        label="Roll", choices=DocumentRole.choices, initial=DocumentRole.INCOMING_AUTHORITY
    )
    upload = forms.FileField(label="Fail")


@login_required
@require_http_methods(["POST"])
def upload_evidence(request: HttpRequest, matter_id: Any) -> HttpResponse:
    """Create a logical document and capture its first immutable version."""
    matter = get_visible_matter(request, matter_id)
    form = DocumentUploadForm(request.POST, request.FILES)

    if not form.is_valid():
        messages.error(request, "Vali fail ja roll.")
        return redirect("matters:matter_documents", pk=matter.pk)

    try:
        upload = read_upload(form.cleaned_data["upload"])
        document = create_document(
            matter=matter,
            title=form.cleaned_data["title"].strip() or upload.filename,
            role=form.cleaned_data["role"],
            created_by=request.user,
        )
        add_evidence_version(
            document=document,
            content=upload.content,
            original_filename=upload.filename,
            mime_type=upload.mime_type,
            uploaded_by=request.user,
        )
        messages.success(request, "Tõend on salvestatud.")
    except (DomainError, UploadRejected) as error:
        messages.error(request, str(error))

    return redirect("matters:matter_documents", pk=matter.pk)


@login_required
@require_http_methods(["POST"])
def add_version(request: HttpRequest, pk: Any) -> HttpResponse:
    """Add a further version to an existing document. Bytes never change."""
    document = get_object_or_404(Document.objects.visible_to(request.user), pk=pk)
    form = DocumentUploadForm(request.POST, request.FILES)
    form.fields["title"].required = False
    form.fields["role"].required = False

    if not form.is_valid() and "upload" in form.errors:
        messages.error(request, "Vali fail.")
        return redirect("matters:matter_documents", pk=document.matter_id)

    try:
        upload = read_upload(request.FILES.get("upload"))
        add_evidence_version(
            document=document,
            content=upload.content,
            original_filename=upload.filename,
            mime_type=upload.mime_type,
            uploaded_by=request.user,
        )
        messages.success(request, "Uus versioon on salvestatud.")
    except (DomainError, UploadRejected) as error:
        messages.error(request, str(error))

    return redirect("matters:matter_documents", pk=document.matter_id)


@gate_required
def download(request: HttpRequest, pk: Any) -> FileResponse:
    """Stream one evidence version to a user entitled to read it.

    The queryset is filtered through the document's visibility, so a restricted
    document 404s for anyone outside its Matter rather than 403ing — the same
    reason the Matter route does.
    """
    viewer = viewer_for(request)
    version = get_object_or_404(
        DocumentVersion.objects.filter(
            document__in=Document.objects.visible_to(viewer)
        ).select_related("document"),
        pk=pk,
    )

    record_security_event(
        event_type=SecurityEventType.DOCUMENT_DOWNLOADED,
        # The persona if one was selected, and nobody otherwise. `audit_detail`
        # records how the reader got in beside it, so a row from a shared-gate
        # session never reads as an individual having signed for the file.
        actor=request.user if request.user.is_authenticated else None,
        subject=version,
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        detail=shared_gate.audit_detail(
            request,
            document=str(version.document_id),
            matter=str(version.document.matter_id),
            sha256=version.sha256,
            disposition="attachment",
        ),
    )

    storage = evidence_storage()
    handle = storage.open(version.storage_key, "rb")

    response = FileResponse(
        handle,
        content_type=version.mime_type,
        # Always an attachment. Untrusted HTML or SVG rendered inline would run
        # in the application's own origin.
        as_attachment=True,
        filename=version.original_filename,
    )
    response["X-Content-Type-Options"] = "nosniff"
    # Set explicitly rather than left to `FileResponse`, because the filename
    # also has to be safe: an upload's name is whatever the multipart part
    # claimed, quotes and directory separators included (app/core/http.py).
    response["Content-Disposition"] = content_disposition("attachment", version.original_filename)
    return response


@gate_required
def open_inline(request: HttpRequest, pk: Any) -> HttpResponseBase:
    """Render one stored file in the browser, when that is safe.

    The same authorized queryset as `download`, so an unreadable document is a
    404 here exactly as it is there — a second route onto the same bytes must
    not be a second answer about who may have them.

    Safety is decided by `app/documents/inline.py`, which requires the extension
    and the stored MIME type to agree. Anything it declines redirects to the
    download route rather than erroring: the reader asked to see a file, and
    "here it is, saved instead" is a better answer than a stack trace.

    The response claims *our* MIME type, never the uploaded one.
    """
    viewer = viewer_for(request)
    version = get_object_or_404(
        DocumentVersion.objects.filter(
            document__in=Document.objects.visible_to(viewer)
        ).select_related("document"),
        pk=pk,
    )

    if not inline.may_open_inline(filename=version.original_filename, mime_type=version.mime_type):
        return redirect("documents:download", pk=version.pk)

    record_security_event(
        event_type=SecurityEventType.DOCUMENT_DOWNLOADED,
        actor=request.user if request.user.is_authenticated else None,
        subject=version,
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        detail=shared_gate.audit_detail(
            request,
            document=str(version.document_id),
            matter=str(version.document.matter_id),
            sha256=version.sha256,
            disposition="inline",
        ),
    )

    handle = evidence_storage().open(version.storage_key, "rb")
    response = FileResponse(handle, content_type=inline.inline_mime_for(version.original_filename))
    return inline.apply_inline_headers(response, filename=version.original_filename)


@gate_required
def thumbnail(request: HttpRequest, pk: Any) -> FileResponse:
    """Serve a generated preview image, inline.

    Inline is safe here and only here. These bytes were produced by this process
    — decoded, resized and re-encoded through Pillow — so whatever was
    interesting about the uploaded file's structure did not survive the round
    trip. The original still goes out as an attachment, because it is somebody
    else's bytes and always will be.

    Same visibility scope as every other document route, so a restricted
    document's thumbnail 404s exactly as its download does. A preview that
    leaked where a download did not would be the more embarrassing of the two.
    """
    derivative = get_object_or_404(
        DocumentDerivative.objects.filter(
            kind=DerivativeKind.THUMBNAIL,
            status=DerivativeStatus.ACTIVE,
            version__document__in=Document.objects.visible_to(viewer_for(request)),
        ).exclude(storage_key=""),
        pk=pk,
    )

    handle = derivative_storage().open(derivative.storage_key, "rb")
    response = FileResponse(handle, content_type="image/png")
    response["X-Content-Type-Options"] = "nosniff"
    # Belt and braces: even a generated PNG is served under a policy that
    # forbids scripts, so a hypothetical polyglot has nothing to execute.
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cache-Control"] = "private, max-age=300"
    return response


@gate_required
def document_detail(request: HttpRequest, pk: Any) -> HttpResponse:
    """One document: its evidence, its derived preview, and the line between.

    The two are never presented as the same thing. The original is a download
    with a checksum; everything else on this page is what a parser made of it
    and says so, because a preview that looks like the source of record is the
    provenance defect this whole stage exists to avoid
    (master specification 15.3, Stage-2B brief 35, 76).

    The queryset is the same ``visible_to`` scope every other document route
    uses, so a restricted document 404s here exactly as it does everywhere else
    — and for the same reason: a 403 would confirm it exists.
    """
    document = get_object_or_404(
        Document.objects.visible_to(viewer_for(request)).select_related(
            "matter", "current_version"
        ),
        pk=pk,
    )
    version = document.current_version
    versions = list(document.versions.order_by("-version_number"))

    return render(
        request,
        "documents/document_detail.html",
        {
            "matter": document.matter,
            "document": document,
            "version": version,
            "versions": versions,
            "preview": build_preview(version) if version is not None else None,
            # Provenance in both directions: what this file arrived inside, and
            # what arrived inside it.
            "parent_email": parent_email_of(version) if version is not None else None,
            "attachments": attachments_of(version) if version is not None else [],
            "nav_active": "teemad",
        },
    )
