"""Assisted intake: deterministic suggestions read from extracted documents.

When incoming material has been captured and the extraction worker has
produced its derivatives, this package reads them and proposes Matter
metadata — a title, the sender, the response deadline, a Menetlusliik,
Valdkonnad — and surfaces facts that have no canonical field yet: the
sender's name and address, an EIS or ministry reference, a link.

It is not an AI feature and does not become one by degrees. Rules,
vocabularies and exact matching against the department's own catalogues;
no model, no embedding, no network, nothing leaving the host. Given the
same text, the same catalogue and the same rules, the result is the same
(docs/adr/0060).

It writes nothing. A GET of the review page computes suggestions, shows
them with their evidence, and pre-fills an empty form control only where
the confidence is high and nothing conflicts. Saving is the existing edit
form posting to the existing services, which is where the audit trail
records that a person changed the Matter.

Layout::

    types.py       the candidate model: field, value, confidence, provenance
    vocabulary.py  the rule tables a maintainer reads
    input.py       what is read — the current versions' live derivatives
    textscan.py    pure readers over text: dates, headings, contacts, …
    resolvers.py   the organisation and policy-area catalogues, loaded once
    analysis.py    findings to suggestions: precedence, confidence, conflict
    prefill.py     which suggestions may pre-fill an empty form control
"""

from __future__ import annotations

from app.matters.intake_suggestions.analysis import CurrentValues, analyse, analyse_matter
from app.matters.intake_suggestions.input import AnalysisInput, build_analysis_input
from app.matters.intake_suggestions.prefill import prefill_initial
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

__all__ = [
    "AnalysisInput",
    "Candidate",
    "Confidence",
    "CurrentValues",
    "DocumentReadiness",
    "FieldSuggestions",
    "IntakeAnalysis",
    "Provenance",
    "SourceKind",
    "SuggestedField",
    "analyse",
    "analyse_matter",
    "build_analysis_input",
    "prefill_initial",
]
