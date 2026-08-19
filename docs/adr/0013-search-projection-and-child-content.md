# ADR 0013 — The search projection, and why child content waits

- Status: accepted
- Date: 2026-08-19
- Stage: 2A
- Related: ADR 0005 (authorization), ADR 0006 (search architecture), ADR 0012

## Context

ADR 0006 committed Stage 2 to a rebuildable `SearchDocument` projection over
PostgreSQL's Estonian configuration. Stage 1 shipped a placeholder that did
`icontains` over titles and organisation names. Stage 2A replaces it.

Two questions had to be settled: what the projection stores about
authorization, and whether Entry and Submission text is indexed now.

## Decision

### The projection stores no visibility

The master specification's sketch of `SearchDocument` (11.3) lists an
"effective visibility scope" field. **It is not implemented, deliberately.**

Stage 0 removed a stored `effective_visibility` column from child records for
exactly this reason (ADR 0005): a denormalised authorization value has to be
kept in step with every change to its parent, and any write that bypasses the
service maintaining it leaves a stale value that reads as *less* restrictive
than the truth.

A search projection makes that worse, not better. It is refreshed *after* the
fact by definition. Between restricting a Matter and reindexing it there would
be a window in which the index says NORMAL and the truth says RESTRICTED — and
that window is precisely when somebody is most likely to search for it.

So every query joins the live Matter and evaluates
`app.core.authorization.matter_visibility_q` against that. Restricting a Matter
takes effect on the very next query with no reindex, which is asserted by a test
that deliberately does not rebuild.

The cost is one join per search. At the target scale — 2,455 matters today,
design headroom to 12,000 — this is not measurable. If it ever were, the answer
is a better index on the join, not a cached permission.

### Authorization is applied before the term, not after

`visible_documents(user)` returns the scoped queryset, and everything else in
the module builds on it. Filtering, ranking, counting and slicing all happen
inside that scope. A restricted Matter cannot influence a result count, a page
boundary or a ranking position.

The count deserves saying out loud: "your search matched 4 things and you may
see 3" discloses the fourth as surely as showing its title would. So
`result_count()` runs the same authorized queryset the rows came from, and the
view uses it instead of the length of the rendered page.

### Ranking is deterministic tiers, then relevance inside a tier

Exact reference (100) → exact title (90) → title phrase (80) → organisation,
policy-area or tag alias (70) → Estonian full text (60) → simple tokens (50) →
trigram fallback (40). The gaps are wide so a strong `ts_rank` inside one tier
can never overtake the tier above it. Within a tier, `ts_rank` decides, reading
the weights set at index time: title A, identifiers B, aliases C, body D.

Nothing is ranked by how often a record is opened or by who owns it. A search
that quietly favours popular matters is a search that hides the neglected ones,
and the neglected ones are what a legal department most needs to find.

The whole thing is **one SQL statement**. Running the tiers as separate queries
and stitching them in Python would make the result count depend on Python-side
deduplication, and a count computed anywhere other than the database is a count
that can disagree with the rows it accompanies.

### Trigram indexes on short strings only

`title` and `identifiers` get `gin_trgm_ops`. `body_text` deliberately does not.
Trigram-indexing extracted document text is how a PostgreSQL search installation
becomes unmaintainable (specification 14.1), and Stage 2B will add exactly that
kind of text.

### Entry and Submission content is deferred to Stage 2B

**This is the load-bearing decision in this ADR.**

Stage 2A indexes Matter-level content: reference, title, alternate titles,
canonical organisation names and their aliases, policy areas, and confirmed tag
names and aliases. That is enough to validate the architecture against the
historical corpus, which is what this stage is for.

Entry and Submission text is not indexed. The brief permits indexing it only if
child visibility can be shown to be evaluated from current source data before
ranking, counting or snippet output — and doing that properly requires either:

* a polymorphic join from each document back to its own child table so the
  child's *current* `visibility_override` participates in the query; or
* denormalising that override into the projection.

The second is the stale-authorization failure this ADR already rejected, one
level further down. The first is achievable but means the projection's
authorization predicate varies by `source_kind`, which turns a single scoped
queryset into a union of three differently-scoped ones — and a union is exactly
where a count stops being trustworthy.

Neither is a *hard* problem. Both are the kind of problem that should be solved
with the child-content requirements actually in front of you, in the stage that
also brings document text extraction and its own authorization questions.
Security beats search completeness, and a deferral is reversible in a way that a
leak is not.

`SearchSourceKind` already names `ENTRY` and `SUBMISSION` so the contract is
visible; nothing writes them.

## Consequences

A lawyer searching for a phrase they remember writing in an entry will not find
it until Stage 2B. That is a real gap and it is stated in the empty-results text
on the search page.

The projection is genuinely disposable: `rebuild_search_index` empties the table
and rebuilds from canonical records, and tests assert that rebuilding from empty
and from stale produce the same result.

Because nothing in the domain reads from `SearchDocument`, a half-finished
rebuild degrades search and breaks nothing else.

## Alternatives considered

**Keep Stage 1's `icontains` implementation.** Rejected: no stemming, so Estonian
inflection and compounding make it useless on the real corpus, and it cannot
rank.

**Elasticsearch or OpenSearch.** Rejected, and the specification (14.6) sets the
conditions under which that decision would be *re-evaluated* rather than assumed:
multi-million searchable fragments, or a sustained failure to meet latency and
quality targets despite sound PostgreSQL indexing. Neither is close.

**Storing a denormalised visibility column and treating it as a hint rather than
an authority.** Rejected. A column that is authoritative-looking but advisory is
the kind of thing a future maintainer optimises a join away against.
