# 0118 — A full rebuild builds a new generation beside the one in use

**Status:** accepted
**Date:** 2026-09-25

Amends ADR 0013 (the rebuild's atomicity), ADR 0041 (what the worker's rebuild
costs a save). No `INDEX_VERSION` change: the rows are the same rows, only
which build they belong to is new.

## Context

**ENG-011.** `rebuild_all` emptied the projection and refilled it in one
transaction, holding the exclusive side of the refresh gate throughout. That
kept readers on the previous complete index — the property ADR 0013 exists
for — and it kept a targeted refresh from colliding with the refill on the
one-row-per-source constraint, which would have rolled back the user's save.

It also held every save that refreshes search — a Märge, an upload, a title
edit — for the whole rebuild. The audit measured 42–50 s at 38,000 rows. The
rebuild is not rare: it is the debt worker's repair (ADR 0041) and the
release's step 11, and it grows with the corpus.

Measuring this found a second defect under the first. A writer that deletes a
record and then refreshes search holds the record's row lock while it waits
for the gate; the rebuild, holding the gate, inserts a row naming that record
and — PostgreSQL's foreign keys here are deferred — waits for the row lock at
commit. PostgreSQL breaks the cycle by cancelling whichever waiter's deadlock
check runs first, and the writer has been waiting longer. The user's delete
failed with «deadlock detected»
(`tests/test_search_generations.py::test_8`, which reproduces it when the
protection below is removed).

## Decision

**Generations.** `SearchDocument.generation` says which build a row belongs
to; `SearchGeneration` records each build's state — BUILDING, ACTIVE, RETIRED
or FAILED, with at most one ACTIVE and one BUILDING (partial unique indexes).

* **Readers read the active generation only.** `visible_documents`, the
  integrity check and the index-state probe start from
  `app.search.generations.projection()`. The active number is an uncorrelated
  scalar subquery — an InitPlan, evaluated once per statement — so a statement
  sees one generation from start to finish, and the swap is atomic to it. With
  no ACTIVE record, the active generation is 1, which is where every row that
  predates this ADR is and where an older release inserts (the column's
  database default).
* **Writers write every live generation.** A targeted refresh replaces its
  source's rows in the active generation and, while a rebuild is filling one,
  in the building generation too. It learns the live set *after* taking the
  shared side of the gate, so a generation cannot appear between its reading
  the set and its commit.
* **A rebuild fills the next generation in batches, each its own short
  transaction under the exclusive side of the gate.** A batch reads canonical
  data under the gate, so a save whose refresh already ran has committed and is
  read, and a save that refreshes later rewrites the batch's rows. Then one
  short transaction swaps: BUILDING becomes ACTIVE and ACTIVE becomes RETIRED.
  Then the retired rows are deleted in batches of 5,000, holding nothing.
* **One rebuild at a time.** A session advisory lock `(24601, 2)`; a second
  rebuild refuses (`RebuildAlreadyRunning`, and the command says so) rather
  than queueing. The worker, finding one running, leaves its debt owed for the
  next pass. The lock dies with its session, so a killed rebuild never blocks
  the next one, and the next one discards what it left.
* **A writer is never the deadlock victim.** A writer that has to wait for the
  gate waits in slices of a quarter of `deadlock_timeout` (at most 100 ms),
  each inside a savepoint with `lock_timeout` set, so it never waits long
  enough for PostgreSQL's deadlock check to run; each slice queues like an
  ordinary lock request, so the next batch cannot starve it. A batch waits for
  a row lock at most half of `deadlock_timeout`, then rolls back — releasing
  the gate — and is retried with backoff (up to 8 attempts). A batch whose
  source was hard-deleted under it fails its foreign key at commit and is
  retried the same way, reading the source as gone. Either way it is the
  rebuild that yields, and giving up is safe: the active generation is
  untouched.
* **Debt.** Unchanged: `rebuild_and_discharge` claims the outstanding debt
  first and clears only what it claimed, after the swap. Debt written during
  the build survives it.

`rebuild_search_index` reports the generation it built, the longest single hold
of the gate, the swap's hold and how many batches were retried.
`check_search_integrity` reports the generation in use and a rebuild in
progress, and finds a BUILDING generation nobody is building (a killed
rebuild), rows of dead generations (a rebuild that stopped after its swap) and
a failed build newer than the one in use.

## Consequences

**Measured on the LARGE synthetic corpus (81,633 rows), with a writer saving a
Teema and adding a Märge every 50 ms throughout the rebuild:**

| | before (one transaction) | after (generations, batch 200) |
|---|---|---|
| rebuild, wall clock | 106 s | 153 s |
| writes completed during the rebuild | 1 | 590 |
| longest wait of a single write | **106 s** | **1.26 s** |
| median / p95 write during the rebuild | 106 s / — | 0.20 s / 0.60 s |
| median write with no rebuild running | 0.025 s | 0.025 s |
| longest hold of the refresh gate | 106 s | 0.88 s (one batch) |
| the swap's hold | — | 0.007 s |
| failed writes | 0 | 0 |

The rebuild itself is slower: it builds `SONAVORM.1` rows (ADR 0117's extra
vector), recomputes vectors batch by batch rather than in one statement at
the end, and shares the machine with 590 writes. Nobody waits for that time
any more. Batch size 500 held the gate for up to 1.5 s — a page of document
fragments is the heaviest batch — and 200 for up to 0.7 s, for a rebuild 3 %
slower on its own; 200 is the default.

* **Readers never see a partial index**, as before; the tests now prove it
  across committed batches rather than inside one transaction.
* **The table briefly holds two copies of the projection.** During a rebuild
  the text indexes return both generations' candidates and the generation
  filter halves them; outside one there is one generation and the filter
  removes nothing. Disk use peaks at roughly twice the projection until the
  retired rows are deleted and vacuumed.
* **`--keep-existing` no longer changes anything.** Every rebuild builds into
  an empty generation, so there is no gap to avoid and nothing stale survives
  either way; the flag is accepted so scripts keep working.
* **Rolling safety.** The release still serving during the migration never
  reads generations: it reads every row (one generation until the first new
  rebuild), inserts into generation 1, and its refresh deletes a source's rows
  in every generation before inserting, so the relaxed uniqueness cannot trip
  it. That holds **until the first generation-aware rebuild activates
  generation 2**: from then on a save served by the old release would move its
  source's row back into generation 1, where the new release does not read it.
  The release runbook already runs the rebuild (step 11) after the old release
  has stopped serving; it must stay that way.
* **Rollback.** An old release reads every generation and so works unchanged,
  once the rebuild's retired rows are gone (they are deleted right after the
  swap). Its own `rebuild_search_index` empties the table and refills
  generation 1; rolling *forward* again then needs the new release's rebuild,
  which the manifest's `search_rebuild_required` does not know to ask for —
  so after a rollback-and-forward, run it.

**Migration `search/0013`** (`atomic = False`):

* `SearchGeneration`, a new table.
* `generation`, an `integer` with a database default of 1 and no CHECK —
  a catalogue change only. A `PositiveIntegerField`'s CHECK would have been
  validated against every row under ACCESS EXCLUSIVE: 182 ms at 146,850 rows,
  blocking readers.
* The one-row-per-source uniqueness gains the generation: the new partial
  unique index is built `CONCURRENTLY` *before* the old one is dropped
  `CONCURRENTLY`, so there is never a moment without one. A plain build would
  have refused writes for 246 ms at 146,850 rows. `migration_plan` flags both
  as constraint changes — they are — and the relaxation is safe for the
  release still serving (above). New operations,
  `AddUniqueIndexConstraintConcurrentlyWhenPossible` and its removal
  counterpart, do this and refuse anything but a conditional
  `UniqueConstraint`.
* **No plain index on `generation`.** One was in the first draft, for the
  discard's `generation = n`, and the plan-shape tests caught the planner
  using it to read the whole active generation instead of asking the text
  indexes. The rebuild's lookups by generation add `source_object_id IS NOT
  NULL`, which lets them use the partial unique index that leads with
  `generation` — an index a reader's query cannot use.
* Reversible, once at most one generation's rows remain.

## Alternatives considered

* **A shadow table swapped in by rename.** `ALTER TABLE … RENAME` takes ACCESS
  EXCLUSIVE on both tables, queues behind every long reader and blocks
  everything behind it; the shadow would have to replicate twenty-odd foreign
  keys, the constraints and every index, and later migrations name those
  indexes. Writes during the build would need a change log replayed under a
  lock at the end, of unbounded size.
* **Batched in place, no generations.** Readers would see a partial index —
  the failure ADR 0013 forbids.
* **A queue of per-source refresh jobs instead of a full rebuild.** ADR 0041
  rejected it for keeping a second projection path in step with the first; the
  reason stands.
* **Writers blocking on the gate as before, the batch with a short
  `lock_timeout`.** Narrows the deadlock window without closing it: a writer
  whose deadlock check falls inside the batch's wait is still cancelled.
