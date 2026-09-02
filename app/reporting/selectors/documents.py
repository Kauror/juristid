"""Extraction and searchability, reported as the several different facts they are.

There is one number this module exists to avoid printing: *"16 000 failed
extraction"*.

The real deployment produces exactly that figure if the states are flattened.
Stage 2B decided that with ``REAL_DATA_ALLOWED`` a file nobody has scanned is
not opened by a parser — the scanner is a Secure Pilot Gate deliverable that
does not exist yet. Stage 2D imports historical evidence with
``malware_scan_state=PENDING``, because no scanner has ever run on a file from
2014 and saying otherwise would be inventing a control. Both decisions are
right. Together they mean a large part of the corpus is *waiting on a control*,
which is neither a failure, nor a queue, nor a defect (main, commit 34d91b1).

What the reader is told about that state
----------------------------------------
The gate stays exactly where it is. What changed is the sentence beside the
number. *Ootab pahavarakontrolli* named the mechanism, and every reader who saw
it understood the same wrong thing: that these files might be infected and that
somebody has an unresolved safety question to answer. Neither is true — the
Juristid corpus is known to be free of malware — and a statistic that leaves a
department believing its own archive is suspect is worse than no statistic.

So the business-facing wording describes the real situation: the text of these
files has not been extracted yet, because a technical precondition of this
system's extraction pipeline is not satisfied. The stored
``malware_scan_state`` is untouched, ``eligibility_q`` is untouched, and no file
becomes extractable because a label was rewritten (Statistika QA §4).

So five states are reported separately, and the eligibility rule is imported
from the orchestrator rather than restated:

* **eligible** — a worker may open it;
* **awaiting extraction** — it may not be opened yet, and that is expected here;
* **done**, **failed**, **not applicable** — the terminal outcomes.

``NOT_APPLICABLE`` is a success. A signed container has no extracted text
because nothing will ever open one, so it is excluded from the searchability
denominator instead of dragging the coverage down and inviting somebody to
"fix" it (brief 31, 32, 33).
"""

from __future__ import annotations

from django.db.models import QuerySet

from app.documents.enums import ExtractionState
from app.documents.extraction.orchestrator import eligibility_q
from app.documents.models import Document, DocumentVersion
from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext
from app.reporting.metric_catalogue import definition
from app.reporting.metric_types import MetricResult, Segment
from app.reporting.selectors.base import simple_result, visible_matters


def visible_versions(context: ReportingContext) -> QuerySet[DocumentVersion]:
    """Evidence versions on Documents this viewer may read.

    Through ``Document.objects.visible_to`` rather than around it: a document's
    own override can restrict it further than its Matter, and a count that
    reached the versions directly would quietly include those.
    """
    return DocumentVersion.objects.filter(
        document__in=Document.objects.visible_to(context.viewer).filter(
            matter__in=visible_matters(context)
        )
    )


def awaiting_scanner(context: ReportingContext) -> QuerySet[DocumentVersion]:
    """Exactly ``orchestrator.awaiting_scanner``, scoped to this viewer.

    The function keeps the orchestrator's name because it is the orchestrator's
    population — renaming the code would hide which gate this is. Only what the
    reader is shown changed.
    """
    return (
        visible_versions(context)
        .filter(extraction_state=ExtractionState.PENDING)
        .exclude(eligibility_q())
    )


def _state_result(
    context: ReportingContext, key: str, queryset: QuerySet[DocumentVersion]
) -> MetricResult:
    return simple_result(
        definition(key),
        context=context,
        value=queryset.count(),
        population_count=context.shared(
            "documents.visible_versions", lambda: visible_versions(context).count()
        ),
        # No URL: the product has no list of evidence versions, and a link that
        # opened a Matter register filtered by nothing in particular would be a
        # promise this number cannot keep. The definition says where the files
        # themselves are read instead (Stage-2E brief 38, 39).
        url="",
    )


def extraction_eligible(context: ReportingContext) -> MetricResult:
    return _state_result(
        context, keys.EXTRACTION_ELIGIBLE, visible_versions(context).filter(eligibility_q())
    )


def extraction_success(context: ReportingContext) -> MetricResult:
    return _state_result(
        context,
        keys.EXTRACTION_SUCCESS,
        visible_versions(context).filter(extraction_state=ExtractionState.DONE),
    )


def extraction_pending(context: ReportingContext) -> MetricResult:
    """Queued or in progress — and genuinely offerable to a worker.

    A version the queue will not offer is not pending, however much its column
    says ``PENDING``. Reporting it here would describe a backlog that no worker
    is going to work through.
    """
    queued = (
        visible_versions(context)
        .filter(
            extraction_state__in=(ExtractionState.PENDING, ExtractionState.PROCESSING),
        )
        .filter(eligibility_q())
    )
    return _state_result(context, keys.EXTRACTION_PENDING, queued)


def extraction_awaiting_scanner(context: ReportingContext) -> MetricResult:
    result = _state_result(context, keys.EXTRACTION_AWAITING_SCANNER, awaiting_scanner(context))
    return result.with_note(
        "Failid on teadaolevalt pahavaravabad. Ootel on tekstitöötlus, mitte "
        "turvakontrolli tulemus: eraldamise tehniline eeltingimus ei ole veel "
        "täidetud. See ei ole viga ega järjekord."
    )


def extraction_failed(context: ReportingContext) -> MetricResult:
    result = _state_result(
        context,
        keys.EXTRACTION_FAILED,
        visible_versions(context).filter(extraction_state=ExtractionState.FAILED),
    )
    return result.with_note("Allkirjaümbrikud ja tekstitöötlust ootavad failid ei kuulu siia.")


def extraction_not_applicable(context: ReportingContext) -> MetricResult:
    result = _state_result(
        context,
        keys.EXTRACTION_NOT_APPLICABLE,
        visible_versions(context).filter(extraction_state=ExtractionState.NOT_APPLICABLE),
    )
    return result.with_note("See on ootuspärane ja edukas seisund, mitte puudujääk.")


def extraction_states(context: ReportingContext) -> tuple[Segment, ...]:
    """The five states as one chart, adding up to the visible version count."""
    versions = visible_versions(context)
    waiting = context.shared(
        "documents.awaiting_scanner", lambda: awaiting_scanner(context).count()
    )
    pending = (
        versions.filter(extraction_state__in=(ExtractionState.PENDING, ExtractionState.PROCESSING))
        .filter(eligibility_q())
        .count()
    )
    return (
        Segment(
            label="Eraldatud", value=versions.filter(extraction_state=ExtractionState.DONE).count()
        ),
        Segment(label="Järjekorras", value=pending),
        Segment(
            label="Ootab tekstitöötlust",
            value=waiting,
            note="Ei ole viga ega pahavarakahtlus",
        ),
        Segment(
            label="Ei kohaldu",
            value=versions.filter(extraction_state=ExtractionState.NOT_APPLICABLE).count(),
            note="Näiteks allkirjaümbrikud",
        ),
        Segment(
            label="Ebaõnnestus",
            value=versions.filter(extraction_state=ExtractionState.FAILED).count(),
        ),
    )


def searchable_document_coverage(context: ReportingContext) -> MetricResult:
    """How much of the openable content is actually extracted.

    The denominator excludes what no parser opens. Including it would make the
    coverage look bad for a reason that is a decision rather than a gap — and
    the number would then never reach 100 %, so nobody would use it.
    """
    spec = definition(keys.SEARCHABLE_DOCUMENT_COVERAGE)
    versions = visible_versions(context)
    total = context.shared("documents.visible_versions", versions.count)
    openable = versions.exclude(extraction_state=ExtractionState.NOT_APPLICABLE).count()
    extracted = versions.filter(extraction_state=ExtractionState.DONE).count()
    waiting = context.shared(
        "documents.awaiting_scanner", lambda: awaiting_scanner(context).count()
    )

    notes = [
        "Nimetajast on välja jäetud failid, mida ükski parser ei ava.",
    ]
    if waiting:
        notes.append(
            f"{waiting} faili ootab tekstitöötlust — need on teadaolevalt "
            f"pahavaravabad, kuid nende teksti ei ole veel eraldatud. Kuni see "
            f"pole tehtud, ei saa see näitaja väita otsitavuse täielikkust."
        )

    percentage = round(100.0 * extracted / openable) if openable else 0
    return simple_result(
        spec,
        context=context,
        value=percentage,
        population_count=total,
        eligible_count=openable,
        coverage_count=extracted,
        coverage_denominator=openable,
        url="",
        notes=tuple(notes),
    )
