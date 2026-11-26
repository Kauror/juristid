# 0231 — Every nightly call and step has a deadline, and the cycle lock outlives the work

Date: 2026-11-26 · Status: proposed (security round 26.11.2026, SEC-010; prepared and tested only — no production schedule, host script or configuration changed, nothing merged or deployed)

Numbering: 0230–0233 are left for the other packages of the same security round.

## Context

The Codex Security scan of e49a0683 (finding SEC-010, MEDIUM; operationally high)
found that the nightly register readers had socket timeouts but no whole-call
deadline, and that the coordinated nightly cycle (ADR 0229) had no execution limit
for its in-container steps.

* `app/register_watch/sources/http.py` bounded a call as a whole only when a
  caller passed ``deadline=`` (HR-04: a person's «Lisa märkena», a Riigikogu stage
  under `client.time_limit`). The register watch, the «Uued eelnõud» sweep, the ADR
  search forms (`post_form` took no deadline at all), the PDR reader and the
  Government agenda passed none. A 25-second socket timeout bounds one ``recv``:
  a server that drips its headers or body a byte at a time, a TLS handshake that
  trickles, or a name lookup that hangs (``getaddrinfo`` was never bounded) could
  hold the call for as long as it liked. The host spacing and the Riigikogu
  client's back-offs slept without regard to any run-level limit outside a stage.
* `scripts/deploy/juristid-nightly-cycle.sh` ran each step as `docker exec … python
  manage.py …` with no limit. Had it been given one on the host (``timeout docker
  exec``), it would not have been safe: killing the host's ``docker exec`` client
  does not stop the process it started inside the container. The cycle would record
  a step as ended and release its flock (ADR 0229 §5) while the step went on writing
  — exactly the overlap with a release or a restore the lock exists to prevent.
* The Lovable synchronisation's one-off containers (`juristid-lovable-sync.sh`) ran
  under ``timeout 1200 docker run`` with ``--entrypoint python``: Python was PID 1 in
  the container, and PID 1 ignores a SIGTERM it installed no handler for, so the
  signal Docker forwards on timeout did nothing and the 20-minute timeout never
  ended a run.

## Decision

### §1 One whole-call deadline for every external register call

`sources/http.py` stays the one door (ADR 0217 §5). **Every** call has a
whole-call deadline, the earliest of:

1. the caller's ``deadline=`` (a `time.monotonic` instant), as before;
2. the **run's deadline**: `http.run_deadline(seconds | until=…)` (a context
   manager; nested limits only narrow), or the nightly step's
   ``JURISTID_STEP_DEADLINE`` (Unix seconds, set by the supervisor of §3 — an
   unreadable value counts as spent, never as unbounded);
3. `CALL_SECONDS` (100 s) from the call's start, so no call is unbounded even
   outside any run (below gunicorn's 120-second worker timeout).

Under it: nothing is opened once it passed; the host spacing (the rate-limit wait)
is not slept past it; the **name lookup** runs in a helper thread waited for no
longer than the time left (the thread is abandoned, never joined, if the deadline
comes first); each address is connected with the time left; and from the TCP
connection on the existing watchdog (HR-04) shuts the socket at the deadline, so a
dripping handshake, headers or body is cut there. The response is read in pieces
with the clock checked between them; a cut answer is never taken for the whole.

A call stopped by a deadline raises **`DeadlineExceeded`**, a subclass of
`FetchError` (``status`` ``None``), so every caller that records a register failure
records it as one; `FetchError.status` and ``retry_after`` (r95) are unchanged.
`post_form`, `get_text`, `get_json` and `get_bytes` all take ``deadline=``. Size
caps, the host allowlist, https only and no redirects are unchanged.

API for a reader (the Government agenda's discovery included): call the helpers
of `sources/http.py` and nothing else — they inherit the run's deadline without
any parameter. Between units of work, `http.check_deadline(what)` raises
`DeadlineExceeded` once the run's time is spent, `http.out_of_time()` asks, and
`http.remaining()` gives the seconds left (``None`` without a run deadline).

### §2 Retries, back-offs and model requests stop at the same deadline

The Riigikogu client's `time_left` is the nearer of its stage limit and
`http.remaining()`; `OutOfTime` is now also a `DeadlineExceeded`. No request starts,
no back-off or ledger wait sleeps, and no ``Retry-After`` is honoured past it; the
retry counts (5/15/45 s for 429, two more for a dropped connection) and the shared
ledger (1 a second, 11 a path a minute) are unchanged. The 90-minute Riigikogu cycle
limit (`cycle.CYCLE_TIME_LIMIT`) is unchanged and sits inside the step's soft
deadline.

`ai_call.call_items` starts no model request once the run's time is spent
(`Failure("request", "ajapiir täis")`), and otherwise gives the SDK no more than the
time left as its timeout and no retries a second attempt could not finish in. The
SDK's timeout bounds each phase of a request, not the whole of it: a model answer
that trickles past the soft deadline is ended by the hard budget of §3.

The register watch and the sweep stop starting new Teemad / sources once the run's
time is spent and record **one** line, ``{"viga": "… ajapiir täis; ülejäänu
loetakse järgmisel ööl", "ajapiir": true}``; what was read is kept and the run is
closed normally (`finished_at`), never as stopped (``katkes``).

### §3 Each in-container step runs under a hard execution budget, enforced inside the container

`app/core/nightly_bounded.py` (standard library only; no Django, no database):

    python -m app.core.nightly_bounded run --step S --seconds B [--grace G] [--kill-after K] -- <manage.py command>
    python -m app.core.nightly_bounded status --step S      # 0 gone, 3 alive
    python -m app.core.nightly_bounded stop   --step S      # 0 gone, 70 stuck

`run` starts the step as `python -m app.core.nightly_bounded child -- <command>` in
a **new session** (its own process group) with ``JURISTID_STEP_DEADLINE`` = now +
B − G, and writes `/tmp/juristid-nightly/S.json` (mode 0700 directory): the group,
its leader's start time (`/proc/<pid>/stat` field 22) and the boot id, so a later
`status`/`stop` never mistakes a recycled pid or another boot's process for it.
In the child, SIGTERM raises `StepTerminated` (a `BaseException`): `watch.run`'s and
`candidates.sweep`'s ``except BaseException`` close their record as stopped and an
open transaction rolls back. At B the supervisor sends SIGTERM to every live
process of the group, SIGKILL K seconds later, and then **verifies** that none is
left (up to 60 s) before it answers. A step that ended by itself is checked the
same way for anything it left running.

Exit codes: the step's own; **124 TIMEOUT** (stopped at its budget, verifiably
gone); **75 BUSY** (the previous run of S is still alive: not started twice);
**70 STUCK** (not verified gone: the pid file stays). A machine line
``NIGHTLY-BOUNDED {…}`` goes to the step's log.

Budgets (`app.core.nightly_schedule.EXECUTION_BUDGET`, = the script's `step_budget`;
grace 5 min, `TERMINATION` 90 s), each with its termination inside the step's
allowance of ADR 0229 (`STEP_ALLOWANCE`), so a step stopped at its budget still
finishes by its finish-by:

| Step | Allowance (ADR 0229) | Budget | Soft deadline | Why |
|---|---|---|---|---|
| register-watch | 1 h 10 | 68 min | 63 min | ≈ 12 min observed; room for a slow night |
| riigikogu | 1 h 40 | 98 min | 93 min | its own 90-min cycle limit inside, plus start-up and the last answer |
| register-sweep | 40 min | 38 min | 33 min | ≈ 1 min observed |
| government-agenda | 40 min | 38 min | 33 min | a few pages and ≤ 3 model attempts per agenda |

The Lovable step keeps its own bounds (its runner flock, the 20-minute timeout per
one-off container, the breakers and backups, unchanged); §5 makes its timeout real.

### §4 The cycle releases its lock only after the work is verifiably gone

`juristid-nightly-cycle.sh` runs every in-container step through `bounded`
(`timeout --kill-after=30 (B + 180) docker exec … nightly_bounded run …`; the host's
own timeout only ends a hung docker client). After every step, whatever its exit
code, `ensure_gone S` asks the container (`status`), stops what is left (`stop`), and
returns only once the step is verified gone — or once the container is not running
(a stopped container runs nothing). While that cannot be verified, the night's
`state/D.stuck` says so, the cycle keeps its lock (fd 7) and checks again every
minute; a release or a restore is refused meanwhile, as the lock intends. After both
lanes the cycle checks every step once more (a lane that died before its own check),
and a cycle stopped from outside (TERM, INT, HUP) kills its lanes, stops every step
and verifies it gone before it exits (143) and the lock goes.

The verdict (`nightly_outcome`, ADR 0229 §4) reads the supervisor's code first: 124
is **FAILED** "TIMEOUT: stopped at its execution budget; the rest is read next
night", 75 **SKIPPED_UNEXPECTED** "BUSY", 70 **INTERRUPTED** "STUCK" — whatever the
step's record says, its counts kept. A run record with the ``ajapiir`` line is
**PARTIAL** ("stopped at its time limit"), or FAILED if it read nothing. A
deliberately skipped step stays deliberate. The register sweep's hard need of the
watch (ADR 0229 §3) is unchanged: after a watch that timed out the sweep waits a
night (SKIPPED_UNEXPECTED), never runs on a half-read list.

**Where it shows.** `nightly_outcome --record` keeps each step's last verdict
(outcome, reason, exit code, seconds, night) in the database cache (`core_cache`, 14
days; migration-free); Haldus → Registrite jälgimine shows it as «Öine tsükkel», a
timed-out, busy or stuck step marked «· ajapiir». The daily monitor
(`juristid-monitor.sh`) names TIMEOUT / BUSY / STUCK beside a step's outcome and
reports an uncleared `D.stuck` first. The host's state files stay the cycle's own
ledger.

### §5 The Lovable one-off containers end at their timeout

Every `timeout … docker run` in `juristid-lovable-sync.sh` gets ``--init`` (a minimal
init as PID 1 forwards Docker's SIGTERM to Python, which then ends) and
``--kill-after=60`` on the client; after a timeout (124/137) `leftover_gone` kills a
container that is still there and waits for it, the runner's lock still held. The
sync algorithm, its breakers (100 ordinary, 150 hand-over), the backup-before-APPLY
and the read-back are unchanged. A run cut by its timeout is recorded RUNNING and
read as INTERRUPTED, as before; its advisory lock ends with its process.

## Consequences

* A night can no longer be held by one slow register or a hung lookup; a step that
  overruns is stopped and reported as such in the cycle summary, Haldus and the
  morning monitor, never as a success.
* No step's work can continue inside the container after the cycle's lock is gone,
  except in the one case the host cannot see into: the cycle script itself SIGKILLed
  (the kernel then releases the flock at once). The next cycle's supervisor refuses
  to start the same step twice (BUSY) and its `ensure_gone` stops the leftover; a
  release recreates the container, which ends it.
* `CALL_SECONDS` also bounds a call made inside a person's request that passed no
  deadline.
* The schedule is unchanged: Lovable 00:00, the lanes not before 01:00, register
  watch → Riigikogu → sweep beside the agenda, no daytime routine job; on-demand and
  `--manual` recovery as before.

## Deployment and rollback

The image carries the code (`app/core/nightly_bounded.py`, the door, the verdicts,
the Haldus section). The host scripts are read from `$APPDATA/repo` (ADR 0229), so
they change when the release updates that checkout:

1. Release the image as usual; `git -C $APPDATA/repo` to the released commit.
2. `scripts/deploy/juristid-nightly-cycle.sh --plan` shows every in-container step
   as `python -m app.core.nightly_bounded run --step … --seconds …`.
3. One check before the first night: `docker exec juristid-main-web python -m
   app.core.nightly_bounded status --step register-watch` answers `gone` (exit 0).
4. The first night: `state/D.outcomes` and Haldus → Registrite jälgimine → «Öine
   tsükkel»; no `D.stuck`.

Rollback: the previous image and `$APPDATA/repo` at the previous commit together
(the old script calls `manage.py` directly; the new image still accepts that). An
old script with the new image runs the steps without budgets, as before. Nothing is
migrated; the cache entries expire by themselves.
