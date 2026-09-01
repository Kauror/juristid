"""What the opinion-archive import will do, decided before it does any of it.

The plan is the object ``apply`` executes. Not a description of it, not a
parallel implementation that agrees today — the same dataclass, built by the
same functions, so a dry run that rolls back has exercised the decisions the
real run will make (Stage-2A/2D pattern, Stage-2H brief 47).

Two gates the plan itself enforces.

**Source identity.** A plan records the archive's hash, the Excel's hash, the
KodaDash artefact's hash and the OneNote capture id. Apply re-reads them and
refuses if any moved. A reviewer approving a reconciliation is approving *these
bytes*, and a newer opinions ZIP is new evidence rather than an update to the
one that was read (brief 48).

**The Submission threshold.** A canonical SENT Submission needs a unique Matter,
an exact final binary, a defensible sent date and no material conflict. Anything
short of that becomes a review candidate carrying its evidence, never a DRAFT
pretending to be a historical letter (brief 25, 26, 27).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.legacy_import.opinion_enums import (
    AUTOMATIC_MATCH_CLASSES,
    HUMAN_DECIDED_STATES,
    OpinionConflict,
    OpinionMatchClass,
    OpinionSignal,
    RecipientBasis,
    SentDateBasis,
)
from app.legacy_import.opinion_match import (
    MatchProposal,
    OneNotePlacement,
    ReconciliationInput,
    RegisterRow,
    classify,
)
from app.legacy_import.opinion_sources import (
    ArchiveOccurrence,
    KodaDashRow,
    keyword_fold,
    read_kodadash_artifact,
    read_opinion_archive,
)
from app.submissions.enums import SubmissionKind

#: Title words that *say* what a letter is. Nothing is inferred from the
#: recipient, from the filename's position in the archive, or from a Matter
#: already having one submission: an opinion sent to a Riigikogu committee is
#: not automatically a parliamentary submission, and the second letter on a
#: matter is not automatically supplementary (Stage-2H brief 23).
EXPLICIT_KINDS: tuple[tuple[str, str], ...] = (
    ("uhispoordumine", SubmissionKind.JOINT_LETTER),
    ("uhiskiri", SubmissionKind.JOINT_LETTER),
    ("taiendav arvamus", SubmissionKind.SUPPLEMENTARY_OPINION),
)

#: What an archive of Chamber opinions is, when its own naming says nothing
#: more specific. Recorded as a decision in docs/open-decisions.md rather than
#: left as a default nobody chose.
DEFAULT_KIND = SubmissionKind.FORMAL_OPINION


class OpinionPlanError(RuntimeError):
    """The sources are not what the plan was told to expect."""


@dataclass
class SubmissionPlan:
    """One canonical historical Submission, and why every field is defensible."""

    sha256: str
    relative_path: str
    matter_id: Any
    kind: str
    title: str
    sent_date: datetime.date
    sent_date_basis: str
    recipient_raw: str
    recipient_basis: str
    match_class: str
    signals: list[str] = field(default_factory=list)
    #: Set when an existing Submission already carries these exact bytes. The
    #: apply attaches provenance to it instead of creating a second record of
    #: one sent action (brief 67).
    existing_submission_id: Any = None
    #: The reviewed candidate this plan came from, when a person approved it.
    #: The automatic route leaves this unset because its candidate row does not
    #: exist until the apply writes it; the apply resolves that one from the
    #: proposal it was built from. Either way the Submission ends up pointing
    #: at the exact candidate that justified it, never at a searched-for one.
    candidate_id: Any = None


@dataclass
class OpinionArchivePlan:
    archive_path: Path
    archive_sha256: str
    excel_sha256: str
    kodadash_path: Path | None
    kodadash_sha256: str
    onenote_capture_id: str

    occurrences: list[ArchiveOccurrence] = field(default_factory=list)
    kodadash_rows: dict[str, KodaDashRow] = field(default_factory=dict)
    proposals: list[MatchProposal] = field(default_factory=list)
    submissions: list[SubmissionPlan] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    # -- reporting ---------------------------------------------------------

    @property
    def distinct_binaries(self) -> int:
        return len({occurrence.sha256 for occurrence in self.occurrences})

    def counts_by_class(self) -> dict[str, int]:
        counts: dict[str, int] = dict.fromkeys(OpinionMatchClass.values, 0)
        for proposal in self.proposals:
            counts[proposal.match_class] += 1
        return counts

    def coverage_by_year(self) -> dict[str, dict[str, int]]:
        """Per-year coverage, because a corpus average hides an empty decade.

        The real archive holds nothing before 2020. A single "38% matched"
        headline would let a reader believe the 2014 opinions were merely
        unmatched rather than absent (brief 52, 54).
        """
        rows: dict[str, dict[str, int]] = {}
        by_sha = {proposal.sha256: proposal for proposal in self.proposals}
        submitted = {plan.sha256 for plan in self.submissions}
        for occurrence in self.occurrences:
            year = str(occurrence.source_year or "teadmata")
            row = rows.setdefault(
                year, {"occurrences": 0, "automatic": 0, "review": 0, "submissions": 0}
            )
            row["occurrences"] += 1
            proposal = by_sha.get(occurrence.sha256)
            if proposal is not None and proposal.match_class in AUTOMATIC_MATCH_CLASSES:
                row["automatic"] += 1
            else:
                row["review"] += 1
            if occurrence.sha256 in submitted:
                row["submissions"] += 1
        return dict(sorted(rows.items()))

    def summary(self) -> dict[str, object]:
        return {
            "archive_sha256": self.archive_sha256,
            "excel_sha256": self.excel_sha256,
            "kodadash_sha256": self.kodadash_sha256,
            "onenote_capture_id": self.onenote_capture_id,
            "occurrences": len(self.occurrences),
            "distinct_binaries": self.distinct_binaries,
            "kodadash_rows_bound": len(self.kodadash_rows),
            "by_class": self.counts_by_class(),
            "submissions_planned": len(self.submissions),
            "submissions_reusing_existing": sum(
                1 for plan in self.submissions if plan.existing_submission_id is not None
            ),
            "coverage_by_year": self.coverage_by_year(),
            "warnings": len(self.warnings),
            "findings": len(self.findings),
        }

    def as_text(self) -> str:
        summary = self.summary()
        lines = [
            "Arvamuste arhiivi plaan",
            f"  arhiiv                {self.archive_sha256[:16]}…",
            f"  Excel                 {self.excel_sha256[:16] or '—'}…",
            f"  KodaDash              {self.kodadash_sha256[:16] or '—'}…",
            f"  OneNote'i hõive       {self.onenote_capture_id or '—'}",
            "",
            f"  esinemisi             {summary['occurrences']:>6}",
            f"  erinevaid baite       {summary['distinct_binaries']:>6}",
            f"  KodaDashi ridu seotud {summary['kodadash_rows_bound']:>6}",
            "",
        ]
        for klass, count in self.counts_by_class().items():
            lines.append(f"  {klass:<28} {count:>6}")
        lines += [
            "",
            f"  kavandatud arvamusi   {summary['submissions_planned']:>6}",
            f"  neist olemasolevaid   {summary['submissions_reusing_existing']:>6}",
            "",
            "  Katvus aastate kaupa (esinemisi / automaatseid / ülevaatusse / arvamusi):",
        ]
        for year, row in self.coverage_by_year().items():
            lines.append(
                f"    {year}  {row['occurrences']:>4}  {row['automatic']:>4}  "
                f"{row['review']:>4}  {row['submissions']:>4}"
            )
        for warning in self.warnings:
            lines.append(f"  hoiatus: {warning}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def build_plan(
    *,
    archive_path: Path,
    excel_sha256: str = "",
    kodadash_path: Path | None = None,
    expected_archive_sha256: str = "",
    expected_kodadash_sha256: str = "",
) -> OpinionArchivePlan:
    """Read every source, classify every occurrence, decide every Submission.

    ``excel_sha256`` pins the register snapshot to read, rather than decorating
    the report with one while the matcher reads another. Empty selects the
    current register (`select_register_snapshot`); a snapshot this database
    never imported is an error, not a heading.
    """
    archive_sha, occurrences = read_opinion_archive(archive_path)
    if expected_archive_sha256 and expected_archive_sha256 != archive_sha:
        raise OpinionPlanError(
            f"Archive SHA-256 is {archive_sha}, expected {expected_archive_sha256}."
        )

    kodadash_sha = ""
    kodadash_rows: dict[str, KodaDashRow] = {}
    if kodadash_path is not None:
        kodadash_sha, rows = read_kodadash_artifact(kodadash_path)
        if expected_kodadash_sha256 and expected_kodadash_sha256 != kodadash_sha:
            raise OpinionPlanError(
                f"KodaDash artefact SHA-256 is {kodadash_sha}, expected {expected_kodadash_sha256}."
            )
        for row in rows:
            kodadash_rows[row.file_sha256] = row

    # One snapshot, chosen once: the rows the matcher sees and the SHA the plan
    # reports are the same register, which is the whole point of this call.
    selected_snapshot = select_register_snapshot(excel_sha256)
    register_rows = load_register_rows(snapshot_sha256=selected_snapshot)
    onenote = load_onenote_placements({occurrence.sha256 for occurrence in occurrences})
    capture_id = onenote_capture_id()

    plan = OpinionArchivePlan(
        archive_path=archive_path,
        archive_sha256=archive_sha,
        excel_sha256=selected_snapshot,
        kodadash_path=kodadash_path,
        kodadash_sha256=kodadash_sha,
        onenote_capture_id=capture_id,
        occurrences=occurrences,
        kodadash_rows=kodadash_rows,
    )

    plan.proposals = classify(
        ReconciliationInput(
            occurrences=occurrences,
            kodadash_by_sha=kodadash_rows,
            register_rows=register_rows,
            onenote_by_sha=onenote,
        )
    )
    _record_inventory_findings(plan)
    # Reviewed decisions are planned first, and the automatic pass then skips
    # every occurrence a person has already answered for. Both orderings
    # produce one Submission per occurrence — `_write_one_submission` stops at
    # the existing import row — but only this one lets the *reviewer's* Matter
    # and date be the ones that get written (brief 63; this task, 21).
    reviewed = _plan_reviewed_submissions(plan)
    plan.submissions = reviewed + _plan_submissions(plan)
    return plan


def _record_inventory_findings(plan: OpinionArchivePlan) -> None:
    """Inventory facts an operator has to see before approving anything."""
    unsupported = [o for o in plan.occurrences if o.detected_type != "application/pdf"]
    if unsupported:
        plan.warnings.append(
            f"{len(unsupported)} faili ei ole PDF; nende liik on kirjas, kuid neid ei "
            "eeldatud arvamuste arhiivis."
        )
    empty = [o for o in plan.occurrences if o.size_bytes == 0]
    if empty:
        plan.warnings.append(f"{len(empty)} tühja faili.")
    unnamed = [o for o in plan.occurrences if o.filename_date is None]
    if unnamed:
        plan.warnings.append(
            f"{len(unnamed)} faili nimi ei järgi arhiivi „AAAA-KK-PP - Saaja - Pealkiri“ "
            "reeglit, seega nimest ei loeta kuupäeva ega saajat."
        )
    damaged = [o for o in plan.occurrences if o.filename_encoding == "cp437"]
    if damaged:
        plan.warnings.append(
            f"{len(damaged)} faili nimi loeti cp437-na; nimi võib olla arhiivis moondunud."
        )
    if plan.kodadash_path is not None:
        unbound = [o for o in plan.occurrences if o.sha256 not in plan.kodadash_rows]
        if unbound:
            plan.findings.append(
                f"{len(unbound)} arhiivi faili ei ole üheski KodaDashi reas — need on "
                "arhiivis uuemad kui rikastuse tõmmis."
            )
        orphaned = set(plan.kodadash_rows) - {o.sha256 for o in plan.occurrences}
        if orphaned:
            plan.findings.append(
                f"{len(orphaned)} KodaDashi rida viitab failile, mida praeguses arhiivis ei ole."
            )


def _plan_submissions(plan: OpinionArchivePlan) -> list[SubmissionPlan]:
    """Which occurrences clear the historical-Submission threshold.

    One Submission per (Matter, binary). Several occurrences of the same bytes
    describe one sent action and produce one record; several *different* letters
    on one Matter each produce their own, which is the whole reason Submission
    hangs off Matter one-to-many (brief 41, 68).
    """
    from app.submissions.models import Submission

    seen: set[tuple[Any, str]] = set()
    planned: list[SubmissionPlan] = []

    for proposal in plan.proposals:
        if proposal.match_class not in AUTOMATIC_MATCH_CLASSES:
            continue
        if proposal.matter_id is None or proposal.conflicts:
            continue
        key = (proposal.matter_id, proposal.sha256)
        if key in seen:
            continue

        occurrence = _occurrence_for(plan, proposal.sha256)
        if occurrence is None:
            continue

        if not _citation_supports_a_dispatch(proposal):
            proposal.explanation += (
                " Seos käib teema kohta: viide tuvastab menetluse, kuid registri VÄLJA ei "
                "ole selle kirja enda kuupäeva lähedal, seega saadetud arvamust ei looda."
            )
            continue

        sent_date, basis = _sent_date_for(plan, proposal, occurrence)
        if sent_date is None:
            proposal.match_class = OpinionMatchClass.REVIEW_REQUIRED
            proposal.explanation += (
                " Teema on üheselt tuvastatud ja lõplik tõend olemas, kuid kaitstavat "
                "väljasaatmise kuupäeva ei ole, seega saadetud arvamust ei looda."
            )
            continue

        existing = _existing_submission(Submission, proposal.matter_id, proposal.sha256)
        conflicting = _conflicting_submission(Submission, proposal.matter_id, sent_date, proposal)
        if existing is None and conflicting is not None:
            proposal.match_class = OpinionMatchClass.CONFLICT
            proposal.conflicts.append(OpinionConflict.EXISTING_SUBMISSION_DISAGREES)
            proposal.explanation += (
                " Sellel teemal on juba samal kuupäeval saadetud arvamus teise lõpliku "
                "tõendiga. Käsitsi tehtud kirjet ei asendata."
            )
            continue

        recipient_raw, recipient_basis = _recipient_for(plan, proposal, occurrence)
        seen.add(key)
        planned.append(
            SubmissionPlan(
                sha256=proposal.sha256,
                relative_path=proposal.relative_path,
                matter_id=proposal.matter_id,
                kind=_kind_for(occurrence),
                title=(occurrence.filename_title or occurrence.original_filename)[:400],
                sent_date=sent_date,
                sent_date_basis=basis,
                recipient_raw=recipient_raw,
                recipient_basis=recipient_basis,
                match_class=proposal.match_class,
                signals=list(proposal.signals),
                existing_submission_id=existing,
            )
        )
    return _withhold_decided_occurrences(plan, _withhold_same_day_bundles(plan, planned))


def _citation_supports_a_dispatch(proposal: MatchProposal) -> bool:
    """Whether a citation match may also assert *when* the letter went out.

    `EXACT_LAW_REFERENCE_MATTER` establishes a **subject** relation: both
    sources named the same parliamentary proceeding. That is what makes it a
    link, and it is deliberately not a claim about a dispatch — the route
    exists precisely to reach Matters whose VÄLJA is nowhere near the letter's
    own date, because Koda writes twice about one proceeding and the register
    keeps the last one. The real corpus has such a pair ten months apart.

    Left alone, `_sent_date_for` would take the register's VÄLJA for those and
    file a Submission saying Koda sent *this* letter that day. The link would be
    right and the date would be invented, which is the one thing this whole
    stage refuses.

    So a citation-class proposal may become a Submission only where the letter's
    own date and the register's agree to within the window the corpus actually
    shows — which the matcher has already recorded as `EXACT_SENT_DATE` or
    `SENT_DATE_WITHIN_ONE_DAY`. Every other class is unaffected: they reached
    their Matter *through* the date, so the question is already answered.
    """
    if proposal.match_class != OpinionMatchClass.EXACT_LAW_REFERENCE_MATTER:
        return True
    return bool(
        {OpinionSignal.EXACT_SENT_DATE, OpinionSignal.SENT_DATE_WITHIN_ONE_DAY}
        & set(proposal.signals)
    )


def _withhold_decided_occurrences(
    plan: OpinionArchivePlan, planned: list[SubmissionPlan]
) -> list[SubmissionPlan]:
    """Drop what a person in the review queue has already answered for.

    Runs **last**, after every classification pass. That ordering is the whole
    subtlety: filtering these occurrences out at the top of the loop would also
    remove them from ``_withhold_same_day_bundles``, so rejecting one file of a
    letter-plus-annex bundle would release the others — quietly converting a
    reviewer's "this one is the annex" into a canonical SENT record for a file
    they never approved. The same-day rule is not weakened by a decision taken
    beside it; a reviewer who wants the letter filed says so with *Kinnita
    saatmine*, which is the route ``_plan_reviewed_submissions`` executes
    (brief 26, 41, 70; this task, 21, 22).

    Matched by SHA-256 rather than by candidate row, and that is deliberate. A
    reviewer pressing *Ei ole arvamus* is making a statement about the **file**,
    not about one of the several proposals the reconciliation happened to raise
    for it; keying on the exact ``(occurrence, Matter, class)`` row would let the
    next run re-file the same file under a neighbouring class and call it new
    work.
    """
    decided = _occurrences_a_person_has_decided()
    if not decided:
        return planned

    withheld = {entry.sha256 for entry in planned if entry.sha256 in decided}
    if not withheld:
        return planned

    for proposal in plan.proposals:
        if proposal.sha256 not in withheld:
            continue
        proposal.explanation += (
            " Ülevaataja on selle faili kohta juba otsuse teinud, seega automaatne import "
            "kanoonilist kirjet ei loo. Kui saatmine tuleb siiski kinnitada, tehke seda "
            "ülevaatuses („Kinnita saatmine“)."
        )
    plan.warnings.append(
        f"{len(withheld)} faili jäi automaatsest impordist välja, sest ülevaataja on "
        "nende kohta juba otsuse teinud."
    )
    return [entry for entry in planned if entry.sha256 not in withheld]


def _occurrences_a_person_has_decided() -> set[str]:
    """The binaries somebody in the review queue has already answered for.

    ``PENDING`` and ``APPLIED`` are the importer's own bookkeeping; the five
    states in ``HUMAN_DECIDED_STATES`` have exactly one writer, and it is a
    person in ``/haldus/arvamuste-ulevaatus/``. That split is what lets an
    automatic rerun recognise a decision without having to ask who made it.
    """
    from app.legacy_import.opinion_archive import OpinionMatchCandidate

    return set(
        OpinionMatchCandidate.objects.filter(state__in=HUMAN_DECIDED_STATES)
        # `.order_by()` before `.distinct()`, not decoration: `Meta.ordering`
        # puts `match_class` and two `item` columns into the SELECT, and a
        # DISTINCT over those returns one row per class rather than per file.
        .order_by()
        .values_list("item__sha256", flat=True)
        .distinct()
    )


def _withhold_same_day_bundles(
    plan: OpinionArchivePlan, planned: list[SubmissionPlan]
) -> list[SubmissionPlan]:
    """Several files, one Matter, one day — that is one letter, not several.

    A Matter genuinely produces more than one submission, which is why
    Submission hangs off Matter one-to-many. But *on the same day* it almost
    never does, and the real corpus shows what that pattern actually is:
    ``2025_44`` is a letter plus its ``Lisa 1``, and ``2024_139`` is a bundle of
    four earlier letters resent together. Filing those as two and four sent
    actions would overstate the department's output by exactly the number of
    attachments it happened to send (brief 41, 68, 70).

    Which file in a bundle is the letter and which is the annex is a judgement,
    so the whole group goes to review rather than one of them being guessed.
    """
    groups: dict[tuple[Any, datetime.date], list[SubmissionPlan]] = {}
    for item in planned:
        groups.setdefault((item.matter_id, item.sent_date), []).append(item)

    withheld = {entry.sha256 for group in groups.values() if len(group) > 1 for entry in group}
    if not withheld:
        return planned

    for proposal in plan.proposals:
        if proposal.sha256 not in withheld:
            continue
        proposal.match_class = OpinionMatchClass.REVIEW_REQUIRED
        proposal.conflicts.append(OpinionConflict.SAME_DAY_BUNDLE)
        proposal.explanation += (
            " Samale teemale langeb samal kuupäeval mitu arhiivi faili. See on üks "
            "väljasaatmine koos lisadega, mitte mitu arvamust; kumb neist on kiri ja "
            "kumb lisa, otsustab ülevaataja."
        )
    plan.warnings.append(
        f"{len(withheld)} faili jäi ülevaatusse, sest samale teemale langeb samal "
        "kuupäeval mitu faili."
    )
    return [item for item in planned if item.sha256 not in withheld]


def _plan_reviewed_submissions(plan: OpinionArchivePlan) -> list[SubmissionPlan]:
    """Decisions an operator already made in the review queue.

    The queue records; this executes. A reviewer confirming a letter was sent
    is asserting the identity and, where they stated one, the date the evidence
    could not settle. The record then says ``REVIEWED_DECISION``, so nobody
    later reads a register value as a person's judgement.

    Where the reviewer approved the letter without stating a date, the register
    date is still used and the record says ``EXCEL_OUT_DATE``. Until now it said
    ``REVIEWED_DECISION`` either way, which is the same confusion in the other
    direction: a person's authority written onto a spreadsheet cell they never
    looked at (brief 19, 63).
    """
    from app.legacy_import.opinion_archive import OpinionMatchCandidate, OpinionSubmissionImport
    from app.legacy_import.opinion_enums import OpinionCandidateState

    already = set(OpinionSubmissionImport.objects.values_list("item__sha256", flat=True))
    planned: list[SubmissionPlan] = []
    by_sha = {occurrence.sha256: occurrence for occurrence in plan.occurrences}

    approved = OpinionMatchCandidate.objects.filter(
        review_approves_submission=True,
        state=OpinionCandidateState.LINKED,
        matter__isnull=False,
    ).select_related("item")

    for candidate in approved:
        sha256 = candidate.item.sha256
        if sha256 in already:
            continue
        occurrence = by_sha.get(sha256)
        if occurrence is None:
            # The reviewer decided about a file that is not in the archive
            # snapshot being applied. That is a source mismatch, not something
            # to guess about.
            plan.warnings.append(
                f"Ülevaatusel kinnitatud fail {candidate.item.original_filename[:60]} "
                "ei ole selles arhiivi tõmmises."
            )
            continue
        # The basis follows the value, always. A reviewer who approved the
        # submission and did not state a date has not decided the date, and
        # labelling the register's VÄLJA column as REVIEWED_DECISION would put a
        # person's authority on a spreadsheet cell — which is exactly the
        # confusion this basis vocabulary exists to prevent (brief 19, 63).
        #
        # The fallback itself is kept: an approved letter with a register date is
        # a defensible record. What changes is that it now says so.
        if candidate.reviewed_sent_date is not None:
            sent_date = candidate.reviewed_sent_date
            sent_date_basis = SentDateBasis.REVIEWED_DECISION
        elif candidate.excel_sent_date is not None:
            sent_date = candidate.excel_sent_date
            sent_date_basis = SentDateBasis.EXCEL_OUT_DATE
        else:
            # No date from either source. A SENT submission whose date nobody
            # can name is withheld rather than dated from something weaker.
            plan.warnings.append(
                f"Ülevaatusel kinnitatud fail {candidate.item.original_filename[:60]}: "
                "saatmise kuupäeva ei ole ei ülevaatuses ega registris."
            )
            continue
        recipient_raw, recipient_basis = _recipient_for(
            plan,
            MatchProposal(
                sha256=sha256,
                relative_path=occurrence.relative_path,
                match_class=candidate.match_class,
                excel_addressee_raw=candidate.excel_addressee_raw,
            ),
            occurrence,
        )
        planned.append(
            SubmissionPlan(
                sha256=sha256,
                relative_path=occurrence.relative_path,
                matter_id=candidate.matter_id,
                kind=_kind_for(occurrence),
                title=(occurrence.filename_title or occurrence.original_filename)[:400],
                sent_date=sent_date,
                sent_date_basis=sent_date_basis,
                recipient_raw=recipient_raw,
                recipient_basis=recipient_basis,
                match_class=candidate.match_class,
                signals=list(candidate.signals),
                candidate_id=candidate.pk,
            )
        )
        already.add(sha256)
    return planned


def _occurrence_for(plan: OpinionArchivePlan, sha256: str) -> ArchiveOccurrence | None:
    for occurrence in plan.occurrences:
        if occurrence.sha256 == sha256:
            return occurrence
    return None


def _kind_for(occurrence: ArchiveOccurrence) -> str:
    folded = keyword_fold(occurrence.filename_title)
    for token, kind in EXPLICIT_KINDS:
        if token in folded:
            return kind
    return DEFAULT_KIND


def _sent_date_for(
    plan: OpinionArchivePlan, proposal: MatchProposal, occurrence: ArchiveOccurrence
) -> tuple[datetime.date | None, str]:
    """The precedence from brief 19, and nothing outside it.

    The archive's own filename date is deliberately absent. In the real corpus
    the register's VÄLJA falls on the filename's day 326 times and the next day
    227 times, so the filename says when the letter was written, not when it
    went out. Using it would put a confident wrong date on a third of the
    corpus — and file mtime, ZIP headers and ``created_at`` are not on this
    list at all.
    """
    email_sent = _outgoing_email_timestamp(occurrence.sha256)
    if email_sent is not None:
        return email_sent, SentDateBasis.OUTGOING_EMAIL
    if proposal.excel_sent_date is not None:
        return proposal.excel_sent_date, SentDateBasis.EXCEL_OUT_DATE
    return None, ""


def _outgoing_email_timestamp(sha256: str) -> datetime.date | None:
    """A date only if an outgoing message demonstrably carried these bytes.

    Reads the *already extracted* EMAIL_METADATA derivative. Nothing here opens
    a message, and a version whose malware scan has not cleared is never
    consulted, because reopening scanner-gated evidence in a parser is exactly
    the door Stage 2B closed (brief 18, 32).
    """
    from app.documents.derivatives import DocumentDerivative, EmailAttachmentLink
    from app.documents.enums import DerivativeKind, DerivativeStatus, MalwareScanState

    links = EmailAttachmentLink.objects.filter(
        attachment_version__sha256=sha256,
        parent_version__malware_scan_state=MalwareScanState.CLEAN,
    ).select_related("parent_version")
    for link in links:
        derivative = DocumentDerivative.objects.filter(
            version=link.parent_version,
            kind=DerivativeKind.EMAIL_METADATA,
            status=DerivativeStatus.ACTIVE,
        ).first()
        if derivative is None:
            continue
        metadata = derivative.metadata or {}
        sent_at = str(metadata.get("sent_at") or "")
        if not sent_at:
            continue
        try:
            return datetime.date.fromisoformat(sent_at[:10])
        except ValueError:
            continue
    return None


def _recipient_for(
    plan: OpinionArchivePlan, proposal: MatchProposal, occurrence: ArchiveOccurrence
) -> tuple[str, str]:
    """The historical recipient, in the words the source used.

    KodaDash's ``recipient_normalized`` is never taken: it folds
    Keskkonnaministeerium into Kliimaministeerium in 52 rows and collapses a
    comma-separated pair to its first name. That is a defensible analytics
    bucket and an indefensible historical identity (brief 10, 21).
    """
    row = plan.kodadash_rows.get(occurrence.sha256)
    if row is not None and row.recipient_raw.strip():
        return row.recipient_raw.strip(), RecipientBasis.KODADASH_RAW
    if proposal.excel_addressee_raw.strip():
        return proposal.excel_addressee_raw.strip(), RecipientBasis.EXCEL_ADDRESSEE
    if occurrence.filename_recipient.strip():
        # The archive's own naming, which is the same thing KodaDash read. It
        # is evidence about the recipient and resolves to an Organisation only
        # by exact identity or a reviewed alias.
        return occurrence.filename_recipient.strip(), RecipientBasis.KODADASH_RAW
    return "", RecipientBasis.UNRESOLVED


def _existing_submission(model: Any, matter_id: Any, sha256: str) -> Any:
    existing = (
        model.objects.filter(matter_id=matter_id, final_version__sha256=sha256)
        .values_list("pk", flat=True)
        .first()
    )
    return existing


def _conflicting_submission(
    model: Any, matter_id: Any, sent_date: datetime.date, proposal: MatchProposal
) -> Any:
    """A manual record on the same Matter and day, with different evidence."""
    return (
        model.objects.filter(matter_id=matter_id, sent_at__date=sent_date)
        .exclude(final_version__sha256=proposal.sha256)
        .exclude(final_version__isnull=True)
        .values_list("pk", flat=True)
        .first()
    )


# ---------------------------------------------------------------------------
# Database views the plan reads
# ---------------------------------------------------------------------------


def select_register_snapshot(explicit: str = "") -> str:
    """The one register snapshot this reconciliation reads.

    `MatterSourceReference` is write-once evidence, and re-importing a newer
    Excel adds references rather than replacing them — that is the provenance
    model working as designed. But a *reconciliation* has to read one register,
    not the union of every register the Chamber has ever imported. With two
    snapshots visible, a Matter appears twice under the same date and
    addressee, the matcher's "exactly one register row" test sees two, and the
    Matter competes with itself: 249 STRICT_MULTI_SIGNAL occurrences fell to
    REVIEW_REQUIRED that way, and the plan still reported a single Excel SHA
    while doing it.

    So one snapshot is chosen here, and both the rows and the plan's
    ``excel_sha256`` come from it. The chronology is `ImportBatch`'s, because
    that is the only record of *when* a snapshot was read and whether the
    reading finished; a file's timestamp, a SHA's lexical order and a row's
    number all say nothing about which register is current.

    Selection is per plan, never per Matter. Taking each Matter's newest
    reference would silently blend two registers and then name one of them in
    the report.

    It fails closed. If several snapshots are present and no finished import
    says which is current, that is a question about the Chamber's records and
    not one this function may answer by picking.
    """
    from app.legacy_import.models import (
        ImportBatch,
        MatterSourceReference,
        ReconciliationStatus,
    )
    from app.legacy_import.parser import SOURCE_SYSTEM

    # An import that finished. ``COMPLETED_WITH_GAPS`` is one of these: the gap
    # is source rows that did not become Matters, not doubt about the rows that
    # did (`app.legacy_import.apply`), and the register importer's own tests
    # treat the two as one successful outcome. ``RUNNING`` and ``FAILED`` are
    # not eligible — a half-written snapshot is not a reading of the register.
    finished = (ReconciliationStatus.COMPLETED, ReconciliationStatus.COMPLETED_WITH_GAPS)

    present = set(
        MatterSourceReference.objects.filter(source_system=SOURCE_SYSTEM)
        .exclude(source_snapshot_sha256="")
        .values_list("source_snapshot_sha256", flat=True)
        .distinct()
    )

    if explicit:
        if explicit not in present:
            raise OpinionPlanError(
                f"No register was imported from snapshot {explicit[:16]}…. "
                "A plan may only be pinned to a snapshot this database holds."
            )
        return explicit

    if not present:
        # No register import at all. There are no rows to disagree about.
        return ""

    latest = (
        ImportBatch.objects.filter(
            source_system=SOURCE_SYSTEM,
            reconciliation_status__in=finished,
        )
        .exclude(source_snapshot_sha256="")
        .order_by("-started_at", "-pk")
        .values_list("source_snapshot_sha256", flat=True)
        .first()
    )
    if latest in present:
        return latest

    if len(present) == 1:
        # One snapshot and no batch naming it — an older deployment, or a
        # fixture. Unambiguous, so there is nothing to refuse.
        return next(iter(present))

    raise OpinionPlanError(
        f"The register holds {len(present)} snapshots and no finished import says which is "
        "current. Re-run the register import, or pin the plan to one snapshot explicitly; "
        "reconciling against several registers at once makes every Matter compete with itself."
    )


def load_register_rows(*, snapshot_sha256: str = "") -> list[RegisterRow]:
    """Register Matters, read through their era contracts.

    ``VÄLJA`` has no canonical column — deliberately, because a date alone can
    never make a SENT Submission — so it is read from the raw row that the
    import preserved, using the contract for that year. Reading it any other
    way would mean hard-coding column F and inheriting every future column move
    (Stage-2A contract, brief 11, 43).

    ``snapshot_sha256`` narrows the read to one imported register. Passing it
    is what keeps a Matter from appearing twice; see `select_register_snapshot`
    for why that matters and why the choice is made once per plan. Empty means
    every reference, which is only correct where a single snapshot exists.
    """
    from app.legacy_import.contracts import contract_for_year
    from app.legacy_import.models import MatterSourceReference
    from app.legacy_import.parser import SOURCE_SYSTEM

    rows: list[RegisterRow] = []
    contracts: dict[int, Any] = {}
    imported = MatterSourceReference.objects.filter(source_system=SOURCE_SYSTEM)
    if snapshot_sha256:
        imported = imported.filter(source_snapshot_sha256=snapshot_sha256)
    references = imported.values(
        "matter_id",
        "matter__reference_year",
        "matter__reference_number",
        "matter__title",
        "source_sheet",
        "source_row_raw",
    )
    for reference in references:
        year = reference["matter__reference_year"]
        if year is None:
            continue
        if year not in contracts:
            contracts[year] = contract_for_year(year)
        contract = contracts[year]
        if contract is None:
            continue
        raw = reference["source_row_raw"] or {}
        sent_column = contract.column_for("opinion_sent_date")
        addressee_column = contract.column_for("addressee_organisation")
        source_column = contract.column_for("source_organisation")
        direction = (
            "addressee"
            if addressee_column is not None
            else ("source" if source_column is not None else "")
        )
        counterparty = addressee_column or source_column
        rows.append(
            RegisterRow(
                matter_id=reference["matter_id"],
                reference=f"{year}_{reference['matter__reference_number']}",
                year=year,
                title=reference["matter__title"] or "",
                sent_date=_parse_date(raw.get(sent_column.letter) if sent_column else None),
                addressee_raw=str(raw.get(counterparty.letter) or "") if counterparty else "",
                counterparty_direction=direction,
            )
        )
    return rows


def register_snapshot_sha256() -> str:
    """The Excel snapshot the reconciliation currently reads.

    The same selection `build_plan` makes, deliberately: `require_unchanged_sources`
    compares this against the snapshot a reviewed plan was built from, and two
    independent answers to "which register is current" would let a plan pass a
    gate it should have failed. This used to be an unordered ``.first()``,
    which named whichever row PostgreSQL returned — in production, the *older*
    of two snapshots, while the matcher read both.
    """
    return select_register_snapshot()


def load_onenote_placements(sha_values: set[str]) -> dict[str, list[OneNotePlacement]]:
    """Where each of these binaries sits in the OneNote corpus, and whose it is.

    The Matter side comes from ``MatterSourcePage``, which is the *accepted*
    relationship — a page a reviewer confirmed or an Excel hyperlink resolved.
    Pending candidates are not consulted: a proposal is not a claim (brief 5).
    """
    from app.legacy_import.source_pages import LegacySourceResource, MatterSourcePage

    if not sha_values:
        return {}

    resources = list(
        LegacySourceResource.objects.filter(sha256__in=sha_values).select_related("source_page")
    )
    page_ids = {resource.source_page_id for resource in resources}
    claims: dict[Any, list[tuple[Any, str]]] = {}
    for link in MatterSourcePage.objects.filter(source_page_id__in=page_ids).values(
        "source_page_id",
        "matter_id",
        "matter__reference_year",
        "matter__reference_number",
    ):
        reference = (
            f"{link['matter__reference_year']}_{link['matter__reference_number']}"
            if link["matter__reference_year"] and link["matter__reference_number"]
            else ""
        )
        claims.setdefault(link["source_page_id"], []).append((link["matter_id"], reference))

    placements: dict[str, list[OneNotePlacement]] = {}
    for resource in resources:
        holders = claims.get(resource.source_page_id, [])
        placements.setdefault(resource.sha256, []).append(
            OneNotePlacement(
                page_key=resource.source_page.page_key,
                page_title=resource.source_page.title,
                section=resource.source_page.source_section,
                block_ordinal=resource.source_block_ordinal,
                matter_ids=tuple(matter_id for matter_id, _ in holders),
                excel_references=tuple(sorted({ref for _, ref in holders if ref})),
            )
        )
    return placements


def onenote_capture_id() -> str:
    from app.legacy_import.source_pages import LegacySourcePage

    return (
        LegacySourcePage.objects.exclude(capture_id="")
        .values_list("capture_id", flat=True)
        .order_by("-latest_imported_at")
        .first()
        or ""
    )


def _parse_date(value: object) -> datetime.date | None:
    """Read a register date exactly as the register parser does.

    Delegated rather than reimplemented: the register contains `27.veebr.`,
    `ei saatnud` and `-`, and a second date parser that handles them slightly
    differently would produce a second history (Stage-2A parser).
    """
    from app.legacy_import.dates import parse_date

    text = str(value or "").strip()
    if not text:
        return None
    return parse_date(text, raw=text).value
