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
| [0006](0006-search-architecture.md) | Search architecture: PostgreSQL first, through a rebuildable projection | Accepted, implementation in Stage 2 |
| [0007](0007-reporting-continuity-and-export-contract.md) | Reporting continuity and the export contract boundary | Accepted, exporter in Stage 4 |
| [0008](0008-production-deployment-candidate.md) | Production deployment candidate | Proposed, deliberately reversible |
| [0009](0009-design-token-foundation.md) | Design-token foundation | Accepted |

Naming: `NNNN-short-decision-title.md`.
