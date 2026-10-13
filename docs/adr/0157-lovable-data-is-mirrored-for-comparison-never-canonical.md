# 0157 — Lovable data is mirrored for comparison, never canonical

**Status:** proposed (five-track round of 2026-10-13, track A; awaiting owner review)
**Date:** 2026-10-13

## Context

The Lovable alpha (`Kauror/cheerful-control`, a Supabase-backed dashboard built
from the same Excel register Juristid has since cut over from) is still in use
beside Juristid. The owner wants its data importable and comparable without
modifying Lovable and without letting its records become canonical. The master
specification already settles that the alpha's snapshot schema is not the
domain model (§1.5, §25.2).

No access to the live Lovable project exists from this runtime. The owner
decided on 2026-10-13 that approved read-only access will be provided later.

## Decision

1. **A separate app, `app/lovable_mirror`, with its own three tables** —
   `SourceSnapshot`, `SourceRecord`, `SourceFieldValue` — and nothing else. No
   foreign key from them to any canonical model. Additive migration
   `lovable_mirror/0001_initial` only.
2. **Immutable snapshots, idempotent by checksum.** One import is one snapshot,
   identified by the SHA-256 of the export manifest, which pins each table
   file's hash. The same bundle twice is a no-op. Rows refuse `UPDATE` in the
   database; deleting a whole snapshot remains the retention path.
3. **A mapping catalogue in code** (`catalogue.py`) classes every Lovable
   column IDENTITY / COMPARABLE / REVIEW_ONLY / NEVER_AUTO / PROVENANCE /
   LOVABLE_DERIVED; unknown columns are stored as UNCATALOGUED; unknown tables
   are refused. `jargmiseks`, `valja`, `valja_kuupaev`, `hetkeseis` and
   `snapshot_themes.source_data` are NEVER_AUTO: no NextAction, SENT
   Submission, stage transition, evidence or opinion is ever derived from them.
4. **Reconciliation is computed on read, never stored**, over
   `Matter.objects.visible_to(reader)`: matched / unmatched / ambiguous by the
   Teemaviide only (`nr` = `YYYY_N`), field changes between consecutive
   snapshots keyed by `nr` (Lovable's row ids change on every upload),
   conflicts only on COMPARABLE fields by exact rules (owner via
   `resolve_owner`), unknown never a conflict, coverage boundaries stated. The
   contract's `ReconciliationMatch` table is deliberately not built: a stored
   match would freeze a visibility- and time-dependent answer.
5. **Access.** `/haldus/lovable/`, GET only, `may_review_historical_matches`
   (the administrator), 404 for everyone else; on no navigation bar.
   `manage.py lovable_mirror_report --as <upn>` gives the same report for a
   person allowed to see the page.

   5a. **Fail-closed visibility of source text** (amended in round 2 of
   2026-10-13). Opening the page is not business access: technical
   administration does not imply business-content access (AGENTS.md). Each
   `nr` is looked up against all Matters in one privileged internal step whose
   answer is never shown. A row whose `nr` belongs to a Matter is shown only
   if that Matter is `visible_to(reader)`. A row with no Matter (or no valid
   `nr`) is shown only to a reader whose scope reads every RESTRICTED Matter —
   on this page, an administrator under an active break-glass grant. Every
   other row is withheld before presentation and counted only, as
   «N rida varjatud: puudub õigus vastava teema sisu nägemiseks», with no
   `nr`, title, field or match class; restricted-Matter rows and Matter-less
   rows share the count so it reveals no restricted Matter. `nr` is treated
   as business content: shown only with a shown row. The same rules bind the
   change lists and `lovable_mirror_report` (text and JSON).
6. **No promotion path.** Turning any mirror value into canonical data needs a
   new, explicit, reviewed decision recorded as an amendment or a new ADR.
7. **Sources.** v1 is an operator-provided export bundle read from outside the
   checkout; a bundle marked `REAL` is refused unless `REAL_DATA_ALLOWED`. The
   refresh seam is a `SourceReader` protocol. `LiveReadOnlyReader` exists as a
   disabled stub: it refuses in every configuration, makes no connection, and
   it may be built or enabled only when every activation condition below
   holds; the access shape is in `docs/lovable-mirror.md`.
8. **Catalogue status.** The catalogue is derived from the August 2026
   repository; the live schema is NOT verified. The owner observed on
   2026-10-13 that the live project contains `topic`, `topics`,
   `topic_change_event`, `topic_change_field`, `topic_commencement`,
   `topic_responsible_change`, `topic_link`, `snapshots`, `snapshot_themes`
   and `datasets`. The six tables absent from the repository are known live
   tables with UNKNOWN columns: placeholders marked NOT MAPPED, no invented
   columns, and the importer refuses them (as any uncatalogued table) with a
   message pointing to the schema-only export procedure in
   `docs/lovable-mirror.md`.

## Activation conditions

All of the following must be true before **(a)** any import of a real
(`REAL`) Lovable snapshot and **(b)** any live reader is built or enabled.
Each is checked and recorded in the owner's sign-off amendment.

1. The approved Koda-controlled real-data environment passes the secure pilot
   gate (`docs/secure-pilot-gate.md`).
2. The fail-closed visibility rule (§5a) has been reviewed, including the
   owner decision on who may see rows with no Matter.
3. The schema-only export of the live project has been run by the owner and
   reconciled with `catalogue.py`; every table that will be imported or read
   has a reviewed catalogue entry and every other table stays refused.
4. The retention period for imported snapshots is decided, with its deletion
   procedure.
5. The owner has signed off by an amendment to this ADR.
6. The read-only role meets the specified shape: SELECT only, on reviewed
   catalogued tables and columns only, no write, no RPC, no `auth`/`storage`
   schema, no `BYPASSRLS`; never the service-role key, not the publishable key.
7. The egress allow-list from the approved host to the one Lovable endpoint
   is in place, with no inbound path.

Until then the code enforces what it can: a `REAL` bundle is refused unless
`REAL_DATA_ALLOWED`, and `LiveReadOnlyReader` refuses in every configuration.

## Consequences

* The default administrator (no break-glass grant) sees only rows about
  Matters they may read; rows about RESTRICTED Matters and rows with no Matter
  are withheld and counted. The «Sidumata» list is therefore empty for them
  and says why. Owner decision pending: keep break-glass as the only way to
  see rows with no Matter, open the page to the department head, or add a
  dedicated capability.
* The catalogue reflects the repository schema of 2026-08-17 and is not the
  complete live schema; see §8.
* Lovable access posture is not assumed. A publishable key is not a
  service-role secret; the owner reports RLS enabled with SELECT granted to
  authenticated roles. What any identity can read depends on Lovable's RLS
  policies and identity boundaries, to be assessed from the schema-only
  export. Nothing in Juristid relies on it.
* Reconciliation is bounded: a constant number of queries (15 at 2,000 rows
  and 3,000 Matters), no per-row query.
* Writes to `config/settings.py` (one INSTALLED_APPS line) and
  `config/urls.py` (one include) are the only edits outside the app, plus the
  three trigger names in `tests/test_schema_completeness.py`.
