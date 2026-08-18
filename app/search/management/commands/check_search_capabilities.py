"""Fail loudly if the database cannot support Estonian search."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from app.search.capabilities import build_report


class Command(BaseCommand):
    help = "Verify PostgreSQL version, extensions and the Estonian text-search configuration."

    def handle(self, *args: Any, **options: Any) -> None:
        report = build_report()

        major, minor = report.postgresql_version
        self.stdout.write(f"PostgreSQL {major}.{minor} (required: 18+)")
        self.stdout.write(
            "Extensions: "
            + (
                "all present"
                if not report.missing_extensions
                else f"missing {report.missing_extensions}"
            )
        )
        self.stdout.write(
            "Estonian text-search configuration: "
            + ("present" if report.has_estonian_configuration else "MISSING")
        )
        if report.estonian_lexemes:
            self.stdout.write(f"Sample lexemes: {', '.join(report.estonian_lexemes)}")

        if not report.ok:
            raise CommandError("Required search capabilities are not available.")

        self.stdout.write(self.style.SUCCESS("Search capabilities are available."))
