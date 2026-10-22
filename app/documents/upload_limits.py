"""One upload policy at the request boundary, for every door at once.

Before this, every upload door — Saabunud, `Uus teema` and its staging, a
held upload resumed, a document's next version, every Teema panel that takes a
file — checked its limits *after* Django had parsed the whole request, and most
of them after reading every accepted file into memory as well. A request of
twenty hundred-megabyte files was two gigabytes in one web worker before any
door could say «too much» (csf_35546c06657fae7b9935969d, docs/adr/0182).

The doors are many and the request is one, so the rule lives at the request:

* :class:`UploadLimitHandler` is the first of ``FILE_UPLOAD_HANDLERS``. It sees
  every multipart request before a byte of its body is read and refuses one
  whose declared ``Content-Length`` is over ``UPLOAD_MAX_REQUEST_BYTES``; while
  the body streams it counts the files (``UPLOAD_MAX_FILES_PER_REQUEST``) and
  the bytes, and stops the parse the moment either is passed. A single file
  over ``MAX_EVIDENCE_UPLOAD_BYTES`` is not stored past one chunk beyond that
  ceiling: the door still refuses it with its own sentence, by name, and the
  rest of the batch is unaffected — exactly as before.
* :class:`UploadLimitMiddleware` is the first thing that touches
  ``request.POST``. When the handler refused, it closes whatever the parse had
  already written and answers **413** before any view, form or service runs —
  a view can never act on a request whose files were cut short.

Below this boundary nothing changed in what is *refused*: the extension
allowlist, the content signatures and the per-file ceiling are
`app.documents.uploads`'s, and a door adding held files to new ones counts
them together with `check_upload_batch`. What changed is that the files stay
on disk (Django's temporary upload files) and are streamed from there.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from django.http import HttpRequest, HttpResponse, QueryDict
from django.template.loader import render_to_string
from django.utils.datastructures import MultiValueDict

from app.core.middleware import FAILURE_HEADER, NO_STORE, is_htmx
from app.documents.uploads import batch_too_large_refusal, too_many_files_refusal

logger = logging.getLogger(__name__)

#: Where the handler leaves its refusal for the middleware.
REFUSAL_ATTRIBUTE = "_upload_limit_refusal"

#: The `X-Juristid-Failure` word `static/js/app.js` tells as its own sentence.
FAILURE_KIND = "upload-too-large"


def _refuse(request: Any, sentence: str, reason: str) -> None:
    if request is not None and not getattr(request, REFUSAL_ATTRIBUTE, ""):
        setattr(request, REFUSAL_ATTRIBUTE, sentence)
    # Sizes and counts only: a filename is business text (ENG-071).
    logger.warning("upload request refused at the boundary: %s", reason)


class UploadLimitHandler(FileUploadHandler):
    """Counts a multipart request's files and bytes, and stops it when they pass.

    First in the handler chain and stores nothing itself: every chunk it lets
    through goes on to Django's own memory and temporary-file handlers.
    """

    def __init__(self, request: Any = None) -> None:
        super().__init__(request)
        self.files = 0
        self.total = 0
        self.this_file = 0

    def handle_raw_input(
        self,
        input_data: Any,
        META: Any,
        content_length: int,
        boundary: Any,
        encoding: str | None = None,
    ) -> tuple[QueryDict, MultiValueDict[str, Any]] | None:
        if content_length > settings.UPLOAD_MAX_REQUEST_BYTES:
            # Answered without reading the body at all: an empty parse, and the
            # refusal for the middleware. Nothing reaches a temporary file.
            _refuse(
                self.request,
                batch_too_large_refusal(),
                f"declared {content_length} bytes",
            )
            return QueryDict(encoding=encoding), MultiValueDict()
        return None

    def new_file(self, *args: Any, **kwargs: Any) -> None:
        super().new_file(*args, **kwargs)
        self.files += 1
        self.this_file = 0
        if self.files > settings.UPLOAD_MAX_FILES_PER_REQUEST:
            _refuse(self.request, too_many_files_refusal(), f"{self.files} files")
            raise StopUpload(connection_reset=True)

    def receive_data_chunk(self, raw_data: bytes, start: int) -> bytes | None:
        size = len(raw_data)
        self.total += size
        if self.total > settings.UPLOAD_MAX_REQUEST_BYTES:
            # Only reachable when the declared length was absent or wrong.
            _refuse(self.request, batch_too_large_refusal(), f"{self.total} bytes received")
            raise StopUpload(connection_reset=True)
        before = self.this_file
        self.this_file += size
        if before > settings.MAX_EVIDENCE_UPLOAD_BYTES:
            # Already past the per-file ceiling by the chunk that crossed it:
            # the door will refuse this file by name from the size the next
            # handler counted, and there is nothing more worth keeping.
            return None
        return raw_data

    def file_complete(self, file_size: int) -> None:
        return None


class UploadLimitMiddleware:
    """Answer a request the upload handler refused, before any view sees it."""

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method == "POST" and request.content_type == "multipart/form-data":
            # Parsed here, first, so the refusal is known before CSRF, the
            # NUL check or any view reads a field.
            request.POST  # noqa: B018 - the parse is the point
            refusal = getattr(request, REFUSAL_ATTRIBUTE, "")
            if refusal:
                _close_files(request)
                return refusal_response(request, refusal)
        return self.get_response(request)


def _close_files(request: HttpRequest) -> None:
    """Delete what the parse had already written (Django's temporary files)."""
    files = getattr(request, "_files", None)
    if not files:
        return
    for _field, handles in files.lists():
        for handle in handles:
            try:
                handle.close()
            except Exception:  # pragma: no cover - best effort; the request closes them too
                logger.warning("could not close a refused upload", exc_info=True)


def refusal_response(request: HttpRequest, sentence: str) -> HttpResponse:
    """413, with the sentence. HTMX gets the header its failure line reads."""
    if is_htmx(request):
        response = HttpResponse(sentence, status=413, content_type="text/plain; charset=utf-8")
        response[FAILURE_HEADER] = FAILURE_KIND
    else:
        response = HttpResponse(
            render_to_string("413.html", {"refusal": sentence}),
            status=413,
            content_type="text/html; charset=utf-8",
        )
    response["Cache-Control"] = NO_STORE
    return response
