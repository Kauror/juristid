# 0136 — Search reads overviews, important dates and work wins

**Status:** accepted
**Date:** 2026-10-04

**Three stored kinds of text become searchable, each as its own row.**
`Ülevaade / uudis` (its stated title), `Oluline tähtaeg` (title and
explanation) and `Töövõit` (title, detail and note) get `SearchSourceKind`
values `WEBSITE_OVERVIEW`, `IMPORTANT_DATE` and `WORK_VICTORY`, each with its
own nullable foreign key on `SearchDocument` and its own live
`visibility_override` join. `INDEX_VERSION` moves `SONAVORM.1 → SONAVORM.2`,
so the release manifest says `search_rebuild_required: YES` and production runs
the documented rebuild (`deploy/unraid-main/README.md` §11).

## Context

The historical replay of 2026-10-04 (F-008) found that the headline Koda
published on koda.ee, an `Oluline tähtaeg` and a work win — all typed into
Juristid — could not be found: in all five cases the distinctive news title
returned no hits. The search index projected Matters, entries, opinions,
documents, rounds, `Märge` and positions, and none of these three.

## Decision

* **Own rows, not Matter text** — each record carries its own override and may
  be stricter than its Matter, so its words must not ride on a row authorized
  by the Matter alone (AUTH-003; the rule `ENGAGEMENT` already follows).
* **Only what a person typed.** An overview is indexed by its stated title and
  nothing else — never its address, and never a title read off the address.
  An overview with no title has no row. No page is fetched; nothing is
  extracted from documents.
* **Every state, never removed.** Plans, publications, cancelled overviews,
  superseded and cancelled deadlines, candidate and rejected wins all stay
  findable; a removed record leaves the index in the save that removed it.
* **The ordinary machinery.** `post_save` signals inside the business
  transaction, delete-and-reinsert refreshes, the full-rebuild plan, and
  `check_search_integrity`'s expected-row counts. Authorization at query time
  through the existing chokepoint, before counting and ordering.

**Migration `search/0014`:** three nullable foreign keys (`CASCADE`) with
partial indexes built concurrently, the new choices, and the `index_version`
default. Additive; the running revision ignores the columns. No row is
rewritten: existing records reach the index by the release's rebuild.

## Consequences

* A release containing this needs `rebuild_search_index`, then
  `check_search_integrity --full`, then `deployment_readiness`.
* Results of the three kinds open the Teema page.

## Not changed

The index of every other kind; the rebuild commands; header suggestions (Matters
only); `Jõustumine` facts stay unindexed.
