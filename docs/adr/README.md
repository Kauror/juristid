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

Naming: `NNNN-short-decision-title.md`.

## Stage coverage

- 0001–0009 — Stage 0 foundation
- 0010–0011 — Stage 1 vertical slice
- 0012–0013 — Stage 2A import and search foundation
- 0014 — Stage 2B evidence and content intelligence
- 0015–0016 — Stage 2D historical corpus and authentication modes
- 0017 — Stage 2E statistics and reporting
- 0018 — Stage 2G structured Matter facts
