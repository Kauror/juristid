# Lovable mirror

A read-only, **non-canonical** copy of the Lovable alpha's data inside Juristid,
for comparison only. Decision record: [ADR 0157](adr/0157-lovable-data-is-mirrored-for-comparison-never-canonical.md).
Code: `app/lovable_mirror/`. Page: `/haldus/lovable/` (administrator only, 404 for
everybody else, on no navigation bar).

## What it is and is not

* It **is** an immutable record of what the Lovable alpha said at the moment an
  export was taken, plus a read-only comparison of that with the previous
  export and with canonical Juristid.
* It is **not** a source of truth. No mirror value is ever written to a
  `Matter`, `Submission`, `NextAction`, document, owner, collaborator,
  `ChangeEvent` or import-provenance row. There is no promotion path (see
  §"Promotion" below).
* It does **not** modify Lovable and holds no credential for it.

## Architecture

```
operator export bundle ──► FileExportReader ─┐
(outside the checkout)                       ├─► ExportBundle ─► importer.import_bundle ─► SourceSnapshot
future read-only role  ──► LiveReadOnlyReader┘   (verified)       (one transaction)        SourceRecord
  (disabled stub)                                                                          SourceFieldValue
                                                                                                │
                              reconcile.build_report(user) ◄────────────────────────────────────┘
                              ├─ diff vs previous snapshot (by nr)
                              ├─ match to Matter.objects.visible_to(user) by Teemaviide
                              ├─ COMPARABLE fields vs canonical (exact rules)
                              └─ coverage boundaries
                                       │
                         /haldus/lovable/  ·  manage.py lovable_mirror_report
```

| Module | Role |
| --- | --- |
| `catalogue.py` | Field-level mapping catalogue (tables, columns, classes, canonical counterpart). |
| `readers.py` | `SourceReader` protocol; `FileExportReader` (v1); `LiveReadOnlyReader` (disabled stub); path guard. |
| `importer.py` | The only write path. Writes the three mirror tables, idempotent by checksum, all-or-nothing. |
| `reconcile.py` | Read-only selector: diff, matching, conflicts, coverage. Stores nothing. |
| `views.py` | The preview page. GET only. |
| `management/commands/lovable_mirror_import.py` | `--export DIR [--expect-sha256 SHA]`, or `--live` (refuses). |
| `management/commands/lovable_mirror_report.py` | `--as UPN [--json]`, the same report as the page. |

The three tables refuse `UPDATE` in the database (trigger `*_immutable` on
`core_reject_mutation()`), and in Python. `DELETE` of a whole snapshot is
allowed: it is the retention path.

Matching results are **not stored**. A stored match would freeze an answer that
depends on who is reading (visibility) and on canonical data that keeps
changing; computing it on read over the reader-visible population is what the
round's contract §3 requires.

## The Lovable schema this is built from

Verified from the private repository `Kauror/cheerful-control` at `f248d20b`
(last pushed **2026-08-17**), file `src/integrations/supabase/types.ts`, and the
code that writes those tables (`parse.server.ts`, `upload.functions.ts`,
`snapshot.server.ts`, `changes.ts`). No Lovable data record was read.
**The live Lovable project may be ahead of that repository**: Lovable syncs its
edits to the connected repository, so either nothing changed after 17 August or
the sync stopped. This must be re-checked when access is granted.

| Table | Columns (as of `f248d20b`) | Role here |
| --- | --- | --- |
| `datasets` | id, file_name, is_current, latest_year, row_count, uploaded_at, years | Tells which upload is current |
| `topics` | id, dataset_id, nr, aasta, teema, oigusakt, sisse, arvamuse_tahtaeg, valja, valja_kuupaev, vastutaja, hetkeseis, jargmiseks, onenote_link, raw_hyperlink, sheet_name, row_index | Operative rows: matched and compared |
| `longterm_topics` | as `topics` without onenote_link/raw_hyperlink (sheets 2011+) | Stored only |
| `snapshots` | id, created_at, dataset_id, file_name, fingerprint, latest_year, row_count, snapshot_date, source, summary, years | Stored only |
| `snapshot_themes` | id, snapshot_id, nr, source_sheet, source_year, vastutaja, source_data, derived_data, is_active, next_deadline, deadline_status, days_to_deadline, is_overdue, requires_attention, attention_reasons | Stored only |

**Not present in that schema:** `topic`, `topic_change_event`,
`topic_change_field`, `topic_commencement`, `topic_responsible_change`,
`topic_link`. (`topic` is `topics`.) They may exist in the live project. The
importer **refuses** a bundle carrying any of them until a reviewed catalogue
entry exists, because a table nobody has described is a table nobody has
decided anything about.

Facts that shaped the design:

* Every Lovable upload inserts a new `datasets` row and **new `topics` ids**,
  and keeps earlier datasets' rows. Lovable's row id is therefore not a stable
  key; `nr` is, and Lovable itself compares snapshots by `nr` only. The
  operative population is the `topics` rows of the single dataset with
  `is_current = true`; if that cannot be determined, nothing is compared and
  the page says why.
* Lovable is itself derived from the Excel register Juristid already imported
  and cut over from; it is a third copy, not an independent source.

## Field mapping catalogue

Classes: **IDENTITY** (deterministic match key) · **COMPARABLE** (exact rule
against one canonical value; a difference is a conflict) · **REVIEW_ONLY**
(shown beside canonical, never counted) · **NEVER_AUTO** (never converted into
a canonical fact) · **PROVENANCE** (where the row sat; not diffed) ·
**LOVABLE_DERIVED** (Lovable's own computation) · **UNCATALOGUED** (unknown
column: stored, reported, never compared).

`topics` (the same for `longterm_topics`):

| Field | Class | Juristid concept | Rule |
| --- | --- | --- | --- |
| nr | IDENTITY | Teemaviide `YYYY_N` | `^\d{4}_\d{1,6}$` → `(reference_year, reference_number)` |
| teema | COMPARABLE | `Matter.title` / `alternate_titles` | `normalize_for_matching` equality |
| sisse | COMPARABLE | `Matter.received_date` | ISO date equality |
| arvamuse_tahtaeg | COMPARABLE | `Matter.response_deadline` | ISO date equality |
| vastutaja | COMPARABLE | `Matter.owner` | `legacy_import.resolution.resolve_owner`, no mapping file; unresolved/ambiguous → «Ei saa võrrelda» |
| oigusakt | REVIEW_ONLY | `Matter.legal_instruments` | shown only |
| hetkeseis | NEVER_AUTO | stage | shown beside the stage; **never a stage transition** |
| jargmiseks | NEVER_AUTO | — | **never a NextAction** or deadline |
| valja, valja_kuupaev | NEVER_AUTO | — | **never a SENT Submission** or send date |
| aasta | LOVABLE_DERIVED | — | — |
| id, dataset_id, sheet_name, row_index, onenote_link, raw_hyperlink | PROVENANCE | — | — |

`snapshot_themes.source_data` (the whole Excel row including note columns) is
NEVER_AUTO: **a note is never evidence or an opinion**. The full catalogue,
including `datasets`, `snapshots` and the remaining `snapshot_themes` columns,
is in `app/lovable_mirror/catalogue.py` and rendered on the page.

Empty on either side, an unparseable date or an owner name that does not
resolve is **not** a conflict; it is counted under «Ulatus ja piirid».

## Matching

Deterministic keys only, no similarity and no AI:

| Class | When |
| --- | --- |
| Seotud (MATCHED) | `nr` parses, occurs once in the current dataset, and a Matter the reader can read has that Teemaviide |
| Mitmeti mõistetav (AMBIGUOUS) | the same `nr` occurs on more than one current Lovable row |
| Sidumata (UNMATCHED) | `nr` missing/unparseable (`NO_KEY`), or no readable Matter has it (`NO_READABLE_MATTER`) |

Matching runs over `Matter.objects.visible_to(reader)`. The administrator does
not read RESTRICTED Matters (docs/adr/0005), so a Lovable row about a
RESTRICTED Matter appears to them as unmatched. That is the same rule every
other surface follows; the page states it.

## Export format (v1)

A directory, **outside the repository**:

```
<bundle>/
  manifest.json
  datasets.json
  topics.json
  ...            one JSON array of row objects per table
```

`manifest.json`:

```json
{
  "format": "juristid.lovable-mirror-export",
  "format_version": 1,
  "data_class": "REAL",
  "source_schema_version": "cheerful-control@<sha> or the Supabase migration id",
  "exported_at": "2026-10-20T09:00:00+03:00",
  "exported_by": "who ran the export",
  "export_method": "e.g. read-only SQL COPY of the listed tables",
  "tables": {
    "topics": {"file": "topics.json", "sha256": "<64 hex>", "row_count": 312}
  },
  "notes": "anything the export could not preserve"
}
```

Rules the importer enforces (each refusal writes nothing):
the directory is outside the checkout; `format`/`format_version` are known;
`data_class` is `REAL` or `SYNTHETIC`, and `REAL` requires
`REAL_DATA_ALLOWED`; `exported_at` carries a time zone; every table is in the
catalogue (and none is an unverified one); every `file` is a bare file name in
the bundle; every file's SHA-256 and row count match the manifest; every row has
the table's id and required columns; ids are unique per table;
`--expect-sha256`, when given, matches `manifest.json`.

The snapshot checksum is the SHA-256 of `manifest.json`, which pins every
table file's own hash. Re-importing the same bundle is a no-op.

## Operator procedure

1. **Where the export is produced.** By someone with approved read access to
   the Lovable project, inside an **approved Koda-controlled real-data
   environment that meets the secure pilot gate** (`docs/secure-pilot-gate.md`).
   Never on a developer or home machine, never in `juristid-test`, Git, CI,
   screenshots, tickets or PR text.
2. Export the catalogued tables as JSON arrays, write the manifest with each
   file's SHA-256, and record the manifest's own SHA-256 out of band.
3. Copy the bundle to the Juristid host, outside the checkout, readable only by
   the operator account.
4. `python manage.py lovable_mirror_import --export <bundle> --expect-sha256 <sha>`
5. Review at `/haldus/lovable/` or `python manage.py lovable_mirror_report --as <admin-upn>`.
6. Delete the bundle files from the host once imported, unless retention says
   otherwise.

## Retention

A snapshot holds real register content. Deleting it removes it whole
(`SourceSnapshot.delete()` cascades; the triggers refuse only UPDATE). No
command is provided yet; the retention period is an owner decision.

## Live read-only refresh (future; disabled)

Owner decision of 2026-10-13: approved read-only access will be provided later.
`readers.LiveReadOnlyReader` is the seam. It refuses today in every
configuration and makes no connection. The setting
`LOVABLE_MIRROR_LIVE_READER_ENABLED` (read with `getattr`, default off, not
declared in settings) only says a reader is configured; a reader still has to
be implemented and reviewed. The import command's `--live` refuses, and the
page shows the refresh as disabled with no button.

Prerequisites, all of them, before a live reader is written or enabled:

1. **Access shape.** A dedicated PostgreSQL role (or a Supabase API key bound to
   it) with `SELECT` only, on exactly `public.datasets`, `public.topics`,
   `public.longterm_topics`, `public.snapshots`, `public.snapshot_themes` (and
   any table later added to the catalogue after review), the catalogued
   columns only; no `INSERT/UPDATE/DELETE`, no RPC (`save_daily_snapshot` must
   not be callable), no `auth`/`storage` schema, RLS respected (no
   `BYPASSRLS`). **Never the service-role key**; never the publishable
   (anon) key either, because it is shipped to browsers and its reach is set by
   RLS policies Juristid does not control.
2. **Where it may run.** Only the approved Koda-controlled real-data Juristid
   deployment that meets the secure pilot gate. Never `juristid-test`, CI, a
   developer machine or the synthetic rehearsal host.
3. **Network path.** Outbound HTTPS/PostgreSQL-TLS from that host to the Lovable
   Supabase project only, egress-allow-listed to that one endpoint; TLS
   verified; no inbound path.
4. **Secret handling.** The credential lives in the deployment's secret store
   / environment file (the open decision in `docs/secure-pilot-gate.md` row 5),
   never in Git, the image, a Compose default, a test or a log line.
5. **Consistency.** One repeatable-read transaction per refresh; the reader
   builds an `ExportBundle` with a checksum over the canonical JSON it read, so
   idempotency and immutability are unchanged.
6. **Schema re-check.** Re-read the live schema against `catalogue.py` first;
   any new table or column gets a reviewed catalogue entry before it is read.
7. **Owner sign-off** recorded in an amendment to ADR 0157, and a
   test proving the reader issues only `SELECT` on catalogued tables.

## Promotion (none)

There is no path from a mirror value to canonical data. Any future promotion —
e.g. offering a lawyer «Lovable says the deadline is X» as a suggestion — is a
separate, explicit, reviewed product decision with its own ADR, its own
authorization and a human confirming each change through the ordinary
canonical services. NEVER_AUTO fields stay never-auto under any promotion.
