# 0154 — CI shards share runners, and main is proved by its pull request's run

**Status:** accepted
**Date:** 2026-10-11

## Context

The owner asked for a materially faster pull-request round without weakening
any gate. Eight green runs before the change (the latest, 38110923538) measured:

* **Wall clock and critical path.** About 697 s of wall clock. The critical path
  was the slowest browser shard, about 663 s: roughly 80 s of setup and 575 s of
  tests. Then came 30–40 s of an informational balance job.
* **The ceiling was job slots.** The account runs 20 jobs at once. Six fixed
  jobs plus 7 + 7 shards used all twenty from the first second. More runners
  were not available.
* **The cores were idle.** A runner for a public repository has four vCPUs, and
  each shard used about one.
* **The runner set PostgreSQL's speed.** The same PostgreSQL files ran 0.57x
  to 1.63x their usual time depending on the runner they landed on, and the
  test database's own setup took 14–35 s. The shard timing model predicted
  402 s for every shard; they took 237–382 s. Runner variance, not the
  assignment, explained the gap. Logging each runner's CPU later showed it is
  mostly the CPU generation a runner draws: an AMD EPYC 7763 ran the same shard
  about 1.7x slower than an EPYC 9V45. Browser runners varied far less
  (0.79–1.10).
* **Main repeated a finished proof.** After a merge, main's push run re-tested
  the exact tree the pull request's run had just proved. For about eleven
  minutes it held every slot, so a pull request updated in that window ran
  21–25 minutes instead of 11.

## Decision

**1. A runner hosts several shards at once, each in a world of its own**
(`ci_slices.py`, `scripts/ci/run_slices.py`).
* **The split.** The suites are cut into more shards than there are runners
  (`--shard-count`), and runner *r* of *R* hosts a consecutive block of them
  (`slices_for`).
* **What a shard runs is unchanged.** It is still `ci_sharding.partition` over
  the collected files: whole files, in serial order, a pure function of its
  inputs.
* **Nothing but the machine is shared.** Each hosted shard gets its own:
  * database on the runner's PostgreSQL;
  * three browser servers on their own ports;
  * evidence, derivative, legacy-source and pending-upload roots;
  * mail and screenshot directories, `TMPDIR`, coverage fragment, JUnit report
    and Playwright output.

  All of it comes from one function, applied to the shard's servers and its
  pytest process alike.
* **Tests stay serial inside a database.** No test runs in parallel with
  another inside a database.

**2. The server-wide views are scoped to the current database.**
`pg_stat_activity` and `pg_locks` show every database on a server.
`rebuild_is_running()` and five test lock-wait helpers now filter by
`current_database()`, and the helpers clear the statistics snapshot before each
look. A deployment has one database per server, so nothing changes there.

**3. The layout is checked in two places.** The runner matrix, the runner
script's `--runners` and the pytest `--shard-count` are one layout
(`ci_workflow_selection.topology`). The completeness proof, the health report
and `tests/test_ci_sharding.py` all read it. Both gates also require exactly one
report (and, for PostgreSQL, one coverage fragment) from every shard, 1..N, so
a shard that never ran turns the build red.

**4. The test and browser PostgreSQL keep their data in memory** (`--tmpfs`).
It is the same PostgreSQL 18, with the same configuration and the same SQL and
transaction semantics. What changes is crash durability, and a test database
lives and dies inside its job, so it has none to lose. GitHub service
containers cannot pass `-c fsync=off`, so this is how the owner's approval of
"fsync off for the throwaway test databases" is applied. The owner made that
approval conditional on a measured win. The A/B on one commit and layout
measured about 10% faster PostgreSQL runners on matching hardware, and a
round of 371 s against 411 s. It did not narrow the spread between runners,
which is CPU generation. Migration safety and the recovery rehearsal keep a
disk.

**5. Main is proved by its pull request's run** (`.github/workflows/main-proof.yml`,
`scripts/ci/assert_main_proved.py`).
* **No push run.** `ci.yml` no longer runs on a push to main.
* **The proof.** A small job applies the release gate's own proof (b), imported
  rather than copied:
  * the commit is a two-parent merge;
  * its pull request's latest `ci.yml` run completed green in every job;
  * that run recorded this commit's tree as the one it tested.
* **When the proof fails.** Anything short of that dispatches the whole of
  `ci.yml` on main.
* **The release gate.** It now accepts such a dispatched run as main's own, and
  still re-derives proof (b) itself.

**6. Two pairs of short fixed jobs share a slot.**
* **The pairs.** `Container and compose smoke test` runs after `Dependency
  vulnerability baseline`, and `Backup and restore rehearsal` runs after
  `PostgreSQL migration and runtime safety`.
* **Each still runs.** Each waits with `!cancelled()`, so it runs and reports
  under its own name whatever its predecessor did. A job a failure would skip is
  refused by `tests/test_ci_sharding.py`.
* **The gain.** Both pairs finish inside the slowest shard runner, and the two
  slots they free are two more shard runners.

**7. The timing table is the median of several runs**
(`update_shard_timings.py --latest N`). Each file's per-test rate is the median
across the runs, so one slow runner's afternoon does not become a file's
weight. The balance report reads the run's own reports instead of collecting,
and reports per runner: setup apart from tests, and the hardware it drew.

## Consequences

* **Faster rounds.** Benchmarked on pull request #423; the numbers are in
  `docs/ci-architecture.md`.
* **A shared failure domain.** A PostgreSQL crash on a runner resets every
  shard on it. The runner names that in the failing shard's neighbour's output,
  so a red shard is not blamed on its own tests.
* **Hardware is now the visible variance.** Hosted runners differ by up to
  ~1.6x per core, and a runner hosting three shards magnifies it. That is why
  every runner hosts two.
* **Main shows a proof job, not a test run.** A commit with no proving pull
  request run still gets the full suite, started automatically. The release
  gate is unchanged in what it requires.
* **Not done.** GitHub Pro (40 jobs) remains the larger lever and is the
  owner's purchase to make.
