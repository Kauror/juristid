#!/usr/bin/env bash
# The coordinated nightly cycle on the Unraid host (docs/adr/0227).
#
#   juristid-nightly-cycle.sh              # from cron: 0 21,22 * * * (UTC)
#   juristid-nightly-cycle.sh --plan [--at 2026-07-01T21:00:00Z] [--with-riigikogu]
#                                          # what a firing would do; writes nothing
#   juristid-nightly-cycle.sh --manual     # now, whatever the hour, every lock honoured
#
# Usage: juristid-nightly-cycle.sh [--plan [--at ISO-UTC]] [--manual] [--with-riigikogu]
#
# One cycle a night, starting at 00:00 Europe/Tallinn. The host keeps UTC and has
# no Tallinn zone, so cron fires at 21:00 AND 22:00 UTC and this script decides
# which firing is 00:00 in Tallinn: Estonia follows the EU rule (UTC+3 from 01:00
# UTC on the last Sunday of March to 01:00 UTC on the last Sunday of October,
# UTC+2 otherwise), computed here with `date -u` only. Exactly one firing a night
# is midnight; the other exits at once and leaves no trace. A marker per Tallinn
# date makes a second start of the same night refuse (ALREADY_RAN).
#
# The steps reuse what cron ran one by one before (ADR 0227 §2), unchanged:
#
#   lovable            scripts/deploy/juristid-lovable-sync.sh (its own gates: one
#                      runner, the release lock, web healthy, pause file, root-only
#                      credentials, `lovable_sync due`, and in APPLY a fresh backup
#                      set verified to level 2 or nothing is written)
#   register-watch     manage.py watch_registers   (ADR 0217; its own advisory lock)
#   riigikogu          manage.py watch_riigikogu   (the Riigikogu programme, PR #723;
#                      only with --with-riigikogu, once that code is on main)
#   register-sweep     manage.py sweep_registers   (ADR 0218; its own advisory lock)
#   government-agenda  manage.py watch_government_agenda (ADR 0219; at most
#                      three model attempts per agenda, its own rule)
#
# Order (ADR 0227 §3): lovable first, alone — new Teemad reach Juristid before
# anything looks for documents on them. Then two lanes side by side: the registers
# lane (register-watch → riigikogu → register-sweep, one after another, because
# they share the Riigikogu API's rate limit and the sweep leaves out what the watch
# already follows) and the agenda lane (valitsus.ee only). A step's failure is its
# own: `set -e` guards only the setup — every step runs as `run_step … || true`,
# where it does not apply, and its exit code is captured and recorded — so one
# failing step never stops the cycle. Only a hard need skips a
# step — register-sweep needs register-watch to have finished, else it would offer
# drafts a Teema already follows as new topics — and the skip names its reason.
# Every other order is soft: a failed Lovable night still lets the watch read the
# Teemad as they stand.
#
# Locks (ADR 0227 §5): one cycle at a time (flock on $CYCLE_LOCK); before the
# lovable step the Lovable runner's own lock is waited for, so an administrator's
# on-demand run that is still going does not make the night SKIP; before every step
# the release lock ($HOST_LOCK, lib.sh's) is waited for, at most $LOCK_WAIT seconds,
# and a step that still finds it held is SKIPPED by the holder's name. The steps'
# own locks (the sync's flock and host lock, the register commands' PostgreSQL
# advisory locks) are theirs and unchanged.
#
# Every step's outcome — OK, FAILED rc=N or SKIPPED <reason> — is written to the
# cycle's log ($NIGHTLY_DIR/logs) and to $NIGHTLY_DIR/last-cycle.txt; a step that
# failed or could not run raises one Unraid notification and the exit status is 1.
# A deliberate pause (PAUSED, PAUSED-<step>) and the Riigikogu step left out are not
# failures. The commands still append to their own logs as before.
#
# Pause the whole cycle: touch $NIGHTLY_DIR/PAUSED. One step: touch
# $NIGHTLY_DIR/PAUSED-<step> (e.g. PAUSED-government-agenda). The Lovable lane's own
# pause file (l100/sync/PAUSED) still pauses that step from inside it.
#
# Test seams (tests/test_nightly_cycle_script.py runs this against a fake docker):
# JURISTID_NIGHTLY_APPDATA, JURISTID_NIGHTLY_NOW (epoch seconds),
# JURISTID_NIGHTLY_LOCK, JURISTID_SYNC_LOCK, JURISTID_HOST_LOCK,
# JURISTID_NIGHTLY_LOCK_WAIT_SECONDS, JURISTID_NIGHTLY_LOCK_RETRY_SECONDS and
# JURISTID_NOTIFY replace the host's values. Cron sets none of them. Sourced (not
# run), the script only defines its functions, so the tests can check the
# Tallinn clock against the zone database. Never `set -x`.
set -euo pipefail

APPDATA=${JURISTID_NIGHTLY_APPDATA:-/mnt/user/appdata/juristid-main}
REPO=$APPDATA/repo
NIGHTLY_DIR=$APPDATA/nightly
LOG_DIR=$NIGHTLY_DIR/logs
STATE_DIR=$NIGHTLY_DIR/state
RETENTION_DAYS=30
WATCH_ENV=$APPDATA/config/register-watch.env
WEB=juristid-main-web
CYCLE_LOCK=${JURISTID_NIGHTLY_LOCK:-/tmp/juristid-nightly-cycle.lock}
SYNC_LOCK=${JURISTID_SYNC_LOCK:-/tmp/juristid-lovable-sync.lock}
HOST_LOCK=${JURISTID_HOST_LOCK:-/tmp/juristid-host-lock}
LOCK_WAIT=${JURISTID_NIGHTLY_LOCK_WAIT_SECONDS:-1200}
LOCK_RETRY=${JURISTID_NIGHTLY_LOCK_RETRY_SECONDS:-30}
NOTIFY=${JURISTID_NOTIFY:-/usr/local/emhttp/webGui/scripts/notify}

# -- the Tallinn clock ---------------------------------------------------------------

# The EU summer-time bounds of a year, as UTC epochs, cached per year.
_DST_YEAR=""
_DST_START=0
_DST_END=0
_dst_bounds() {
  local year=$1 dow
  [ "$year" = "$_DST_YEAR" ] && return 0
  dow=$(date -u -d "$year-03-31" +%u)
  _DST_START=$(date -u -d "$year-03-$((31 - dow % 7)) 01:00:00" +%s)
  dow=$(date -u -d "$year-10-31" +%u)
  _DST_END=$(date -u -d "$year-10-$((31 - dow % 7)) 01:00:00" +%s)
  _DST_YEAR=$year
}

# `tallinn_clock EPOCH` prints «YYYY-MM-DD HH:MM +N»: Tallinn's date, time and
# offset from UTC at that moment.
tallinn_clock() {
  local t=$1 offset=2
  _dst_bounds "$(date -u -d "@$t" +%Y)"
  if [ "$t" -ge "$_DST_START" ] && [ "$t" -lt "$_DST_END" ]; then offset=3; fi
  echo "$(date -u -d "@$((t + offset * 3600))" '+%F %H:%M') +$offset"
}

# Sourced by a test: the functions above and nothing else.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then return 0; fi

usage() {
  echo "Usage: juristid-nightly-cycle.sh [--plan [--at ISO-UTC]] [--manual] [--with-riigikogu]"
}

PLAN=0
MANUAL=0
WITH_RIIGIKOGU=0
AT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --plan) PLAN=1; shift ;;
    --at) AT="${2:-}"; shift 2 || { usage >&2; exit 64; } ;;
    --manual) MANUAL=1; shift ;;
    --with-riigikogu) WITH_RIIGIKOGU=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done
if [ -n "$AT" ] && [ "$PLAN" = 0 ]; then echo "--at is only for --plan" >&2; exit 64; fi

if [ -n "$AT" ]; then
  NOW=$(date -u -d "$AT" +%s 2>/dev/null) || { echo "--at: not a time: $AT" >&2; exit 64; }
else
  NOW=${JURISTID_NIGHTLY_NOW:-$(date -u +%s)}
fi
read -r TALLINN_DATE TALLINN_TIME TALLINN_OFFSET <<<"$(tallinn_clock "$NOW")"

# -- the steps -------------------------------------------------------------------------

SYNC_SUFFIX=""
LOG_SUFFIX=""
if [ "$MANUAL" = 1 ]; then SYNC_SUFFIX=" --manual"; LOG_SUFFIX="-manual"; fi

# name|lane|hard need|soft order|what it runs (for the plan)
STEPS=(
  "lovable|first|-|-|bash $REPO/scripts/deploy/juristid-lovable-sync.sh$SYNC_SUFFIX"
  "register-watch|registers|-|after lovable|docker exec --env-file $WATCH_ENV $WEB python manage.py watch_registers >> $APPDATA/register-watch.log"
  "riigikogu|registers|-|after register-watch|docker exec --env-file $WATCH_ENV $WEB python manage.py watch_riigikogu >> $APPDATA/riigikogu.log"
  "register-sweep|registers|register-watch|after riigikogu|docker exec --env-file $WATCH_ENV $WEB python manage.py sweep_registers >> $APPDATA/register-sweep.log"
  "government-agenda|agenda|-|after lovable|docker exec --env-file $WATCH_ENV $WEB python manage.py watch_government_agenda >> $APPDATA/government-agenda.log"
)

# The reason a firing does nothing, or "" when it is the night's cycle.
not_due_reason() {
  if [ "$MANUAL" = 1 ]; then return 0; fi
  if [ "${TALLINN_TIME%%:*}" != 00 ]; then echo "NOT_MIDNIGHT_IN_TALLINN"; return 0; fi
  if [ -e "$STATE_DIR/$TALLINN_DATE.done" ]; then echo "ALREADY_RAN"; return 0; fi
}

if [ "$PLAN" = 1 ]; then
  reason="$(not_due_reason)"
  echo "Nightly cycle plan (docs/adr/0227) — writes nothing, runs nothing."
  echo "UTC $(date -u -d "@$NOW" +%FT%TZ) = Tallinn $TALLINN_DATE $TALLINN_TIME (UTC+$TALLINN_OFFSET)"
  if [ -n "$reason" ]; then echo "Decision: NOT_DUE $reason (this firing exits at once)"; else echo "Decision: DUE"; fi
  [ -e "$NIGHTLY_DIR/PAUSED" ] && echo "PAUSED: $NIGHTLY_DIR/PAUSED is present; nothing would run"
  echo "Steps (lane | hard need | order | command):"
  for entry in "${STEPS[@]}"; do
    IFS='|' read -r name lane need order command <<<"$entry"
    note=""
    if [ "$name" = riigikogu ] && [ "$WITH_RIIGIKOGU" = 0 ]; then note="  [SKIPPED NOT_INSTALLED: needs PR #723 and --with-riigikogu]"; fi
    [ -e "$NIGHTLY_DIR/PAUSED-$name" ] && note="  [SKIPPED PAUSED]"
    printf '  %-18s %-10s needs %-15s %-21s %s%s\n' "$name" "$lane" "$need" "$order" "$command" "$note"
  done
  echo "Lanes after lovable run side by side: registers (in order) || agenda."
  exit 0
fi

REASON="$(not_due_reason)"
if [ -n "$REASON" ]; then exit 0; fi

# One cycle at a time; a second one leaves no log behind.
exec 8>"$CYCLE_LOCK"
if ! flock -n 8; then exit 0; fi

mkdir -p "$LOG_DIR" "$STATE_DIR"
chmod 0750 "$NIGHTLY_DIR" "$LOG_DIR" "$STATE_DIR" 2>/dev/null || true
LOG="$LOG_DIR/$(date -u -d "@$NOW" +%Y%m%dT%H%M%SZ)$LOG_SUFFIX.log"
STATUS_DIR="$(mktemp -d)"
trap 'rm -rf -- "$STATUS_DIR"' EXIT
say() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >>"$LOG"; }

say "START nightly cycle, Tallinn $TALLINN_DATE $TALLINN_TIME (UTC+$TALLINN_OFFSET)${LOG_SUFFIX:+, manual}"
if [ -e "$NIGHTLY_DIR/PAUSED" ]; then
  say "SKIP paused on the host ($NIGHTLY_DIR/PAUSED)"
  exit 0
fi
# The night is taken before any step runs: a crash halfway is not re-run by the
# other firing (a person reads the log and runs --manual).
if [ "$MANUAL" = 0 ]; then
  printf 'started %s\n' "$(date -u +%FT%TZ)" >"$STATE_DIR/$TALLINN_DATE.done"
fi

# record NAME STATUS RC SECONDS REASON
record() {
  printf '%s %s %s %s\n' "$2" "$3" "$4" "${5:--}" >"$STATUS_DIR/$1"
  case "$2" in
    OK) say "STEP $1 OK (${4}s)" ;;
    FAILED) say "STEP $1 FAILED rc=$3 (${4}s)" ;;
    *) say "STEP $1 $2 $5" ;;
  esac
}

status_of() { cut -d' ' -f1 "$STATUS_DIR/$1" 2>/dev/null || echo NONE; }

# Waits while a release (or any other holder) has the release lock; "" when free.
host_lock_holder() {
  local waited=0
  while [ -d "$HOST_LOCK" ]; do
    if [ "$waited" -ge "$LOCK_WAIT" ]; then
      cat "$HOST_LOCK/owner" 2>/dev/null || echo unknown
      return 0
    fi
    sleep "$LOCK_RETRY"
    waited=$((waited + LOCK_RETRY))
  done
}

run_lovable() {
  local args=()
  if [ "$MANUAL" = 1 ]; then args=(--manual); fi
  bash "$REPO/scripts/deploy/juristid-lovable-sync.sh" "${args[@]}" 8>&- </dev/null >>"$LOG" 2>&1
}

run_manage() {
  local command=$1 log=$2
  docker exec --env-file "$WATCH_ENV" "$WEB" python manage.py "$command" \
    8>&- </dev/null >>"$APPDATA/$log" 2>&1
}

# run_step NAME NEED — the gates every step shares, then the step, then its record.
run_step() {
  local name=$1 need=$2 holder rc started
  if [ -e "$NIGHTLY_DIR/PAUSED-$name" ]; then
    record "$name" SKIPPED 0 0 "PAUSED ($NIGHTLY_DIR/PAUSED-$name)"
    return 0
  fi
  if [ "$name" = riigikogu ] && [ "$WITH_RIIGIKOGU" = 0 ]; then
    record "$name" SKIPPED 0 0 "NOT_INSTALLED (PR #723; --with-riigikogu once it is on main)"
    return 0
  fi
  if [ "$need" != - ] && [ "$(status_of "$need")" != OK ]; then
    record "$name" SKIPPED 0 0 "NEEDS $need, which was $(status_of "$need")"
    return 0
  fi
  holder="$(host_lock_holder)"
  if [ -n "$holder" ]; then
    record "$name" SKIPPED 0 0 "HOST_LOCK held after ${LOCK_WAIT}s by: $holder"
    return 0
  fi
  say "STEP $name start"
  started=$(date -u +%s)
  rc=0
  case "$name" in
    lovable)
      # An on-demand run still going holds the runner's lock; the night waits
      # for it rather than letting the sync script SKIP the whole night.
      exec 7>"$SYNC_LOCK"
      if ! flock -w "$LOCK_WAIT" 7; then
        exec 7>&-
        record "$name" SKIPPED 0 0 "SYNC_BUSY another synchronisation held $SYNC_LOCK for ${LOCK_WAIT}s"
        return 0
      fi
      exec 7>&-
      run_lovable || rc=$?
      ;;
    register-watch) run_manage watch_registers register-watch.log || rc=$? ;;
    riigikogu) run_manage watch_riigikogu riigikogu.log || rc=$? ;;
    register-sweep) run_manage sweep_registers register-sweep.log || rc=$? ;;
    government-agenda) run_manage watch_government_agenda government-agenda.log || rc=$? ;;
  esac
  if [ "$rc" = 0 ]; then
    record "$name" OK 0 "$(($(date -u +%s) - started))"
  else
    record "$name" FAILED "$rc" "$(($(date -u +%s) - started))"
  fi
}

# Every step in an `||` context: `set -e` never ends the cycle inside one, and a
# step that broke before it could record itself is reported «did not report».
run_step lovable - || true

# The two lanes, side by side; each step inside a lane in order.
(
  run_step register-watch - || true
  run_step riigikogu - || true
  run_step register-sweep register-watch || true
) &
registers_lane=$!
(run_step government-agenda - || true) &
agenda_lane=$!
wait "$registers_lane" || true
wait "$agenda_lane" || true

# -- the night's summary ---------------------------------------------------------------

problems=()
summary="cycle $TALLINN_DATE (Tallinn), started $(date -u -d "@$NOW" +%FT%TZ), ended $(date -u +%FT%TZ)"
for entry in "${STEPS[@]}"; do
  name="${entry%%|*}"
  line="$(cat "$STATUS_DIR/$name" 2>/dev/null || echo 'NONE - - did not report')"
  read -r status rc seconds reason <<<"$line"
  summary+=$'\n'"$(printf '%-18s %-8s rc=%s %ss %s' "$name" "$status" "$rc" "$seconds" "$reason")"
  case "$status" in
    OK) ;;
    SKIPPED)
      case "$reason" in PAUSED* | NOT_INSTALLED*) ;; *) problems+=("$name") ;; esac ;;
    *) problems+=("$name") ;;
  esac
done
printf '%s\n' "$summary" >"$NIGHTLY_DIR/last-cycle.txt.tmp" && mv -f "$NIGHTLY_DIR/last-cycle.txt.tmp" "$NIGHTLY_DIR/last-cycle.txt"
printf '%s\n' "$summary" >>"$LOG"

rc=0
if [ "${#problems[@]}" -gt 0 ]; then
  rc=1
  say "END rc=1 not ok: ${problems[*]}"
  # Subject and body carry step names and a path, never a value from any file.
  if [ -x "$NOTIFY" ]; then
    "$NOTIFY" -e "Juristid nightly cycle" -s "nightly cycle: ${problems[*]} not ok" \
      -d "Read $LOG and $NIGHTLY_DIR/last-cycle.txt." -i alert >/dev/null 2>&1 || true
  fi
else
  say "END rc=0"
fi
find "$LOG_DIR" -maxdepth 1 -type f -name '*.log' -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true
find "$STATE_DIR" -maxdepth 1 -type f -name '*.done' -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true
exit "$rc"
