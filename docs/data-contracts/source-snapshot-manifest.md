---
contract: source-snapshot-manifest
version: 1
status: convention agreed; no snapshot is stored in this repository
owner: technical owner
---

# Source snapshot manifest

## Rule

**Confidential source material never enters Git.** This repository documents the
convention and the manifest format. The snapshots themselves live in the
approved snapshot store, outside version control, with restricted access.

Nothing has been imported and no snapshot is referenced by this repository yet.

## Directory convention

Outside the repository, in the approved store:

```
<snapshot-root>/
  2026-08-18-excel-tood-eelnoudega/
    manifest.json
    source/
      Tood eelnoudega.xlsx          # byte-exact copy, read-only
    checksums.txt
  2026-08-18-onenote-oigusosakond/
    manifest.json
    source/
      export/                       # approved export tooling output
      resources/                    # embedded binaries where recoverable
    checksums.txt
```

Inside the repository, only `docs/data-contracts/` and the importer code. A
snapshot is referenced by its `snapshot_id` and hash, recorded on
`legacy_import.ImportBatch`, never by a copied file.

## Manifest format

```json
{
  "snapshot_id": "2026-08-18-excel-tood-eelnoudega",
  "captured_at": "2026-08-18T09:00:00+03:00",
  "captured_by": "name of the person who took it",
  "source_system": "EXCEL_TOOD_EELNOUDEGA",
  "capture_method": "byte-exact file copy from the live location",
  "source_location": "described, not linked",
  "items": [
    {
      "path": "source/Tood eelnoudega.xlsx",
      "sha256": "…",
      "size_bytes": 0,
      "modified_at": "…",
      "source_identifier": "original path or OneNote page id",
      "source_url": "original URL where one exists"
    }
  ],
  "counts": {
    "files": 1,
    "sheets": 16,
    "onenote_pages": 0
  },
  "notes": "anything the capture could not preserve, stated explicitly",
  "immutable_copy": {
    "location": "approved archival store",
    "made_read_only_at": "…"
  }
}
```

## Capture rules

1. **Before any destructive change to a source**, take the snapshot.
2. Byte-exact copies with SHA-256 for every file.
3. For OneNote, use approved export tooling and preserve page metadata,
   identifiers and embedded resource binaries where possible; also keep the
   native export format as a fidelity backstop.
4. Record what the capture could **not** preserve. An honest gap is evidence; a
   silent one is a defect.
5. Make an immutable, read-only archival copy.
6. Repeat the capture at cutover, and record both.

## Migration process notes

- Migration reads **per-era Excel contracts** (one per sheet/year) and immutable
  OneNote snapshots. It never reads the live sources directly.
- A legacy hyperlink is evidence, not a primary key. Matching runs
  deterministic identifiers first, then explicit reference tokens and exact
  URLs, then multi-signal candidates, then human review. Match method,
  confidence and reviewer are preserved on `MatterSourceReference`.
- The same SHA-256 proves identical bytes, not the same business occurrence.
  Separate source occurrences keep separate provenance rows.
