"""Load the verified price catalogue (`price_catalogue.toml`): a dry run unless --apply.

Idempotent: a price already present for the same model, tier and effective date is left
alone; a later revision closes the previous one the day before, so earlier estimates keep the
revision they were made with. One exchange rate per currency and day, read once; no rate is
fetched per request. The dry run goes through the same validation as --apply (inside a
rolled-back transaction), so an invalid catalogue fails here, not at release. Never run by a
deploy automatically.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.ai_usage import prices
from app.ai_usage.models import AiFxRate, AiPrice

CATALOGUE = Path(__file__).resolve().parents[2] / "price_catalogue.toml"


class _DryRun(Exception):
    """Raised to roll the dry run's transaction back."""


class Command(BaseCommand):
    help = "Dry-run (default) or apply the verified AI price catalogue."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        data = tomllib.loads(CATALOGUE.read_text(encoding="utf-8"))
        apply = bool(options["apply"])
        try:
            with transaction.atomic():
                self._run(data, apply)
                if not apply:
                    raise _DryRun
        except _DryRun:
            self.stdout.write("dry run: nothing written (use --apply)")
        except ValidationError as error:
            raise CommandError(f"the catalogue is invalid: {'; '.join(error.messages)}") from error

    def _run(self, data: dict[str, Any], apply: bool) -> None:
        verb = "add" if apply else "would add"
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
                f"price {entry['model']} ({entry.get('tier') or 'no tier'}) from {start}: {verb}"
                f" — {entry['currency']}/Mtok input {entry['input_per_mtok']}, output "
                f"{entry['output_per_mtok']}, cache read "
                f"{entry.get('cache_read_per_mtok', 'unpriced')}; source {entry['source_url']} "
                f"(verified {entry['verified_on']})"
            )
            prices.add_price(
                provider=entry["provider"],
                model=entry["model"],
                tier=entry.get("tier", ""),
                currency=entry["currency"],
                input_per_mtok=entry["input_per_mtok"],
                output_per_mtok=entry["output_per_mtok"],
                cache_read_per_mtok=entry.get("cache_read_per_mtok"),
                cache_creation_per_mtok=entry.get("cache_creation_per_mtok"),
                effective_from=start,
                source_url=entry["source_url"],
                verified_on=dt.date.fromisoformat(entry["verified_on"]),
            )
        for rate in data.get("fx", []):
            day = dt.date.fromisoformat(rate["on_date"])
            exists = AiFxRate.objects.filter(currency=rate["currency"], on_date=day).exists()
            self.stdout.write(
                f"rate {rate['currency']} {day} = {rate['per_eur']} per EUR ({rate['source']}): "
                + ("already present" if exists else verb)
            )
            prices.add_rate(
                currency=rate["currency"],
                on_date=day,
                per_eur=rate["per_eur"],
                source=rate["source"],
            )
