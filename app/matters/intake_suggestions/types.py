"""What the assisted-intake analyser proposes, and how it says why.

Every value the analyser produces is a *candidate*: a proposed value for one
Matter field (or an informational finding with no canonical field), with a
confidence, the rule that produced it, the exact document and place it came
from, and a short excerpt a person can check it against. Nothing here is a
fact about the Matter. A candidate becomes canonical data only when a person
puts it on the edit form and presses Salvesta, through the same services every
other edit goes through (docs/adr/0060).

Confidence is three named levels rather than a score. Numbers invite
thresholds nobody remembers the meaning of; three words carry their own
contract (assisted-intake brief §15):

* ``HIGH`` — may pre-fill an *empty* unsaved form control.
* ``MEDIUM`` — shown as a suggestion the person explicitly chooses.
* ``LOW`` — kept out of the primary form; shown under «Muud leiud» if useful.

A field whose candidates conflict — two different dates both introduced by
*palume esitada hiljemalt* — pre-fills nothing, whatever the individual
confidences say. Choosing between them is the lawyer's decision, and the
analyser's job is to show both with their evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


#: How the three levels read on the page. Words, never colour alone.
CONFIDENCE_LABELS: dict[str, str] = {
    Confidence.HIGH: "tugev",
    Confidence.MEDIUM: "võimalik",
    Confidence.LOW: "nõrk",
}


class SourceKind(StrEnum):
    """Where the characters a candidate was read from actually came from.

    The distinction between native text and OCR is the extraction system's
    own (`app.documents.enums.TextSource`) and is carried through unchanged:
    a deadline read off a scanned page is a recognition engine's guess, and
    the page says so.
    """

    EMAIL_HEADER = "EMAIL_HEADER"
    NATIVE_TEXT = "NATIVE_TEXT"
    OCR_TEXT = "OCR_TEXT"
    FILENAME = "FILENAME"


SOURCE_LABELS: dict[str, str] = {
    SourceKind.EMAIL_HEADER: "kirja päis",
    SourceKind.NATIVE_TEXT: "dokumendi tekst",
    SourceKind.OCR_TEXT: "OCR tekst",
    SourceKind.FILENAME: "failinimi",
}


class SuggestedField(StrEnum):
    """What a candidate is *for*.

    The first five are canonical Matter fields the edit form carries. The rest
    are informational findings: facts worth showing that have no correct
    canonical home yet (docs/adr/0060 records why, and what the future model
    should look like).
    """

    TITLE = "title"
    SOURCE_ORGANISATIONS = "source_organisations"
    RESPONSE_DEADLINE = "response_deadline"
    TRACK = "track"
    POLICY_AREAS = "policy_areas"

    SENDER_CONTACT = "sender_contact"
    DOCUMENT_CONTACT = "document_contact"
    EMAIL_SENT_AT = "email_sent_at"
    EXTERNAL_REFERENCE = "external_reference"
    EIS_REFERENCE = "eis_reference"
    SOURCE_URL = "source_url"
    RELATIVE_DEADLINE = "relative_deadline"
    ORGANISATION_MENTION = "organisation_mention"


#: The fields the edit form can take a value for, in the order the panel
#: shows them. Everything else is a finding.
FORM_FIELDS: tuple[str, ...] = (
    SuggestedField.TITLE,
    SuggestedField.SOURCE_ORGANISATIONS,
    SuggestedField.RESPONSE_DEADLINE,
    SuggestedField.TRACK,
    SuggestedField.POLICY_AREAS,
)

FIELD_LABELS: dict[str, str] = {
    SuggestedField.TITLE: "Pealkiri",
    SuggestedField.SOURCE_ORGANISATIONS: "Kellelt",
    SuggestedField.RESPONSE_DEADLINE: "Arvamuse tähtaeg",
    SuggestedField.TRACK: "Menetlusliik",
    SuggestedField.POLICY_AREAS: "Valdkonnad",
    SuggestedField.SENDER_CONTACT: "Saatja kontakt",
    SuggestedField.DOCUMENT_CONTACT: "Kontakt dokumendis",
    SuggestedField.EMAIL_SENT_AT: "Kirja saatmise aeg",
    SuggestedField.EXTERNAL_REFERENCE: "Viide",
    SuggestedField.EIS_REFERENCE: "EIS",
    SuggestedField.SOURCE_URL: "Link dokumendis",
    SuggestedField.RELATIVE_DEADLINE: "Suhteline tähtaeg",
    SuggestedField.ORGANISATION_MENTION: "Nimetatud asutus",
}

#: How long an evidence excerpt may be. Long enough to hold the sentence that
#: names a deadline, short enough that the panel stays a panel.
EXCERPT_LIMIT = 220


@dataclass(frozen=True)
class Provenance:
    """Exactly where a candidate was read from."""

    document_id: Any
    version_id: Any
    filename: str
    source_kind: str
    #: The extraction system's own locator label — ``lk 1``, ``kirja päis``,
    #: ``lõigud 1–40``. Empty when the source has no inner location.
    locator_label: str = ""

    @property
    def is_ocr(self) -> bool:
        return self.source_kind == SourceKind.OCR_TEXT

    @property
    def source_label(self) -> str:
        """``kaaskiri.pdf · lk 1 · OCR`` — the line under every suggestion."""
        parts = [self.filename]
        if self.locator_label:
            parts.append(self.locator_label)
        elif self.source_kind == SourceKind.EMAIL_HEADER:
            parts.append(SOURCE_LABELS[SourceKind.EMAIL_HEADER])
        if self.is_ocr:
            parts.append("OCR")
        return " · ".join(parts)


@dataclass(frozen=True)
class Candidate:
    """One proposed value, with everything needed to judge it."""

    field: str
    #: The canonical form the form control takes: an ISO date, a ``Track``
    #: value, a ``PolicyArea`` primary key, an ``Organisation`` primary key, a
    #: title string, an e-mail address.
    value: str
    #: What a person reads: ``18.9.2026``, ``ELi algatus``, ``Kliimaministeerium``.
    display: str
    confidence: str
    rule: str
    provenance: Provenance
    #: A short, exact excerpt of the source around the value. Text, never
    #: markup; the template escapes it like any other string.
    evidence: str = ""
    #: A second line where one is useful: the address under a contact name,
    #: the kind of reference, the words that scored a policy area.
    detail: str = ""
    #: The value the form control carries for this candidate, where it differs
    #: from ``value`` — a date is ``18.9.2026`` on the form and ISO in ``value``.
    form_value: str = ""
    score: int = 0
    #: True when the Matter already carries this exact value. Shown, so the
    #: person sees the document agrees, but never offered again.
    already_current: bool = False
    #: True when the GET put this value into an empty form control. Set by
    #: `prefill.py`, never by the rules: whether a value may pre-fill is a
    #: decision about the form, not about the text.
    prefilled: bool = False

    @property
    def label(self) -> str:
        return FIELD_LABELS.get(self.field, self.field)

    @property
    def detail_is_address(self) -> bool:
        """A contact's detail is its address and sits beside the name."""
        return self.field in (SuggestedField.SENDER_CONTACT, SuggestedField.DOCUMENT_CONTACT)

    @property
    def control_value(self) -> str:
        return self.form_value or self.value

    @property
    def confidence_label(self) -> str:
        return CONFIDENCE_LABELS.get(self.confidence, self.confidence)

    @property
    def is_high(self) -> bool:
        return self.confidence == Confidence.HIGH


@dataclass(frozen=True)
class FieldSuggestions:
    """Every candidate for one form field, ranked, with the conflict verdict."""

    field: str
    candidates: tuple[Candidate, ...] = ()
    #: Two or more HIGH candidates that disagree. Nothing pre-fills; the person
    #: chooses. Stated as a flag rather than derived in the template, so the
    #: rule lives in one place (`analysis.py`).
    conflict: bool = False
    #: A sentence for the panel when the candidates need one — «Kaks erinevat
    #: tähtaega, vali ise.»
    note: str = ""

    @property
    def label(self) -> str:
        return FIELD_LABELS.get(self.field, self.field)

    @property
    def high(self) -> tuple[Candidate, ...]:
        return tuple(c for c in self.candidates if c.is_high and not c.already_current)

    @property
    def offered(self) -> tuple[Candidate, ...]:
        """What the primary panel shows: HIGH and MEDIUM, never LOW."""
        return tuple(c for c in self.candidates if c.confidence != Confidence.LOW)

    @property
    def prefill_candidate(self) -> Candidate | None:
        """The one candidate that may pre-fill a single-valued control.

        Exactly one HIGH candidate and no conflict. Anything else — none, two,
        a disagreement — is the person's call.
        """
        if self.conflict:
            return None
        high = self.high
        return high[0] if len(high) == 1 else None

    @property
    def prefill_candidates(self) -> tuple[Candidate, ...]:
        """Every HIGH candidate, for a multi-valued control such as Valdkonnad."""
        if self.conflict:
            return ()
        return self.high


@dataclass(frozen=True)
class DocumentReadiness:
    """One document's part in the analysis, and why it may have had none."""

    document_id: Any
    filename: str
    role: str
    role_label: str
    extraction_state: str
    state_label: str
    state_tone: str
    analysed: bool
    from_ocr: bool = False
    truncated: bool = False
    note: str = ""


@dataclass(frozen=True)
class IntakeAnalysis:
    """Everything the review page needs, already decided.

    Assembled in one place so the template judges nothing: which candidates
    conflict, which are pre-filled, what each document's state means. A
    template that decided whether a value is safe to pre-fill would be a
    template somebody edits without noticing they changed the contract.
    """

    documents: tuple[DocumentReadiness, ...]
    fields: dict[str, FieldSuggestions]
    #: Informational findings in panel order: sender contact, sent time,
    #: contacts in documents, references, links.
    findings: tuple[Candidate, ...] = ()
    #: LOW-confidence material a person may still want: organisations merely
    #: mentioned, stray addresses, further links.
    other_findings: tuple[Candidate, ...] = ()
    #: Configuration problems a maintainer should see — a rule keyed on a
    #: policy area that no longer exists. Never a reason to guess.
    diagnostics: tuple[str, ...] = ()
    #: Form fields the GET pre-filled, so the panel can say «vormil
    #: eeltäidetud» beside exactly those. Filled in by `prefill.py`.
    prefilled: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def analysed_documents(self) -> tuple[DocumentReadiness, ...]:
        return tuple(d for d in self.documents if d.analysed)

    @property
    def waiting_documents(self) -> tuple[DocumentReadiness, ...]:
        return tuple(d for d in self.documents if d.extraction_state in ("PENDING", "PROCESSING"))

    @property
    def failed_documents(self) -> tuple[DocumentReadiness, ...]:
        return tuple(d for d in self.documents if d.extraction_state == "FAILED")

    @property
    def has_text(self) -> bool:
        return bool(self.analysed_documents)

    @property
    def is_waiting(self) -> bool:
        return bool(self.waiting_documents)

    @property
    def form_suggestions(self) -> list[FieldSuggestions]:
        """The five form fields, in panel order, only where something was found."""
        return [
            self.fields[name]
            for name in FORM_FIELDS
            if name in self.fields and self.fields[name].offered
        ]

    @property
    def has_anything(self) -> bool:
        return bool(self.form_suggestions or self.findings or self.other_findings)

    def prefilled_values(self, field_name: str) -> tuple[str, ...]:
        return self.prefilled.get(field_name, ())
