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
6. **No promotion path.** Turning any mirror value into canonical data needs a
   new, explicit, reviewed decision recorded as an amendment or a new ADR.
7. **Sources.** v1 is an operator-provided export bundle read from outside the
   checkout; a bundle marked `REAL` is refused unless `REAL_DATA_ALLOWED`. The
   refresh seam is a `SourceReader` protocol. `LiveReadOnlyReader` exists as a
   disabled stub: it refuses in every configuration, makes no connection, and
   its prerequisites (a SELECT-only role on catalogued tables, never the
   service-role or publishable key, run only in the approved real-data
   deployment, egress-allow-listed, secret outside Git, owner sign-off) are in
   `docs/lovable-mirror.md`.

## Consequences

* A reader-visible population means the administrator sees a row about a
  RESTRICTED Matter as unmatched. Lovable's own text for that row (the Excel
  title) is still shown to the administrator, because it is source content
  rather than Juristid content — an owner decision may narrow that.
* The catalogue reflects the repository schema of 2026-08-17. The tables
  `topic_change_event`, `topic_change_field`, `topic_commencement`,
  `topic_responsible_change` and `topic_link` were not in it and are refused
  until catalogued.
* Writes to `config/settings.py` (one INSTALLED_APPS line) and
  `config/urls.py` (one include) are the only edits outside the app, plus the
  three trigger names in `tests/test_schema_completeness.py`.
