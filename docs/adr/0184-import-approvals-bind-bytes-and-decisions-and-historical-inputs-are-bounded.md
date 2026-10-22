# ADR 0184 — Import approvals bind bytes and decisions, and historical inputs are bounded

- Status: Proposed (2026-10-22), security hardening round 2
- Findings: csf_566209e1736d4e8706e9eccd (register apply not bound to the reviewed
  dry run), csf_ff21dec71f0f7bfecdef6502 (historical approvals do not cover every
  file and decision consumed), csf_c79fda7c380313bb74cf517b (historical readers
  read unbounded files, inventories and CSV/JSON)
- Extends: ADR 0173 (evidence pilot), ADR 0178 (historical containment and limits)

## Context

Three reviewed importers write business records from operator-supplied sources.
Each has a plan step a person reads and an apply step that writes, and in each
the apply could write something the reviewer had not seen:

1. **`import_legacy_register`.** The dry run printed the workbook's hash; the
   apply rebuilt a fresh plan from the path, the mapping file and the database
   as they were at that moment, and wrote it. The workbook was also hashed by
   one open and parsed by another.
2. **The evidence pilot.** `--expect-package-sha256` is the canonical digest of
   `package.json`. Page metadata, page text, blocks, links, the source XML,
   resource records and the originals of skipped files were read from the
   archive beside it and covered by nothing. A page's XML was stored without
   comparing it with the `xmlSha256` its `current.json` claims.
3. **`historical_import`.** The gate compared the audit's
   `archive-manifest.tsv` digest with the audit's own record of it — a file
   compared with a claim about that file — never the archive's bytes with the
   manifest, and no digest covered the audit's decisions or the plan.

And the readers round 1 left alone (ADR 0178 bounded ZIPs and workbooks) still
read whole JSON, text and CSV files, listed page and resource directories in
full before sorting them, and the opinions-directory reader sorted the whole
walk before counting it.

## Decision

### 1. One integrity layer (`app/legacy_import/integrity.py`)

- **Source manifest.** Every file of a source tree as `(relative POSIX path,
  size, SHA-256)`, read through `safe_paths` (no links, no special files) and
  within the limits of §4. Two names that are one file on a case-insensitive or
  normalising file system (`NFC` + case fold) are refused. Its digest is the
  SHA-256 of canonical JSON, independent of directory order.
- **Verified reads.** `OneNoteArchive(root, manifest=…)` checks every file it
  reads against the manifest: the bytes parsed are the bytes hashed, a file the
  manifest does not list is refused, a listed file that is gone is refused.
  `read_page_xml` always refuses XML that does not hash to the claimed
  `xmlSha256`.
- **Canonical plan digest.** `canonical_sha256` over the resolved plan: sorted
  keys, no display text; a database row enters as its model label and primary
  key, so the digest changes when the rows a plan resolved against change.
  `require_digest` refuses an empty or different expectation.

### 2. Each reviewed apply requires its plan digest

| Command | Plan prints | Apply requires | The digest covers |
| --- | --- | --- | --- |
| `import_legacy_register` | `--dry-run`: `plan sha256` | `--expect-plan-sha256` | workbook bytes as parsed, mapping-file bytes, every era contract's bytes, importer/parser/contract versions, every row's outcome, note, anomalies, values written, and the Matter, owner, organisation and stage rows it resolved to, the reference high-water marks |
| `evidence_pilot` | `validate`: `manifest_sha256`; `plan`: `plan_sha256` | `--expect-package-sha256` (unchanged) **and** `--expect-plan-sha256` | the package digest, the manifest of every file in the package directory, the database state, and every owner, stage, instrument, addressee and organisation resolution, with the catalogue rows each approved name matched |
| `historical_import` | `plan`, `dry-run`: `plan SHA-256` | `--expect-plan-sha256` | the Excel hash, every archive file, every audit report parsed, and every page decision, exact link (with the register Matter it names), candidate and already-imported page |

- The **register** apply opens one transaction, locks `matters_matter` (as the
  evidence pilot does, ADR 0173 §6), builds the plan inside it, and refuses on a
  mismatch before any business write. The digest is recorded on the batch
  (`notes`: `plan-sha256 …`) and in the `IMPORT_RUN` security event.
- The **evidence pilot** rebuilds the plan inside its transaction after the lock
  — the package re-hashed file by file, the database re-resolved — and writes
  from that rebuilt plan, whose archive reads only through its manifest. The
  package digest stays the import's identity in `EvidencePilotRecord`, so an
  applied package is still recognised as applied and **nothing already
  imported needs re-importing**. An applied package writes nothing and says so
  before the digest is compared. The plan and manifest digests are recorded in
  the batch notes and the `IMPORT_RUN` event.
- The **historical** plan hashes the whole archive and refuses it unless it
  matches `archive-manifest.tsv` file for file (missing, unexpected or changed);
  pages whose XML does not hash to their claim are fatal findings; apply links a
  page only to the Matter the plan named, and reports a register that changed
  since. Its structure apply stays one transaction per page (by design); every
  archive read during it is verified against the plan's manifest. The digest is
  recorded in the batch notes.

The reviewed v2.3 package is **not** rewritten. Its digest is unchanged
(`b83861f9…`); the stronger manifest is computed from the package as it is, and
`--manifest-out` writes it beside the operator's records — never into the
package.

### 3. Every source document is read within limits

`app/legacy_import/limits.py` gains, each settable under its `LEGACY_IMPORT_*`
name, refusing rather than truncating:

| Setting | Default | Measured maximum (2026-10-22) |
| --- | --- | --- |
| `MAX_JSON_BYTES` (checked before parsing; nesting past the recursion limit refused) | 64 MiB | 359,541 B (`package.json`, v2.3), depth 8 |
| `MAX_TEXT_BYTES` (`page.txt`) | 64 MiB | 8,756 B |
| `MAX_MEMBER_BYTES` (existing; originals and page XML) | 512 MiB | 7,133,408 B original; 126,135 B XML |
| `MAX_CSV_BYTES` / `MAX_CSV_ROWS` / `MAX_CSV_FIELD_CHARS` (per cell; the process-wide `csv.field_size_limit` is left alone) | 256 MiB / 1,000,000 / 131,072 | audit: 2,455 Excel Matters, 755 pages, 776 exact matches (Stage-2D audit figures; the audit itself is not held locally) |
| `MAX_PAGES` (refused while listing) | 20,000 | 12 (v2.3); 755 (historical corpus) |
| `MAX_PAGE_RESOURCES` (refused while listing) | 5,000 | 44 |
| `MAX_DIRECTORY_MEMBERS` (any listed directory, refused at limit + 1) | 50,000 | 44 (packages); 767 (opinions) |
| `MAX_SOURCE_FILES` (one tree walk) | 500,000 | 363 (v2.3), 711 (raw captures), 767 (opinions) |
| `MAX_TOTAL_SOURCE_BYTES` (one tree) | 32 GiB | 65.4 MB (v2.3); 4.14 GiB (historical corpus originals) |
| `MAX_WORK_BYTES` / `MAX_WORK_FILES` (everything one run reads, counted per read) | 128 GiB / 5,000,000 | v2.3 plan + apply ≈ 3 reads of 65 MB |

Directory enumeration (`safe_paths.scan_directory`, `iter_files`) counts as
`os.scandir` yields and refuses at one past the limit, so no caller ever sorts
an unbounded list. The migration audit is read once, through `safe_paths`, and
every CSV and JSON in it within these bounds.

## Consequences

- No migration, no new setting required, no new dependency, no service.
- **Runbook change:** `import_legacy_register --apply`, `historical_import
  apply` and `evidence_pilot apply` now each need `--expect-plan-sha256` from a
  plan run against the same sources *and the same database*. A plan made
  before another import ran is stale by design; plan again.
- A historical archive that differs from its audit manifest in any file, or
  whose page XML does not match its claim, no longer plans.
- Residual: the historical structure apply is per page, so a database change
  between two pages' transactions is caught only where the plan recorded the
  row it depends on (exact links, existing pages). The real `archive-manifest.tsv`
  is not held locally; its format is the synthetic one (`path<TAB>size<TAB>sha256`,
  backslashes accepted), and a different format refuses to plan rather than
  pass. `excel_pilot` and `refresh_current_register` (current-register readers,
  not historical sources) keep their own reads.
