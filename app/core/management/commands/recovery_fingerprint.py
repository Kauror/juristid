"""What a restore has to reproduce, in a form two runs can be compared by.

`gzip -t` proves a file decompresses. It says nothing about whether the register
came back. This is the other end: a small JSON document describing the canonical
state — row counts per model, a rolled-up digest of every evidence object, the
digest of the OneNote page XML, the migration leaves and the PostgreSQL major —
which a restore can be measured against instead of eyeballed.

Three properties make it useful rather than decorative:

* **Canonical and rebuildable are counted separately.** A restored database is
  *supposed* to have an empty search projection until `rebuild_search_index`
  runs, so comparing those would fail every correct restore. Rebuildable counts
  are reported and never compared (docs/adr/0014).
* **Evidence is verified against its own recorded hash**, not merely counted.
  A DocumentVersion row whose bytes did not come back is exactly the failure a
  count cannot see.
* **It contains no content.** Filenames, titles and bytes stay where they are;
  what travels is digests, counts and schema state. A fingerprint is safe to
  keep beside a backup, which is the only reason it is worth writing one.

Read-only. It writes the file it is told to write and nothing else.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from app.core import deployment

#: Bumped when the meaning of a field changes, so an old fingerprint is refused
#: rather than silently compared against a new one that counts differently.
FINGERPRINT_VERSION = 1

#: Read in chunks: an evidence object may be a 200 MB scanned annex, and a
#: verification pass that loads one into memory per row is a verification pass
#: that gets switched off.
CHUNK_BYTES = 1024 * 1024


class Command(BaseCommand):
    help = "Fingerprint the canonical state, or compare it with an earlier fingerprint."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--out", help="Write the fingerprint to this path as JSON.")
        parser.add_argument(
            "--compare",
            help=(
                "An earlier fingerprint to compare against. Exits non-zero on any "
                "canonical difference."
            ),
        )
        parser.add_argument(
            "--evidence-sample",
            type=int,
            default=0,
            help=(
                "Verify the bytes of at most N evidence objects instead of all of "
                "them. 0 means all, which is what a restore rehearsal wants."
            ),
        )
        parser.add_argument(
            "--skip-evidence-bytes",
            action="store_true",
            help=(
                "Record what the database says about evidence without reading the "
                "objects. Faster, and proves less: use it only on a live system where "
                "the full pass is too slow to run often."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        fingerprint = self._fingerprint(
            sample=options["evidence_sample"],
            skip_bytes=options["skip_evidence_bytes"],
        )

        rendered = json.dumps(fingerprint, indent=2, sort_keys=True, ensure_ascii=False)
        if options["out"]:
            Path(options["out"]).write_text(rendered + "\n", encoding="utf-8")
            self.stdout.write(f"Fingerprint written to {options['out']}.")
        else:
            self.stdout.write(rendered)

        if options["compare"]:
            self._compare(Path(options["compare"]), fingerprint)

    # -- building ----------------------------------------------------------

    def _fingerprint(self, *, sample: int, skip_bytes: bool) -> dict[str, Any]:
        identity = deployment.runtime_identity()
        state = deployment.migration_state()
        major, minor = deployment.postgresql_version()

        canonical = deployment.canonical_model_labels()
        rebuildable = deployment.rebuildable_model_labels()

        evidence, mismatches = self._evidence(sample=sample, skip_bytes=skip_bytes)
        for mismatch in mismatches:
            self.stderr.write(self.style.ERROR(mismatch))
        if mismatches:
            raise CommandError(
                f"{len(mismatches)} evidence object(s) do not match the hash recorded "
                "for them. The database and the evidence tree are not from the same "
                "moment, or an object did not come back."
            )

        return {
            "fingerprint_version": FINGERPRINT_VERSION,
            "revision": identity.revision,
            "built_at": identity.built_at,
            "environment": identity.environment,
            "postgresql_major": major,
            "postgresql_minor": minor,
            "migration_leaves": list(state.leaves),
            "migrations_consistent": state.is_consistent,
            "canonical_counts": deployment.model_counts(canonical),
            "rebuildable_counts": deployment.model_counts(rebuildable),
            "evidence": evidence,
            "legacy_source": self._tree_digest(Path(settings.LEGACY_SOURCE_ROOT)),
        }

    def _evidence(self, *, sample: int, skip_bytes: bool) -> tuple[dict[str, Any], list[str]]:
        """Roll every evidence object into one comparable digest.

        The digest is over `storage_key sha256` lines in key order, so it changes
        if an object is missing, renamed or replaced, and does not change merely
        because rows were counted in a different order.
        """
        from app.documents.models import DocumentVersion
        from app.documents.services import evidence_storage

        storage = evidence_storage()
        rollup = hashlib.sha256()
        mismatches: list[str] = []
        total_bytes = 0
        count = 0
        verified = 0

        rows = DocumentVersion.objects.order_by("storage_key").values_list(
            "storage_key", "sha256", "size_bytes"
        )
        for storage_key, recorded, size_bytes in rows.iterator(chunk_size=500):
            count += 1
            total_bytes += size_bytes or 0
            rollup.update(f"{storage_key} {recorded}\n".encode())

            if skip_bytes or (sample and verified >= sample):
                continue

            actual = self._stored_digest(storage, storage_key)
            if actual is None:
                mismatches.append(f"missing evidence object for {storage_key}")
            elif actual != recorded:
                # Never the bytes and never the filename: an operator needs to
                # know which row, not what is in it.
                mismatches.append(f"hash mismatch for {storage_key}")
            verified += 1

        return (
            {
                "version_count": count,
                "total_bytes": total_bytes,
                "rollup_sha256": rollup.hexdigest(),
                "objects_verified": verified,
                "bytes_verified": not skip_bytes,
            },
            mismatches,
        )

    def _stored_digest(self, storage: Any, storage_key: str) -> str | None:
        try:
            handle = storage.open(storage_key, "rb")
        except (FileNotFoundError, OSError):
            return None
        digest = hashlib.sha256()
        with handle:
            while chunk := handle.read(CHUNK_BYTES):
                digest.update(chunk)
        return digest.hexdigest()

    def _tree_digest(self, root: Path) -> dict[str, Any]:
        """One digest for a directory of source evidence.

        Relative paths and content hashes only. The OneNote page XML is source
        evidence and must come back byte for byte; what it says is nobody's
        business here.
        """
        if not root.is_dir():
            return {"present": False, "file_count": 0, "total_bytes": 0, "rollup_sha256": ""}

        rollup = hashlib.sha256()
        file_count = 0
        total_bytes = 0
        for path in sorted(path for path in root.rglob("*") if path.is_file()):
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                while chunk := handle.read(CHUNK_BYTES):
                    digest.update(chunk)
            relative = path.relative_to(root).as_posix()
            rollup.update(f"{relative} {digest.hexdigest()}\n".encode())
            file_count += 1
            total_bytes += path.stat().st_size

        return {
            "present": True,
            "file_count": file_count,
            "total_bytes": total_bytes,
            "rollup_sha256": rollup.hexdigest(),
        }

    # -- comparing ---------------------------------------------------------

    #: Compared, because a restore has to reproduce them exactly.
    COMPARED = ("canonical_counts", "evidence", "legacy_source", "migration_leaves")

    def _compare(self, path: Path, current: dict[str, Any]) -> None:
        try:
            earlier = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise CommandError(f"Cannot read the fingerprint at {path}: {error}") from error

        if earlier.get("fingerprint_version") != FINGERPRINT_VERSION:
            raise CommandError(
                f"That fingerprint is version {earlier.get('fingerprint_version')!r} and "
                f"this build writes version {FINGERPRINT_VERSION}. They do not count the "
                "same things, so comparing them would be misleading."
            )

        differences: list[str] = []
        for field in self.COMPARED:
            before: Any = earlier.get(field)
            after: Any = current[field]
            if field == "evidence":
                # `objects_verified` and `bytes_verified` describe how hard this
                # run looked, not what it found.
                measured = ("version_count", "total_bytes", "rollup_sha256")
                before = {key: (before or {}).get(key) for key in measured}
                after = {key: after[key] for key in measured}
            if before != after:
                differences.extend(self._describe(field, before, after))

        if current["revision"] != earlier.get("revision"):
            # Not a failure. A restore onto a newer build is ordinary; saying so
            # keeps somebody from reading equal counts as "nothing changed".
            self.stdout.write(
                self.style.WARNING(
                    f"Fingerprints were taken by different builds: "
                    f"{earlier.get('revision')} then {current['revision']}."
                )
            )

        if differences:
            raise CommandError(
                "The canonical state does not match the earlier fingerprint:\n"
                + "\n".join(f"  - {line}" for line in differences)
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Canonical state matches the earlier fingerprint: "
                f"{sum(current['canonical_counts'].values())} rows across "
                f"{len(current['canonical_counts'])} models, "
                f"{current['evidence']['version_count']} evidence objects."
            )
        )

    def _describe(self, field: str, before: Any, after: Any) -> list[str]:
        if isinstance(before, dict) and isinstance(after, dict):
            lines = []
            for key in sorted(set(before) | set(after)):
                if before.get(key) != after.get(key):
                    lines.append(f"{field}.{key}: {before.get(key)!r} -> {after.get(key)!r}")
            return lines
        if isinstance(before, list) and isinstance(after, list):
            missing = sorted(set(before) - set(after))
            added = sorted(set(after) - set(before))
            lines = []
            if missing:
                lines.append(f"{field}: no longer present: {', '.join(missing)}")
            if added:
                lines.append(f"{field}: newly present: {', '.join(added)}")
            return lines
        return [f"{field}: {before!r} -> {after!r}"]
