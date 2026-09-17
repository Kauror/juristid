"""Whether `Uus teema` offers what the reader found in the file.

One function, read by the create surface and by the tests that describe it, so
that "the suggestion area is withdrawn" is a decision with a name rather than a
condition repeated in a view and a template.

**What it governs.** The lawyer-facing half of assisted intake on the *creation*
form: the «Failist leitud» panel, the ``data-prefill-*`` markers the browser
writes into empty controls, and the reading states the form prints while it
waits for them. Nothing else. Files are staged, promoted and held across a
refusal exactly as they were, because uploading a document and being offered a
reading of it are two features that merely met on one screen
(``app/matters/views.py`` ``_intake_context``, ``app/matters/intake_staging.py``).

**What it does not govern.** `Muuda teemat` — the saved Matter's own review of
what its documents say — is a different surface with a different argument: there
the record exists, the page is opened on purpose to look at the reading, and
nobody is being interrupted mid-capture. The feedback that withdrew this was
about the form a lawyer fills in every day, and it is not evidence about the
page they open when they want the reading (docs/adr/0088).

**Read per call rather than at import.** ``@override_settings`` is how the
suite proves both states of one page, and a module-level constant would freeze
whichever value happened to be in force when the module was first imported.
"""

from __future__ import annotations

from django.conf import settings


def create_form_suggestions_offered() -> bool:
    """True when `Uus teema` may show document-derived suggestions."""
    return bool(getattr(settings, "MATTER_INTAKE_SUGGESTIONS_ENABLED", False))
