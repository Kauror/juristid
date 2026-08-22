"""A second pass that reads the letters, not their filenames.

The first pass had three sources: what the archive called a file, what the
register row said, and where OneNote put a byte-identical copy. Roughly two
thirds of the corpus survived all three and stayed unmatched, and the reason is
plain — a filename is what somebody typed when saving a copy, and for hundreds
of these letters it is all the first pass had.

The letter itself is a fourth source, and a genuinely independent one. Its
dateline is what Koda wrote, its addressee block is who Koda addressed, and the
proceeding numbers in its body are the ones its author cited. That independence
is the whole value: a filename agreeing with a filename is one source agreeing
with itself, whereas a *body* agreeing with a register row is two.

The rules of the first pass hold here unchanged, and one is added:

* **named exact signals, never a score.** Every signal below is an equality
  test between two written values. Nothing here computes a distance, a
  similarity, a threshold or a confidence, and no arithmetic decides anything
  (Stage-2H brief 14, 16);
* **nothing files itself.** `CONTENT_MULTI_SIGNAL` is not in
  ``AUTOMATIC_MATCH_CLASSES``. This pass has never run against the real corpus
  — extraction is blocked wherever the real archive lives — so its precision is
  not merely unproven, it is unmeasured. The bar for a new automatic class is
  measured precision on real data, and until somebody has that number every
  proposal here goes in front of a person (docs/adr/0023);
* **it proposes only where the first pass found nothing.** A letter that
  already has a live matched candidate is not re-litigated from its text: a
  second opinion arriving beside a first, with no way to tell which is newer,
  is how a review queue becomes unreadable.

The pass writes candidates and nothing else. It creates no Submission, no
Matter, no link, and it never touches a row a person has decided.
"""

from __future__ import annotations

import datetime
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.utils import timezone

from app.legacy_import.opinion_binary import OpinionArchiveBinary
from app.legacy_import.opinion_enums import (
    ArchiveTextState,
    OpinionCandidateState,
    OpinionConflict,
    OpinionMatchClass,
    OpinionSignal,
)
from app.legacy_import.opinion_match import RegisterRow
from app.legacy_import.opinion_sources import fold, law_references

#: How much of a letter is read for its dateline and addressee block. Both live
#: at the top of a Koda letter; scanning the whole body would find the date some
#: other document was written on, quoted in a paragraph, and call it this
#: letter's date.
HEADER_CHARACTERS = 1500

#: Two independent content signals before anything is proposed. One alone is
#: routinely coincidental: a ministry is named in half the corpus, and a single
#: date matches whatever else was sent that day.
MINIMUM_SIGNALS = 2

#: Recorded on the batch this pass opens, so a candidate can always name the
#: run that produced it and the version of the rules it ran under.
MATCHER_VERSION = "opinion-content-match/1"

MONTHS = {
    "jaanuar": 1,
    "veebruar": 2,
    "märts": 3,
    "aprill": 4,
    "mai": 5,
    "juuni": 6,
    "juuli": 7,
    "august": 8,
    "september": 9,
    "oktoober": 10,
    "november": 11,
    "detsember": 12,
}

NUMERIC_DATE = re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\b")
NAMED_DATE = re.compile(
    r"\b(\d{1,2})\.?\s+(" + "|".join(MONTHS) + r")[a-zõäöü]*\s+(\d{4})", re.IGNORECASE
)


@dataclass
class ContentMatchReport:
    """Aggregates. Never a title, never a line of a letter."""

    considered: int = 0
    no_text: int = 0
    already_matched: int = 0
    proposed: int = 0
    conflicted: int = 0
    no_signal: int = 0
    findings: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        rows = [
            ("vaadatud", self.considered),
            ("sisu puudub", self.no_text),
            ("juba seotud", self.already_matched),
            ("uusi ettepanekuid", self.proposed),
            ("vastuolulisi", self.conflicted),
            ("signaalita", self.no_signal),
        ]
        lines = [f"  {label:<32} {value:>12}" for label, value in rows]
        lines.extend(f"  leid: {finding}" for finding in self.findings)
        return "\n".join(lines)


@dataclass(frozen=True)
class ContentEvidence:
    """What one letter's own text says, reduced to comparable values."""

    dates: frozenset[datetime.date]
    laws: frozenset[str]
    header: str

    @property
    def is_empty(self) -> bool:
        return not (self.dates or self.laws or self.header)


def read_evidence(body: str) -> ContentEvidence:
    """Pull the comparable facts out of one letter.

    Dates and proceeding numbers come from the header; the folded header itself
    is kept so an addressee can be tested for containment rather than parsed
    out of a block whose layout varies by decade.
    """
    header = body[:HEADER_CHARACTERS]
    return ContentEvidence(
        dates=frozenset(_dates_in(header)),
        laws=law_references(header) | law_references(body),
        header=fold(header),
    )


def _dates_in(text: str) -> set[datetime.date]:
    found: set[datetime.date] = set()
    for match in NUMERIC_DATE.finditer(text):
        day, month, year = (int(part) for part in match.groups())
        date = _safe_date(year, month, day)
        if date is not None:
            found.add(date)
    for match in NAMED_DATE.finditer(text):
        day = int(match.group(1))
        month = MONTHS[match.group(2).lower()]
        date = _safe_date(int(match.group(3)), month, day)
        if date is not None:
            found.add(date)
    return found


def _safe_date(year: int, month: int, day: int) -> datetime.date | None:
    try:
        return datetime.date(year, month, day)
    except ValueError:
        # 31.02 in a scanned letter is an OCR-free parsing artefact, not a date.
        return None


def signals_for(evidence: ContentEvidence, row: RegisterRow) -> list[str]:
    """The exact agreements between one letter's text and one register row."""
    signals: list[str] = []
    if row.sent_date is not None and row.sent_date in evidence.dates:
        signals.append(OpinionSignal.CONTENT_EXACT_DATE)
    if evidence.laws and (evidence.laws & law_references(row.title)):
        signals.append(OpinionSignal.CONTENT_EXACT_LAW_REFERENCE)
    if row.addressee_is_comparable:
        addressee = fold(row.addressee_raw)
        # Long enough that containment is a statement rather than a coincidence.
        # Estonian ministry names run to twenty characters; a five-character
        # fragment would match half the corpus.
        if len(addressee) >= 8 and addressee in evidence.header:
            signals.append(OpinionSignal.CONTENT_EXACT_ADDRESSEE)
    return signals


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


def plan_content_matches() -> ContentMatchReport:
    """Say what a second pass would propose, writing nothing."""
    return _run(commit=False)


def apply_content_matches() -> ContentMatchReport:
    """Write the second pass's proposals into the review queue."""
    return _run(commit=True)


def _run(*, commit: bool) -> ContentMatchReport:
    from app.legacy_import.opinion_plan import load_register_rows

    report = ContentMatchReport()
    rows = [row for row in load_register_rows() if row.matter_id is not None]
    if not rows:
        report.findings.append("registri ridu ei ole — teine läbimine ei tee midagi")
        return report

    # No `prefetch_related` here: `_write` reads the occurrences of the few
    # binaries that actually produce a proposal, and prefetching them for all
    # 759 would load the whole catalogue to answer a question about a handful.
    binaries = OpinionArchiveBinary.objects.select_related("text").order_by("pk")
    batch: Any = None
    for binary in binaries.iterator():
        report.considered += 1
        text = getattr(binary, "text", None)
        if text is None or text.state != ArchiveTextState.DONE or not text.body:
            report.no_text += 1
            continue
        if _already_matched(binary):
            report.already_matched += 1
            continue

        evidence = read_evidence(text.body)
        if evidence.is_empty:
            report.no_signal += 1
            continue

        by_row = _score_free_grouping(evidence, rows)
        if not by_row:
            report.no_signal += 1
            continue
        if len(by_row) > 1:
            # Several register rows corroborated by the letter's own text. Not
            # a tie to be broken — the letter genuinely mentions more than one
            # matter, which is a fact a person should see rather than one an
            # importer should resolve.
            report.conflicted += 1
            if commit:
                batch = batch or _open_batch()
                _write(binary, batch, None, [], [OpinionConflict.MULTIPLE_SOURCE_ROWS], len(by_row))
            continue

        row, signals = next(iter(by_row.items()))
        report.proposed += 1
        if commit:
            batch = batch or _open_batch()
            _write(binary, batch, row, signals, [], 1)
    return report


def _open_batch() -> Any:
    """One batch per pass that writes something, opened only if it does.

    A candidate has to be able to name the run that produced it — that is what
    makes a queue row auditable a year later — and this pass is a run like any
    other. It is opened lazily so a pass that proposes nothing leaves nothing
    behind.

    The archive hash is the binaries' own provenance when they all came from
    one snapshot, and left empty when they did not: this pass reads held bytes
    rather than an archive, and naming one of several snapshots would be a
    guess dressed as provenance.
    """
    from app.legacy_import.opinion_archive import OpinionArchiveBatch

    sources = set(
        OpinionArchiveBinary.objects.values_list("source_archive_sha256", flat=True).distinct()
    )
    return OpinionArchiveBatch.objects.create(
        archive_sha256=sources.pop() if len(sources) == 1 else "",
        importer_version=MATCHER_VERSION,
        started_at=timezone.now(),
        notes=("Teine läbimine: ettepanekud kirjade endi tekstist. Ükski neist ei ole automaatne."),
    )


def _score_free_grouping(
    evidence: ContentEvidence, rows: list[RegisterRow]
) -> dict[RegisterRow, list[str]]:
    """Register rows this letter's text corroborates, and how.

    A grouping, not a ranking. Every row that clears the same named bar is
    kept, and if more than one does, that is the answer: two candidates, not a
    winner. Nothing here compares one row's evidence with another's, which is
    what keeps "best match wins" out of the module.
    """
    grouped: dict[RegisterRow, list[str]] = {}
    for row in rows:
        signals = signals_for(evidence, row)
        if len(signals) >= MINIMUM_SIGNALS:
            grouped[row] = signals
    return grouped


def _already_matched(binary: OpinionArchiveBinary) -> bool:
    """Whether the first pass, or a person, has already answered this letter."""
    from app.legacy_import.opinion_archive import OpinionMatchCandidate

    return (
        OpinionMatchCandidate.objects.filter(item__binary=binary, matter__isnull=False)
        .exclude(state=OpinionCandidateState.SUPERSEDED)
        .exists()
    )


@transaction.atomic
def _write(
    binary: OpinionArchiveBinary,
    batch: Any,
    row: RegisterRow | None,
    signals: list[str],
    conflicts: list[str],
    competing: int,
) -> None:
    """One candidate per occurrence, in the queue, waiting for a person.

    Idempotent by the same uniqueness key the first pass uses, so rerunning the
    second pass after another extraction changes nothing that has not changed.
    """
    from app.legacy_import.opinion_archive import OpinionArchiveItem, OpinionMatchCandidate

    match_class = (
        OpinionMatchClass.CONFLICT if row is None else OpinionMatchClass.CONTENT_MULTI_SIGNAL
    )
    matter_id: Any = row.matter_id if row is not None else None
    explanation = (
        "Teine läbimine luges kirja enda teksti. "
        + (
            f"Registri rida {row.reference} kattub: {', '.join(signals)}."
            if row is not None
            else f"{competing} registri rida saab kirja tekstist sama tuge."
        )
        + " Ükski neist ei loo arvamust ilma ülevaatuseta."
    )
    for item in OpinionArchiveItem.objects.filter(binary=binary):
        OpinionMatchCandidate.objects.get_or_create(
            item=item,
            matter_id=matter_id,
            match_class=match_class,
            defaults={
                "batch": batch,
                "signals": list(signals),
                "conflicts": list(conflicts),
                "excel_reference": (row.reference if row is not None else "")[:40],
                "excel_sent_date": row.sent_date if row is not None else None,
                "excel_addressee_raw": (row.addressee_raw if row is not None else "")[:400],
                "competing_matter_count": competing,
                "explanation": explanation,
            },
        )


def content_coverage() -> dict[str, int]:
    """How much of the archive the second pass can even look at."""
    from app.legacy_import.opinion_binary import OpinionArchiveText

    counts: dict[str, int] = defaultdict(int)
    counts["binaries"] = OpinionArchiveBinary.objects.count()
    for state, _ in ArchiveTextState.choices:
        counts[state] = OpinionArchiveText.objects.filter(state=state).count()
    return dict(counts)
