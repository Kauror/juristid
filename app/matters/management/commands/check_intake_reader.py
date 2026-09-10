"""Is the `Uus teema` intake reader's loop still turning?

The container healthcheck for the reader service. A command rather than a
`python -c` in the Compose file, for three reasons: the rule lives in one place
instead of being copied into every deployment's YAML, it is covered by the test
suite, and when it fails it can say *why* — which is the line an operator reads
out of `docker inspect`.

A worker has no port, so the image's HTTP healthcheck cannot describe it. This
reads the mark the loop leaves each time round, which says something the
process table cannot: not "the process exists" but "the loop is turning". A
parser wedged on a malformed file leaves the process alive and the queue
stopped, and that is the failure this catches — the one that made a lawyer
watch «Loen faili…» for half an hour.

Its window is `INTAKE_READER_STALE_CLAIM_MINUTES`, five minutes rather than the
corpus worker's thirty, because a staged file is bounded by
`MAX_INTAKE_UPLOAD_BYTES` and finishes in seconds. Waiting half an hour to
learn that this loop had stopped would outlast the form it stopped in front of
(app/documents/extraction/heartbeat.py, docs/adr/0069).

Exit 0 means the loop turned recently. Exit 1 means it did not, and the
container is correctly marked unhealthy.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Exit non-zero unless the Uus teema intake reader's loop turned recently."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Say nothing on success. The healthcheck does not read stdout.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from app.documents.extraction.heartbeat import INTAKE_READER as mark

        age = mark.age_seconds()
        limit = mark.threshold_seconds()

        if age is None:
            raise SystemExit(self._fail(f"The reader has left no heartbeat at {mark.path()}."))
        if age >= limit:
            raise SystemExit(
                self._fail(
                    f"The reader's loop last turned {int(age)}s ago, "
                    f"which is past the {limit}s limit."
                )
            )

        if not options["quiet"]:
            self.stdout.write(f"Lugeja on elus ({int(age)}s tagasi).")

    def _fail(self, message: str) -> int:
        self.stderr.write(message)
        return 1
