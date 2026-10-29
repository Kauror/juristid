# ADR 0175 — Archive statistics follow the archive reader rule, and import summaries are allowlisted

- Status: Proposed (security remediation, 2026-10-22)
- Findings: csf_a21a2d1c9c6d746327ed27e9, csf_1cecc4b5be8fb1a7be4fe125,
  csf_31caadb347feb9939db2cd4e (Codex Security scan of 4818203e)

## Context

**Statistika counted the opinion archive for everyone.** `may_read_archive`
(app/legacy_import/opinion_access.py, ADR 0028/0042) is all-or-nothing on
purpose — «a reader who may see the coverage figures can infer the corpus» —
and every archive page asks it. The reporting selectors in
`app/reporting/selectors/archive.py` and `opinions.py` queried
`OpinionArchiveItem` directly, on the stated rule that unlinked inventory
«names no Matter» and is «counted for everyone». A READER and a shared-gate
viewer with no persona, both refused by the archive, read its size, distinct
files, years, link coverage and reconstruction coverage on Ülevaade-adjacent
tabs.

**The legacy import's «publishable» summaries carried source content.**
`summary.json`/`summary.md` held the workbook's file name and SHA-256, sheet
names, duplicate references, structural findings quoting header cells,
per-sheet status values and the workbook's vocabulary terms. The row-level
CSVs wrote workbook cells into Excel-bound files without the formula
neutralisation every Statistika export already applies.

## Decision

1. **Every archive metric is guarded by the archive's own rule.** A selector
   that reads the archive is registered with
   `app.reporting.archive_access.reads_opinion_archive`; the registration is the
   guard and refuses (`ArchiveMetricWithheld`, a `PermissionDenied`) before any
   query for a viewer `may_read_archive` refuses. `services.ARCHIVE_METRICS` is
   derived from the registrations. `services.compute_many` leaves a withheld
   metric out, so the tabs show no archive card, chart or section — absent, not
   zero — and the Tegevus archive section is not rendered. Archive readers'
   numbers are unchanged; within the archive the Matter-naming numerators stay
   scoped by Matter visibility (AUD-044). No Statistika CSV carries archive
   figures; none did before either.
2. **Import summaries are a typed, allowlisted schema.** `summary.json` and
   `summary.md` are written only from `PublicSummary`
   (app/legacy_import/reporting.py): named counts, parser/importer/contract
   version stamps and three yes/no flags; breakdowns only by this application's
   own codes (`RowOutcome`, `Anomaly`, `ProposedRecordMode`). Anything else is
   refused, not dropped. The full detail moves to local-only
   `operator-summary.json`/`.md`, beside the row-level CSVs.
3. **Every import CSV cell is neutralised.** `_write_csv` and the owner-backfill
   report pass header and rows through `csv_safety.csv_safe_row`, which now looks
   through any leading whitespace, control or format character before deciding
   whether a cell starts a formula. Non-string values are untouched.

## Consequences

- A READER or a no-persona shared-gate viewer sees Statistika without the
  opinion-archive figures. Widening `ARCHIVE_READERS` widens browse and
  Statistika together.
- Anything that consumed `summary.json`'s old shape must read
  `operator-summary.json` locally instead; nothing in the repository did.
- No migration, setting, compose or deployment change.

## Reversibility

Code-only. Removing the decorator from a selector, or adding a field to
`PublicSummary`, is a deliberate, reviewable change with a failing test first.

## Status update 2026-10-29 — merged and deployed

This section records what happened after the decision; it does not amend the decision, and the status is unchanged (no formal acceptance was recorded).

- **Merged** in the security remediation integration (merge `9a2e834a, #514 = #506–#513, 2026-10-22`).
- **Deployed** to production as `1270ca01` (2026-10-23, the release of #521, which carried the security baseline merged before it).
