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
    AUTOMATIC_MATCH_CLASSES,
    OpinionConflict,
    OpinionMatchClass,
    OpinionSignal,
)
from app.legacy_import.opinion_sources import (
    ArchiveOccurrence,
    KodaDashRow,
    addressee_bodies,
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
    """(sent date, one addressee) -> rows. Built once, not per occurrence.

    The corpus is small, but an N-occurrences x N-matters scan is the shape that
    stops being small without anybody noticing (Stage-2H brief 82).

    A row is filed under *every* body its KELLELE names — the whole folded
    string as before, and each comma-separated part, and the expansion of any
    reviewed abbreviation (`addressee_bodies`). A row naming one ministry is
    reachable from a letter naming two, which is the case 163 of the 192
    `UNMATCHED` files were failing on. Filing a row under several keys cannot
    widen a lookup that already succeeded: the exact whole-string key is still
    one of them.
    """
    index: dict[tuple[datetime.date, str], list[RegisterRow]] = {}
    for row in rows:
        if row.sent_date is None or not row.addressee_is_comparable:
            continue
        for body in addressee_bodies(row.addressee_raw):
            index.setdefault((row.sent_date, body), []).append(row)
    return index


def _by_reference(rows: list[RegisterRow]) -> dict[str, RegisterRow]:
    return {row.reference: row for row in rows if row.reference}


def _law_index(rows: list[RegisterRow]) -> dict[str, list[RegisterRow]]:
    """Riigikogu proceeding number -> the register rows citing it.

    Deduplicated by Matter, because one Matter routinely has several register
    rows and a number that names one Matter twice still names one Matter.
    """
    index: dict[str, dict[uuid.UUID, RegisterRow]] = {}
    for row in rows:
        for reference in law_references(row.title):
            index.setdefault(reference, {}).setdefault(row.matter_id, row)
    return {reference: list(rows_by_matter.values()) for reference, rows_by_matter in index.items()}


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
    by_law = _law_index(data.register_rows)
    return [
        _classify_one(occurrence, data, index, by_reference, by_law)
        for occurrence in data.occurrences
    ]


def _classify_one(
    occurrence: ArchiveOccurrence,
    data: ReconciliationInput,
    index: dict[tuple[datetime.date, str], list[RegisterRow]],
    by_reference: dict[str, RegisterRow],
    by_law: dict[str, list[RegisterRow]],
) -> MatchProposal:
    placements = data.onenote_by_sha.get(occurrence.sha256, [])
    if placements:
        proposal = _from_binary(occurrence, placements, by_reference)
        if proposal is not None:
            return proposal
    # The register first, and the citation only for what it could not settle.
    #
    # The obvious order is the other way round — a proceeding number is a
    # citation and a date is not — and it is wrong, measured. Six archive files
    # carry an *exact* date, an exact addressee and a proceeding number that
    # names two Matters; a citation-first pass sees the ambiguous number,
    # refuses, and throws away the two exact signals that resolve it. One more
    # file matches 2020_69 on its own day and addressee while its proceeding
    # number appears only on a 2021 Matter, and citation-first files it ten
    # months away from the letter.
    #
    # So the rule is not "citations outrank dates"; it is **more independent
    # exact signals outrank fewer**. The register route already requires three,
    # and where it produces one of those, nothing here may overrule it. The
    # citation route exists for the corpus the register route cannot see at
    # all: a letter whose VÄLJA is months from its own date, or whose addressee
    # the register spells in a way no reviewed alias covers.
    from_register = _from_register(occurrence, data, index)
    if from_register.match_class in AUTOMATIC_MATCH_CLASSES:
        return from_register

    cited = _from_citation(occurrence, by_law)
    if cited is None:
        return from_register
    if cited.match_class in AUTOMATIC_MATCH_CLASSES:
        return cited

    # Neither route reached an automatic class, so this is a queue entry and
    # the only question is which explanation is worth more to the reviewer.
    #
    # A citation that named nothing usable still beats "no register row was
    # sent on that day to that addressee", which is what an UNMATCHED file says
    # and which tells a reviewer nowhere to look. Where the register *did*
    # name a candidate, its proposal is kept: it carries the date and the
    # addressee as well as whatever the title shared, and dropping that to
    # report a proceeding number would be less evidence, not more.
    if from_register.match_class == OpinionMatchClass.UNMATCHED:
        return cited
    return from_register


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


def _from_citation(
    occurrence: ArchiveOccurrence,
    by_law: dict[str, list[RegisterRow]],
) -> MatchProposal | None:
    """Class C: the letter and the register cite the same parliamentary file.

    A Riigikogu proceeding number is the one thing in this corpus that both
    sources wrote down *as an identifier*. `662 SE` is not a word that happens
    to be shared and it is not a distance between two titles — it names a file
    on the Riigikogu's own docket, and two documents citing it are about the
    same legislative matter.

    That is why this route sits above the register's date-and-addressee route
    rather than inside it as a third signal: a citation does not need a date to
    mean something, and the corpus contains letters written months apart about
    one proceeding — an archive file dated 2020-08-12 citing `190 SE` against a
    register row whose VÄLJA is 2021-03-16, because Koda wrote twice and the
    register keeps the last dispatch. The date-first route cannot see that pair
    at all; this one can, and says so with a date gap rather than pretending
    the days agree.

    **It is never enough on its own.** 25 of the register's 165 distinct
    proceeding numbers name more than one Matter — a bill split, or two
    Chamber files opened on one proceeding — so a number resolving to several
    Matters is a question, not an answer. And a number resolving to exactly one
    Matter still has to be corroborated by the addressee or the date, because a
    proceeding number appearing in a title is not proof that *this* letter is
    the one the register row recorded. Both refusals are returned as
    REVIEW_REQUIRED with the competing count intact, never as a pick.

    Returns ``None`` when the filename cites nothing, which hands the
    occurrence to the register route unchanged.
    """
    references = law_references(occurrence.filename_title)
    if not references:
        return None

    matched: dict[uuid.UUID, RegisterRow] = {}
    for reference in references:
        for row in by_law.get(reference, []):
            matched.setdefault(row.matter_id, row)
    if not matched:
        return None

    shared = sorted(reference for reference in references if by_law.get(reference))
    proposal = MatchProposal(
        sha256=occurrence.sha256,
        relative_path=occurrence.relative_path,
        match_class=OpinionMatchClass.REVIEW_REQUIRED,
        signals=[OpinionSignal.EXACT_LAW_REFERENCE],
        competing_matter_count=len(matched),
    )
    if len(matched) > 1:
        proposal.conflicts.append(OpinionConflict.MULTIPLE_SOURCE_ROWS)
        proposal.explanation = (
            f"Õigusakti viide {', '.join(shared)} esineb {len(matched)} teemal. "
            "Viide üksi ei ütle, milline neist on see kiri."
        )
        return proposal

    row = next(iter(matched.values()))
    proposal.matter_id = row.matter_id
    proposal.excel_reference = row.reference
    proposal.excel_sent_date = row.sent_date
    proposal.excel_addressee_raw = row.addressee_raw

    # Corroboration. Either signal is independent of the citation: the
    # addressee comes from the register's own KELLELE, the date from its VÄLJA.
    recipients_agree = bool(
        row.addressee_is_comparable
        and addressee_bodies(occurrence.filename_recipient) & addressee_bodies(row.addressee_raw)
    )
    gap: int | None = None
    if row.sent_date is not None and occurrence.filename_date is not None:
        gap = abs((occurrence.filename_date - row.sent_date).days)

    if recipients_agree:
        proposal.signals.append(OpinionSignal.EXACT_RECIPIENT)
    if gap == 0:
        proposal.signals.append(OpinionSignal.EXACT_SENT_DATE)
    elif gap == 1:
        proposal.signals.append(OpinionSignal.SENT_DATE_WITHIN_ONE_DAY)

    if not recipients_agree and (gap is None or gap > 1):
        proposal.explanation = (
            f"Õigusakti viide {', '.join(shared)} osutab täpselt ühele teemale, kuid ei adressaat "
            "ega kuupäev seda ei kinnita. Viide üksi ei ole identiteet."
        )
        return proposal

    proposal.match_class = OpinionMatchClass.EXACT_LAW_REFERENCE_MATTER
    corroboration = "sama adressaat" if recipients_agree else f"kuupäevade vahe {gap} päeva"
    proposal.explanation = (
        f"Mõlemad allikad viitavad samale menetlusele ({', '.join(shared)}), mis registris "
        f"osutab täpselt ühele teemale. Kinnitab {corroboration}."
    )
    if gap is not None and gap > 1:
        # Said out loud rather than left to be inferred from two dates: this is
        # a link about subject matter, and it is not evidence of a dispatch on
        # the archive file's own day.
        proposal.explanation += (
            f" Registri VÄLJA on {gap} päeva kaugusel — seos käib teema, mitte väljasaatmise kohta."
        )
    return proposal


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

    bodies = addressee_bodies(occurrence.filename_recipient)
    recipient = fold(occurrence.filename_recipient)
    exact_rows = _rows_on(index, occurrence.filename_date, bodies)
    near_rows = _within_one_day(index, occurrence.filename_date, bodies)

    if len(exact_rows) == 1:
        return _single_exact(occurrence, proposal, exact_rows[0])

    if len(exact_rows) > 1:
        return _resolve_exact_tie(occurrence, proposal, exact_rows)

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


def _adopt_row(proposal: MatchProposal, row: RegisterRow) -> None:
    """Copy the register row a proposal has settled on into the proposal.

    Extracted because two routes now settle on a row — one exact row, and an
    exact tie a single third signal resolved — and a second copy of these four
    assignments is how the two would drift apart.
    """
    proposal.matter_id = row.matter_id
    proposal.excel_reference = row.reference
    proposal.excel_sent_date = row.sent_date
    proposal.excel_addressee_raw = row.addressee_raw


def _resolve_exact_tie(
    occurrence: ArchiveOccurrence, proposal: MatchProposal, rows: list[RegisterRow]
) -> MatchProposal:
    """Several register rows share the date and the addressee. Ask the third signal.

    Refusing here the moment a second row appeared was wrong, and wrong in a
    way that got worse as the matcher got better. `addressee_bodies` made a row
    whose KELLELE names three ministries reachable from a letter naming one of
    them — correct, and the reason 76 files stopped being `UNMATCHED`. But a row
    that becomes *comparable* is not thereby a *competitor*: it arrives holding
    two signals, the date and the addressee, and the refusal then discarded a
    row holding three. Widening what the matcher can see demoted a match it had
    already earned, which inverts this module's one ordering rule — more
    independent exact signals outrank fewer.

    So the tie is put to the same question `_single_exact` asks, once per
    competing Matter, and the answer is counted rather than scored:

    * **exactly one** row carries a third signal — it is the only row with three
      exact signals against the others' two, and it wins as `STRICT_MULTI_SIGNAL`;
    * **none** does — every row has the same two signals and nothing separates
      them, which is the case this branch was written for;
    * **more than one** does — each has three, they are level on the only
      evidence this matcher accepts, and a person decides.

    Deliberately binary. Two shared title tokens do not beat one, a law
    reference does not beat a title token, and no count, distance, frequency or
    confidence is compared anywhere in here: `_third_signal` already ranks the
    two kinds *within* a row, and borrowing that to rank rows *against each
    other* would be a new hierarchy invented to break a tie rather than
    evidence that the tie is broken.

    Only exact same-day rows reach this. A one-day gap stays review evidence
    (`_within_one_day` below), because the thing being resolved here is which
    of several equally-dated rows the letter is — not whether a differently
    dated row is the same letter at all.
    """
    proposal.signals += [OpinionSignal.EXACT_SENT_DATE, OpinionSignal.EXACT_RECIPIENT]
    # Retained on both paths: it says how many Matters competed, which stays
    # true — and worth reading — after one of them won.
    proposal.competing_matter_count = len(rows)

    qualifying = [
        (row, signal) for row in rows if (signal := _third_signal(occurrence, row)) is not None
    ]

    if len(qualifying) != 1:
        proposal.match_class = OpinionMatchClass.REVIEW_REQUIRED
        proposal.conflicts.append(OpinionConflict.MULTIPLE_SOURCE_ROWS)
        proposal.explanation = (
            f"{len(rows)} registri rida saadeti samal päeval samale adressaadile. "
            + (
                "Ükski neist ei jaga faili pealkirjaga eristavat sõna ega õigusakti viidet."
                if not qualifying
                else f"Neist {len(qualifying)} jagab eristavat sõna või õigusakti viidet, "
                "seega valiku teeb inimene."
            )
        )
        return proposal

    row, third = qualifying[0]
    _adopt_row(proposal, row)
    proposal.signals.append(third)
    proposal.match_class = OpinionMatchClass.STRICT_MULTI_SIGNAL
    proposal.explanation = (
        f"{len(rows)} registri rida saadeti samal päeval samale adressaadile, kuid ainult "
        "ühel neist on kolmas sõltumatu täpne signaal: "
        + (
            "sama õigusakti viide."
            if third == OpinionSignal.EXACT_LAW_REFERENCE
            else "sama eristav pealkirjasõna."
        )
    )
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
    _adopt_row(proposal, row)
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


def _rows_on(
    index: dict[tuple[datetime.date, str], list[RegisterRow]],
    day: datetime.date,
    bodies: frozenset[str],
) -> list[RegisterRow]:
    """Every register row sent on ``day`` to any body the letter names.

    Deduplicated by Matter, because both sides are now *sets* of names: a row
    whose KELLELE reads "Siseministeerium, HTM" is filed under three keys, and
    a letter addressed the same way would otherwise find it three times and
    report three competing rows where there is one.
    """
    seen: dict[uuid.UUID, RegisterRow] = {}
    for body in bodies:
        for row in index.get((day, body), []):
            seen.setdefault(row.matter_id, row)
    return list(seen.values())


def _within_one_day(
    index: dict[tuple[datetime.date, str], list[RegisterRow]],
    day: datetime.date,
    bodies: frozenset[str],
) -> list[RegisterRow]:
    seen: dict[uuid.UUID, RegisterRow] = {}
    for offset in (-1, 0, 1):
        for row in _rows_on(index, day + datetime.timedelta(days=offset), bodies):
            seen.setdefault(row.matter_id, row)
    return list(seen.values())
