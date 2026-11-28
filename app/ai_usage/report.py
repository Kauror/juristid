"""What the ledger says, priced and summed (r98, ADR 0236).

Every figure is read from `AiOperation` / `AiAttempt` rows; nothing calls a
provider. Cost is an **estimate from list prices**, never an invoice:

* an attempt with no measured tokens has an **unknown** cost (not zero);
* an attempt whose model has no price valid on its day has an unknown cost;
* the price used is the row in force on the attempt's Europe/Tallinn day, so a
  later price never rewrites an earlier estimate (`Cost.price_id` names the row);
* a non-euro price is converted with a stored `AiFxRate` for that day (the latest
  earlier one if none); without a rate the cost is unknown;
* cache tokens that were not reported make the figure a lower bound, counted in
  ``cache_unknown``.

Aggregates and detail rows use the same `cost_of` and the same `population`, so they always
reconcile.

**One completeness definition** (`Totals.incomplete`): a total is *complete* only when none of
these holds, and it is then the same word for a day, a service, a model, the selected period,
the cost per item and the budget. Anything else is a **known subtotal** (a lower bound), never
shown as a total: unknown cost; cache tokens not reported; a stale price or rate; a request
still running or interrupted (its result is unknown); an interrupted request with no stored
attempt (it may have sent one); a request whose HTTP attempts were not watched; a request whose
result could not be saved (`AiLedgerGap`); a period that starts before the ledger does.

**Populations.** A request belongs to the Europe/Tallinn day it *started*; all its attempts and
costs go to that day, so a request crossing midnight is counted once, on one day. A filter acts
on the logical request: service = the request's service; outcome = its effective outcome; model
= the model that answered an attempt (else the one requested) — with it only the matching
attempts are counted and only requests that have one are listed.
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
from django.db.models import Q, QuerySet
from django.utils import timezone

from app.ai_usage import prices as price_rules
from app.ai_usage.models import (
    GOOD,
    AiAttempt,
    AiFxRate,
    AiLedgerGap,
    AiOperation,
    AiPrice,
    AttemptCoverage,
    Outcome,
    UsageStatus,
)

TALLINN = ZoneInfo("Europe/Tallinn")
MILLION = Decimal(1_000_000)
CENT = Decimal("0.0001")

#: Why a figure is a known subtotal and not a total: key → Estonian label (the one list the
#: pages, the budget and the overview all read).
REASONS = {
    "unknown_cost_attempts": "päringu maksumus teadmata (tokenid või hind puuduvad)",
    "cache_unknown": "vahemälu tokenid teadmata (maksumus on alampiir)",
    "stale_price": "hinda pole viimasel ajal üle kontrollitud",
    "stale_rate": "vahetuskurss on vana",
    "ops_running": "päring on veel pooleli (tulemus teadmata)",
    "ops_interrupted": "päring katkestati (tulemus teadmata)",
    "ops_unobserved": "katkestatud päringul pole salvestatud katset (katse võis olla)",
    "ops_unwatched": "päringu HTTP-katseid ei jälgitud (katsete arv teadmata)",
    "gaps": "päringu tulemust ei õnnestunud salvestada",
    "ledger_starts_late": "periood algab enne arvestuse algust",
}


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
        for price in AiPrice.objects.filter(tier__in=price_rules.TIERS_PRICED).order_by(
            "-effective_from"
        ):
            book.prices.setdefault(price.model, []).append(price)
        for rate in AiFxRate.objects.order_by("-on_date"):
            book.rates.setdefault(rate.currency, []).append(rate)
        return book

    def price(self, model: str, day: dt.date) -> AiPrice | None:
        for row in self.prices.get(model, []):
            if row.effective_from <= day and (row.effective_to is None or day <= row.effective_to):
                return row
        return None

    def rate(self, currency: str, day: dt.date) -> AiFxRate | None:
        for rate in self.rates.get(currency, []):
            if rate.on_date <= day:
                return rate
        return None

    def per_eur(self, currency: str, day: dt.date) -> Decimal | None:
        if currency == "EUR":
            return Decimal(1)
        found = self.rate(currency, day)
        return None if found is None else found.per_eur


@dataclass(frozen=True)
class Cost:
    eur: Decimal | None  # None = unknown
    original: Decimal | None = None
    currency: str = ""
    price_id: int | None = None  # the revision this estimate used
    rate_on: dt.date | None = None  # the exchange rate's date (None for EUR prices)
    lower_bound: bool = False  # cache tokens were not reported
    stale_price: bool = False  # the price's page was last verified long before this day
    stale_rate: bool = False  # the rate is much older than this day
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
    stale_price = (day - price.verified_on).days > price_rules.STALE_PRICE_DAYS
    if price.currency == "EUR":
        per_eur, rate_on, stale_rate = Decimal(1), None, False
    else:
        row = book.rate(price.currency, day)
        if row is None:
            return Cost(None, original, price.currency, price.pk, reason="no_rate")
        per_eur, rate_on = row.per_eur, row.on_date
        stale_rate = (day - row.on_date).days > price_rules.STALE_RATE_DAYS
    return Cost(
        original / per_eur,
        original,
        price.currency,
        price.pk,
        rate_on,
        lower_bound=cache_read is None or cache_creation is None,
        stale_price=stale_price,
        stale_rate=stale_rate,
    )


def attempt_cost(book: PriceBook, attempt: AiAttempt) -> Cost:
    return cost_of(
        book,
        model=model_of(attempt.actual_model, attempt.requested_model),
        day=local_date(attempt.started_at),
        status=attempt.usage_status,
        input_tokens=attempt.input_tokens,
        output_tokens=attempt.output_tokens,
        cache_read=attempt.cache_read_tokens,
        cache_creation=attempt.cache_creation_tokens,
        fallback_served=attempt.fallback_observed,
    )


def model_of(actual: str, requested: str) -> str:
    """The model an attempt is counted under: the one that answered, else the one asked."""
    return actual or requested


# -- the summary ----------------------------------------------------------------------------


@dataclass
class Totals:
    operations: int = 0
    attempts: int = 0
    ops_good: int = 0
    ops_failed: int = 0
    #: Logical requests that sent no HTTP request (information, not a gap).
    ops_no_attempt: int = 0
    retried_attempts: int = 0
    failed_attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    cost_eur: Decimal = Decimal(0)  # the KNOWN subtotal
    unknown_cost_attempts: int = 0
    cache_unknown: int = 0
    stale_price: int = 0
    stale_rate: int = 0
    ops_running: int = 0
    ops_interrupted: int = 0
    ops_unobserved: int = 0
    ops_unwatched: int = 0
    gaps: int = 0
    ledger_starts_late: int = 0
    unknown_reasons: dict[str, int] = field(default_factory=dict)
    items_presented: int = 0
    items_classified: int = 0

    @property
    def incomplete(self) -> dict[str, int]:
        """Why this is a known subtotal and not a total: reason key → how many (the one rule)."""
        return {key: getattr(self, key) for key in REASONS if getattr(self, key)}

    @property
    def complete(self) -> bool:
        return not self.incomplete

    @property
    def incomplete_labels(self) -> list[str]:
        return [f"{count} × {REASONS[key]}" for key, count in self.incomplete.items()]

    @property
    def cost_per_item(self) -> Decimal | None:
        """Known subtotal ÷ classified items. A lower bound unless `complete`."""
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
            if cost.stale_price:
                self.stale_price += 1
            if cost.stale_rate:
                self.stale_rate += 1

    def add_operation(self, op: AiOperation, outcome: str, attempt_rows: int) -> None:
        self.operations += 1
        self.items_presented += op.items_presented or 0
        self.items_classified += op.items_classified or 0
        if outcome in GOOD:
            self.ops_good += 1
        elif outcome == Outcome.IN_PROGRESS:
            self.ops_running += 1
        else:
            self.ops_failed += 1
        if outcome == Outcome.INTERRUPTED:
            self.ops_interrupted += 1
            if not attempt_rows:
                self.ops_unobserved += 1
        if op.attempt_coverage == AttemptCoverage.UNAVAILABLE:
            self.ops_unwatched += 1
        elif op.attempt_coverage == AttemptCoverage.NONE:
            self.ops_no_attempt += 1


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
    #: The selected logical requests: the detail table and the export read exactly these.
    op_ids: list[Any] = field(default_factory=list)


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


_ATTEMPT_FIELDS = (
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
    "headers_ms",
    "fallback_observed",
)


def model_filter(model: str) -> Q:
    """Attempts counted under ``model`` (see `model_of`)."""
    return Q(actual_model=model) | Q(actual_model="", requested_model=model)


def summarise(
    start: dt.date,
    end: dt.date,
    *,
    service: str = "",
    model: str = "",
    outcome: str = "",
    book: PriceBook | None = None,
) -> Summary:
    book = book or PriceBook.load()
    now = timezone.now()
    first, after = day_bounds(start, end)
    found = AiOperation.objects.filter(started_at__gte=first, started_at__lt=after)
    if service:
        found = found.filter(service=service)
    operations = list(found)
    outcomes = {op.pk: effective_outcome(op, now) for op in operations}
    if outcome:
        operations = [op for op in operations if outcomes[op.pk] == outcome]
    rows_by_op: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    rows = AiAttempt.objects.filter(operation_id__in=[op.pk for op in operations]).values(
        *_ATTEMPT_FIELDS
    )
    for row in rows:
        if model and model_of(row["actual_model"], row["requested_model"]) != model:
            continue
        rows_by_op[row["operation_id"]].append(row)
    if model:
        operations = [op for op in operations if rows_by_op.get(op.pk)]

    totals = Totals()
    by_service: dict[str, Totals] = defaultdict(Totals)
    by_model: dict[str, Totals] = defaultdict(Totals)
    by_day: dict[dt.date, Totals] = defaultdict(Totals)
    by_outcome: dict[str, int] = defaultdict(int)
    durations: list[int] = []
    per_service_durations: dict[str, list[int]] = defaultdict(list)
    unpriced: set[str] = set()
    for op in operations:
        day = local_date(op.started_at)
        op_rows = rows_by_op.get(op.pk, [])
        state = outcomes[op.pk]
        by_outcome[state] += 1
        for bucket in (totals, by_service[op.service], by_day[day]):
            bucket.add_operation(op, state, len(op_rows))
        if state == Outcome.INTERRUPTED and not op_rows:
            # no attempt row to carry the model: the requested one holds the unknown
            by_model[op.requested_model].ops_unobserved += 1
        for row in op_rows:
            name = model_of(row["actual_model"], row["requested_model"])
            cost = cost_of(
                book,
                model=name,
                day=local_date(row["started_at"]),
                status=row["usage_status"],
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
                cache_read=row["cache_read_tokens"],
                cache_creation=row["cache_creation_tokens"],
                fallback_served=row["fallback_observed"],
            )
            if row["usage_status"] == UsageStatus.MEASURED and cost.reason == "no_price":
                unpriced.add(name)
            for bucket in (totals, by_service[op.service], by_model[name], by_day[day]):
                bucket.add_attempt(row, cost)
            if row["headers_ms"] is not None:
                durations.append(row["headers_ms"])
                per_service_durations[op.service].append(row["headers_ms"])
    first_record = (
        AiOperation.objects.order_by("started_at").values_list("started_at", flat=True).first()
    )
    if first_record is not None and start < local_date(first_record):
        totals.ledger_starts_late = 1
    gaps = AiLedgerGap.objects.filter(at__gte=first, at__lt=after)
    if service:
        gaps = gaps.filter(service=service)
    gap_count = 0
    for gap in gaps:
        gap_count += 1
        for bucket in (totals, by_service[gap.service], by_day[local_date(gap.at)]):
            bucket.gaps += 1
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
        first_record=first_record,
        gaps=gap_count,
        interrupted=by_outcome.get(Outcome.INTERRUPTED, 0),
        unpriced_models=sorted(unpriced),
        durations_by_service={
            name: (_percentile(values, 0.5), _percentile(values, 0.95))
            for name, values in per_service_durations.items()
        },
        op_ids=[op.pk for op in operations],
    )


def attempts_of(summary: Summary, model: str = "") -> QuerySet[AiAttempt]:
    """The detail rows of a summary: the attempts of exactly its selected requests (and, with
    a model filter, only those counted under that model), newest first."""
    found = AiAttempt.objects.filter(operation_id__in=summary.op_ids).select_related("operation")
    if model:
        found = found.filter(model_filter(model))
    return found.order_by("-started_at", "-sequence")


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
    """Accounting health and budget policy, kept apart.

    ``complete`` says whether ``known_eur`` is the whole month's estimate (health). ``level`` is
    the policy: «none» no threshold set; «ok» a complete total below every threshold;
    «unproven» a known subtotal below the threshold while the total is incomplete (it proves
    nothing); «warn»/«escalate» the known subtotal alone reaches the threshold (a lower bound
    that already reaches it is proof)."""

    known_eur: Decimal
    warn: Decimal | None
    escalate: Decimal | None
    reasons: dict[str, int]
    level: str  # "none" | "ok" | "unproven" | "warn" | "escalate"
    complete: bool
    unknown_cost_attempts: int


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
    complete = totals.complete
    if warn is None and escalate is None:
        level = "none"
    elif escalate is not None and totals.cost_eur >= escalate:
        level = "escalate"
    elif warn is not None and totals.cost_eur >= warn:
        level = "warn"
    else:
        level = "ok" if complete else "unproven"
    return Budget(
        totals.cost_eur,
        warn,
        escalate,
        totals.incomplete,
        level,
        complete,
        totals.unknown_cost_attempts,
    )


def money(value: Decimal | None) -> str:
    return "Maksumus teadmata" if value is None else f"{value.quantize(CENT):f} €"


def known_money(totals: Totals) -> str:
    """A cost for a table cell: a total only when complete; else the known subtotal, said so."""
    text = f"{totals.cost_eur.quantize(CENT):f} €"
    return text if totals.complete else f"vähemalt {text}"
