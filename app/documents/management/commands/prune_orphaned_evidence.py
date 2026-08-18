"""Find stored evidence objects that no DocumentVersion refers to.

``add_evidence_version`` removes the object it wrote whenever the record
describing it does not survive. Two residual cases it cannot cover on its own:
the process dying between the storage write and the commit, and a caller's
outer transaction rolling back after the call returned successfully. Both leave
an orphaned object rather than a version row pointing at missing bytes, which is
the safe direction — and this command is how they are found and removed.

Reports by default; deletes only when asked.
"""

from __future__ import annotations

import posixpath
from typing import Any

from django.core.management.base import BaseCommand

from app.documents.models import DocumentVersion
from app.documents.services import evidence_storage


class Command(BaseCommand):
    help = "List (or delete) stored evidence objects that no DocumentVersion references."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Actually remove the orphaned objects. Without this the command only reports.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        storage = evidence_storage()
        known = set(DocumentVersion.objects.values_list("storage_key", flat=True))

        orphans = [key for key in self._walk(storage, "") if key not in known]

        if not orphans:
            self.stdout.write(self.style.SUCCESS("No orphaned evidence objects found."))
            return

        for key in orphans:
            self.stdout.write(key)

        if not options["delete"]:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(orphans)} orphaned object(s). Re-run with --delete to remove them."
                )
            )
            return

        for key in orphans:
            storage.delete(key)
        self.stdout.write(self.style.SUCCESS(f"Deleted {len(orphans)} orphaned object(s)."))

    def _walk(self, storage: Any, prefix: str) -> list[str]:
        try:
            directories, files = storage.listdir(prefix)
        except (FileNotFoundError, NotADirectoryError):
            return []

        keys = [posixpath.join(prefix, name) if prefix else name for name in files]
        for directory in directories:
            child = posixpath.join(prefix, directory) if prefix else directory
            keys.extend(self._walk(storage, child))
        return keys
