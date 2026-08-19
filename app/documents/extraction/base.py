"""The contract every parser implements, and the registry that finds one.

A parser is an adapter and nothing more. It receives bytes and returns a
description of what it found; it does not touch the database, does not know
about `Document` or `Matter`, does not decide extraction state, and does not
open a network connection. Everything that writes is in `orchestrator.py`, so
there is exactly one place where a parse becomes committed state and exactly one
place to reason about what happens when it does not.

Keeping parsers pure is what makes them testable against a byte string and what
makes "the parser raised halfway" a boring case rather than an interesting one:
nothing has been written yet, because parsers cannot write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.documents.enums import DerivativeKind, LocatorKind, TextSource


@dataclass(frozen=True)
class SourceFile:
    """The bytes to parse, and what we were told about them.

    ``mime_type`` is the *server-side* determination made at upload from the
    extension allowlist and content signature, never the browser's claim
    (`app/documents/uploads.py`).
    """

    content: bytes
    filename: str
    mime_type: str


@dataclass(frozen=True)
class Fragment:
    """One bounded piece of text that knows where in the file it sits."""

    text: str
    locator_kind: str = LocatorKind.NONE
    locator: dict[str, object] = field(default_factory=dict)
    locator_label: str = ""
    text_source: str = TextSource.NATIVE


@dataclass(frozen=True)
class ParsedAttachment:
    """A file that arrived inside another file.

    ``inline`` separates a signature logo from the annex somebody actually
    sent. Both are preserved; only one becomes a Document (Stage-2B brief 27).
    """

    content: bytes
    filename: str
    mime_type: str
    content_id: str = ""
    inline: bool = False


@dataclass(frozen=True)
class DerivativePayload:
    """One derivative a parser wants written.

    A parse usually produces one of these. An email produces two — the parsed
    headers and the body text — and a scanned PDF produces text whose fragments
    are individually marked as having come from OCR.
    """

    kind: str = DerivativeKind.EXTRACTED_TEXT
    fragments: tuple[Fragment, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)
    # For derivatives that are files rather than rows: a thumbnail, a rendered
    # preview. Written to the derivative storage class, never to evidence.
    binary: bytes | None = None
    binary_extension: str = ""
    binary_mime_type: str = ""


@dataclass(frozen=True)
class ParseResult:
    derivatives: tuple[DerivativePayload, ...]
    attachments: tuple[ParsedAttachment, ...] = ()
    #: Operator-facing remark for a parse that succeeded with something worth
    #: saying — "3 of 12 pages were read by OCR", "no text layer found".
    note: str = ""


@runtime_checkable
class Parser(Protocol):
    """What the orchestrator requires of an adapter."""

    #: Stable short name, recorded on every derivative it produces. Changing it
    #: orphans previous output from its origin, so it is chosen once.
    name: str

    #: Bumped whenever this parser's output would differ for the same bytes.
    #: The orchestrator uses it to decide whether a rebuild has anything to do,
    #: and a derivative carries it so "which parser said this" survives the
    #: parser being replaced.
    version: str

    mime_types: frozenset[str]

    def parse(self, source: SourceFile) -> ParseResult: ...


class ParserRegistry:
    """MIME type to parser. One owner per type, checked at registration.

    Two parsers claiming the same MIME type is not a conflict to resolve at
    runtime by ordering — it is a mistake, and the loudest possible moment to
    discover it is import time.
    """

    def __init__(self) -> None:
        self._by_mime: dict[str, Parser] = {}

    def register(self, parser: Parser) -> Parser:
        for mime_type in parser.mime_types:
            existing = self._by_mime.get(mime_type)
            if existing is not None and existing is not parser:
                raise RuntimeError(
                    f"Two parsers claim {mime_type!r}: {existing.name} and {parser.name}."
                )
            self._by_mime[mime_type] = parser
        return parser

    def for_mime_type(self, mime_type: str) -> Parser | None:
        return self._by_mime.get(mime_type)

    def parsers(self) -> tuple[Parser, ...]:
        return tuple(dict.fromkeys(self._by_mime.values()))

    def supported_mime_types(self) -> frozenset[str]:
        return frozenset(self._by_mime)


registry = ParserRegistry()


def normalise(text: str) -> str:
    """Collapse a parser's whitespace without losing paragraph structure.

    Extracted text arrives full of layout artefacts — a PDF gives one newline
    per rendered line, a DOCX table gives none at all. Blank lines survive as
    paragraph boundaries because they carry meaning a reader uses; runs of
    spaces and trailing whitespace do not.
    """
    lines = [" ".join(line.split()) for line in (text or "").splitlines()]
    collapsed: list[str] = []
    for line in lines:
        if not line and collapsed and not collapsed[-1]:
            continue
        collapsed.append(line)
    return "\n".join(collapsed).strip()
