# 0002 — Database and identifier strategy

- **Status:** Accepted (Stage 0)
- **Date:** 2026-08-18

## Context

PostgreSQL 18+ is locked by the master specification because the Estonian
full-text search configuration must exist at launch. Stage 0 also has to settle
how records are identified, because primary keys and the human `YYYY_N`
reference cannot be changed later without a painful migration.

## Decision

**Database**

- PostgreSQL **18 or later**, single canonical database, no read replica, no
  second store for analytics.
- `pg_trgm` and `unaccent` are installed by `app/search/migrations/0001`.
- The Estonian text-search configuration, the extensions and the server major
  version are verified by `manage.py check_search_capabilities` and by
  `tests/test_search_capabilities.py`, so a database that cannot support the
  product fails CI rather than Stage 2.
- `MINIMUM_POSTGRESQL_VERSION` in settings states the baseline in code.
- Major-version upgrades are a planned operation with a restore-tested
  rehearsal; the application does not depend on 18-only syntax beyond the text
  search configuration.

**Primary keys: UUIDv7, generated in Python**

- Every domain table uses a `UUIDField` primary key populated by
  `app.core.ids.uuid7` (RFC 9562 version 7: 48-bit millisecond timestamp then
  randomness).
- Generation is application-side, not `uuidv7()` in PostgreSQL, so that the
  identifier exists before INSERT, fixtures and imports are reproducible, and
  the scheme does not bind the product to one database's function set.
- `app.core.ids.uuid7` delegates to `uuid.uuid7` when the runtime provides it
  (Python 3.14+), so the implementation disappears as the baseline moves.
- Time-sortable keys keep index locality reasonable at the expected scale
  (12,000+ Matters, 150,000+ document versions) without exposing a guessable
  sequential identifier.

**Human reference: `YYYY_N`, derived not stored**

- `Matter.reference_year` and `Matter.reference_number` are separate nullable
  integer columns.
- `display_reference` is a **Python property**, not a column. The specification
  requires one canonical storage location per fact; storing the formatted string
  as well would create a second, editable copy.
- A partial unique constraint enforces uniqueness whenever both parts are
  present; a check constraint requires them to be set together.
- Archive rows may have no reference at all. Nothing invents one.
- Allocation goes through `allocate_matter_reference()`, which takes a row lock
  on a per-year sequence table.

**Open business decision.** The numbering rule itself (per-year restart,
successor reference handling) is owned by the department head and is still open.
It is isolated in one function precisely so the decision can land cheaply.

## Alternatives considered

- **BigAutoField primary keys** — simpler, but exposes record counts, and makes
  merging imported data and offline identifier assignment harder.
- **UUIDv4** — no time locality; measurably worse index behaviour at this scale
  for no benefit.
- **`display_reference` as a stored generated column** — attractive for search,
  but Django's `Concat` coalesces NULLs, so a partial reference would render as
  `_`. If search later needs an indexed reference column, it belongs in the
  `SearchDocument` projection (ADR 0006), not on `Matter`.

## Consequences

- Identifiers are portable across environments and safe to mint offline during
  import.
- The human reference remains exactly what lawyers already recognise.
- One extra table (`MatterReferenceSequence`) and one locked read per Matter
  creation.

## Reversibility

Primary-key strategy: low reversibility, which is why it is settled now.
Numbering rule: high — one function.
