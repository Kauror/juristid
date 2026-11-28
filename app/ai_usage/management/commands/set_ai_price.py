"""Enter one verified list price (r98). Never invented: the owner supplies each figure
and the page it was read from. A new price is a new row; an earlier one is closed the
day before, so earlier estimates keep their revision."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from app.ai_usage.models import AiFxRate, AiPrice


class Command(BaseCommand):
    help = "Add a model list price (per million tokens) or an exchange rate."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--model")
        parser.add_argument("--currency", default="USD")
        parser.add_argument("--input", dest="input_price")
        parser.add_argument("--output", dest="output_price")
        parser.add_argument("--cache-read", dest="cache_read")
        parser.add_argument("--cache-creation", dest="cache_creation")
        parser.add_argument("--effective-from")
        parser.add_argument("--source-url")
        parser.add_argument("--verified-on")
        parser.add_argument("--fx-per-eur", help="record units of --currency per 1 EUR")
        parser.add_argument("--fx-date")
        parser.add_argument("--fx-source")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["fx_per_eur"]:
            for needed in ("fx_date", "fx_source"):
                if not options[needed]:
                    raise CommandError(f"--{needed.replace('_', '-')} is required")
            AiFxRate.objects.update_or_create(
                currency=options["currency"],
                on_date=dt.date.fromisoformat(options["fx_date"]),
                defaults={
                    "per_eur": Decimal(options["fx_per_eur"]),
                    "source": options["fx_source"],
                },
            )
            self.stdout.write("rate saved")
            return
        for needed in (
            "model",
            "input_price",
            "output_price",
            "effective_from",
            "source_url",
            "verified_on",
        ):
            if not options[needed]:
                raise CommandError(f"--{needed.replace('_', '-')} is required")
        start = dt.date.fromisoformat(options["effective_from"])
        AiPrice.objects.filter(
            model=options["model"], effective_to__isnull=True, effective_from__lt=start
        ).update(effective_to=start - dt.timedelta(days=1))
        AiPrice.objects.create(
            model=options["model"],
            currency=options["currency"],
            input_per_mtok=Decimal(options["input_price"]),
            output_per_mtok=Decimal(options["output_price"]),
            cache_read_per_mtok=Decimal(options["cache_read"]) if options["cache_read"] else None,
            cache_creation_per_mtok=(
                Decimal(options["cache_creation"]) if options["cache_creation"] else None
            ),
            effective_from=start,
            source_url=options["source_url"],
            verified_on=dt.date.fromisoformat(options["verified_on"]),
        )
        self.stdout.write("price saved")
