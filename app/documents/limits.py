"""Bounds the documents domain enforces on what a person may store.

One rule governs this module, and it is the same one
``app/documents/extraction/limits.py`` states for the parsers: **a limit
refuses; it does not truncate.** A value silently shortened to fit is worse
than one refused, because the refused one gets corrected and the shortened one
is filed as if it were what the person supplied.

The working-document URL is where that mattered. Until now the service wrote
``sharepoint_web_url=url[:1000]`` — a SharePoint address longer than the column
was cut to length, stored, and reported as saved. The lawyer who pasted it got
a success message and a link that opens nothing, and the missing suffix is not
recoverable from the row: the only copy of it was in the clipboard.

1000 was never a considered number either. It was three independent literals —
the model's ``max_length``, the form's ``max_length`` and the slice in the
service — that happened to agree, and nothing made them agree. Long site and
drive paths with a sharing token in the query string legitimately run past it.

So there is one number, stated once, and it is a *bound* rather than a
convenience: past it the reference is refused with a message that says so.
"""

from __future__ import annotations

#: The longest working-document reference the system will store.
#:
#: Held by ``Document.sharepoint_web_url``, enforced again by
#: ``WorkingDocumentForm.web_url`` so the browser refuses before a request is
#: made, and enforced authoritatively by
#: ``app.documents.services.link_working_document``, which raises rather than
#: shortening. All three read this name; none of them carries its own literal.
#:
#: 4096 is chosen to sit above the longest addresses SharePoint actually
#: produces — a nested site path, a drive id, an item id and a sharing token in
#: the query string — while staying a number a person can reason about. It is
#: not a technical ceiling: ``varchar`` has none worth naming, and browsers
#: differ. It is the point past which a "URL" has stopped being one.
WORKING_DOCUMENT_URL_MAX_LENGTH = 4096
