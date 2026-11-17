"""Load the reviewed sender and Valdkond proposals for the Teemad (ADR 0215).

    manage.py load_classification_suggestions FILE [--dry-run]

Idempotent: a Teema that already has a proposal keeps it. It writes proposals
only; no Teema field changes until a person confirms one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from app.core.errors import DomainError
from app.reporting import classification


class Command(BaseCommand):
    help = "Laadib teemade saatja ja valdkonna ettepanekud ülevaatatud failist."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("path")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["path"])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as problem:
            raise CommandError(f"cannot read {path}: {problem}") from problem
        try:
            report = classification.load(data, dry_run=options["dry_run"])
        except DomainError as refusal:
            raise CommandError(str(refusal)) from refusal
        mode = "DRY-RUN" if options["dry_run"] else "APPLIED"
        self.stdout.write(
            f"{mode} created={report.created} already={report.already} "
            f"missing_matter={report.missing_matter} "
            f"unknown_sender={list(report.unknown_sender)} unknown_area={list(report.unknown_area)}"
        )
