"""Is the extraction worker's loop still turning?

The container healthcheck for the worker service. A command rather than a
`python -c` in the Compose file, for three reasons: the rule lives in one place
instead of being copied into every deployment's YAML, it is covered by the test
suite, and when it fails it can say *why* — which is the line an operator reads
out of `docker inspect`.

Exit 0 means the loop turned recently. Exit 1 means it did not, and the
container is correctly marked unhealthy (app/documents/extraction/heartbeat.py).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Exit non-zero unless the extraction worker's loop turned recently."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Say nothing on success. The healthcheck does not read stdout.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from app.documents.extraction import heartbeat

        age = heartbeat.age_seconds()
        limit = heartbeat.threshold_seconds()

        if age is None:
            raise SystemExit(self._fail(f"The worker has left no heartbeat at {heartbeat.path()}."))
        if age >= limit:
            raise SystemExit(
                self._fail(
                    f"The worker's loop last turned {int(age)}s ago, "
                    f"which is past the {limit}s limit."
                )
            )

        if not options["quiet"]:
            self.stdout.write(f"Töötaja on elus ({int(age)}s tagasi).")

    def _fail(self, message: str) -> int:
        self.stderr.write(message)
        return 1
