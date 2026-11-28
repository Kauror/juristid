"""Entering prices and rates: validated, transactional, never rewriting history (r98, ADR 0236).

A price is a row valid from a date; a revision closes the previous open row the day before
and may never overlap another row of the same provider, model and tier. A rate is one row
per currency and day and is never overwritten with a different value. The two commands
(`seed_ai_prices`, `set_ai_price`) and nothing else write these tables.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from app.ai_usage.models import AiFxRate, AiPrice

#: A rate older than this many days before the day it prices is shown as an old rate.
STALE_RATE_DAYS = 5
#: A price whose verification is older than this many days before the day it prices is shown
#: as not recently verified (list prices change; the page must be read again).
STALE_PRICE_DAYS = 120
#: Attempts carry no billing tier, so only the standard tier is ever applied to them.
TIERS_PRICED = ("", "standard")


def _number(value: Any, name: str, *, required: bool = True) -> Decimal | None:
    if value in (None, ""):
        if required:
            raise ValidationError(f"{name} is required")
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValidationError(f"{name} is not a number") from error
    if not number.is_finite() or number < 0:
        raise ValidationError(f"{name} must be a non-negative finite number")
    return number


def _source(url: str) -> str:
    if not url.startswith("https://"):
        raise ValidationError("source_url must be an https:// page")
    return url


@transaction.atomic
def add_price(
    *,
    provider: str = "anthropic",
    model: str,
    tier: str = "",
    currency: str,
    input_per_mtok: Any,
    output_per_mtok: Any,
    cache_read_per_mtok: Any = None,
    cache_creation_per_mtok: Any = None,
    effective_from: dt.date,
    source_url: str,
    verified_on: dt.date,
) -> AiPrice:
    """One new revision, atomically: validate, close the open predecessor, insert."""
    if not model or len(model) > 80:
        raise ValidationError("model is required")
    if len(currency) != 3 or not currency.isalpha() or currency != currency.upper():
        raise ValidationError("currency must be a three-letter upper-case code")
    if verified_on > timezone.localdate():
        raise ValidationError("verified_on cannot be in the future")
    price = AiPrice(
        provider=provider,
        model=model,
        tier=tier,
        currency=currency,
        input_per_mtok=_number(input_per_mtok, "input_per_mtok"),
        output_per_mtok=_number(output_per_mtok, "output_per_mtok"),
        cache_read_per_mtok=_number(cache_read_per_mtok, "cache_read", required=False),
        cache_creation_per_mtok=_number(cache_creation_per_mtok, "cache_creation", required=False),
        effective_from=effective_from,
        source_url=_source(source_url),
        verified_on=verified_on,
    )
    same = AiPrice.objects.select_for_update().filter(provider=provider, model=model, tier=tier)
    latest = same.order_by("-effective_from").first()
    if latest is not None and latest.effective_from >= effective_from:
        raise ValidationError(
            "a price revision must start after the latest existing one for this model "
            f"(there is one from {latest.effective_from})"
        )
    if latest is not None and latest.effective_to is None:
        latest.effective_to = effective_from - dt.timedelta(days=1)
        latest.save(update_fields=["effective_to"])
    elif latest is not None and latest.effective_to >= effective_from:
        raise ValidationError("the new price would overlap an earlier revision")
    price.full_clean()
    price.save()
    return price


@transaction.atomic
def add_rate(
    *, currency: str, on_date: dt.date, per_eur: Any, source: str
) -> tuple[AiFxRate, bool]:
    """(row, created). The same day with the same value is a no-op; a different one is refused."""
    rate = _number(per_eur, "per_eur")
    if rate is None or rate <= 0:
        raise ValidationError("per_eur must be positive")
    if not source.strip():
        raise ValidationError("the rate's source is required")
    existing = (
        AiFxRate.objects.select_for_update().filter(currency=currency, on_date=on_date).first()
    )
    if existing is not None:
        if existing.per_eur != rate:
            raise ValidationError(
                f"a different {currency} rate for {on_date} already exists; rates are not rewritten"
            )
        return existing, False
    row = AiFxRate(currency=currency, on_date=on_date, per_eur=rate, source=source)
    row.full_clean()
    row.save()
    return row, True
