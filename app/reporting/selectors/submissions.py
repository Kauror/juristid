"""Submission metrics, and the one definition they all rest on.

**A Submission is a Submission.** It is never inferred from a Matter, a PDF, a
DOCX, an ASiC-E, a OneNote page, a filename containing *arvamus*, or the
register's `VÄLJA` column. Stage 2D deliberately did not fabricate historical
submissions out of an outbound date with no evidence behind it, and this module
preserves that decision rather than quietly reversing it in a chart
(master specification 18.2, Stage-2E brief 23, 25).

The consequence is a reporting rule that has to be stated on the page rather
than hidden: **structured submission data begins with this system.** A year
before that has no measurement, and a year with no measurement is not a zero. So
the trend never draws a bar for a year outside the measured window, and the
whole metric declines with ``INSUFFICIENT_DATA`` when no submission record
exists at all — which is what stops "0" from being read as "Koda sent nothing"
(brief 24).

Every population here comes from one function, ``sent_submissions``. The trend
bar, the recipient bar, the drill-through list and the CSV export all call it
with the same arguments, which is why the count above a list can be asserted to
equal the list (brief 66).
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlencode

from django.db.models import Count, Q, QuerySet
from django.urls import reverse

from app.matters.enums import RecordMode
from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext
from app.reporting.metric_catalogue import definition
from app.reporting.metric_types import MetricResult, Segment
from app.reporting.selectors.base import (
    count,
    grouped_count,
    population_for,
    register_url,
    simple_result,
    top_segments,
    visible_matters,
)
from app.submissions.enums import RecipientRole, SubmissionKind, SubmissionStatus
from app.submissions.models import Submission

_ERA_NOTE = (
    "Struktuurne arvamuse kirje tekib alles selles süsteemis. Varasemate "
    "aastate kohta ei ole mõõtmist — see ei tähenda, et arvamusi ei saadetud."
)


def all_sent(context: ReportingContext) -> QuerySet[Submission]:
    """Every sent Submission this viewer may read, before any period narrowing.

    Scoped twice on purpose: ``visible_to`` derives the submission's own
    effective visibility from its Matter, and ``matter__in`` applies the page's
    active Matter filters. Neither is redundant — the first is authorization,
    the second is the filter bar.
    """
    return Submission.objects.visible_to(context.viewer).filter(
        status=SubmissionStatus.SENT,
        matter__in=visible_matters(context),
    )


def sent_submissions(
    context: ReportingContext,
    *,
    year: int | None = None,
    recipient_id: uuid.UUID | None = None,
    kind: str = "",
) -> QuerySet[Submission]:
    """The one population behind every submission number on the site."""
    queryset = all_sent(context)

    if year is not None:
        queryset = queryset.filter(sent_at__year=year)
    elif not context.period.is_all:
        start, end = context.period.start_datetime(), context.period.end_datetime()
        queryset = queryset.filter(sent_at__gte=start, sent_at__lt=end)

    if recipient_id is not None:
        queryset = queryset.filter(
            recipient_rows__organisation_id=recipient_id,
            recipient_rows__role=RecipientRole.ADDRESSEE,
        )
    if kind:
        queryset = queryset.filter(kind=kind)

    return queryset


def measured_window(context: ReportingContext) -> tuple[int, int] | None:
    """The years structured submission data can actually speak for.

    ``None`` when nothing has ever been recorded. Derived from the data rather
    than from a configured cutover date, because a configured date that drifts
    from reality would produce exactly the false zeros this guards against.
    """
    first = all_sent(context).order_by("sent_at").values_list("sent_at", flat=True).first()
    if first is None:
        return None
    return first.year, context.today.year


def _submission_url(context: ReportingContext, **extra: str) -> str:
    params = {key: value for key, value in extra.items() if value}
    params.setdefault("periood", context.period.key)
    return f"{reverse('reporting:submissions')}?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def submissions_sent(context: ReportingContext) -> MetricResult:
    spec = definition(keys.SUBMISSIONS_SENT)
    ever = count(all_sent(context))
    value = count(sent_submissions(context))
    return simple_result(
        spec,
        context=context,
        value=value,
        population_count=ever,
        eligible_count=value,
        url=_submission_url(context),
        notes=(_ERA_NOTE,),
    )


def submissions_sent_by_period(context: ReportingContext) -> MetricResult:
    """A bar per year of the measured window, and none outside it.

    Inside the window a zero is a real measurement and is drawn. Outside it
    there is no measurement, so there is no bar — the difference between "none
    were sent" and "nobody recorded any" is the whole point of this metric.
    """
    spec = definition(keys.SUBMISSIONS_SENT_BY_PERIOD)
    ever = count(all_sent(context))
    window = measured_window(context)

    if window is None:
        return simple_result(
            spec,
            context=context,
            value=0,
            population_count=0,
            url=_submission_url(context),
            notes=(_ERA_NOTE, "Ühtki struktuurset arvamuse kirjet ei ole veel salvestatud."),
        )

    first_year, last_year = window
    if context.period.is_all:
        years = list(range(first_year, last_year + 1))
    else:
        years = [year for year in context.period.years if first_year <= year <= last_year]

    counted = {
        row["sent_at__year"]: row["total"]
        for row in all_sent(context).values("sent_at__year").annotate(total=grouped_count())
    }
    segments = tuple(
        Segment(
            label=str(year),
            value=counted.get(year, 0),
            url=_submission_url(context, aasta=str(year)),
        )
        for year in years
    )

    notes = [_ERA_NOTE, f"Mõõdetud aastad: {first_year}–{last_year}."]
    if not context.period.is_all and not years:
        notes.append("Valitud periood jääb tervikuna mõõdetud akna alt välja.")

    return simple_result(
        spec,
        context=context,
        value=sum(segment.value for segment in segments),
        population_count=ever,
        segments=segments,
        url=_submission_url(context),
        notes=tuple(notes),
    )


def submissions_by_recipient(context: ReportingContext) -> MetricResult:
    """Who Koda formally wrote to. Addressees only.

    "Teadmiseks" recipients are excluded because they answer a different
    question. Flattening the two would make "who did Koda actually write to"
    unanswerable, which is why the roles were separated in the first place.
    """
    spec = definition(keys.SUBMISSIONS_BY_RECIPIENT)
    population = sent_submissions(context)
    ever = count(all_sent(context))

    rows = (
        population.filter(recipient_rows__role=RecipientRole.ADDRESSEE)
        .values("recipient_rows__organisation_id", "recipient_rows__organisation__name")
        .annotate(total=grouped_count())
        .order_by("-total", "recipient_rows__organisation__name")
    )
    segments = top_segments(
        [
            Segment(
                label=row["recipient_rows__organisation__name"],
                value=row["total"],
                url=_submission_url(context, saaja=str(row["recipient_rows__organisation_id"])),
            )
            for row in rows
        ],
        remainder_url=_submission_url(context),
    )

    addressed = count(population.filter(recipient_rows__role=RecipientRole.ADDRESSEE))
    in_period = count(population)

    return simple_result(
        spec,
        context=context,
        value=addressed,
        population_count=ever,
        eligible_count=addressed,
        coverage_count=addressed,
        coverage_denominator=in_period,
        segments=segments,
        url=_submission_url(context),
        notes=(
            _ERA_NOTE,
            "Ainult adressaadid. Rollis „teadmiseks“ olevad saajad ei ole siin.",
        ),
    )


def submissions_by_kind(context: ReportingContext) -> MetricResult:
    spec = definition(keys.SUBMISSIONS_BY_KIND)
    population = sent_submissions(context)
    labels = dict(SubmissionKind.choices)
    rows = population.values("kind").annotate(total=grouped_count()).order_by("-total")
    segments = tuple(
        Segment(
            label=labels.get(row["kind"], row["kind"]),
            value=row["total"],
            url=_submission_url(context, arvamus=row["kind"]),
        )
        for row in rows
    )
    return simple_result(
        spec,
        context=context,
        value=count(population),
        population_count=count(all_sent(context)),
        segments=segments,
        url=_submission_url(context),
        notes=(_ERA_NOTE,),
    )


def matters_by_submission_count(context: ReportingContext) -> MetricResult:
    """0, 1, 2+. Kept as three buckets because the tail is what is interesting.

    A Matter with several submissions is the case the register's single sent
    date could never represent — an opinion in the consultation round, a
    supplementary letter, then a submission to the committee.
    """
    spec = definition(keys.MATTERS_BY_SUBMISSION_COUNT)
    population = population_for(context, spec)
    sent = Q(submissions__status=SubmissionStatus.SENT)
    annotated = population.annotate(sent_count=Count("submissions", filter=sent, distinct=True))

    buckets = (
        ("Arvamust ei ole saadetud", annotated.filter(sent_count=0)),
        ("Üks arvamus", annotated.filter(sent_count=1)),
        ("Kaks või rohkem", annotated.filter(sent_count__gte=2)),
    )
    segments = tuple(
        Segment(
            label=label,
            value=queryset.distinct().count(),
            url=register_url(context, liik=RecordMode.FULL.value),
        )
        for label, queryset in buckets
    )
    return simple_result(
        spec,
        context=context,
        value=count(population),
        segments=segments,
        url=register_url(context, liik=RecordMode.FULL.value),
        notes=(_ERA_NOTE,),
    )


def matters_with_multiple_submissions(context: ReportingContext) -> MetricResult:
    spec = definition(keys.MATTERS_WITH_MULTIPLE_SUBMISSIONS)
    population = population_for(context, spec)
    sent = Q(submissions__status=SubmissionStatus.SENT)
    several = (
        population.annotate(sent_count=Count("submissions", filter=sent, distinct=True))
        .filter(sent_count__gte=2)
        .distinct()
        .count()
    )
    return simple_result(
        spec,
        context=context,
        value=several,
        population_count=count(population),
        url=register_url(context, liik=RecordMode.FULL.value),
        notes=(_ERA_NOTE,),
    )


def list_rows(context: ReportingContext, **filters: Any) -> QuerySet[Submission]:
    """The drill-through list, from the same selector the numbers used."""
    return (
        sent_submissions(context, **filters)
        .select_related("matter", "matter__owner", "sent_by")
        .prefetch_related("recipient_rows__organisation")
        .distinct()
        .order_by("-sent_at")
    )
