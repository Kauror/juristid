"""Price and rate entry: validated, overlap-free, transactional, never rewriting (r98 final)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction

from app.ai_usage import prices, report
from app.ai_usage.models import AiFxRate, AiPrice

pytestmark = pytest.mark.django_db

SRC = "https://example.test/pricing"
TODAY = dt.date.today()


def _add(**kw):
    values = {
        "model": "m1",
        "currency": "USD",
        "input_per_mtok": "4",
        "output_per_mtok": "20",
        "effective_from": dt.date(2026, 1, 1),
        "source_url": SRC,
        "verified_on": TODAY,
    }
    values.update(kw)
    return prices.add_price(**values)


@pytest.mark.parametrize(
    "bad",
    [
        {"input_per_mtok": "-1"},
        {"output_per_mtok": "-0.01"},
        {"input_per_mtok": "NaN"},
        {"input_per_mtok": "abc"},
        {"cache_read_per_mtok": "-3"},
        {"currency": "usd"},
        {"currency": "US"},
        {"source_url": "http://insecure.test/p"},
        {"verified_on": TODAY + dt.timedelta(days=3)},
        {"model": ""},
    ],
)
def test_invalid_entries_are_refused_and_leave_nothing(bad):
    with pytest.raises(ValidationError):
        _add(**bad)
    assert AiPrice.objects.count() == 0


def test_the_database_itself_refuses_a_negative_rate_and_a_backwards_range():
    for fields in ({"input_per_mtok": Decimal("-1")}, {"effective_to": dt.date(2025, 1, 1)}):
        values = {
            "model": "m",
            "currency": "USD",
            "input_per_mtok": Decimal("1"),
            "output_per_mtok": Decimal("1"),
            "effective_from": dt.date(2026, 1, 1),
            "source_url": SRC,
            "verified_on": TODAY,
        }
        values.update(fields)
        with pytest.raises(IntegrityError), transaction.atomic():
            AiPrice.objects.create(**values)


def test_a_revision_closes_the_previous_row_and_never_overlaps():
    first = _add()
    second = _add(effective_from=dt.date(2026, 6, 1), input_per_mtok="5")
    first.refresh_from_db()
    assert first.effective_to == dt.date(2026, 5, 31) and second.effective_to is None
    book = report.PriceBook.load()
    assert book.price("m1", dt.date(2026, 5, 31)).pk == first.pk  # history keeps its revision
    assert book.price("m1", dt.date(2026, 6, 1)).pk == second.pk


@pytest.mark.parametrize("start", [dt.date(2026, 6, 1), dt.date(2026, 3, 1), dt.date(2025, 12, 31)])
def test_a_revision_not_after_the_latest_is_refused_and_changes_nothing(start):
    _add()
    _add(effective_from=dt.date(2026, 6, 1), input_per_mtok="5")
    before = list(AiPrice.objects.order_by("effective_from").values_list("pk", "effective_to"))
    with pytest.raises(ValidationError):
        _add(effective_from=start, input_per_mtok="9")
    assert (
        list(AiPrice.objects.order_by("effective_from").values_list("pk", "effective_to")) == before
    )


def test_a_failing_revision_does_not_leave_the_old_price_closed(monkeypatch):
    old = _add()
    monkeypatch.setattr(
        AiPrice, "full_clean", lambda self, *a, **k: (_ for _ in ()).throw(ValidationError("boom"))
    )
    with pytest.raises(ValidationError):
        _add(effective_from=dt.date(2026, 6, 1))
    old.refresh_from_db()
    assert old.effective_to is None  # the close and the insert are one transaction


def test_other_tiers_are_never_applied_to_attempts():
    _add(tier="batch", input_per_mtok="1", output_per_mtok="1")
    standard = _add(tier="standard")
    book = report.PriceBook.load()
    assert book.price("m1", dt.date(2026, 3, 1)).pk == standard.pk


def test_a_rate_is_stored_once_and_never_rewritten():
    day = dt.date(2026, 11, 27)
    row, created = prices.add_rate(currency="USD", on_date=day, per_eur="1.1", source="ECB")
    assert created
    again, created = prices.add_rate(currency="USD", on_date=day, per_eur="1.1", source="ECB")
    assert again.pk == row.pk and not created
    with pytest.raises(ValidationError):
        prices.add_rate(currency="USD", on_date=day, per_eur="1.2", source="ECB")
    for bad in ("0", "-1", "x"):
        with pytest.raises(ValidationError):
            prices.add_rate(
                currency="USD", on_date=dt.date(2026, 11, 28), per_eur=bad, source="ECB"
            )
    with pytest.raises(ValidationError):
        prices.add_rate(currency="USD", on_date=dt.date(2026, 11, 28), per_eur="1.1", source=" ")
    assert AiFxRate.objects.get().per_eur == Decimal("1.1")


def test_the_estimate_names_its_price_revision_and_rate_date():
    old = _add(input_per_mtok="4")
    prices.add_rate(currency="USD", on_date=dt.date(2026, 2, 27), per_eur="2", source="s")
    book = report.PriceBook.load()
    cost = report.cost_of(
        book,
        model="m1",
        day=dt.date(2026, 3, 2),
        status="MEASURED",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read=0,
        cache_creation=0,
    )
    assert cost.price_id == old.pk and cost.rate_on == dt.date(2026, 2, 27)
    assert cost.eur == Decimal("2") and not cost.stale_rate
    far = report.cost_of(
        book,
        model="m1",
        day=dt.date(2026, 3, 20),
        status="MEASURED",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read=0,
        cache_creation=0,
    )
    assert far.stale_rate  # the same old rate, 21 days later, is called old


def test_the_seed_dry_run_validates_prints_and_writes_nothing():
    out = StringIO()
    call_command("seed_ai_prices", stdout=out)
    text = out.getvalue()
    assert "would add" in text and "dry run: nothing written" in text
    assert "https://" in text and "verified" in text
    assert AiPrice.objects.count() == 0 and AiFxRate.objects.count() == 0


def test_the_seed_apply_is_idempotent_and_matches_the_dry_run():
    call_command("seed_ai_prices", "--apply", stdout=StringIO())
    rows = AiPrice.objects.count(), AiFxRate.objects.count()
    assert rows[0] >= 1 and rows[1] >= 1
    again = StringIO()
    call_command("seed_ai_prices", "--apply", stdout=again)
    assert (AiPrice.objects.count(), AiFxRate.objects.count()) == rows
    assert "already present" in again.getvalue()


def test_the_command_refuses_a_bad_entry_with_a_message():
    with pytest.raises(CommandError):
        call_command(
            "set_ai_price",
            model="m",
            input_price="-1",
            output_price="1",
            effective_from="2026-01-01",
            source_url=SRC,
            verified_on=TODAY.isoformat(),
        )
    assert AiPrice.objects.count() == 0
