"""From findings to suggestions: confidence, precedence and conflict.

`textscan.py` says what the text contains. This module says what that means
for the Matter, and it is the one place the policy lives:

* **Structured beats parsed.** A sender read from a message's own ``From:``
  header outranks a ministry named in an annex; a heading on the draft
  outranks a subject line that says *palun arvamust* (brief §4, §21).
* **Explicit beats incidental.** A date introduced by *palume esitada
  hiljemalt* is a deadline; the letter's own date, an entry-into-force day
  and the date of the letter being answered are not (brief §8).
* **Two strong answers are no answer.** Two different dates both stated as
  the deadline, two organisations both named as the sender, two headings
  that disagree — each is a conflict, shown with both sides and pre-filling
  nothing (brief §15).
* **The Matter's own values win.** What the record already carries is never
  overwritten; a suggestion that agrees with it is shown as agreement and a
  suggestion that differs is shown as an alternative the person may choose.

Everything here is pure once the catalogues are loaded: `analyse` takes the
extracted text, the organisation patterns, the policy-area vocabulary and
the Matter's current values, and returns the same `IntakeAnalysis` for the
same inputs. `analyse_matter` is the thin database-facing wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

from app.core.dates import format_estonian_date
from app.documents.enums import DocumentRole
from app.documents.preview import STATE_LABELS, STATE_TONES
from app.matters.intake_suggestions import textscan
from app.matters.intake_suggestions import vocabulary as vocab
from app.matters.intake_suggestions.input import (
    AnalysisInput,
    SourceDocument,
    TextBlock,
    build_analysis_input,
)
from app.matters.intake_suggestions.resolvers import (
    OrganisationCatalogue,
    load_organisation_catalogue,
    load_policy_areas,
    rule_diagnostics,
)
from app.matters.intake_suggestions.types import (
    Candidate,
    Confidence,
    DocumentReadiness,
    FieldSuggestions,
    IntakeAnalysis,
    Provenance,
    SourceKind,
    SuggestedField,
)
from app.taxonomy.models import PolicyArea
from app.workflow.enums import Track

#: A heading this strong on a document is the document's formal title.
TITLE_HIGH_SCORE = 6
#: Weaker headings are offered, not pre-filled.
TITLE_MEDIUM_SCORE = 4
#: A subject line that names an act — one strong cue — is a strong title;
#: a plain subject of a few words is only a suggestion.
SUBJECT_HIGH_SCORE = 3


@dataclass(frozen=True)
class CurrentValues:
    """What the Matter already says, so nothing of it is ever overwritten."""

    title: str = ""
    source_organisation_ids: frozenset[Any] = frozenset()
    response_deadline: date | None = None
    track: str = ""
    policy_area_ids: frozenset[Any] = frozenset()

    @classmethod
    def of(cls, matter: Any) -> CurrentValues:
        """Read the Matter once."""
        return cls(
            title=matter.title,
            source_organisation_ids=frozenset(matter.source_organisation_ids),
            response_deadline=matter.response_deadline,
            track=matter.track or "",
            policy_area_ids=frozenset(area.pk for area in matter.policy_areas.all()),
        )


@dataclass
class _Builder:
    """Accumulates candidates while the rules run, then freezes them."""

    fields: dict[str, list[Candidate]] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    conflicts: set[str] = field(default_factory=set)
    findings: list[Candidate] = field(default_factory=list)
    other: list[Candidate] = field(default_factory=list)

    def add(self, candidate: Candidate) -> None:
        self.fields.setdefault(candidate.field, []).append(candidate)


def analyse_matter(matter: Any, viewer: Any) -> IntakeAnalysis:
    """Read the Matter's visible derivatives and propose. Writes nothing."""
    analysis_input = build_analysis_input(matter, viewer)
    return analyse(
        analysis_input,
        organisations=load_organisation_catalogue(),
        policy_areas=load_policy_areas(),
        current=CurrentValues.of(matter),
    )


def analyse(
    analysis_input: AnalysisInput,
    *,
    organisations: OrganisationCatalogue,
    policy_areas: dict[str, PolicyArea],
    current: CurrentValues,
) -> IntakeAnalysis:
    builder = _Builder()
    documents = analysis_input.analysed

    _titles(documents, current, builder)
    _senders(documents, organisations, current, builder)
    _deadlines(documents, current, builder)
    _tracks(documents, current, builder)
    _areas(documents, policy_areas, current, builder)
    _contacts(documents, builder)
    _references(documents, builder)

    fields = {
        name: FieldSuggestions(
            field=name,
            candidates=tuple(candidates),
            conflict=name in builder.conflicts,
            note=builder.notes.get(name, ""),
        )
        for name, candidates in builder.fields.items()
    }
    return IntakeAnalysis(
        documents=tuple(_readiness(document) for document in analysis_input.documents),
        fields=fields,
        findings=tuple(sorted(builder.findings, key=_finding_order)),
        other_findings=tuple(sorted(builder.other, key=_finding_order)),
        diagnostics=rule_diagnostics(policy_areas),
    )


#: The panel reads people first, then time, then paper: who wrote, when the
#: message went, who to contact, the references, the links. A stable order
#: rather than discovery order, so two opens of the same page look the same.
_FINDING_ORDER: dict[str, int] = {
    SuggestedField.SENDER_CONTACT: 0,
    SuggestedField.EMAIL_SENT_AT: 1,
    SuggestedField.DOCUMENT_CONTACT: 2,
    SuggestedField.RELATIVE_DEADLINE: 3,
    SuggestedField.EIS_REFERENCE: 4,
    SuggestedField.EXTERNAL_REFERENCE: 5,
    SuggestedField.ORGANISATION_MENTION: 6,
    SuggestedField.SOURCE_URL: 7,
}


def _finding_order(candidate: Candidate) -> tuple[int, int]:
    return _FINDING_ORDER.get(candidate.field, 9), _rank(candidate.confidence)


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


def _readiness(document: SourceDocument) -> DocumentReadiness:
    state = document.extraction_state
    note = ""
    if document.skipped_for_budget:
        note = "Jäi automaatkontrollist välja; tekst on olemas ja avatav."
    elif document.truncated:
        note = "Analüüsiti dokumendi algusosa; ülejäänu jäi mahupiirangu taha."
    elif state == "FAILED" and document.extraction_note:
        note = document.extraction_note
    elif state == "NOT_APPLICABLE":
        note = document.extraction_note or "Selle vormingu teksti ei eraldata."
    return DocumentReadiness(
        document_id=document.document_id,
        filename=document.filename,
        role=document.role,
        role_label=str(DocumentRole(document.role).label)
        if document.role in DocumentRole.values
        else document.role,
        extraction_state=state,
        state_label=STATE_LABELS.get(state, state),
        state_tone=STATE_TONES.get(state, "quiet"),
        analysed=document.analysed,
        from_ocr=document.from_ocr,
        truncated=document.truncated,
        skipped_for_budget=document.skipped_for_budget,
        note=note,
    )


def _provenance(
    document: SourceDocument, block: TextBlock | None, *, header: bool = False
) -> Provenance:
    if header or block is None:
        return Provenance(
            document_id=document.document_id,
            version_id=document.version_id,
            filename=document.filename,
            source_kind=SourceKind.EMAIL_HEADER if header else SourceKind.NATIVE_TEXT,
        )
    return Provenance(
        document_id=document.document_id,
        version_id=document.version_id,
        filename=document.filename,
        source_kind=block.source_kind,
        locator_label=block.locator_label,
    )


# ---------------------------------------------------------------------------
# Pealkiri
# ---------------------------------------------------------------------------


def _titles(
    documents: tuple[SourceDocument, ...], current: CurrentValues, builder: _Builder
) -> None:
    headings: list[Candidate] = []
    subjects: list[Candidate] = []
    for document in documents:
        if document.email is not None:
            found = textscan.subject_title(document.email_value("subject"))
            if found is not None:
                subjects.append(
                    Candidate(
                        field=SuggestedField.TITLE,
                        value=found.text,
                        display=found.text,
                        confidence=(
                            Confidence.HIGH
                            if found.score >= SUBJECT_HIGH_SCORE
                            else Confidence.MEDIUM
                        ),
                        rule=found.rule,
                        provenance=_provenance(document, None, header=True),
                        evidence=textscan.collapse(found.line),
                        detail=_cue_line(found.cues),
                        score=found.score,
                    )
                )
        for found in textscan.find_heading_titles(document)[:2]:
            if found.score < TITLE_MEDIUM_SCORE:
                # A weak heading is noise, not a «Muud leiud» entry: the person
                # can see the document's first page for themselves.
                continue
            confidence = (
                Confidence.HIGH
                if found.score >= TITLE_HIGH_SCORE and found.rule == "formal_heading"
                else Confidence.MEDIUM
            )
            headings.append(
                Candidate(
                    field=SuggestedField.TITLE,
                    value=found.text,
                    display=found.text,
                    confidence=confidence,
                    rule=found.rule,
                    provenance=_provenance(document, found.block),
                    evidence=found.line,
                    detail=_cue_line(found.cues),
                    score=found.score,
                )
            )

    merged = _merge_titles([*headings, *subjects])
    if not merged:
        return
    # A formal heading outranks a subject line that says the same thing less
    # formally; a subject is HIGH only when no document heading is.
    strong_headings = [c for c in merged if c.rule == "formal_heading" and c.is_high]
    if strong_headings:
        merged = [
            c if c in strong_headings or c.confidence != Confidence.HIGH else _demote(c)
            for c in merged
        ]
    if len({textscan.normalise_title(c.value) for c in strong_headings}) > 1:
        builder.conflicts.add(SuggestedField.TITLE)
        builder.notes[SuggestedField.TITLE] = (
            "Dokumendid kannavad erinevaid pealkirju — vali ise, mis teemat kirjeldab."
        )
    current_key = textscan.normalise_title(current.title)
    ranked = sorted(merged, key=lambda c: (_rank(c.confidence), -c.score))
    for candidate in ranked:
        already = textscan.normalise_title(candidate.value) == current_key
        builder.add(_with_current(candidate, already))


def _merge_titles(candidates: list[Candidate]) -> list[Candidate]:
    merged: dict[str, Candidate] = {}
    for candidate in candidates:
        key = textscan.normalise_title(candidate.value)
        existing = merged.get(key)
        if existing is None or _rank(candidate.confidence) < _rank(existing.confidence):
            merged[key] = candidate
    return list(merged.values())


def _cue_line(cues: tuple[str, ...]) -> str:
    unique = list(dict.fromkeys(cues))
    return f"Tunnused: {', '.join(unique)}" if unique else ""


_RANKS: dict[str, int] = {Confidence.HIGH: 0, Confidence.MEDIUM: 1, Confidence.LOW: 2}


def _rank(confidence: str) -> int:
    return _RANKS.get(confidence, 3)


def _demote(candidate: Candidate) -> Candidate:
    return replace(candidate, confidence=Confidence.MEDIUM)


def _with_current(candidate: Candidate, already: bool) -> Candidate:
    if not already:
        return candidate
    return replace(candidate, already_current=True)


# ---------------------------------------------------------------------------
# Kellelt
# ---------------------------------------------------------------------------


def _senders(
    documents: tuple[SourceDocument, ...],
    organisations: OrganisationCatalogue,
    current: CurrentValues,
    builder: _Builder,
) -> None:
    best: dict[Any, Candidate] = {}
    mentions_only: dict[Any, Candidate] = {}

    def offer(candidate: Candidate) -> None:
        existing = best.get(candidate.value)
        if existing is None or (
            _rank(candidate.confidence),
            -candidate.score,
        ) < (_rank(existing.confidence), -existing.score):
            best[candidate.value] = candidate

    for document in documents:
        if document.email is not None:
            _senders_from_email(document, organisations, offer)
        mentions = textscan.find_organisation_mentions(document, organisations.patterns)
        letterhead_ids = {
            mention.organisation_id
            for mention in mentions
            if mention.zone == "letterhead"
            and not organisations.entries[mention.organisation_id].is_chamber
        }
        for mention in mentions:
            entry = organisations.entries.get(mention.organisation_id)
            if entry is None or entry.is_chamber:
                continue
            confidence, rule = _sender_confidence(mention, len(letterhead_ids))
            candidate = Candidate(
                field=SuggestedField.SOURCE_ORGANISATIONS,
                value=str(entry.id),
                display=entry.name,
                confidence=confidence,
                rule=rule,
                provenance=_provenance(document, mention.block),
                evidence=textscan.excerpt_around(mention.block.text, mention.start, mention.end),
                detail=f"Tekstis: „{mention.matched}”",
                score={"sender_cue": 4, "letterhead": 3, "signature": 2, "body": 1}[mention.zone],
            )
            if confidence == Confidence.LOW:
                if entry.id not in mentions_only:
                    mentions_only[entry.id] = candidate
            else:
                offer(candidate)

    high = [c for c in best.values() if c.is_high]
    distinct_high = {c.value for c in high}
    if len(distinct_high) > 1:
        builder.conflicts.add(SuggestedField.SOURCE_ORGANISATIONS)
        builder.notes[SuggestedField.SOURCE_ORGANISATIONS] = (
            "Materjal nimetab mitut võimalikku saatjat — vali ise."
        )
    ranked = sorted(best.values(), key=lambda c: (_rank(c.confidence), -c.score, c.display))
    for candidate in ranked:
        already = _pk_in(candidate.value, current.source_organisation_ids)
        builder.add(_with_current(candidate, already))
    for candidate in sorted(mentions_only.values(), key=lambda c: c.display)[
        : vocab.ORGANISATION_MENTION_LIMIT
    ]:
        if candidate.value in best or _pk_in(candidate.value, current.source_organisation_ids):
            continue
        builder.other.append(replace(candidate, field=SuggestedField.ORGANISATION_MENTION))


def _pk_in(value: str, ids: frozenset[Any]) -> bool:
    return any(str(pk) == value for pk in ids)


def _sender_confidence(
    mention: textscan.OrganisationMention, letterhead_count: int
) -> tuple[str, str]:
    if mention.zone == "sender_cue":
        return (Confidence.MEDIUM if mention.is_abbreviation else Confidence.HIGH), "sender_cue"
    if mention.zone == "letterhead":
        if mention.is_abbreviation:
            return Confidence.MEDIUM, "letterhead_abbreviation"
        if letterhead_count == 1:
            return Confidence.HIGH, "letterhead"
        return Confidence.MEDIUM, "letterhead_shared"
    if mention.zone == "signature":
        return Confidence.MEDIUM, "signature_block"
    return Confidence.LOW, "body_mention"


def _senders_from_email(
    document: SourceDocument, organisations: OrganisationCatalogue, offer: Any
) -> None:
    from_name = document.email_value("from_name")
    from_email = document.email_value("from_email")
    header = _provenance(document, None, header=True)
    for pattern in textscan.organisation_in_display_name(from_name, organisations.patterns):
        entry = organisations.entries.get(pattern.organisation_id)
        if entry is None or entry.is_chamber:
            continue
        offer(
            Candidate(
                field=SuggestedField.SOURCE_ORGANISATIONS,
                value=str(entry.id),
                display=entry.name,
                confidence=Confidence.MEDIUM if pattern.is_abbreviation else Confidence.HIGH,
                rule="email_from_display",
                provenance=header,
                evidence=f"Saatja: {from_name} <{from_email}>"
                if from_email
                else f"Saatja: {from_name}",
                score=5,
            )
        )
    label, _ = textscan.fold(textscan.domain_label(from_email))
    if not label:
        return
    for pattern in organisations.patterns:
        # The domain's own label as a whole word: «mkm.ee» names the ministry
        # whose recorded alias is MKM. A domain is not a name, so MEDIUM.
        folded_form, _ = textscan.fold(pattern.form)
        if folded_form.replace(" ", "").replace("-", "") != label.replace("-", ""):
            continue
        entry = organisations.entries.get(pattern.organisation_id)
        if entry is None or entry.is_chamber:
            continue
        offer(
            Candidate(
                field=SuggestedField.SOURCE_ORGANISATIONS,
                value=str(entry.id),
                display=entry.name,
                confidence=Confidence.MEDIUM,
                rule="email_domain",
                provenance=header,
                evidence=f"Saatja aadress: {from_email}",
                detail=f"Domeen „{label}” vastab nimekujule „{pattern.form}”",
                score=2,
            )
        )


# ---------------------------------------------------------------------------
# Arvamuse tähtaeg
# ---------------------------------------------------------------------------

_MEMORANDUM_HEAD = vocab.TITLE_MEMORANDUM_WRAPPER


def _deadlines(
    documents: tuple[SourceDocument, ...], current: CurrentValues, builder: _Builder
) -> None:
    by_date: dict[date, Candidate] = {}
    for document in documents:
        is_memorandum = _is_explanatory_memorandum(document)
        blocks = document.prose_blocks
        for index, block in enumerate(blocks):
            for found in textscan.find_deadline_dates(block.text, opens_document=index == 0):
                if found.strength == Confidence.LOW:
                    continue
                confidence = found.strength
                rule = found.rule
                if is_memorandum and confidence == Confidence.HIGH:
                    # An explanatory memorandum quotes the letter's deadline at
                    # best; the covering letter is where it is stated.
                    confidence, rule = Confidence.MEDIUM, "deadline_in_memorandum"
                candidate = Candidate(
                    field=SuggestedField.RESPONSE_DEADLINE,
                    value=found.value.isoformat(),
                    display=format_estonian_date(found.value),
                    confidence=confidence,
                    rule=rule,
                    provenance=_provenance(document, block),
                    evidence=textscan.excerpt_around(block.text, found.start, found.end),
                    detail=_cue_line(found.cues),
                    form_value=format_estonian_date(found.value),
                    score=3 if confidence == Confidence.HIGH else 1,
                )
                existing = by_date.get(found.value)
                if existing is None or _rank(confidence) < _rank(existing.confidence):
                    by_date[found.value] = candidate
        for relative in textscan.find_relative_deadlines(document.text):
            relative_block = _block_for_offset(document, relative.start)
            builder.findings.append(
                Candidate(
                    field=SuggestedField.RELATIVE_DEADLINE,
                    value=relative.phrase,
                    display=relative.phrase,
                    confidence=Confidence.MEDIUM,
                    rule="relative_deadline",
                    provenance=_provenance(document, relative_block),
                    evidence=textscan.excerpt_around(document.text, relative.start, relative.end),
                    detail="Tähtaeg on öeldud ajavahemikuna; alguspäeva dokument ei nimeta.",
                )
            )
    if not by_date:
        return
    high_dates = {value for value, candidate in by_date.items() if candidate.is_high}
    if len(high_dates) > 1:
        builder.conflicts.add(SuggestedField.RESPONSE_DEADLINE)
        builder.notes[SuggestedField.RESPONSE_DEADLINE] = (
            "Materjalis on kaks erinevat tähtaega — kontrolli allikaid ja vali ise."
        )
    for value in sorted(by_date, key=lambda d: (_rank(by_date[d].confidence), d)):
        candidate = by_date[value]
        builder.add(_with_current(candidate, current.response_deadline == value))


def _is_explanatory_memorandum(document: SourceDocument) -> bool:
    opening = document.text[:400]
    first_lines = [textscan.collapse(line) for line in opening.split("\n")[:6]]
    return any(
        line.casefold().startswith("seletuskiri") or _MEMORANDUM_HEAD.match(line)
        for line in first_lines
        if line
    )


def _block_for_offset(document: SourceDocument, offset: int) -> TextBlock | None:
    position = 0
    for block in document.prose_blocks:
        end = position + len(block.text)
        if offset < end:
            return block
        position = end + 2
    return document.prose_blocks[-1] if document.prose_blocks else None


# ---------------------------------------------------------------------------
# Menetlusliik
# ---------------------------------------------------------------------------


def _tracks(
    documents: tuple[SourceDocument, ...], current: CurrentValues, builder: _Builder
) -> None:
    scored: list[tuple[str, int, list[textscan.SignalHit], SourceDocument]] = []
    for track, rule in vocab.TRACK_RULES.items():
        pooled: dict[str, textscan.SignalHit] = {}
        pooled_document: dict[str, SourceDocument] = {}
        for document in documents:
            for hit in textscan.count_signals(document, rule.signals):
                existing = pooled.get(hit.label)
                if existing is None or hit.points > existing.points:
                    pooled[hit.label] = hit
                    pooled_document[hit.label] = document
        hits = sorted(pooled.values(), key=lambda hit: -hit.points)
        total = sum(hit.points for hit in hits)
        if total <= 0:
            continue
        if rule.requires and not all(label in pooled for label in rule.requires):
            total = min(total, vocab.TRACK_HIGH_THRESHOLD - 1)
        if rule.ceiling == "MEDIUM":
            total = min(total, vocab.TRACK_HIGH_THRESHOLD - 1)
        scored.append((track, total, hits, pooled_document[hits[0].label]))

    if not scored:
        return
    eu_strength = max(
        (
            total
            for track, total, _, _ in scored
            if track in (Track.EU_INITIATIVE, Track.NATIONAL_TRANSPOSITION)
        ),
        default=0,
    )
    if eu_strength >= vocab.TRACK_EU_SUPPRESSES_DOMESTIC_AT:
        scored = [row for row in scored if row[0] != Track.DOMESTIC]
    scored.sort(key=lambda row: -row[1])
    offered = [row for row in scored if row[1] >= vocab.TRACK_MEDIUM_THRESHOLD]
    if not offered:
        return
    top_total = offered[0][1]
    runner_up = offered[1][1] if len(offered) > 1 else 0
    top_is_high = (
        top_total >= vocab.TRACK_HIGH_THRESHOLD and top_total - runner_up >= vocab.TRACK_HIGH_MARGIN
    )
    if (
        len(offered) > 1
        and offered[1][1] >= vocab.TRACK_HIGH_THRESHOLD
        and top_total - runner_up < vocab.TRACK_HIGH_MARGIN
    ):
        builder.conflicts.add(SuggestedField.TRACK)
        builder.notes[SuggestedField.TRACK] = "Materjal sobib mitme menetlusliigiga — vali ise."
    for position, (track, total, hits, document) in enumerate(offered):
        confidence = Confidence.HIGH if position == 0 and top_is_high else Confidence.MEDIUM
        first = hits[0]
        builder.add(
            _with_current(
                Candidate(
                    field=SuggestedField.TRACK,
                    value=str(track),
                    display=str(Track(track).label),
                    confidence=confidence,
                    rule=f"track_{str(track).casefold()}",
                    provenance=_provenance(document, first.block),
                    evidence=textscan.excerpt_around(first.block.text, first.start, first.end),
                    detail=_cue_line(tuple(hit.label for hit in hits[:4])),
                    score=total,
                ),
                current.track == track,
            )
        )


# ---------------------------------------------------------------------------
# Valdkonnad
# ---------------------------------------------------------------------------


def _areas(
    documents: tuple[SourceDocument, ...],
    policy_areas: dict[str, PolicyArea],
    current: CurrentValues,
    builder: _Builder,
) -> None:
    scored: list[tuple[PolicyArea, int, list[textscan.SignalHit], SourceDocument]] = []
    for key, signals in vocab.AREA_RULES.items():
        area = policy_areas.get(key)
        if area is None:
            continue
        pooled: dict[str, textscan.SignalHit] = {}
        pooled_document: dict[str, SourceDocument] = {}
        for document in documents:
            for hit in textscan.count_signals(document, signals):
                existing = pooled.get(hit.label)
                if existing is None or hit.points > existing.points:
                    pooled[hit.label] = hit
                    pooled_document[hit.label] = document
        hits = sorted(pooled.values(), key=lambda hit: -hit.points)
        total = sum(hit.points for hit in hits)
        if total >= vocab.AREA_MEDIUM_THRESHOLD:
            scored.append((area, total, hits, pooled_document[hits[0].label]))
    scored.sort(key=lambda row: (-row[1], row[0].sort_order))
    for area, total, hits, document in scored[: vocab.AREA_LIMIT]:
        first = hits[0]
        confidence = Confidence.HIGH if total >= vocab.AREA_HIGH_THRESHOLD else Confidence.MEDIUM
        builder.add(
            _with_current(
                Candidate(
                    field=SuggestedField.POLICY_AREAS,
                    value=str(area.pk),
                    display=area.name_et,
                    confidence=confidence,
                    rule=f"area_{area.key}",
                    provenance=_provenance(document, first.block),
                    evidence=textscan.excerpt_around(first.block.text, first.start, first.end),
                    detail=_cue_line(tuple(hit.label for hit in hits[:4])),
                    score=total,
                ),
                area.pk in current.policy_area_ids,
            )
        )


# ---------------------------------------------------------------------------
# Contacts and references — informational findings
# ---------------------------------------------------------------------------


def _contacts(documents: tuple[SourceDocument, ...], builder: _Builder) -> None:
    sender_addresses: set[str] = set()
    for document in documents:
        if document.email is None:
            continue
        from_name = document.email_value("from_name")
        from_email = document.email_value("from_email")
        header = _provenance(document, None, header=True)
        if from_name or from_email:
            sender_addresses.add(from_email.casefold())
            builder.findings.append(
                Candidate(
                    field=SuggestedField.SENDER_CONTACT,
                    value=from_email or from_name,
                    display=from_name or from_email,
                    confidence=Confidence.HIGH,
                    rule="email_from",
                    provenance=header,
                    evidence=f"Saatja: {from_name} <{from_email}>"
                    if from_name and from_email
                    else "",
                    detail=from_email if from_name else "",
                )
            )
        sent_at = document.email_value("sent_at")
        if sent_at:
            builder.findings.append(
                Candidate(
                    field=SuggestedField.EMAIL_SENT_AT,
                    value=sent_at,
                    display=_format_sent_at(sent_at),
                    confidence=Confidence.HIGH,
                    rule="email_date",
                    provenance=header,
                    detail="Kirja saatmise aeg, mitte Koja saabumise kuupäev.",
                )
            )
    for document in documents:
        for found in textscan.find_contacts(document):
            if found.email.casefold() in sender_addresses:
                continue
            confidence = (
                Confidence.MEDIUM
                if found.rule in ("labelled_contact", "signature_contact")
                else Confidence.LOW
            )
            candidate = Candidate(
                field=SuggestedField.DOCUMENT_CONTACT,
                value=found.email,
                display=found.name or found.email,
                confidence=confidence,
                rule=found.rule,
                provenance=_provenance(document, found.block),
                evidence=found.line,
                detail=found.email if found.name else "",
            )
            (builder.findings if confidence == Confidence.MEDIUM else builder.other).append(
                candidate
            )


def _format_sent_at(raw: str) -> str:
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    return f"{format_estonian_date(moment.date())} {moment:%H:%M}".strip()


def _references(documents: tuple[SourceDocument, ...], builder: _Builder) -> None:
    for document in documents:
        found = textscan.find_references(document)
        counted = 0
        for reference in found:
            field_name = (
                SuggestedField.EIS_REFERENCE
                if reference.kind in ("eis", "eis_url")
                else SuggestedField.SOURCE_URL
                if reference.kind == "url"
                else SuggestedField.EXTERNAL_REFERENCE
            )
            candidate = Candidate(
                field=field_name,
                value=reference.value,
                display=reference.value,
                confidence=reference.strength,
                rule=f"reference_{reference.kind}",
                provenance=_provenance(document, reference.block),
                evidence=reference.line,
                detail=vocab.REFERENCE_LABELS.get(reference.kind, ""),
            )
            if reference.strength == Confidence.LOW:
                builder.other.append(candidate)
                continue
            if counted >= vocab.REFERENCE_LIMIT:
                builder.other.append(candidate)
                continue
            counted += 1
            builder.findings.append(candidate)
