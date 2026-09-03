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

---

## Amendment (2026-09-03) — the archive projection is inside this contract

- Status: accepted
- Related: ADR 0056 (the archive is department work product), UX-006

### What this ADR missed

Everything above is about `SearchDocument`, and the invalidation map is
exhaustive for it. There is a **second** projection —
`OpinionArchiveSearchDocument`, added later with the searchable opinion archive
— and this record never mentioned it. It arrived with a rebuild
(`rebuild_archive_index`) and no handlers at all, so the decision that
"a mutation may not leave the projection stale with nothing recording that it
did" was written down for one projection and true of one projection.

The consequence was the whole point of this ADR, reproduced exactly. Two of the
archive projection's columns are computed at index time from relations that
nothing about a binary touches:

| Column | Computed from |
| --- | --- |
| `is_linked` | `OpinionArchiveMatterLink` on the binary |
| `has_submission` | `OpinionSubmissionImport` on any of the binary's items |

Links and canonical Submissions were created after the corpus was indexed and
nothing refreshed the projection, so `/arvamused/arhiiv/` reported
`767 kirja · 0 teemaga seotud · 0 kanoonilise arvamusena` over a corpus holding
320 links and 313 Submissions. Every letter's own detail page named its Teema
correctly, because the detail page reads the canonical relation. The list, the
`Teemaga seotud` tab and the header count read the projection. A lawyer
filtering to linked letters got an empty workspace, and the archive — the one
thing in the product whose purpose is to be connected to the register — looked
entirely disconnected from it (UX-006).

`archive_index_findings()` reported none of this. It checked for missing rows,
a stale `index_version` and a null tsvector, and passed cleanly throughout.

### Decision

**`OpinionArchiveSearchDocument` is covered by the same contract**, in the same
two-class shape, with one addition.

**One-binary mutation path — class A.** `OpinionArchiveMatterLink` and
`OpinionSubmissionImport`, created, updated or deleted, invalidate exactly one
archive row. They are refreshed **inside the business transaction** by
`app/legacy_import/opinion_search_signals.py`, so a committed link and a
findable link are the same event and a rolled-back link takes its refresh with
it. There is no high-fanout mutation on this projection and therefore no
durable-debt half: the archive has no equivalent of renaming an Organisation.

**Bulk-caller responsibility.** `suspend_archive_indexing()` suppresses those
handlers and `refresh_archive_binaries(ids)` is what the caller owes in
return — a refresh bounded by the binaries it actually touched. A rebuild of the
corpus after every bulk operation is **not** the discharge: it makes each apply
pay for hundreds of letters it did not write, and it is the "rebuild somebody
remembers" this ADR replaced, merely relocated.

`apply_plan` is the one current bulk writer that needs it, and it needs it for
three reasons rather than one: it touches the same binary several times, it
marks candidates applied with a `QuerySet.update()` that sends no signals, and
`review_state` and `match_class` are projected from exactly those candidates.
It suspends, and refreshes the binaries its batch catalogued, inside its own
atomic block. `derive_links` needs nothing: it creates links one at a time
through `link_matter`, so the handlers already cover it.

**Drift verification.** A contract nothing checks is a comment. `verify` now
compares both columns against canon in both directions and reports the count of
rows that disagree — it **detects and does not repair**, because a verify that
quietly fixed what it found would make the next occurrence invisible too. It
reports aggregates only, like every other finding here: a count and a class of
problem, never a filename, a title or a SHA.

### What this amendment does not do

It does not touch archive semantics, the archive authorization boundary
(ADR 0056 — still asked at query time, still not stored on the row, so widening
the reader set still needs no rebuild), or what counts as evidence for a link
(ADR 0055). It is about freshness and nothing else.

It also does not run the rebuild that converges the corpus as it stands today.
The code stops the projection drifting again; the existing drift is an
operational one-off, authorised and performed separately.

### Follow-up (2026-09-03) — the third relation

The amendment above covered two of the archive projection's derived columns and
named the third in passing, as a reason `apply_plan` owes a bounded refresh:
`review_state` and `match_class` are computed at index time from the
occurrence's live `OpinionMatchCandidate` rows. Nothing outside `apply_plan`
refreshed them, so the table is completed here:

| Column | Computed from |
| --- | --- |
| `review_state` | non-`SUPERSEDED` `OpinionMatchCandidate` on the binary's items |
| `match_class` | the same rows, in the model's own order |

This was the worse of the three omissions, because candidates are the relation
the product actually writes. Every decision recorded in
`/haldus/arvamuste-ulevaatus/` — rejected, duplicate, not an opinion, deferred,
linked — is a `save()` on one candidate; so is every proposal a rerun retires
through `supersede_candidate`, and every one a catalogue adds. All of them could
commit while the archive workspace went on labelling and filtering the letter by
the state before the decision, with `verify` reporting a clean run throughout.

**Same class, same discharge.** One candidate names one occurrence, which names
at most one binary, so this is class A like the other two: refreshed inside the
business transaction, the whole row rather than the two obvious columns — a
candidate's `excel_reference` is indexed among the row's `identifiers` and would
otherwise go stale behind them. Suspension and `refresh_archive_binaries` are
unchanged, and `apply_plan` keeps the bounded discharge it already had; nothing
here introduces a rebuild.

**Cascades are not the link's lifecycle.** `app/search/signals.py` and the two
handlers above skip a delete that did not begin at the row itself, because a
link goes either on its own or with its binary. A candidate is `CASCADE` from
its `OpinionArchiveItem` *and* from its `Matter`, and the binary outlives both —
`OpinionArchiveItem.binary` is `PROTECT`, and the TEST-data purge deletes
Matters while holding `OpinionArchiveBinary` in `NEVER_OWNED` for exactly that
reason. The candidate handler therefore asks the narrower question, *is the
binary going too*, and refreshes in every other case.

**Drift verification, extended.** `archive_index_findings()` now also reports,
in both directions, rows whose stored `review_state` or `match_class` disagree
with the live candidates — computed through the same helpers the row builder
uses, because a verifier carrying its own priority order would be checking
itself. Aggregate counts only, like every other finding here.

### Follow-up (2026-09-03) — the occurrences the other three hang off

The two follow-ups above cover the three relations that *point at* a letter. The
fourth is the one they point through. `OpinionArchiveItem` is one filing — one
path inside one archive snapshot — and the row builder reads six columns off the
live set of a binary's filings, not three:

| Column | Computed from |
| --- | --- |
| `occurrence_count` | the binary's live `OpinionArchiveItem` rows |
| `occurrence_paths` | their `archive_relative_path`, de-duplicated, in the model's order |
| `identifiers` | the binary's own SHA-256, plus their `original_filename`, plus the `external_id` of the `OpinionArchiveMetadata` and the `excel_reference` of the `OpinionMatchCandidate` hanging off them |
| `title` | the first filing's `filename_title`, falling back to KodaDash's `title` |
| `recipient` | the first filing's `filename_recipient`, falling back to `recipient_raw` |
| `document_date` (and `source_year`) | the first filing's `filename_date`, falling back to `document_date` |

"First" is the model's own ordering — `filename_date`, then `original_filename` —
which is what makes those three well defined for a letter filed at several paths,
and what the drift check has to share rather than re-derive.

So removing one filing of a letter moves six columns at once, and takes that
filing's metadata and candidates with it by CASCADE. The reproduction on
`3d34f0dd`, synthetic data: a binary catalogued at two paths, indexed; delete one
occurrence; the row goes on reporting two filings, both paths and the removed
filename among its identifiers, and `archive_index_findings()` reports **no
findings at all**. Deleting the last one leaves the row still naming a title and
a date that nothing holds.

The candidate handler from the follow-up above *does* fire during that delete —
candidates are CASCADE from the item — and it does not help. The collector
removes children before their parent, so the refresh runs while the occurrence
row is still there and recomputes a row that still contains it. Being one
statement early is indistinguishable from not running.

**Same class as the other three.** One filing names at most one binary, so the
fanout is bounded and the refresh belongs in the business transaction:
`post_save` and `post_delete` on `OpinionArchiveItem`, recomputing the whole row
through `_row_values` as the others do, in `opinion_search_signals.py`. A
rolled-back deletion takes its projection change with it, and is tested that way.

`post_delete` rather than `pre_delete`: the recompute must see the corpus as it
is afterwards, once the occurrence's metadata and candidates have gone with it.

**No cascade can reach an occurrence, and the schema is what says so.**
`OpinionArchiveItem.binary` and `.batch` are both `PROTECT` and are the model's
only foreign keys, so there is no parent whose deletion takes a filing with it:
every deletion begins at the row or at a queryset of them, and the binary is
always still held afterwards. The delete handler keeps `_binary_survives` anyway,
for the reason that guard was written — re-projecting a binary that is itself
being deleted would insert a row the cascade has already swept past — because the
consequence of being wrong there is a failed delete rather than a stale row.

**The last filing does not remove the row.** `rebuild_archive_index` writes a row
for every held binary whether the archive still lists it or not, so a bounded
refresh must land in the same place: the row stays, reporting nothing catalogued
and still findable by the hash. The bytes are canonical evidence and the
catalogue is not what makes them real. Asserted against an actual rebuild rather
than against a belief about one.

**`pre_save` for a filing that moves.** An occurrence carries its filename, its
path, its metadata and its candidates with it, so moving one between binaries
invalidates *two* rows and `post_save` can only see the one it joined. The
binary it left is captured on `pre_save` — the same shape `app/search/signals.py`
uses for a rename, believing `update_fields` and testing suspension before the
lookup — and refreshed alongside. No production path reassigns a filing today;
this is the shell session and the next writer, and it costs one indexed
primary-key lookup on a model written once per filing.

**The bulk exception is `materialize`, and it now pays.** `_link_occurrences`
points a catalogued occurrence at its bytes with a compare-and-set `update()` —
it links only a row still holding no binary — which no signal can see. It is the
one production write that changes a projected column without a model save, and
the case that matters is the reuse case: a later snapshot refiling a letter we
already hold attaches a second occurrence to a binary that *already has an
indexed row*, which then reported one filing where the archive held two. It
refreshes the one binary it linked, inside its own atomic block, exactly as
`apply_plan` does for the batch it catalogues. `suspend_archive_indexing` and
`refresh_archive_binaries` are unchanged, and no rebuild is introduced.

One consequence worth stating plainly: materialisation now leaves the letters it
held *in* the projection rather than out of it. The "not in the search
projection" finding stops being the normal state after a materialise, which is
what it should always have meant.

**Drift verification, extended.** `archive_index_findings()` now recomputes
`occurrence_count`, `occurrence_paths`, `identifiers` and the title/recipient/date
triple through `_occurrence_values` — the row builder's own function, for the
reason the candidate check shares `_candidate_values` — and reports the count of
rows that disagree. Both directions, inherently: a stale extra path, a missing
one, a count that never moved and an identifier left behind are all a recomputed
value differing from a stored one. This closes the one gap the candidate
follow-up named and could not close: subtracting a single identifier from the
column cannot be told apart from the rest of it by inspection, but recomputing
the whole column can. Aggregate counts only, like every other finding here — the
column being checked is a filename, so leaking it would be its own finding.

**Not yet covered when this was written, and detected rather than fixed.**
`OpinionArchiveMetadata` and `OpinionArchiveText` had no handlers of their own: a
metadata row written against an already-indexed binary, or a body extracted after
one, moved the projection with nothing refreshing it. Both were *reported* — the
text case by the "text exists, the projection does not reflect it" finding that
predates this, the metadata case by the identifier and heading checks above — so
neither could go stale in silence, which is the contract this ADR states. The
follow-up below closes them.

### Follow-up (2026-09-03) — what an occurrence carries, and the map closed

The four follow-ups above cover the relations that *point at* a letter and the
occurrences they point through. Two inputs were named as not covered, in the
paragraph immediately above this one, and this closes both.

| Column | Computed from |
| --- | --- |
| `identifiers` | …plus the `external_id` of each `OpinionArchiveMetadata` on the binary's filings |
| `title` | the first filing's `filename_title`, falling back to the metadata `title` |
| `recipient` | the first filing's `filename_recipient`, falling back to `recipient_raw` |
| `document_date` (and `source_year`) | the first filing's `filename_date`, falling back to the metadata `document_date` |
| `body_text` | `OpinionArchiveText.body`, where that row `has_body` |
| `has_body_text` | whether it does |

**Metadata reads as inert, and is not.** `OpinionArchiveMetadata` is KodaDash's
reading of one filing, and three of its four projected columns are *fallbacks* —
they are what the row says only where the archive's filename said nothing. Where
the naming convention was followed the column never moves, which is exactly what
made the relation easy to leave out. But `external_id` is unioned into
`identifiers` unconditionally, and it is the letter's register-side handle: the
only string by which somebody holding a KodaDash reference can find the letter at
all. And a great many archive filenames carry no recipient and no date, which is
where the fallback is the value being projected rather than a spare.

The write that mattered is an ordinary one and an operator reaches it in the
normal course. `_write_metadata` is shared by `catalogue_plan` and `apply_plan`,
and only the second suspends indexing — so a KodaDash workbook that arrives
*after* the archive has been catalogued and materialised is one `catalogue` run
writing readings against occurrences whose bytes are already held and already
projected. Reproduced on `65f89cff`, synthetic data: catalogue, materialise,
catalogue again with a workbook; the handle never reaches `identifiers` and the
letter is unfindable by it until somebody rebuilds.

**Text is the whole of the archive's full-text search.** `body_text` is what both
tsvectors are built from and `has_body_text` is what the `Sisuga` filter and the
coverage figure read. `opinion_text._record` is the one production writer — an
`update_or_create` per binary — and the *update* branch is the case the existing
verifier could not see: a re-extraction replaced the canonical body while the
projection went on serving the previous parse, so the letter stayed findable by
words that had been superseded and could not be found by the ones that replaced
them. The same shape with the other sign is `DONE` → `BLOCKED`, which is what
turning `REAL_DATA_ALLOWED` on does to a corpus that had been extracted: the
policy withdraws permission to open the file and the search goes on serving its
contents.

**Same class as the other four.** One metadata row names one occurrence, which
names at most one binary; one text row names exactly one binary. Both are
bounded, so both refresh inside the business transaction through `_row_values`,
in `opinion_search_signals.py`, and a rolled-back write takes its projection
change with it.

**Extraction is not batched behind a suspension, deliberately.** Wrapping
`extract_all` in `suspend_archive_indexing` and refreshing at the end would be
cheaper and would reintroduce the defect: hundreds of canonical bodies would
commit while their rows stayed stale, and an extraction killed halfway — which is
how a 767-file run over real bytes ends when something goes wrong — would leave
exactly the stale search this closes. `_record` is already `@transaction.atomic`
per letter, so each committed body is committed with its projection. `apply_plan`
keeps its suspension and its bounded refresh, which now converge the metadata it
wrote as well as the candidates.

**Two different delete guards, and the difference is the point.**
`OpinionArchiveMetadata` uses `_started_here`: its only foreign key is `item`, so
the only cascade that can reach it is an occurrence's deletion, and that
occurrence's own `post_delete` already owns the final refresh. Firing from the
metadata handler during that cascade would be both redundant and *wrong* — the
collector removes children before their parent, so it would recompute a row that
still contained the occurrence being deleted, which is the one-statement-early
mistake the candidate follow-up was written to avoid. `OpinionArchiveText` uses
`_binary_survives`, and here that guard stops being defensive: text is `CASCADE`
from the binary and is the first archive relation for which that path is
reachable at all, because the occurrences and candidates are kept off it by
`OpinionArchiveItem.binary` being `PROTECT`. Re-projecting a binary mid-cascade
would insert a row the collector had already swept past and the binary's own
delete would fail on a foreign key at COMMIT.

**Both are move-capable, and neither writer moves them.** `metadata.item` is an
ordinary editable foreign key and `text.binary` is a `OneToOneField`, so an
ordinary save can repoint either — leaving the row it left still projecting a
handle or a body that has gone elsewhere. The binary being left is captured on
`pre_save`, believing `update_fields` and testing suspension before the lookup,
so `_record` (which saves eight named columns, none of them `binary`) pays no
SELECT for a move it cannot make.

**Text drift verification, replaced rather than extended.** The check that stood
here was one-directional — canonical `DONE` with a body, projection saying
`has_body_text=False` — which caught the case that had happened and was silent
about every other one. `archive_index_findings()` now recomputes both columns
through `_text_values`, the row builder's own function, and reports the count of
rows that disagree, in two findings: a flag that disagrees means the filter and
the coverage figures are wrong, and a *body* that disagrees means the search is
wrong, which is worse and far harder to notice by looking. Both directions follow
from the comparison rather than from a list of cases. `has_body` is not restated
anywhere: it stays the model's own property, and neither the builder nor the
verifier re-derives it in Python or in SQL. Aggregate counts only — the column
being compared is the contents of a letter.

Metadata needed no new verifier. `_occurrence_values` already unions
`external_id` into `identifiers` and already resolves the heading fallback, so
the identifier and heading checks the previous follow-up added see a metadata
change without any metadata-specific comparison — and adding one would have been
a second copy of the precedence rule.

**The map is now read off the row builder rather than written down beside it.**
Every one of these follow-ups started the same way: somebody read `_row_values`,
found a relation nobody had noticed, and the corpus had been stale on it for a
fortnight. So `tests/test_opinion_archive_search.py` discovers the inputs by
capturing the queries `_row_values` actually runs and asserting that every table
among them has a `post_save` and a `post_delete` owner. A scan of the function's
source text would not have done: `OpinionArchiveText` is reached as `binary.text`
and is named nowhere in it. A seventh relation now fails that test with the
table's own name.

**After this, `OpinionArchiveSearchDocument` has no ordinary mutable input
without a freshness owner.** The six relations are covered by handlers; the only
other input is the binary's own `sha256`, and `OpinionArchiveBinary` has no
`save()` anywhere in the application — `materialize` creates one and
`_link_occurrences` refreshes it, deleting one takes its projection row with it
by `CASCADE`, and a held binary with no row at all is what `unindexed_binaries()`
reports. A `sha256` edited by hand would still be *detected*, by the identifier
check that recomputes the column from it. `index_version` is the remaining input
and is a constant, which is what the stale-version finding is for.

### What this amendment does not do

Nothing about extraction policy (ADR 0014 stands: an unscanned file is not opened
where real data lives, and this makes it no more permissive), the parser, OCR,
`ArchiveTextState` semantics, archive authorization (ADR 0056), matching
classifications, or what counts as evidence for a link (ADR 0055). No schema
change and no index-version bump: the projected values are byte-for-byte what the
previous builder produced, so no rebuild is required by this change. It does not
run any rebuild on production.
