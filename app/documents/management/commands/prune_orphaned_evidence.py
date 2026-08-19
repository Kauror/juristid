"""Find stored evidence objects that no DocumentVersion refers to.

``add_evidence_version`` removes the object it wrote whenever the record
describing it does not survive. Two residual cases it cannot cover on its own:
the process dying between the storage write and the commit, and a caller's outer
transaction rolling back after the call returned successfully. Both leave an
orphaned object rather than a version row pointing at missing bytes, which is
the safe direction — and this command is how they are found and removed.

**Why a grace period.** Being unreferenced is not proof of being an orphan. The
bytes are written before the row, so an upload that is mid-transaction right now
looks exactly like an orphan from the storage backend's point of view. Deleting
it would destroy evidence a committing transaction is about to point at. An
object is therefore eligible only when its own storage timestamp proves it is
older than the grace period — long enough that no live transaction could still
be holding it.

If the backend cannot establish an object's age, it is reported and left alone.
Never deleting a live upload matters more than reclaiming a stray object.
"""

from __future__ import annotations

import logging
import posixpath
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from app.documents.models import DocumentVersion
from app.documents.services import evidence_storage

logger = logging.getLogger(__name__)

UNKNOWN_AGE = "age-unknown"
WITHIN_GRACE = "within-grace"
ELIGIBLE = "eligible"


@dataclass(frozen=True)
class Candidate:
    key: str
    verdict: str
    stored_at: datetime | None

    @property
    def detail(self) -> str:
        if self.verdict == UNKNOWN_AGE:
            return "age could not be established — not eligible"
        if self.stored_at is None:  # pragma: no cover - defensive
            return self.verdict
        return f"stored {self.stored_at.isoformat()}"


class Command(BaseCommand):
    help = (
        "List (or delete) stored evidence objects that no DocumentVersion references "
        "and that are older than the grace period."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Remove eligible objects. Without this the command only reports.",
        )
        parser.add_argument(
            "--grace-hours",
            type=float,
            default=None,
            help=(
                "Minimum age before an unreferenced object may be deleted. "
                f"Defaults to EVIDENCE_ORPHAN_GRACE_HOURS ({settings.EVIDENCE_ORPHAN_GRACE_HOURS})."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        grace_hours = options["grace_hours"]
        if grace_hours is None:
            grace_hours = settings.EVIDENCE_ORPHAN_GRACE_HOURS
        if grace_hours < 0:
            raise CommandError("--grace-hours cannot be negative.")

        cutoff = timezone.now() - timedelta(hours=grace_hours)
        storage = evidence_storage()
        referenced = set(DocumentVersion.objects.values_list("storage_key", flat=True))

        candidates = [
            self._classify(storage, key, cutoff)
            for key in self._walk(storage, "")
            if key not in referenced
        ]

        if not candidates:
            self.stdout.write(self.style.SUCCESS("No unreferenced evidence objects found."))
            return

        for candidate in sorted(candidates, key=lambda item: item.key):
            self.stdout.write(f"{candidate.verdict}\t{candidate.key}\t{candidate.detail}")

        eligible = [item for item in candidates if item.verdict == ELIGIBLE]
        protected = len(candidates) - len(eligible)

        if protected:
            self.stdout.write(
                self.style.WARNING(
                    f"{protected} unreferenced object(s) left alone: too recent, or age "
                    "unknown. An upload whose transaction has not committed yet looks "
                    "exactly like an orphan."
                )
            )

        if not eligible:
            return

        if not options["delete"]:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(eligible)} object(s) eligible. Re-run with --delete to remove them."
                )
            )
            return

        for candidate in eligible:
            storage.delete(candidate.key)
        self.stdout.write(self.style.SUCCESS(f"Deleted {len(eligible)} orphaned object(s)."))

    def _classify(self, storage: Any, key: str, cutoff: datetime) -> Candidate:
        stored_at = self._stored_at(storage, key)
        if stored_at is None:
            return Candidate(key=key, verdict=UNKNOWN_AGE, stored_at=None)
        if stored_at > cutoff:
            return Candidate(key=key, verdict=WITHIN_GRACE, stored_at=stored_at)
        return Candidate(key=key, verdict=ELIGIBLE, stored_at=stored_at)

    def _stored_at(self, storage: Any, key: str) -> datetime | None:
        """The object's own timestamp, or None if the backend cannot supply one.

        Modified time is asked for first. Evidence binaries are immutable, so it
        is the same instant as creation for any object this command can see, and
        it is the timestamp backends actually agree on: on Linux the filesystem
        backend derives "created" from the inode change time, and the Azure
        backend exposes last-modified rather than creation.

        Not every Storage implementation provides these, and a backend that
        raises, returns nothing, or hands back something that is not a datetime
        means the age is unproven. Unproven age is not a licence to delete.
        """
        for accessor in ("get_modified_time", "get_created_time"):
            method = getattr(storage, accessor, None)
            if method is None:
                continue
            try:
                value = method(key)
            except Exception:
                logger.info(
                    "Storage backend could not report %s for %s; treating age as unproven.",
                    accessor,
                    key,
                )
                continue
            if not isinstance(value, datetime):
                continue
            if timezone.is_naive(value):
                value = timezone.make_aware(value, timezone.get_default_timezone())
            return value
        return None

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
