# Lovable mirror

A read-only, **non-canonical** copy of the Lovable alpha's data inside Juristid,
for comparison only. Decision record: [ADR 0157](adr/0157-lovable-data-is-mirrored-for-comparison-never-canonical.md).
Code: `app/lovable_mirror/`. Page: `/haldus/lovable/` (administrator only, 404 for
everybody else, on no navigation bar). The administrator sees only the Lovable
rows whose canonical content they may read (§«Who may see a Lovable row»).

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
                              ├─ nr → every Matter (privileged, internal) → withhold
                              │   what the reader may not see (count only)
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

**Catalogue status: derived from the August 2026 repository; live schema NOT
verified.**

The catalogue was built from the private repository `Kauror/cheerful-control`
at `f248d20b` (last pushed **2026-08-17**), file
`src/integrations/supabase/types.ts`, and the code that writes those tables
(`parse.server.ts`, `upload.functions.ts`, `snapshot.server.ts`, `changes.ts`).
No Lovable data record was read. **That repository is not the complete live
schema.**

### Known live tables

On 2026-10-13 the owner observed, through a read-only Lovable connector, that
the LIVE project contains these tables (names only; no columns, no rows):

| Live table | In the August repository | Catalogue | Columns |
| --- | --- | --- | --- |
| `datasets` | yes | catalogued (repository columns) | not verified live |
| `topics` | yes | catalogued (repository columns) | not verified live |
| `snapshots` | yes | catalogued (repository columns) | not verified live |
| `snapshot_themes` | yes | catalogued (repository columns) | not verified live |
| `topic` | no | **NOT MAPPED** — import refused | UNKNOWN |
| `topic_change_event` | no | **NOT MAPPED** — import refused | UNKNOWN |
| `topic_change_field` | no | **NOT MAPPED** — import refused | UNKNOWN |
| `topic_commencement` | no | **NOT MAPPED** — import refused | UNKNOWN |
| `topic_responsible_change` | no | **NOT MAPPED** — import refused | UNKNOWN |
| `topic_link` | no | **NOT MAPPED** — import refused | UNKNOWN |

`longterm_topics` is in the August repository and in the catalogue but was not
in the owner's live listing; whether it still exists live is unknown.

For the six new tables the column definitions are **unknown** pending an
operator-provided schema-only export (§«Schema-only export» below). No column
has been invented for them. The importer refuses a bundle carrying any table
without a catalogue entry, and the refusal message points to that procedure.

### Columns of the catalogued tables (from the repository, not verified live)

| Table | Columns (as of `f248d20b`) | Role here |
| --- | --- | --- |
| `datasets` | id, file_name, is_current, latest_year, row_count, uploaded_at, years | Tells which upload is current |
| `topics` | id, dataset_id, nr, aasta, teema, oigusakt, sisse, arvamuse_tahtaeg, valja, valja_kuupaev, vastutaja, hetkeseis, jargmiseks, onenote_link, raw_hyperlink, sheet_name, row_index | Operative rows: matched and compared |
| `longterm_topics` | as `topics` without onenote_link/raw_hyperlink (sheets 2011+) | Stored only |
| `snapshots` | id, created_at, dataset_id, file_name, fingerprint, latest_year, row_count, snapshot_date, source, summary, years | Stored only |
| `snapshot_themes` | id, snapshot_id, nr, source_sheet, source_year, vastutaja, source_data, derived_data, is_active, next_deadline, deadline_status, days_to_deadline, is_overdue, requires_attention, attention_reasons | Stored only |

### Placeholder mappings for the unmapped live tables

Each entry states the table's **probable** role, read from its name only, and
is **NOT MAPPED**: no column, class or canonical counterpart is assigned, and a
bundle carrying the table is refused. Each becomes a catalogue entry only after
the schema-only export has been reconciled and the entry reviewed. The same
placeholders are in `catalogue.UNMAPPED_LIVE_TABLES`.

#### `topic` — NOT MAPPED
Probable role (name only): a single, longer-lived topic entity beside the
per-upload `topics` rows. Its relation to `topics` and to `nr` is unknown, so
it cannot yet be matched to a Matter. Columns: UNKNOWN.

#### `topic_change_event` — NOT MAPPED
Probable role (name only): one row per recorded change to a topic (when, by
whom, of what kind). If confirmed, it would be source history, never a
canonical `ChangeEvent`. Columns: UNKNOWN.

#### `topic_change_field` — NOT MAPPED
Probable role (name only): the field-level detail of a change event (field,
old and new value). Columns: UNKNOWN.

#### `topic_commencement` — NOT MAPPED
Probable role (name only): entry-into-force (jõustumine) dates recorded per
topic. If confirmed, it would be compared at most with `MatterEffectiveDate`
(ADR 0071/0158) and never written to it. Columns: UNKNOWN.

#### `topic_responsible_change` — NOT MAPPED
Probable role (name only): the history of a topic's responsible person
(vastutaja). Never an ownership change in Juristid. Columns: UNKNOWN.

#### `topic_link` — NOT MAPPED
Probable role (name only): links from a topic to another topic or to an
external address. Never a related-matter link in Juristid. Columns: UNKNOWN.

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
| Sidumata (UNMATCHED) | `nr` missing/unparseable (`NO_KEY`), or no Matter at all has it (`NO_MATTER`) |

These classes apply only to rows the reader may see. Everything else is
withheld first (next section).

## Who may see a Lovable row (fail-closed)

A Lovable row is a copy of register content. Technical administration is not
business-content access (AGENTS.md, `app/core/authorization.py`): the
ADMINISTRATOR role reads NORMAL Matters, and reads RESTRICTED ones only as an
owner/collaborator or under a time-bounded break-glass grant. So the page —
although only the administrator can open it — shows a row only when the reader
could see the canonical content it stands for.

1. **Privileged lookup, internal only.** Every `nr` in the current and the
   previous snapshot is looked up against **all** Matters
   (`Matter.all_objects`, tombstones included) in one query
   (`reconcile.source_access`). The result is used only to decide visibility;
   it is never rendered or printed.
2. **`nr` belongs to a Matter** → the row is shown only if that Matter is in
   `Matter.objects.visible_to(reader)`. Otherwise it is withheld.
3. **No Matter at all, or no valid `nr`** (unmatched rows) → there is no
   canonical visibility to inherit, and the row may be about anything,
   including restricted work. Decision: it is shown only to a reader whose
   scope already reads every RESTRICTED Matter (`Scope.sees_all_restricted`).
   That is the narrowest existing combination of "may open this page"
   (ADMINISTRATOR) and "has business-content access to anything an unmatched
   row could be about": **an administrator under an active break-glass grant**
   (`accounts.BreakGlassGrant`, granted and revoked through the audited
   `grant_break_glass`). Without one, the «Sidumata» section renders a
   fail-closed message instead of rows. See the owner decision below.
4. **Withheld rows are counted, never described.** A withheld row contributes
   only to «N rida varjatud: puudub õigus vastava teema sisu nägemiseks» —
   one count for the current rows and one for the snapshot changes. No `nr`,
   title, field value, match class or «restricted match» marker. Rows about a
   RESTRICTED Matter and rows with no Matter fall into the same count, so the
   count does not reveal that a restricted Matter exists (a test proves the
   report is identical whether `nr` belongs to a RESTRICTED Matter or to none).
5. **Before presentation.** Withheld rows are dropped in `build_report`, so
   the page, the text report and the JSON report all receive only what may be
   shown. The same rules apply to `lovable_mirror_report`, which adds
   `hidden_rows`, `hidden_changes` and `coverage.unmatched_withheld` to the
   JSON.
6. **Change lists too.** Added, removed and changed rows between snapshots
   follow the same rule by `nr`.

**Does `nr` itself leak?** Yes, it can: a Teemaviide is a business identifier.
Next to a «hidden» marker it would confirm that a restricted Matter with that
reference exists; for a row with no Matter it reveals the register's numbering
and the existence of a register row. Decision: `nr` is shown only for rows that
are shown, i.e. when the reader may see the Matter it names (and so already
sees its reference everywhere else) or may see unmatched rows. A withheld row's
`nr` is never shown, printed or put in JSON.

**What is still shown to everyone who may open the page:** snapshot metadata
(checksum, export time), per-table row counts, column names of uncatalogued
fields, the catalogue, and the opaque withheld counts. None of these is a
Lovable field value.

**Owner decision needed (unmatched rows).** Choose one: (a) keep the rule
above — unmatched rows only under an active break-glass grant (note: the
codebase defines `BREAK_GLASS_USED` but no read path records it yet, so the
grant itself is the audit trail); (b) let the department head, who reads every
RESTRICTED Matter by role (ADR 0042), open this page as well; or (c) add a
dedicated capability (ADR 0145 style) for reading unmatched Lovable rows. Until
decided, (a) applies and the default administrator sees no unmatched text.

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
catalogue (a known-live but unmapped table is refused like an unknown one,
with a message pointing to §«Schema-only export»); every `file` is a bare file name in
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
   Rows the named administrator may not see are withheld and only counted.
6. Delete the bundle files from the host once imported, unless retention says
   otherwise.

## Schema-only export

The procedure that turns the known-live table names into reviewed catalogue
entries. It is run **by the owner**, in Lovable's own SQL console (Supabase SQL
editor) or through the owner's read-only connector — never from Juristid,
which holds no Lovable credential. Every query below reads only PostgreSQL
catalogue views; **none reads a business row.** Run each one separately and
save each result (CSV or JSON) outside any repository.

```sql
-- 1. Columns of every table and view in the public schema.
SELECT table_schema, table_name, ordinal_position, column_name,
       data_type, udt_name, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position;

-- 2. Tables, their kind and whether row-level security is enabled / forced.
SELECT n.nspname AS table_schema, c.relname AS table_name, c.relkind,
       c.relrowsecurity AS rls_enabled, c.relforcerowsecurity AS rls_forced
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm')
ORDER BY c.relname;

-- 3. Row-level security policies.
SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual, with_check
FROM pg_catalog.pg_policies
WHERE schemaname = 'public'
ORDER BY tablename, policyname;

-- 4. Table privileges by grantee (who may SELECT / INSERT / UPDATE / DELETE).
SELECT table_schema, table_name, grantee, privilege_type, is_grantable
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
ORDER BY table_name, grantee, privilege_type;

-- 5. Column-level privileges, if any were granted per column.
SELECT table_schema, table_name, column_name, grantee, privilege_type
FROM information_schema.column_privileges
WHERE table_schema = 'public'
  AND grantee NOT IN ('postgres', 'supabase_admin')
ORDER BY table_name, column_name, grantee, privilege_type;

-- 6. Keys and references (how the topic_* tables relate to topics / topic).
SELECT tc.table_name, tc.constraint_name, tc.constraint_type,
       kcu.column_name, ccu.table_name AS references_table,
       ccu.column_name AS references_column
FROM information_schema.table_constraints tc
LEFT JOIN information_schema.key_column_usage kcu
  ON kcu.constraint_schema = tc.constraint_schema
 AND kcu.constraint_name = tc.constraint_name
LEFT JOIN information_schema.constraint_column_usage ccu
  ON ccu.constraint_schema = tc.constraint_schema
 AND ccu.constraint_name = tc.constraint_name
WHERE tc.table_schema = 'public'
  AND tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY', 'UNIQUE')
ORDER BY tc.table_name, tc.constraint_name;

-- 7. Callable functions (RPC surface) and whether they run as their definer.
SELECT routine_schema, routine_name, routine_type, security_type
FROM information_schema.routines
WHERE routine_schema = 'public'
ORDER BY routine_name;
```

Before handing the results over, the owner checks two columns that can carry
literal values: `column_default` (query 1) and `qual` / `with_check`
(query 3). A policy expression may name a user, an e-mail address or a
constant; redact any such literal and say so.

Reconciliation (by the reviewer, in a PR): every live table and column is
compared with `catalogue.py`; each `UNMAPPED_LIVE_TABLES` entry either becomes a
reviewed `TableMapping` with classes for every column or stays refused; changed
columns of the catalogued tables are corrected; the RLS and privilege listings
feed the read-only role design (§«Live read-only refresh») and the access
assessment below. `CATALOGUE_STATUS` changes only when this is done.

## Access to the live project (neutral note)

A Supabase publishable (anon) key is a client key, not a service-role secret.
The owner reports (2026-10-13) that row-level security is enabled on the live
project and that SELECT is granted to authenticated roles. What any key or
identity can actually read therefore depends on Lovable's RLS policies and its
identity boundaries; that is to be assessed from the schema-only export
(queries 2–5). Nothing in Juristid relies on it: Juristid holds no Lovable
credential and makes no connection to Lovable.

## Reconciliation cost

Measured 2026-10-13 on synthetic data (local PostgreSQL 18): 2,000 current
source rows plus a 2,000-row previous snapshot, 3,000 Matters (every tenth
RESTRICTED). `build_report` issued **15 queries** for the administrator (1,710
rows shown, 290 withheld) and **13** for the department head (2,000 shown); a
test pins that 5 and 60 rows issue the same number. Wall time ~2.9 s, almost
all of it Python comparison. No per-row query.

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
   it) with `SELECT` only, on exactly the tables that have a reviewed catalogue
   entry at the time (today `public.datasets`, `public.topics`,
   `public.snapshots`, `public.snapshot_themes`, and `public.longterm_topics`
   if it still exists live; never an unmapped table), the catalogued
   columns only; no `INSERT/UPDATE/DELETE`, no RPC (`save_daily_snapshot` must
   not be callable), no `auth`/`storage` schema, RLS respected (no
   `BYPASSRLS`). **Never the service-role key**; not the publishable (anon)
   key either: it is a client key whose reach is whatever Lovable's RLS
   policies grant, which Juristid neither controls nor has assessed.
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
6. **Schema re-check.** The schema-only export has been reconciled with
   `catalogue.py`; any new table or column has a reviewed catalogue entry
   before it is read.
7. **Owner sign-off** recorded in an amendment to ADR 0157, and a
   test proving the reader issues only `SELECT` on catalogued tables.

The complete activation checklist — which also gates any import of a REAL
snapshot — is ADR 0157 §«Activation conditions».

## Promotion (none)

There is no path from a mirror value to canonical data. Any future promotion —
e.g. offering a lawyer «Lovable says the deadline is X» as a suggestion — is a
separate, explicit, reviewed product decision with its own ADR, its own
authorization and a human confirming each change through the ordinary
canonical services. NEVER_AUTO fields stay never-auto under any promotion.
