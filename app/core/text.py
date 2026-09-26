"""Text normalisation shared by organisation and tag matching.

Estonian users type both ``õigusloome`` and ``oigusloome``; historic sources
mix casing and spacing. Normalised columns give deterministic exact matching
before any fuzzy search is involved (master specification 14.3–14.4).

Two folds live here, and the difference between them is deliberate.
:func:`normalize_for_matching` is the general one, and the search projection
writes its output into indexed text — so its behaviour is part of what
``INDEX_VERSION`` promises, and it is not changed to serve one caller.
:func:`normalize_organisation_name` is the key an *institution's identity* is
compared on, and it is that fold with Unicode's invisible format characters
removed first (ENG-045).
"""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")

#: The Unicode bidirectional formatting characters: the embeddings, overrides
#: and isolates, their terminators, and the three implicit marks (ALM, LRM,
#: RLM). Each one reorders or re-directs what a reader sees without being seen
#: itself — «Trojan Source» is the well-known abuse — and none has a business
#: meaning in the name of an institution.
BIDI_CONTROLS = frozenset(
    "\u061c"  # ARABIC LETTER MARK
    "\u200e\u200f"  # LEFT-TO-RIGHT MARK, RIGHT-TO-LEFT MARK
    "\u202a\u202b\u202c\u202d\u202e"  # LRE, RLE, PDF, LRO, RLO
    "\u2066\u2067\u2068\u2069"  # LRI, RLI, FSI, PDI
)


def normalize_for_matching(value: str) -> str:
    """Casefold, strip diacritics and collapse whitespace."""
    if not value:
        return ""
    lowered = unicodedata.normalize("NFKD", value.strip().casefold())
    without_marks = "".join(ch for ch in lowered if not unicodedata.combining(ch))
    return _WHITESPACE.sub(" ", without_marks).strip()


def without_format_characters(value: str) -> str:
    """``value`` without any character of Unicode general category Cf.

    Cf is the invisible "format" class: the soft hyphen, zero-width space and
    joiners, the word joiner, the byte-order mark, the direction controls and a
    long tail of script-specific marks. Copying text out of Word, a PDF or a web
    page brings them along, and a reader cannot see that they are there.
    """
    return "".join(ch for ch in value if unicodedata.category(ch) != "Cf")


def normalize_organisation_name(value: str) -> str:
    """The key an institution's name or alias is matched on.

    :func:`normalize_for_matching` after :func:`without_format_characters`, and
    nothing else: casing, diacritics and spacing fold exactly as they always
    have — `Põllumajandus` still finds `Pollumajandus` — and visible
    punctuation still tells two names apart. What no longer does is an
    invisible character, so «Rahandusministeerium» with a soft hyphen (U+00AD) pasted
    into it is the ministry that is already there rather than a second one.

    Removing them *first* is sufficient: casefolding and NFKD never produce a
    format character from one that is not.

    Separate from the general fold on purpose. The search projection writes
    that one's output into indexed text, and changing it would change what the
    index means without an ``INDEX_VERSION`` to say so.
    """
    if not value:
        return ""
    return normalize_for_matching(without_format_characters(value))


def strip_bidi_controls(value: str) -> str:
    """``value`` without the :data:`BIDI_CONTROLS`.

    For single-line governed names, where a direction override has no meaning
    and can make a stored name display as something it is not. Other invisible
    characters are left as written — they are ignored for identity by
    :func:`normalize_organisation_name`, not deleted from somebody's text — and
    free text is not passed through here at all.
    """
    if not value:
        return value
    return "".join(ch for ch in value if ch not in BIDI_CONTROLS)


def clean_single_line_name(value: str | None) -> str:
    """A typed single-line name as it is stored: no direction controls, and
    whitespace collapsed to single spaces with none at either end."""
    return " ".join(strip_bidi_controls(value or "").split())
