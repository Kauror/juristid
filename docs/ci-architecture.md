# CI architecture

What runs on every pull request, why it is split the way it is, and what to do
when a piece of it goes red.

The short version: nothing was removed to make CI faster. The two suites that
took the time — the PostgreSQL suite and the browser suite — were split across
independent runners, and the visual scenarios were given a world of their own.
Every test that ran before still runs, exactly once, and there are two
independent proofs of that.

## The six logical gates

CI proves six things. That has not changed, and the names have not changed
either — a required status check that said `PostgreSQL 18 test suite` yesterday
still says it today.

| Gate | Job that reports it | What stands behind it |
| --- | --- | --- |
| Quality | `Format, lint, types and system checks` | ruff, mypy, Django checks, shellcheck, era contracts, shard completeness |
| PostgreSQL tests | `PostgreSQL 18 test suite` | `PostgreSQL migration and runtime safety` + 5 test shards |
| Browser | `Browser workflow (Playwright on PostgreSQL 18)` | 6 browser shards + `Visual regression` |
| Container/compose | `Container and compose smoke test` | unchanged |
| Backup/restore | `Backup and restore rehearsal` | unchanged |
| Dependency baseline | `Dependency vulnerability baseline` | unchanged |

The two gates with several jobs behind them are *aggregators*. They exist so a
reviewer reads one result instead of eleven, and they are written so that they
cannot be greener than what they aggregate:

* they run `if: always()`, because a gate that only runs when its dependencies
  succeed is a gate that gets **skipped** when they fail — and a skipped
  required check blocks nothing;
* they fail unless every dependency reported exactly `success`. `cancelled` and
  `skipped` fail too: a cancelled shard proved nothing.

Every job still starts immediately. Nothing waits for lint. If quality fails in
half a minute, that is visible in half a minute, and
`cancel-in-progress` still stops the whole run — matrix jobs included — when a
newer commit arrives.

## How the suites are split

`ci_sharding.py` partitions the files pytest collected across the runners the
workflow asked for. Two properties matter more than speed:

**Every test is in exactly one shard.** The partition is computed from
`session.items` — the files pytest actually collected on this commit — and never
from a list anybody maintains. A file another branch adds is partitioned the
moment it is collected. There is no list to forget to update, which is the whole
reason this is not four hand-written `--ignore` arguments.

**The assignment is reproducible.** It is a pure function of the collected file
set, the test count per file, the shard count and the timing table. No clock, no
randomness, no "whichever worker was free". Re-running shard 3 runs the same
tests, which is what makes a failure investigable. This is also why the suite is
not run under `pytest -n`: xdist's assignment depends on worker availability, so
the same commit does not produce the same distribution twice.

Files are never split. The browser suite drives one long-lived server and
mutates its database as it goes, and a module was written assuming that what
precedes a test inside it is whatever that module did. Whole files also mean
each shard is an **order-preserving subsequence** of the serial run that is
green on `main`: a shard can never reach a file *earlier* than the unsharded
suite reaches it.

### Reproducing a shard locally

The command in the workflow is the whole mechanism, so it works anywhere:

```bash
uv run pytest --shard-count=5 --shard-index=3
```

```bash
uv run pytest e2e --ignore=e2e/test_ui_regression.py --shard-count=6 --shard-index=2 --browser chromium
```

To see which files a shard holds without running them, add `--collect-only -q`.
Omit both options — or pass `--shard-count=1` — and pytest behaves exactly as it
did before this existed.

### Interpreting a shard failure

A red shard is an ordinary red suite: the tests it names failed. Read them
first, and only then consider the split. Three things are worth knowing:

* **The shard number is not stable across commits.** Adding a test file
  re-balances the partition, so "shard 3" in one run is not the same set as
  "shard 3" in another. Reproduce by node id, not by shard number.
* **A shard that reports `UsageError` is a misconfigured matrix**, not a broken
  test — the index does not exist in the count, or the count exceeded the number
  of files. `tests/test_ci_sharding.py` normally catches that first.
* **A failure that only appears in a shard** and not in the whole suite is an
  order dependency between test files. Reproduce it by running the shard's file
  list in order; do not paper over it by moving the file.

## Visual regression runs in its own world

It used to run in the same job as the behavioural browser suite, immediately
after it — which meant the screenshots were taken against a database that
fourteen minutes of browser tests had been writing to. Every Matter they
created, every stage they moved and every deadline they set was still on the
page being photographed. A baseline that depends on what unrelated tests
happened to leave behind drifts for reasons nobody can name.

The `Visual regression` job now gets its own PostgreSQL, its own migrations, its
own `seed_e2e_data`, its own two servers and its own Chromium, on the same
runner image with the same fonts. The only thing that changed about what it sees
is what ran before the first screenshot: nothing.

This is a determinism improvement that happens to also take the visual suite off
the end of the browser critical path. **Baselines were not regenerated for it.**
The committed renderings still match, which is itself the evidence that the
screenshots did not depend on the leftover state — had they, the split would
have been visible as a diff.

## Coverage

Every shard measures coverage; the `PostgreSQL 18 test suite` gate downloads the
fragments, runs `coverage combine`, and prints one report for the whole suite —
the same number CI printed when the suite ran in one process, and now also
uploaded as an artifact. The instrumentation cost is divided across the shards
along with everything else, so keeping it costs a fraction of what it did.

Shards pass `--cov-report=` deliberately: a partial report printed five times
reads as a coverage collapse.

There is no `fail_under` and there never was; coverage here is informational.
Nothing about that changed, and the instrumentation was not removed from pull
requests to buy time — sharding pays for it instead.

`COVERAGE_CORE=sysmon` — the PEP 669 core, which on 3.13 is close to free where
the default tracer roughly doubles a run — was measured and **rejected**. On the
same subset it reported 39% where the default core reported 40%, missing 328
lines the default one saw. A cheaper number that means something slightly
different is not the same number, and quietly lowering the reported figure is
not a performance improvement. If the gap closes in a later coverage.py, this is
worth revisiting.

## The timing table

`ci/shard-timings.json` holds measured seconds and test counts per file. It is
an optimisation input and nothing else — **no test's membership depends on
it**. A wrong table costs balance, never correctness:

* a file that has grown since it was measured is weighted at its measured cost
  scaled by how many tests it holds now;
* a file nobody has measured is weighted at the median per-test cost of its own
  directory times its test count;
* a file with neither falls back to the median measured file.

That is why a branch that adds tests needs to do nothing at all. Refresh the
table when balance has visibly drifted — one shard finishing minutes after the
others — from any ordinary green run:

```bash
gh run download <run-id> --dir /tmp/junit --pattern 'test-report-*'
```

```bash
uv run python scripts/ci/update_shard_timings.py /tmp/junit --run <run-id>
```

Every sharded job uploads a JUnit report, so one run's reports describe the
whole suite. `tests/test_ci_sharding.py` fails if the table names files that no
longer exist, so it cannot quietly rot.

## Why the suite is complete, and how that is proved

Two independent proofs, at different levels:

**`tests/test_ci_sharding.py`** proves it about the function, for shard counts
from 1 to 13 over the repository's real file list: the union of the shards is
the input, no file is in two shards, the result does not depend on the order the
files were given, an unmeasured file still lands in exactly one shard, and an
out-of-range index raises rather than silently running the wrong slice. It also
reads `.github/workflows/ci.yml` and asserts the matrix indexes are exactly
`1..N` and that the job passes the matching `--shard-count` — the one place a
matrix of `[1, 2, 3]` against a count of 4 would otherwise leave a quarter of the
suite unrun with every job green.

**`scripts/ci/assert_shard_completeness.py`** proves it end to end. It runs the
same pytest commands the workflow runs — every shard, plus the unsharded suite —
and compares collected node ids as sets: union equals the whole, no overlaps, no
empty shard. It also checks the `--ignore` that separates the behavioural browser
suite from the visual one, so a file collected by neither job is a red build.
It reads the shard counts out of the workflow rather than from a constant, so
what it proves is what CI will actually do.

It runs in the quality job, which has no database and finishes in well under a
minute. The proof that the slow jobs are complete should not itself be slow.

## What was measured

Baseline, run [33321958813](https://github.com/Kauror/juristid/actions/runs/33321958813),
and the architecture benchmark, run
[33323182379](https://github.com/Kauror/juristid/actions/runs/33323182379).

The PostgreSQL suite is 5328 tests with a mean of 0.116s and a slowest single
test of 6.56s — many comparable tests, no pathological ones, so the only lever
is running them in more places at once. The browser suite is the opposite shape
in miniature: 423 tests over 24 files, one of which (`test_kpi_navigation.py`,
109s) is twice the next and therefore sets the floor for how short a browser
shard can be, whatever the shard count.

`pytest -n` was benchmarked and rejected on two grounds, in that order: its work
assignment is not reproducible, and running the whole suite through one
PostgreSQL and four contended cores was measurably slower than the same work on
independent runners. It also turned up a latent order dependency in the suite
that independent shards do not (see below).

## A latent order dependency, reported not fixed

Under `pytest -n --dist loadfile`, which reorders whole files across workers,
between 28 and 82 tests fail with `StageVocabulary.DoesNotExist` or
`PolicyArea.DoesNotExist`. Those rows are created by data migrations, and a
`django_db(transaction=True)` test flushes every table at teardown without
restoring them — so the serial suite is green partly because of the order its
files happen to run in.

The sharding here does not depend on that luck being repeated: whole files, in
collection order, means every shard is an order-preserving subsequence of the
green serial run. But the fragility is real, it is in the test fixtures rather
than in the application, and it will bite the next person who reorders anything.
It is out of scope for a CI-performance change and is written down here so it is
not rediscovered from scratch.
