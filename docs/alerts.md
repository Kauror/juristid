# Alert catalogue — what `juristid-monitor.sh` sends, and what to do

`scripts/deploy/juristid-monitor.sh` (docs/adr/0196) turns the read-only checks
the runbooks already name into Unraid notifications, once per transition. This
is the list of what can arrive, with the first move for each. The thresholds
are in the monitor's environment file
(`deploy/unraid-main/cron/juristid-monitor.env.example`); the ones marked
*decision* have no default in the repository.

Severity: **CRIT** — the register is unreachable, unwritable or a copy is
missing; act now. **WARN** — degraded, stale or suspicious; act within a
working day. **INFO** — a digest; read it.

| Key | Tier | Severity | What it means | First move |
| --- | --- | --- | --- | --- |
| `containers` | fast | CRIT | an expected container is not running or is unhealthy (the MCP trio only while `MONITOR_MCP_PROFILE_ON=1`); a tunnel the monitor restarted shows as `(restarted)` until it is healthy | `docker compose -p juristid-main -f … ps`; README "Everyday operations"; for `mcp-tunnel`, OPERATIONS.md "After recreating mcp" |
| `healthz` | fast | CRIT | the public `/healthz` did not answer 200 ok | 503 = database (RECOVERY.md Scenario B); no answer = tunnel or web (RECOVERY.md "Cloudflare tunnel recovery") |
| `db` | fast | CRIT | `pg_isready` failed inside the db container | RECOVERY.md Scenario B |
| `space` | fast | WARN / CRIT | free space on the data or backup root below the thresholds (default 20 / 5 GiB) | the deploy preflight refuses below 2 GiB; do not free space by deleting Docker objects |
| `backup_age` | daily | WARN / CRIT | the newest complete set is older than the RPO (*decision*, `MONITOR_RPO_HOURS`) / there is no complete set | RECOVERY.md "Has a backup been taken lately?"; take a set by hand if the schedule did not |
| `partials` | daily | WARN | unfinished `.partial` runs under `sets/` | look at them; they are not backups and nothing removes them |
| `dump_age` | daily | WARN | the host's nightly `pg_dump` is older than 26 h | the host's own dump job log (RECOVERY.md "Host-level copies") |
| `lovable` | daily | WARN | the newest scheduled night that ran failed or did not finish, or none ran in 26 h (a not-due firing is not a night; a paused or off sync is `ok`) | Haldus → Lovable sünkroonimine; the night's log under `l100/sync/logs/` |
| `nightly` | daily | WARN | the night's coordinated cycle (ADR 0229) never started, was refused (a release or restore held the cycle lock), was interrupted or is overdue (started, not completed after 4 h), or completed with a PARTIAL, FAILED, INTERRUPTED or SKIPPED_UNEXPECTED step (a step stopped at its execution budget, not started because its previous run was alive, or not verified gone is named `(TIMEOUT)`, `(BUSY)`, `(STUCK)`; an uncleared `<night>.stuck` is reported first, ADR 0234); a paused cycle or an authorised manual recovery is `ok` | `nightly/last-cycle.txt`, `nightly/state/<night>.*` and `nightly/logs/`; recover by hand with `juristid-nightly-cycle.sh --manual`, never by re-running cron |
| `status` | daily | WARN | `production_status` reports a failing row | run the row's own command (docs/production-readiness.md §4) |
| `errors` | daily | WARN | more ERROR/CRITICAL lines than `MONITOR_ERRORS_WARN` in a container's last 24 h (counts only), read from the kept logs so a release does not reset them | `MONITOR_APP_LOG_DIR` (default `appdata/juristid-main/app-logs/<service>-<day>.log`, 30 days) |
| `auth` | daily | WARN | gate failures, lockouts or step-up failures over the thresholds; any MCP refresh-token reuse, grant suspension, emergency disablement or refused write commit | `manage.py audit_failures --since 24h`; docs/incident-response.md scenarios 1 and 5 |
| `appdata_backup` | daily | WARN | the last Unraid Appdata Backup run is flagged failed | the plugin's log; its retention cleanup did not run |
| `evidence` | weekly | WARN | `check_evidence_integrity` found a problem | RECOVERY.md Scenario C |
| `verify_newest` | weekly | WARN | the newest set fails level-2 verification | RECOVERY.md "Checking a backup" |
| `digest` | weekly | INFO | sizes, set count, free space | read it; growth is the trend the 90 % cliff cannot show |
| `restarted` | fast | INFO | `mcp-tunnel` or `tunnel` was unhealthy for two checks in a row and the monitor restarted it (at most once in six hours each) | if it stays unhealthy, docs/mcp/OPERATIONS.md; the restart itself needs nothing |
| `monitor` | any | CRIT | the monitor could not start: its environment file is missing or open, or a required setting is absent (at most once in six hours) | fix `config/monitor.env` (root, 600); until then no other alert is sent |

What no message may carry: a Matter, a person, an address, a user agent, a
secret, a path under the evidence tree, a traceback body. The audit counts
come from `audit_failures`, which never prints a row.

What the monitor cannot see: itself. Every finished run writes
`monitor-state/last-run-<tier>`; the host watchdog
(`/mnt/user/appdata/kocorp-backup/watchdog.sh`, every 15 minutes, independent of
this script) alerts when `last-run-fast` is older than 45 minutes (quality audit
2026-11, QA26-OPS-05). A refusal to start sends `monitor` itself.

What the host cannot see: itself. A dead host sends nothing. The cheapest
independent probes are a Cloudflare *Tunnel Health* notification (free plan)
and a dead-man's-switch ping from the fast tier (`MONITOR_HEARTBEAT_URL`, a
UUID and the host's address leave the host, nothing else). Both are owner
decisions.

Recipient: Unraid's `notify` reaches a person only through a configured
notification agent (Settings → Notifications). Without one, every message here
ends in the WebGUI bell.
