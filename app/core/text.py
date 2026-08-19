"""Text normalisation shared by organisation and tag matching.

Estonian users type both ``õigusloome`` and ``oigusloome``; historic sources
mix casing and spacing. Normalised columns give deterministic exact matching
before any fuzzy search is involved (master specification 14.3–14.4).
"""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")


def normalize_for_matching(value: str) -> str:
    """Casefold, strip diacritics and collapse whitespace."""
    if not value:
        return ""
    lowered = unicodedata.normalize("NFKD", value.strip().casefold())
    without_marks = "".join(ch for ch in lowered if not unicodedata.combining(ch))
    return _WHITESPACE.sub(" ", without_marks).strip()
