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
