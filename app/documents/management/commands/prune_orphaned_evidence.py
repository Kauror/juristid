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

Two further rules follow from the same priority, and both concern what this
command is allowed to *claim*.

**A listing that failed is not an empty store.** Walking the evidence prefix is
how the set of unreferenced objects is established, so a directory that could
not be read means the answer is unknown for everything under it. Reporting "no
unreferenced evidence objects found" in that situation is the single most
misleading thing this command could say — it is the sentence an operator reads
as "the evidence store is healthy". Unreadable prefixes are named and the
command exits non-zero.

**A delete that failed was not a prune.** Counting an attempted deletion as a
completed one leaves an operator believing the store was reclaimed when it was
not, and the next run has to rediscover it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from app.documents.integrity import walk_storage
from app.documents.models import DocumentVersion
from app.documents.services import evidence_storage

logger = logging.getLogger(__name__)

UNKNOWN_AGE = "age-unknown"
WITHIN_GRACE = "within-grace"
ELIGIBLE = "eligible"

#: The shortest grace period this command will delete under. The grace period is
#: not a tidiness preference, it is the only thing standing between this command
#: and the bytes of a transaction that has not committed yet — so a zero passed
#: on the command line silently reinstates exactly the race the design exists to
#: prevent. Reporting is unaffected: `--grace-hours 0` with no `--delete` is a
#: perfectly reasonable way to see everything unreferenced right now.
MINIMUM_DELETE_GRACE_HOURS = 1.0


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
        if options["delete"] and grace_hours < MINIMUM_DELETE_GRACE_HOURS:
            raise CommandError(
                f"--delete requires a grace period of at least {MINIMUM_DELETE_GRACE_HOURS} "
                f"hour(s); {grace_hours} was given. Evidence bytes are written before the "
                "row that describes them, so a shorter window makes this command capable of "
                "deleting an upload that is still committing. Run without --delete to see "
                "what a shorter window would select."
            )

        cutoff = timezone.now() - timedelta(hours=grace_hours)
        storage = evidence_storage()
        referenced = set(DocumentVersion.objects.values_list("storage_key", flat=True))

        keys, unreadable = walk_storage(storage)
        candidates = [self._classify(storage, key, cutoff) for key in keys if key not in referenced]

        for prefix, reason in unreadable:
            self.stderr.write(f"unreadable\t{prefix or '(root)'}\t{reason}")

        if not candidates:
            if unreadable:
                # Deliberately not the success line. Everything under an
                # unreadable prefix is unknown, and "none found" would be read
                # as "none exist".
                raise CommandError(
                    f"{len(unreadable)} prefix(es) could not be listed, so the evidence "
                    "store was not fully examined. No conclusion is available."
                )
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

        failures = 0
        if eligible and options["delete"]:
            failures = self._delete(storage, eligible)
        elif eligible:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(eligible)} object(s) eligible. Re-run with --delete to remove them."
                )
            )

        if unreadable:
            raise CommandError(
                f"{len(unreadable)} prefix(es) could not be listed; the report above covers "
                "only what could be read."
            )
        if failures:
            raise CommandError(f"{failures} object(s) could not be deleted and remain in place.")

    def _delete(self, storage: Any, eligible: list[Candidate]) -> int:
        """Remove what was selected, and count only what actually went.

        Each deletion stands alone. One object the backend refuses must not
        abandon the rest of the run, and it must not be counted among the
        deleted either — an operator reading "Deleted 40" and finding 39 still
        there has been told something false about the store they are
        responsible for.
        """
        deleted = 0
        failures = 0
        for candidate in eligible:
            try:
                storage.delete(candidate.key)
            except Exception as error:
                failures += 1
                logger.warning("Could not delete orphaned evidence object %s", candidate.key)
                self.stderr.write(f"delete-failed\t{candidate.key}\t{type(error).__name__}")
                continue
            deleted += 1
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} orphaned object(s)."))
        return failures

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
