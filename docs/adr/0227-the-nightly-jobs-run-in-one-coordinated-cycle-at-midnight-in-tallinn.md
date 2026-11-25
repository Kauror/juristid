# 0227 — The nightly jobs run in one coordinated cycle at midnight in Tallinn

Date: 2026-11-25 · Status: proposed (owner's decision of 2026-11-25: move the Government agenda watch into one coordinated nightly cycle and review every synchronisation and monitoring schedule; prepared and tested only — no production schedule changed, nothing merged or deployed)

## Context

On 25.11.2026 the host KOCORP (UTC clock, no Europe/Tallinn zone) ran Juristid's
scheduled work from three cron files under `/boot/config/plugins/dynamix/`, each
added by a different release:

| Job | Cron (UTC) | Tallinn winter / summer | Source on the host | Lock / precondition |
|---|---|---|---|---|
| Daily backup set + RPO check | `45 20 * * *` | 22:45 / 23:45 | `juristid.cron` → `deploy/unraid-main/cron/juristid-backup-nightly.sh` | takes the host lock; waits ≤ 20 min for it |
| Lovable synchronisation (nightly) | `15 21,22 * * *` | 23:15 + 00:15 / 00:15 + 01:15; `lovable_sync due` runs only the 00:15 Tallinn firing | `juristid-lovable-sync.cron` → `scripts/deploy/juristid-lovable-sync.sh` | flock `/tmp/juristid-lovable-sync.lock`, host lock, web healthy, pause file, root-only credentials, run key per Tallinn date, APPLY → fresh backup set verified to level 2 (`VERIFIED-L2`, < 2 h old) or nothing is written, breaker (ADR 0213) |
| Lovable / OneNote on-demand lane | `2-59/5 * * * *` | every 5 min | `juristid-lovable-sync.cron` → same script `--on-demand` | same locks; acts only on an administrator's waiting request |
| Register watch (ADR 0217) | `30 3 * * *` | 05:30 / 06:30 | `juristid-register-watch.cron` → `docker exec … watch_registers` | PostgreSQL advisory lock 2170001; model step bounded per batch |
| «Uued eelnõud» sweep (ADR 0218) | `20 4 * * *` | 06:20 / 07:20 | `juristid-register-watch.cron` → `docker exec … sweep_registers` | advisory lock 2180001; leaves out what the watch follows, so after the watch |
| Government agenda (ADR 0219) | `*/30 10-23 * * 3` and `*/30 0-9 * * 4` | Wed 12:00 – Thu 11:30 (winter only; the summer hour was a manual edit) | `juristid-register-watch.cron` → `docker exec … watch_government_agenda` | no weekday guard in the command; a stored week reads nothing; at most `MAX_ATTEMPTS` = 3 model attempts per agenda |
| Monitor fast / daily / weekly | `*/15`, `5 4 * * *`, `0 5 * * 1` | — | `juristid.cron` → `scripts/deploy/juristid-monitor.sh` | reads only |

Measured on the host (logs of 21–25.11.2026): backup ≈ 3 min; Lovable APPLY ≈ 5½ min
(backup + level-2 verification ≈ 4 min of it); register watch ≈ 12 min; sweep ≈ 1 min;
agenda < 1 min (one query once the week is stored). There is no separate job for «AI
analysis» or «pending retries»: each command judges what it found with the model and
retries what was left unjudged on its next run, under its own limits. The Riigikogu
cycle (`watch_riigikogu`) exists only on `rk/programme` (PR #723, draft, switched off),
whose own record proposes running it after the watch and before the sweep.

The owner decided on 25.11.2026 that this work runs in one coordinated cycle starting at
00:00 Europe/Tallinn, with dependent steps in order, independent ones side by side where
safe, no step's failure needlessly stopping another, every safeguard kept, and daytime
schedules removed only by an authorised deployment.

## Decision

1. **One cycle a night at 00:00 Europe/Tallinn**, run by
   `scripts/deploy/juristid-nightly-cycle.sh` from one cron line, `0 21,22 * * *`
   (`deploy/unraid-main/cron/juristid-nightly.cron`). The host has no Tallinn zone, so
   cron fires at both possible midnights and the script decides which one it is: Estonia
   follows the EU rule — UTC+3 from 01:00 UTC on the last Sunday of March to 01:00 UTC on
   the last Sunday of October, UTC+2 otherwise — computed with `date -u` alone. This is
   the pattern the Lovable synchronisation already uses (two firings, one is due), with
   the decision on the host instead of in a container, because the cycle must decide
   before it knows whether the web container is healthy. A test holds the rule against
   the zone database for 2026–2030, including both nights of each change. The other
   firing exits at once and leaves no file; a marker per Tallinn date
   (`nightly/state/<date>.done`) makes a second start of the same night refuse.
   `--manual` runs a cycle now (every lock still honoured); `--plan [--at …]` prints the
   decision and the steps and writes nothing.
2. **The steps are the existing jobs, unchanged.** The Lovable step runs
   `juristid-lovable-sync.sh` exactly as its cron line did (scheduled trigger; its own
   `due`, run key, pause file, credentials check, APPLY backup with level-2 proof, breaker
   and observation all apply). The register watch, the sweep and the agenda are the same
   `docker exec --env-file config/register-watch.env juristid-main-web python manage.py
   …` commands, appending to the same logs (`register-watch.log`, `register-sweep.log`,
   `government-agenda.log`). No management command, limit, breaker or model setting is
   changed by this record; the AI limits stay where they are (agenda `MAX_ATTEMPTS` = 3,
   the watch's and sweep's batches and token caps), and so do their audit records
   (`WatchRun`, `SweepRun`, `GovernmentAgenda.ai_attempts`, `LovableSyncRun`).
3. **Order.** The Lovable synchronisation runs first and alone: a Teema Lovable created
   that night is then followed by the watch the same night, the sweep does not offer its
   draft as a new topic, and the agenda's model sees it. After it, two lanes run side by
   side:
   - *registers*: `watch_registers` → `watch_riigikogu` (once installed, §10) →
     `sweep_registers`, one after another — they share the Riigikogu API's published
     rate limit, and the sweep leaves out drafts the watch already follows;
   - *agenda*: `watch_government_agenda` — valitsus.ee only, no shared source or lock.

   Each command's model step follows its own discovery inside the same command, so «AI
   analysis after discovery» holds within every step, and the night's unjudged items are
   retried by the same commands the next night. Expected length: ≈ 6 min for Lovable,
   then ≈ 13 min for the longer lane — about 20 minutes, ending ≈ 22:20 UTC in winter and
   ≈ 21:20 UTC in summer, well before the 23:00 UTC database dumps.
4. **Failure is per step, and said.** The script has no `set -e`; every step's exit
   code is captured and recorded as `OK`, `FAILED rc=N` or `SKIPPED <reason>`. Only one
   dependency is hard: `register-sweep` needs `register-watch` to have finished `OK`, else
   it would offer drafts that a Teema already follows; it is then SKIPPED with
   «NEEDS register-watch, which was FAILED». Every other order is soft — a failed Lovable
   night still lets the watch read the Teemad as they stand, and a failed agenda or
   Riigikogu step touches nothing else. The night's outcome goes to its log
   (`nightly/logs/`, 30 days), to `nightly/last-cycle.txt`, and — when a step failed or
   could not run — to one Unraid notification whose text holds step names and paths
   only; the exit status is then 1. A deliberate pause (`nightly/PAUSED`,
   `nightly/PAUSED-<step>`) and the Riigikogu step left out are not failures. The
   application's own health checks (`production_status`, the daily monitor at 04:05 UTC)
   keep reading the same run records and keep their thresholds.
5. **Locks.** One cycle at a time (`flock` on `/tmp/juristid-nightly-cycle.lock`; a
   second one exits quietly). Before every step the release lock
   (`/tmp/juristid-host-lock`, `scripts/deploy/lib.sh`) is waited for, at most 20
   minutes; a step that still finds it held is SKIPPED by the holder's name and the lock
   is never touched. Before the Lovable step the runner's own lock is waited for (≤ 20
   min), so an administrator's on-demand run still going does not make the night's sync
   SKIP as it could at 22:15 before. The steps' own locks — the sync's flock and host
   lock, the commands' advisory locks — are theirs and unchanged. The register commands
   gain the release-lock check they never had; they do not take the lock, so the two
   lanes do not exclude each other.
6. **The Government's agenda is read every night.** The command has no weekday guard of
   its own — the Wednesday/Thursday limit was only in its cron lines — and its own rules
   make a nightly run cheap and safe: a week whose agenda is stored reads nothing again,
   days later than tomorrow are not tried, a missing page (404) is not an error, and a
   model that failed is asked at most three times per agenda in all. The run at 00:00 on
   the night to Thursday finds the agenda published on Wednesday, before the Thursday
   session; the runs on other nights find nothing new and cost one query. What changes:
   an agenda published, or first readable, only after midnight to Thursday is found at
   00:00 on Friday, after the session, where the half-hourly watch found it on Thursday
   morning (§11 asks the owner).
7. **What the authorised deployment changes on the host** (and nothing before it):
   - add `/boot/config/plugins/dynamix/juristid-nightly.cron` from
     `deploy/unraid-main/cron/juristid-nightly.cron`:
     `0 21,22 * * * bash /mnt/user/appdata/juristid-main/repo/scripts/deploy/juristid-nightly-cycle.sh &> /dev/null`;
   - in `juristid-lovable-sync.cron`, remove
     `15 21,22 * * * bash /mnt/user/appdata/juristid-main/repo/scripts/deploy/juristid-lovable-sync.sh &> /dev/null`
     (the on-demand `2-59/5` line stays — it serves an administrator's request, it is not
     a schedule of the night);
   - remove `juristid-register-watch.cron` whole: the `30 3 * * *` watch, the
     `20 4 * * *` sweep and the two agenda lines `*/30 10-23 * * 3`, `*/30 0-9 * * 4`;
   - `juristid.cron` (backup 20:45, monitors) is unchanged;
   - then `update_cron`, and `grep juristid /etc/cron.d/root` shows exactly the backup,
     the three monitor lines, the on-demand line and the cycle.

   The repository at the deployed revision must contain the script, so the cron file is
   added only after the checkout holds it. The first cycle is watched in its log;
   `--plan` beforehand shows the decision for that evening.
8. **Rollback** restores the cron lines of 25.11.2026: copies of
   `juristid-lovable-sync.cron` and `juristid-register-watch.cron` are kept beside the
   release (`*.cron.pre-0227`) and put back, `juristid-nightly.cron` is removed, and
   `update_cron` runs. Nothing in the database depends on the cycle, so no data step is
   needed. The watch's 30-hour staleness threshold holds across either change of
   schedule (22:00 → 03:30 UTC the next-but-one morning is under 30 hours).
9. **Neighbours on the host.** The cycle starts 15 minutes after the 20:45 backup set
   (≈ 3 min) in summer and 75 minutes after it in winter; the backup's own lock wait is
   covered by §5. It ends before the 23:00 database dumps and the 23:30 USB backup in both
   seasons, before the 00:30 DashKoda/Juristid backups, the Monday 01:00 Appdata Backup
   (which stops every container), the 01:15 media tier, the 01:40 mover and DashKoda's
   02:00–07:00 feeds. The parity check on the 1st of a month at 21:30 overlaps it, as it
   overlapped the sync before. The release kits' refusal window («outside 20:30–22:40
   UTC») must widen to **20:30–23:00 UTC** so that a winter cycle's lanes are covered as
   well as its sync; that is a change to the kits' header, not to this repository.
10. **Riigikogu and the model's work.** The Riigikogu cycle plugs into the registers lane
    between the watch and the sweep, as its own record on `rk/programme` proposes, with no
    other change: once PR #723 is on main and the owner switches
    `JURISTID_RIIGIKOGU_MONITORING` on, the cron line gains `--with-riigikogu`. Until then
    the step is recorded `SKIPPED NOT_INSTALLED`, which is not a failure. Its ANALYSIS stage
    is recorded disabled by that programme and stays so. A failing Riigikogu step never
    skips the sweep (soft order). There is no standalone «retry the model» command to
    schedule; if one is ever written it belongs at the end of the lane whose items it
    judges.
11. **Open for the owner.** (a) Whether the agenda also keeps one Thursday-morning
    catch-up run (e.g. 07:00 Tallinn), or the midnight run alone is enough (§6).
    (b) The release window widening (§9). (c) Whether the daily monitor should also read
    `nightly/last-cycle.txt`; today the cycle reports by notification and the run records
    are read as before. (d) Two in-code descriptions of the old times stay until a later
    change touches that code: `watch.SCHEDULED_AT_UTC` (03:30, used only for the first
    run's grace after switching the watch on — more lenient, not stricter, with an
    earlier run) and `candidates.SWEEP_AT_UTC`, and the agenda module's docstring.

## Consequences

- One cron line replaces five nightly or weekly ones; the night's work is in one log with
  a one-line verdict per step, and a failure raises one notification instead of an
  appended line nobody reads.
- The register watch and the sweep run ≈ 5½ hours earlier (00:05–00:20 instead of
  05:30–06:20 Tallinn), so their proposals are ready when the working day starts, and
  they now see the Teemad Lovable created that night.
- The agenda is read nightly instead of 48 times between Wednesday noon and Thursday
  noon; the Wednesday-afternoon/Thursday-morning freshness is traded for one predictable
  read before the session (§6, §11).
- The register commands now wait for a release to finish, which they never did.
- The deployment is a host change the owner approves, with its rollback in §8.
