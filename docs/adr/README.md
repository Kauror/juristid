# Architecture Decision Records

ADRs record material technical decisions and any intentional departure from
`docs/master-specification.md`. The master specification locks capabilities and
invariants; ADRs lock the current implementation choices.

Each ADR states status, context, decision, alternatives considered,
consequences and reversibility.

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-application-architecture-and-versions.md) | Application architecture and supported-version policy | Accepted |
| [0002](0002-database-and-identifier-strategy.md) | Database and identifier strategy | Accepted |
| [0003](0003-document-lifecycle.md) | Document lifecycle: immutable evidence and mutable working documents | Accepted |
| [0004](0004-authentication-direction.md) | Authentication: synthetic local sign-in now, Entra ID for real data | Accepted |
| [0005](0005-authorization-and-visibility-inheritance.md) | Authorization and visibility inheritance | Accepted |
| [0006](0006-search-architecture.md) | Search architecture: PostgreSQL first, through a rebuildable projection | Accepted, implemented in Stage 2A |
| [0007](0007-reporting-continuity-and-export-contract.md) | Reporting continuity and the export contract boundary | Accepted, exporter in Stage 4 |
| [0008](0008-production-deployment-candidate.md) | Production deployment candidate | Proposed, deliberately reversible |
| [0009](0009-design-token-foundation.md) | Design-token foundation | Accepted |
| [0010](0010-stage-1-interaction-and-browser-testing.md) | Stage-1 interaction model and browser testing | Accepted |
| [0011](0011-next-action-and-submission-modelling.md) | NextAction and Submission modelling | Accepted |
| [0012](0012-legacy-register-import.md) | Legacy register import architecture | Accepted |
| [0013](0013-search-projection-and-child-content.md) | The search projection, and why child content waits | Accepted |
| [0014](0014-content-extraction-and-derivatives.md) | Content extraction and rebuildable derivatives | Accepted |
| [0015](0015-historical-corpus-integration.md) | Historical corpus integration | Accepted |
| [0016](0016-authentication-modes-and-the-shared-gate.md) | Authentication modes and the shared gate | Accepted |
| [0017](0017-statistics-and-the-metric-catalogue.md) | Statistics, the metric catalogue and operational snapshots | Accepted, on a feature branch |
| [0018](0018-structured-matter-facts.md) | Structured Matter facts, and the generated department views | Accepted, on a feature branch |
| [0019](0019-opinion-archive-reconciliation.md) | Reconstructing historical submissions from the opinions archive | Accepted, on a feature branch |
| [0020](0020-historical-cutover-current-state.md) | The historical cutover, and what a closed archive row may claim | Accepted, on a feature branch |
| [0021](0021-final-register-cutover.md) | The final register cutover, and the two columns that mean different things | Accepted, on a feature branch |
| [0022](0022-deployment-backup-and-recovery.md) | Deployment, backup and recovery on the Unraid host | Accepted |
| [0023](0023-searchable-opinion-archive.md) | Making the whole opinions archive searchable evidence | Accepted, on a feature branch |
| [0024](0024-test-data-classification.md) | Test data is a stored class on the Matter, and purging it is a later decision | Accepted, on a feature branch |
| [0025](0025-multiple-matter-senders.md) | A Matter has zero, one or several senders; the addressee stays singular | Accepted |
| [0026](0026-source-data-enrichment.md) | Source facts are never rewritten; interpretation is added on top of them | Accepted, on a feature branch |
| [0027](0027-matter-engagement.md) | `Kaasamine` is a pointer to outreach, not an engagement system | Accepted |
| [0028](0028-development-archive-workspace-access.md) | The archive is readable behind the shared gate; the register is not | Accepted |
| [0029](0029-reference-data-foundation.md) | Reference data is governed, additive, and never invented from source strings | Accepted |
| [0030](0030-teema-workspace-redesign.md) | The Teema workspace: one page, two tabs, and one save per professional update | Accepted |
| [0031](0031-teema-human-qa-corrections.md) | What hands-on use changed about the Teema workspace | Accepted |

Naming: `NNNN-short-decision-title.md`.

## Stage coverage

- 0001–0009 — Stage 0 foundation
- 0010–0011 — Stage 1 vertical slice
- 0012–0013 — Stage 2A import and search foundation
- 0014 — Stage 2B evidence and content intelligence
- 0015–0016 — Stage 2D historical corpus and authentication modes
- 0017 — Stage 2E statistics and reporting
- 0018 — Stage 2G structured Matter facts
- 0019 — Stage 2H opinion archive and historical submissions
- 0020 — Stage 2I historical cutover state
- 0021 — the final register cutover
- 0022 — deployment, backup and recovery on the host the system actually runs on
- 0023 — Stage 2H.2, the searchable opinion archive
- 0024 — real/test data classification and the purge plan that precedes a purge
- 0025 — multiple Matter senders, and the addressee that stays singular
- 0026 — Wave 2 source-data enrichment: JÄRGMISEKS, OneNote filing structure and historical activity
- 0027 — Wave 2 Kaasamine: how members and stakeholders were asked
- 0028 — the development archive workspace: who may read the corpus behind the shared gate, and why that is not the register
- 0029 — the reviewed reference-data baseline: nine policy areas, the core public institutions, and why no backfill yet
- 0030 — the approved Teema workspace redesign: two tabs, one composer save, grouped-not-merged history, and the twenty-three working Valdkonnad
- 0031 — what a working session on real data changed: the position back in the rail, one dated work list, a full edit page beside the inline controls
- 0032 — the approved Uus teema redesign: one screen, one chip control, two Valdkonnad withdrawn, and Hetkeseis explaining itself on the row
- 0033 — Ülevaade drill-down parity: a number opens a list of its own kind, `?too=` makes the dated-work populations addressable, and "Ootab pahavarakontrolli" stops telling readers their archive might be infected
