# 0193 — CI runs FAST for drafts and FULL for merge readiness

- Status: Proposed (owner's CI efficiency reform brief, phase 1, 2026-10-28;
  PR #561, not merged; branch protection unchanged)
- Amends: the trigger and job conditions of `.github/workflows/ci.yml`; the
  release gate's proofs (ENG-015, `scripts/ci/assert_release_provenance.py`),
  which `main-proof.yml` imports
- Relates to: docs/ci-architecture.md (the six gates, sharding, main proof)

## Context

Every push to a pull request ran the whole of CI: 25 jobs, ~122 runner-minutes,
~8.4 minutes of wall clock. On 26–27 October 2026 that was 54 run attempts and
**6,243 runner-minutes**: 19 green, 18 red, 14 cancelled because a newer commit
arrived, and 3 full re-runs. The cancelled runs alone were ~1,250
runner-minutes. The architecture had been optimised for wall clock (every
shard at once, nothing waiting for lint), which is right for a round that must
merge and wasteful for a round that is still being written.

## Decision

### Two tiers, from one fact of the event

| Tier | When | What runs |
| --- | --- | --- |
| **FAST** | `pull_request` whose PR is a **draft** (`opened`, `synchronize`, `reopened`) | `Format, lint, types and system checks` (unchanged, incl. the completeness proof) + `Focused tests (FAST tier)` |
| **FULL** | everything else: `ready_for_review`, a push to a ready PR, `workflow_dispatch` on any branch (incl. main-proof's fallback on main) | the whole of CI as before, plus a one-minute `Preflight` the expensive jobs wait for |

The condition is written out as one of two strings in every job, and
`tests/test_ci_tiers.py` refuses a job written any other way:

- FAST: `github.event_name == 'pull_request' && github.event.pull_request.draft`
- FULL: `github.event_name != 'pull_request' || !github.event.pull_request.draft`

`converted_to_draft` is not a trigger: a commit that was FULL-verified stays
verified, and the draft's next push is FAST (and cancels any FULL run still in
flight through the existing `concurrency` group).

### Required checks are earned, never inherited from a skip

GitHub counts a **skipped** required check as passing. On a draft, the jobs
behind the five non-quality required checks are therefore skipped **under
another name** (`… (FULL tier, not run on a draft)`), so the draft's commit
carries no check under a required name it did not earn. Branch protection then
shows those five as *Expected* and blocks the merge. This is what closes the
window between `gh pr ready` and the FULL run's checks appearing on the same
commit, which `gh pr ready && gh pr merge` would otherwise fall into.

Branch protection is unchanged: the same six contexts, `strict`, admins
included. FULL reports each under exactly the name it has always had.

### FAST evidence can never release or prove main

Both proofs of the release gate (main's own run; the PR's run on the identical
tree) now also require **all six required gates among the run's jobs, each
`success`** — a FAST run lacks five and fails that independently of its skipped
jobs. Proof (b) also requires the tested-tree record to say `tier full`; a FAST
run records `tier fast`, and a record from before the tiers has no tier line and
is refused (fail closed: at most one FULL run on main, and branch protection's
`strict` rule means every PR merging after this change has a new record).

### Fail fast before provisioning, without a misleading green

In FULL, `postgres-safety`, the 7 PostgreSQL runners, the 9 browser runners and
`Visual regression` wait for `Preflight (format, lint, types, imports)` —
`ruff format`, `ruff check`, `mypy`, `manage.py check`, and one
`pytest --collect-only` of `tests` and `e2e`. When it fails they are skipped;
the two suite gates (`always()`) refuse a failed or skipped dependency, and the
two chained required leaves (`Backup and restore rehearsal`, `Container and
compose smoke test`) run, refuse in their first step, and report red under their
required names. The shard matrices keep `fail-fast: false`.

### The focused job is a convenience, not a selection mechanism

`ci_focused_selection.py` picks the database-free contract tests, the CI tests
when CI changes, and every changed `tests/**/test_*.py`, within a 240-second
budget priced from `ci/shard-timings.json` (the heaviest are deferred and
named). It is not a required check, nothing in FULL imports it, and the release
gate never reads it. FULL's suites are still the whole collection, partitioned by
`ci_sharding.py`, proved complete by `assert_shard_completeness.py`.

## Consequences

- An iterating author pays ~3 minutes and ~5 runner-minutes per draft push
  instead of ~8.4 minutes and ~122; the FULL round is ~1 minute longer (the
  preflight) and costs about one runner-minute more.
- A PR opened ready for review, or pushed to while ready, still runs FULL on
  every push. The saving depends on agents opening drafts and marking ready once
  (AGENTS.md, "CI tiers").
- Visual baseline candidates come from a FULL run's `test-report-visual`
  artifact, and — since the amendment below — from a UI draft's FAST run.
- Nothing about main-proof changes: it still accepts a green PR run on the exact
  tree (now: a FULL one) and otherwise dispatches FULL on main.

## Amendment: the visual job on a UI draft (2026-10-28, owner's go-ahead)

A draft whose change touches `templates/`, `static/` or `e2e/` also runs
`Visual regression` in FAST. A ten-second FAST-only job, `changes`, decides it
with `ci_focused_selection.touches_ui` (standard library only, on the bare
runner). The visual job keeps its name, world and artifact; its condition is
`!cancelled()` and either FULL with a green preflight, or FAST with `ui ==
'true'`. FULL is unchanged. On a draft no required check aggregates it, so it
is information, never evidence (`tests/test_ci_tiers.py`).

Why: replaying 26–27 Oct, 7 of the 18 red FULL runs failed only in visual
regression — baselines an intended UI change moved, found a FULL round too
late. With the visual job in FAST those become ~9-runner-minute draft rounds;
the replay goes from 32% to ~45% fewer runner-minutes.

### Correction: a FULL run's FAST-only skips (2026-10-28)

As merged in #561, the gate required every job of a run to be `success`, but
every FULL run skips the FAST-only jobs by design — so no FULL run could prove
anything. Main-proof found it on the merge of #561 (`7a066621`), failed closed
and dispatched FULL on main; the release gate would have refused too. The
gate now accepts exactly one kind of non-success: a **skipped** job whose name
is in `FAST_ONLY_JOBS` (`Focused tests (FAST tier)`, `Does the draft touch the
UI (FAST tier)`). A FAST-only job that ran and failed is still refused, and
`tests/test_ci_tiers.py` holds that list to `ci.yml` and checks a FULL run as
the jobs API reports it — skips included — which is what the original test had
filtered away.

## Rejected

- **A separate FAST workflow file.** `ci.yml` would still trigger on a draft's
  push and report its required checks as skipped — the same hole, in two files.
- **Required checks failing red on drafts.** Closes the hole, but every draft
  push would read as a failure and wake the CI-fix automation.
- **Path-based test selection inside FULL.** The brief forbids it, and the
  completeness proof exists precisely to refuse a narrowed suite.
- **Cancelling the run from the preflight.** No wall-clock penalty, but the run
  reads as `cancelled` — indistinguishable from a superseded run.
