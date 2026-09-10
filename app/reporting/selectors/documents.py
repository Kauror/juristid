"""Extraction and searchability, reported as the several different facts they are.

There is one number this module exists to avoid printing: *"16 000 failed
extraction"*.

The real deployment produces exactly that figure if the states are flattened.
The historical corpus was imported without anyone parsing it, so most of it
sits at ``PENDING`` — which was never a failure and is not one now. What
changed with ADR 0069 is what ``PENDING`` *predicts*: corpus-wide extraction is
no longer a deployed service, so a pending version is one nobody has read and
nobody is about to, until an operator deliberately runs the worker.

Five states are reported separately, and the sixth thing to know is that one of
them is new:

* **done** — the text was extracted and is what search reads;
* **queued** — ``PENDING`` or ``PROCESSING``; nothing has read it;
* **read at intake** — ``INTAKE_READ``: a person watched this file being read
  on `Uus teema`, the answers went onto the form, and no permanent text was
  ever made from it. Terminal and successful (docs/adr/0069);
* **failed**, **not applicable** — the other two terminal outcomes.

``NOT_APPLICABLE`` is a success. A signed container has no extracted text
because nothing will ever open one, so it is excluded from the searchability
denominator instead of dragging the coverage down and inviting somebody to
"fix" it (brief 31, 32, 33).

``INTAKE_READ`` is excluded from that denominator too, and for a *different*
reason worth stating plainly rather than folding into the one above: those
files could be extracted and deliberately are not. Counting them as a coverage
gap would invite exactly the corpus run this system was rebuilt to stop
starting by itself, so the note beside the number says what the trade is —
those files are not searchable by content, on purpose.
"""

from __future__ import annotations

from django.db.models import QuerySet

from app.documents.enums import ExtractionState
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


def read_at_intake(context: ReportingContext) -> QuerySet[DocumentVersion]:
    """Versions read once on `Uus teema` and deliberately never read again."""
    return visible_versions(context).filter(extraction_state=ExtractionState.INTAKE_READ)


def openable(context: ReportingContext) -> QuerySet[DocumentVersion]:
    """Versions some parser opens: everything but ``NOT_APPLICABLE``.

    The searchability denominator, and now also `extraction_eligible`. One
    function, because the card and the coverage percentage disagreeing about
    which files *could* have text is the kind of drift a reader has no way to
    detect.
    """
    return visible_versions(context).exclude(extraction_state=ExtractionState.NOT_APPLICABLE)


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
    return _state_result(context, keys.EXTRACTION_ELIGIBLE, openable(context))


def extraction_success(context: ReportingContext) -> MetricResult:
    return _state_result(
        context,
        keys.EXTRACTION_SUCCESS,
        visible_versions(context).filter(extraction_state=ExtractionState.DONE),
    )


def extraction_pending(context: ReportingContext) -> MetricResult:
    """Queued or in progress: nothing has derived text from these bytes.

    It used to exclude what the malware gate would not offer, which was the
    honest reading while a gate existed. There is none now, so ``PENDING``
    means what it says — and what it no longer implies is that anybody is
    working through it. Corpus extraction is an operator command, not a
    running service (docs/adr/0069).
    """
    queued = visible_versions(context).filter(
        extraction_state__in=(ExtractionState.PENDING, ExtractionState.PROCESSING),
    )
    return _state_result(context, keys.EXTRACTION_PENDING, queued)


def extraction_intake_read(context: ReportingContext) -> MetricResult:
    result = _state_result(context, keys.EXTRACTION_INTAKE_READ, read_at_intake(context))
    return result.with_note(
        "Need failid loeti teema loomise ajal ja vastused läksid vormile. "
        "Püsivat teksti neist ei tehtud, seega ei ole nende sisu otsitav — "
        "see on teadlik valik, mitte puudujääk."
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
    intake = context.shared("documents.read_at_intake", lambda: read_at_intake(context).count())
    pending = versions.filter(
        extraction_state__in=(ExtractionState.PENDING, ExtractionState.PROCESSING)
    ).count()
    return (
        Segment(
            label="Eraldatud", value=versions.filter(extraction_state=ExtractionState.DONE).count()
        ),
        Segment(label="Järjekorras", value=pending, note="Keegi ei ole neid veel lugenud"),
        Segment(
            label="Loetud teema loomisel",
            value=intake,
            note="Loetud vormil; püsivat teksti ei tehtud",
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
    open_count = openable(context).count()
    extracted = versions.filter(extraction_state=ExtractionState.DONE).count()
    intake = context.shared("documents.read_at_intake", lambda: read_at_intake(context).count())

    notes = [
        "Nimetajast on välja jäetud failid, mida ükski parser ei ava.",
    ]
    if intake:
        notes.append(
            f"{intake} faili loeti teema loomise ajal ja nende sisust ei tehtud "
            f"püsivat teksti, seega ei ole need sisu järgi otsitavad. See on "
            f"teadlik valik (docs/adr/0069), mitte tegemata töö."
        )

    percentage = round(100.0 * extracted / open_count) if open_count else 0
    return simple_result(
        spec,
        context=context,
        value=percentage,
        population_count=total,
        eligible_count=open_count,
        coverage_count=extracted,
        coverage_denominator=open_count,
        url="",
        notes=tuple(notes),
    )
