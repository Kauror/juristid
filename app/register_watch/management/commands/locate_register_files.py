"""Look up the file links of waiting register documents (owner, 2026-11-23).

    manage.py locate_register_files [--limit 300]

The nightly `watch_registers` does this after each run; this is the same pass on
its own, for the first fill after a deploy. Reads the registers, writes only
`WatchFinding.files`.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from app.register_watch import register_files


class Command(BaseCommand):
    help = "Leiab ootel registridokumentide failide lingid."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--limit", type=int, default=register_files.REMEMBER_LIMIT)

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write(f"FILES located={register_files.remember(options['limit'])}")
