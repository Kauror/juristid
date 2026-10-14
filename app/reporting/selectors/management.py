"""Management statistics: deadlines, timeliness, comparisons and composition.

Six questions a department head brings to a year, each answered from a field the
product already stores and each with its own catalogue entry
(docs/adr/0160, ``app/reporting/metric_catalogue.py``):

* how long Koda was given to prepare an opinion — ``Saabus`` to the first known
  ``Arvamuse tähtaeg`` (``RESPONSE_PREPARATION_TIME``);
* whether the answer went in time — the actual send date against the deadline
  that was in force **when it was sent** (``OPINIONS_ON_TIME``);
* this year so far against the same stretch of last year
  (``SUBMISSIONS_SENT_YOY_CHANGE``);
* how much of each year's work is European, by the reviewed `Õigusakt` group
  (``EU_RELATED_MATTERS_BY_YEAR``);
* how much the Chamber started itself (``KODA_INITIATIVES_BY_YEAR``);
* how sending is spread over the months of each year
  (``SUBMISSIONS_SENT_BY_MONTH_AND_YEAR``).

Three rules run through all of them, and each is a way these numbers would
otherwise lie.

**A Chamber initiative is not an incoming request.** A Teema on the «Koja
algatus» track was started by Koda; nobody asked it for an opinion, and an
internal target date on it is ordinary work, not an `Arvamuse tähtaeg`
(docs/adr/0151). It is excluded from both deadline populations, and the number
excluded is said beside them.

**A deadline is judged as it stood when the letter went.** `Arvamuse tähtaeg`
keeps its history (docs/adr/0135), so the deadline an answer named is checked
against that history: if the deadline changed — moved, replaced, or recorded at
all — after the opinion was sent, the deadline in force at sending cannot be
established, and the answer is shown as *not verifiable* rather than judged
against a later date. A deadline from before the history was kept has no record
of its revisions and is not verifiable either. Neither is counted as on time or
as late; both are counted, and both open their list.

**Every number opens the rows it counted.** The deadline and composition
metrics have no register filter that could express their groups, so their rows
come from :func:`metric_rows` — the same observation lists the numbers were
taken from — and the Statistika list (`/statistika/loend/`) pages them. Where
the register *can* express a group exactly (a track and a year), it is linked to
instead of being reimplemented.

Authorization precedes aggregation here as everywhere: every population starts
at ``visible_matters`` (and the opinion side at ``Submission.visible_to``), and
nothing is filtered after counting.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from django.db.models import (
    BooleanField,
    Case,
    CharField,
    Count,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Value,
    When,
)
from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from app.core.dates import format_estonian_date
from app.matters.enums import ResponseDeadlineOutcome
from app.matters.models import Matter, MatterResponseDeadline
from app.matters.selectors import DATA_CLASS_REAL, MISSING, UNKNOWN_YEAR
from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext
from app.reporting.metric_catalogue import OPINION_ARCHIVE_FIRST_YEAR, definition
from app.reporting.metric_types import (
    Comparison,
    Matrix,
    MatrixCell,
    MatrixRow,
    MetricResult,
    MetricStatus,
    Segment,
    distribution_from,
)
from app.reporting.selectors.base import (
    count,
    eligible_matters,
    grouped_count,
    known_year_q,
    population_for,
    register_url,
    simple_result,
    visible_matters,
)
from app.reporting.selectors.matters import UNKNOWN_YEAR_LABEL
from app.reporting.selectors.portfolio import MONTH_LABELS, same_day_last_year
from app.reporting.selectors.submissions import (
    all_sent,
    measured_window,
    sent_submissions,
)
from app.submissions.enums import SentAtPrecision, SubmissionStatus
from app.submissions.models import Submission
from app.taxonomy.legal_instruments import DOMESTIC_LEGAL_INSTRUMENT_KEYS, EU_LEGAL_INSTRUMENT_KEYS
from app.workflow.enums import Track

#: The URL parameters of the Statistika list. ASCII, like the rest.
PARAM_METRIC = "naitaja"
PARAM_GROUP = "ruhm"
PARAM_YEAR = "aasta"

#: The note every deadline metric carries about the Chamber's own work. Since
#: docs/adr/0151 (amendment of 2026-10-13) an initiative may hold an
#: `Arvamuse tähtaeg` of its own; it is still not a deadline anybody gave Koda.
INITIATIVE_NOTE = (
    "{count} Koja algatust (menetlusliik «Koja algatus») ei ole arvestatud: "
    "neilt ei küsitud arvamust, ja tähtaeg, mille Koda endale paneb, ei ole "
    "väljast antud arvamuse tähtaeg."
)

#: The day the `Arvamuse tähtaeg` history and the opinion-to-deadline link
#: began (docs/adr/0135). Printed on the deadline cards as their coverage.
DEADLINE_HISTORY_SINCE = date(2026, 10, 4)


def _share_text(numerator: int, denominator: int) -> str:
    """«12,5%», or a dash when there is nothing to take a share of."""
    if not denominator:
        return "—"
    return f"{100.0 * numerator / denominator:.1f}%".replace(".", ",")


def metric_list_url(
    context: ReportingContext, key: str, group: str, *, year: str | int | None = None
) -> str:
    """A link into the Statistika list for one group of one metric.

    It carries every active filter, because the rows behind it are computed
    from the same context the number was: the same period, the same owner, the
    same policy area.
    """
    params = {**context.query_params(), PARAM_METRIC: key, PARAM_GROUP: group}
    if year is not None:
        params[PARAM_YEAR] = str(year)
    return f"{reverse('reporting:metric_rows')}?{urlencode(params)}"


def _submission_list_url(context: ReportingContext, **extra: str) -> str:
    params = {**context.query_params(), **{k: v for k, v in extra.items() if v}}
    return f"{reverse('reporting:submissions')}?{urlencode(params)}"


def _real_matters(queryset: QuerySet[Matter]) -> QuerySet[Matter]:
    """The same population through a primary-key subquery.

    A narrowing by tag or policy area joins and can repeat a row; resolving the
    population by key first means nothing below can count a Matter twice
    (Stage-2E: "a join fan-out plus distinct()").
    """
    return Matter.objects.filter(pk__in=queryset.values("pk"))


# ---------------------------------------------------------------------------
# 1. Time available to prepare an opinion
# ---------------------------------------------------------------------------

#: Day buckets, inclusive at both ends. Calendar days, because that is how a
#: deadline is given; working days would need a holiday calendar nobody has
#: reviewed.
DAY_BUCKETS: tuple[tuple[str, str, int, int | None], ...] = (
    ("0-7", "0–7 päeva", 0, 7),
    ("8-14", "8–14 päeva", 8, 14),
    ("15-21", "15–21 päeva", 15, 21),
    ("22-30", "22–30 päeva", 22, 30),
    ("31-60", "31–60 päeva", 31, 60),
    ("61+", "Üle 60 päeva", 61, None),
)
PREP_ALL = "koik"
PREP_NO_DEADLINE = "puudub"
PREP_BEFORE_RECEIPT = "enne-saabumist"
PREP_GROUP_LABELS: dict[str, str] = {
    PREP_ALL: "Mõõdetud teemad",
    PREP_NO_DEADLINE: "Arvamuse tähtaeg puudub",
    PREP_BEFORE_RECEIPT: "Tähtaeg on enne saabumist",
    **{key: label for key, label, _low, _high in DAY_BUCKETS},
}


@dataclass(frozen=True)
class PreparationRow:
    """One incoming Matter and the time it was given."""

    matter_id: Any
    title: str
    received: date
    deadline: date | None
    #: Whether the deadline used comes from before the history was kept.
    legacy: bool
    group: str

    @property
    def days(self) -> int | None:
        return None if self.deadline is None else (self.deadline - self.received).days

    @property
    def is_measured(self) -> bool:
        return self.group not in (PREP_NO_DEADLINE, PREP_BEFORE_RECEIPT)


def _bucket(days: int) -> str:
    for key, _label, low, high in DAY_BUCKETS:
        if days >= low and (high is None or days <= high):
            return key
    raise ValueError(days)  # pragma: no cover - the last bucket is open-ended


def _preparation_population(context: ReportingContext) -> QuerySet[Matter]:
    """Incoming Matters whose arrival falls in the period, before the deadline test."""
    spec = definition(keys.RESPONSE_PREPARATION_TIME)
    return population_for(context, spec).filter(received_date__isnull=False)


def preparation_rows(context: ReportingContext) -> list[PreparationRow]:
    """Every incoming Matter in the population, classified once per page."""
    return context.shared("management.preparation", lambda: _preparation_rows(context))


def _initiatives_with_deadline_but_no_arrival(context: ReportingContext) -> QuerySet[Matter]:
    """Chamber initiatives with no `Saabus` that hold a deadline in the period.

    Since docs/adr/0151 (amendment of 2026-10-13) an initiative may carry its own
    `Arvamuse tähtaeg` and never a `Saabus`. It has no arrival to measure from
    and is not in the population, but it is exactly what a reader might expect
    to see counted, so it is named in the exclusion note. With no arrival date
    it is placed in the period by any deadline it held — the current one or one
    in its history.
    """
    spec = definition(keys.RESPONSE_PREPARATION_TIME)
    history = MatterResponseDeadline.objects.all()
    current = Q(response_deadline__isnull=False)
    if not context.period.is_all:
        start, end = context.period.start_date, context.period.end_date
        history = history.filter(deadline__gte=start, deadline__lte=end)
        current = Q(response_deadline__gte=start, response_deadline__lte=end)
    return eligible_matters(context, spec).filter(
        Q(track=Track.KODA_INITIATIVE, received_date__isnull=True)
        & (current | Q(pk__in=history.values("matter_id")))
    )


def _preparation_rows(context: ReportingContext) -> list[PreparationRow]:
    matters = list(
        _real_matters(_preparation_population(context))
        .exclude(track=Track.KODA_INITIATIVE)
        .order_by("received_date", "pk")
        .values("pk", "title", "received_date", "response_deadline", "response_requested_at")
    )
    # The first deadline each Matter was ever given: the oldest history row,
    # or — with no history — the current field. A later request, a move or a
    # replacement never shortens or lengthens the time Koda was first given.
    first: dict[Any, tuple[date, bool]] = {}
    history = (
        MatterResponseDeadline.objects.filter(matter_id__in=[row["pk"] for row in matters])
        .order_by("matter_id", "ended_at", "created_at")
        .values("matter_id", "deadline", "requested_at")
    )
    for row in history:
        first.setdefault(row["matter_id"], (row["deadline"], row["requested_at"] is None))

    rows: list[PreparationRow] = []
    for matter in matters:
        received = matter["received_date"]
        if received is None:  # pragma: no cover - filtered above
            continue
        deadline: date | None
        if matter["pk"] in first:
            deadline, legacy = first[matter["pk"]]
        else:
            deadline = matter["response_deadline"]
            legacy = deadline is not None and matter["response_requested_at"] is None
        if deadline is None:
            group = PREP_NO_DEADLINE
        elif deadline < received:
            group = PREP_BEFORE_RECEIPT
        else:
            group = _bucket((deadline - received).days)
        rows.append(
            PreparationRow(
                matter_id=matter["pk"],
                title=matter["title"],
                received=received,
                deadline=deadline,
                legacy=legacy,
                group=group,
            )
        )
    return rows


def response_preparation_time(context: ReportingContext) -> MetricResult:
    spec = definition(keys.RESPONSE_PREPARATION_TIME)
    rows = preparation_rows(context)
    measured = [row for row in rows if row.is_measured]
    with_deadline = [row for row in rows if row.deadline is not None]
    arrived_initiatives = count(
        _preparation_population(context).filter(track=Track.KODA_INITIATIVE)
    )
    unarrived_initiatives = count(_initiatives_with_deadline_but_no_arrival(context))
    initiatives = arrived_initiatives + unarrived_initiatives

    def link(group: str) -> str:
        return metric_list_url(context, keys.RESPONSE_PREPARATION_TIME, group)

    by_group: dict[str, int] = defaultdict(int)
    for row in rows:
        by_group[row.group] += 1
    segments = [
        Segment(label=label, value=by_group.get(key, 0), url=link(key) if by_group.get(key) else "")
        for key, label, _low, _high in DAY_BUCKETS
    ]
    for key in (PREP_BEFORE_RECEIPT, PREP_NO_DEADLINE):
        if by_group.get(key):
            segments.append(
                Segment(
                    label=PREP_GROUP_LABELS[key],
                    value=by_group[key],
                    url=link(key),
                    note="Ei ole jaotuses",
                    is_unknown=True,
                )
            )

    distribution = distribution_from([row.days or 0 for row in measured])
    notes: list[str] = [
        f"Katvus: {len(with_deadline)} saabunud teemal {len(rows)}-st on arvamuse "
        f"tähtaeg; tähtaegade ajalugu peetakse alates "
        f"{format_estonian_date(DEADLINE_HISTORY_SINCE)}."
    ]
    if measured:
        notes.append(
            f"Mediaan {distribution.median:.0f} päeva".replace(".", ",")
            + f" · p75 {distribution.p75:.0f} · p90 {distribution.p90:.0f} · "
            f"lühim {min(row.days or 0 for row in measured)} · pikim {distribution.maximum}."
        )
    legacy = sum(1 for row in measured if row.legacy)
    if legacy:
        notes.append(
            f"{legacy} mõõdetud tähtaega pärineb ajast enne tähtaegade ajaloo "
            "pidamist; neist ei saa öelda, kas see oli esimene antud tähtaeg."
        )
    if initiatives:
        note = INITIATIVE_NOTE.format(count=initiatives)
        if unarrived_initiatives:
            note += (
                f" Neist {unarrived_initiatives} on ilma saabumiseta, kuid oma tähtajaga perioodis."
            )
        notes.append(note)

    status = None if measured else MetricStatus.INSUFFICIENT_DATA
    return simple_result(
        spec,
        context=context,
        value=len(measured),
        population_count=len(rows),
        eligible_count=len(measured),
        coverage_count=len(with_deadline),
        coverage_denominator=len(rows),
        url=link(PREP_ALL) if measured else "",
        segments=tuple(segments),
        distribution=distribution,
        notes=tuple(notes),
        status=status,
    )


# ---------------------------------------------------------------------------
# 2. Opinions submitted on time
# ---------------------------------------------------------------------------

ON_TIME = "ajal"
LATE = "hiljem"
VERIFIABLE = "kontrollitav"
CHANGED_AFTER_SENDING = "muutus"
BEFORE_HISTORY = "ajaloota"
WITHDRAWN = "tagasi"
ANSWER_GROUP_LABELS: dict[str, str] = {
    VERIFIABLE: "Kontrollitavad vastused",
    ON_TIME: "Tähtajaks",
    LATE: "Pärast tähtaega",
    # The reason comes first, so three bars that cannot be verified can be told
    # apart however narrowly they are cut; «ei saa kontrollida» is their note
    # and the list heading (visual QA 2026-10-13, ISSUE-4).
    CHANGED_AFTER_SENDING: "Tähtaeg muutus või lisati pärast saatmist",
    BEFORE_HISTORY: "Tähtaeg on enne tähtaegade ajalugu",
    WITHDRAWN: "Vastus on tagasi võetud",
}
UNVERIFIABLE_NOTE = "Ei saa kontrollida · ei ole protsendis"
UNVERIFIABLE_GROUPS: tuple[str, ...] = (CHANGED_AFTER_SENDING, BEFORE_HISTORY, WITHDRAWN)


@dataclass(frozen=True)
class AnswerRow:
    """One answered request: the deadline it named and the opinion that answered it."""

    matter_id: Any
    matter_title: str
    submission_title: str
    deadline: date
    sent_on: date
    group: str

    @property
    def days_late(self) -> int:
        return (self.sent_on - self.deadline).days

    @property
    def is_verifiable(self) -> bool:
        return self.group in (ON_TIME, LATE)


def _later_than_sending(
    moment: datetime, sent_at: datetime, precision: str, *, same_day: bool
) -> bool:
    """Whether ``moment`` falls after the letter went.

    With a real timestamp the two instants are compared. With a date-only send
    — the anchor time in the column is nobody's statement — the comparison is
    by Tallinn calendar day, and ``same_day`` says how a change on the sending
    day itself is read: as *after* for a deadline revision (the order within
    that day is unknown, so it cannot be ruled out), and as *not after* for the
    moment a request was recorded.
    """
    if precision != SentAtPrecision.DATE:
        return moment > sent_at
    moment_day, sent_day = timezone.localdate(moment), timezone.localdate(sent_at)
    return moment_day >= sent_day if same_day else moment_day > sent_day


def _answered_population(context: ReportingContext) -> QuerySet[MatterResponseDeadline]:
    """Answered requests on visible Matters, naming an opinion this reader may see.

    The deadline history has no visibility of its own and is read only through
    its Matter; the opinion keeps its own, so a restricted opinion on an
    ordinary Matter takes its answer out of the population for a reader who may
    not see it (docs/adr/0137).
    """
    sent = Submission.objects.visible_to(context.viewer).filter(
        status__in=(SubmissionStatus.SENT, SubmissionStatus.WITHDRAWN, SubmissionStatus.SUPERSEDED),
        sent_at__isnull=False,
    )
    queryset = MatterResponseDeadline.objects.filter(
        outcome=ResponseDeadlineOutcome.ANSWERED,
        submission__in=sent,
        matter__in=visible_matters(context),
    )
    if not context.period.is_all:
        queryset = queryset.filter(
            submission__sent_at__gte=context.period.start_datetime(),
            submission__sent_at__lt=context.period.end_datetime(),
        )
    return queryset


def answer_rows(context: ReportingContext) -> list[AnswerRow]:
    return context.shared("management.answers", lambda: _answer_rows(context))


def _answer_rows(context: ReportingContext) -> list[AnswerRow]:
    answered = list(
        _answered_population(context)
        .exclude(matter__track=Track.KODA_INITIATIVE)
        .select_related("matter", "submission")
        .order_by("submission__sent_at", "pk")
    )
    # Every history row of the same Matters, to see whether the current
    # deadline changed between the send and the answer being recorded.
    others: dict[Any, list[MatterResponseDeadline]] = defaultdict(list)
    for row in MatterResponseDeadline.objects.filter(
        matter_id__in={row.matter_id for row in answered}
    ).only("pk", "matter_id", "ended_at"):
        others[row.matter_id].append(row)

    rows: list[AnswerRow] = []
    for row in answered:
        submission = row.submission
        if submission is None or submission.sent_at is None:  # pragma: no cover - filtered above
            continue
        sent_at, precision = submission.sent_at, submission.sent_at_precision
        sent_on = timezone.localdate(sent_at)
        if submission.status == SubmissionStatus.WITHDRAWN:
            group = WITHDRAWN
        elif row.requested_at is None:
            group = BEFORE_HISTORY
        elif _later_than_sending(row.requested_at, sent_at, precision, same_day=False) or any(
            other.pk != row.pk
            and other.ended_at < row.ended_at
            and _later_than_sending(other.ended_at, sent_at, precision, same_day=True)
            for other in others[row.matter_id]
        ):
            group = CHANGED_AFTER_SENDING
        else:
            group = ON_TIME if sent_on <= row.deadline else LATE
        rows.append(
            AnswerRow(
                matter_id=row.matter_id,
                matter_title=row.matter.title,
                submission_title=submission.title,
                deadline=row.deadline,
                sent_on=sent_on,
                group=group,
            )
        )
    return rows


def opinions_on_time(context: ReportingContext) -> MetricResult:
    spec = definition(keys.OPINIONS_ON_TIME)
    rows = answer_rows(context)
    by_group: dict[str, int] = defaultdict(int)
    for row in rows:
        by_group[row.group] += 1
    verifiable = by_group[ON_TIME] + by_group[LATE]

    def link(group: str) -> str:
        return metric_list_url(context, keys.OPINIONS_ON_TIME, group)

    segments = [
        Segment(
            label=ANSWER_GROUP_LABELS[key],
            value=by_group[key],
            url=link(key) if by_group[key] else "",
        )
        for key in (ON_TIME, LATE)
    ]
    segments.extend(
        Segment(
            label=ANSWER_GROUP_LABELS[key],
            value=by_group[key],
            url=link(key),
            note=UNVERIFIABLE_NOTE,
            is_unknown=True,
        )
        for key in UNVERIFIABLE_GROUPS
        if by_group[key]
    )

    population = _answered_population(context)
    initiatives = count(population.filter(matter__track=Track.KODA_INITIATIVE))
    notes: list[str] = [
        f"Katvus: arvesse lähevad ainult seotud tähtajad — alates "
        f"{format_estonian_date(DEADLINE_HISTORY_SINCE)}, kui arvamuse salvestamisel "
        "nimetatakse tähtaeg, millele see vastab. Varem saadetud ja arhiivist "
        "taastatud arvamustel seost ei ole ja neid kuupäeva järgi ei seota."
    ]
    if verifiable:
        notes.append(
            f"{by_group[ON_TIME]} {verifiable}-st kontrollitavast vastusest saadeti "
            "hiljemalt saatmise ajal kehtinud tähtajal."
        )
    unverifiable = len(rows) - verifiable
    if unverifiable:
        notes.append(
            f"{unverifiable} vastust ei saa kontrollida ega ole protsendis — "
            "praegust tähtaega nende hindamiseks ei kasutata, ka siis, kui tähtaeg "
            "lisati alles pärast saatmist."
        )
    note_only = count(
        MatterResponseDeadline.objects.filter(
            outcome=ResponseDeadlineOutcome.ANSWERED,
            submission__isnull=True,
            matter__in=visible_matters(context).exclude(track=Track.KODA_INITIATIVE),
        ).filter(_ended_in_period(context))
    )
    if note_only:
        notes.append(
            f"{note_only} tähtajale vastati ilma Juristides saadetud arvamuseta "
            "(ainult selgitusega); neil ei ole saatmise kuupäeva."
        )
    unnamed = count(
        sent_submissions(context)
        .exclude(matter__track=Track.KODA_INITIATIVE)
        .filter(answered_response_deadlines__isnull=True)
    )
    if unnamed:
        notes.append(
            f"Perioodil saadetud {unnamed} arvamust ei ole ühegi tähtaja vastuseks "
            "nimetatud ja ei kuulu siia."
        )
    if initiatives:
        notes.append(INITIATIVE_NOTE.format(count=initiatives))

    percent = round(100 * by_group[ON_TIME] / verifiable) if verifiable else 0
    return simple_result(
        spec,
        context=context,
        value=percent,
        population_count=len(rows),
        eligible_count=verifiable,
        coverage_count=verifiable,
        coverage_denominator=len(rows),
        url=link(VERIFIABLE) if verifiable else "",
        segments=tuple(segments),
        notes=tuple(notes),
        status=None if verifiable else MetricStatus.INSUFFICIENT_DATA,
    )


def _ended_in_period(context: ReportingContext) -> Q:
    """An answer given in words has no send date; it is dated when it was recorded."""
    if context.period.is_all:
        return Q()
    return Q(
        ended_at__gte=context.period.start_datetime(),
        ended_at__lt=context.period.end_datetime(),
    )


# ---------------------------------------------------------------------------
# 3. This year so far against the same stretch of last year
# ---------------------------------------------------------------------------


def _first_send_day(context: ReportingContext) -> date | None:
    """The Tallinn day of the first sent opinion this context can count.

    The same population and the same clock as ``measured_window``, read to the
    day: the year alone cannot tell whether last year's window up to the cutoff
    was measured at all (QA-STATS-05).
    """
    first = all_sent(context).order_by("sent_at").values_list("sent_at", flat=True).first()
    return None if first is None else timezone.localdate(first)


def submissions_sent_yoy_change(context: ReportingContext) -> MetricResult:
    """1 January to today, against 1 January to the same date last year.

    Both sides are read in Tallinn and both end on the same calendar day, so
    nine and a half months are never measured against twelve. A year before the
    first recorded Submission is not a zero: it was never measured, and the card
    declines rather than reporting a rise from nothing.
    """
    spec = definition(keys.SUBMISSIONS_SENT_YOY_CHANGE)
    cutoff = context.today
    previous_cutoff = same_day_last_year(cutoff)

    current = count(sent_submissions(context, year=cutoff.year, through=cutoff))
    previous = count(sent_submissions(context, year=cutoff.year - 1, through=previous_cutoff))

    def label(day: date) -> str:
        return f"1.1.{day.year} – {format_estonian_date(day)}"

    comparison = Comparison(
        current_value=current,
        previous_value=previous,
        current_period_label=label(cutoff),
        previous_period_label=label(previous_cutoff),
        coverage_cutoff=cutoff,
        cutoff_note="Mõlemad perioodid algavad 1. jaanuaril ja lõpevad sama kuupäevaga.",
        current_url=_submission_list_url(
            context, aasta=str(cutoff.year), kuni=f"{cutoff.month:02d}-{cutoff.day:02d}"
        ),
        previous_url=_submission_list_url(
            context,
            aasta=str(previous_cutoff.year),
            kuni=f"{previous_cutoff.month:02d}-{previous_cutoff.day:02d}",
        ),
    )

    ever = count(all_sent(context))
    window = measured_window(context)
    notes = [
        "Mahu muutus, mitte tulemuslikkus. Rohkem saadetud arvamusi ei ole "
        "iseenesest parem ega halvem.",
        "Arhiivist taastatud arvamused on kanoonilised kirjed ja kuuluvad siia; "
        "arhiivi failid ise ei kuulu.",
    ]
    status: MetricStatus | None = None
    if window is not None:
        notes.insert(0, f"Katvus: struktuursed arvamuse kirjed alates aastast {window[0]}.")
    first_day = _first_send_day(context) if window is not None else None
    if window is None or first_day is None:
        status = MetricStatus.INSUFFICIENT_DATA
    elif first_day > previous_cutoff:
        # Compared as a *date*, not a year (QA-STATS-05, ADR 0160 §5): a first
        # send on 9.9.2025 leaves 1.1–1.8.2025 unmeasured, and comparing years
        # (2025 > 2025 is false) reported «3 vs 0» against a window no record
        # could speak for.
        status = MetricStatus.INSUFFICIENT_DATA
        notes.append(
            f"Eelmise aasta võrreldava perioodi kohta mõõtmist ei ole: esimene "
            f"salvestatud arvamus on {format_estonian_date(first_day)}."
        )

    return simple_result(
        spec,
        context=context,
        value=current,
        population_count=ever,
        eligible_count=current,
        url=comparison.current_url,
        comparison=comparison,
        notes=tuple(notes),
        status=status,
    )


# ---------------------------------------------------------------------------
# 4 and 5. Composition by reporting year
# ---------------------------------------------------------------------------

#: *ELiga seotud* has two bases, kept apart so a reader can see which one a
#: Matter was counted on (owner decision of 2026-10-13). The reviewed `Õigusakt`
#: group comes first; only a Matter whose `Õigusakt` is unclassified may be
#: counted European on its `Menetlusliik`. Each Matter lands in exactly one
#: column — the order of the ``Case`` in :func:`_eu_group` is the precedence.
EU_RELATED = "el"
EU_BY_TRACK = "el-menetlus"
DOMESTIC = "siseriiklik"
UNCLASSIFIED = "maaramata"
EU_COLUMNS: tuple[str, ...] = (EU_RELATED, EU_BY_TRACK, DOMESTIC, UNCLASSIFIED)
EU_GROUP_LABELS: dict[str, str] = {
    EU_RELATED: "ELiga seotud õigusakti järgi",
    EU_BY_TRACK: "ELiga seotud ainult menetlusliigi järgi",
    DOMESTIC: "Siseriiklik",
    UNCLASSIFIED: "Määramata",
}
#: The two `Menetlusliik` values that are European by definition.
EU_TRACKS: tuple[str, ...] = (Track.EU_INITIATIVE.value, Track.NATIONAL_TRANSPOSITION.value)
#: The historical version-1.0 «EL teatis» — «Euroopa Komisjoni teatis või muu
#: ELi institutsiooni teatis». **European for statistics only** (owner's
#: decision 2026-10-13, docs/adr/0160 §6): it stays retired for capture
#: (docs/adr/0090) and outside ``app.taxonomy.legal_instruments``' two groups,
#: which every other surface reads. Counted under «õigusakti järgi» and kept
#: identifiable as its own subset (:data:`EU_NOTICE_SUBSET`).
EU_NOTICE_KEY = "el-teatis"
#: The instrument keys this metric reads as European: the reviewed group plus
#: the historical notice. A reporting-side set, never written back anywhere.
REPORTING_EU_INSTRUMENT_KEYS: frozenset[str] = EU_LEGAL_INSTRUMENT_KEYS | {EU_NOTICE_KEY}
#: The list group for «sh … «EL teatis»»: Matters in «õigusakti järgi» that
#: carry the historical notice. A subset of a column, not a column.
EU_NOTICE_SUBSET = "el-teatis"
EU_NOTICE_SUBSET_LABEL = "ELiga seotud õigusakti järgi, sh ajalooline «EL teatis»"

INITIATIVE = "algatus"
OTHER_TRACK = "muu"
NO_TRACK = "puudub"
TRACK_COLUMNS: tuple[str, ...] = (INITIATIVE, OTHER_TRACK, NO_TRACK)
TRACK_GROUP_LABELS: dict[str, str] = {
    INITIATIVE: "Koja algatus",
    OTHER_TRACK: "Muu menetlusliik",
    NO_TRACK: "Menetlusliik määramata",
}


def _year_key() -> Case:
    """The register reporting year, or NULL when the Matter has none (one bucket)."""
    return Case(
        When(known_year_q(), then=F("reporting_year")),
        default=Value(None),
        output_field=IntegerField(),
    )


def _year_q(year: str) -> Q:
    """The Matters of one URL year value, by the same rule as :func:`_year_key`."""
    if year == UNKNOWN_YEAR:
        return ~known_year_q()
    return known_year_q() & Q(reporting_year=int(year))


def _eu_group(queryset: QuerySet[Matter]) -> QuerySet[Matter]:
    """Each Matter's one EU group, and whether it carries the historical «EL teatis».

    ``Exists`` rather than a join, so a Matter with three instruments is still
    one row. The ``Case`` is evaluated in order, which *is* the precedence:

    1. *õigusakti järgi* — any instrument in the European group, or the
       historical «EL teatis» (for statistics only, :data:`EU_NOTICE_KEY`);
    2. *siseriiklik* — instruments present and every one domestic;
    3. *ainult menetlusliigi järgi* — the `Õigusakt` group is unknown (none, or
       one the reviewed list puts in neither group) and the stored
       `Menetlusliik` is «ELi algatus» or «ELi õiguse ülevõtmine»;
    4. *määramata* — everything else.

    A domestic instrument on a European track (a `Seadus` transposing a
    directive) stays domestic: the `Õigusakt` answer is the one asked for and
    takes precedence. Nothing is read from titles, notes or links.
    """
    through = Matter.legal_instruments.through
    mine = through.objects.filter(matter_id=OuterRef("pk"))
    grouped = REPORTING_EU_INSTRUMENT_KEYS | DOMESTIC_LEGAL_INSTRUMENT_KEYS
    return queryset.annotate(
        has_eu=Exists(mine.filter(legalinstrumenttype__key__in=REPORTING_EU_INSTRUMENT_KEYS)),
        has_domestic=Exists(
            mine.filter(legalinstrumenttype__key__in=DOMESTIC_LEGAL_INSTRUMENT_KEYS)
        ),
        has_ungrouped=Exists(mine.exclude(legalinstrumenttype__key__in=grouped)),
        has_eu_notice=Exists(mine.filter(legalinstrumenttype__key=EU_NOTICE_KEY)),
    ).annotate(
        eu_group=Case(
            When(has_eu=True, then=Value(EU_RELATED)),
            When(has_domestic=True, has_ungrouped=False, then=Value(DOMESTIC)),
            When(track__in=EU_TRACKS, then=Value(EU_BY_TRACK)),
            default=Value(UNCLASSIFIED),
            output_field=CharField(),
        ),
        on_eu_track=Case(
            When(track__in=EU_TRACKS, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )


def _track_group(queryset: QuerySet[Matter]) -> QuerySet[Matter]:
    """Each Matter's one track group: the Chamber's own, another, or none stored."""
    return queryset.annotate(
        track_group=Case(
            When(track=Track.KODA_INITIATIVE, then=Value(INITIATIVE)),
            When(track="", then=Value(NO_TRACK)),
            default=Value(OTHER_TRACK),
            output_field=CharField(),
        )
    )


def _year_population(context: ReportingContext, key: str) -> QuerySet[Matter]:
    """Visible Matters of the metric, by *register* reporting year, in the period."""
    return _real_matters(population_for(context, definition(key)))


def _grouped_by_year(
    population: QuerySet[Matter], group_field: str, *extra: str
) -> list[dict[str, Any]]:
    """One aggregate query: Matters per (year, group[, extra flags]).

    The query count does not grow with the rows or the years — the whole matrix
    is one statement (docs/adr/0160, round 2 performance check).
    """
    return list(
        population.annotate(year_key=_year_key())
        .order_by()
        .values("year_key", group_field, *extra)
        .annotate(total=Count("pk"))
    )


def _matrix_rows(
    context: ReportingContext,
    key: str,
    columns: tuple[str, ...],
    counts: dict[tuple[int | None, str], int],
    share: Callable[[dict[str, int]], str],
) -> tuple[list[MatrixRow], dict[str, int]]:
    """Year rows, oldest first, then *Teadmata aasta* last (only over all years).

    Every cell opens the Statistika list of exactly its Matters, which carries
    the same reporting context — the same data class, visibility and filters.
    """
    years = sorted({year for year, _group in counts if year is not None})
    keys_: list[tuple[str, str, int | None, bool]] = [
        (str(year), str(year), year, False) for year in years
    ]
    if context.period.is_all and any(year is None for year, _group in counts):
        keys_.append((UNKNOWN_YEAR_LABEL, UNKNOWN_YEAR, None, True))

    totals = dict.fromkeys(columns, 0)
    rows: list[MatrixRow] = []
    for label, year_value, year, unknown in keys_:
        values = {group: counts.get((year, group), 0) for group in columns}
        for group in columns:
            totals[group] += values[group]
        rows.append(
            MatrixRow(
                label=label,
                cells=tuple(
                    MatrixCell(
                        value=values[group],
                        url=metric_list_url(context, key, group, year=year_value)
                        if values[group]
                        else "",
                    )
                    for group in columns
                ),
                total=sum(values.values()),
                is_unknown=unknown,
                share_text=share(values),
            )
        )
    return rows, totals


def _eu_share(values: dict[str, int]) -> str:
    european = values[EU_RELATED] + values[EU_BY_TRACK]
    return _share_text(european, european + values[DOMESTIC])


def eu_related_matters_by_year(context: ReportingContext) -> MetricResult:
    spec = definition(keys.EU_RELATED_MATTERS_BY_YEAR)
    population = _eu_group(_year_population(context, keys.EU_RELATED_MATTERS_BY_YEAR))
    grouped = _grouped_by_year(population, "eu_group", "has_eu_notice", "on_eu_track")

    counts: dict[tuple[int | None, str], int] = defaultdict(int)
    notice = 0
    domestic_on_eu_track = 0
    for row in grouped:
        if row["year_key"] is None and not context.period.is_all:
            # A period filter has already said unknown-year Matters are outside it.
            continue
        counts[(row["year_key"], row["eu_group"])] += row["total"]
        if row["has_eu_notice"] and row["eu_group"] == EU_RELATED:
            notice += row["total"]
        if row["on_eu_track"] and row["eu_group"] == DOMESTIC:
            domestic_on_eu_track += row["total"]

    matrix_rows, totals = _matrix_rows(
        context, keys.EU_RELATED_MATTERS_BY_YEAR, EU_COLUMNS, counts, _eu_share
    )
    all_rows = sum(totals.values())
    european = totals[EU_RELATED] + totals[EU_BY_TRACK]
    classified = european + totals[DOMESTIC]
    matrix = Matrix(
        row_header="Aruandlusaasta",
        columns=tuple(EU_GROUP_LABELS[group] for group in EU_COLUMNS),
        rows=tuple(matrix_rows),
        column_totals=tuple(totals[group] for group in EU_COLUMNS),
        grand_total=all_rows,
        column_header="ELiga seotuse aluse",
        column_noun="alust",
        share_header="ELiga seotud osakaal",
        subsets=(
            Segment(
                label=f"sh {notice} ajaloolise õigusaktiga «EL teatis»",
                value=notice,
                url=metric_list_url(context, keys.EU_RELATED_MATTERS_BY_YEAR, EU_NOTICE_SUBSET),
            ),
        )
        if notice
        else (),
    )
    notes = [
        f"Katvus: {totals[UNCLASSIFIED]} teemal {all_rows}-st on määramata, kas see "
        "on ELiga seotud — õigusakti rühm ega ELi menetlusliik ei ütle seda.",
        f"Kokku ELiga seotud {european} teemat: {totals[EU_RELATED]} õigusakti järgi "
        f"ja {totals[EU_BY_TRACK]} ainult menetlusliigi («ELi algatus», «ELi õiguse "
        "ülevõtmine») järgi. Osakaal teemadest, mille seos on teada: "
        f"{_share_text(european, classified)}.",
        "Iga teema on ühes veerus. Õigusakt on eelistatud: menetlusliiki loetakse "
        "ainult siis, kui õigusakti rühm on määramata. Määramata teemad ei ole "
        "nimetajas ega ole siseriiklikud.",
    ]
    if domestic_on_eu_track:
        notes.append(
            f"{domestic_on_eu_track} siseriikliku õigusaktiga teemal on ELi "
            "menetlusliik; õigusakt on eelistatud, seega on need siseriiklikud."
        )
    if notice:
        notes.append(
            "Ajalooline õigusakt «EL teatis» (Euroopa Komisjoni või muu ELi "
            "institutsiooni teatis) on statistikas «õigusakti järgi» ELiga seotud; "
            "uute teemade jaoks see liik valitav ei ole."
        )
    notes.append(
        "ELiga seotud osakaal ei mõõda kõiki siseriiklikke õigusloome teemasid, "
        "mida EL-i õigus mõjutab: siseriiklik õigusakt jääb siseriiklikuks."
    )
    return simple_result(
        spec,
        context=context,
        value=european,
        population_count=all_rows,
        eligible_count=classified,
        coverage_count=classified,
        coverage_denominator=all_rows,
        matrix=matrix,
        notes=tuple(notes),
        status=None if all_rows else MetricStatus.INSUFFICIENT_DATA,
    )


def koda_initiatives_by_year(context: ReportingContext) -> MetricResult:
    spec = definition(keys.KODA_INITIATIVES_BY_YEAR)
    population = _track_group(_year_population(context, keys.KODA_INITIATIVES_BY_YEAR))
    counts: dict[tuple[int | None, str], int] = defaultdict(int)
    for row in _grouped_by_year(population, "track_group"):
        if row["year_key"] is None and not context.period.is_all:
            continue
        counts[(row["year_key"], row["track_group"])] += row["total"]

    def share(values: dict[str, int]) -> str:
        return _share_text(values[INITIATIVE], sum(values.values()))

    matrix_rows, totals = _matrix_rows(
        context, keys.KODA_INITIATIVES_BY_YEAR, TRACK_COLUMNS, counts, share
    )
    all_rows = sum(totals.values())
    matrix = Matrix(
        row_header="Aruandlusaasta",
        columns=tuple(TRACK_GROUP_LABELS[group] for group in TRACK_COLUMNS),
        rows=tuple(matrix_rows),
        column_totals=tuple(totals[group] for group in TRACK_COLUMNS),
        grand_total=all_rows,
        column_header="menetlusliik",
        column_noun="menetlusliigi rühma",
        share_header="Koja algatuste osakaal",
    )
    with_track = totals[INITIATIVE] + totals[OTHER_TRACK]
    notes = [
        f"Katvus: menetlusliik puudub {totals[NO_TRACK]} teemal {all_rows}-st "
        "(peamiselt vanemad registriread); nende seas võib olla märkimata algatusi, "
        "seega on algatuste arv alampiir.",
        f"Kokku {totals[INITIATIVE]} Koja algatust "
        f"({_share_text(totals[INITIATIVE], all_rows)} kõigist teemadest).",
    ]
    return simple_result(
        spec,
        context=context,
        value=totals[INITIATIVE],
        population_count=all_rows,
        eligible_count=with_track,
        coverage_count=with_track,
        coverage_denominator=all_rows,
        url=register_url(context, menetlusliik=Track.KODA_INITIATIVE.value, andmed=DATA_CLASS_REAL)
        if totals[INITIATIVE]
        else "",
        matrix=matrix,
        notes=tuple(notes),
        status=None if all_rows else MetricStatus.INSUFFICIENT_DATA,
    )


# ---------------------------------------------------------------------------
# 6. Sending by month, year beside year
# ---------------------------------------------------------------------------


def submissions_sent_by_month_and_year(context: ReportingContext) -> MetricResult:
    """Months down, measured years across, so one month reads across the years.

    The columns are the measured window — the first year a Submission was
    recorded (never before the archive's first year) to this one. A month of
    this year still to come is printed as a dash: it has not been measured, and
    a 0 there would read as a quiet month.
    """
    spec = definition(keys.SUBMISSIONS_SENT_BY_MONTH_AND_YEAR)
    ever = count(all_sent(context))
    window = measured_window(context)
    if window is None:
        return simple_result(
            spec,
            context=context,
            value=0,
            population_count=0,
            notes=("Ühtki struktuurset arvamuse kirjet ei ole veel salvestatud.",),
        )

    first_year = max(window[0], OPINION_ARCHIVE_FIRST_YEAR)
    years = list(range(first_year, window[1] + 1))
    population = all_sent(context)
    counted: dict[tuple[int, int], int] = {
        (row["sent_at__year"], row["sent_at__month"]): row["total"]
        for row in population.order_by()
        .values("sent_at__year", "sent_at__month")
        .annotate(total=grouped_count())
    }
    today = context.today

    rows: list[MatrixRow] = []
    for month in range(1, 13):
        cells = []
        for year in years:
            future = (year, month) > (today.year, today.month)
            value = counted.get((year, month), 0)
            cells.append(
                MatrixCell(
                    value=value,
                    missing=future,
                    url=_submission_list_url(context, aasta=str(year), kuu=str(month))
                    if value
                    else "",
                )
            )
        rows.append(
            MatrixRow(
                label=MONTH_LABELS[month], cells=tuple(cells), total=sum(c.value for c in cells)
            )
        )
    column_totals = tuple(
        sum(value for (year, _month), value in counted.items() if year == column)
        for column in years
    )
    before = sum(value for (year, _month), value in counted.items() if year < first_year)
    notes = [
        f"Katvus: struktuursed arvamuse kirjed alates aastast {window[0]}; "
        "varasemaid aastaid ei ole mõõdetud.",
        "Arhiivist taastatud arvamused on kanoonilised kirjed ja kuuluvad siia; "
        "arhiivi failid on eraldi plokis.",
    ]
    if before:
        notes.append(
            f"{before} arvamust on dateeritud enne aastat {first_year}, millest "
            "varasemat mõõtmist ei avaldata."
        )
    matrix = Matrix(
        row_header="Kuu",
        columns=tuple(str(year) for year in years),
        rows=tuple(rows),
        column_totals=column_totals,
        grand_total=sum(column_totals),
        column_header="aasta",
        column_noun="aastat",
    )
    return simple_result(
        spec,
        context=context,
        value=sum(column_totals),
        population_count=ever,
        eligible_count=sum(column_totals),
        url=_submission_list_url(context, periood=f"{first_year}-{window[1]}"),
        matrix=matrix,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# The Statistika list: the rows behind a group
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ListRow:
    matter_id: Any
    title: str
    cells: tuple[str, ...]


@dataclass(frozen=True)
class RowList:
    """What one group of one metric counted, ready for a table and a CSV."""

    heading: str
    group_label: str
    columns: tuple[str, ...]
    rows: tuple[ListRow, ...]
    #: The same group in the Teemad register, when the register can express it.
    register_url: str = ""


def _preparation_list(context: ReportingContext, group: str, year: str) -> RowList:
    if group not in PREP_GROUP_LABELS or year:
        raise Http404("Tundmatu rühm.")
    rows = preparation_rows(context)
    selected = [
        row for row in rows if (row.is_measured if group == PREP_ALL else row.group == group)
    ]
    return RowList(
        heading=definition(keys.RESPONSE_PREPARATION_TIME).label_et,
        group_label=PREP_GROUP_LABELS[group],
        columns=("Saabus", "Esimene teadaolev tähtaeg", "Päevi", "Märkus"),
        rows=tuple(
            ListRow(
                matter_id=row.matter_id,
                title=row.title,
                cells=(
                    format_estonian_date(row.received),
                    format_estonian_date(row.deadline),
                    "" if row.days is None else str(row.days),
                    "enne tähtaegade ajalugu" if row.legacy else "",
                ),
            )
            for row in selected
        ),
    )


def _answer_list(context: ReportingContext, group: str, year: str) -> RowList:
    if group not in ANSWER_GROUP_LABELS or year:
        raise Http404("Tundmatu rühm.")
    selected = [
        row
        for row in answer_rows(context)
        if (row.is_verifiable if group == VERIFIABLE else row.group == group)
    ]
    return RowList(
        heading=definition(keys.OPINIONS_ON_TIME).label_et,
        group_label=ANSWER_GROUP_LABELS[group]
        + (" — ei saa kontrollida" if group in UNVERIFIABLE_GROUPS else ""),
        columns=("Arvamus", "Saadetud", "Tähtaeg, millele vastati", "Päevi pärast tähtaega"),
        rows=tuple(
            ListRow(
                matter_id=row.matter_id,
                title=row.matter_title,
                cells=(
                    row.submission_title,
                    format_estonian_date(row.sent_on),
                    format_estonian_date(row.deadline),
                    str(row.days_late) if row.days_late > 0 and row.is_verifiable else "",
                ),
            )
            for row in selected
        ),
    )


def _year_list(
    context: ReportingContext,
    *,
    key: str,
    group: str,
    year: str,
    labels: dict[str, str],
    group_field: str,
    annotate: Callable[[QuerySet[Matter]], QuerySet[Matter]],
) -> RowList:
    """The Matters of one matrix cell: the same population, group and year rule.

    The cell was counted by grouping on ``group_field`` and :func:`_year_key`;
    the list filters the identical annotated population on the same two values,
    so the cell and its list cannot disagree.
    """
    if group not in labels or not year:
        raise Http404("Tundmatu rühm.")
    if year != UNKNOWN_YEAR and not year.isdigit():
        raise Http404("Tundmatu aasta.")
    population = annotate(_year_population(context, key))
    if year == UNKNOWN_YEAR and not context.period.is_all:
        # The matrix shows no unknown-year row under a period; neither does this.
        selected: Iterable[Matter] = []
    else:
        selected = population.filter(_year_q(year), **{group_field: group}).order_by("title", "pk")
    return RowList(
        heading=definition(key).label_et,
        group_label=f"{labels[group]} · {UNKNOWN_YEAR_LABEL if year == UNKNOWN_YEAR else year}",
        columns=("Aruandlusaasta", "Menetlusliik"),
        rows=tuple(
            ListRow(
                matter_id=matter.pk,
                title=matter.title,
                cells=(
                    "" if matter.reporting_year is None else str(matter.reporting_year),
                    matter.get_track_display() if matter.track else "",
                ),
            )
            for matter in selected
        ),
    )


def _eu_notice_list(context: ReportingContext) -> RowList:
    """«sh … «EL teatis»»: the counted subset of «õigusakti järgi», over the period.

    The same annotated population and group as the column, narrowed to the
    Matters carrying the historical notice — so the list is exactly the number
    under the table. Unknown-year Matters belong to it only over all years, as
    they belong to the matrix.
    """
    population = _eu_group(_year_population(context, keys.EU_RELATED_MATTERS_BY_YEAR)).filter(
        # Annotations, so spelled as a mapping the type plugin does not resolve.
        **{"eu_group": EU_RELATED, "has_eu_notice": True}
    )
    if not context.period.is_all:
        population = population.filter(known_year_q())
    return RowList(
        heading=definition(keys.EU_RELATED_MATTERS_BY_YEAR).label_et,
        group_label=EU_NOTICE_SUBSET_LABEL,
        columns=("Aruandlusaasta", "Menetlusliik"),
        rows=tuple(
            ListRow(
                matter_id=matter.pk,
                title=matter.title,
                cells=(
                    "" if matter.reporting_year is None else str(matter.reporting_year),
                    matter.get_track_display() if matter.track else "",
                ),
            )
            for matter in population.order_by("title", "pk")
        ),
    )


def _eu_list(context: ReportingContext, group: str, year: str) -> RowList:
    if group == EU_NOTICE_SUBSET:
        if year:
            raise Http404("Tundmatu rühm.")
        return _eu_notice_list(context)
    return _year_list(
        context,
        key=keys.EU_RELATED_MATTERS_BY_YEAR,
        group=group,
        year=year,
        labels=EU_GROUP_LABELS,
        group_field="eu_group",
        annotate=_eu_group,
    )


def _initiative_list(context: ReportingContext, group: str, year: str) -> RowList:
    listing = _year_list(
        context,
        key=keys.KODA_INITIATIVES_BY_YEAR,
        group=group,
        year=year,
        labels=TRACK_GROUP_LABELS,
        group_field="track_group",
        annotate=_track_group,
    )
    # The register can express two of the three groups exactly; offer it beside
    # the list, with the same data class, so a reader can keep working there.
    track = {INITIATIVE: Track.KODA_INITIATIVE.value, NO_TRACK: MISSING}.get(group)
    if track is None:
        return listing
    return replace(
        listing,
        register_url=register_url(context, menetlusliik=track, aasta=year, andmed=DATA_CLASS_REAL),
    )


ROW_SOURCES: dict[str, Callable[[ReportingContext, str, str], RowList]] = {
    keys.RESPONSE_PREPARATION_TIME: _preparation_list,
    keys.OPINIONS_ON_TIME: _answer_list,
    keys.EU_RELATED_MATTERS_BY_YEAR: _eu_list,
    keys.KODA_INITIATIVES_BY_YEAR: _initiative_list,
}


def metric_rows(context: ReportingContext, key: str, group: str, year: str = "") -> RowList:
    """The rows one group of one metric counted, or a 404 for a link nobody drew."""
    try:
        source = ROW_SOURCES[key]
    except KeyError:
        raise Http404("Tundmatu näitaja.") from None
    return source(context, group, year)
