"""What the ledger says, priced and summed (r98, ADR 0236).

Every figure is read from `AiOperation` / `AiAttempt` rows; nothing calls a
provider. Cost is an **estimate from list prices**, never an invoice:

* an attempt with no measured tokens has an **unknown** cost (not zero);
* an attempt whose model has no price valid on its day has an unknown cost;
* the price used is the row in force on the attempt's Europe/Tallinn day, so a
  later price never rewrites an earlier estimate;
* a non-euro price is converted with a stored `AiFxRate` for that day (the latest
  earlier one if none); without a rate the cost is unknown;
* cache tokens that were not reported make the figure a lower bound, counted in
  ``cache_unknown``.

Aggregates and detail rows use the same `cost_of`, so they always reconcile.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from app.ai_usage.models import (
    GOOD,
    AiAttempt,
    AiFxRate,
    AiLedgerGap,
    AiOperation,
    AiPrice,
    Outcome,
    UsageStatus,
)

TALLINN = ZoneInfo("Europe/Tallinn")
MILLION = Decimal(1_000_000)
CENT = Decimal("0.0001")


def local_date(moment: dt.datetime) -> dt.date:
    return moment.astimezone(TALLINN).date()


def day_bounds(start: dt.date, end: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """[start 00:00, end+1 00:00) in Europe/Tallinn, as aware datetimes."""
    first = dt.datetime.combine(start, dt.time.min, tzinfo=TALLINN)
    after = dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, tzinfo=TALLINN)
    return first, after


def period(
    key: str, today: dt.date | None = None, custom: tuple[dt.date, dt.date] | None = None
) -> tuple[dt.date, dt.date]:
    today = today or local_date(timezone.now())
    if key == "today":
        return today, today
    if key == "7d":
        return today - dt.timedelta(days=6), today
    if key == "prev_month":
        last = today.replace(day=1) - dt.timedelta(days=1)
        return last.replace(day=1), last
    if key == "custom" and custom is not None:
        return min(custom), max(custom)
    return today.replace(day=1), today  # "month"


# -- prices --------------------------------------------------------------------------------


@dataclass
class PriceBook:
    """Prices and rates read once, looked up per (model, day)."""

    prices: dict[str, list[AiPrice]] = field(default_factory=dict)
    rates: dict[str, list[AiFxRate]] = field(default_factory=dict)

    @classmethod
    def load(cls) -> PriceBook:
        book = cls()
        for price in AiPrice.objects.order_by("-effective_from"):
            book.prices.setdefault(price.model, []).append(price)
        for rate in AiFxRate.objects.order_by("-on_date"):
            book.rates.setdefault(rate.currency, []).append(rate)
        return book

    def price(self, model: str, day: dt.date) -> AiPrice | None:
        for row in self.prices.get(model, []):
            if row.effective_from <= day and (row.effective_to is None or day <= row.effective_to):
                return row
        return None

    def per_eur(self, currency: str, day: dt.date) -> Decimal | None:
        if currency == "EUR":
            return Decimal(1)
        for rate in self.rates.get(currency, []):
            if rate.on_date <= day:
                return rate.per_eur
        return None


@dataclass(frozen=True)
class Cost:
    eur: Decimal | None  # None = unknown
    original: Decimal | None = None
    currency: str = ""
    price_id: int | None = None
    lower_bound: bool = False  # cache tokens were not reported
    reason: str = ""  # why eur is None: no_usage, no_price, no_rate, cache_price, fallback


UNKNOWN = Cost(None, reason="no_usage")


def cost_of(
    book: PriceBook,
    *,
    model: str,
    day: dt.date,
    status: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read: int | None,
    cache_creation: int | None,
    fallback_served: bool | None = None,
) -> Cost:
    if fallback_served:
        # The top-level tokens of a fallback-served answer are not attributable to one
        # model's price (the provider reports per-iteration usage); never guess.
        return Cost(None, reason="fallback")
    if status != UsageStatus.MEASURED or input_tokens is None or output_tokens is None:
        return UNKNOWN
    price = book.price(model, day) if model else None
    if price is None:
        return Cost(None, reason="no_price")
    total = (
        Decimal(input_tokens) * price.input_per_mtok
        + Decimal(output_tokens) * price.output_per_mtok
    )
    for tokens, rate in (
        (cache_read, price.cache_read_per_mtok),
        (cache_creation, price.cache_creation_per_mtok),
    ):
        if tokens:
            if rate is None:
                return Cost(None, reason="cache_price")  # billed, unpriced: do not guess
            total += Decimal(tokens) * rate
    original = total / MILLION
    per_eur = book.per_eur(price.currency, day)
    if per_eur is None:
        return Cost(None, original, price.currency, price.pk, reason="no_rate")
    return Cost(
        original / per_eur,
        original,
        price.currency,
        price.pk,
        lower_bound=cache_read is None or cache_creation is None,
    )


def attempt_cost(book: PriceBook, attempt: AiAttempt) -> Cost:
    return cost_of(
        book,
        model=attempt.actual_model or attempt.requested_model,
        day=local_date(attempt.started_at),
        status=attempt.usage_status,
        input_tokens=attempt.input_tokens,
        output_tokens=attempt.output_tokens,
        cache_read=attempt.cache_read_tokens,
        cache_creation=attempt.cache_creation_tokens,
        fallback_served=attempt.fallback_observed,
    )


# -- the summary ----------------------------------------------------------------------------


@dataclass
class Totals:
    operations: int = 0
    attempts: int = 0
    ops_good: int = 0
    ops_failed: int = 0
    retried_attempts: int = 0
    failed_attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    cost_eur: Decimal = Decimal(0)
    unknown_cost_attempts: int = 0
    cache_unknown: int = 0
    unknown_reasons: dict[str, int] = field(default_factory=dict)
    items_presented: int = 0
    items_classified: int = 0

    @property
    def cost_per_item(self) -> Decimal | None:
        if not self.items_classified:
            return None
        return self.cost_eur / self.items_classified

    def add_attempt(self, attempt: Mapping[str, Any], cost: Cost) -> None:
        self.attempts += 1
        self.input_tokens += attempt["input_tokens"] or 0
        self.output_tokens += attempt["output_tokens"] or 0
        self.cache_read += attempt["cache_read_tokens"] or 0
        self.cache_creation += attempt["cache_creation_tokens"] or 0
        if attempt["retry_class"] == "retried":
            self.retried_attempts += 1
        if attempt["outcome"] != "OK":
            self.failed_attempts += 1
        if cost.eur is None:
            self.unknown_cost_attempts += 1
            self.unknown_reasons[cost.reason] = self.unknown_reasons.get(cost.reason, 0) + 1
        else:
            self.cost_eur += cost.eur
            if cost.lower_bound:
                self.cache_unknown += 1


@dataclass
class Summary:
    start: dt.date
    end: dt.date
    totals: Totals
    by_service: dict[str, Totals]
    by_model: dict[str, Totals]
    by_day: dict[dt.date, Totals]
    by_outcome: dict[str, int]
    median_ms: int | None
    p95_ms: int | None
    first_record: dt.datetime | None
    gaps: int
    interrupted: int
    unpriced_models: list[str]
    durations_by_service: dict[str, tuple[int | None, int | None]]


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(q * (len(ordered) - 1))))
    return ordered[index]


def effective_outcome(op: AiOperation, now: dt.datetime) -> str:
    """The stored outcome, except that a request still IN_PROGRESS past the latest instant
    it could be running is shown interrupted (its result is unknown). Reading never writes."""
    if op.outcome != Outcome.IN_PROGRESS:
        return str(op.outcome)
    limit = op.expected_end_at or (op.started_at + dt.timedelta(hours=3))
    return str(Outcome.INTERRUPTED) if limit < now else str(Outcome.IN_PROGRESS)


def operations_in(
    start: dt.date, end: dt.date, *, service: str = "", model: str = ""
) -> QuerySet[AiOperation]:
    first, after = day_bounds(start, end)
    found = AiOperation.objects.filter(started_at__gte=first, started_at__lt=after)
    if service:
        found = found.filter(service=service)
    if model:
        found = found.filter(attempts__actual_model=model).distinct()
    return found


def summarise(
    start: dt.date,
    end: dt.date,
    *,
    service: str = "",
    model: str = "",
    book: PriceBook | None = None,
) -> Summary:
    book = book or PriceBook.load()
    operations = list(operations_in(start, end, service=service, model=model))
    ids = [op.pk for op in operations]
    by_op = {op.pk: op for op in operations}
    totals = Totals()
    by_service: dict[str, Totals] = defaultdict(Totals)
    by_model: dict[str, Totals] = defaultdict(Totals)
    by_day: dict[dt.date, Totals] = defaultdict(Totals)
    by_outcome: dict[str, int] = defaultdict(int)
    durations: list[int] = []
    per_service_durations: dict[str, list[int]] = defaultdict(list)
    unpriced: set[str] = set()
    now = timezone.now()
    for op in operations:
        outcome = effective_outcome(op, now)
        for bucket in (totals, by_service[op.service], by_day[local_date(op.started_at)]):
            bucket.operations += 1
            bucket.items_presented += op.items_presented or 0
            bucket.items_classified += op.items_classified or 0
            if outcome in GOOD:
                bucket.ops_good += 1
            elif outcome != Outcome.IN_PROGRESS:
                bucket.ops_failed += 1
        by_outcome[outcome] += 1
    rows = AiAttempt.objects.filter(operation_id__in=ids).values(
        "operation_id",
        "started_at",
        "requested_model",
        "actual_model",
        "usage_status",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "retry_class",
        "outcome",
        "duration_ms",
        "fallback_observed",
    )
    for row in rows:
        op = by_op[row["operation_id"]]
        model_name = row["actual_model"] or row["requested_model"]
        cost = cost_of(
            book,
            model=model_name,
            day=local_date(row["started_at"]),
            status=row["usage_status"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cache_read=row["cache_read_tokens"],
            cache_creation=row["cache_creation_tokens"],
            fallback_served=row["fallback_observed"],
        )
        if row["usage_status"] == UsageStatus.MEASURED and cost.reason == "no_price":
            unpriced.add(model_name)
        for bucket in (
            totals,
            by_service[op.service],
            by_model[model_name],
            by_day[local_date(row["started_at"])],
        ):
            bucket.add_attempt(row, cost)
        if row["duration_ms"] is not None:
            durations.append(row["duration_ms"])
            per_service_durations[op.service].append(row["duration_ms"])
    first = AiOperation.objects.order_by("started_at").values_list("started_at", flat=True).first()
    lo, hi = day_bounds(start, end)
    return Summary(
        start=start,
        end=end,
        totals=totals,
        by_service=dict(by_service),
        by_model=dict(by_model),
        by_day=dict(sorted(by_day.items())),
        by_outcome=dict(by_outcome),
        median_ms=_percentile(durations, 0.5),
        p95_ms=_percentile(durations, 0.95),
        first_record=first,
        gaps=AiLedgerGap.objects.filter(at__gte=lo, at__lt=hi).count(),
        interrupted=by_outcome.get(Outcome.INTERRUPTED, 0),
        unpriced_models=sorted(unpriced),
        durations_by_service={
            name: (_percentile(values, 0.5), _percentile(values, 0.95))
            for name, values in per_service_durations.items()
        },
    )


def coverage_note(summary: Summary) -> str:
    """Whether the period precedes the ledger: absence of rows then is not zero spend."""
    if summary.first_record is None:
        return "Registrit pole veel ühtegi päringut; varasem kasutus on teadmata."
    first_day = local_date(summary.first_record)
    if summary.start < first_day:
        return (
            f"Päringute arvestus algab {first_day.day}.{first_day.month}.{first_day.year}; "
            "varasem kasutus on teadmata (mitte null)."
        )
    return ""


# -- budget ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Budget:
    spent_eur: Decimal
    warn: Decimal | None
    escalate: Decimal | None
    unknown_cost_attempts: int
    level: str  # "none" | "ok" | "warn" | "escalate"
    complete: bool


def _setting_decimal(name: str) -> Decimal | None:
    raw = str(getattr(settings, name, "") or "").strip().replace(",", ".")
    try:
        value = Decimal(raw)
    except Exception:
        return None
    return value if value > 0 else None


def budget(today: dt.date | None = None, book: PriceBook | None = None) -> Budget:
    start, end = period("month", today)
    totals = summarise(start, end, book=book).totals
    warn, escalate = (
        _setting_decimal("AI_BUDGET_WARN_EUR"),
        _setting_decimal("AI_BUDGET_ESCALATE_EUR"),
    )
    complete = totals.unknown_cost_attempts == 0
    if warn is None and escalate is None:
        level = "none"
    elif escalate is not None and totals.cost_eur >= escalate:
        level = "escalate"
    elif warn is not None and totals.cost_eur >= warn:
        level = "warn"
    else:
        level = "ok"
    return Budget(totals.cost_eur, warn, escalate, totals.unknown_cost_attempts, level, complete)


def money(value: Decimal | None) -> str:
    return "Maksumus teadmata" if value is None else f"{value.quantize(CENT):f} €"
