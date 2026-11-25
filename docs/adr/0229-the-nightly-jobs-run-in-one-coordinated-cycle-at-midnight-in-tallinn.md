# 0229 — The nightly jobs run in one coordinated cycle at midnight in Tallinn

Date: 2026-11-25 · Status: proposed (owner's decision of 2026-11-25, definitive: all routine synchronisation, external-source monitoring and AI analysis run nightly, in one coordinated cycle; prepared and tested only — no production schedule changed, nothing merged or deployed)

Numbering: 0221–0223 are the UI/UX programme's (PR #721), 0222–0226 the Riigikogu
programme's (PR #723); this record took 0229 so that the proposed settlement — UI
keeps 0221–0223, Riigikogu moves to 0224–0228 — leaves it in place.

## Context

On 25.11.2026 the host KOCORP (UTC clock, no Europe/Tallinn zone) ran Juristid's
scheduled work from three cron files under `/boot/config/plugins/dynamix/`:

| Job | Cron (UTC) | Source on the host | Lock / precondition |
|---|---|---|---|
| Daily backup set + RPO check | `45 20 * * *` (≈ 3 min) | `juristid.cron` → `deploy/unraid-main/cron/juristid-backup-nightly.sh` | takes the host lock; waits ≤ 20 min for it |
| Lovable synchronisation (nightly) | `15 21,22 * * *`; `lovable_sync due` runs only the 00:15 Tallinn firing (≈ 5½ min in APPLY) | `juristid-lovable-sync.cron` → `scripts/deploy/juristid-lovable-sync.sh` | runner flock, host lock, web healthy, pause file, root-only credentials, run key per Tallinn date, APPLY → fresh backup set verified to level 2 or nothing written, breakers (ADR 0213) |
| Lovable / OneNote on-demand processor | `2-59/5 * * * *` | same file, `--on-demand` | the same locks; acts only on a waiting request |
| Register watch (ADR 0217) | `30 3 * * *` (≈ 12 min) | `juristid-register-watch.cron` → `docker exec … watch_registers` | advisory lock 2170001 |
| «Uued eelnõud» sweep (ADR 0218) | `20 4 * * *` (≈ 1 min) | same file → `sweep_registers` | advisory lock 2180001; after the watch |
| Government agenda (ADR 0219) | `*/30 10-23 * * 3`, `*/30 0-9 * * 4` (winter hours) | same file → `watch_government_agenda` | no weekday guard in the command; ≤ 3 model attempts per agenda |
| Monitor fast / daily / weekly | `*/15`, `5 4 * * *`, `0 5 * * 1` | `juristid.cron` → `juristid-monitor.sh` | reads only |

There is no separate «AI analysis» or «retry» job: each command judges what it found
and retries what was left unjudged on its next run, under its own limits.

The owner decided (25.11.2026, definitive): all routine synchronisation, external-source
monitoring and AI analysis run nightly, in one coordinated cycle starting at 00:00
Europe/Tallinn; dependent steps in order, independent ones side by side where safe; one
failure never needlessly stops another; every safeguard kept; daytime schedules removed
only by an authorised deployment. A Wednesday agenda is normally found by the cycle going
into Thursday; one published later may wait until Friday — accepted, with no daytime
catch-up; an authorised manual run remains possible.

## Decision

1. **One cycle a night at 00:00 Europe/Tallinn**, `scripts/deploy/juristid-nightly-cycle.sh`,
   from one cron line, `0 21,22 * * *`, in its own file
   `deploy/unraid-main/cron/juristid-nightly.cron`. The host has no Tallinn zone, so cron
   fires at both possible midnights and the script decides which one it is (`lib.sh`
   `tallinn_clock`: UTC+3 from 01:00 UTC on the last Sunday of March to 01:00 UTC on the
   last Sunday of October, UTC+2 otherwise — held against tzdata for 2026–2030 by a test).
   The other firing exits at once and leaves nothing. In the application every schedule
   reference uses the same rule through zoneinfo (`app.core.nightly_schedule`).
   `--plan [--at …]` prints the decision, the locks and the steps and writes nothing;
   `--manual` is the authorised recovery run.
2. **The steps are the existing jobs, unchanged**: `juristid-lovable-sync.sh` exactly as
   its cron line ran it (scheduled trigger, its own `due`, run key, pause file,
   credentials, APPLY backup with level-2 proof, breakers, observation), and the same
   `docker exec --env-file config/register-watch.env juristid-main-web python manage.py …`
   commands with the same logs. No limit, breaker or model setting changes; the agenda's
   `MAX_ATTEMPTS` = 3 and the batches and token caps stay; the run ledgers stay
   (`LovableSyncRun`, `WatchRun`, `SweepRun`, `GovernmentAgenda.ai_attempts`).
3. **Order.** Lovable first and alone: a Teema it creates is followed by the watch the same
   night, the sweep does not offer its draft as new, the agenda's model sees it. Then two
   lanes side by side: *registers* — `watch_registers` → `watch_riigikogu` (§10) →
   `sweep_registers`, in order (they share the Riigikogu API's rate limit, and the sweep
   leaves out what the watch follows) — and *agenda* — `watch_government_agenda`. One hard
   need: the sweep needs the watch to have finished (SUCCEEDED, SUCCEEDED_NO_NEW or
   PARTIAL); a deliberately skipped watch skips it deliberately, any other outcome skips
   it as SKIPPED_UNEXPECTED with the reason. Everything else is soft order: a failed
   Lovable night still lets the watch read the Teemad as they stand. The model's work
   follows discovery inside every command. Each step has a finish-by from the cycle's
   start (`nightly_schedule.FINISH_BY`: Lovable 1 h 30, agenda 2 h 30, watch 3 h, sweep
   4 h), adding up the steps before it and the bounded lock waits; a normal night takes
   ≈ 20 minutes.
4. **An exit code is not an outcome.** The script has no `set -e` across steps (every step
   runs as `run_step … || true`). After each step it asks `manage.py nightly_outcome`
   (read-only, `app.core.nightly_outcome`), which classifies from the step's own record:

   | Outcome | Lovable (`LovableSyncRun`, SCHEDULED, since the cycle began) | Watch / sweep (`WatchRun` / `SweepRun`) | Agenda (`--outcome-json` counts) |
   |---|---|---|---|
   | SUCCEEDED | SUCCEEDED with Teemad updated, created or retired | finished, no errors, new findings / candidates | stored or judged something |
   | SUCCEEDED_NO_NEW | SUCCEEDED, nothing written | finished, no errors, nothing new | nothing stored or judged |
   | PARTIAL | REVIEW_ONLY (breaker, no backup), failed / unverified Teemad, search left to refresh | errors or model failures, some source read | errors or model failures |
   | SKIPPED_DELIBERATE | no run: control OFF, cut over, already ran tonight; host pause file | reading / the sweep off | reading off |
   | SKIPPED_UNEXPECTED | no run and no reason (a lock, not due) | no run record; another run held the lock | no outcome line with exit 0 |
   | FAILED | FAILED, or a non-zero exit | `katkes`, read no source, non-zero exit | non-zero exit |
   | INTERRUPTED | still RUNNING after the script ended | never finished | — |

   The agenda keeps no run ledger, so `watch_government_agenda --outcome-json` ends with
   one machine line of counts; no human-readable log line is parsed. Riigikogu, until its
   `StageRun` ledger reaches main, is classified by exit code and says so. The cycle's
   own pre-conditions record SKIPPED_DELIBERATE (`PAUSED-<step>`, Riigikogu not
   installed, the Lovable pause file) or SKIPPED_UNEXPECTED (a held release lock, the
   Lovable runner busy for 20 minutes, a missing hard need). A step whose lane died
   before it reported is INTERRUPTED.
5. **Locks: real exclusion with releases, in one order.**
   - The nightly cycle's lock `/tmp/juristid-nightly-cycle.lock` (`lib.sh`
     `take_cycle_lock`, `flock -n` on fd 7 — atomic, no check-then-act) is held by the
     cycle from its start to its end, and inherited by every step it starts, so an
     overrunning cycle still holds it. A release (the kit's d1/d2) and a restore
     (`juristid-restore.sh`, in this change) take the same lock **before** the host lock
     and hold it to their end: whichever takes it first wins, the other refuses — the
     cycle writes `D.refused`, the release stops by name. The read-only preflight asks
     (`cycle_lock_is_free`) and fails while it is held.
   - Order, everywhere: cycle lock → Lovable runner flock (`/tmp/juristid-lovable-sync.lock`)
     → host lock directory (`/tmp/juristid-host-lock`) → the backup's own lock → PostgreSQL
     advisory locks. Every wait is bounded or non-blocking, and nobody takes an earlier lock
     while holding a later one, so there is no deadlock. The nightly backup (20:45) and the
     on-demand processor never take the cycle lock.
   - Before the Lovable step the cycle waits (≤ 20 min) for the runner's flock, so an
     administrator's on-demand run still going finishes first; while the night's sync
     runs, an on-demand firing finds the flock held and leaves the request PENDING in the
     database, served by the next firing (the request's own two-hour expiry is unchanged).
     The two never run side by side, and no request is dropped by the cycle.
   - Before every step the cycle still waits (≤ 20 min) for the host lock to be free, for
     a release kit that does not take the cycle lock yet; a step that finds it held is
     SKIPPED_UNEXPECTED by the holder's name, and the lock is never touched.
6. **State and health.** Per Tallinn night D, in `nightly/state/`: `D.started` (once the
   lock is held, before any step), `D.outcomes` (a line per step as it ends), `D.completed`
   (the summary, last), `D.refused`, and `D.recovered` (an authorised `--manual` run's
   summary for that night). A night with `D.started` or `D.completed` is never started
   again by cron — a cycle that died halfway is not re-run blindly over partial writes; a
   person recovers it with `--manual`. The existing daily monitor (`juristid-monitor.sh
   --tier daily`, 04:05 UTC, `check_nightly`, `MONITOR_NIGHTLY_DIR`) reports through the
   existing notification path a night that never started, was refused, was interrupted
   (started, never completed, its process gone) or overdue (still running past 4 h), or
   completed with a PARTIAL, FAILED, INTERRUPTED or SKIPPED_UNEXPECTED step; a paused
   cycle and a recovered night are ok. The application's health checks keep their run
   records and 30-hour staleness, and their first-run grace now follows the cycle
   (`watch.first_run_due`, `candidates._first_run_due` = the step's finish-by in the first
   cycle after switching on). The agenda's «missing this week» (`agenda.missing_this_week`)
   turns on only after the last cycle that reads the week — Friday 00:00 Tallinn plus the
   agenda's finish-by — so an agenda first published after the Thursday cycle, which the
   design accepts may wait until Friday, is not called missing on Thursday.
7. **What the authorised deployment changes on the host** — by
   `deploy/unraid-main/cron/juristid-nightly-transition.sh --cron-dir
   /boot/config/plugins/dynamix --nightly-cron <repo>/deploy/unraid-main/cron/juristid-nightly.cron
   --apply`, then `update_cron`:
   - add `juristid-nightly.cron`:
     `0 21,22 * * * bash /mnt/user/appdata/juristid-main/repo/scripts/deploy/juristid-nightly-cycle.sh &> /dev/null`;
   - in `juristid-lovable-sync.cron` remove
     `15 21,22 * * * bash /mnt/user/appdata/juristid-main/repo/scripts/deploy/juristid-lovable-sync.sh &> /dev/null`
     (the on-demand `2-59/5` line stays);
   - remove `juristid-register-watch.cron` whole (`30 3 * * *` watch, `20 4 * * *` sweep,
     `*/30 10-23 * * 3` and `*/30 0-9 * * 4` agenda);
   - `juristid.cron` (backup, monitors) unchanged; add `MONITOR_NIGHTLY_DIR=…/nightly` to
     `config/monitor.env`.
   The example `juristid.cron.example` only points to the standalone file, and a test
   proves the union of every cron source in `deploy/` holds exactly one cycle line, so
   following the instructions cannot install the cycle twice. The checkout must hold the
   script before the cron file goes in.
8. **Rollback**: `juristid-nightly-transition.sh … --rollback` puts back the byte-for-byte
   copies (`*.cron.pre-0229`) and removes `juristid-nightly.cron`; then `update_cron`. A
   test applies and rolls back the transition on a copy of the host's cron files of
   25.11.2026 and checks the result byte for byte. No data step is needed.
9. **Neighbours and releases.** The cycle starts 15 minutes after the 20:45 backup in
   summer, 75 in winter, and normally ends ≈ 21:20 / 22:20 UTC, before the 23:00 dumps and
   the 23:30 USB backup, the 00:30 backups, the Monday 01:00 Appdata Backup, the 01:15
   media tier, the 01:40 mover and DashKoda's 02:00–07:00 feeds; the parity check on the
   1st at 21:30 overlaps it as it overlapped the sync. The release kits' refusal window
   widens to **20:30–23:00 UTC**, and — the real guarantee, overruns included — the kit
   takes the cycle lock (kit change below). Changing `lib.sh` changes a blob that a
   future kit pins (`juristid-lovable-sync.sh` changes in its comments only, in the r93 integration).
10. **Riigikogu** plugs into the registers lane between the watch and the sweep once PR
    #723 is on main and the owner switches it on: the cron line gains `--with-riigikogu`.
    Until then the step is SKIPPED_DELIBERATE (NOT_INSTALLED). A failing Riigikogu step
    never skips the sweep.

**The kit-side change** (the generator lives outside the repository and is not edited
here; for the next authorised kit): in the kit library's `acquire_lock`, before it takes
the host lock —

```bash
# docs/adr/0229 §5: never beside the nightly cycle, overruns included.
. "$REPO/scripts/deploy/lib.sh"        # take_cycle_lock, cycle_lock_owner
cycle_rc=0; take_cycle_lock "release-$1" || cycle_rc=$?
[ "$cycle_rc" = 0 ] || stop "the nightly cycle holds $JURISTID_CYCLE_LOCK ($(cycle_lock_owner)); wait for nightly/last-cycle.txt to show it ended"
```

and the header's window «outside 20:30–22:40 UTC» becomes «outside 20:30–23:00 UTC». The
lock is held on fd 7 until the kit script exits (every kit script that changes the stack
takes it).

## Consequences

- One cron line replaces five; the night's work is in one log and one state file per
  night with a classified outcome per step; the daily monitor reports a bad night once.
- The register watch and the sweep run ≈ 5½ hours earlier and see the Teemad Lovable
  created that night; the agenda is read nightly instead of 48 times from Wednesday noon
  to Thursday noon, and «missing this week» appears on Friday morning, not Thursday noon.
- A release and the cycle exclude each other atomically; the register commands wait for
  a release, which they never did.
- `lib.sh`, `juristid-restore.sh`, `juristid-deploy-preflight.sh`,
  `juristid-monitor.sh` and `juristid-lovable-sync.sh` (comments only, r93 integration)
  change, so a kit that pins their blobs must be regenerated.
- The deployment is a host change the owner approves, with its rollback in §8.
