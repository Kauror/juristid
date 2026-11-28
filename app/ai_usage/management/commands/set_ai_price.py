"""Enter one verified list price or exchange rate (r98). Never invented: the owner supplies
each figure and the page it was read from. A new price is a new revision (the previous one is
closed the day before, so earlier estimates keep theirs); invalid, overlapping or rewriting
entries are refused by `app.ai_usage.prices`, in one transaction."""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from app.ai_usage import prices


class Command(BaseCommand):
    help = "Add a model list price (per million tokens) or an exchange rate."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--model")
        parser.add_argument("--currency", default="USD")
        parser.add_argument("--tier", default="")
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
        try:
            if options["fx_per_eur"]:
                for needed in ("fx_date", "fx_source"):
                    if not options[needed]:
                        raise CommandError(f"--{needed.replace('_', '-')} is required")
                _, created = prices.add_rate(
                    currency=options["currency"],
                    on_date=dt.date.fromisoformat(options["fx_date"]),
                    per_eur=options["fx_per_eur"],
                    source=options["fx_source"],
                )
                self.stdout.write("rate saved" if created else "rate already present")
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
            prices.add_price(
                model=options["model"],
                tier=options["tier"],
                currency=options["currency"],
                input_per_mtok=options["input_price"],
                output_per_mtok=options["output_price"],
                cache_read_per_mtok=options["cache_read"],
                cache_creation_per_mtok=options["cache_creation"],
                effective_from=dt.date.fromisoformat(options["effective_from"]),
                source_url=options["source_url"],
                verified_on=dt.date.fromisoformat(options["verified_on"]),
            )
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error
        self.stdout.write("price saved")
