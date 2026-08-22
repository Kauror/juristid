"""Copying archived letters into evidence storage, so the application holds them.

Materialising is deliberately a separate act from reconciling, and the split is
the point of this module.

*Reconciling* answers "whose letter is this, and did Koda send it on that day",
which is a judgement with a high bar and, for two thirds of the corpus, no
defensible answer yet. *Materialising* answers "do we have the bytes", which is
not a judgement at all. Tying them together is what left 523 files visible only
as catalogue rows: the application knew they existed and could not open one,
because holding a file had been made conditional on knowing whose it was.

So this creates no Submission, links no Matter and decides nothing. It reads the
pinned archive, writes each distinct byte sequence into the evidence store once,
and points the occurrences at it.

**Pinned, because bytes are the claim.** The operator names the archive SHA-256
they reviewed, and a different archive is refused rather than reconciled against.
There is no closest-file fallback, no filename match and no timestamp: those
answer "which file is roughly this one", and the whole value of an evidence store
is that it never answers roughly.

**Idempotent, because it will be re-run.** A second pass over the same archive
reuses the binary it finds by SHA-256, notices occurrences that already point at
it, and writes nothing. A third is a no-op with the same report.

**A row without its bytes is a failure, not a completion.** If the database says
a binary exists and the store cannot produce the object, this reports an
integrity failure and leaves the row alone. Silently treating that as "already
done" would let a restore that lost its evidence tree look like a healthy
archive.

The reverse — an object in the store with no row, because a transaction rolled
back after the write — stays the safe direction and is what
`prune_orphaned_evidence` is for.
"""

from __future__ import annotations

import hashlib
import posixpath
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from app.documents.services import evidence_storage
from app.legacy_import.opinion_archive import OpinionArchiveItem
from app.legacy_import.opinion_binary import OpinionArchiveBinary
from app.legacy_import.opinion_sources import ArchiveReader, file_sha256

#: Where archive binaries live inside the evidence store.
#:
#: A namespace of its own so an operator reading a storage listing can tell
#: archive evidence from Matter evidence without a database, and so the two can
#: never collide on a key.
ARCHIVE_PREFIX = "opinion-archive"

#: PDFs are what the corpus is. The type is recorded rather than trusted: it
#: comes from the catalogue's own sniffing, and a wrong one must not stop the
#: bytes being held.
DEFAULT_MIME_TYPE = "application/pdf"


class OpinionMaterializeError(RuntimeError):
    """The archive is not the one the plan was reviewed against, or is unreadable."""


def storage_key_for(sha256: str) -> str:
    """A key derived from the content, never from the archive's own path.

    The archive contains names that were mojibake before they were zipped, names
    with quotes, and paths that would escape a directory if joined naively. None
    of that is a storage layout. The SHA-256 is unique per binary by definition,
    which is also what makes the key stable across re-runs, and the two-level
    fan-out keeps any one directory small.
    """
    return posixpath.join(ARCHIVE_PREFIX, sha256[:2], sha256[2:4], sha256)


@dataclass
class MaterializeReport:
    """Aggregates only.

    No filename reaches this object. The archive is real Koda correspondence and
    this report is printed to a terminal, written into CI logs and pasted into
    tickets; a count says what an operator needs and a list of letter titles says
    considerably more than that (brief 48, 81).
    """

    archive_sha256: str = ""
    occurrences: int = 0
    distinct_binaries: int = 0

    binaries_created: int = 0
    binaries_reused: int = 0
    occurrences_linked: int = 0
    occurrences_already_linked: int = 0

    bytes_written: int = 0
    missing_from_archive: int = 0
    hash_mismatch: int = 0
    missing_stored_object: int = 0

    findings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing_from_archive or self.hash_mismatch or self.missing_stored_object)

    def as_text(self) -> str:
        rows = [
            ("arhiivi SHA-256", self.archive_sha256[:16] + "…"),
            ("esinemisi", self.occurrences),
            ("erinevaid baite", self.distinct_binaries),
            ("uusi baite salvestatud", self.binaries_created),
            ("olemasolevaid baite kasutatud", self.binaries_reused),
            ("esinemisi seotud", self.occurrences_linked),
            ("juba seotud", self.occurrences_already_linked),
            ("kirjutatud baite", f"{self.bytes_written:,}"),
            ("arhiivist puudu", self.missing_from_archive),
            ("räsi ei klapi", self.hash_mismatch),
            ("salvestusest puudu", self.missing_stored_object),
        ]
        lines = [f"  {label:<32} {value:>12}" for label, value in rows]
        lines.extend(f"  leid: {finding}" for finding in self.findings)
        return "\n".join(lines)


@dataclass(frozen=True)
class _Occurrence:
    """What materialisation needs to know about one catalogued file."""

    item_id: Any
    relative_path: str
    sha256: str
    size_bytes: int
    detected_type: str
    already_linked: bool


def _catalogued(archive_sha256: str) -> list[_Occurrence]:
    rows = (
        OpinionArchiveItem.objects.filter(archive_sha256=archive_sha256)
        .order_by("pk")
        .values_list(
            "pk", "archive_relative_path", "sha256", "size_bytes", "detected_type", "binary_id"
        )
    )
    return [
        _Occurrence(
            item_id=pk,
            relative_path=path,
            sha256=sha,
            size_bytes=size,
            detected_type=detected,
            already_linked=binary_id is not None,
        )
        for pk, path, sha, size, detected, binary_id in rows
    ]


def require_archive(archive_path: Path, expected_sha256: str) -> str:
    """Refuse anything but the archive the operator says they reviewed.

    A ZIP hashes as a file. A directory hashes as its manifest, the same way the
    catalogue computed it, so the two forms of the same snapshot agree.
    """
    from app.legacy_import.opinion_sources import read_opinion_archive

    if not archive_path.exists():
        raise OpinionMaterializeError(f"Arhiivi ei leitud: {archive_path}")
    actual = (
        file_sha256(archive_path)
        if archive_path.is_file()
        else read_opinion_archive(archive_path)[0]
    )
    expected = (expected_sha256 or "").strip().lower()
    if not expected:
        raise OpinionMaterializeError(
            "Materialiseerimine nõuab --expect-archive-sha256: baidid on väide, "
            "ja väidet ei kinnitata ligikaudse failiga."
        )
    if actual != expected:
        raise OpinionMaterializeError(
            f"Arhiiv ei ole see, mille kohta plaan tehti. Oodati {expected[:16]}…, "
            f"leiti {actual[:16]}…"
        )
    return actual


def plan_materialization(*, archive_path: Path, expected_archive_sha256: str) -> MaterializeReport:
    """What a materialise would do, writing nothing.

    Reads the archive and the catalogue and reports the arithmetic. It also
    reports what is already wrong — an occurrence whose bytes are gone from the
    snapshot, or a binary whose stored object has disappeared — because those
    are the two answers an operator needs *before* deciding to run the real
    thing, not after.
    """
    archive_sha256 = require_archive(archive_path, expected_archive_sha256)
    report = MaterializeReport(archive_sha256=archive_sha256)
    occurrences = _catalogued(archive_sha256)
    if not occurrences:
        report.findings.append(
            "Kataloogis ei ole selle arhiivi kirjeid — jooksuta enne `opinion_archive apply`."
        )
        return report

    report.occurrences = len(occurrences)
    wanted = {occurrence.sha256 for occurrence in occurrences}
    report.distinct_binaries = len(wanted)

    existing = dict(
        OpinionArchiveBinary.objects.filter(sha256__in=wanted).values_list("sha256", "storage_key")
    )
    storage = evidence_storage()
    for sha, key in existing.items():
        if not storage.exists(key):
            report.missing_stored_object += 1
            report.findings.append(f"baidil {sha[:12]}… on rida, aga salvestuses objekti ei ole")

    report.binaries_reused = len(existing)
    report.binaries_created = len(wanted - set(existing))
    report.occurrences_already_linked = sum(1 for o in occurrences if o.already_linked)
    report.occurrences_linked = report.occurrences - report.occurrences_already_linked

    reader = ArchiveReader(archive_path)
    for sha in sorted(wanted - set(existing)):
        occurrence = next(o for o in occurrences if o.sha256 == sha)
        data = reader.read(occurrence.relative_path)
        if data is None:
            report.missing_from_archive += 1
            report.findings.append(f"arhiivist ei leitud baiti {sha[:12]}…")
            continue
        if hashlib.sha256(data).hexdigest() != sha:
            report.hash_mismatch += 1
            report.findings.append(f"arhiivi bait {sha[:12]}… ei vasta kataloogitud räsile")
            continue
        report.bytes_written += len(data)
    return report


def materialize(*, archive_path: Path, expected_archive_sha256: str) -> MaterializeReport:
    """Copy the archive's distinct byte sequences into the evidence store.

    Creates no Submission and links no Matter. What it produces is the ability
    to open the file.
    """
    archive_sha256 = require_archive(archive_path, expected_archive_sha256)
    report = MaterializeReport(archive_sha256=archive_sha256)
    occurrences = _catalogued(archive_sha256)
    if not occurrences:
        report.findings.append("Kataloogis ei ole selle arhiivi kirjeid.")
        return report

    report.occurrences = len(occurrences)
    by_sha: dict[str, list[_Occurrence]] = {}
    for occurrence in occurrences:
        by_sha.setdefault(occurrence.sha256, []).append(occurrence)
    report.distinct_binaries = len(by_sha)

    reader = ArchiveReader(archive_path)
    storage = evidence_storage()
    for sha, group in sorted(by_sha.items()):
        binary = _binary_for(
            sha=sha,
            group=group,
            reader=reader,
            storage=storage,
            archive_sha256=archive_sha256,
            report=report,
        )
        if binary is None:
            continue
        _link_occurrences(group, binary, report)
    return report


def _binary_for(
    *,
    sha: str,
    group: list[_Occurrence],
    reader: ArchiveReader,
    storage: Any,
    archive_sha256: str,
    report: MaterializeReport,
) -> OpinionArchiveBinary | None:
    """The stored binary for these bytes, creating it if this is the first time."""
    existing = OpinionArchiveBinary.objects.filter(sha256=sha).first()
    if existing is not None:
        if not storage.exists(existing.storage_key):
            # Not "already done". A row pointing at bytes the store cannot
            # produce is the one failure this operation must never paper over,
            # because the operator's next move depends on knowing about it.
            report.missing_stored_object += 1
            report.findings.append(f"baidil {sha[:12]}… on rida, aga salvestuses objekti ei ole")
            return None
        report.binaries_reused += 1
        return existing

    data = reader.read(group[0].relative_path)
    if data is None:
        report.missing_from_archive += 1
        report.findings.append(f"arhiivist ei leitud baiti {sha[:12]}…")
        return None
    if hashlib.sha256(data).hexdigest() != sha:
        report.hash_mismatch += 1
        report.findings.append(f"arhiivi bait {sha[:12]}… ei vasta kataloogitud räsile")
        return None

    key = storage_key_for(sha)
    if not storage.exists(key):
        # Bytes before the row, deliberately. The reverse order can leave a row
        # pointing at nothing, which is unrecoverable; this order can leave an
        # object nothing points at, which the pruner reclaims.
        storage.save(key, ContentFile(data))
    report.bytes_written += len(data)

    binary = OpinionArchiveBinary.objects.create(
        sha256=sha,
        size_bytes=len(data),
        mime_type=group[0].detected_type or DEFAULT_MIME_TYPE,
        storage_key=key,
        source_archive_sha256=archive_sha256,
        materialized_at=timezone.now(),
    )
    report.binaries_created += 1
    return binary


@transaction.atomic
def _link_occurrences(
    group: Iterable[_Occurrence], binary: OpinionArchiveBinary, report: MaterializeReport
) -> None:
    """Point every occurrence of these bytes at the one stored binary."""
    for occurrence in group:
        if occurrence.already_linked:
            report.occurrences_already_linked += 1
            continue
        updated = OpinionArchiveItem.objects.filter(
            pk=occurrence.item_id, binary__isnull=True
        ).update(binary=binary)
        if updated:
            report.occurrences_linked += 1
        else:
            report.occurrences_already_linked += 1
