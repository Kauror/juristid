# AGENTS.md — Juristid / Koda Õigusloome

## Authority

`docs/master-specification.md` is the authoritative product and architecture specification.

Read it before planning or modifying code.

If implementation pressure conflicts with the master specification:
1. do not silently change the product model;
2. document the conflict;
3. propose the smallest correction;
4. record an ADR for material architecture changes;
5. require explicit approval before proceeding with a material departure.

## Stage discipline

Work only on the currently authorized stage.

Do not implement later-stage features early unless they are a cheap schema hook explicitly required by the master specification to prevent a future rewrite.

The current authorized stage at repository creation is **Stage 0 only**.

## Product principles

- The system owns the policy record, not every productivity activity around it.
- Routine work must be faster than Excel + OneNote double entry.
- Preserve uncertainty and provenance; never manufacture historical certainty.
- One canonical Matter model covers current and archive records.
- Stage, disposition and next action are separate concepts.
- Submission is first-class MVP data.
- Blob is the immutable evidence store; SharePoint may be referenced for mutable collaborative working documents.
- Search is a core product capability.
- Statistics must be drillable and coverage-aware.
- Advocacy outcome and attribution are separate concepts.
- Dark-mode-first Koda CVI is the production visual direction. Do not copy Lovable's UI.

## Architecture constraints

Preferred architecture characteristics are:
- Django modular monolith;
- supported PostgreSQL with required Estonian search capability;
- server-rendered HTML + HTMX by default;
- minimal isolated JavaScript/TypeScript islands only where UX requires client-side state;
- Docker portability;
- Azure production target;
- Entra identity;
- canonical relational data in PostgreSQL;
- immutable evidence binaries outside PostgreSQL.

Do not introduce without measured need and explicit approval:
- microservices;
- Kubernetes;
- React SPA/API split;
- Redis;
- Celery;
- Elasticsearch/OpenSearch;
- generic workflow/BPM engine;
- separate BI warehouse;
- generic CRM;
- custom web-based Office editor.

## Data and security

- Never commit secrets or `.env` files containing secrets.
- Never use real Koda/member/confidential data in local development before the Secure Pilot Gate.
- Use migrations for every schema change.
- Preserve immutable import provenance.
- Enforce authorization centrally, including search, exports, counts and statistics.
- Restricted child records may be more restrictive than their Matter, never less restrictive.
- Technical administration does not imply unrestricted business-content access.

## Engineering quality

- Prefer boring, explicit, well-tested code over clever abstractions.
- Keep domain rules in named services/use cases rather than views/templates.
- Add tests for every material business rule.
- Add migration/import fixtures for historical edge cases.
- CI must run before merge.
- Keep commits focused and reviewable.
- Do not rewrite published Git history.
- Use feature branches and PRs for implementation work.

## UX quality

- Estonian UI.
- Fast and dense, but not cluttered.
- Every keyboard shortcut must have an obvious click equivalent.
- Avoid dashboard decoration without a decision-making use.
- Prefer actionable lists over vanity metrics.
- Do not make routine capture slower by requiring optional metadata.

## Definition of done

A feature is not done because it renders. It is done when:
- business rules are correct;
- permissions are tested;
- happy path and important edge cases are tested;
- migrations are safe;
- accessibility basics are respected;
- failure/empty/loading states are handled;
- documentation/ADR is updated when applicable;
- CI is green.
