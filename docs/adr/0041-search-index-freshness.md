# ADR 0041 — Search index freshness: durable debt instead of a rebuild somebody remembers

- Status: accepted
- Date: 2026-08-27
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0013 (the search projection is derived state), ADR 0014 (child
  content and live authorization joins), ADR 0038 (child visibility in
  projections, and the index-version gate), master specification §14 (search)
  and §24.3 (background jobs)

## Context

The projection is derived state and is rebuilt from canonical records; nothing
in the domain reads from it. That is settled and this ADR does not reopen it.

What was never settled is **who notices that it has gone stale.** Search
maintained itself for writes whose fanout was bounded — a Matter save, an Entry,
a Submission, a recipient change, a document's pages, a Matter's claim on a
OneNote page — by refreshing them inside the business transaction. For writes
whose fanout was not bounded it did nothing, on purpose: renaming an
Organisation changes the indexed text of every Matter, Submission and Entry that
names it, and reindexing thousands of rows inside somebody's admin form
submission is a worse failure than staleness. The documented answer was
`rebuild_search_index`, run by a human, prompted by `check_search_integrity`, run
by a human.

That is not a design with a deferred step. It is a design whose last step is
somebody remembering.

Four reproductions on `adce39e5ec3691c98f15d2218c59c1a4c4bc7009`, all with
synthetic data:

1. **Organisation rename — stale, and detected.** Index a Matter whose sender is
   "Rahandusministeerium ALPHA"; searching ALPHA finds it. Rename the
   organisation to BEETA. Canonical data says BEETA, search finds only ALPHA,
   and `check_search_integrity` reports *Vananenud tekst*. A rebuild converges
   it. This is the known, documented gap working exactly as documented.

2. **Organisation rename affecting only child rows — stale, and undetected.**
   An organisation that is a *submission recipient* and nothing else appears in
   no MATTER row's text. `_stale_matter_text` recomputes MATTER rows, so
   renaming it leaves the SUBMISSION rows stale and the integrity check reports
   **no findings at all**. The same is true of the organisation on an `Entry`,
   and of a person's `display_name`, which every ENTRY row they authored copies.

3. **`Kaasamine` — never indexed, and undetected.** ADR 0038 gave engagements
   their own search source kind because they carry a `visibility_override` a
   MATTER row cannot express. It gave them no way to *arrive*: no signal, no
   service call, nothing but a full rebuild ever wrote an ENGAGEMENT row. So
   every consultation recorded through `add_engagement` was outside the corpus,
   and `_expected_populations` did not list the kind, so nothing counted it
   missing. This is the worst shape the defect takes — not "a little behind" but
   "this content is not searchable and no report will say so".

4. **Matter senders — never indexed on creation, and undetected.**
   `_alias_text_for` indexes every sender's name and aliases, which is the whole
   point of ADR 0025. Nothing refreshed the projection when that list changed:
   there was no `m2m_changed` receiver for `source_organisations` and no handler
   on its through model. Worse, `create_matter` calls `Matter.objects.create()`
   — firing `post_save`, which indexes the Matter with **no senders at all** —
   and attaches the senders afterwards. So a Matter created through the product
   with its sender filled in was not findable by that sender, ever, until
   something else happened to save it. `update_matter_senders` worked only
   because it saves the Matter afterwards for an unrelated reason.

The user-visible symptom of all four is identical and is the one this system
exists to prevent: a lawyer types a word that is true of the record, gets an
empty page, and concludes Koda never worked on it.

## The invalidation map

What every canonical mutation invalidates, and what converges it. "Bounded"
means the number of search rows the write can invalidate is bounded by the write
itself; "high" means one edit can invalidate the corpus.

| Canonical mutation | Rows it invalidates | Fanout | Converged by |
| --- | --- | --- | --- |
| `Matter` save — title, alternate titles, summaries, reference, addressee, `policy_area_other`, visibility | that Matter's MATTER row | bounded | `post_save` → `refresh_matters`, in the transaction |
| `Matter.policy_areas` / `Matter.tags` add/remove/clear | that Matter's MATTER row | bounded | `m2m_changed` (post\_\* only) |
| `TagAssignment` save/delete | that Matter's MATTER row | bounded | `post_save`/`post_delete` — both, because `tags.add()` and a service-created row each miss the other's signal |
| `Matter.source_organisations` add/remove/set, and `create_matter(source_organisations=...)` | that Matter's MATTER row | bounded | **new** — `m2m_changed` **and** `post_save`/`post_delete` on `MatterSourceOrganisation`, because the through model has two write paths and each misses the other's signal |
| `Entry` save | that Entry's ENTRY row | bounded | `post_save` → `refresh_entry` |
| `Entry` delete | that ENTRY row | bounded | FK CASCADE (and a belt-and-braces `post_delete`) |
| `Submission` save | that SUBMISSION row | bounded | `post_save` → `refresh_submission` |
| `Submission` delete | that SUBMISSION row | bounded | FK CASCADE |
| `SubmissionRecipient` add/remove | that Submission's SUBMISSION row | bounded | `post_save`/`post_delete`, plus an explicit `reindex_submission` in `set_recipients` because `bulk_create` sends no signal |
| `MatterEngagement` create/update | that engagement's ENGAGEMENT row | bounded | **new** — `post_save` → `refresh_engagement`, in the transaction |
| `MatterEngagement` delete | that ENGAGEMENT row | bounded | FK CASCADE |
| `MatterEngagement.visibility_override` | nothing — visibility is never stored | — | live authorization join, no reindex |
| `Document` rename/reclassify | that document's DOCUMENT\_FRAGMENT rows | bounded | `post_save` → `refresh_document_version` for ACTIVE derivatives |
| Extraction publication | that version's DOCUMENT\_FRAGMENT rows | bounded | explicit refresh **inside the publish transaction** — committed derivative and findable derivative are one event |
| `DocumentVersion` / derivative / fragment delete | the fragment rows | bounded | FK CASCADE |
| `MatterSourcePage` save | that link's LEGACY\_SOURCE\_PAGE row | bounded | `post_save` → `refresh_source_link` |
| `MatterSourcePage` delete | that row | bounded | FK CASCADE |
| `LegacySourcePage` recapture | the LEGACY\_SOURCE\_PAGE rows of every Matter that accepted it | bounded (a page belongs to a handful) | `post_save` → refresh each link |
| `Matter` delete | every row of that Matter | bounded | FK CASCADE |
| **`Organisation.name`** | every MATTER, SUBMISSION and ENTRY row naming it | **high** | **new** — `pre_save` → durable debt → worker → `rebuild_all` |
| **`OrganisationAlias` create/edit/delete** | every MATTER and SUBMISSION row naming its organisation | **high** | **new** — durable debt |
| **`Tag.name_et`** | every MATTER row tagged with it | **high** | **new** — durable debt |
| **`TagAlias` create/edit/delete** | every MATTER row tagged with its tag | **high** | **new** — durable debt |
| **`PolicyArea.name_et`** | every MATTER row in that area | **high** | **new** — durable debt |
| **`PolicyArea` delete** | every MATTER row in that area | **high** | **new** — `post_delete` → durable debt. The only reference row here that can be deleted while its name is indexed: every other one is held by a `PROTECT` foreign key the moment anything names it, and `Matter.policy_areas` alone is a plain many-to-many |
| **`User.display_name`** | every ENTRY row they authored | **high** | **new** — durable debt |
| Bulk writers (`update()`, `bulk_create`, importers, cutover commands) | whatever they touch | varies | unchanged: the caller owns its refresh, under `suspend_indexing`, and every current one does |

Three rows changed classification and none had a detector before this change.
`MatterEngagement` was **silently never projected**. `Matter.source_organisations`
was **silently never projected on creation**, which `_stale_matter_text` cannot
see either: it recomputes what `indexed_text_for` would produce and would have
reported it, but only for a Matter that happened to fall inside its 250-row
sample, and only for a corpus somebody thought to run the check against. And an
`Organisation` renamed while it was only a submission recipient or an entry's
organisation was **silently stale** in rows the drift sample does not cover at
all.

## Decision

**Every canonical mutation that changes indexed text does one of two things, and
there is no third.**

> **A. Bounded fanout — refreshed inside the same transaction as the canonical
> write.** A committed record and a findable record are the same event.
>
> **B. High fanout — records a durable obligation inside the same transaction as
> the canonical write.** An automatic consumer discharges it with the atomic
> full rebuild, and the obligation is cleared only after that rebuild has
> committed.

A mutation may not leave the projection stale with nothing recording that it
did. That is the whole contract, and the second half of it is what SEARCH-001
adds.

### `Kaasamine` and Matter senders join class A

`refresh_engagement` in `app/search/indexing.py`, called from a `post_save`
handler, composing its row through `refresh_engagements` — the same builder the
full rebuild uses, and it recomputes the tsvector, because a row inserted
without one exists, counts as indexed and can never match. There is deliberately
no `post_delete` companion: `SearchDocument.engagement` is a real foreign key
with `on_delete=CASCADE`, so the schema already removes the row, and a handler
would be a second mechanism for something the database guarantees.

Senders get the pair of handlers `TagAssignment` already has — `m2m_changed` on
the through table for `.set()`/`.add()`, and `post_save`/`post_delete` on
`MatterSourceOrganisation` for a row a service creates directly — because each
covers the write path the other cannot see. The `m2m_changed` handler is also
what fixes creation: it fires after `.set()`, which is after
`Matter.objects.create()` indexed the row without them.

`check_search_integrity` now counts ENGAGEMENT in `_expected_populations` and
checks it in `_crossed_matters`. A projected kind that nothing counts is a kind
nothing watches.

### Class B is a row in PostgreSQL, not a callback in a process

`SearchRebuildDebt` — one small table, search-local, no foreign key to any
business record, holding a `reason` from a fixed vocabulary and the attempt
bookkeeping. `mark_rebuild_owed` is one INSERT with no reads, called from
`pre_save`/`post_save`/`post_delete` handlers on `Organisation`,
`OrganisationAlias`, `Tag`, `TagAlias`, `PolicyArea` and `User.display_name`.
Every one of those is connected with `weak=False`, which is load-bearing: a
closure passed to `Signal.connect` is held weakly, is collected at the next
garbage collection, and stops being a receiver with nothing raising anywhere —
a failure indistinguishable from the defect being fixed. It has its own guard
in the suite rather than a comment.

`transaction.on_commit` was considered and rejected. It is enough for a process
that never dies: a deploy, an OOM kill or a `docker compose down` between the
commit and the callback loses the only record that search is now stale, and
loses it silently. A committed row survives all three, and a business
transaction that rolls back takes its debt with it for free — which is tested
both ways.

**No deduplication at write time.** Renaming three organisations writes three
rows; they coalesce at the other end, where the consumer claims every
outstanding row, performs *one* rebuild and clears exactly what it claimed.
Collapsing them at write time would need an upsert whose lost-update window is
precisely a mark arriving while a rebuild is running — the one moment a mark
matters most.

**Renames are compared; alias rows are not.** `Organisation` and `User` are
saved for reasons that have nothing to do with their names, so the handler reads
the stored row and marks only if the name actually moved — and skips even that
read when `update_fields` promises the save cannot touch it, which keeps this
subsystem off the sign-in path. An alias row exists only to be indexed, so every
write to one is a write to searchable text and there is nothing cheaper to test.

### The consumer is a Compose service running a management command

`run_search_refresh_worker`, in `deploy/unraid-main/compose.yml` and
`deploy/unraid-test/compose.yml`, built like `run_extraction_worker` and for the
same reasons: PostgreSQL is the queue, no broker, safe to run twice, safe to
kill. The master specification names exactly this for exactly this job
(§24.3, "search projection maintenance/rebuild support"). A host cron entry was
not an option: this host's cron is generated from the flash drive and editing it
can take Unraid's own mover and parity jobs with it, which the rehearsal runbook
already says.

`check_search_freshness` is its healthcheck and the operator's one-line
question. It measures **convergence, not liveness** — a departure from
`check_extraction_worker`, which reads a heartbeat file. A stopped search worker
with nothing owed reports healthy here, and that is correct: the index is
complete and current, and the moment a rename arrives the probe goes red within
`SEARCH_REBUILD_DEBT_STALE_SECONDS`, well before anybody has searched for the new
name and not found it. In exchange it needs no writable path and no second
definition of "recently".

`check_search_integrity` reports the debt and consumes nothing. Pending work is
printed as a fact; it becomes a *finding* only once it has outlived the
threshold — nothing is converging it — or a rebuild has already failed against
it. A diagnostic that drained the queue it was asked to describe would be the
same mistake as one that quietly repaired what it found.

### The debt table holds no indexed content, including in its failure column

`SearchRebuildDebt.last_error` records **sanitised metadata about a failure and
never the exception's own message**. Everything written to it goes through
`app.search.freshness.describe_failure`, which is the column's only writer.

The reason is not tidiness. `check_search_freshness` prints that column to an
operator's terminal and to a container log, and a PostgreSQL error message is
composed out of the row that failed. A rebuild that hits a not-null violation
raises with `DETAIL: Failing row contains (…)` — and the row in question is a
`SearchDocument`, so those brackets contain the projected `title` and
`body_text`. A unique violation gives `Key (column)=(value) already exists`.
Storing `str(error)` put the most confidential material this system holds — a
RESTRICTED `Kaasamine`'s note is in that text — into the one search table that
was never meant to carry any of it, and then printed it at a health probe's
verbosity.

The rule is therefore an allow-list rather than a filter: SQLSTATE, exception
class, and the schema identifiers PostgreSQL supplies separately
(`table_name`, `column_name`, `constraint_name`). Those are names that appear in
the DDL, never values that appear in a row. What an operator gets is
`IntegrityError [23502] search_searchdocument.indexed_at`, which still says
which constraint broke and therefore what to look at; the full traceback goes to
the worker's log, whose audience is a developer rather than a probe.

Sanitising does not soften the failure state. `attempts` still increments,
`check_search_freshness` still faults immediately on a non-zero attempt count,
and the debt still stands until a rebuild succeeds.

### A database restart must not need a container restart

Every worker iteration begins with `close_old_connections()`
(`freshness.worker_pass`). A request/response cycle gets this for free — Django
drops a connection broken by the previous request before the next one needs it —
and a management command that loops forever gets none of it.

Without it, a PostgreSQL restart wedged the worker permanently: every subsequent
pass raised `OperationalError` against the same dead socket, the debt kept
accumulating, and `restart: unless-stopped` never fired, because it watches for a
process that *exited* and this one was still running and still logging. Recovery
needed a human to notice and restart the container, which is the failure this
whole ADR exists to remove, displaced by one layer.

`close_old_connections` rather than an unconditional close: it drops a connection
only when it is unusable or past `CONN_MAX_AGE`, so an ordinary pass reuses what
it had. And the healing happens *before* the attempt rather than after the
failure, so nothing depends on the pass that lost the connection having survived
long enough to clean up after itself.

This is connection hygiene and not a retry policy. A rebuild that fails for an
application reason still fails on the next pass, ten seconds later, and stays
visible in the debt table — the ten-second idle is the whole of the backoff, and
at six users and a rebuild measured in seconds it is enough.

### Why the whole corpus rather than a fanout queue

Measured. A full rebuild is 1.7s for 2,946 rows on the deployed host and 4.4s
for 3,155 rows on a developer laptop, inside one transaction, with readers
holding the previous *complete* index throughout. A queue of per-Matter refresh
jobs would be a second projection path to keep in step with the first, in
exchange for saving a few seconds of machine time a few times a month — and it
would need extending by hand every time a new column starts contributing to
searchable text, which a coarse rebuild picks up for free.

`INDEX_VERSION` is **not** bumped. It states the contract a projection row was
built under, and nothing about what a row contains has changed. AUTH003.1 stays
authoritative.

## Consequences

`migrations: search/0007_search_rebuild_debt` — one new table, search-local, and
still the only one. **No business-data migration**, and nothing that rewrites a
Matter, a Submission or a Document. Forward deployment is safe against an
existing corpus: the table starts empty, which reads as "nothing owed", which is
true of an index nobody has invalidated yet.

`POLICY_AREA_REMOVED` was folded into 0007 rather than added as a second
migration, because 0007 has never been applied anywhere: it exists on this
branch alone, the service it belongs to is not deployed, and CI builds the
schema from zero on every run. A choices-only `AlterField` emits no DDL, so the
two forms are identical to PostgreSQL — one file is simply the honest
description of a table that has only ever been created once.

`SearchRebuildDebt` is **operational** state, and `app/core/deployment.py` grows
a third category to say so: `OPERATIONAL_MODELS`, alongside canonical and
rebuildable.

It is not `REBUILDABLE_MODELS` — nothing rebuilds a debt row; rebuilding is what
*clears* one — and leaving it canonical, which is what this ADR originally
decided, turned out not to be moot at all. A pending row made
`recovery_fingerprint --compare` report

    canonical_counts.search.SearchRebuildDebt: 0 -> 1

which is a healthy queue with a few seconds' work in it being reported as
canonical-state divergence, by the one command whose job is to tell an operator
whether a restore brought the register back. A probe that cries wolf during a
correct restore is worse than no probe.

The two halves of the classification are separate decisions and only one of them
moved. The debt table is still **persisted and restored** exactly as before — the
backup is a whole-database `pg_dump` and has no table list, and `REBUILDABLE_MODELS`
never fed it; the classification is read only by `recovery_fingerprint`. What
changed is that operational counts are **reported and never compared**, for a
different reason from rebuildable ones: rebuildable rows are legitimately absent
after a restore, operational rows are legitimately *present*.

`FINGERPRINT_VERSION` is **not** bumped. A fingerprint written before this build
carries the debt table under `canonical_counts`, so the comparison drops
operational labels from both sides rather than refusing the older file — which
keeps a pre-deploy fingerprint comparable across the deploy that introduces this,
the one moment somebody most wants to compare one.

**A rebuild can now happen at any moment**, where before it happened when an
operator chose. Ordinary saves are unaffected: the shared/exclusive advisory
lock in `app/search/indexing.py` predates this and is what keeps a rebuild from
failing a lawyer's save, and the regression is re-asserted against a
debt-driven rebuild specifically. A save landing mid-rebuild waits for it —
seconds, a few times a month.

**Convergence target.** There is no business SLA for this and this ADR does not
invent one. `SEARCH_REFRESH_WORKER_IDLE_SECONDS` defaults to 10, so a vocabulary
edit converges in roughly one idle period plus one rebuild.
`SEARCH_REBUILD_DEBT_STALE_SECONDS` defaults to 300, comfortably above that, so
ordinary pending work never trips a probe.

**Operational requirement.** The service definition is in the repository;
deploying it is a stack redeploy on the host. Until that happens the durable
debt still accumulates and `check_search_integrity` and `check_search_freshness`
both report it — which is strictly better than the previous state, where
staleness left no record at all — but convergence is manual, and the honest
description of that stack is *not yet automatic*.

## What this does not do

DATA-001 and DATA-002 are untouched. Nothing in this change alters evidence or
submission mutation services, their schemas, or any integrity semantics.
`app/documents/services.py`, `app/submissions/services.py` and
`app/documents/extraction/*` are not modified — the extraction publish path
already refreshes its own fragments inside the publication transaction, which is
class A and correct as it stands.

No authorization change. `projected_visibility_q`, `visible_documents`, the live
child joins, the AUTH003.1 version gate and the fail-closed behaviour for stale
rows are all exactly as ADR 0038 left them. Ranking, query semantics and the
searchable-text composition are unchanged: every new trigger calls the builders
the rebuild already used, so there is one definition of what a record says.

## Known boundaries, deliberately left alone

Three things this design does not guarantee. Each was examined during the
correction round, each is reachable only outside the paths the product supports,
and each would cost more to close than the failure is worth today. They are
written down so the next person meets them here rather than in production.

**A rename outside a transaction has a window.** `_mark_on_rename` runs on
`pre_save`, and Django sends `pre_save` *before* the context manager that wraps
the write — and for a model with no parents that context manager is
`mark_for_rollback_on_error`, not `atomic`. So a bare `organisation.save()` in
autocommit commits the debt row in one transaction and the rename in the next.
A worker polling between the two would rebuild against the old name and clear a
debt that was owed for a change it did not see, leaving the corpus stale with
nothing recording it.

It is not reachable from the product. Every supported way to rename an
`Organisation`, a `Tag`, a `PolicyArea` or a `User` is Django admin, and
`ModelAdmin.changeform_view` wraps the whole POST in `transaction.atomic` — so
the mark and the rename commit together, which is the guarantee this design
claims. What is left is a `manage.py shell` session, where the operator running
it can also run `rebuild_search_index`. Closing it properly means capturing the
old value in `pre_save` and marking in `post_save`, which is a second piece of
per-instance state and a redesign of every handler in the file; it is not worth
that until something other than admin edits reference data.

**`QuerySet.update()` sends no signals**, so a bulk rename through it owes
nothing. Re-checked in this round: every `.update()` in the repository that
touches one of these models sets `is_active`, `sort_order` or `help_text` —
none of which reaches indexed text — and all of them are migrations or the
seed command rather than a production write path. The alternative is a database
trigger, which would put a second definition of "the projection is stale" in a
place no test in this repository can read. Bulk writers own their refresh here,
as they already do for `suspend_indexing`.

**The retry cadence is flat.** A rebuild that fails for an application reason is
retried every `SEARCH_REFRESH_WORKER_IDLE_SECONDS`, for ever, with the failure
visible in the debt table and in the log each time. That is not a busy loop —
ten seconds is the floor — and at six users, a rebuild measured in seconds and
a failure that a human is expected to look at, a backoff curve would add a reset
rule and its own tests to save a log line every ten seconds. Revisit it if this
worker ever runs somewhere that pays per query.

The extraction worker (`run_extraction_worker`) has the same connection
lifecycle as this one had before the correction. It is a separate service with a
separate queue and it is out of scope here, but whoever touches it next should
know that `close_if_unusable_or_obsolete` between passes is what keeps a
long-lived loop alive across a database restart.
