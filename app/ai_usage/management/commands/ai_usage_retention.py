"""Retention DRY RUN for the AI ledger (r98, docs/adr/0236 §7). It deletes nothing.

Reports what a 13-month detail retention would aggregate and remove. Actual deletion is a
separate owner decision; this command has no apply mode on purpose, so the day that decision
is made the procedure is: run this, review the per-month aggregates it prints, store them,
and only then add a reviewed deletion step. Prices and exchange rates are never touched.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.core.management.base import BaseCommand
from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from app.ai_usage.models import AiAttempt, AiOperation

MONTHS = 13


class Command(BaseCommand):
    help = "Dry run: what 13-month retention of detailed AI usage would affect. Deletes nothing."

    def handle(self, *args: Any, **options: Any) -> None:
        cutoff = timezone.now() - dt.timedelta(days=MONTHS * 30)
        old = AiAttempt.objects.filter(started_at__lt=cutoff)
        self.stdout.write(f"cutoff {cutoff:%Y-%m-%d}: {old.count()} attempts, ")
        self.stdout.write(
            f"{AiOperation.objects.filter(started_at__lt=cutoff).count()} operations older than it"
        )
        for row in (
            old.annotate(month=TruncMonth("started_at"))
            .values("month")
            .annotate(n=Count("pk"), tin=Sum("input_tokens"), tout=Sum("output_tokens"))
            .order_by("month")
        ):
            self.stdout.write(
                f"  {row['month']:%Y-%m}: {row['n']} attempts, {row['tin']} in, {row['tout']} out"
            )
        self.stdout.write("dry run only: nothing deleted, nothing scheduled")
