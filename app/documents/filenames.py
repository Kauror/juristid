"""One rule for the filename a new evidence file is stored under.

Two defects had the same shape: a name was stored exactly as the sender's
software wrote it.

* **Unicode form.** macOS, some archivers and some mail clients write `õ` as
  `o` followed by a combining tilde (NFD). A lawyer's keyboard types the single
  precomposed letter (NFC). The two look identical and compare different, so
  the Dokumendid filter could not find the file and two copies of one name were
  not marked as twins (ENG-088).
* **Length.** A machine-generated attachment name longer than the 400
  characters `Document.title` and `DocumentVersion.original_filename` hold
  failed the whole e-mail's extraction with a database error (ENG-033), and the
  upload path's blind ``[:400]`` cut the extension off the end instead.

New names are therefore stored in NFC, bounded with their extension kept.
Names already stored are immutable (a trigger guards `DocumentVersion`), so
comparison normalises both sides at read time instead of rewriting history —
:func:`canonical_filename` is also what the comparisons call.

No transliteration, no case folding and no locale: `Õigusloome.pdf` stays
`Õigusloome.pdf`, only in one canonical spelling.
"""

from __future__ import annotations

import unicodedata

from django.db.models import CharField, Func

#: `Document.title` and `DocumentVersion.original_filename` are both
#: varchar(400); this is the one number both are bounded to.
FILENAME_MAX_LENGTH = 400

#: An extension longer than this is not an extension worth keeping at the cost
#: of the name — `.pdf`, `.docx`, `.eml` are all short.
_MAX_KEPT_EXTENSION = 16

#: Marks where a name was shortened, so a reader never mistakes a bounded name
#: for the whole of what the sender wrote.
_ELLIPSIS = "…"


def canonical_filename(name: str, *, limit: int = FILENAME_MAX_LENGTH) -> str:
    """``name`` in NFC, at most ``limit`` characters, extension preserved.

    Shortening cuts the stem, never the extension, and never leaves a
    combining mark stranded at the cut. An empty name stays empty — choosing a
    fallback is the caller's business, because only the caller knows whether
    «Manus 3» or «manus-3» is the right word.
    """
    text = unicodedata.normalize("NFC", name or "")
    if len(text) <= limit:
        return text

    stem, dot, extension = text.rpartition(".")
    if dot and stem and 0 < len(extension) <= _MAX_KEPT_EXTENSION:
        tail = f"{_ELLIPSIS}.{extension}"
        return _cut(stem, limit - len(tail)) + tail
    return _cut(text, limit - len(_ELLIPSIS)) + _ELLIPSIS


def _cut(text: str, length: int) -> str:
    """The first ``length`` characters, without splitting a base letter from
    the combining mark that follows it (NFC leaves a few such pairs)."""
    length = max(length, 0)
    while 0 < length < len(text) and unicodedata.combining(text[length]):
        length -= 1
    return text[:length]


class NFC(Func):
    """PostgreSQL's ``normalize(text, NFC)``, for names stored before ENG-088.

    Those rows are immutable and may hold decomposed letters, so a filter that
    compares them with a typed term normalises the column at read time rather
    than trusting what was written. Requires a UTF-8 database, which is the
    only kind Juristid runs on.
    """

    function = "normalize"
    template = "%(function)s(%(expressions)s, NFC)"
    output_field = CharField()
