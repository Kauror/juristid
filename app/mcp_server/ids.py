"""The opaque source ids the MCP tools hand out and accept back (docs/adr/0185 §2).

``<prefix>:<uuid>[@<ordinal>]`` — a prefix naming the record family, the
record's own primary key, and optionally where inside it a continuation starts.

**An id is a name, never a permission.** Parsing one says only which table to
look in; every reader then asks that table *through the reader's own scope*
(`visible_to`, `visible_links`), so a well-formed id for a record somebody may
not see answers exactly like an id for a record that does not exist. Nothing
here touches the database.

The ordinal means one thing per family:

* ``doc:<uuid>@<n>`` — the document's text from the fragment with ordinal
  ``n`` on (fragment ordinals are what `DocumentTextFragment.ordinal` stores);
* ``frag:<uuid>@<n>`` — that fragment's text from character ``n`` on, then the
  fragments after it. Issued only when one fragment is longer than a whole
  fetch, so the rest of it is still reachable;
* every other family — ``matter``, ``entry``, ``sub``, ``hist``, ``dev``,
  ``eng``, ``pos``, ``ovw``, ``date``, ``eff``, ``win`` — a character offset into
  the plain text `fetch` returned, for the next bounded part.

The server issues every id with an ordinal itself (`metadata.next_id`); a
client is not expected to compute one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

MATTER = "matter"
ENTRY = "entry"
SUBMISSION = "sub"
DOCUMENT = "doc"
FRAGMENT = "frag"
HISTORICAL_PAGE = "hist"
DEVELOPMENT = "dev"
ENGAGEMENT = "eng"
EXTERNAL_POSITION = "pos"
WEBSITE_OVERVIEW = "ovw"
IMPORTANT_DATE = "date"
EFFECTIVE_DATE = "eff"
WORK_VICTORY = "win"

PREFIXES: frozenset[str] = frozenset(
    {
        MATTER,
        ENTRY,
        SUBMISSION,
        DOCUMENT,
        FRAGMENT,
        HISTORICAL_PAGE,
        DEVELOPMENT,
        ENGAGEMENT,
        EXTERNAL_POSITION,
        WEBSITE_OVERVIEW,
        IMPORTANT_DATE,
        EFFECTIVE_DATE,
        WORK_VICTORY,
    }
)

#: Families whose id may carry ``@<ordinal>`` — every one; what it counts differs.
ORDINAL_PREFIXES: frozenset[str] = PREFIXES

#: The search projection's source kinds, by the prefix their record is fetched
#: under. Keyed by the `SearchSourceKind` *value* so this module imports no
#: model. `DOCUMENT_FRAGMENT` is named by its fragment, which `fetch` opens at
#: that fragment's page.
SEARCH_KIND_PREFIXES: dict[str, str] = {
    "MATTER": MATTER,
    "ENTRY": ENTRY,
    "SUBMISSION": SUBMISSION,
    "DOCUMENT": DOCUMENT,
    "DOCUMENT_FRAGMENT": FRAGMENT,
    "LEGACY_SOURCE_PAGE": HISTORICAL_PAGE,
    "ENGAGEMENT": ENGAGEMENT,
    "PROCEDURAL_DEVELOPMENT": DEVELOPMENT,
    "EXTERNAL_POSITION": EXTERNAL_POSITION,
    "WEBSITE_OVERVIEW": WEBSITE_OVERVIEW,
    "IMPORTANT_DATE": IMPORTANT_DATE,
    "WORK_VICTORY": WORK_VICTORY,
}

#: Longer than any id this module writes (a 6-character prefix, a 36-character
#: UUID, an ordinal), and short enough that nothing large is ever parsed.
MAX_ID_LENGTH = 80

#: An ordinal beyond this is past the end of anything stored, and would not fit
#: the database's integer either.
MAX_ORDINAL = 2_000_000_000


class MalformedSourceId(ValueError):
    """The string is not an id this server issues. The message names no record."""


@dataclass(frozen=True)
class SourceId:
    prefix: str
    pk: uuid.UUID
    ordinal: int | None = None

    def __str__(self) -> str:
        return format_source_id(self.prefix, self.pk, self.ordinal)


def format_source_id(prefix: str, pk: object, ordinal: int | None = None) -> str:
    """``doc:0192…`` or ``doc:0192…@7``. Refuses a prefix it would not parse back."""
    if prefix not in PREFIXES:
        raise ValueError(f"unknown source id prefix {prefix!r}")
    value = f"{prefix}:{uuid.UUID(str(pk))}"
    if ordinal is not None:
        if prefix not in ORDINAL_PREFIXES:
            raise ValueError(f"source id prefix {prefix!r} takes no ordinal")
        value = f"{value}@{int(ordinal)}"
    return value


def parse_source_id(raw: object) -> SourceId:
    """Read an id back, or raise :class:`MalformedSourceId`.

    Strict on purpose: one spelling per id. A lower-case canonical UUID with
    hyphens, a known prefix, and a decimal ordinal with no sign — anything else
    is refused rather than normalised, so two different strings never name the
    same record and a hand-built id cannot smuggle anything through.
    """
    if not isinstance(raw, str):
        raise MalformedSourceId("The id must be a string.")
    value = raw.strip()
    if not value or len(value) > MAX_ID_LENGTH:
        raise MalformedSourceId("The id is empty or too long.")
    prefix, separator, rest = value.partition(":")
    if not separator or prefix not in PREFIXES:
        raise MalformedSourceId("Unknown id prefix.")
    key, at, ordinal_text = rest.partition("@")
    try:
        pk = uuid.UUID(key)
    except ValueError:
        raise MalformedSourceId("The id does not name a record.") from None
    if str(pk) != key:
        raise MalformedSourceId("The id does not name a record.")
    ordinal: int | None = None
    if at:
        if prefix not in ORDINAL_PREFIXES:
            raise MalformedSourceId("This id takes no continuation.")
        if not ordinal_text.isascii() or not ordinal_text.isdigit():
            raise MalformedSourceId("The continuation is not a number.")
        ordinal = int(ordinal_text)
        if ordinal > MAX_ORDINAL:
            raise MalformedSourceId("The continuation is out of range.")
    return SourceId(prefix=prefix, pk=pk, ordinal=ordinal)
