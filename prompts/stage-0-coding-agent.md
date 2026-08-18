# Coding Agent Prompt — Juristid Stage 0

You are starting a new production-oriented repository for **Koda Õigusloome**, repository name `juristid`.

Your job in this conversation is **Stage 0 only**.

Do not build the product UI or Stage-1 features yet.

## Required first action

Read completely:
- `AGENTS.md`
- `docs/master-specification.md`
- `README.md`

Treat `docs/master-specification.md` as authoritative.

## Goal of Stage 0

Create a boring, durable, testable application skeleton and resolve the small number of architecture/data decisions that must be settled before the first vertical slice.

At the end of Stage 0, the repository should be ready to implement Stage 1 without architectural churn.

## Scope

### 1. Repository and development baseline

Create a clean Django modular-monolith skeleton suitable for long-term maintenance.

Use supported stable versions that satisfy the master specification. Exact versions belong in dependency files/ADRs, not product doctrine.

Expected baseline characteristics:
- Django modular monolith;
- PostgreSQL 18-compatible development environment;
- Docker Compose local development;
- server-rendered HTML + HTMX baseline;
- CSS design-token foundation for a dark-mode-first Koda CVI;
- no React SPA;
- no Redis/Celery/Elasticsearch/Kubernetes/microservices.

Create logical Django apps/modules only where Stage 0 or Stage 1 clearly needs a boundary. Do not create empty apps for every future concept.

Likely initial domains:
- accounts
- matters
- documents
- organisations
- audit
- search

Use judgment and keep it minimal.

### 2. Custom user model from migration 0001

Implement the custom User model in the first migration.

It must be designed to support Microsoft Entra identity later, including a durable Entra object ID field, while allowing a safe local-development login path using synthetic users only.

Do not build production Entra integration yet unless required to prove the abstraction.

### 3. Foundational domain schema only

Implement only schema that is either:
- explicitly required in Stage 0; or
- a cheap non-retrofittable hook required by the master specification.

Do NOT implement full Stage-1 workflows/UI.

Review and settle schema shape for at least:
- Matter identity/reference strategy;
- FULL vs ARCHIVE record mode;
- source institution vs addressee institution;
- visibility inheritance model;
- PolicyArea and narrow Tag foundations;
- SourceReference / import provenance foundation;
- retention/legal-hold fields where the specification requires early presence;
- Document / DocumentVersion metadata shape sufficient to avoid later destructive redesign;
- whether UUIDv7 is used and how it is generated/portable.

Do not create PolicyThread, Proposal, Response, WorkingGroup, DecisionRecord, semantic vectors or analytics fact tables yet.

### 4. Architecture Decision Records

Create concise ADRs for decisions that need explicit recording before Stage 1. At minimum assess and document:

1. application architecture and supported-version policy;
2. database + identifier strategy;
3. document lifecycle: immutable Blob evidence vs SharePoint working references;
4. authentication direction: local synthetic dev vs production Entra;
5. authorization/visibility inheritance;
6. search architecture direction, including rebuildable SearchDocument projection and PostgreSQL-first policy;
7. reporting-continuity/export contract boundary;
8. production deployment candidate: Container Apps vs App Service remains reversible unless evidence now clearly decides it.

Do not write long essays. ADRs should be decision records.

### 5. CI and code quality

Create CI that at minimum runs:
- dependency install;
- formatting/linting;
- Django system checks;
- migrations check;
- unit tests;
- PostgreSQL-backed test suite where database behavior matters.

Choose a small modern Python quality toolchain and document it.

Do not add tools for their own sake.

### 6. Test architecture

Create test fixtures/factories using synthetic data.

Seed tests for important future invariants even if full workflow methods land in Stage 1:
- archive and full Matters can coexist;
- source/addressee are independent;
- restricted visibility cannot become less restrictive on a child;
- historical provenance values are preservable verbatim;
- no assumption that consultation asked/responded counts form a response rate;
- dates can be nullable/unknown for archive data.

### 7. Development safety

Create:
- `.env.example` containing no secrets;
- clear local setup instructions;
- explicit warning that no production/Koda/member data may be used locally yet;
- synthetic seed command or fixture for development.

Ensure `.env` and secret-bearing files are gitignored.

### 8. CVI/design-system preparation

Do not design the application screens yet.

Create only the design-system foundation:
- semantic color tokens;
- typography tokens;
- spacing/radius/focus tokens;
- dark-mode-first architecture;
- architecture capable of a later light theme without component rewrites.

If official Koda CVI assets/tokens are not present, use clearly marked temporary semantic values and do not invent a fake final brand system.

### 9. Source snapshot/migration placeholders

Do not import historical production data.

Create the documented directory/convention and manifest format for future source snapshots and migration artifacts, while keeping actual confidential snapshots outside Git.

Document that the migration process will use per-era Excel contracts and immutable OneNote snapshots.

### 10. Reporting contract placeholder

Create a versioned contract/document describing the fields the future application must be able to export for existing Koda management reporting, as required by the master specification.

Do not build the exporter yet.

## Explicit non-goals for Stage 0

Do NOT build:
- Minu töö;
- Saabunud workflow;
- Teemad table beyond any tiny dev/admin proof needed;
- unified composer;
- Submission workflow;
- Consultation workflow;
- search UI;
- statistics dashboard;
- EIS integration;
- Outlook integration;
- Blob/Azure production deployment;
- SharePoint integration;
- Entra production auth;
- historical importer;
- AI features;
- PolicyThread;
- Proposal/outcomes;
- Response;
- working groups.

No speculative future abstractions.

## Stage-0 exit criteria

Stage 0 is complete only when:

- local `docker compose up` produces a working app + PostgreSQL environment;
- a fresh database migrates from zero successfully;
- custom User exists from the first migration;
- foundational models have tests;
- CI is green;
- no secrets are committed;
- synthetic development data works;
- key ADRs exist;
- architecture boundaries match the master specification;
- no unauthorized Stage-1+ product implementation has crept in;
- README contains exact local run/test/migration instructions;
- all changes are on a feature branch and presented as a reviewable PR.

## Working method

1. Inspect the repository and master specification.
2. Produce a short Stage-0 implementation plan and identify contradictions or genuinely blocking unknowns.
3. Resolve non-blocking details using the simplest option consistent with the specification.
4. Implement Stage 0 completely.
5. Run all tests and CI-equivalent commands locally.
6. Review your own diff for overengineering and accidental later-stage scope.
7. Commit intentionally.
8. Push the Stage-0 branch and open a PR.
9. In the final report include:
   - what was implemented;
   - ADR decisions;
   - tests executed and results;
   - deviations from the master spec, if any;
   - anything that must be decided before Stage 1;
   - exact Stage-1 starting point.

Do not merge the PR yourself unless explicitly instructed.
