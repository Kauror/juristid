# 0196 — Scheduled operations share the host lock, and the application's checks become notifications

- Status: Proposed (security and resilience review 2026-10-29; not merged)
- Relates to: 0022 (backup is taken by hand, not scheduled), 0179 (restore
  refuses unsafe objects; the db/tunnel recreate step), 0183 (credential
  changes end sessions), 0185 §6 and 0189 (the MCP processes' own checks),
  0192 (the Lovable sync takes the host lock), 0193 (CI tiers)
- Amends nothing. It adds what the review found missing and leaves every
  decision the review surfaced where it was (`docs/open-decisions.md`).

## Context

The 2026-10-29 independent security and operational-resilience review
(`JURISTID_SECURITY_AND_RESILIENCE_REVIEW.md`, outside the repository) found
no critical or high weakness in the application: the actor × route matrices,
the MCP write chain and the Lovable pull all held. What it found was
operational:

1. **Checks exist and nobody is told.** Container health, `/healthz`,
   `production_status`, the backup age check, the Lovable sync ledger and the
   audit tables each end in an exit code, a log line, a row or a page. The host
   has a watchdog that calls Unraid's `notify`, but it knows five of the ten
   Juristid containers and nothing of the application, and no notification
   agent is configured, so even its alerts stop at the WebGUI bell.
2. **The backup and the restore took no lock.** The Lovable sync takes
   `/tmp/juristid-host-lock` so a release cannot start under a run; a backup
   could run under a release's `migrate` or under a restore's
   `pg_restore --single-transaction`, and record the empty pre-restore database
   as that minute's backup.
3. **The audit tables had no reader.** Failed gate attempts with their reason,
   refused administrator step-ups, MCP locks, suspensions and refresh-token
   reuse, refused write commits — all recorded, none counted.
4. Smaller things pinned by tests: the shared gate's lockout keyed on a full
   IPv6 address while the sign-in throttle counted the /64; a fresh gate pass
   rotated the session key but not the CSRF secret; a malformed `?kood=` value
   wrote a session row before the gate; `ThrottleScope.CREDENTIAL_LINK` was
   defined and never charged; a NUL in any Lovable string failed the whole
   night as an opaque error; the always-on application containers ran with
   Docker's default capabilities while the MCP ones did not; the age check
   called a future-dated stamp fresh for ever; the off-host wrapper's
   same-host guard missed `/mnt/disks` (the USB disk) and bare paths.

## Decision

### 1. One host lock for everything that must not overlap

`scripts/deploy/lib.sh` owns the lock (`take_host_lock`, `host_lock_owner`,
`release_host_lock`; `JURISTID_HOST_LOCK`, `/tmp/juristid-host-lock`). The
backup takes it before its first write and the restore before the first byte
reaches the container; both release it on exit and both refuse, by the owner's
name and with a non-zero status, when another operation holds it. The Lovable
sync already took it; a release takes it for its window (README). A scheduled
backup that meets a release therefore does not happen, and the next morning's
age check is what says whether that mattered.

### 2. The application's checks become notifications through what the host has

`scripts/deploy/juristid-monitor.sh` runs, read-only, the checks the runbooks
already name — container state and health, `/healthz`, `pg_isready`, free
space, the backup age against the RPO, unfinished backup runs, the nightly
dump's age, the Lovable sync's last scheduled night, `production_status`, log
error counts, the audit counts, the weekly `check_evidence_integrity` and a
level-2 verification of the newest set — and calls the same Unraid `notify` the
host's watchdog calls, **once per transition** (and once a day while a warning
persists). It repairs, restarts and rebuilds nothing. A message carries a
check's name, a container's name, a count, an age or a size; never a Matter, a
person, an address, a secret or a traceback. The thresholds live in a
root-only environment file; the RPO has no default there either.

`manage.py audit_failures` is the reader the audit tables lacked: counts per
event and reason over a window, three output shapes, no row and no address.

### 3. What is prepared and not installed

`deploy/unraid-main/cron/` holds the nightly backup wrapper and example cron
and environment files. Installing a cron line is a host change the owner
approves, and the age limit it needs is the RPO, a decision. Nothing in the
repository installs them, and nothing here deletes a backup set.

### 4. What the review fixed in the application

The items in Context 4, each in the smallest safe way and each pinned by a
test that failed before the change: `shared_gate.client_key` counts
`throttle.counting_unit`; `open_gate` rotates the CSRF token on a fresh pass;
`keep_link_proof` keeps nothing for a value that is not the shape of a link;
`_link_view` charges `CREDENTIAL_LINK`; `lovable_source.only_columns` drops a
NUL and logs the table, row id and column; the five always-on application
services in `compose.yml` carry `cap_drop: ALL`, `no-new-privileges` and a
`pids_limit`; `juristid.W059` warns on real data while the application's
PostgreSQL role is privileged; the preflight warns about `*.bak*` copies
beside the environment file; the BuildKit `# syntax=` directive is gone; the
release artifact records the image's Debian packages and the uv lock;
Dependabot proposes Actions and uv bumps.

## Consequences

- A scheduled backup becomes possible without a race with releases, restores
  or the sync. Whether one runs, and how old the newest set may be, are still
  the owner's (`docs/open-decisions.md`).
- Alerts reach a person only once an Unraid notification agent exists. That
  is a host setting, recorded as the first owner decision of the review.
- `juristid.W059` becomes an error once the application's database role has
  been split from the cluster superuser on the live cluster — an announced
  maintenance step, not part of this record.
- The MCP containers are expected by the monitor only while
  `MONITOR_MCP_PROFILE_ON=1`; switching the profile off
  (`docs/mcp/OPERATIONS.md`) means setting that to 0 in the same step.
- Memory limits, a read-only root for `web`, the `db` and `tunnel` hardening,
  the split of networks into an egress-free data network, the second
  administrator, the off-host destination, the secrets escrow, retention, RPO,
  RTO and the alert recipient are deliberately not decided here; the review
  report lists them as owner decisions with the options and their consequences.
