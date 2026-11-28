"""Load the verified price catalogue (`price_catalogue.toml`): a dry run unless --apply.

Idempotent: a price already present for the same model, tier and effective date is left
alone; a different price for a new date closes the previous one the day before, so earlier
estimates keep the revision they were made with. One exchange rate per currency and day,
read once; no rate is fetched per request. Never run by a deploy automatically.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from app.ai_usage.models import AiFxRate, AiPrice

CATALOGUE = Path(__file__).resolve().parents[2] / "price_catalogue.toml"


def _dec(value: Any) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


class Command(BaseCommand):
    help = "Dry-run (default) or apply the verified AI price catalogue."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        data = tomllib.loads(CATALOGUE.read_text(encoding="utf-8"))
        apply = bool(options["apply"])
        with transaction.atomic():
            for entry in data.get("price", []):
                start = dt.date.fromisoformat(entry["effective_from"])
                same = AiPrice.objects.filter(
                    provider=entry["provider"],
                    model=entry["model"],
                    tier=entry.get("tier", ""),
                    effective_from=start,
                )
                if same.exists():
                    self.stdout.write(f"price {entry['model']} {start}: already present")
                    continue
                self.stdout.write(
                    f"price {entry['model']} from {start}: {'add' if apply else 'would add'}"
                )
                if apply:
                    AiPrice.objects.filter(
                        provider=entry["provider"],
                        model=entry["model"],
                        effective_to__isnull=True,
                        effective_from__lt=start,
                    ).update(effective_to=start - dt.timedelta(days=1))
                    AiPrice.objects.create(
                        provider=entry["provider"],
                        model=entry["model"],
                        tier=entry.get("tier", ""),
                        currency=entry["currency"],
                        input_per_mtok=Decimal(entry["input_per_mtok"]),
                        output_per_mtok=Decimal(entry["output_per_mtok"]),
                        cache_read_per_mtok=_dec(entry.get("cache_read_per_mtok")),
                        cache_creation_per_mtok=_dec(entry.get("cache_creation_per_mtok")),
                        effective_from=start,
                        source_url=entry["source_url"],
                        verified_on=dt.date.fromisoformat(entry["verified_on"]),
                    )
            for rate in data.get("fx", []):
                day = dt.date.fromisoformat(rate["on_date"])
                exists = AiFxRate.objects.filter(currency=rate["currency"], on_date=day).exists()
                self.stdout.write(
                    f"rate {rate['currency']} {day}: "
                    + ("already present" if exists else "add" if apply else "would add")
                )
                if apply and not exists:
                    AiFxRate.objects.create(
                        currency=rate["currency"],
                        on_date=day,
                        per_eur=Decimal(rate["per_eur"]),
                        source=rate["source"],
                    )
            if not apply:
                self.stdout.write("dry run: nothing written (use --apply)")
