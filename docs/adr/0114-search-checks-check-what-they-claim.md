# 0114 — Search checks check what they claim, and the manual repair repays

**Status:** accepted
**Date:** 2026-09-25

**Amends ADR 0041** on one point: a manual `rebuild_search_index` now pays off
the debt it covers. The worker's own path is unchanged.

## Context

Three findings about the tools an operator uses to trust search. Each was
confirmed on the code as of PR C (#293):

* **ENG-080.** `check_search_integrity` had four gaps:
  * **Narrow drift check.** It recomputed text for only 250 MATTER rows, taken
    in UUIDv7 order, which is always the rows written longest ago.
  * **Child rows never checked.** It recomputed no child row, so no author name
    was checked either.
  * **Partial crossings.** It compared crossings for five of eight kinds.
  * **Wrong repair hint.** It named `refresh_matter_search`, which cannot repair
    a child row.

  Operator documentation presented it as proof that search was complete and
  current.
* **ENG-085.** `rebuild_search_index` rebuilt the index and left every
  `SearchRebuildDebt` row. Freshness, the container healthcheck, stayed red
  after the repair, and the worker then rebuilt the whole corpus again.
* **ENG-142.** A release that moved `INDEX_VERSION` left `deployment_readiness`
  and the healthcheck green while search answered nothing. The rebuild step
  depended on somebody remembering that this release moved the version.

## Decision

**One registry of source kinds for the integrity check** (`kind_contracts`).
* **What each kind states:**
  * how its row reaches the Matter its source belongs to;
  * how its text is rebuilt, which is through the indexer's own builders;
  * which command repairs it.
* **What is derived from the registry:** crossings, text drift and repair
  hints, for every kind.
* **Completeness is tested.** A test holds the registry equal to
  `SearchSourceKind`, so the next kind cannot be added without being checked.
* **Two modes, and the command says which it ran:**
  * **Default:** a deterministic, corpus-wide sample of each kind, ordered by a
    hash of the source id. It answers "is a rebuild owed".
  * **`--full`:** recomputes every row. It is the only mode that can say
    "current", and the deployment proof uses it.
* **Author names are covered,** because the builders project them.

**The manual rebuild discharges what it covers** (`freshness.rebuild_and_discharge`).
The command and the worker share one path:
* claim the debt rows that exist now;
* rebuild atomically;
* after commit, delete only the claimed rows.

Debt written during the rebuild survives. A failed rebuild records the attempt
on the claimed rows and deletes nothing, so freshness stays red and says why.
The Round 4 lock order is untouched: the rebuild still takes the gate's
exclusive side inside `rebuild_all`.

**A moved `INDEX_VERSION` cannot be missed.**
* **The release says so.** `scripts/ci/index_version_change.py` compares the
  constant at the two commits the release workflow already has. The manifest
  records `index_version: OLD -> NEW` and `search_rebuild_required: YES/NO`.
  An unreadable target refuses the build, and an unreadable previous version
  counts as a change.
* **Readiness has two phases.** `deployment_readiness` gains a search phase:
  * **final (default):** fails on any row built under an older version, or on
    Matters with no current row at all;
  * **`pre-rebuild`:** reports the same state as the expected warning of a
    release whose manifest says YES, before its rebuild.
* **Runbook order.** The runbook runs the pre-rebuild phase at step 10, then
  the rebuild, `check_search_integrity --full` and the final readiness at step
  11. A release that does not move the version is unaffected.

## Consequences

* A deployment that skips the rebuild now ends red, at the final readiness
  check and in `production_status`, which rolls readiness up.
* `--full` recomputes every row. On the synthetic corpus of 12,000 rows it is
  seconds; it is an operator's proof, not a healthcheck.
* Search-query performance is untouched (ENG-010 remains its own round).
