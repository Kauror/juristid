# 0003 — Document lifecycle: immutable evidence and mutable working documents

- **Status:** Accepted (Stage 0)
- **Date:** 2026-08-18

## Context

The specification settles a hybrid model: PostgreSQL owns document metadata,
Azure Blob owns immutable evidentiary binaries, and SharePoint may hold
collaborative working documents. Stage 0 must fix the schema and the storage
seam so Stage 2 can add real storage without a destructive redesign.

## Decision

**Three roles, never conflated**

| Concern | Owner |
| --- | --- |
| Document metadata, relationships, visibility, retention | PostgreSQL |
| Immutable evidentiary binaries | Azure Blob (production), local filesystem (development) |
| Mutable Office co-authoring files | SharePoint, by reference only |

**Schema**

- `Document` is the logical artefact: role, title, current evidence-version
  pointer, optional SharePoint identifiers (site / drive / item / URL / etag /
  observed-at), retention class, legal hold, provenance and visibility.
- `DocumentVersion` is one exact binary: storage key, original filename, MIME
  type, size, SHA-256, uploader, acquisition time, source path/URL/identifier,
  malware-scan state and extraction state.
- A trigger (`documents/migrations/0002`) rejects any UPDATE that changes the
  byte-identity columns. Operational state columns (`malware_scan_state`,
  `extraction_state`) stay editable, because they describe what we have learned
  about the binary, not the binary itself.
- A correction is always a new version. There is no in-place replace path.

**Write path (`add_evidence_version`)**

The order of operations is deliberate:

1. Take a row lock on the logical `Document` (`select_for_update`). Version
   numbers are allocated under that lock, so concurrent captures queue instead
   of racing to the same number.
2. Derive the storage key from the allocated number **and** a freshly generated
   UUIDv7 for the version. Two writers cannot produce the same key even if a
   retry re-uses a number; a unique constraint on `storage_key` is the
   database-level backstop, and the backend is asked whether the key already
   exists before anything is written.
3. Write the bytes.
4. Create the `DocumentVersion` row, move the current-version pointer and record
   the ChangeEvent, all in the same transaction. **If any of that fails, the
   stored object is deleted again** before the exception propagates.

Bytes are written *before* the row that describes them, not after. The
alternative — create the row, write the bytes on commit — trades an orphaned
object for a `DocumentVersion` claiming evidence that does not exist. An
orphaned object is harmless, detectable and removable; a row pointing at missing
bytes is an evidence-integrity hole.

One residual case remains: a caller that wraps the call in a larger transaction
and rolls it back *after* the call returned, or a process that dies between the
storage write and the commit. Django offers no rollback hook, so
`manage.py prune_orphaned_evidence` lists — and with `--delete` removes —
stored objects that no `DocumentVersion` references. The design fails in the
safe direction by construction.

**Pruning is deliberately conservative.** Being unreferenced is not proof of
being an orphan: because the bytes are written before the row, an upload that is
mid-transaction *right now* is indistinguishable from an orphan. The command
therefore deletes an object only when the object's own storage timestamp proves
it is older than a grace period, `EVIDENCE_ORPHAN_GRACE_HOURS` (default 24, far
longer than any transaction could last). If the storage backend cannot establish
an object's age, the object is reported and left alone. Reclaiming a stray blob
is worth far less than never destroying evidence a committing transaction is
about to point at.

Three later corrections follow from the same priority, and all three concern
what the command is allowed to *claim* rather than what it deletes.

- The grace period is the only protection there is, so `--grace-hours 0` with
  `--delete` silently reinstated the exact race the design prevents. Deletion
  now requires at least `MINIMUM_DELETE_GRACE_HOURS`; reporting under any window
  stays available.
- A listing that raised used to come back as an empty result, so an unreadable
  prefix — an unmounted evidence root, a permission problem on one Matter's
  directory — printed *no unreferenced evidence objects found*, which is the
  sentence an operator reads as "the store is healthy". Unreadable prefixes are
  now named and the command exits non-zero.
- A deletion that failed used to be counted among the deleted and to abandon the
  rest of the run. Each deletion now stands alone, only completed ones are
  counted, and the command exits non-zero if any failed.

**Detecting the failure the schema cannot see.** Every constraint PostgreSQL can
enforce about evidence is enforced there — unique version numbers, a unique
storage key, lowercase-hex checksums, non-negative sizes. The one failure this
whole design is arranged around, a committed row whose object is not there, is
by construction invisible to all of them, because the bytes are not in the
database. `manage.py check_evidence_integrity` is how an operator asks: it
compares every `DocumentVersion` against the object it claims, reports objects
nothing references, and checks the invariants the schema does not hold —
notably that a `Document.current_version` belongs to that same document, which
only `add_evidence_version` guarantees, and that a `Submission.final_version`
still satisfies the rule it was accepted under (ADR 0011, amended 2026-08-27).
The second is enforced on every input a running system can change, so a row
failing it predates the enforcement or was written around it; the check is there
because such a row is otherwise invisible — the submission renders and its
evidence downloads, to the wrong people. `--verify-sha` reads every stored byte
and is never implied, because on a multi-gigabyte store it is a maintenance
window rather than a health check. It is read-only in every mode: a missing
object is a restore decision and a mismatched checksum is a question about which
copy is real, and correcting a row to match whatever the store currently holds
would turn a detected loss into an undetectable one.

**Storage seam**

- Django's `STORAGES` setting with a named `evidence` alias. Development uses
  `FileSystemStorage`; the secure pilot and production point the same alias at
  Azure Blob through `django-storages`. Domain services only ever call
  `evidence_storage()`.
- The Azure dependency is added when the environment that needs it exists
  (Stage 2 / 2.5), not before.

**Upload rules (already enforced in `app/documents/services.py`)**

- An allow-list of business MIME types; anything else is refused.
- Empty files refused, size ceiling enforced (`MAX_EVIDENCE_UPLOAD_BYTES`).
- SHA-256 computed at capture and stored.
- Malware scanning starts as `PENDING`; the scanning/quarantine path is a
  Secure Pilot Gate requirement, and the state column exists from day one so it
  can be wired in without a migration on live evidence.

**Not built in Stage 0**

- `DocumentDerivative` (extracted text, previews, OCR). Derivatives are
  rebuildable by definition, so adding the table in Stage 2 is additive and
  safe. `DocumentVersion.extraction_state` is the hook.
- Any SharePoint API integration. Only the identifier columns exist.

**Explicitly rejected:** a default full SharePoint mirror of evidence. It would
create a second hierarchy with its own permissions and reconciliation burden.
Independent recovery is satisfied by tested backups and portable archive
exports.

## Consequences

- The database can always answer "what evidence exists and is it intact"
  without reading storage.
- A restore can be verified by re-hashing bytes against `sha256`.
- Identical bytes (same SHA-256) do not imply the same business occurrence;
  separate source occurrences keep separate rows and provenance.

## Reversibility

Storage backend: high (one settings alias). Immutability model: low, and
deliberately so.
