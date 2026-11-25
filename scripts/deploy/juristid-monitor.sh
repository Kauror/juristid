#!/usr/bin/env bash
#
# Run the read-only checks the runbooks already name, and tell somebody when
# one fails. Nothing here repairs, rebuilds or writes to the application: every
# command below is one the runbooks document as safe to run at any time. What
# it writes: its own state files, and the application containers' logs it
# keeps under MONITOR_APP_LOG_DIR. What it restarts: only a tunnel container
# (`mcp-tunnel`, `tunnel`) that stayed unhealthy for two checks in a row, at
# most once in six hours, and it says so (quality audit 2026-11, QA26-OPS-06).
#
# WHY THIS EXISTS
#
# The 2026-10-29 security and resilience review found that every check the
# system has — container health, `/healthz`, `production_status`, the backup
# age check, the Lovable sync ledger, the audit tables — ends in an exit code,
# a log line or a page that nobody is pushed to read. The host's own watchdog
# calls Unraid's `notify`, but knows half the Juristid containers and nothing
# of the application. This wrapper is the one place the application's checks
# become notifications, through the same `notify` the host already uses.
#
# TIERS
#
#   --tier fast     every 15 minutes: containers (restarting an unhealthy
#                   tunnel), /healthz, the database, free space, keeping the
#                   application logs, an optional heartbeat ping
#   --tier daily    once a day: backup age against the RPO, unfinished
#                   backup runs, the nightly dump's age, the Lovable sync's
#                   last scheduled night, the nightly cycle's last night
#                   (docs/adr/0229 §6), production_status, log error
#                   counts, authentication failure counts
#   --tier weekly   once a week: evidence integrity (structural), a level-2
#                   verification of the newest set, and an INFO digest
#   --tier logs     only keep the application logs: a release runs it before
#                   it recreates the containers (their logs die with them)
#
# Every finished run writes `last-run-<tier>` in --state-dir; the host
# watchdog reads `last-run-fast` to tell when the monitor itself has stopped.
# A run that cannot start (environment file missing or open, a setting
# absent) sends «CRIT monitor», at most once in six hours.
#
# ONE MESSAGE PER TRANSITION
#
# A check that fails sends one message when it starts failing and one when it
# recovers, never one per run; a WARN that persists is repeated once a day. The
# state lives in --state-dir, one small file per check. A dry run prints what
# it would send and touches no state.
#
# WHAT A MESSAGE MAY CARRY
#
# A check's name, a container's name, a count, an age, a size, the first line
# of a log that names no record. Never a Matter, a person, an address, a
# secret, a path under the evidence tree, or a traceback body.
#
# THE NUMBERS THAT ARE DECISIONS
#
# The RPO (`MONITOR_RPO_HOURS`) has no default: the age check it feeds refuses
# to invent one (docs/open-decisions.md), and so does this. The other
# thresholds default to the values the review proposed and are overridable in
# the environment file.
#
# EXIT STATUS
#
#   0  the checks ran (whatever they found — findings are notifications)
#   1  the wrapper itself could not run (arguments, environment file, tools)

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

TIER=""
ENV_FILE=""
STATE_DIR=""
DRY_RUN=0

usage() {
  cat <<'USAGE'
usage: juristid-monitor.sh --tier fast|daily|weekly|logs --env-file PATH --state-dir DIR [--dry-run]

The environment file (root, mode 600) names the deployment and the thresholds:

  MONITOR_PROJECT            Compose project (default juristid-main)
  MONITOR_COMPOSE_FILE       the Compose file (required)
  MONITOR_DATA_ROOT          the appdata root holding evidence/ (required)
  MONITOR_BACKUP_ROOT        the backup root holding sets/ (required)
  MONITOR_HEALTHZ_URL        the public /healthz (optional; skipped if empty)
  MONITOR_DUMP_DIR           the host's nightly pg_dump directory (optional)
  MONITOR_SYNC_LOG_DIR       the Lovable sync's log directory (optional)
  MONITOR_NIGHTLY_DIR        the nightly cycle's directory, nightly/ (optional; set it
                             when the cycle is installed — then a night that never
                             started is reported)
  MONITOR_APPDATA_BACKUP_DIR the Unraid Appdata Backup tree (optional)
  MONITOR_HEARTBEAT_URL      a dead-man's-switch URL to ping after a fast run (optional)
  MONITOR_APP_LOG_DIR        where the application logs are kept (default: MONITOR_DATA_ROOT/app-logs, 30 days)
  MONITOR_MCP_PROFILE_ON     1 while the mcp profile is meant to be running (default 0)
  MONITOR_RPO_HOURS          the backup age limit — required for --tier daily, no default
  MONITOR_FREE_GIB_WARN      free space warning (default 20)
  MONITOR_FREE_GIB_CRIT      free space critical (default 5)
  MONITOR_ERRORS_WARN        log error lines per container per day before a WARN (default 0)
  MONITOR_GATE_FAIL_WARN     failed gate attempts per day before a WARN (default 20)
  MONITOR_LOCKED_OUT_WARN    gate lockouts per day before a WARN (default 3)
  MONITOR_STEP_UP_FAIL_WARN  administrator step-up failures per day before a WARN (default 3)
  MONITOR_NOTIFY             the notify command (default /usr/local/emhttp/webGui/scripts/notify)
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --tier) TIER="${2:-}"; shift 2 ;;
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --state-dir) STATE_DIR="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

case "$TIER" in
  fast | daily | weekly | logs) : ;;
  *) usage >&2; die "--tier must be fast, daily, weekly or logs" ;;
esac
[ -n "$ENV_FILE" ] || { usage >&2; die "--env-file is required"; }
[ -n "$STATE_DIR" ] || { usage >&2; die "--state-dir is required"; }

#: A monitor that cannot run must not look like a healthy one (quality audit
#: QA26-OPS-05): every alarm below depends on this script, and cron discards its
#: output. Once its arguments are known, a refusal is also sent — at most once
#: in six hours, so a broken setup does not alert every 15 minutes.
monitor_cannot_run() {
  local reason="$1" notifier marker
  notifier="${MONITOR_NOTIFY:-${JURISTID_MONITOR_NOTIFY:-/usr/local/emhttp/webGui/scripts/notify}}"
  marker="$STATE_DIR/monitor-cannot-run"
  if [ "$DRY_RUN" != 1 ] && [ -x "$notifier" ] &&
    { [ ! -e "$marker" ] || [ -n "$(find "$marker" -mmin +360 2>/dev/null)" ]; }; then
    "$notifier" -e "Juristid monitor" -s "CRIT monitor" -d "the $TIER check could not run: $reason" -i alert >/dev/null 2>&1 || true
    mkdir -p "$STATE_DIR" 2>/dev/null && touch "$marker" 2>/dev/null || true
  fi
  die "$reason"
}

[ -f "$ENV_FILE" ] || monitor_cannot_run "$ENV_FILE does not exist"

# Root-owned and private on the host. Only checked where this runs as root: a
# non-root test harness cannot hold a mode-600 file on every filesystem, and
# cannot read root's file either way.
if [ "$(id -u)" = 0 ]; then
  mode="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo '')"
  case "$mode" in
    600 | 400 | '') : ;;
    *) monitor_cannot_run "$ENV_FILE is mode $mode; it must be 600" ;;
  esac
fi

# Read KEY=value lines only; never `source`, which would execute the file.
monitor_setting() {
  local key="$1" line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  printf '%s' "${line#"${key}="}"
}

MONITOR_PROJECT="$(monitor_setting MONITOR_PROJECT)"
MONITOR_PROJECT="${MONITOR_PROJECT:-juristid-main}"
MONITOR_COMPOSE_FILE="$(monitor_setting MONITOR_COMPOSE_FILE)"
MONITOR_DATA_ROOT="$(monitor_setting MONITOR_DATA_ROOT)"
MONITOR_BACKUP_ROOT="$(monitor_setting MONITOR_BACKUP_ROOT)"
MONITOR_HEALTHZ_URL="$(monitor_setting MONITOR_HEALTHZ_URL)"
MONITOR_DUMP_DIR="$(monitor_setting MONITOR_DUMP_DIR)"
MONITOR_SYNC_LOG_DIR="$(monitor_setting MONITOR_SYNC_LOG_DIR)"
MONITOR_NIGHTLY_DIR="$(monitor_setting MONITOR_NIGHTLY_DIR)"
MONITOR_APPDATA_BACKUP_DIR="$(monitor_setting MONITOR_APPDATA_BACKUP_DIR)"
MONITOR_HEARTBEAT_URL="$(monitor_setting MONITOR_HEARTBEAT_URL)"
MONITOR_MCP_PROFILE_ON="$(monitor_setting MONITOR_MCP_PROFILE_ON)"
MONITOR_RPO_HOURS="$(monitor_setting MONITOR_RPO_HOURS)"
MONITOR_FREE_GIB_WARN="$(monitor_setting MONITOR_FREE_GIB_WARN)"
MONITOR_FREE_GIB_CRIT="$(monitor_setting MONITOR_FREE_GIB_CRIT)"
MONITOR_ERRORS_WARN="$(monitor_setting MONITOR_ERRORS_WARN)"
MONITOR_GATE_FAIL_WARN="$(monitor_setting MONITOR_GATE_FAIL_WARN)"
MONITOR_LOCKED_OUT_WARN="$(monitor_setting MONITOR_LOCKED_OUT_WARN)"
MONITOR_STEP_UP_FAIL_WARN="$(monitor_setting MONITOR_STEP_UP_FAIL_WARN)"
MONITOR_NOTIFY="$(monitor_setting MONITOR_NOTIFY)"
MONITOR_NOTIFY="${MONITOR_NOTIFY:-${JURISTID_MONITOR_NOTIFY:-/usr/local/emhttp/webGui/scripts/notify}}"
MONITOR_FREE_GIB_WARN="${MONITOR_FREE_GIB_WARN:-20}"
MONITOR_FREE_GIB_CRIT="${MONITOR_FREE_GIB_CRIT:-5}"
MONITOR_ERRORS_WARN="${MONITOR_ERRORS_WARN:-0}"
MONITOR_GATE_FAIL_WARN="${MONITOR_GATE_FAIL_WARN:-20}"
MONITOR_LOCKED_OUT_WARN="${MONITOR_LOCKED_OUT_WARN:-3}"
MONITOR_STEP_UP_FAIL_WARN="${MONITOR_STEP_UP_FAIL_WARN:-3}"
MONITOR_MCP_PROFILE_ON="${MONITOR_MCP_PROFILE_ON:-0}"

[ -n "$MONITOR_COMPOSE_FILE" ] || monitor_cannot_run "$ENV_FILE sets no MONITOR_COMPOSE_FILE"
[ -n "$MONITOR_DATA_ROOT" ] || monitor_cannot_run "$ENV_FILE sets no MONITOR_DATA_ROOT"
[ -n "$MONITOR_BACKUP_ROOT" ] || monitor_cannot_run "$ENV_FILE sets no MONITOR_BACKUP_ROOT"
if [ "$TIER" = daily ]; then
  case "$MONITOR_RPO_HOURS" in
    '' | *[!0-9]*) monitor_cannot_run "$ENV_FILE sets no MONITOR_RPO_HOURS. The backup age limit is the RPO, a decision (docs/open-decisions.md); it has no default here either." ;;
  esac
fi
#: Where the application containers' logs are kept past a release (QA26-OPS-04).
MONITOR_APP_LOG_DIR="$(monitor_setting MONITOR_APP_LOG_DIR)"
MONITOR_APP_LOG_DIR="${MONITOR_APP_LOG_DIR:-$MONITOR_DATA_ROOT/app-logs}"
MONITOR_APP_LOG_DAYS=30
require_known_project "$MONITOR_PROJECT"
require_command docker "the checks read the Compose stack"
JURISTID_PROJECT="$MONITOR_PROJECT"
JURISTID_COMPOSE_FILE="$MONITOR_COMPOSE_FILE"

mkdir -p "$STATE_DIR"

# -- notifications -----------------------------------------------------------

#: Severity → Unraid importance.
importance_of() {
  case "$1" in
    CRIT) printf 'alert' ;;
    WARN) printf 'warning' ;;
    *) printf 'normal' ;;
  esac
}

send() {
  local severity="$1" key="$2" message="$3"
  if [ "$DRY_RUN" = 1 ]; then
    note "DRY-RUN notify [$severity] $key: $message"
    return 0
  fi
  if [ -x "$MONITOR_NOTIFY" ]; then
    "$MONITOR_NOTIFY" -e "Juristid monitor" -s "$severity $key" -d "$message" -i "$(importance_of "$severity")" >/dev/null 2>&1 ||
      note "WARNING: notify failed for $key"
  else
    note "WARNING: $MONITOR_NOTIFY is not executable; would have sent [$severity] $key: $message"
  fi
}

#: Record a verdict and notify on the transition. `ok` after a failure sends a
#: recovery; a WARN or CRIT that persists is repeated once a day, not per run.
report() {
  local key="$1" severity="$2" message="$3" state previous
  state="$STATE_DIR/$key"
  previous="$(cat "$state" 2>/dev/null || printf 'ok')"
  note "$severity $key: $message"
  if [ "$DRY_RUN" = 1 ]; then
    [ "$severity" = ok ] && [ "$previous" = ok ] || send "$severity" "$key" "$message"
    return 0
  fi
  if [ "$severity" != "$previous" ]; then
    if [ "$severity" = ok ]; then
      send recovered "$key" "$message"
    else
      send "$severity" "$key" "$message"
    fi
    printf '%s\n' "$severity" > "$state"
    return 0
  fi
  if [ "$severity" != ok ] && [ -n "$(find "$state" -mmin +1440 2>/dev/null)" ]; then
    send "$severity" "$key" "$message (still)"
    touch "$state"
  fi
}

#: INFO never has a transition: it is sent whenever it is produced.
inform() {
  local key="$1" message="$2"
  note "INFO $key: $message"
  send INFO "$key" "$message"
}

# -- helpers -------------------------------------------------------------------

container_name() {
  printf '%s-%s' "$MONITOR_PROJECT" "$1"
}

#: The containers this deployment must have running: the always-on ones, and
#: the AI-access trio only while the profile is meant to be on.
expected_containers() {
  printf '%s\n' db web intake-reader searchindex intake-sweeper notifications tunnel
  if [ "$MONITOR_MCP_PROFILE_ON" = 1 ]; then
    printf '%s\n' mcp mcp-writer mcp-tunnel
  fi
}

free_gib_at() {
  local kib
  kib="$(free_kib "$1" 2>/dev/null || printf 0)"
  printf '%s' $((kib / 1024 / 1024))
}

newest_file_age_hours() {
  # The newest regular file in a directory, by mtime; prints the age in hours
  # or nothing when the directory holds no file.
  local directory="$1" newest
  newest="$(find "$directory" -maxdepth 1 -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -n 1 | cut -d' ' -f2- || true)"
  [ -n "$newest" ] || return 0
  printf '%s' $(( ( $(date +%s) - $(stat -c %Y "$newest") ) / 3600 ))
}

# -- fast ----------------------------------------------------------------------

#: The containers the monitor may restart on its own: the two tunnels, which hold
#: no data and only reconnect. `mcp-tunnel` stayed unhealthy 0.5–12 h three
#: times in 14 days after a db/mcp restart until a person restarted it (quality
#: audit QA26-OPS-06). Restarted after two unhealthy runs in a row (~30 min), at
#: most once in six hours each; every restart is sent.
SELF_HEALING="mcp-tunnel tunnel"

heal() {
  local service="$1" name="$2" count_file="$STATE_DIR/unhealthy-$1" stamp="$STATE_DIR/restarted-$1" count
  count=$(( $(cat "$count_file" 2>/dev/null || printf 0) + 1 ))
  if [ "$DRY_RUN" = 1 ]; then
    note "DRY-RUN $service unhealthy (run $count); would restart after 2"
    return 1
  fi
  printf '%s\n' "$count" > "$count_file"
  [ "$count" -ge 2 ] || return 1
  if [ -e "$stamp" ] && [ -z "$(find "$stamp" -mmin +360 2>/dev/null)" ]; then
    return 1
  fi
  touch "$stamp"
  rm -f -- "$count_file"
  if docker restart "$name" >/dev/null 2>&1; then
    inform restarted "$service was unhealthy for two checks in a row and was restarted. If it stays unhealthy, see docs/mcp/OPERATIONS.md."
    return 0
  fi
  inform restarted "$service was unhealthy for two checks in a row; restarting it failed."
  return 1
}

check_containers() {
  local service name state down=""
  while read -r service; do
    name="$(container_name "$service")"
    state="$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name" 2>/dev/null || printf 'absent none')"
    case "$state" in
      "running healthy" | "running none" | "running starting")
        [ "$DRY_RUN" = 1 ] || rm -f -- "$STATE_DIR/unhealthy-$service"
        ;;
      "running unhealthy")
        # Still reported until it is healthy again; the restart is sent on its own.
        if [[ " $SELF_HEALING " == *" $service "* ]] && heal "$service" "$name"; then
          down="$down $service(restarted)"
        else
          down="$down $service(${state// //})"
        fi
        ;;
      *) down="$down $service(${state// //})" ;;
    esac
  done < <(expected_containers)
  if [ -n "$down" ]; then
    report containers CRIT "not running or unhealthy:$down. See deploy/unraid-main/README.md, Everyday operations (the stack's ps and logs)."
  else
    report containers ok "every expected container is running"
  fi
}

check_healthz() {
  [ -n "$MONITOR_HEALTHZ_URL" ] || return 0
  local code body
  body="$(mktemp)"
  code="$(curl -s -o "$body" -w '%{http_code}' --max-time 25 "$MONITOR_HEALTHZ_URL" 2>/dev/null || printf '000')"
  if [ "$code" = 200 ] && grep -q '"ok"' "$body"; then
    report healthz ok "HTTP 200 ok"
  else
    report healthz CRIT "HTTP $code. 503 = database (RECOVERY.md Scenario B); 000 or 5xx = tunnel or web (RECOVERY.md, Cloudflare tunnel recovery)."
  fi
  rm -f -- "$body"
}

check_database() {
  if docker exec "$(container_name db)" pg_isready -q >/dev/null 2>&1; then
    report db ok "pg_isready"
  else
    report db CRIT "pg_isready failed in $(container_name db)."
  fi
}

check_space() {
  local tree free worst="" severity=ok
  for tree in "$MONITOR_DATA_ROOT" "$MONITOR_BACKUP_ROOT"; do
    [ -d "$tree" ] || continue
    free="$(free_gib_at "$tree")"
    if [ "$free" -lt "$MONITOR_FREE_GIB_CRIT" ]; then
      severity=CRIT; worst="$worst $tree=${free}GiB"
    elif [ "$free" -lt "$MONITOR_FREE_GIB_WARN" ] && [ "$severity" != CRIT ]; then
      severity=WARN; worst="$worst $tree=${free}GiB"
    fi
  done
  if [ "$severity" = ok ]; then
    report space ok "free space above ${MONITOR_FREE_GIB_WARN} GiB on the data and backup roots"
  else
    report space "$severity" "free space low:$worst (warn ${MONITOR_FREE_GIB_WARN}, critical ${MONITOR_FREE_GIB_CRIT}). The deploy preflight refuses below 2 GiB."
  fi
}

#: The application containers whose logs are kept.
APP_LOG_SERVICES="web intake-reader searchindex intake-sweeper notifications mcp mcp-writer"

#: Append each application container's log since the last collection to
#: `<service>-<UTC day>.log` under MONITOR_APP_LOG_DIR, with Docker's own
#: timestamps, and drop files older than MONITOR_APP_LOG_DAYS. A container's
#: log dies with the container at every release; collected every 15 minutes
#: (and by a release before it recreates them, `--tier logs`), the history
#: survives (quality audit QA26-OPS-04). Nothing leaves the host.
collect_logs() {
  local service name since_file since until day
  [ "$DRY_RUN" = 1 ] && { note "DRY-RUN collect logs into $MONITOR_APP_LOG_DIR"; return 0; }
  mkdir -p "$MONITOR_APP_LOG_DIR" && chmod 0700 "$MONITOR_APP_LOG_DIR" 2>/dev/null || true
  until="$(date -u +%FT%T.%NZ)"
  day="$(date -u +%Y%m%d)"
  for service in $APP_LOG_SERVICES; do
    name="$(container_name "$service")"
    docker inspect -f '{{.State.Status}}' "$name" >/dev/null 2>&1 || continue
    since_file="$STATE_DIR/logs-since-$service"
    since="$(cat "$since_file" 2>/dev/null || printf '1h')"
    if docker logs --timestamps --since "$since" --until "$until" "$name" >>"$MONITOR_APP_LOG_DIR/$service-$day.log" 2>&1; then
      printf '%s\n' "$until" > "$since_file"
    fi
    chmod 0600 "$MONITOR_APP_LOG_DIR/$service-$day.log" 2>/dev/null || true
  done
  find "$MONITOR_APP_LOG_DIR" -maxdepth 1 -type f -name '*.log' -mtime +"$MONITOR_APP_LOG_DAYS" -delete 2>/dev/null || true
}

heartbeat() {
  [ -n "$MONITOR_HEARTBEAT_URL" ] || return 0
  [ "$DRY_RUN" = 1 ] && { note "DRY-RUN heartbeat ping"; return 0; }
  curl -fsS -m 10 -o /dev/null "$MONITOR_HEARTBEAT_URL" >/dev/null 2>&1 || note "WARNING: heartbeat ping failed"
}

# -- daily ---------------------------------------------------------------------

check_backup_age() {
  local output status
  output="$(bash "$SCRIPT_DIR/juristid-check-backup-age.sh" --backup-root "$MONITOR_BACKUP_ROOT" --max-age-hours "$MONITOR_RPO_HOURS" 2>&1)" && status=0 || status=$?
  case "$status" in
    0) report backup_age ok "$(printf '%s\n' "$output" | grep '^OK:' | head -n 1)" ;;
    3) report backup_age WARN "$(printf '%s\n' "$output" | grep '^CRITICAL:' | head -n 1) RECOVERY.md, Has a backup been taken lately?" ;;
    2) report backup_age CRIT "$(printf '%s\n' "$output" | grep '^CRITICAL:' | head -n 1)" ;;
    *) report backup_age WARN "the age check could not run (exit $status)." ;;
  esac
}

check_partials() {
  local count
  count="$(find "$MONITOR_BACKUP_ROOT/sets" -maxdepth 1 -mindepth 1 -type d -name '*.partial' 2>/dev/null | wc -l | tr -d ' ')"
  if [ "$count" -gt 0 ]; then
    report partials WARN "$count unfinished backup run(s) under sets/. They are not backups; look at them (RECOVERY.md)."
  else
    report partials ok "no unfinished backup run"
  fi
}

check_dump_age() {
  [ -n "$MONITOR_DUMP_DIR" ] || return 0
  local age
  age="$(newest_file_age_hours "$MONITOR_DUMP_DIR")"
  if [ -z "$age" ]; then
    report dump_age WARN "no nightly dump in $MONITOR_DUMP_DIR."
  elif [ "$age" -gt 26 ]; then
    report dump_age WARN "the newest nightly dump is ${age}h old; read the dump job's log on the host."
  else
    report dump_age ok "newest nightly dump ${age}h old"
  fi
}

#: The scheduled sync fires at 21:15 and 22:15 UTC and only the one at Tallinn
#: midnight runs; the other logs NOT_DUE and leaves without «END». The night to
#: judge is the newest log that ran (« run») or failed (« FAIL »); a firing that
#: was not due is not a night (quality audit QA26-ARCH-04: in summer time, and
#: whenever the sync is off, the newest log is always a NOT_DUE one). A sync
#: that is paused or off on purpose is said so, never «did not finish».
check_lovable() {
  [ -n "$MONITOR_SYNC_LOG_DIR" ] || return 0
  local newest latest age first path
  latest="$(find "$MONITOR_SYNC_LOG_DIR" -maxdepth 1 -type f -name '*-scheduled.log' -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -n 1 | cut -d' ' -f2- || true)"
  if [ -z "$latest" ]; then
    report lovable WARN "no scheduled Lovable sync log in $MONITOR_SYNC_LOG_DIR."
    return 0
  fi
  newest=""
  while read -r path; do
    if grep -qE ' run$| FAIL ' "$path"; then newest="$path"; break; fi
  done < <(find "$MONITOR_SYNC_LOG_DIR" -maxdepth 1 -type f -name '*-scheduled.log' -printf '%T@ %p\n' 2>/dev/null | sort -rn | cut -d' ' -f2-)
  if grep -qE 'SKIP paused|NOT_DUE (PAUSED|OFF)' "$latest" && { [ -z "$newest" ] || [ "$latest" -nt "$newest" ]; }; then
    report lovable ok "the scheduled sync is paused or off on purpose ($(grep -oE 'SKIP paused|NOT_DUE [A-Z_]+' "$latest" | head -n 1))."
    return 0
  fi
  if [ -z "$newest" ]; then
    report lovable WARN "no scheduled Lovable sync night has run (only not-due firings in $MONITOR_SYNC_LOG_DIR)."
    return 0
  fi
  age=$(( ( $(date +%s) - $(stat -c %Y "$newest") ) / 3600 ))
  if [ "$age" -gt 26 ]; then
    report lovable WARN "the newest scheduled Lovable sync night is ${age}h old; the cron line may not be running."
    return 0
  fi
  first="$(grep -E ' (FAIL|WARN) ' "$newest" | head -n 1 | cut -c21- || true)"
  if ! grep -q 'END rc=0' "$newest" || [ -n "$first" ]; then
    report lovable WARN "last scheduled night: ${first:-did not finish}. Haldus, Lovable sünkroonimine."
  else
    report lovable ok "last scheduled night finished, rc=0"
  fi
}

#: The nightly cycle (docs/adr/0229 §6). The night judged is the one whose cycle
#: should have completed: the Tallinn date of (now − NIGHTLY_COMPLETE_HOURS), so a
#: run at 04:05 UTC (06:05 or 07:05 in Tallinn) judges the night that began at
#: 00:00 that day. Never started, refused by a release, started but never
#: completed (interrupted, or still running past its time: overdue), and a
#: completed night with a PARTIAL, FAILED, INTERRUPTED or SKIPPED_UNEXPECTED step
#: are each a WARN naming the night and the steps. A paused cycle is ok on purpose;
#: an authorised --manual recovery of the night stands in for it.
#: JURISTID_MONITOR_NOW (epoch seconds) is a test seam; cron sets none.
NIGHTLY_COMPLETE_HOURS=4
check_nightly() {
  [ -n "$MONITOR_NIGHTLY_DIR" ] || return 0
  local now night state verdict bad pid steps
  now="${JURISTID_MONITOR_NOW:-$(date -u +%s)}"
  read -r night _ _ <<<"$(tallinn_clock "$((now - NIGHTLY_COMPLETE_HOURS * 3600))")"
  state="$MONITOR_NIGHTLY_DIR/state"
  if [ -e "$MONITOR_NIGHTLY_DIR/PAUSED" ]; then
    report nightly ok "the nightly cycle is paused on purpose ($MONITOR_NIGHTLY_DIR/PAUSED)."
    return 0
  fi
  for verdict in "$state/$night.recovered" "$state/$night.completed"; do
    [ -f "$verdict" ] || continue
    if grep -q '^paused ' "$verdict"; then
      report nightly ok "night $night: the cycle was paused on purpose."
      return 0
    fi
    bad="$(awk '$2 ~ /^(PARTIAL|FAILED|INTERRUPTED|SKIPPED_UNEXPECTED)$/ { printf "%s%s %s", sep, $1, $2; sep = ", " }' "$verdict")"
    if [ -n "$bad" ]; then
      report nightly WARN "night $night: $bad. Read $MONITOR_NIGHTLY_DIR/last-cycle.txt and the night's log."
    elif [ "$verdict" = "$state/$night.recovered" ]; then
      report nightly ok "night $night recovered by an authorised manual run."
    else
      report nightly ok "night $night completed; every step succeeded or was skipped on purpose."
    fi
    return 0
  done
  if [ -f "$state/$night.refused" ]; then
    report nightly WARN "night $night: the cycle refused to start ($(cut -c1-160 "$state/$night.refused")). A release or a restore held the cycle lock at 00:00 Tallinn."
    return 0
  fi
  if [ -f "$state/$night.started" ]; then
    pid="$(sed -nE 's/.* pid=([0-9]+).*/\1/p' "$state/$night.started")"
    steps="$(awk '{ printf "%s%s %s", sep, $1, $2; sep = ", " } END { if (!NR) printf "none" }' "$state/$night.outcomes" 2>/dev/null || printf 'none')"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      report nightly WARN "night $night: the cycle is still running past ${NIGHTLY_COMPLETE_HOURS}h (overdue); steps so far: $steps."
    else
      report nightly WARN "night $night: the cycle was interrupted (started, never completed); steps recorded: $steps. Recover it by hand (--manual), never by re-running cron."
    fi
    return 0
  fi
  report nightly WARN "night $night: the nightly cycle never started (cron line missing, host down at 00:00 Tallinn, or lock state). See docs/adr/0229."
}

check_status() {
  local output status failing
  output="$(juristid_compose exec -T web python manage.py production_status 2>&1)" && status=0 || status=$?
  if [ "$status" = 0 ]; then
    report status ok "production_status passes"
  else
    failing="$(printf '%s\n' "$output" | grep -E 'FAIL$' | grep -v '^Overall' | sed -E 's/ +FAIL$//' | tr '\n' ';' | cut -c1-300)"
    report status WARN "production_status exit $status: ${failing:-see the command output}. docs/production-readiness.md, section 4."
  fi
}

#: Error lines of one service in the last 24 hours: from the kept logs
#: (`collect_logs`; they survive a release), or — before the first collection —
#: from the container's own log, which starts at its last recreate.
errors_in_24h() {
  local service="$1" cutoff files
  files="$(find "$MONITOR_APP_LOG_DIR" -maxdepth 1 -type f -name "$service-*.log" -mtime -2 2>/dev/null | sort || true)"
  if [ -n "$files" ]; then
    cutoff="$(date -u -d '-24 hours' +%FT%T)"
    # shellcheck disable=SC2086 # the file names are ours, without spaces
    awk -v cutoff="$cutoff" '$1 >= cutoff && ($0 ~ / (ERROR|CRITICAL) / || $2 == "Traceback") { n++ } END { print n + 0 }' $files
    return 0
  fi
  docker logs --since 24h "$(container_name "$service")" 2>&1 | grep -cE ' (ERROR|CRITICAL) |^Traceback' || true
}

check_errors() {
  local service count summary="" worst=0
  for service in web intake-reader searchindex intake-sweeper notifications; do
    count="$(errors_in_24h "$service")"
    count="${count:-0}"
    summary="$summary $service=$count"
    [ "$count" -gt "$worst" ] && worst="$count"
  done
  if [ "$worst" -gt "$MONITOR_ERRORS_WARN" ]; then
    report errors WARN "log error lines in the last 24h:$summary (limit $MONITOR_ERRORS_WARN). Counts only; read $MONITOR_APP_LOG_DIR on the host."
  else
    report errors ok "log error lines in the last 24h:$summary"
  fi
}

check_auth() {
  local output status line gate_failed=0 locked_out=0 step_up_failed=0 refresh_reuse=0 suspended=0 emergency=0 commit_refused=0 problems=""
  output="$(juristid_compose exec -T web python manage.py audit_failures --since 24h --format monitor 2>&1)" && status=0 || status=$?
  if [ "$status" != 0 ]; then
    report auth WARN "audit_failures could not run (exit $status)."
    return 0
  fi
  while IFS='=' read -r key value; do
    case "$key" in
      gate_failed) gate_failed="$value" ;;
      gate_locked_out) locked_out="$value" ;;
      admin_step_up_failed) step_up_failed="$value" ;;
      mcp_refresh_reuse) refresh_reuse="$value" ;;
      mcp_suspended) suspended="$value" ;;
      emergency_disabled) emergency="$value" ;;
      write_commit_refused) commit_refused="$value" ;;
    esac
  done <<<"$output"
  [ "$gate_failed" -ge "$MONITOR_GATE_FAIL_WARN" ] && problems="$problems gate failures $gate_failed;"
  [ "$locked_out" -ge "$MONITOR_LOCKED_OUT_WARN" ] && problems="$problems gate lockouts $locked_out;"
  [ "$step_up_failed" -ge "$MONITOR_STEP_UP_FAIL_WARN" ] && problems="$problems administrator step-up failures $step_up_failed;"
  [ "$refresh_reuse" -gt 0 ] && problems="$problems MCP refresh-token reuse $refresh_reuse;"
  [ "$suspended" -gt 0 ] && problems="$problems MCP grants suspended $suspended;"
  [ "$emergency" -gt 0 ] && problems="$problems emergency account disablements $emergency;"
  [ "$commit_refused" -gt 0 ] && problems="$problems write commits refused $commit_refused;"
  if [ -n "$problems" ]; then
    report auth WARN "last 24h:$problems docs/incident-response.md scenarios 1 and 5."
  else
    report auth ok "last 24h: gate failures $gate_failed, lockouts $locked_out, step-up failures $step_up_failed"
  fi
}

check_appdata_backup() {
  [ -n "$MONITOR_APPDATA_BACKUP_DIR" ] || return 0
  local newest
  newest="$(find "$MONITOR_APPDATA_BACKUP_DIR" -maxdepth 1 -mindepth 1 -type d -name 'ab_*' 2>/dev/null | sort | tail -n 1 || true)"
  [ -n "$newest" ] || return 0
  case "$newest" in
    *-failed) report appdata_backup WARN "the last Appdata Backup run is flagged failed ($(basename -- "$newest")); its retention cleanup did not run." ;;
    *) report appdata_backup ok "last Appdata Backup run $(basename -- "$newest")" ;;
  esac
}

# -- weekly --------------------------------------------------------------------

check_evidence() {
  local output status
  output="$(juristid_compose exec -T web python manage.py check_evidence_integrity 2>&1)" && status=0 || status=$?
  if [ "$status" = 0 ]; then
    report evidence ok "check_evidence_integrity passes"
  else
    report evidence WARN "check_evidence_integrity exit $status: $(printf '%s\n' "$output" | tail -n 1 | cut -c1-200). RECOVERY.md Scenario C."
  fi
}

check_newest_set() {
  local newest status
  newest="$(find "$MONITOR_BACKUP_ROOT/sets" -maxdepth 1 -mindepth 1 -type d -name '[0-9]*Z' 2>/dev/null | sort | tail -n 1 || true)"
  [ -n "$newest" ] || { report verify_newest WARN "no backup set to verify."; return 0; }
  if bash "$SCRIPT_DIR/juristid-verify-backup.sh" --project "$MONITOR_PROJECT" --compose-file "$MONITOR_COMPOSE_FILE" --set "$newest" --backup-root "$MONITOR_BACKUP_ROOT" --level 2 >/dev/null 2>&1; then
    report verify_newest ok "newest set $(basename -- "$newest") passes level 2"
  else
    status=$?
    report verify_newest WARN "newest set $(basename -- "$newest") fails level-2 verification (exit $status)."
  fi
}

digest() {
  local sets evidence pool
  sets="$(find "$MONITOR_BACKUP_ROOT/sets" -maxdepth 1 -mindepth 1 -type d -name '[0-9]*Z' 2>/dev/null | wc -l | tr -d ' ')"
  evidence="$(du -sh "$MONITOR_DATA_ROOT/evidence" 2>/dev/null | cut -f1 || printf '?')"
  pool="$(du -sh "$MONITOR_BACKUP_ROOT" 2>/dev/null | cut -f1 || printf '?')"
  inform digest "weekly: $sets backup sets, backup root $pool, evidence $evidence, free $(free_gib_at "$MONITOR_DATA_ROOT") GiB."
}

# -- run -----------------------------------------------------------------------

note "Juristid monitor, tier $TIER, $(date -u +%FT%TZ)"
case "$TIER" in
  fast)
    check_containers
    check_healthz
    check_database
    check_space
    collect_logs
    heartbeat
    ;;
  logs)
    # A release runs this before it recreates the containers (QA26-OPS-04).
    collect_logs
    ;;
  daily)
    check_backup_age
    check_partials
    check_dump_age
    check_lovable
    check_nightly
    check_status
    check_errors
    check_auth
    check_appdata_backup
    ;;
  weekly)
    check_evidence
    check_newest_set
    digest
    ;;
esac
# Every finished run leaves its time: the host watchdog reads `last-run-fast`
# and alerts when the monitor itself has stopped (quality audit QA26-OPS-05).
[ "$DRY_RUN" = 1 ] || date -u +%FT%TZ > "$STATE_DIR/last-run-$TIER"
rm -f -- "$STATE_DIR/monitor-cannot-run"
exit 0
