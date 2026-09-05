"""Pure readers over extracted text.

Every function here is a function of strings and the vocabulary in
`vocabulary.py`. None of them touches the database, the network, the clock
or a model: given the same text they return the same findings, which is what
makes them testable against a sentence and what makes the analyser
deterministic end to end (assisted-intake brief §22).

What they return is deliberately raw — positions, matched forms, the cues
that fired. Deciding what a finding *means* for the Matter (a confidence, a
conflict, a pre-fill) is `analysis.py`'s job, and keeping that decision out
of here is what lets a maintainer read the rules without reading the policy.

The one shared piece of prior art is reused rather than rewritten: the
register parser's Estonian date scanner (`_scan_dates`) already reads
``18.09.2026``, ``18. septembril 2026``, ``18. septembriks 2026`` and
``2026-09-18`` with the month tables the register work reviewed. It is
imported under its private name on purpose — promoting it to a public one is
a change to the register import, which is not this branch's to make.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date

from app.legacy_import.register_next_actions import DateMention, _scan_dates
from app.matters.intake_suggestions import vocabulary as vocab
from app.matters.intake_suggestions.input import SourceDocument, TextBlock
from app.matters.intake_suggestions.types import EXCERPT_LIMIT, Confidence
from app.workflow.enums import DatePrecision

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_WHITESPACE = re.compile(r"\s+")

#: A sentence boundary that is not the dot inside ``18.09.2026`` or ``18. septembril``.
#:
#: A full stop after a digit ends a sentence only when a capital letter
#: follows: «…18. septembriks 2026. Seadus jõustub…» is two sentences, while
#: «18. septembril», «2027. aasta» and «5. kvartal» are one phrase each
#: (Estonian month and period words are lower case).
_SENTENCE_BREAK = re.compile(r"(?<!\d)[.!?;](?!\s*\d)|(?<=\d)[.!?;](?=\s+[A-ZÕÄÖÜŠŽ])|\n\s*\n")


def collapse(text: str) -> str:
    return _WHITESPACE.sub(" ", text or "").strip()


#: How far past ``limit`` the forward sentence search may look, so a break
#: sitting on the boundary can still match its own lookahead («… 2026. Seadus
#: …» needs the space and the capital after the dot to be inside the window).
_BREAK_MARGIN = 64


def excerpt_around(text: str, start: int, end: int, *, limit: int = EXCERPT_LIMIT) -> str:
    """The sentence around ``[start, end)``, collapsed and bounded.

    The span itself is always inside the excerpt. Where the sentence is longer
    than the limit, the excerpt is centred on the span and marked with an
    ellipsis at whichever ends were cut.

    **Both boundary searches are bounded by ``limit``, and the string this
    returns is the one an unbounded search returns.** It used to scan from
    character zero up to the span on every call — linear in the offset, and so
    quadratic over a document once it holds many matches. A reference-dense
    100 000-character text spent most of one analysis in this function.

    Looking further back cannot change the answer. If a sentence break lies
    inside the window it is the last one before the span, so ``left`` is
    exact. If none does, the excerpt is necessarily longer than ``limit``, so
    it is centred on the span with an ellipsis on each side — which is exactly
    what the true, more distant sentence start produces, character for
    character, because the centring is relative to the span and never reaches
    more than ``limit // 2`` away from it. The forward search is the same
    argument mirrored, with ``_BREAK_MARGIN`` of slack so a break on the
    boundary is not lost to a truncated lookahead.
    """
    window_start = max(0, start - limit)
    left = window_start
    for match in _SENTENCE_BREAK.finditer(text, window_start, start):
        left = match.end()
    ceiling = min(len(text), end + limit)
    right_match = _SENTENCE_BREAK.search(text, end, min(len(text), ceiling + _BREAK_MARGIN))
    right = min(right_match.end(), ceiling) if right_match else ceiling
    sentence = text[left:right]
    span_start, span_end = start - left, end - left
    if len(sentence) > limit:
        room = max(limit - (span_end - span_start), 0)
        before = min(span_start, room // 2)
        cut_start = span_start - before
        cut_end = min(len(sentence), span_end + (room - before))
        piece = sentence[cut_start:cut_end]
        prefix = "…" if cut_start > 0 else ""
        suffix = "…" if cut_end < len(sentence) else ""
        return f"{prefix}{collapse(piece)}{suffix}"
    return collapse(sentence)


def line_of(text: str, position: int, *, limit: int = EXCERPT_LIMIT) -> str:
    """The line ``position`` sits on, trimmed and bounded like an excerpt.

    A «line» is only as short as the extractor made it. A .txt filed as one
    paragraph, or a PDF whose text layer arrives unwrapped, has no newline for
    tens of thousands of characters — and this string is shown to the reader
    as the evidence under a suggestion. Unbounded it was both a page of prose
    in a panel meant for one sentence and, collapsed once per match, the
    second quadratic in a reference-dense document.
    """
    left = text.rfind("\n", 0, position) + 1
    right = text.find("\n", position)
    if right < 0:
        right = len(text)
    if right - left <= limit:
        return collapse(text[left:right])
    cut_start = max(left, position - limit // 2)
    cut_end = min(right, cut_start + limit)
    prefix = "…" if cut_start > left else ""
    suffix = "…" if cut_end < right else ""
    return f"{prefix}{collapse(text[cut_start:cut_end])}{suffix}"


def _window(text: str, start: int, end: int, before: int, after: int) -> tuple[str, int]:
    """The clause around a span: bounded by sentence breaks and by distance.

    Returns the window and the offset of the span's start inside it. Single
    line breaks are *not* clause breaks: a PDF's text arrives one line per
    rendered line, and «Palume esitada arvamus / hiljemalt 18.09.2026» is one
    sentence wrapped, not two clauses.
    """
    left = max(0, start - before)
    for match in _SENTENCE_BREAK.finditer(text, left, start):
        left = match.end()
    right = min(len(text), end + after)
    right_match = _SENTENCE_BREAK.search(text, end, right)
    if right_match:
        right = right_match.start()
    return text[left:right], start - left


def _fired(signals: tuple[vocab.Signal, ...], text: str) -> list[vocab.Signal]:
    return [signal for signal in signals if signal.regex.search(text)]


# ---------------------------------------------------------------------------
# Dates that may be a response deadline
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DateFinding:
    """One exact date in a block, with the verdict the words around it earned."""

    value: date
    start: int
    end: int
    span: str
    #: HIGH, MEDIUM, or LOW. LOW is a date the clause says is something else —
    #: a dateline, an entry-into-force day, the date of the letter answered.
    strength: str
    cues: tuple[str, ...] = ()
    decoys: tuple[str, ...] = ()
    rule: str = ""


#: A month form ending in ``-ks`` (*septembriks*) is the translative case:
#: «by September». It states a deadline on its own.
_TRANSLATIVE_MONTH = re.compile(r"[a-zõäöü]+ks\b", re.IGNORECASE)


def find_deadline_dates(text: str, *, opens_document: bool = False) -> list[DateFinding]:
    """Every exact date in ``text``, classified by the clause it sits in.

    ``opens_document`` says this text is the document's first block, where a
    date inside the first :data:`vocab.DATELINE_WINDOW` characters with no cue
    at all is the letter's own date.

    Periods — *septembris 2026*, *II kvartal 2027* — are read by the scanner
    and deliberately dropped here: ``Matter.response_deadline`` is a day, and
    a period offered as one would invent a precision the source never stated.

    **Scanned in chunks.** The shared scanner resolves overlapping mentions by
    comparing every mention with every other, which is exactly right for the
    register cell it was written for and quadratic on a document: one 400 000
    character block holding thousands of dates took eighteen seconds, inside a
    web request. Chunking makes that term per chunk instead of per block. The
    chunks end on line breaks and every date keeps its position in the whole
    text, so what a clause is read against is unchanged — a chunk boundary is
    the same kind of boundary a page already is.
    """
    findings: list[DateFinding] = []
    for chunk, offset in _chunks(text):
        for mention in _scan_dates(chunk).mentions:
            if mention.precision != DatePrecision.EXACT.value:
                continue
            moved = replace(mention, start=mention.start + offset, end=mention.end + offset)
            findings.append(_classify_date(text, moved, opens_document=opens_document))
    findings.sort(key=lambda finding: finding.start)
    return findings


#: How much text goes to the date scanner at once. Large enough that a page,
#: a paragraph group or an ordinary message body is one chunk; small enough
#: that the scanner's pairwise overlap resolution stays cheap.
SCAN_CHUNK = 20_000


def _chunks(text: str) -> list[tuple[str, int]]:
    """``text`` in scanner-sized pieces, each with its offset in the original.

    Split on line breaks so a chunk never ends mid-sentence; a line longer
    than the target simply becomes its own chunk rather than being cut.
    """
    if len(text) <= SCAN_CHUNK:
        return [(text, 0)]
    pieces: list[tuple[str, int]] = []
    start = 0
    while start < len(text):
        if len(text) - start <= SCAN_CHUNK:
            pieces.append((text[start:], start))
            break
        cut = text.rfind("\n", start, start + SCAN_CHUNK)
        end = cut + 1 if cut > start else start + SCAN_CHUNK
        pieces.append((text[start:end], start))
        start = end
    return pieces


def _classify_date(text: str, mention: DateMention, *, opens_document: bool) -> DateFinding:
    span = text[mention.start : mention.end]
    clause, offset = _window(
        text,
        mention.start,
        mention.end,
        vocab.DEADLINE_CONTEXT_BEFORE,
        vocab.DEADLINE_CONTEXT_AFTER,
    )
    # Decoys are judged close to the date — the sixty characters before it and
    # the forty after, inside its own sentence. A «Teie 04.09.2026» in the
    # address block must not poison the sentence that states the real
    # deadline, and «Seadus jõustub 1. jaanuaril 2027» in the next sentence
    # must not poison this one.
    near = clause[max(0, offset - 60) : offset + (mention.end - mention.start) + 40]
    decoys = tuple(
        signal.label for signal in vocab.DEADLINE_DECOY_CUES if signal.regex.search(near)
    )

    strong = _fired(vocab.DEADLINE_STRONG_CUES, clause)
    requests = _fired(vocab.DEADLINE_REQUEST_CUES, clause)
    responses = _fired(vocab.DEADLINE_RESPONSE_CUES, clause)
    cues = [signal.label for signal in (*strong, *requests, *responses)]
    if _TRANSLATIVE_MONTH.search(span) or span.rstrip().endswith("-ks"):
        strong.append(vocab.Signal("", 3, "…ks (tähtajaks)"))
        cues.append("…ks (tähtajaks)")

    if decoys:
        return DateFinding(
            mention.anchor,
            mention.start,
            mention.end,
            span,
            Confidence.LOW,
            tuple(cues),
            decoys,
            "date_in_decoy_clause",
        )
    if opens_document and mention.start < vocab.DATELINE_WINDOW and not cues:
        return DateFinding(
            mention.anchor,
            mention.start,
            mention.end,
            span,
            Confidence.LOW,
            (),
            ("dokumendi kuupäev",),
            "dateline",
        )
    # «kuni» alone is weak: «kuni 31.12.2026 kehtiv» is a validity, not a
    # request. It counts as strong only beside a request or a response word.
    decisive = [signal for signal in strong if signal.label != "kuni"] or (
        strong if (requests or responses) else []
    )
    if decisive:
        return DateFinding(
            mention.anchor,
            mention.start,
            mention.end,
            span,
            Confidence.HIGH,
            tuple(cues),
            (),
            "explicit_response_deadline",
        )
    if requests and responses:
        return DateFinding(
            mention.anchor,
            mention.start,
            mention.end,
            span,
            Confidence.MEDIUM,
            tuple(cues),
            (),
            "requested_response_date",
        )
    return DateFinding(
        mention.anchor,
        mention.start,
        mention.end,
        span,
        Confidence.LOW,
        tuple(cues),
        (),
        "date_without_deadline_wording",
    )


@dataclass(frozen=True)
class RelativeDeadline:
    phrase: str
    start: int
    end: int


def find_relative_deadlines(text: str) -> list[RelativeDeadline]:
    """«kolme nädala jooksul» beside a request — stated, but without a day."""
    found: list[RelativeDeadline] = []
    for match in vocab.RELATIVE_DEADLINE.finditer(text):
        clause, _ = _window(
            text,
            match.start(),
            match.end(),
            vocab.DEADLINE_CONTEXT_BEFORE,
            vocab.DEADLINE_CONTEXT_AFTER,
        )
        if _fired(vocab.DEADLINE_REQUEST_CUES, clause) or _fired(
            vocab.DEADLINE_RESPONSE_CUES, clause
        ):
            found.append(RelativeDeadline(match.group(0), match.start(), match.end()))
    return found


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TitleFinding:
    text: str
    score: int
    rule: str
    cues: tuple[str, ...]
    #: The line as it stood in the source, for the evidence excerpt.
    line: str
    block: TextBlock | None = None
    start: int = 0


def find_heading_titles(document: SourceDocument) -> list[TitleFinding]:
    """Heading-shaped lines near the start of a document that name an act.

    A line qualifies when it is heading-length, carries legislative language,
    and is not a salutation, an address, a date line or a bare document type.
    «Seletuskiri … juurde» yields what it wraps, at a lower score: the
    memorandum's heading names the draft, but a memorandum is not the draft.
    """
    findings: list[TitleFinding] = []
    consumed = 0
    for block in document.prose_blocks:
        if consumed >= vocab.TITLE_OPENING_WINDOW:
            break
        window = block.text[: vocab.TITLE_OPENING_WINDOW - consumed]
        consumed += len(block.text)
        position = 0
        raw_lines = window.split("\n")
        for index, raw_line in enumerate(raw_lines):
            line_start = position
            position += len(raw_line) + 1
            line = collapse(raw_line)
            if not line:
                continue
            # A heading stands on its own: blank lines on both sides, or the
            # top of the document. Prose has neighbours.
            above = collapse(raw_lines[index - 1]) if index > 0 else ""
            below = collapse(raw_lines[index + 1]) if index + 1 < len(raw_lines) else ""
            finding = _heading_from_line(line, block, line_start, isolated=not above and not below)
            if finding is not None:
                findings.append(finding)
    findings.sort(key=lambda finding: (-finding.score, finding.start))
    return findings


def _heading_from_line(
    line: str, block: TextBlock, start: int, *, isolated: bool
) -> TitleFinding | None:
    if not vocab.TITLE_MIN_LENGTH <= len(line) <= vocab.TITLE_MAX_LENGTH:
        return None
    if vocab.TITLE_SALUTATION.match(line) or vocab.TITLE_TYPE_ONLY.match(line):
        return None
    if vocab.EMAIL_ADDRESS.search(line) or vocab.ANY_URL.search(line):
        return None
    if line.endswith(":") or _mostly_numbers(line):
        return None
    rule = "formal_heading"
    candidate = line
    wrapped = vocab.TITLE_MEMORANDUM_WRAPPER.match(line)
    if wrapped:
        # «Seletuskiri pakendiseaduse … eelnõu juurde» — the draft's name sits
        # inside the sentence in the genitive-led lower case the wrapper gives
        # it; as a title it starts with a capital, and that is the only letter
        # this changes.
        inner = collapse(wrapped.group("title"))
        candidate = inner[:1].upper() + inner[1:]
        rule = "memorandum_heading"
    fired = _fired(vocab.TITLE_STRONG_CUES, candidate)
    if not fired:
        return None
    score = sum(signal.weight for signal in fired)
    if rule == "memorandum_heading":
        score -= 2
    if isolated:
        score += 2
    # A heading is a phrase, not a sentence. A full stop, a trailing comma, a
    # sentence boundary inside the line, a lower-case first letter, or a verb
    # of sending or asking says this line is prose that happens to mention an
    # act — «Saadame Teile kooskõlastamiseks … eelnõu, mis on», or a wrapped
    # line of a paragraph.
    if (
        candidate.endswith((".", ","))
        or line[:1].islower()
        or _SENTENCE_BREAK.search(candidate)
        or vocab.TITLE_PROSE_CUES.search(candidate)
    ):
        score -= vocab.TITLE_PROSE_PENALTY
    if score < 2:
        return None
    return TitleFinding(
        text=clean_title(candidate),
        score=score,
        rule=rule,
        cues=tuple(signal.label for signal in fired),
        line=line,
        block=block,
        start=start,
    )


def clean_title(value: str) -> str:
    """A heading without the purpose it was sent for.

    «Pakendiseaduse muutmise seaduse eelnõu kooskõlastamiseks» names the draft
    and says why it was sent; the Matter is about the draft. Only a closed
    list of purpose words is removed, and only from the end.
    """
    cleaned = collapse(value).rstrip(" .")
    cleaned = vocab.TITLE_PURPOSE_SUFFIX.sub("", cleaned).rstrip(" .,;:—–-")
    return cleaned


def _mostly_numbers(line: str) -> bool:
    letters = sum(1 for ch in line if ch.isalpha())
    digits = sum(1 for ch in line if ch.isdigit())
    return digits >= letters


def subject_title(subject: str) -> TitleFinding | None:
    """A message subject with its mail prefixes stripped, scored like a heading."""
    cleaned = collapse(vocab.SUBJECT_PREFIXES.sub("", subject or ""))
    if len(cleaned) < vocab.TITLE_MIN_LENGTH:
        return None
    fired = _fired(vocab.TITLE_STRONG_CUES, cleaned)
    if fired:
        return TitleFinding(
            text=clean_title(cleaned),
            score=sum(signal.weight for signal in fired),
            rule="email_subject",
            cues=tuple(signal.label for signal in fired),
            line=subject,
        )
    if len(cleaned.split()) >= 3:
        return TitleFinding(
            text=cleaned, score=1, rule="email_subject_plain", cues=(), line=subject
        )
    return None


def normalise_title(value: str) -> str:
    return collapse(value).casefold().rstrip(" .")


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContactFinding:
    email: str
    name: str
    rule: str
    block: TextBlock
    start: int
    line: str


def find_contacts(document: SourceDocument) -> list[ContactFinding]:
    """E-mail addresses in a document, with the name beside them when there is one.

    Three readings, from strongest to weakest: an address introduced by a
    contact label (*Kontaktisik:*, *Koostaja:*, *E-post:*); an address in the
    signature block after *Lugupidamisega*; and an address anywhere else,
    which is a fact about the document and nothing about who sent it.
    """
    findings: list[ContactFinding] = []
    seen: set[str] = set()
    blocks = document.prose_blocks
    for index, block in enumerate(blocks):
        is_last = index == len(blocks) - 1
        for match in vocab.EMAIL_ADDRESS.finditer(block.text):
            address = match.group(0).rstrip(".,;:")
            key = address.casefold()
            if key in seen:
                continue
            before = block.text[
                max(0, match.start() - vocab.CONTACT_CONTEXT_BEFORE) : match.start()
            ]
            # A label introduces the address it sits in the same sentence with.
            # «Küsimuste korral pöörduge … poole: Nimi, nimi@…» is labelled;
            # the address in the sentence after it is not.
            clause, _ = _window(
                block.text, match.start(), match.end(), vocab.CONTACT_CONTEXT_BEFORE, 0
            )
            labelled = bool(vocab.CONTACT_LABELS.search(clause))
            in_signature = (
                is_last
                and match.start() >= len(block.text) - vocab.SIGNATURE_WINDOW
                and bool(vocab.SIGNATURE_OPENERS.search(before))
            )
            if labelled:
                rule = "labelled_contact"
            elif in_signature:
                rule = "signature_contact"
            else:
                rule = "address_in_text"
            seen.add(key)
            findings.append(
                ContactFinding(
                    email=address,
                    name=_name_before(before),
                    rule=rule,
                    block=block,
                    start=match.start(),
                    line=line_of(block.text, match.start()),
                )
            )
    return findings


def _name_before(text: str) -> str:
    """The last person-shaped name in ``text``, or nothing."""
    candidates = [
        match.group("name")
        for match in vocab.PERSON_NAME.finditer(text)
        if not any(part.casefold() in vocab.NOT_A_NAME for part in match.group("name").split())
    ]
    return candidates[-1] if candidates else ""


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReferenceFinding:
    kind: str
    value: str
    block: TextBlock
    start: int
    line: str
    #: MEDIUM for a labelled reference, LOW for a bare «nr» or an ordinary link.
    strength: str


_DATE_SHAPED = re.compile(r"^\d{1,2}\.\d{1,2}\.(?:\d{2}|\d{4})\.?$|^\d{4}-\d{2}-\d{2}$")


def _looks_like_date(token: str) -> bool:
    return bool(_DATE_SHAPED.match(token))


def find_references(document: SourceDocument) -> list[ReferenceFinding]:
    """Labelled document numbers, EIS references and links, exactly as written.

    The token is kept verbatim — «1-4/26/1234-2» stays «1-4/26/1234-2» — because
    a reference somebody will type into another system is only useful exact.
    Nothing here asserts what an EIS number looks like: an EIS reference is a
    token that follows an EIS label, and a date-shaped token is refused.
    """
    findings: list[ReferenceFinding] = []
    # One token, one finding. The readers run from the most specific label to
    # the least, so «Teie … nr 1-7/26/321» is recorded once, as the reference
    # to Koda's own letter, and not again as a bare «nr».
    seen: set[str] = set()

    def add(kind: str, value: str, block: TextBlock, start: int, strength: str) -> None:
        key = value.casefold()
        if key in seen or _looks_like_date(value):
            return
        seen.add(key)
        findings.append(
            ReferenceFinding(kind, value, block, start, line_of(block.text, start), strength)
        )

    for block in document.prose_blocks:
        text = block.text
        for match in vocab.EIS_URL.finditer(text):
            add("eis_url", match.group(0).rstrip(".,;"), block, match.start(), Confidence.MEDIUM)
        for match in vocab.EIS_REFERENCE.finditer(text):
            add("eis", match.group("ref"), block, match.start("ref"), Confidence.MEDIUM)
        for match in vocab.MEIE_TEIE_REFERENCE.finditer(text):
            kind = "meie_nr" if match.group("who").casefold() == "meie" else "teie_nr"
            add(kind, match.group("ref"), block, match.start("ref"), Confidence.MEDIUM)
        for match in vocab.LABELLED_REFERENCE.finditer(text):
            label = match.group("label").casefold()
            strength = Confidence.LOW if label == "nr" else Confidence.MEDIUM
            add("document_nr", match.group("ref"), block, match.start("ref"), strength)
        for match in vocab.COM_REFERENCE.finditer(text):
            add("com", collapse(match.group(0)), block, match.start(), Confidence.MEDIUM)
        for match in vocab.EU_ACT_REFERENCE.finditer(text):
            add("eu_act", match.group(0), block, match.start(), Confidence.MEDIUM)
        for match in _LAW_REFERENCE.finditer(text):
            add(
                "riigikogu",
                f"{match.group(1)} {match.group(2).upper()}",
                block,
                match.start(),
                Confidence.MEDIUM,
            )
        urls = 0
        for match in vocab.ANY_URL.finditer(text):
            url = match.group(0).rstrip(".,;")
            if vocab.EIS_URL.match(url):
                continue
            if urls >= vocab.URL_LIMIT:
                break
            urls += 1
            add("url", url, block, match.start(), Confidence.LOW)
    return findings


#: A Riigikogu proceeding number — the one official identifier the codebase
#: already parses (app/legacy_import/opinion_sources.py). Restated rather
#: than imported so this module does not depend on the opinion archive.
_LAW_REFERENCE = re.compile(r"\b(\d{1,4})\s*(SE|OE|UA)\b")


# ---------------------------------------------------------------------------
# Lexical signals (tracks, policy areas)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalHit:
    label: str
    weight: int
    hits: int
    block: TextBlock
    start: int
    end: int

    @property
    def points(self) -> int:
        return self.weight * min(self.hits, vocab.SIGNAL_HIT_CAP)


def count_signals(document: SourceDocument, signals: tuple[vocab.Signal, ...]) -> list[SignalHit]:
    """How often each signal fires in a document, and where it first did.

    Hits are capped per signal so a term on every page of a draft is one
    piece of evidence, and two signals with the same label pool into one —
    the vocabulary spells *ülevõtmine* three ways and means one thing.
    """
    pooled: dict[str, SignalHit] = {}
    for block in document.prose_blocks:
        for signal in signals:
            hits = 0
            first: re.Match[str] | None = None
            for match in signal.regex.finditer(block.text):
                hits += 1
                if first is None:
                    first = match
                if hits >= vocab.SIGNAL_HIT_CAP:
                    break
            if first is None:
                continue
            existing = pooled.get(signal.label)
            if existing is None:
                pooled[signal.label] = SignalHit(
                    signal.label, signal.weight, hits, block, first.start(), first.end()
                )
            else:
                pooled[signal.label] = SignalHit(
                    existing.label,
                    max(existing.weight, signal.weight),
                    existing.hits + hits,
                    existing.block,
                    existing.start,
                    existing.end,
                )
    return sorted(pooled.values(), key=lambda hit: (-hit.points, hit.label))


# ---------------------------------------------------------------------------
# Organisations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrganisationPattern:
    """One name form of one catalogue organisation, ready to scan with."""

    organisation_id: object
    display: str
    form: str
    is_abbreviation: bool
    regex: re.Pattern[str] = field(repr=False, compare=False)
    #: A cheap substring pre-check before the regex runs: the first token of
    #: the folded form. Most organisations appear in no document at all.
    probe: str = ""


def fold(text: str) -> tuple[str, list[int]]:
    """Casefold and strip diacritics while remembering where each character was.

    The same rule as `app.core.text.normalize_for_matching` — casefold, NFKD,
    combining marks removed — applied character by character so a match in
    the folded text can be excerpted from the original. Whitespace is kept
    as-is; the patterns allow any run of it between tokens.
    """
    out: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        for lowered in char.casefold():
            for piece in unicodedata.normalize("NFKD", lowered):
                if unicodedata.combining(piece):
                    continue
                out.append(piece)
                origin.append(index)
    return "".join(out), origin


def organisation_pattern(
    *, organisation_id: object, display: str, form: str, is_abbreviation: bool
) -> OrganisationPattern | None:
    folded, _ = fold(form)
    tokens = folded.split()
    if not tokens:
        return None
    body = r"\s+".join(re.escape(token) for token in tokens)
    if is_abbreviation:
        # «MKM-i», «MKMi», «MKM». Case-insensitive in the folded text, so the
        # word boundary is what keeps «rm» out of «arm».
        suffix = r"(?:-?i?(?:le|lt|st|ga|ks|s|l)?)"
    else:
        suffix = vocab.ORGANISATION_SUFFIX
    regex = re.compile(rf"(?<![\w-]){body}{suffix}(?![\w-])")
    return OrganisationPattern(
        organisation_id=organisation_id,
        display=display,
        form=form,
        is_abbreviation=is_abbreviation,
        regex=regex,
        probe=tokens[0],
    )


@dataclass(frozen=True)
class OrganisationMention:
    organisation_id: object
    display: str
    matched: str
    zone: str
    is_abbreviation: bool
    block: TextBlock
    start: int
    end: int
    line: str


def find_organisation_mentions(
    document: SourceDocument, patterns: tuple[OrganisationPattern, ...]
) -> list[OrganisationMention]:
    """Every catalogue organisation named in a document, and in which zone.

    Zones, strongest first: ``sender_cue`` (after *Saatja:* / *From:*),
    ``letterhead`` (the opening block), ``signature`` (the closing block),
    ``body`` (anywhere else). The zone is evidence about *role*; the match is
    always the full name or a recorded alias, never a resemblance.
    """
    mentions: list[OrganisationMention] = []
    blocks = document.prose_blocks
    for index, block in enumerate(blocks):
        folded, origin = fold(block.text)
        is_first = index == 0
        is_last = index == len(blocks) - 1
        head_end = letterhead_end(block.text) if is_first else 0
        sign_start = signature_start(block.text) if is_last else len(block.text)
        for pattern in patterns:
            if pattern.probe not in folded:
                continue
            for match in pattern.regex.finditer(folded):
                start = origin[match.start()]
                end = origin[match.end() - 1] + 1
                if _inside_address(block.text, start, end):
                    continue
                before = block.text[max(0, start - 40) : start]
                if vocab.ORGANISATION_SENDER_CUES.search(before):
                    zone = "sender_cue"
                elif is_first and start < head_end:
                    zone = "letterhead"
                elif is_last and start >= sign_start:
                    zone = "signature"
                else:
                    zone = "body"
                mentions.append(
                    OrganisationMention(
                        organisation_id=pattern.organisation_id,
                        display=pattern.display,
                        matched=block.text[start:end],
                        zone=zone,
                        is_abbreviation=pattern.is_abbreviation,
                        block=block,
                        start=start,
                        end=end,
                        line=line_of(block.text, start),
                    )
                )
    return mentions


def letterhead_end(text: str) -> int:
    """Where a letter's opening block ends: at the salutation, or at the cap.

    The letterhead and the address block come before «Lugupeetud …»; the body
    comes after it. A ministry named in the body is a ministry discussed, not
    the ministry writing. Where no salutation is found the opening block is
    the first :data:`vocab.LETTERHEAD_WINDOW` characters.
    """
    match = vocab.LETTER_BODY_OPENS.search(text, 0, vocab.LETTERHEAD_WINDOW)
    if match is None:
        return min(len(text), vocab.LETTERHEAD_WINDOW)
    return match.start()


def signature_start(text: str) -> int:
    """Where a letter's closing block starts: at «Lugupidamisega», or near the end.

    The last closing formula in the final :data:`vocab.SIGNATURE_WINDOW`
    characters opens the signature; without one, the tail of the document is
    read as the signature so that a name and a body under it still count.
    """
    floor = max(0, len(text) - vocab.SIGNATURE_WINDOW)
    last = None
    for match in vocab.SIGNATURE_OPENERS.finditer(text, floor):
        last = match
    return last.start() if last is not None else floor


def _inside_address(text: str, start: int, end: int) -> bool:
    """Whether a match is a piece of an e-mail address or a web address.

    «kadri@kliimaministeerium.ee» contains the ministry's name and is not a
    mention of it; the address is reported by the contact reader instead.
    """
    if start > 0 and text[start - 1] in "@./":
        return True
    return end < len(text) - 1 and text[end] == "." and text[end + 1].isalpha()


def organisation_in_display_name(
    display_name: str, patterns: tuple[OrganisationPattern, ...]
) -> list[OrganisationPattern]:
    """The catalogue organisations a sender's display name spells out in full."""
    folded, _ = fold(display_name or "")
    if not folded:
        return []
    found: list[OrganisationPattern] = []
    seen: set[object] = set()
    for pattern in patterns:
        if pattern.probe in folded and pattern.regex.search(folded):
            if pattern.organisation_id in seen:
                continue
            seen.add(pattern.organisation_id)
            found.append(pattern)
    return found


def domain_label(address: str) -> str:
    """``kliimaministeerium`` from ``kadri@kliimaministeerium.ee``."""
    if "@" not in address:
        return ""
    host = address.rsplit("@", 1)[1].casefold().strip()
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        return ""
    return labels[-2]
