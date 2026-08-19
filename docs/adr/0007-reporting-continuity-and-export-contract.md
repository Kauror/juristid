# 0007 — Reporting continuity and the export contract boundary

- **Status:** Accepted (Stage 0), exporter scheduled for Stage 4
- **Date:** 2026-08-18

## Context

Koda's existing management reporting is fed from the Excel register. The new
system replaces that feed only after a parallel reconciliation with zero
unexplained differences on the agreed fields. Stage 0 must fix the contract
boundary so Stage 1 does not build a schema the export cannot serve.

## Decision

**A versioned document, not code, in Stage 0.** The contract lives at
`docs/data-contracts/dashkoda-export-v1.md`. No exporter is built yet.

**Delivery shape:** a versioned file (CSV or JSON) or an authenticated endpoint
agreed with the DashKoda owner. No event streaming, no warehouse, no BI cube at
this scale.

**Compatibility fields are derived, never stored.** The single "opinion sent
date" that the current register carries is computed from `Submission` records
by a documented, versioned rule. It is not a canonical column on `Matter`,
because one Matter may produce several submissions and a stored copy would drift.

**Coverage is part of every number.** A metric declares its source population,
eligibility, exclusions, earliest reliable period and completeness threshold. If
completeness is not met, the export and any dashboard show *insufficient data*
rather than a precise-looking figure. The catalogue template is at
`docs/metric-catalog/README.md`.

**Prohibited outputs.** The contract must never express lawyer productivity,
"workload" from open Matter counts, a legacy consultation response rate, a Koda
win rate, causal attribution percentages or a ministry success ranking.
`tests/test_reporting_contract.py` fails if the contract document acquires a
response rate, and if any model gains a field that looks like a stored derived
metric.

**Consultation counts stay independent.** The register's contacted and response
counts are separate observations with no guaranteed common denominator. They are
exported as two independent numbers with their provenance, never as a ratio.

**Reconciliation.** Before cutover, old and new exports are generated for the
same population, diffed field by field, and every mismatch classified as
expected mapping difference, source anomaly or defect. The report is stored with
the migration evidence.

## Consequences

- Stage 1 knows which fields must survive to the export and can be reviewed
  against the contract.
- The DashKoda owner has something concrete to agree to now (an open business
  decision), long before the exporter exists.

## Reversibility

High for delivery format and field set (the contract is versioned). Low for the
principle that compatibility fields are derived from `Submission`.
