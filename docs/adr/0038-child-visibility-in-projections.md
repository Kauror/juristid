# ADR 0038 — Child visibility in projections, and the index-version gate

- Status: accepted
- Date: 2026-08-26
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0005 (authorization and visibility inheritance), ADR 0013 and
  0014 (the search projection and why visibility is derived live), ADR 0036
  (who work may be assigned to), ADR 0037 (the business-write HTTP boundary)

## Context

ADR 0037 answered *may this actor write*. This answers a different question, and
a correct write boundary does nothing for it: a reader properly refused a
restricted child can still learn its contents if some other surface was built
from an unscoped population.

`Entry`, `Submission`, `Document`, `NextAction`, `MatterEngagement` and the three
`MatterFact` kinds are all `VisibilityInheritingModel`s. Each carries a
`visibility_override` that can make it stricter than its Matter. So a Matter
being visible is never sufficient authority to project a child of it, and every
surface that walks `.all()` on one of those relations has widened visibility
without anybody deciding to.

Five such surfaces were confirmed on `3f5636b`:

1. **Search.** `_engagement_text_for` walked `matter.engagements.all()` and
   concatenated every title and note into the **MATTER** row's tsvector. A
   MATTER row is authorized by the Matter alone, so a RESTRICTED consultation on
   a NORMAL Matter was searchable by the whole department.
2. **Timeline.** `ChangeEvent.objects.filter(matter=matter)` with no child
   scoping. `EVIDENCE_VERSION_ADDED` records the original filename as its
   summary and the template prints it, so a document properly hidden from
   Dokumendid still named itself one tab away.
3. **Filter chips.** `_filter_display` and `_display_value` resolved an owner
   UUID from the query string against `User.objects`, turning the address bar
   into a directory — and answering most revealingly exactly when the person
   appears nowhere the reader may look.
4. **Current action.** `open_action_prefetch()` was unscoped, printing a
   restricted `Järgmiseks` and its responsible colleague onto register rows.
5. **Final evidence.** The opinion card and the position rail printed a
   filename, size and SHA-256 prefix for a `DocumentVersion` whose `Document`
   may be restricted below the Submission. The download route already refused
   the bytes; the metadata was never checked.

## Decision

**Kaasamine becomes a first-class search source.** `SearchSourceKind.ENGAGEMENT`,
a nullable live FK on `SearchDocument`, and an entry in `SOURCE_OVERRIDE_FIELDS`
mapping it to `engagement__visibility_override`. Engagement text leaves the
MATTER projection entirely. The rule this restores is the one every other child
kind already followed: **a MATTER row may carry only content the Matter itself
governs**, and text whose visibility may be narrower gets a row whose visibility
can express that.

**The index-version gate.** `INDEX_VERSION` moves from `2D.1` to `AUTH003.1`,
and the search chokepoint now reads only rows carrying the current version.

This is the part worth arguing. A row indexed before the fix has the restricted
words inside its stored tsvector, and no predicate can take them back out —
visibility filtering decides whether a row is *returned*, never what is *in*
one. Excluding all MATTER rows would delete ordinary search. So the only
available lever is to stop trusting rows built under the old contract, which
makes every pre-fix row ineligible the moment the code is deployed, **before any
rebuild**.

The cost is that search is empty until the one-time rebuild runs. That is
deliberately the right direction to fail: a reader sees too little and can tell,
rather than reading something they should not and cannot. Measured at production
scale (2,455 matters, 2,946 rows) the rebuild takes **1.7 seconds** inside a
single transaction, so the window is small enough that no dual-version
compatibility scheme is worth its own failure modes.

**One `ChangeEvent` scoping helper.** `app/audit/visibility.py` maps event types
to the child they describe and the path from that child to its Matter; the
predicate is `child_visibility_q`, unchanged. The timeline uses it.

`Viimased muudatused` deliberately does **not**, and the reason is worth writing
down because the two look like duplication. That feed's event filter is doing
two jobs at once: it decides which events are *curated into* the section at all
— a whitelist of six Matter events plus five child families — and it scopes
them. Replacing it with this helper would keep the scoping and lose the
curation, letting every uncurated Matter-level event into a section that exists
to be readable. Its scoping was written correctly in PR #72, is tested there,
and generalising it here is what produced this module; consolidating the two
would be a redesign of a working surface for symmetry, which is a bad trade in
security code.

**Filter labels resolve inside the reader's own data.**
`named_owner_in(population, raw_id)` sits beside `owner_filter_choices`, the
population PR #69 already built for the *options*, and the chip now agrees with
the dropdown. An unresolvable value prints `tundmatu` rather than the raw UUID.

## Consequences

`migrations: search/0006_engagement_search_source` — one nullable FK, the
choices change, the default version string. **No business-data migration**, and
deliberately none transforming existing contaminated MATTER text into engagement
rows: `SearchDocument` is rebuildable derived data and reconstructing it from a
projection is exactly the coupling ADR 0013 refuses.

**A one-time production search rebuild is required to restore coverage.** It is
*not* required to establish confidentiality — the version gate does that on
deploy. The release order is: migrate, deploy, stale rows are already ineligible,
run `rebuild_search_index`, verify the current-version row count, coverage
returns. If the rebuild fails, old rows stay unavailable and confidentiality is
still correct. There is deliberately no fallback that re-admits old versions.

This is not SEARCH-001. No mutation-triggered refresh, no rebuild scheduling and
no freshness hooks were added; a one-time rebuild forced by an index-contract
change is a different thing from keeping the index fresh, and SEARCH-001 remains
open.

## What this does not do

DATA-001 and DATA-002 are untouched: the evidence fix hides *metadata* from
readers who may not see the document and changes no integrity semantics. No
authentication change. No role semantics change. No write authorization change.
