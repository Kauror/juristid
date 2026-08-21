"""Deciding which Matter an archived opinion belongs to.

The rule the whole module is built on: **evidence classes, not a score.** A
number can be tuned until it produces the coverage somebody wanted; a class can
be argued with. Every candidate carries the individual signals that produced it
and the individual conflicts that held it back, and only three classes may be
applied without a person looking at them (Stage-2H brief 14, 15, 16).

What the real corpus does to each route, measured before this was written:

* **Exact binary through OneNote** is the strongest edge and the rarest one.
  Ten of 767 archive files exist byte-for-byte in the OneNote corpus, and only
  one of those sits on a page the Stage-2D audit tied to exactly one register
  reference. The archive's PDFs are re-exports, not the attachments. The route
  stays first because when it fires it is identity; it is simply not the route
  that will carry the corpus.

* **Register date plus addressee** picks a unique row for 291 files — and is
  *not* identity. Two different matters can share a day and a ministry, and the
  corpus contains such a pair. So a third exact signal is required: a shared
  Riigikogu proceeding number, or a shared distinctive title word.

* **A one-day date difference is a suggestion.** The register's VÄLJA falls on
  the letter's own date 326 times and the next day 227 times. Widening the
  window to one day raises unique candidates from 291 to 462, which is exactly
  why it must not be automatic: the extra 171 are the ones a person should see.

Nothing here calls an LLM, computes an edit distance, or accepts a similarity
threshold. Filename and title similarity may *suggest*; they may never file
(Stage-2H brief 16, 83).
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field

from app.legacy_import.opinion_enums import (
    OpinionConflict,
    OpinionMatchClass,
    OpinionSignal,
)
from app.legacy_import.opinion_sources import (
    ArchiveOccurrence,
    KodaDashRow,
    fold,
    law_references,
    title_tokens,
)

#: The first year the register's counterparty column means *addressee*. Before
#: it, KELLELT is the sender, and comparing it to an opinion's recipient would
#: compare the ministry that wrote to Koda with the ministry Koda wrote to
#: (Stage-2H brief 11).
ADDRESSEE_ERA_FIRST_YEAR = 2020


@dataclass(frozen=True)
class RegisterRow:
    """One register Matter, as the reconciliation needs to see it.

    Built from ``MatterSourceReference.source_row_raw`` through the era
    contract, so the caller never has to remember which letter VÄLJA is in a
    given year — and so a year whose counterparty column is the *sender* cannot
    accidentally supply a recipient.
    """

    matter_id: uuid.UUID
    reference: str
    year: int
    title: str
    sent_date: datetime.date | None
    addressee_raw: str
    counterparty_direction: str

    @property
    def addressee_is_comparable(self) -> bool:
        return self.counterparty_direction == "addressee" and self.year >= ADDRESSEE_ERA_FIRST_YEAR


@dataclass(frozen=True)
class OneNotePlacement:
    """Where an exact binary sits in the OneNote corpus, and what claims it."""

    page_key: str
    page_title: str
    section: str
    block_ordinal: int
    matter_ids: tuple[uuid.UUID, ...]
    excel_references: tuple[str, ...]


@dataclass
class MatchProposal:
    """One occurrence's proposed Matter, with the reasoning intact."""

    sha256: str
    relative_path: str
    match_class: str
    matter_id: uuid.UUID | None = None
    excel_reference: str = ""
    excel_sent_date: datetime.date | None = None
    excel_addressee_raw: str = ""
    onenote_page_key: str = ""
    onenote_page_title: str = ""
    onenote_section: str = ""
    onenote_block_ordinal: int | None = None
    signals: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    competing_matter_count: int = 0
    explanation: str = ""
    #: Filled in by the apply once the candidate row exists, so a Submission
    #: written in the same run can point back at the proposal that justified it.
    candidate_id: uuid.UUID | None = None


@dataclass
class ReconciliationInput:
    """Everything the classifier is allowed to look at."""

    occurrences: list[ArchiveOccurrence]
    kodadash_by_sha: dict[str, KodaDashRow]
    register_rows: list[RegisterRow]
    onenote_by_sha: dict[str, list[OneNotePlacement]]


def _register_index(rows: list[RegisterRow]) -> dict[tuple[datetime.date, str], list[RegisterRow]]:
    """(sent date, folded addressee) -> rows. Built once, not per occurrence.

    The corpus is small, but an N-occurrences x N-matters scan is the shape that
    stops being small without anybody noticing (Stage-2H brief 82).
    """
    index: dict[tuple[datetime.date, str], list[RegisterRow]] = {}
    for row in rows:
        if row.sent_date is None or not row.addressee_is_comparable:
            continue
        key = (row.sent_date, fold(row.addressee_raw))
        index.setdefault(key, []).append(row)
    return index


def _by_reference(rows: list[RegisterRow]) -> dict[str, RegisterRow]:
    return {row.reference: row for row in rows if row.reference}


def _third_signal(occurrence: ArchiveOccurrence, row: RegisterRow) -> str | None:
    """An exact token shared by the archive's title and the register's.

    A Riigikogu proceeding number is a citation and outranks a word. A shared
    seven-character-or-longer Estonian compound is the weaker of the two and
    still exact: it is a token both sources wrote, not a distance between them.
    """
    shared_law = law_references(occurrence.filename_title) & law_references(row.title)
    if shared_law:
        return OpinionSignal.EXACT_LAW_REFERENCE
    if title_tokens(occurrence.filename_title) & title_tokens(row.title):
        return OpinionSignal.EXACT_TITLE_TOKEN
    return None


def classify(data: ReconciliationInput) -> list[MatchProposal]:
    """One proposal per archive occurrence, in evidence order."""
    index = _register_index(data.register_rows)
    by_reference = _by_reference(data.register_rows)
    return [_classify_one(occurrence, data, index, by_reference) for occurrence in data.occurrences]


def _classify_one(
    occurrence: ArchiveOccurrence,
    data: ReconciliationInput,
    index: dict[tuple[datetime.date, str], list[RegisterRow]],
    by_reference: dict[str, RegisterRow],
) -> MatchProposal:
    placements = data.onenote_by_sha.get(occurrence.sha256, [])
    if placements:
        proposal = _from_binary(occurrence, placements, by_reference)
        if proposal is not None:
            return proposal
    return _from_register(occurrence, data, index)


def _from_binary(
    occurrence: ArchiveOccurrence,
    placements: list[OneNotePlacement],
    by_reference: dict[str, RegisterRow],
) -> MatchProposal | None:
    """Classes A and B: the archive file exists byte-for-byte in OneNote.

    A page that several Matters claim does not get a decision made for it. The
    same PDF legitimately sits in more than one case file — a form letter, a
    joint position circulated twice — and picking the first one would file it
    where nobody looks for it again (Stage-2H brief 15, 31).
    """
    first = placements[0]
    matters = {matter_id for placement in placements for matter_id in placement.matter_ids}
    references = sorted({ref for placement in placements for ref in placement.excel_references})

    base = MatchProposal(
        sha256=occurrence.sha256,
        relative_path=occurrence.relative_path,
        match_class=OpinionMatchClass.REVIEW_REQUIRED,
        onenote_page_key=first.page_key,
        onenote_page_title=first.page_title,
        onenote_section=first.section,
        onenote_block_ordinal=first.block_ordinal,
        signals=[OpinionSignal.EXACT_BINARY_ONENOTE],
        competing_matter_count=len(matters),
    )
    if references:
        base.signals.append(OpinionSignal.EXACT_ONENOTE_PAGE)
        base.excel_reference = references[0] if len(references) == 1 else ""

    if len(matters) == 1:
        base.match_class = OpinionMatchClass.EXACT_BINARY_MATTER
        base.matter_id = next(iter(matters))
        row = by_reference.get(base.excel_reference)
        if row is not None:
            base.excel_sent_date = row.sent_date
            base.excel_addressee_raw = row.addressee_raw
        base.explanation = (
            "Arhiivi fail on baidi täpsusega OneNote'i lehel, mis kuulub täpselt ühele teemale."
        )
        return base

    if len(matters) > 1:
        base.match_class = OpinionMatchClass.EXACT_BINARY_MULTI_MATTER
        base.conflicts = [OpinionConflict.MULTIPLE_MATTER_BINARY]
        base.explanation = (
            f"Sama bait esineb lehel, mida taotleb {len(matters)} teemat. Valiku teeb ülevaataja."
        )
        return base

    # The binary is in OneNote but the page belongs to nothing yet. That is
    # still real evidence about where the file sat, so it is kept and queued
    # rather than thrown away and re-derived from the filename.
    base.explanation = (
        "Arhiivi fail on baidi täpsusega OneNote'i lehel, kuid leht ei ole ühegi teemaga seotud."
    )
    if references:
        base.explanation += f" Auditi viide: {', '.join(references)}."
    return base


def _from_register(
    occurrence: ArchiveOccurrence,
    data: ReconciliationInput,
    index: dict[tuple[datetime.date, str], list[RegisterRow]],
) -> MatchProposal:
    """Classes D through G: no binary edge, so the register has to carry it."""
    proposal = MatchProposal(
        sha256=occurrence.sha256,
        relative_path=occurrence.relative_path,
        match_class=OpinionMatchClass.UNMATCHED,
    )
    if occurrence.sha256 in data.kodadash_by_sha:
        proposal.signals.append(OpinionSignal.EXACT_KODADASH_SOURCE_FILE)

    if occurrence.filename_date is None:
        proposal.explanation = (
            "Failinimi ei järgi arhiivi nimetamisreeglit, seega kuupäeva ega saajat ei loeta."
        )
        return proposal

    if occurrence.filename_date.year < ADDRESSEE_ERA_FIRST_YEAR:
        # The register's counterparty column is the *sender* in this era, so
        # the one comparison that would otherwise be available is meaningless.
        proposal.conflicts.append(OpinionConflict.EXCEL_DIRECTION_NOT_COMPARABLE)
        proposal.match_class = OpinionMatchClass.REVIEW_REQUIRED
        proposal.explanation = (
            "Enne 2020. aastat on registri vastaspool saatja, mitte adressaat, "
            "seega saaja võrdlust ei tehta."
        )
        return proposal

    recipient = fold(occurrence.filename_recipient)
    exact_rows = index.get((occurrence.filename_date, recipient), [])
    near_rows = _within_one_day(index, occurrence.filename_date, recipient)

    if len(exact_rows) == 1:
        return _single_exact(occurrence, proposal, exact_rows[0])

    if len(exact_rows) > 1:
        proposal.match_class = OpinionMatchClass.REVIEW_REQUIRED
        proposal.signals += [OpinionSignal.EXACT_SENT_DATE, OpinionSignal.EXACT_RECIPIENT]
        proposal.conflicts.append(OpinionConflict.MULTIPLE_SOURCE_ROWS)
        proposal.competing_matter_count = len(exact_rows)
        proposal.explanation = (
            f"{len(exact_rows)} registri rida saadeti samal päeval samale adressaadile."
        )
        return proposal

    if len(near_rows) == 1:
        row = near_rows[0]
        proposal.match_class = OpinionMatchClass.REVIEW_REQUIRED
        proposal.matter_id = row.matter_id
        proposal.excel_reference = row.reference
        proposal.excel_sent_date = row.sent_date
        proposal.excel_addressee_raw = row.addressee_raw
        proposal.signals += [OpinionSignal.SENT_DATE_WITHIN_ONE_DAY, OpinionSignal.EXACT_RECIPIENT]
        third = _third_signal(occurrence, row)
        if third:
            proposal.signals.append(third)
        proposal.competing_matter_count = 1
        proposal.explanation = (
            "Registri VÄLJA erineb faili enda kuupäevast ühe päeva. See on korpuses tavaline "
            "ja tähendab enamasti sama kirja, kuid ühe päeva vahe ei ole identiteet."
        )
        return proposal

    if len(near_rows) > 1:
        proposal.match_class = OpinionMatchClass.REVIEW_REQUIRED
        proposal.signals.append(OpinionSignal.EXACT_RECIPIENT)
        proposal.conflicts.append(OpinionConflict.MULTIPLE_SOURCE_ROWS)
        proposal.competing_matter_count = len(near_rows)
        proposal.explanation = (
            f"{len(near_rows)} registri rida sobib ühe päeva täpsusega samale adressaadile."
        )
        return proposal

    proposal.explanation = (
        "Ühtki registri rida ei saadetud sellel või kõrvalpäeval sellele adressaadile."
    )
    if not recipient:
        proposal.conflicts.append(OpinionConflict.UNKNOWN_RECIPIENT)
    return proposal


def _single_exact(
    occurrence: ArchiveOccurrence, proposal: MatchProposal, row: RegisterRow
) -> MatchProposal:
    """Exactly one register row shares the date and the addressee.

    That is two exact signals, and the corpus proves two is not enough: it
    contains a file whose date and ministry match a register row about an
    entirely different subject. The third signal is what separates the 253
    that may be filed from the 38 that must not (Stage-2H brief 15 D, 16).
    """
    proposal.matter_id = row.matter_id
    proposal.excel_reference = row.reference
    proposal.excel_sent_date = row.sent_date
    proposal.excel_addressee_raw = row.addressee_raw
    proposal.signals += [OpinionSignal.EXACT_SENT_DATE, OpinionSignal.EXACT_RECIPIENT]
    proposal.competing_matter_count = 1

    third = _third_signal(occurrence, row)
    if third is None:
        proposal.match_class = OpinionMatchClass.REVIEW_REQUIRED
        proposal.conflicts.append(OpinionConflict.TITLE_CONFLICT)
        proposal.explanation = (
            "Kuupäev ja adressaat langevad kokku, kuid pealkirjad ei jaga ühtki eristavat "
            "sõna ega õigusakti viidet. Kaks signaali ei ole identiteet."
        )
        return proposal

    proposal.signals.append(third)
    proposal.match_class = OpinionMatchClass.STRICT_MULTI_SIGNAL
    proposal.explanation = (
        "Kolm sõltumatut täpset signaali: sama väljasaatmise kuupäev, sama adressaat ja "
        + (
            "sama õigusakti viide."
            if third == OpinionSignal.EXACT_LAW_REFERENCE
            else "sama eristav pealkirjasõna."
        )
    )
    return proposal


def _within_one_day(
    index: dict[tuple[datetime.date, str], list[RegisterRow]],
    day: datetime.date,
    recipient: str,
) -> list[RegisterRow]:
    rows: list[RegisterRow] = []
    for offset in (-1, 0, 1):
        rows.extend(index.get((day + datetime.timedelta(days=offset), recipient), []))
    return rows
