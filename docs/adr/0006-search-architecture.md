# 0006 — Search architecture: PostgreSQL first, through a rebuildable projection

- **Status:** Accepted (Stage 0), implementation scheduled for Stage 2
- **Date:** 2026-08-18

## Context

Search is a core product capability over Estonian legal text. The specification
locks PostgreSQL as the first search engine and requires a rebuildable
`SearchDocument` projection so ranking and indexing are decoupled from the
transactional schema.

## Decision

**Engine:** PostgreSQL 18+, using:

- the `estonian` full-text search configuration for stemmed matching;
- `simple` tokens in parallel for identifiers and exact terms;
- `pg_trgm` for short strings, filenames and typo tolerance;
- `unaccent` so diacritic-free input matches;
- normalised exact-identifier columns;
- controlled tag and organisation aliases.

Stage 0 installs the extensions and **proves the capability**: the Estonian
configuration, the extensions and the server version are asserted in
`tests/test_search_capabilities.py` and by `manage.py check_search_capabilities`.
If the Estonian configuration turns out to be unavailable on the deployment
target, that is a Stage-0 finding, not a Stage-2 surprise.

**Projection:** `SearchDocument` is a derived, rebuildable table — one row per
searchable source (Matter title/summaries, Entry, Submission, Consultation
summary, document-derived text, external references) carrying source kind,
source object id, Matter id, source locator, tsvectors and the effective
visibility scope. It is never authoritative business data and can always be
rebuilt from canonical records.

It is **not implemented in Stage 0**: the sources it projects (Entry,
Submission, Consultation, DocumentDerivative) do not exist yet, so the table
shape would be guesswork. Stage 2 builds it together with them.

**Authorization:** search results, snippets and counts pass through the same
chokepoint as every other read (ADR 0005). The projection stores effective
visibility so the scope can be applied inside the search query rather than
filtered afterwards.

**Ranking order** (specification 14.3), deterministic before fuzzy: exact Matter
reference → exact official identifier → normalised title → tag/organisation
alias → Estonian FTS → simple/phrase → trigram fallback.

**External engine:** not adopted. Elasticsearch/OpenSearch is evaluated only if,
after tuning, one of the specification's measured triggers is met. The
projection exists so that such a migration would be incremental.

## Consequences

- No search infrastructure to operate at pilot scale.
- Ranking changes do not require transactional schema migrations.
- A maintained lawyer query corpus (≥30 real queries) becomes the regression
  suite before the real pilot; it is a Stage-2 deliverable.

## Reversibility

High. That is the point of the projection.
