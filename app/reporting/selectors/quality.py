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

from django.db.models import Exists, OuterRef, Q, QuerySet
from django.urls import reverse

from app.accounts.enums import UserRole
from app.core.authorization import apply as apply_scope
from app.core.authorization import matter_visibility_q, scope_for_user
from app.legacy_import.source_pages import CandidateClass, CandidateState, HistoricalMatchCandidate
from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext
from app.reporting.metric_catalogue import definition
from app.reporting.metric_types import MetricResult, Segment
from app.reporting.selectors import activity, documents, historical
from app.reporting.selectors.base import (
    count,
    eligible_matters,
    grouped_count,
    register_url,
    simple_result,
)
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction


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


def _materials_state_url(context: ReportingContext, state: str) -> str:
    return (
        f"{reverse('reporting:materials')}?"
        f"{urlencode({'periood': context.period.key, 'seisund': state})}"
    )


def queues(context: ReportingContext) -> list[QualityQueue]:
    """Everything the Andmekvaliteet tab lists, in the order it is acted on."""
    has_open = NextAction.objects.filter(matter=OuterRef("pk"), status=ActionStatus.OPEN)
    active = eligible_matters(context, definition(keys.ACTIVE_WITHOUT_NEXT_ACTION)).filter(
        is_open=True
    )

    rows = [
        QualityQueue(
            key="reconciliation_conflict",
            label="Ajaloo sidumise vastuolud",
            count=reconciliation_conflict(context).value,
            url=_review_url(
                context, olek=CandidateState.PENDING.value, klass=CandidateClass.CONFLICT.value
            ),
            explanation="Tõendus on omavahel vastuolus. Otsustada saab ainult inimene.",
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
            explanation="Pakutud seos, mida keegi ei ole veel kinnitanud ega tagasi lükanud.",
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
            explanation="Tõenäoliselt õige seos, mis ootab kinnitust.",
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
            explanation="Registri link ei viita ühelegi arhiveeritud lehele.",
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
            explanation="Teemalaadne OneNote'i leht, mis ei kuulu ühegi teema alla.",
        ),
        QualityQueue(
            key="active_without_owner",
            label="Aktiivne teema ilma vastutajata",
            count=activity.active_without_owner(context).value,
            url=reverse("matters:inbox"),
            explanation="Avatud täielik teema, mille eest ei vastuta praegu keegi.",
        ),
        QualityQueue(
            key="active_without_next_action",
            label="Aktiivne teema ilma järgmise tegevuseta",
            count=count(active.annotate(has_action=Exists(has_open)).filter(has_action=False)),
            url=register_url(context, olek="avatud", liik="FULL", aasta=""),
            explanation="Ilma järgmise tegevuseta teema kaob vaikselt igalt töölaualt.",
        ),
        QualityQueue(
            key="active_without_stage",
            label="Aktiivne teema ilma hetkeseisuta",
            count=activity.active_without_stage(context).value,
            url=register_url(context, olek="avatud", liik="FULL", aasta=""),
            explanation="Avatud teema, mille menetlusetapp ei ole määratud.",
        ),
        QualityQueue(
            key="active_without_policy_area",
            label="Aktiivne teema ilma valdkonnata",
            count=_matters_missing_policy_area(context),
            url=register_url(context, olek="avatud", aasta=""),
            explanation="Ainult avatud teemad. Vana klassifitseerimata arhiivirida ei ole viga.",
        ),
        QualityQueue(
            key="unresolved_organisation",
            label="Aktiivne teema ilma asutuseta",
            count=_unresolved_legacy_organisations(context),
            url=register_url(context, olek="avatud", aasta=""),
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
