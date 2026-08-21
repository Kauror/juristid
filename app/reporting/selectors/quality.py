"""Andmekvaliteet — where reporting or source completeness is genuinely short.

The hard part of this page is not finding problems. It is refusing to report
things that only look like problems, because a queue that cries wolf is a queue
people stop opening.

**These are not defects, and none of them appears here:**

* an ARCHIVE Matter with no next action — an archive row is not live work;
* an old Matter with no modern stage — the register had no stage column before
  2023;
* a OneNote-only Matter with no ``YYYY_N`` — the register never had one;
* an ASiC-E with no derivative — nothing will ever open it;
* an attachment that is zero bytes in OneNote itself — a fact about the source;
* an unclassified 2013 archive Matter — a coverage limitation, reported as
  coverage, not as an error (brief 36, 37).

Everything below is something a person can actually do something about, and
every row links to the surface where they would do it.

Reconciliation counts are scoped through the Matter wherever a candidate names
one. The queue that resolves them is administrator-only and stays that way; a
reader without that role sees the number and is told where it is handled rather
than being handed a link that 404s.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from django.db.models import Q, QuerySet
from django.urls import reverse

from app.accounts.enums import UserRole
from app.core.authorization import apply as apply_scope
from app.core.authorization import matter_visibility_q, scope_for_user
from app.legacy_import.source_pages import CandidateClass, CandidateState, HistoricalMatchCandidate
from app.matters.selectors import MISSING
from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext
from app.reporting.metric_catalogue import definition
from app.reporting.metric_types import MetricResult, Segment
from app.reporting.selectors import activity, documents, historical
from app.reporting.selectors.base import (
    corpus_url,
    count,
    eligible_matters,
    grouped_count,
    simple_result,
)


def visible_candidates(context: ReportingContext) -> QuerySet[HistoricalMatchCandidate]:
    """Reconciliation candidates, scoped through the Matter they name.

    A candidate with no Matter is an unlinked page: there is no Matter to
    authorize through, and the row carries no Matter content — only the audit's
    own class and score. Those are included; a candidate that *does* name a
    Matter is included only if the reader may see it.
    """
    scope = scope_for_user(context.viewer)
    with_matter = apply_scope(
        HistoricalMatchCandidate.objects.filter(matter__isnull=False),
        matter_visibility_q(scope, prefix="matter__"),
    )
    return HistoricalMatchCandidate.objects.filter(
        Q(matter__isnull=True) | Q(pk__in=with_matter.values("pk"))
    )


def can_open_review_queue(context: ReportingContext) -> bool:
    return getattr(context.viewer, "role", None) == UserRole.ADMINISTRATOR


def _review_url(context: ReportingContext, **params: str) -> str:
    if not can_open_review_queue(context):
        return ""
    base = reverse("legacy_import:review_queue")
    query = "&".join(f"{key}={value}" for key, value in params.items() if value)
    return f"{base}?{query}" if query else base


#: Appended to a reconciliation row when the reader cannot open the queue. A
#: number with no link and no explanation reads as a dead end; this says the
#: work has a home and whose it is (the queue can create Matters, so it stays
#: administrator-only).
_ADMIN_ONLY = " Lahendatakse halduri ajaloo-ülevaatuse vaates."


def _reconciliation_note(context: ReportingContext, explanation: str) -> str:
    return explanation if can_open_review_queue(context) else explanation + _ADMIN_ONLY


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def reconciliation_pending(context: ReportingContext) -> MetricResult:
    spec = definition(keys.RECONCILIATION_PENDING)
    pending = visible_candidates(context).filter(state=CandidateState.PENDING)
    return simple_result(
        spec,
        context=context,
        value=pending.count(),
        population_count=visible_candidates(context).count(),
        url=_review_url(context, olek=CandidateState.PENDING.value),
    )


def reconciliation_conflict(context: ReportingContext) -> MetricResult:
    spec = definition(keys.RECONCILIATION_CONFLICT)
    conflicts = visible_candidates(context).filter(
        candidate_class=CandidateClass.CONFLICT, state=CandidateState.PENDING
    )
    return simple_result(
        spec,
        context=context,
        value=conflicts.count(),
        population_count=visible_candidates(context).count(),
        url=_review_url(
            context, olek=CandidateState.PENDING.value, klass=CandidateClass.CONFLICT.value
        ),
        notes=("Vastuoluline tõendus. Oletamine paneks kirja vale teema alla.",),
    )


def reconciliation_by_class(context: ReportingContext) -> MetricResult:
    spec = definition(keys.RECONCILIATION_BY_CLASS)
    pending = visible_candidates(context).filter(state=CandidateState.PENDING)
    labels = dict(CandidateClass.choices)
    rows = pending.values("candidate_class").annotate(total=grouped_count()).order_by("-total")
    segments = tuple(
        Segment(
            label=labels.get(row["candidate_class"], row["candidate_class"]),
            value=row["total"],
            url=_review_url(
                context,
                olek=CandidateState.PENDING.value,
                klass=row["candidate_class"],
            ),
        )
        for row in rows
    )
    return simple_result(
        spec,
        context=context,
        value=pending.count(),
        segments=segments,
        url=_review_url(context, olek=CandidateState.PENDING.value),
    )


def unlinked_substantive_pages(context: ReportingContext) -> MetricResult:
    spec = definition(keys.UNLINKED_SUBSTANTIVE_PAGES)
    unlinked = visible_candidates(context).filter(
        candidate_class=CandidateClass.UNLINKED_PAGE, state=CandidateState.PENDING
    )
    return simple_result(
        spec,
        context=context,
        value=unlinked.count(),
        population_count=visible_candidates(context).count(),
        url=_review_url(
            context, olek=CandidateState.PENDING.value, klass=CandidateClass.UNLINKED_PAGE.value
        ),
    )


# ---------------------------------------------------------------------------
# The queues
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityQueue:
    """One actionable data-quality state, with somewhere to go about it."""

    key: str
    label: str
    count: int
    url: str
    explanation: str
    #: Set when the number is a limitation to understand rather than a job to
    #: do. Rendered differently, and excluded from the attention total.
    is_coverage_note: bool = False


def _matters_missing_policy_area(context: ReportingContext) -> int:
    """Only *active* Matters. An unclassified 2013 archive row is not a task."""
    active = eligible_matters(context, definition(keys.ACTIVE_WITHOUT_STAGE)).filter(
        is_open=True, policy_areas__isnull=True
    )
    return count(active)


def _unresolved_legacy_organisations(context: ReportingContext) -> int:
    """Register rows whose counterparty never resolved to an Organisation.

    Both directions, because an unmapped sender and an unmapped addressee are
    the same job: somebody has to say which institution the source meant.
    """
    matters = eligible_matters(context, definition(keys.ACTIVE_WITHOUT_STAGE)).filter(
        is_open=True,
        source_organisation__isnull=True,
        addressee_organisation__isnull=True,
    )
    return count(matters)


def _from_metric(key: str, label: str, result: MetricResult, explanation: str) -> QualityQueue:
    """A queue row that borrows its count *and its link* from the metric.

    Rebuilding the link here would be a second definition of the same
    population, and the first time the two drifted the queue would send an
    operator to a list that does not contain what the number promised.
    """
    return QualityQueue(
        key=key,
        label=label,
        count=result.value,
        url=result.drillthrough_url,
        explanation=explanation,
    )


def _materials_state_url(context: ReportingContext, state: str) -> str:
    return (
        f"{reverse('reporting:materials')}?"
        f"{urlencode({'periood': context.period.key, 'seisund': state})}"
    )


def queues(context: ReportingContext) -> list[QualityQueue]:
    """Everything the Andmekvaliteet tab lists, in the order it is acted on."""
    rows = [
        QualityQueue(
            key="reconciliation_conflict",
            label="Ajaloo sidumise vastuolud",
            count=reconciliation_conflict(context).value,
            url=_review_url(
                context, olek=CandidateState.PENDING.value, klass=CandidateClass.CONFLICT.value
            ),
            explanation=_reconciliation_note(
                context, "Tõendus on omavahel vastuolus. Otsustada saab ainult inimene."
            ),
        ),
        QualityQueue(
            key="reconciliation_review",
            label="Ajaloo sidumine vajab ülevaatust",
            count=visible_candidates(context)
            .filter(candidate_class=CandidateClass.REVIEW_REQUIRED, state=CandidateState.PENDING)
            .count(),
            url=_review_url(
                context,
                olek=CandidateState.PENDING.value,
                klass=CandidateClass.REVIEW_REQUIRED.value,
            ),
            explanation=_reconciliation_note(
                context, "Pakutud seos, mida keegi ei ole veel kinnitanud ega tagasi lükanud."
            ),
        ),
        QualityQueue(
            key="reconciliation_strong",
            label="Tugevad sidumisettepanekud ootel",
            count=visible_candidates(context)
            .filter(candidate_class=CandidateClass.STRONG, state=CandidateState.PENDING)
            .count(),
            url=_review_url(
                context, olek=CandidateState.PENDING.value, klass=CandidateClass.STRONG.value
            ),
            explanation=_reconciliation_note(
                context, "Tõenäoliselt õige seos, mis ootab kinnitust."
            ),
        ),
        QualityQueue(
            key="broken_excel_link",
            label="Katkised Exceli lingid",
            count=visible_candidates(context)
            .filter(candidate_class=CandidateClass.BROKEN_EXCEL_LINK, state=CandidateState.PENDING)
            .count(),
            url=_review_url(
                context,
                olek=CandidateState.PENDING.value,
                klass=CandidateClass.BROKEN_EXCEL_LINK.value,
            ),
            explanation=_reconciliation_note(
                context, "Registri link ei viita ühelegi arhiveeritud lehele."
            ),
        ),
        QualityQueue(
            key="unlinked_page",
            label="Sidumata sisukad lehed",
            count=unlinked_substantive_pages(context).value,
            url=_review_url(
                context,
                olek=CandidateState.PENDING.value,
                klass=CandidateClass.UNLINKED_PAGE.value,
            ),
            explanation=_reconciliation_note(
                context, "Teemalaadne OneNote'i leht, mis ei kuulu ühegi teema alla."
            ),
        ),
        _from_metric(
            "active_without_owner",
            "Aktiivne teema ilma vastutajata",
            activity.active_without_owner(context),
            "Avatud täielik teema, mille eest ei vastuta praegu keegi.",
        ),
        _from_metric(
            "active_without_next_action",
            "Aktiivne teema ilma järgmise tegevuseta",
            activity.active_without_next_action(context),
            "Ilma järgmise tegevuseta teema kaob vaikselt igalt töölaualt.",
        ),
        _from_metric(
            "active_without_stage",
            "Aktiivne teema ilma hetkeseisuta",
            activity.active_without_stage(context),
            "Avatud teema, mille menetlusetapp ei ole määratud.",
        ),
        QualityQueue(
            key="active_without_policy_area",
            label="Aktiivne teema ilma valdkonnata",
            count=_matters_missing_policy_area(context),
            url=corpus_url(context, olek="avatud", valdkond=MISSING),
            explanation="Ainult avatud teemad. Vana klassifitseerimata arhiivirida ei ole viga.",
        ),
        QualityQueue(
            key="unresolved_organisation",
            label="Aktiivne teema ilma asutuseta",
            count=_unresolved_legacy_organisations(context),
            url=corpus_url(context, olek="avatud", saatja=MISSING, adressaat=MISSING),
            explanation="Ei saatjat ega adressaati. Sageli lahendamata nimevaste registrist.",
        ),
        QualityQueue(
            key="materialisation_failed",
            label="Materjali kopeerimine ebaõnnestus",
            count=historical.materialisation_failed(context).value,
            url=_materials_state_url(context, "unavailable"),
            explanation="Fail on allikas olemas, aga selle ülekandmine ei õnnestunud.",
        ),
        QualityQueue(
            key="extraction_failed",
            label="Teksti eraldamine ebaõnnestus",
            count=documents.extraction_failed(context).value,
            url="",
            explanation="Tõeline parseri viga. Allkirjaümbrikud ei ole siin.",
        ),
        QualityQueue(
            key="reading_order",
            label="Ebaselge lugemisjärjekorraga lehti",
            count=historical.reading_order_ambiguous(context).value,
            url="",
            explanation=(
                "Sisu on olemas, kuid narratiivi järjekord võib olla ebatäpne. "
                "Piirang, mitte parandatav viga."
            ),
            is_coverage_note=True,
        ),
        QualityQueue(
            key="awaiting_scanner",
            label="Ootab pahavarakontrolli",
            count=documents.extraction_awaiting_scanner(context).value,
            url="",
            explanation=(
                "Neid ei töödelda enne, kui skanner on olemas. Ootuspärane "
                "seisund, mitte tegevusnimekiri."
            ),
            is_coverage_note=True,
        ),
        QualityQueue(
            key="materialisation_empty",
            label="Allikas tühjad manused",
            count=historical.visible_resources(context, state="empty").count(),
            url=_materials_state_url(context, "empty"),
            explanation=("Manus on OneNote'is ise null baiti. Allika fakt, mitte impordi viga."),
            is_coverage_note=True,
        ),
    ]
    return [row for row in rows if row.count]


def data_quality_attention(context: ReportingContext) -> MetricResult:
    """How many rows are waiting for a person, coverage notes excluded."""
    spec = definition(keys.DATA_QUALITY_ATTENTION)
    actionable = [row for row in queues(context) if not row.is_coverage_note]
    return simple_result(
        spec,
        context=context,
        value=sum(row.count for row in actionable),
        population_count=len(actionable),
        segments=tuple(
            Segment(label=row.label, value=row.count, url=row.url) for row in actionable
        ),
        url=reverse("reporting:quality"),
        notes=("Arhiivi õigustatud hõredus ei ole siin sees.",),
    )
