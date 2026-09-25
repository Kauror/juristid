# 0116 — Search answers from its indexes, and every result is reachable

**Status:** accepted
**Date:** 2026-09-25

**Resolves** the open decision «Global-search behaviour at real corpus scale»
(`docs/open-decisions.md`), which waited for exactly the measurement recorded
here. **Amends ADR 0014** on one point: the trigram indexes are no longer
partial. The ranking of ADR 0006 and the authorization of ADR 0005 and 0013 are
unchanged, and a test holds that row for row.

## Context

Two findings about global search, both confirmed on the code as of Round 5
(main `ad1593a1`, `INDEX_VERSION` `DOKUMENT.1`).

* **ENG-010 — every search scanned the whole projection.** `_build()` ORed the
  tiers into one WHERE clause, and several arms could not be served by an index:
  * `title_exact` tested the *joined* `matters_matter.title`, so the whole OR
    became a filter applied after joining every row;
  * the fuzzy tier filtered on a `word_similarity` annotation, which the trigram
    indexes cannot answer;
  * three substring tiers used Django's `icontains`, which compiles to
    `UPPER(column) LIKE UPPER('%…%')` — nothing indexes `UPPER(column)`.

  One unindexable arm makes an OR unindexable. The page statement then ranked
  every matching row while carrying about 260 columns (eleven `select_related`
  tables, the body and both vectors) through the sort, and `/otsing/` ran a
  second, whole-search `COUNT`. A READER's or an administrator's page joined the
  collaborators and de-duplicated with `DISTINCT` over all those columns.

  Measured locally before the change, PostgreSQL 18 with default settings, on
  synthetic corpora (`round6-search-benchmark.md`, kept with the session, not
  committed):

  | corpus | rows | specialist, rare word | specialist, no hit | READER, rare word |
  | --- | --- | --- | --- | --- |
  | SMALL | 17,126 | 295 ms | 286 ms | 3,451 ms |
  | LARGE | 81,633 | 746 ms | 340 ms | 902 ms |
  | IMPORT | 146,850 | 1,445 ms | 625 ms | 1,861 ms |

  Every search statement was a sequential scan; no text index was used. On the
  small corpus the READER's page spent over four seconds *planning* the
  `DISTINCT`.

* **ENG-048 — results stopped at fifty.** `MAX_RESULTS = 50` sliced the ranking
  and the view took no page parameter, so the fifty-first result could not be
  reached from global search.

## Decision

**Every arm of the matching predicate is one an index serves**, on the
projection's own columns (`app/search/services.py`, `_build`).
* Substrings use `ILIKE` (a `contains_any_case` lookup registered on the three
  trigram-indexed columns), escaped exactly as `icontains` escapes.
* The exact-title arm is narrowed through the projection's own title, which a
  MATTER row's title begins with, and compared with the Matter's title through
  an uncorrelated `matter_id IN (…)` that PostgreSQL evaluates once.
* The fuzzy arm uses the `%>` operator, which the trigram index serves, and then
  the tier's own `word_similarity >= 0.6`. `check_search_capabilities` refuses a
  server whose `pg_trgm.word_similarity_threshold` is above 0.6, because the
  operator would then drop matches before the rule saw them.

**One statement per question, and the ranking statement is slim.**
* **The page:** a single statement returns ids, tiers and relevance in rank order
  for one page (`values_list`), then a second fetches those rows with what they
  render (`_present`), and a third the excerpts, as before.
* **The count:** a plain `COUNT(*)` over the same filtered queryset, with no
  ranking, sort or presentation join. It stays exact, and PostgreSQL can run it
  in parallel.

**Participation by subquery, and no `DISTINCT`.**
`projected_visibility_q(…, participation_by_subquery=True)` reaches the
collaborators through `matter_id IN (SELECT matter_id FROM the through table
WHERE user_id = …)`. It cannot multiply rows, so `visible_documents` filters
without `.distinct()`. The rule it expresses is unchanged; the join form stays
the default for every other caller.

**Trigram indexes over every row** (migration `search/0011`).
* **The indexes:** `search_title_trgm`, `search_identifiers_trgm` and
  `search_alias_trgm` replace the two partial MATTER-only ones, so the substring
  tiers for child titles, document names and aliases have an index.
* **Their size:** at 82,000 rows all three together are about 16 MB.
* **How they are built:** created `CONCURRENTLY` (a non-atomic migration), new
  ones before old ones are dropped, so the release's migrate step blocks no
  write.

**Pagination** reuses the register's component and `leht` parameter.
* **Offset pagination** over an order that ends in the primary key, so pages
  neither repeat nor skip a row.
* **Malformed input:** `Paginator.get_page` is the policy for it — anything
  that is not a page number is the first page, and a number past the end is the
  last.
* **`RESULTS_PER_PAGE = 50`** replaces `MAX_RESULTS`.

## Consequences

**Selective searches are served by their indexes.**

| corpus | query | before | after |
| --- | --- | --- | --- |
| IMPORT | specialist, rare word | 1,445 ms | 140 ms |
| IMPORT | specialist, no hit | 625 ms | 28 ms |
| IMPORT | specialist, fuzzy title | 1,474 ms | 128 ms |
| IMPORT | specialist, document name | 1,456 ms | 341 ms |
| IMPORT | register, rare word | 1,581 ms | 325 ms |
| SMALL | READER, rare word | 3,451 ms | 117 ms |

All figures are HTTP medians. The SQL for the rare word falls from about
1,260 ms to about 100 ms, and the buffers it touches by roughly 50×.

**Broad terms are unchanged, and that is the right plan.** A word in half the
corpus («eelnõu» matches 40,000 of 82,000 rows) is still answered by one
parallel pass for the count and one for the ranked page: about 1.4 s at IMPORT
scale, the same as before. Ranking that many rows needs every one of them; an
index would only add random reads.

**What each query means is unchanged.**
* `tests/test_search_differential.py` runs the query as it stood before
  (`tests/search_reference.py`, frozen) and the new one over the same corpus:
  66 queries, every persona. It requires the same rows, order, tiers and
  relevance.
* `tests/test_search_plan_shape.py` holds the plan contract: with sequential
  scans switched off, the projection is reached through its text indexes and
  nothing else, and no plan de-duplicates rows.

**Two narrow semantic edges.**
* **Case folding:** `ILIKE` folds case character by character; `UPPER(…) LIKE`
  upper-cases whole strings. They differ only for characters whose case mapping
  is not one-to-one (Turkish dotless ı, for example). The differential corpus,
  which is Estonian, shows none.
* **A stale projection:** the exact-title arm now also requires the projection's
  own title to contain the term. A MATTER row always begins with its Matter's
  title, so the two can differ only when the projection is stale — which
  freshness and `check_search_integrity` report.

**A failed concurrent build leaves an INVALID index.** Running `migrate` again
does not repair it. The recovery is `DROP INDEX CONCURRENTLY <name>;` followed by
`migrate`. The projection itself is untouched either way, and no rebuild is
needed for this migration: it changes indexes, not rows.

`INDEX_VERSION` is unchanged by this decision.

## Alternatives considered

* **A `UNION` of per-tier candidate sets in a CTE.** Measured, and each branch
  was index-served. For a broad word, though, each full-text branch became its
  own sequential pass: three passes instead of one (1.8 s against 1.2 s cold at
  LARGE). An OR whose every arm is indexable lets PostgreSQL choose a BitmapOr
  or a single pass as the statistics warrant.
* **The count as `COUNT(*) OVER ()` in the page statement.** One statement
  instead of two, but a window function above the scan forbids a parallel plan.
  Measured at LARGE, that cost about 400 ms more for a broad word than the
  separate, parallel count.
* **A capped count («üle 1000»).** Cheaper for broad words, but a numbered
  pagination over a capped total cannot reach the results past the cap, which
  is ENG-048 again.
* **Keyset pagination.** The first sort key is a float relevance and the
  archive rows have no reference, so a cursor would carry five values, one of
  them a float. Offset pagination over a bounded, indexed candidate set is
  sufficient at this scale; a deep page of a broad word costs what its count
  costs.
