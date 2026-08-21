"""Koja tegevus — the structured work this system actually records.

Everything here is about *modern* operational data. Historical attachment
volume is not lawyer activity and never appears on this tab: sixteen thousand
files imported out of OneNote in one afternoon would swamp every real number
beside them and would measure the importer rather than the department
(Stage-2E brief 22).

The one distinction the whole tab rests on is the one Stage 1 built the model
around: **only DO + DEADLINE can be overdue.** A WAIT whose review date has
passed is due for a look, not missed. Waiting on a ministry is the ordinary
state of much of this work, and a queue that calls it a failure is a queue
people stop believing (master specification 18.8).
"""

from __future__ import annotations

from django.db.models import Exists, OuterRef, QuerySet
from django.urls import reverse

from app.matters.entry_enums import EntryKind
from app.matters.enums import RecordMode
from app.matters.models import Entry, Matter
from app.matters.selectors import MISSING
from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext
from app.reporting.metric_catalogue import definition
from app.reporting.metric_types import MetricResult, Segment
from app.reporting.selectors.base import (
    corpus_url,
    count,
    eligible_matters,
    grouped_count,
    population_for,
    simple_result,
    visible_matters,
)
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from app.workflow.models import NextAction


def open_actions(context: ReportingContext) -> QuerySet[NextAction]:
    """Current instructions on Matters this viewer may read.

    Scoped through the child chokepoint *and* through the page's Matter filters,
    for the same reason as everywhere else: the first is authorization, the
    second is the filter bar, and neither substitutes for the other.
    """
    return NextAction.objects.visible_to(context.viewer).filter(
        status=ActionStatus.OPEN,
        matter__in=visible_matters(context),
    )


def active_full_for(context: ReportingContext, key: str) -> QuerySet[Matter]:
    return eligible_matters(context, definition(key)).filter(is_open=True)


# ---------------------------------------------------------------------------
# Intake and inventory
# ---------------------------------------------------------------------------


def new_native_full_matters(context: ReportingContext) -> MetricResult:
    """Matters created in this system whose material arrived in the period.

    Measured on ``received_date``, never on the database row's creation time: a
    Matter entered a fortnight late belongs to the week the letter arrived.
    """
    spec = definition(keys.NEW_NATIVE_FULL_MATTERS)
    eligible = eligible_matters(context, spec)
    population_count = count(eligible)
    dated = count(eligible.filter(received_date__isnull=False))
    value = count(population_for(context, spec).filter(received_date__isnull=False))

    return simple_result(
        spec,
        context=context,
        value=value,
        population_count=population_count,
        eligible_count=value,
        coverage_count=dated,
        coverage_denominator=population_count,
        # Measured on `received_date`; the register filters on reporting year.
        # Linking there would open a population selected on a different column,
        # so this card is deliberately unlinked and says why.
        url="",
        notes=(
            "Mõõdetud saabumise kuupäeva järgi. Registris saab filtreerida "
            "aruandlusaasta järgi, mis ei ole sama veerg.",
        ),
    )


def active_without_next_action(context: ReportingContext) -> MetricResult:
    """The one attention state no date can produce.

    Without this query a Matter simply stops appearing anywhere and goes quiet,
    which is why it is a first-class number rather than something a lawyer is
    expected to notice.
    """
    spec = definition(keys.ACTIVE_WITHOUT_NEXT_ACTION)
    active = active_full_for(context, keys.ACTIVE_WITHOUT_NEXT_ACTION)
    has_open = NextAction.objects.filter(matter=OuterRef("pk"), status=ActionStatus.OPEN)
    quiet = active.annotate(has_action=Exists(has_open)).filter(has_action=False)
    return simple_result(
        spec,
        context=context,
        value=count(quiet),
        population_count=count(active),
        url=corpus_url(context, olek="avatud", liik=RecordMode.FULL.value, tegevus=MISSING),
        notes=("Arhiivikirjed ei ole siin. Arhiivikirjel ei peagi järgmist tegevust olema.",),
    )


def active_without_owner(context: ReportingContext) -> MetricResult:
    spec = definition(keys.ACTIVE_WITHOUT_OWNER)
    active = active_full_for(context, keys.ACTIVE_WITHOUT_OWNER)
    return simple_result(
        spec,
        context=context,
        value=count(active.filter(owner__isnull=True)),
        population_count=count(active),
        url=corpus_url(context, olek="avatud", liik=RecordMode.FULL.value, vastutaja=MISSING),
    )


def active_without_stage(context: ReportingContext) -> MetricResult:
    spec = definition(keys.ACTIVE_WITHOUT_STAGE)
    active = active_full_for(context, keys.ACTIVE_WITHOUT_STAGE)
    return simple_result(
        spec,
        context=context,
        value=count(active.filter(stage__isnull=True)),
        population_count=count(active),
        url=corpus_url(context, olek="avatud", liik=RecordMode.FULL.value, hetkeseis=MISSING),
    )


def response_deadlines_open(context: ReportingContext) -> MetricResult:
    spec = definition(keys.RESPONSE_DEADLINES_OPEN)
    active = active_full_for(context, keys.RESPONSE_DEADLINES_OPEN)
    ahead = active.filter(response_deadline__gte=context.today)
    return simple_result(
        spec,
        context=context,
        value=count(ahead),
        population_count=count(active),
        url="",
        notes=(
            "Registris ei ole eraldi tähtajafiltrit, seega avaneb siit "
            "täisnimekiri „Teemad“ vaates sorteerituna tähtaja järgi.",
        ),
    )


# ---------------------------------------------------------------------------
# Next actions
# ---------------------------------------------------------------------------


def overdue_do_deadline(context: ReportingContext) -> MetricResult:
    spec = definition(keys.OVERDUE_DO_DEADLINE)
    actions = open_actions(context).filter(
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date__lt=context.today,
    )
    return simple_result(
        spec,
        context=context,
        value=count(actions),
        population_count=count(open_actions(context)),
        url=corpus_url(context, olek="avatud", tegevus="hilinenud"),
        notes=("Ainult TEEN koos tähtajaga. Ootamine ja jälgimine ei ole hilinemine.",),
    )


def _review_due(context: ReportingContext, kind: str) -> QuerySet[NextAction]:
    return open_actions(context).filter(
        kind=kind,
        target_date__isnull=False,
        target_date__lte=context.today,
    )


def wait_review_due(context: ReportingContext) -> MetricResult:
    spec = definition(keys.WAIT_REVIEW_DUE)
    return simple_result(
        spec,
        context=context,
        value=count(_review_due(context, ActionKind.WAIT)),
        population_count=count(open_actions(context).filter(kind=ActionKind.WAIT)),
        url=corpus_url(context, olek="avatud", tegevus="ootan-ulevaatus"),
        notes=("Ülevaatuse aeg on käes. See ei ole tähtaja ületamine.",),
    )


def monitor_review_due(context: ReportingContext) -> MetricResult:
    spec = definition(keys.MONITOR_REVIEW_DUE)
    return simple_result(
        spec,
        context=context,
        value=count(_review_due(context, ActionKind.MONITOR)),
        population_count=count(open_actions(context).filter(kind=ActionKind.MONITOR)),
        url=corpus_url(context, olek="avatud", tegevus="jalgin-ulevaatus"),
        notes=("Ülevaatuse aeg on käes. See ei ole tähtaja ületamine.",),
    )


def next_action_by_kind(context: ReportingContext) -> MetricResult:
    spec = definition(keys.NEXT_ACTION_BY_KIND)
    labels = dict(ActionKind.choices)
    #: One `?tegevus=` value per action kind, so each bar opens exactly the
    #: Matters it counted. One open action per Matter is a database constraint,
    #: which is what makes the action count and the Matter count the same
    #: number (app/workflow/models.py).
    parameters = {
        ActionKind.DO.value: "teen",
        ActionKind.WAIT.value: "ootan",
        ActionKind.MONITOR.value: "jalgin",
    }
    rows = open_actions(context).values("kind").annotate(total=grouped_count()).order_by("kind")
    segments = tuple(
        Segment(
            label=labels.get(row["kind"], row["kind"]),
            value=row["total"],
            url=corpus_url(context, olek="avatud", tegevus=parameters.get(row["kind"], "")),
        )
        for row in rows
    )
    return simple_result(
        spec,
        context=context,
        value=count(open_actions(context)),
        segments=segments,
        url=reverse("matters:my_work"),
    )


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------


def visible_entries(context: ReportingContext) -> QuerySet[Entry]:
    queryset = Entry.objects.visible_to(context.viewer).filter(matter__in=visible_matters(context))
    if context.period.is_all:
        return queryset
    return queryset.filter(
        occurred_at__gte=context.period.start_datetime(),
        occurred_at__lt=context.period.end_datetime(),
    )


def entry_count(context: ReportingContext) -> MetricResult:
    spec = definition(keys.ENTRY_COUNT)
    ever = Entry.objects.visible_to(context.viewer).filter(matter__in=visible_matters(context))
    return simple_result(
        spec,
        context=context,
        value=count(visible_entries(context)),
        population_count=count(ever),
        url="",
        notes=(
            "OneNote'i lehed ei ole sissekanded ja neid siin ei loeta.",
            "Sissekannete eraldi loendit ei ole — need elavad teema ajajoonel.",
        ),
    )


def entry_count_by_kind(context: ReportingContext) -> MetricResult:
    spec = definition(keys.ENTRY_COUNT_BY_KIND)
    labels = dict(EntryKind.choices)
    rows = (
        visible_entries(context).values("kind").annotate(total=grouped_count()).order_by("-total")
    )
    segments = tuple(
        Segment(label=labels.get(row["kind"], row["kind"]), value=row["total"]) for row in rows
    )
    return simple_result(
        spec,
        context=context,
        value=count(visible_entries(context)),
        segments=segments,
        url="",
    )
