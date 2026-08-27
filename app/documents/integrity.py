"""Does the evidence the database describes actually exist, and is it that?

The database constrains what it can. A version number is unique per document, a
storage key is unique outright, a checksum is lowercase hex, a size is not
negative — all of that is enforced in PostgreSQL and none of it is re-checked
here, because a guarantee asserted twice is a guarantee somebody will later
weaken in one of the two places.

What PostgreSQL cannot see is the other half of the record. Evidence bytes live
outside it, and the one failure this whole subsystem is arranged to prevent — a
committed row pointing at an object that is not there — is by construction
invisible to every constraint in the schema. `add_evidence_version` writes the
bytes before the row so that the failure direction is the recoverable one, and
`prune_orphaned_evidence` reclaims what that leaves behind. Neither can tell an
operator whether it worked.

That is what this module is for: the questions an operator has to be able to
answer without opening a database session, and which the schema cannot answer
for them.

* Is there a version row whose object is gone?
* Is there an object whose size disagrees with the row?
* Is there an object whose bytes disagree with the recorded checksum?
* Is there a stored object nothing refers to?
* Does a document's current version belong to a different document?
* Does a submission's final evidence still satisfy the rule it was accepted
  under — the right Matter, and no less restricted than the submission?
* Is a version numbering sequence missing an entry?
* Is anything stuck mid-extraction?

Everything here reads. Nothing repairs. A checker that fixes what it finds is a
checker nobody can run to find out what is wrong, and evidence is the last place
in this system where an automatic repair should be allowed to guess.

**Two depths, because they cost differently.** The structural checks are a
handful of queries plus one `exists`/`size` call per version, and are cheap
enough to run routinely against tens of thousands of rows. Verifying checksums
reads every stored byte, which on a multi-gigabyte store is a maintenance window
rather than a health check — so it is asked for explicitly and never implied.
"""

from __future__ import annotations

import hashlib
import posixpath
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db.models import Count, Max
from django.utils import timezone

from app.core.enums import Visibility, most_restrictive
from app.documents.enums import ExtractionState
from app.documents.models import Document, DocumentVersion
from app.documents.references import referenced_storage_keys
from app.documents.services import evidence_storage

#: Bytes per read when verifying a checksum. Large enough that the syscall
#: overhead disappears, small enough that a 2 GB video never becomes 2 GB of
#: resident memory in a worker that also has a parser loaded.
HASH_CHUNK_BYTES = 1024 * 1024

# Finding classes. Strings rather than an enum because they are an operator
# interface — they are grepped out of cron mail — and the stability that matters
# is the stability of the words.
MISSING_OBJECT = "missing-object"
SIZE_MISMATCH = "size-mismatch"
SHA_MISMATCH = "sha-mismatch"
UNREADABLE_OBJECT = "unreadable-object"
ORPHAN_OBJECT = "orphan-object"
UNREADABLE_PREFIX = "unreadable-prefix"
FOREIGN_CURRENT_VERSION = "foreign-current-version"
FOREIGN_FINAL_EVIDENCE = "foreign-final-evidence"
EVIDENCE_LESS_RESTRICTED = "evidence-less-restricted"
VERSION_NUMBER_GAP = "version-number-gap"
STUCK_PROCESSING = "stuck-processing"

#: Which classes mean evidence is not intact, as opposed to something an
#: operator should look at. The distinction drives nothing in this module; it is
#: here so the command and any future caller agree on it.
INTEGRITY_FAILURES: frozenset[str] = frozenset(
    {
        MISSING_OBJECT,
        SIZE_MISMATCH,
        SHA_MISMATCH,
        UNREADABLE_OBJECT,
        FOREIGN_CURRENT_VERSION,
        FOREIGN_FINAL_EVIDENCE,
        EVIDENCE_LESS_RESTRICTED,
    }
)

#: Which of those are about *bytes*, and are therefore the ones a restore
#: answers. Everything else in `INTEGRITY_FAILURES` is about a relationship
#: between rows: the bytes are present and are what was hashed, so a backup
#: holds the same relationship the live database does and a restore repairs
#: nothing. What to change instead — restrict the document, relax the
#: submission, supersede the evidence, refile it — is a decision about the
#: record of what Koda sent, and it is a person's to take (docs/adr/0011).
#:
#: The listed side is the storage one deliberately, because "restore from
#: backup" is the sentence that costs something when it is wrong. A finding
#: class added later and left out of this set is described to an operator as a
#: question rather than as a restore, which is the harmless direction to be
#: wrong in — the same reason `restrictiveness` ranks a value it does not
#: recognise as the most restrictive one.
STORAGE_FAILURES: frozenset[str] = frozenset(
    {MISSING_OBJECT, SIZE_MISMATCH, SHA_MISMATCH, UNREADABLE_OBJECT}
)


@dataclass(frozen=True)
class Finding:
    """One thing that is wrong, named safely.

    ``subject`` is a UUID or a storage key and never a document title or a
    filename. Both of those are routinely the confidential part of a legal
    record, and this output goes to terminals, cron mail and CI logs.
    """

    kind: str
    subject: str
    detail: str = ""


@dataclass
class IntegrityReport:
    versions_checked: int = 0
    bytes_hashed: int = 0
    objects_seen: int = 0
    sha_verified: bool = False
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def by_kind(self) -> dict[str, list[Finding]]:
        grouped: dict[str, list[Finding]] = defaultdict(list)
        for finding in self.findings:
            grouped[finding.kind].append(finding)
        return dict(grouped)


def walk_storage(storage: Any, prefix: str = "") -> tuple[list[str], list[tuple[str, str]]]:
    """Every key under ``prefix``, and every prefix that could not be listed.

    Shared with `prune_orphaned_evidence`, which must not conclude "no orphans"
    from a listing that failed any more than this must conclude "no missing
    objects" from one. A missing or non-directory path is not a failure: an
    installation that has never stored evidence has no directory, and that is a
    complete answer rather than an unknown one.
    """
    try:
        directories, files = storage.listdir(prefix)
    except (FileNotFoundError, NotADirectoryError):
        return [], []
    except Exception as error:
        return [], [(prefix, type(error).__name__)]

    keys = [posixpath.join(prefix, name) if prefix else name for name in files]
    unreadable: list[tuple[str, str]] = []
    for directory in directories:
        child = posixpath.join(prefix, directory) if prefix else directory
        child_keys, child_unreadable = walk_storage(storage, child)
        keys.extend(child_keys)
        unreadable.extend(child_unreadable)
    return keys, unreadable


def check_evidence(*, verify_sha: bool = False, scan_storage: bool = True) -> IntegrityReport:
    """Examine the evidence store against the rows that describe it.

    Read-only. ``verify_sha`` reads every stored object end to end and is the
    difference between "the object is there and the right size" and "the object
    is the one we hashed".

    ``scan_storage`` walks the store to find objects nothing refers to. It is
    the expensive half on a backend where listing costs a request per prefix,
    and it is separable for that reason — a run that only wants to know whether
    any row has lost its bytes does not need it.
    """
    report = IntegrityReport(sha_verified=verify_sha)
    storage = evidence_storage()

    _check_versions(storage, report, verify_sha=verify_sha)
    _check_current_versions(report)
    _check_final_evidence(report)
    _check_version_numbering(report)
    _check_stuck_extractions(report)
    if scan_storage:
        _check_orphans(storage, report)
    return report


def _check_versions(storage: Any, report: IntegrityReport, *, verify_sha: bool) -> None:
    """Every version row, against the object it claims.

    Iterated in chunks and with only the four columns this needs, because the
    corpus this is written for is tens of thousands of rows and the ones it
    would otherwise drag in — provenance text, source paths — are the wide ones.
    """
    rows = DocumentVersion.objects.order_by("pk").values_list(
        "pk", "storage_key", "size_bytes", "sha256"
    )
    for version_id, key, size_bytes, sha256 in rows.iterator(chunk_size=500):
        report.versions_checked += 1
        try:
            if not storage.exists(key):
                report.findings.append(
                    Finding(MISSING_OBJECT, str(version_id), f"storage_key={key}")
                )
                continue
        except Exception as error:
            report.findings.append(
                Finding(
                    UNREADABLE_OBJECT, str(version_id), f"exists() raised {type(error).__name__}"
                )
            )
            continue

        try:
            stored_size = storage.size(key)
        except Exception:
            stored_size = None
        if stored_size is not None and stored_size != size_bytes:
            report.findings.append(
                Finding(
                    SIZE_MISMATCH,
                    str(version_id),
                    f"row says {size_bytes} bytes, store holds {stored_size}",
                )
            )
            # Deliberately no `continue`. A size that disagrees is already
            # enough to know the bytes are not the recorded ones, but a run that
            # asked for checksums asked for them on everything.

        if not verify_sha:
            continue

        digest, read = _digest_of(storage, key)
        if digest is None:
            report.findings.append(
                Finding(UNREADABLE_OBJECT, str(version_id), "object could not be read")
            )
            continue
        report.bytes_hashed += read
        if digest != sha256:
            # The stored digest is not repeated in the message. Printing both
            # invites somebody to "fix" the row to match the bytes, which is
            # the one repair that turns a detected corruption into a clean one.
            report.findings.append(
                Finding(
                    SHA_MISMATCH, str(version_id), "stored bytes do not match the recorded SHA-256"
                )
            )


def _digest_of(storage: Any, key: str) -> tuple[str | None, int]:
    digest = hashlib.sha256()
    read = 0
    try:
        with storage.open(key, "rb") as handle:
            while chunk := handle.read(HASH_CHUNK_BYTES):
                digest.update(chunk)
                read += len(chunk)
    except Exception:
        return None, 0
    return digest.hexdigest(), read


def _check_current_versions(report: IntegrityReport) -> None:
    """A document's live evidence must be its own.

    Nothing in the schema says so. ``current_version`` is an ordinary foreign
    key to `DocumentVersion`, and the only reason it always points at a version
    of the same document is that `add_evidence_version` is the only writer. That
    is a property of the code rather than of the data, so it is checked as one.
    """
    rows = Document.objects.exclude(current_version=None).values_list(
        "pk", "current_version_id", "current_version__document_id"
    )
    for document_id, version_id, owner_id in rows.iterator(chunk_size=500):
        if owner_id != document_id:
            report.findings.append(
                Finding(
                    FOREIGN_CURRENT_VERSION,
                    str(document_id),
                    f"current version {version_id} belongs to document {owner_id}",
                )
            )


def _check_final_evidence(report: IntegrityReport) -> None:
    """Both halves of the rule a final evidence version was accepted under.

    `check_evidence_is_usable` says a submission's final version must belong to
    the submission's own Matter, and must not be less restricted than the
    submission. Both are refused by the service and backed by triggers, so
    nothing this system does can produce a row that fails them — but the
    triggers are `BEFORE UPDATE` and cannot reach backwards, and the third of
    them arrived after the first two. A row written before it, or written with
    triggers disabled, is exactly the corruption an operator has to be able to
    find, and it is invisible: the submission still renders, and its evidence
    still downloads. To the wrong people, which is the point.

    Two columns of a bounded set of rows, in one query. Effective visibility is
    derived rather than stored (docs/adr/0005), so it is computed here the same
    way the application computes it.
    """
    # Imported here rather than at module scope: `submissions` depends on
    # `documents`, and the reach back the other way belongs to this one
    # operator question rather than to the module.
    from app.submissions.models import Submission

    rows = (
        Submission.objects.exclude(final_version=None)
        .order_by("pk")
        .values_list(
            "pk",
            "matter_id",
            "visibility_override",
            "matter__visibility",
            "final_version_id",
            "final_version__document__matter_id",
            "final_version__document__visibility_override",
        )
    )
    for (
        submission_id,
        matter_id,
        submission_override,
        matter_visibility,
        version_id,
        evidence_matter_id,
        evidence_override,
    ) in rows.iterator(chunk_size=500):
        if evidence_matter_id != matter_id:
            report.findings.append(
                Finding(
                    FOREIGN_FINAL_EVIDENCE,
                    str(submission_id),
                    f"final version {version_id} is filed under teema {evidence_matter_id}",
                )
            )
            # The visibility comparison below reads the evidence's own Matter,
            # which for a foreign version is not the one being compared against.
            # Reporting the second finding from that would be noise on top of a
            # fault that already has to be resolved by hand.
            continue

        submission_effective = most_restrictive(
            matter_visibility, submission_override or Visibility.NORMAL
        )
        evidence_effective = most_restrictive(
            matter_visibility, evidence_override or Visibility.NORMAL
        )
        if most_restrictive(evidence_effective, submission_effective) != evidence_effective:
            report.findings.append(
                Finding(
                    EVIDENCE_LESS_RESTRICTED,
                    str(submission_id),
                    f"final version {version_id} reads as {evidence_effective} "
                    f"while the submission is {submission_effective}",
                )
            )


def _check_version_numbering(report: IntegrityReport) -> None:
    """Version numbers run 1..n with nothing missing.

    Uniqueness is a constraint; contiguity is not, and cannot be. Allocation is
    `max + 1` under a row lock, so a gap means a row that once existed is no
    longer there — which should be impossible, because `DocumentVersion` is
    PROTECT-ed from every side. A gap is therefore evidence that something got
    at the table outside the application, and is worth saying out loud even
    though nothing downstream depends on the numbers being contiguous.
    """
    rows = (
        DocumentVersion.objects.values("document_id")
        .annotate(count=Count("pk"), top=Max("version_number"))
        .order_by("document_id")
    )
    for row in rows.iterator(chunk_size=500):
        if row["top"] != row["count"]:
            report.findings.append(
                Finding(
                    VERSION_NUMBER_GAP,
                    str(row["document_id"]),
                    f"{row['count']} version(s) but the highest number is {row['top']}",
                )
            )


def _check_stuck_extractions(report: IntegrityReport) -> None:
    """Claims older than the window in which a claim means anything.

    Not an evidence failure — the bytes are fine and the derivative is
    rebuildable — but it is one of the questions an operator has to be able to
    ask, and the honest answer is a count with identifiers rather than "the
    worker looks busy".
    """
    stale_before = timezone.now() - timedelta(minutes=settings.EXTRACTION_STALE_CLAIM_MINUTES)
    rows = DocumentVersion.objects.filter(
        extraction_state=ExtractionState.PROCESSING, extraction_claimed_at__lt=stale_before
    ).values_list("pk", "extraction_claimed_at")
    for version_id, claimed_at in rows.iterator(chunk_size=500):
        # The filter is `__lt`, so a NULL cannot reach here; mypy is reading the
        # column's nullability rather than the query's.
        when = claimed_at.isoformat() if claimed_at is not None else "unknown"
        report.findings.append(
            Finding(STUCK_PROCESSING, str(version_id), f"claimed {when} and never finished")
        )


def _check_orphans(storage: Any, report: IntegrityReport) -> None:
    """Stored objects no version refers to.

    Reported, never deleted — the grace-period reasoning that makes deletion
    safe lives in `prune_orphaned_evidence`, and duplicating it here would mean
    two answers to "is this object safe to remove". A recent orphan here is
    usually an upload that is committing right now.
    """
    keys, unreadable = walk_storage(storage)
    report.objects_seen = len(keys)
    for prefix, reason in unreadable:
        report.findings.append(
            Finding(UNREADABLE_PREFIX, prefix or "(root)", f"listing raised {reason}")
        )
    if unreadable and not keys:
        # Nothing could be listed, so "no orphans" would be a conclusion drawn
        # from an absence of evidence rather than from evidence of absence.
        return
    # Every canonical holder of evidence bytes, asked in one place. The opinion
    # archive keeps its binaries here too, and a checker that only knew about
    # `DocumentVersion` would report every one of them as an orphan — which is
    # the report an operator acts on with the pruner (app/documents/references.py).
    referenced = referenced_storage_keys()
    for key in keys:
        if key not in referenced:
            report.findings.append(Finding(ORPHAN_OBJECT, key, "no canonical row refers to it"))
