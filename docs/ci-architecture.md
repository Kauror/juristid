# CI architecture

What runs on every pull request, why it is split the way it is, and what to do
when a piece of it goes red.

## Two tiers: FAST for a draft, FULL for merge readiness (2026-10-28)

Since docs/adr/0193 the whole of CI described below is the **FULL** tier. A
pull request that is a **draft** runs the **FAST** tier instead:

| | FAST (draft PR) | FULL (ready PR, `ready_for_review`, any `workflow_dispatch`) |
| --- | --- | --- |
| Jobs | `Format, lint, types and system checks`, `Focused tests (FAST tier)`, `Does the draft touch the UI`; plus `Visual regression` when it does | `Preflight`, then everything in this document |
| Wall clock | ~3 min; ~4–5 min when the draft touches the UI | ~9–10 min |
| Runner-minutes | ~4.5; ~9 when the draft touches the UI | ~122 + ~1 for the preflight |
| Merge or release evidence | **never** | yes — the only evidence there is |

What decides it is one expression, written out in every job's `if:` and held
there by `tests/test_ci_tiers.py`: a `pull_request` event whose PR is a draft is
FAST; anything else is FULL. So marking a draft ready runs FULL; a push to a
ready PR runs FULL again (its previous result was for another commit and does
not carry over); converting back to draft starts nothing, and the next push is
FAST; and a run started by hand or by `main-proof.yml`, on main or on a branch,
is FULL.

**Required checks on a draft.** GitHub counts a skipped required check as
passing. On a draft, the five non-quality gates are skipped **under another
name**, so a draft's commit carries no check under those required names and
branch protection shows them as *Expected*. That is what stops a merge in the
seconds between `gh pr ready` and the FULL run's checks appearing on the same
commit. GitHub does not evaluate the `name:` expression of a skipped job, so on
a draft the Checks tab lists those five by their raw expression text — expected,
and not the required name either way. The quality job reports under its
required name in both tiers and is identical in both.

**Failed prerequisite.** In FULL, the PostgreSQL safety job, the PostgreSQL and
browser runners and the visual job wait for `Preflight (format, lint, types,
imports)` (~1 minute). If it fails they are skipped, both suite gates refuse a
skipped dependency, and `Backup and restore rehearsal` and `Container and
compose smoke test` run and refuse in their first step. Every required check is
red; none is a green skip.

**Evidence.** The release gate and `main-proof.yml` accept a run only if it holds
all six required gates, each `success` (the only skips allowed are the
FAST-only jobs', which every FULL run skips), and — for a pull request's run — a
tested-tree record that says `tier full` (see "Main is proved by its pull
request's run").

**The focused job** runs the database-free contract tests, the CI tests when CI
changed, and the changed `tests/**/test_*.py` files, within a 240 s budget
(`ci_focused_selection.py`; deferred files are named in the job summary). It is
a convenience for the author: not required, not read by any gate, imported by
nothing in FULL. The behavioural browser shards run only in FULL.

**The visual job on a UI draft.** When a draft changes `templates/`, `static/`
or `e2e/` (`ci_focused_selection.touches_ui`, decided by the ten-second
`changes` job on the bare runner), FAST also runs `Visual regression` — the
same job, same world, same artifact (`test-report-visual`) as in FULL. A draft
that moves a baseline learns it, and gets the candidates, without a FULL round;
in the 26–27 Oct history 7 of 18 red FULL runs were visual-only. On a draft no
required check aggregates it (the browser gate does not run), so a red visual
job blocks nothing and is never evidence. In FULL the job is exactly what it
was: after a green preflight, aggregated by `Browser workflow (Playwright on
PostgreSQL 18)`.

The short version: nothing was removed to make CI faster. The two suites that
took the time — the PostgreSQL suite and the browser suite — were split into
shards, each runner hosts two of them at once in worlds of their own, and the
visual scenarios were given a world of their own. Every test that ran before
still runs, exactly once, and there are three independent proofs of that. Main
does not run the suite again after a merge: the pull request's run already
tested the exact tree the merge commits (see "Main is proved by its pull
request's run").

## The six logical gates

CI proves six things. That has not changed, and the names have not changed
either — a required status check that said `PostgreSQL 18 test suite` yesterday
still says it today.

| Gate | Job that reports it | What stands behind it |
| --- | --- | --- |
| Quality | `Format, lint, types and system checks` | ruff, mypy, JavaScript parse and lint (`node --check`, ESLint pinned, `eslint.config.mjs`), Django checks, shellcheck, era contracts, shard completeness, shard health |
| PostgreSQL tests | `PostgreSQL 18 test suite` | `PostgreSQL migration and runtime safety` + the test shards, two per runner |
| Browser | `Browser workflow (Playwright on PostgreSQL 18)` | the browser shards, two per runner, + `Visual regression` |
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

Nothing waits for lint: if quality fails, that is visible as soon as quality
finishes, and `cancel-in-progress` still stops the whole run — matrix jobs
included — when a newer commit arrives. Two short jobs wait for another job,
for a slot rather than a result: `Container and compose smoke test` runs after
`Dependency vulnerability baseline`, and `Backup and restore rehearsal` after
`PostgreSQL migration and runtime safety`, each with `!cancelled()` so it runs
and reports whatever its predecessor did. Both pairs finish well inside the
slowest shard runner, and the two slots they free are two more shard runners
(`tests/test_ci_sharding.py` refuses a chained job that a failure would skip).

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

### Several shards on one runner

A runner has four vCPUs and a shard used about one, while the account's twenty
concurrent jobs were all in use. So the suites are cut into more shards than
there are runners, and each runner hosts a consecutive block of them at the same
time: runner *r* of *R* runs shards `ci_slices.slices_for(r, R, shard_count)`.
The workflow states it three times — the `runner` matrix, `run_slices.py
--runners`, and pytest's `--shard-count` — and `ci_workflow_selection.topology`
refuses any layout where they disagree or the shards cannot be shared equally.

**What a shard runs does not change.** It is still the partition above, so
hosting changes which machine a shard runs on, never what is in it, and inside
a database tests still run one at a time.

**What a shard shares with its neighbour is the machine, and nothing else.**
`ci_slices.shard_environment` gives each hosted shard its own database on the
runner's PostgreSQL (`juristid_<suite>_<n>`, so pytest-django creates
`test_juristid_tests_<n>`), its own `TMPDIR`, its own coverage fragment and, for
the browser suite, its own three servers on its own ports (8000/8001/8002 for the
first shard on a runner, ten higher for the next), its own evidence, derivative,
legacy-source and pending-upload roots, mail directory, screenshot directory and
Playwright output. The servers and the pytest process are started from the same
environment, which matters because browser tests start `manage.py` subprocesses
that read the pytest process's variables, not the server's.
`tests/test_ci_slices.py` proves the property: two shards on one runner differ in
every one of those variables, no path of one contains or lies inside the other's,
every `*_ROOT`/`*_PATH`/`*_DIR` the settings or `e2e/` read is either per shard
or a named read-only input, no source names a fixed `/tmp` path, and every SQL
string that reads `pg_stat_activity` or `pg_locks` — views that show every
database on the server — is scoped to `current_database()`.

The one thing two shards on a runner do share is the PostgreSQL *server*, so a
crash of one backend resets every shard's connections. `run_slices.py` names
that in a failing runner's output («crash of another server process»), so a red
neighbour is not blamed on its own tests.

**The test and browser PostgreSQL keep their data in memory** (`--tmpfs
/var/lib/postgresql` on the service). Same PostgreSQL 18, same configuration,
same SQL and transaction semantics; only crash durability changes, and a test
database lives and dies inside its job. Measured A/B on one commit and layout,
the PostgreSQL runners ran about 10% faster on matching hardware (EPYC 9V45
196 s against 245 s, Xeon 8573C 348 s against 388 s) and the round took 371 s
instead of 411 s. It did not narrow the spread between runners: that is the
CPU a runner draws (below), not its disk. The migration safety job and the
recovery rehearsal keep a disk.

### Reproducing a shard locally

The pytest command in the workflow is the whole of what decides a shard, so it
works anywhere; take the counts from the workflow:

```bash
uv run pytest --shard-count=12 --shard-index=3
```

```bash
uv run pytest e2e --ignore=e2e/test_ui_regression.py --shard-count=16 --shard-index=2 --browser chromium
```

Which database or port a shard used on CI does not matter locally — those only
keep two shards on one runner apart.

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
  list in order; do not paper over it by moving the file. The one such
  dependency this repository is known to have had is closed, and guarded — see
  "The order dependency it turned up, since closed".
* **A runner's log interleaves its shards**, each line prefixed `[tests 5]` or
  `[browser 11]`; each shard's own log is also uploaded (`tests-5.log`,
  `artifacts/browser-11/…`). If both shards on a runner fail at once on
  connection errors, read for «crash of another server process» before reading
  the tests.

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
the end of the browser critical path.

**Seventeen of the thirty-six baselines had to be retaken, and what they showed
is the argument for the split.** Every one of them was *shorter* on a clean
world, because the old rendering included rows the behavioural suite had created
minutes earlier. `otsing` is the clearest: the committed baseline was a
2693px page of **21** search results including a whole `Dokument` class of
extracted PDF text and an `Arvamus`, none of which the seed creates — they came
from the document and opinion tests. On a fresh world the same query returns
**3** results, one from each of three result types, over 900px.

Two of the seventeen failed on pixel tolerance rather than size, and those
exposed a second problem: `jalgimine` and `teema-dokumendid` still carried the
navigation labels from before the Osakond and Minu asjad renames. A stale
baseline had been passing because the page *height* matched and the changed text
was under the 0.2% pixel limit. Splitting the world surfaced it.

The retaken baselines therefore depict less than the old ones did, and that is
the honest state of the deterministic world: what `seed_e2e_data` creates, and
nothing else. If a scenario deserves richer content — the search page is the
obvious candidate — the fix is to seed it deliberately, not to photograph
whatever an unrelated test left behind. The candidates were taken from the
failing run's own `test-report-visual` artifact, scenario by scenario, never
with `E2E_UPDATE_BASELINES`.

### Two tests that were only running by accident

The same split turned up two browser tests that used to pass and started
skipping themselves: the closed banner's geometry in `e2e/test_ui_shell.py` and
the `teema-suletud` rendering in `e2e/test_ui_regression.py`. Both open
`/teemad/?olek=suletud` and skip when it is empty — and it was never the seed
that filled it, it was some *other* test closing a Matter earlier in the same
job. A test that runs only when an unrelated test happens to precede it is not
a test that runs.

`seed_e2e_data` now creates one closed Matter, so both run in every shard. The
register defaults to `olek=avatud`, so it adds a row to the closed view and to
nothing else.

## Coverage

Every shard measures coverage; the `PostgreSQL 18 test suite` gate downloads the
fragments, runs `coverage combine`, and prints one report for the whole suite —
the same number CI printed when the suite ran in one process, and now also
uploaded as an artifact. The instrumentation cost is divided across the shards
along with everything else, so keeping it costs a fraction of what it did.

Shards pass `--cov-report=` deliberately: a partial report printed once per
shard reads as a coverage collapse.

**The gate checks that every shard's fragment arrived.** A runner uploads its
shards' fragments together, and an upload only refuses when *none* of its paths
match — so a shard that never ran would have left `coverage combine` combining
whatever arrived, green, with less coverage than the suite has. The gate counts
one `.coverage.shard-N` and one `junit-tests-N.xml` for every N up to the shard
count before it combines, and the browser gate counts the browser reports the
same way.

The combination is exact rather than approximately exact. The unsharded baseline
reported `24408 statements, 2174 missing, 91%`; five shards combined report
`24408 / 2174 / 91%` — the same three numbers, which is the whole claim.

There is no `fail_under` and there never was; coverage here is informational.
Nothing about that changed, and the instrumentation was not removed from pull
requests to buy time — sharding pays for it instead.

`COVERAGE_CORE=sysmon` — the PEP 669 core, which on 3.13 is close to free where
the default tracer roughly doubles a run — was measured and **rejected** at
first. On the same subset it reported 39% where the default core reported 40%,
missing 328 lines the default one saw. A cheaper number that means something
slightly different is not the same number, and quietly lowering the reported
figure is not a performance improvement. If the gap closes in a later
coverage.py, this is worth revisiting.

**Revisited 2026-10-10, and adopted** (audit AUD-031). On coverage 7.15 the gap
is gone:

- The codebase audit ran shard 4 of 7 on PostgreSQL 18.6 three ways. Without
  coverage it took 662.8 s. With the default core it took 961.4 s (+45 %).
  With `COVERAGE_CORE=sysmon` it took 638.0 s, which is within noise of no
  coverage. Both coverage runs produced identical data: 42,255 statements,
  13,970 missed.
- A local re-check on coverage 7.15.4 ran six test files with each core and
  compared `coverage report --show-missing` file by file. The reports did not
  differ in a single line (42,718 statements, 25,025 missed).

The PostgreSQL shard step therefore sets `COVERAGE_CORE: sysmon`. Per-PR
coverage, the combined report and the completeness proof are unchanged. Only
the tracer differs. Re-check the equality with the same file-by-file
comparison whenever coverage.py is upgraded.

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
table when balance has visibly drifted — one runner finishing minutes after the
others — from the newest green runs:

```bash
uv run python scripts/ci/update_shard_timings.py --latest 5
```

It downloads those runs' shard reports with `gh` and weighs each file at the
**median** of its per-test rate across them, at today's test count. One run is
not enough: the same files run up to 1.6x slower on one hosted runner than on
another, and a table taken from a single run takes that runner's afternoon as
the file's weight. The table records which runs it came from, so the same runs
always regenerate the same table; one run's reports on disk still work too
(`update_shard_timings.py DIR --run ID`). Every sharded job uploads a JUnit
report, so a run's reports describe the whole suite. `tests/test_ci_sharding.py`
fails if the table names files that no longer exist, so it cannot quietly rot.

### "Refresh it when balance has visibly drifted" was not enough

It drifted for three weeks and nobody saw it. The table said 2026-08-30 and was
read on 2026-09-21, and in between the suite had gone from 5328 tests to 10032:
**39% of the collected files had no measurement at all**, and 36% of the tests.
The partition still ran every one of them — that never depended on the table —
but it was balancing a model of a suite that no longer existed, and the slowest
PostgreSQL shard was finishing 42% above the median of its own run. Two and a
half minutes a build, invisible, because nothing about a slow shard looks wrong.

So the drift is now reported rather than waited for.
`scripts/ci/report_shard_health.py` runs in the quality job and prints, every
build: where the table came from and how old it is, how many collected files and
tests have no measurement, how many files hold a different number of tests than
the table records, the predicted load of each shard, and the slowest/median
ratio. Given a directory of downloaded JUnit reports (`--durations`) it also
prints what those runners actually did, which is the only honest check on the
prediction — and the `shard-balance` job now does exactly that on every build,
with the reports the same run's shards uploaded: predicted and actual seconds
per shard, side by side, and each column's slowest/median (ENG-139). It is not
a required check and cannot fail the build.

**Session setup is modelled apart from the files.** JUnit books a test's setup
to the test, so the one-off cost of a shard starting — creating and migrating
the test database — landed on the first test of every shard, and so on whichever
file happened to run first: a 5 s file weighed 21.5 s in the table because it
had once run first. `update_shard_timings.py` now measures that cost in each
report as the first test's excess over the other tests of its own file, takes
the median across the suite's shards, removes it from each shard's first file
and records it as `session_setup_seconds` beside the files. Balance does not
use it — every shard pays it — but a predicted shard duration includes it. On
run 36282670062 it came to 21.5 s per PostgreSQL shard and 1.4 s per browser
shard.

When something crosses a threshold it says `CI SHARD TIMINGS NEED REFRESH` and
names which one, with the command above.

**The balance job reports per runner, with setup apart.** It reads the run's own
reports instead of collecting (every collected test is in exactly one shard's
report, so their union is the collection), and prints, per shard, the
prediction, the test time its JUnit accounts for and the ratio of the two, the
wall time of its pytest process and of building its browser world; per runner,
the job, its test step and everything else, and the CPU the runner drew. The
same numbers go to the run's summary page and to a `shard-health` artifact. A
high actual/predicted ratio on every shard of one runner is that runner, not
the table. It cannot fail its job: the release gate requires every job of a run
to succeed, so every step of it continues on error. The thresholds are the numbers the
2026-08-30 table crossed — 15% of collected tests unmeasured, 20% of files, 30
days old, or a predicted slowest shard 1.25x the median — so each is set where
it would have fired while the drift was still cheap.

**Two warnings are loud** (JTEST-006, 2026-10-18): a collected file the table
does not hold is *named*, with its test count, and so is a shard whose actual
test time is more than twice the table's weight of the files it ran plus its
setup, with the three files that ran furthest past their own weight. The
2026-10-11 table had lost 66 files and 30 % of the suite's time while the
report said "current" — only the unmeasured *share* was checked — and the
slowest file, `qa_audit/test_qa_authz_oracles.py`, ran about 450 s against an
estimate of 3 s. Both run with `--strict`, which exits 1 on a loud warning, and
in CI every warning is also a `::warning` annotation on the run.

**It still cannot fail the build.** Every step that runs it carries
`continue-on-error: true`, so a loud warning turns the step red and leaves the
job, and the release gate, green. Without `--strict`, and whenever it cannot
report at all, it exits 0. Age, the unmeasured share and imbalance are printed
and annotated but never fail the step: an imbalance one heavy file causes is
mended by splitting that file, not by refreshing the table, and the warning now
names the file. A performance report is not a correctness proof; the
correctness proofs are below, and they are the ones allowed to be red.

Note which threshold does the work. The predicted slowest/median ratio is close
to 1.00 almost always, *including when the table is badly stale* — the greedy
schedule balances its own model, and a wrong model balances perfectly against
itself. Under the 2026-08-30 table it predicted six equal 242s shards for a
suite whose shards actually ranged over 100s, and on 2026-09-27 the committed
table predicted 368 s and 1.00 for PostgreSQL shards that took 332-570 s (1.14)
in the same run — which is why the actual column is printed beside it now. The unmeasured fraction is what
detects staleness; the ratio detects the different fault of a shard count raised
past what whole-file granularity can balance.

## Why the suite is complete, and how that is proved

Two independent proofs, at different levels:

**`tests/test_ci_sharding.py`** proves it about the function, for shard counts
from 1 to 13 over the repository's real file list: the union of the shards is
the input, no file is in two shards, the result does not depend on the order the
files were given, an unmeasured file still lands in exactly one shard, and an
out-of-range index raises rather than silently running the wrong slice. It also
reads `.github/workflows/ci.yml` through `ci_workflow_selection.topology`: the
runner matrix is exactly `1..R`, every `run_slices.py` step says `--runners R`,
the shard count is a multiple of `R`, every shard index is hosted by exactly one
runner, and each gate counts reports against that same shard count — the places
a matrix of `[1, 2, 3]` against four runners' worth of shards would otherwise
leave a quarter of the suite unrun with every job green.

**The gates prove it about the run.** The PostgreSQL gate requires a JUnit
report and a coverage fragment from every shard, 1..N, and the browser gate a
report from every browser shard, before either can pass.

**`scripts/ci/assert_shard_completeness.py`** proves it end to end. It runs the
same pytest commands the workflow runs — every shard, plus the unsharded suite —
and compares collected node ids as sets: union equals the whole, no overlaps, no
empty shard. It also checks the `--ignore` that separates the behavioural browser
suite from the visual one, so a file collected by neither job is a red build.
It reads the shard counts out of the workflow rather than from a constant, so
what it proves is what CI will actually do.

**And the arguments, since ENG-110.** It used to collect with a hand-copied list
of pytest arguments, so an `--ignore` added to the workflow left it green over a
smaller suite. `ci_workflow_selection.py` now parses each suite's selecting
arguments out of its own `run:` line and fails closed on any flag it does not
know. The proof then compares the jobs' selections with the unfiltered
universe: bare `pytest` for the PostgreSQL suite, `pytest e2e` for the browser
and visual jobs together. A narrowed command is a red build here.
`report_shard_health.py` reads the same definition.

**Skips are allow-listed (ENG-051).** In CI, a skip that `ci_skip_policy.py`
does not name — by test and by stated reason — fails the run. A missing
`E2E_BASE_URL` or `E2E_GATE_*` fails the browser fixtures instead of skipping
them. See docs/adr/0115.

It runs in the quality job, which has no database and finishes in well under a
minute. The proof that the slow jobs are complete should not itself be slow.

## Main is proved by its pull request's run

`ci.yml` runs for pull requests and when it is started by hand or by
`main-proof.yml`. It does **not** run on a push to main.

A pull request merges only when it is up to date with main and green (branch
protection, `strict`), and its run tests GitHub's test merge — the exact tree
the merge commit then has. Main's own push run used to test those identical
bytes again, holding all twenty of the account's concurrent jobs for about
eleven minutes, so every pull request updated in that window queued behind it:
rounds of 21–25 minutes instead of 11.

On a push to main, `main-proof.yml` runs one small job,
`scripts/ci/assert_main_proved.py`. It applies the release gate's own proof (b),
imported from `assert_release_provenance.py` rather than copied: the commit is a
two-parent merge, the latest `ci.yml` run for its pull request (waited for if it
is still finishing) completed green in **every** job, and the tree that run
recorded as tested is byte-for-byte this commit's tree. **Anything short of that
starts the whole of `ci.yml` on main** — a squash or direct push, a red, missing
or other-tree run, an API failure — and that run is then the commit's proof. If
main has already moved on, the newer commit's own proof covers it and nothing is
started for the old one (the release gate would refuse the old one).

The release gate (`release-image.yml`) is unchanged in what it requires: main's
own green run, now including a run started on main, or — while that is absent
or still running, never over a red one — proof (b), which it re-derives itself
rather than trusting the proof job.

Since 2026-10-28 (docs/adr/0193) both proofs also require all six required
gates among the run's jobs, each `success`, and proof (b) requires the
tested-tree record to say `tier full`. A draft's FAST run fails both conditions
independently, so it can never stand in for a FULL run. A record with no tier
line (a run from before the tiers) is refused; main-proof then dispatches FULL
on main, which is safe and costs one run.

## What was measured, when it was split (2026-08-30)

Baseline, run [33321958813](https://github.com/Kauror/juristid/actions/runs/33321958813);
the architecture benchmark, run
[33323182379](https://github.com/Kauror/juristid/actions/runs/33323182379);
the green sharded runs
[33325578608](https://github.com/Kauror/juristid/actions/runs/33325578608),
[33326635051](https://github.com/Kauror/juristid/actions/runs/33326635051) and
[33327716364](https://github.com/Kauror/juristid/actions/runs/33327716364), the
last of them with `main` merged in.

| | Before | After |
| --- | --- | --- |
| Quality | 0:28 | 0:48 |
| PostgreSQL critical | 12:47 | 4:22 (slowest of 5 shards) |
| Browser critical | 14:48, inside a 16:47 job | 3:19 (slowest of 6 shards) |
| Visual | 1:19, at the end of that job | 1:58, its own job, in parallel |
| Container | 0:59 | 1:00 |
| Backup | 1:03 | 0:47 |
| Dependency | 0:21 | 0:17 |
| Gates | — | 0:20 + 0:02 |
| **Total wall clock** | **16:50** | **4:40** |
| Runner minutes | ~33 | ~45 |

Nothing runs less. On the integrated run: 5355 pytest tests all passing, 425
browser tests with the two world-dependent skips the baseline also had, and 36
visual scenarios with none skipped — where the baseline skipped one. Coverage
is 91% either way.

Those totals include the seven pytest tests and two browser tests `main` gained
while this branch was open. Nothing had to be told about them: the partition
reads the collection, `tests/test_my_work_timeline.py` landed in shard 3, and
the completeness proof passed on the merged head unchanged.

Balance, measured rather than predicted: browser shards 3:03–3:16 (1.07), pytest
shards 3:41–4:22 (1.19). A shard's cost is not only its tests — each one also
creates its own test database, and runners differ — so the spread is a little
wider than the partition predicts, and well inside the range where more metadata
precision would buy nothing.

The runner-minute increase is the price of the wall-clock reduction and is
deliberate: nineteen jobs that finish in five minutes cost more machine time
than six that finish in seventeen.

The PostgreSQL suite is 5328 tests with a mean of 0.116s and a slowest single
test of 6.56s — many comparable tests, no pathological ones, so the only lever
is running them in more places at once. The browser suite is the opposite shape
in miniature: 423 tests over 24 files, one of which (`test_kpi_navigation.py`,
109s) is twice the next and therefore sets the floor for how short a browser
shard can be, whatever the shard count.

`pytest -n` was benchmarked and rejected on two grounds, in that order: its work
assignment is not reproducible, and running the whole suite through one
PostgreSQL and four contended cores was measurably slower than the same work on
independent runners. It also turned up an order dependency in the
suite that independent shards do not (see below).

## What was re-measured three weeks later (2026-09-21)

The suite had roughly doubled — 5328 pytest tests to 10032, 423 browser tests
to 1101 — and the split had not been re-measured since the day it was built.
Twelve consecutive green `main` runs (35470566458 through 35591850047) put the
median wall clock at **9:47**, against the 4:40 this document recorded.

Almost none of that was the shards being too few. Breaking the jobs into steps
shows how small the fixed cost of a runner actually is:

| Per shard | PostgreSQL | Browser |
| --- | --- | --- |
| Job setup (containers, checkout, uv, OCR, upload) | 38s | 49s |
| Test execution | ~392s | ~407s |

An extra runner therefore costs about 40–50 runner-seconds and nothing at all on
the critical path. What the runs were losing was **balance**: scoring the live
file set against a run the table had never seen, the 2026-08-30 metadata put the
slowest PostgreSQL shard at **1.42x** the median of its own run and the slowest
browser shard at **1.28x**. The suite was not too big for five runners; it was
being cut with a three-week-old ruler.

| | 2026-08-30 table, 5+6 shards | refreshed, 5+6 | refreshed, 6+7 |
| --- | --- | --- | --- |
| PostgreSQL slowest | 578s | 481s | **409s** |
| PostgreSQL imbalance | 1.42 | 1.03 | 1.01 |
| Browser slowest | 565s | 486s | **424s** |
| Browser imbalance | 1.28 | 1.05 | 1.04 |
| Shard runner-minutes | 86.3 | 86.3 | 88.3 |

Refreshing the table alone is free and recovers most of it — 97s off the
PostgreSQL critical path and 79s off the browser one, for no extra machine at
all. The sixth PostgreSQL runner is worth another 72s and the seventh browser
runner another 62s, which is what a runner has to buy to be worth keeping
forever.

**It stops there**, and deliberately. A seventh
PostgreSQL shard buys 45s and an eighth browser shard 47s — below the bar, for a
permanent machine each. Past that the granularity runs out rather than the
arithmetic: whole files are the unit, `tests/test_business_write_boundary.py`
alone is 225s of the PostgreSQL suite's 1998s, and at nine shards it *is* a
shard. The numbers were simulated out to nine and ten and are in the pull
request that made this change; the answer was six and seven.

### 2026-10-02: the round is what costs, and twenty jobs is the ceiling

The bar above priced a runner in *runner-minutes*, and both of its premises
have moved. The repository is public now, so standard runners cost nothing; and
the owner works in rapid UI rounds where every change pays the whole round two
or three times (a visible change needs a candidate round and a confirming one),
so the round's wall clock is the cost that accumulates. A runner is worth it if
it takes time off the critical path, whatever it adds to the total.

Measured before the change, on PR run 36963869282 and main run 36965662372: a
round was **10.8–11.9 minutes**, the critical path was the slowest PostgreSQL
shard (Test suite 566s) and the slowest browser shard (500s), and the
PostgreSQL shards ran **312s to 525s** in one run on a five-day-old table — a
spread `slowest / median 1.04` did not show, because with most shards slow the
median sits with them. The health report now prints `slowest / fastest` beside
it.

Scored the held-out way (partitioned with a table refreshed from run
36965662372, scored with run 36963869282's JUnit seconds):

| shards | PostgreSQL slowest | Browser slowest |
| --- | --- | --- |
| 6 / 7 (before) | 510s | 521s |
| 10 | 319s | 371s |
| **12** | **279s** | **318s** |
| 14 | 233s | 262s |
| 16 | 202s | 240s |

**Twelve and twelve was tried and measured, and the account's ceiling decided
it.** PR run 36968364017 ran 12+12: every shard took the predicted ~5–6
minutes, and the round took **12.6** — worse. Exactly twenty jobs started in the
first four seconds and the other ten queued, starting up to five minutes late as
slots freed. The account runs **20 concurrent jobs** (GitHub Free; Pro is 40,
Team 60), shared with every other repository's workflows.

So the shard counts are chosen to fit, not to minimise per-shard time. Six fixed
jobs (quality, migration safety, dependency scan, compose, backup rehearsal,
visual) hold six slots for the first ~2–3 minutes; **7 + 7 shards fill the
other fourteen and everything starts at once**. Packing more shards into the
slots the fixed jobs free was modelled (9 + 11: six shards starting 2–3 minutes
late) and came out no faster.

With 7 + 7 the critical path is the slowest browser shard, ~521s held-out plus
~85s of job overhead: a round of roughly ten minutes, against the eleven and a
half before — the refreshed table and the OCR cache, not the count.

**On a 40-job plan, 12 + 12 is the setting** (measured above: ~5.5–6.5 minutes a
shard, so a round of ~7–7.5). It is a one-line change per matrix plus the two
`--shard-count`s and the gate messages; `tests/test_ci_sharding.py` holds the
matrix and the count to each other. *(Superseded on 2026-10-11 by hosting
several shards per runner; see below.)*

The OCR runtime is now installed from `.deb` files cached per runner image. The
step was 18s when Ubuntu's mirror was quick and up to four minutes when it was
not, and one slow shard is the whole round waiting.

### 2026-10-11: four cores per runner, and the CPU each one draws

The bar moved again: the owner asked for a materially shorter round with every
gate kept. Eight green runs before the change (38110923538 among them) took
692–710 s. Their critical path was the slowest browser shard, a ~663 s job of
which ~80 s was setup, followed by 30–40 s of the balance report collecting
both suites. The twenty slots were all in use from the first second, so more
runners were not on offer — but each runner has four vCPUs and a shard used
about one.

**What the model gap was.** The table predicted 402 s for every PostgreSQL
shard; they took 237–382 s. Not the assignment: the same files ran 0.57x to
1.63x their usual time depending on the runner, and the test database's own
setup took 14–35 s. Logging each runner's CPU (the shard records now do)
named it: an AMD EPYC 7763 runs a PostgreSQL shard about 1.7x slower than an
EPYC 9V45, with the 9V74 and the Xeon 8573C in between. The table was also
~10% stale (the sysmon coverage core made everything faster), which costs
nothing in balance and is now refreshed from a median of runs.

**What two shards on one runner cost.** Per file, a median of 1.09x — the
cores really were idle. Three on a runner was measured and rejected: on an
EPYC 7763 its PostgreSQL shards ran 1.5–1.7x the prediction.

| run | layout (PostgreSQL / browser) | wall clock | slowest PG runner | slowest browser runner |
| --- | --- | --- | --- | --- |
| [38110923538](https://github.com/Kauror/juristid/actions/runs/38110923538) (before) | 7 / 7 shards, one per runner | 697 s | 445 s | 666 s |
| [38117799736](https://github.com/Kauror/juristid/actions/runs/38117799736) | 7x2 / 7x2, old table | 454 s | 360 s | 410 s |
| [38118423762](https://github.com/Kauror/juristid/actions/runs/38118423762) | 5x3 / 9x2 | 367 s | 339 s (EPYC 7763) | 341 s |
| [38118903791](https://github.com/Kauror/juristid/actions/runs/38118903791) | 6x2 / 8x2 | 371 s | 348 s | 346 s |
| [38119307615](https://github.com/Kauror/juristid/actions/runs/38119307615) | 6x2 / 8x2 on disk (A/B) | 411 s | 388 s | 349 s |
| [38120321976](https://github.com/Kauror/juristid/actions/runs/38120321976) | **7x2 / 9x2, chained fixed jobs** | **352 s** | 297 s | 328 s |

The A/B row is the same commit with the PostgreSQL data directory on disk:
the in-memory one is about 10% faster on matching hardware and does not
narrow the spread between runners.

The last row is the layout this round settled on: fourteen PostgreSQL shards
on seven runners and eighteen browser shards on nine, two to a runner, with
the container smoke test after the dependency scan and the recovery rehearsal
after the migration safety job, which frees the two slots. On it:

| | before (38110923538) | after (38120321976) |
| --- | --- | --- |
| Wall clock | 697 s | 352 s (−49%); required checks done at 335 s |
| Slowest PostgreSQL shard (tests / job) | 382 s / 445 s | 239 s / 297 s (runner) |
| PostgreSQL slowest/median, slowest/fastest | 1.25, 1.61 | 1.30, 1.67 — the CPU drawn, see above |
| Slowest browser shard (tests / job) | 570 s / 666 s | 246 s / 328 s (runner) |
| Browser slowest/median, slowest/fastest | 1.06, 1.12 | 1.05, 1.39 |
| Shards (runners) | 7 (7) + 7 (7) | 14 (7) + 18 (9) |
| Setup outside the tests, per runner | PG ~66 s + ~22 s test DB; browser ~79 s | PG 32–44 s + ~20 s; browser 57–78 s (worlds built concurrently, 21–39 s) |
| Tests | 14552 + 1364 browser (22 deliberate skips) + 87 visual | 14612 + 1365 (the same 22 skips) + 87 |
| Coverage | 42370 statements, 2609 missed, 94% | 42370, 2609, 94% |

Two latent defects surfaced on the way, both fixed rather than worked
around: five lock-wait helpers and `rebuild_is_running()` read server-wide
views that two databases on one server share (scoped to `current_database()`,
and the helpers re-read the statistics snapshot), and a new partition put
`e2e/test_long_filename_wrapping.py` before `e2e/test_work_surfaces_review.py`
for the first time, which showed Minu asjad scrolling 225px sideways on a phone
when a Teema's title is one long compound (the quiet rail now wraps it, and the
file that files such a title proves it).

What is left on the critical path is a runner's fixed cost — ~70 s for a
browser runner, ~35 s for a PostgreSQL one — and which CPU it draws. A 40-job
plan would still be the larger lever.

### 2026-10-25: a week-old table, and the PostgreSQL shards set the round

Re-measured on the 52 green pull-request runs of 2026-10-20 to 2026-10-25,
from each run's job and step timings in the GitHub API; the summary numbers
are in the pull request's description, and the table itself is reproducible
with `scripts/ci/update_shard_timings.py --latest 5` and
`scripts/ci/report_shard_health.py`. Median wall clock **597 s**, and in 44 of
them the job that finished last was a PostgreSQL runner — not a browser one,
as on 2026-10-11. The PostgreSQL runners ran 240–410 s at their fastest and
410–840 s at their slowest in the same run; browser runners stayed at
335 s median (361 s p90), ~70 s of it outside the test step (39 s of
that building the worlds).
Setup was not where the time went: checkout, `uv sync`, the OCR runtime and
the Playwright binary are 1–4 s each on cache hits, `Initialize containers`
15 s.

The table was the cause. Measured 2026-10-18, it held none of the 142 files
the MCP and security rounds had added since (`tests/test_evidence_pilot.py`,
`tests/test_mcp_*` and more; the health report named them in every run), and
it weighed `tests/qa_audit/test_qa_authz_oracles.py` at 403 s where it now
takes ~206 s, so one shard was cut around a file half that size. Run
40180382406 shows it: predicted 425 s for shard 1 and 298 s for the rest;
actual 232–492 s (slowest/fastest 2.12).

Held-out, as on 2026-10-02 — partition with a table built from five other
green runs, score with one run's own JUnit seconds, over ten runs — a
refreshed table brings the slowest PostgreSQL shard from a median **473 s
to 385 s** (−88 s) and the slowest browser shard from 284 s to 276 s.
Nothing else changed: same 7+9 runners, same 14+18 shards, same
completeness proofs. Combining nine runs instead of five scored 377 s, within
the spread of one runner's CPU, so the documented `--latest 5` stays.

Measured on the refreshed table, the balance is fixed and the round gains
less than the simulation said, because the CPU a runner draws now decides
most of what is left. Shard slowest/median went from 1.51 (run 40180382406)
to 1.13 and 1.21 (runs 40189653921 and 40191011828). The slowest PostgreSQL
runner job took 473 s and 512 s, against a median of 530 s (451–608 s) over
the nine runs of 2026-10-22..25 that, like these two, drew four or more of
their seven PostgreSQL runners on an AMD EPYC 7763 — about 40 s, not 88 s.
On those two runs the 7763 runners took 411–512 s and the others 291–413 s,
for the same predicted 322 s a shard.

### The slow files, and why none of them were touched

The ten slowest files in each suite were read. Nothing in them is a defect:

* `tests/test_read_side_existence_oracles.py` — 139s over 11 tests, 12.6s each.
  It builds a full page of work with real evidence and real extraction, twice,
  and compares the two worlds. The cost *is* the proof; a shared fixture would
  weaken exactly what it asserts.
* `tests/test_multiple_senders_migration.py` — 58s over 6 tests, running real
  migrations forwards and backwards.
* `tests/test_reference_data_isolation.py` — 46s, of which 28s is one test that
  starts a second pytest process on purpose.
* `tests/test_business_write_boundary.py` — 225s, but over 720 tests: 0.31s
  each, and it is the whole write surface enumerated.

These are expensive because they do expensive things, and a refreshed table
schedules around them instead of being surprised by them. No test was made
cheaper by being made weaker.

### The setup costs that were investigated and left alone

* **Tesseract.** Every PostgreSQL shard installs it, and it is genuinely absent
  from the runner image — 9MB of packages, **11s median** (16s at p90). Exactly
  four tests need the real runtime, all in `tests/test_extraction_parsers.py`,
  and they cost 0.44s between them. Tempting, and rejected: `requires_ocr` is a
  `skipif`, so a shard without the binary *silently skips* those four, and the
  shard a file lands in is not stable across commits — there is no shard to
  install it on. A dedicated job would mean carving one file out of a partition
  whose entire virtue is having no special cases, to save 11s of a 409s shard.
  The runtime's presence is already asserted separately, in
  `PostgreSQL migration and runtime safety`, and all four tests are confirmed to
  run rather than skip.
* **`uv sync --frozen`** — 3s, on a confirmed `setup-uv` cache hit. Nothing to
  win, and a second overlapping cache would only add a way to be stale.
* **Playwright** — `~/.cache/ms-playwright` is hitting; `Install the browser`
  takes **1s**. Left alone.
* **Coverage** stays on every shard, measured with `COVERAGE_CORE=sysmon` since
  2026-10-10. It now reports the same data as the default core (see above).

## The order dependency it turned up, since closed

Under `pytest -n --dist loadfile`, which reorders whole files across workers,
between 28 and 82 tests failed with `StageVocabulary.DoesNotExist` or
`PolicyArea.DoesNotExist`. Those rows are created by data migrations, and a
`django_db(transaction=True)` test flushes every table at teardown; nothing
re-runs a historical migration, so they were gone for whatever ran next.

Two things about that are worth keeping, because both were misread at the time.

**The serial suite was not green by luck.** pytest-django sorts every collected
item so that non-transactional database tests run before transactional ones, and
the sort is stable — so within one process the flush can only ever land after
the last test that needed the seeded rows. The sharding here inherits that
directly: whole files, in collection order, means every shard is an
order-preserving subsequence of the green serial run, and each shard is its own
process that does its own sort.

**What the sort does not survive is a second process.** `--reuse-db` after any
transactional run picks up the flushed database, and a run abandoned by `-x`
mid-file leaves one behind. That is a real failure people hit locally, and it
reproduces in two ordinary invocations with no xdist and no plugin:

```
pytest --create-db --reuse-db tests/test_documents.py::test_concurrent_writers_get_distinct_version_numbers
pytest --reuse-db tests/test_stage_vocabulary_seed.py::test_ten_canonical_stages_are_seeded
```

The contract now is that a test whose database teardown is a flush leaves the
migrated baseline restored for whatever runs next — this process, the next
process, or a worker taking files in any order at all. Three pieces hold it up:

* every transactional test declares `serialized_rollback=True`, which is what
  makes Django snapshot the migrated database once at session set-up, and what
  stops its flush re-creating content types under fresh primary keys;
* a teardown hook in `tests/conftest.py` puts that snapshot back after the
  test's database teardown, through `tests/reference_baseline.py`;
* `tests/test_reference_data_isolation.py` fails if a new transactional test
  arrives without the marker, and fails if the restore stops happening.

The restore is Django's own serialized rollback, moved to the other boundary:
`serialized_rollback=True` alone restores the baseline *before* a transactional
test, which is not the same question. Nothing in it restates a vocabulary — the
snapshot is whatever `migrate` produced — so a reference-data migration added
later is covered without touching any of it.
