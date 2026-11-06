# 0211 — Post-audit fixes: cohort retirement, export audit, the release image's own gate, the cache table

Status: accepted (owner brief «Focused Post-Audit Corrective Round», 2026-11-06)
Date: 2026-11-06

This ADR amends:
- [0192](0192-the-lovable-pilot-teemad-follow-lovable-nightly-one-way.md) §5;
- [0165](0165-permanent-deletion-is-the-department-heads.md) §5;
- [0181](0181-one-internal-read-policy-for-all-business-content.md) (audit of break-glass).

It answers the November 2026 full codebase audit, findings C-L-04, B-01, G-02, L-02, G-01 and E-03.

## Decision

### 1. A permanently deleted cohort Teema retires from its cohort (C-L-04)

**The retirement record.** Permanently deleting a Teema that a synchronised Lovable cohort holds writes an attributable `LovableSyncRetirement`. It is written in the deletion's own transaction and names:
- the deletion's MATTER_DELETED event;
- the actor;
- the Lovable id;
- the reference.

**The required size.** A cohort's required size is its enrolled size minus its retirements, never below zero. `LovableSyncCohort.expected` is never rewritten.

**Drift still stops the night.** A state that disappears without a retirement is drift, and the night stops with COHORT_SIZE. A vanished topic or a broken read stops it as before.

**The replacement is not a deletion.** The source-authoritative replacement (ADR 0203) records no member retirements, because its enrolment retires whole cohorts.

**The operator command.** `lovable_sync retire --cohort --reference --reason [--apply]` records a retirement for a deletion made before the recorder existed. It is a dry run by default and runs under the run lock. It is refused unless:
- the Teema is a tombstone whose MATTER_DELETED event removed a synchronisation state;
- no retirement names it yet;
- the cohort is short.

**Forgery.** The model accepts a retirement only for a tombstone beside its own MATTER_DELETED event, and the table is append-only in the database (trigger on `core_reject_mutation()`).

**Reporting.** `report` lists each retirement by cohort, kind and time, with no reference. Retirement rows stay under the tombstone and hold identifiers only.

**What does not change.** COMPARE writes no business data, and APPLY stays off.

### 2. Bulk exports and break-glass reads are audited, without their contents (B-01)

**Export rows.** Every bulk export a request hands over writes one `EXPORT_GENERATED` row. The export views are the register table and full export, the reporting CSVs, the legacy workbook, the Lovable workbook and the Word report. The row records:
- the export's code name and format;
- the number of records selected;
- the method;
- the persona and authentication provenance (ADR 0180);
- the client address and user agent;
- the names of the filters that export honours (its own allowlist; any other name is only counted);
- when the request may read business content, a SECRET_KEY-keyed HMAC of the filter values. An actor without business read gets no digest, since the application computing one for a chosen guess would confirm it.

**Never stored.** Titles, cell values and search text.

**What writes nothing.** HEAD requests, refusals, 404s and 409s. Single evidence files keep `DOCUMENT_DOWNLOADED`.

**Break-glass rows.** A request whose business read is authorised by a break-glass grant, rather than by a business role, writes one `BREAK_GLASS_USED` row per request and grant: the grant, path, route and method. Nothing is written outside a request. A failed audit write fails the read.

**Restricted records.** `RESTRICTED_RECORD_ACCESSED` is not emitted: RESTRICTED is a label here, not an access boundary.

### 3. The release image is gated on its own bytes (G-02)

**The gates.** `release-image.yml` builds the image afresh, so it runs CI's runtime check and CI's Trivy gate on the image it archives, before saving it. The Trivy gate uses the same pinned scanner and the same rule (`--ignore-unfixed`, HIGH and CRITICAL fail).

**The manifest.** It records:
- the scanned, runtime-checked and archived image IDs, which must be equal;
- the scan summary;
- the sha256 of the OS-package list.

**The host side.** The host refuses any artifact, or any loaded image, whose ID is not the scanned one (`scripts/deploy/juristid-verify-release-artifact.sh`, plus the release kit's d1). A green CI run on the source is not evidence about a release image.

**The verifier.** It hashes the archive itself and requires its `.sha256` to be one line naming exactly that archive. The runtime check and the OS-package list run the recorded image id, not the tag.

### 4. The database cache table is schema (L-02)

`core_cache` is created by migration `core/0003_database_cache_table`, so no deployment step has to remember `createcachetable`. The migration:
- is idempotent;
- uses `createcachetable`'s exact schema;
- is a no-op wherever the table already exists;
- has a no-op reverse.

CI probes the cache after migrate-from-zero and after the recovery rehearsal.

### 5. Tests build Tallinn days from Tallinn days (G-01)

A test that asserts Tallinn calendar days builds its instants as `start_of_local_day(localdate() + timedelta(days=n))`, never as `timezone.now() ± timedelta(days=n)`. Across a summer-time change, n × 24 h lands on the neighbouring day for one evening hour. `tests/test_aud_w4_no_dst_bombs.py` guards the pattern.

### 6. Historical source text wraps, never clips (E-03)

Text on `/ajalugu/` is evidence. It wraps (`overflow-wrap: anywhere` on the page text, and on the title and file-name cells of the review tables) and is never truncated or elided. The grid and flex holders of such text use `minmax(0, 1fr)` / `min-width: 0`, so the `overflow: hidden` card cannot clip it.

## Consequences
- **Migrations:** two, both additive. `legacy_import.0023` adds a new table. `core.0003` creates the cache table if it is missing, and is a no-op on production.
- **Per-export cost:** one COUNT and one INSERT for each export.
- **Release time:** release-image runs are 2–3 minutes longer. A release now fails if a fixable HIGH or CRITICAL vulnerability appears between CI and release.
