#!/usr/bin/env bash
# The coordinated nightly cycle on the Unraid host (docs/adr/0229).
#
#   juristid-nightly-cycle.sh              # from cron: 0 21,22 * * * (UTC)
#   juristid-nightly-cycle.sh --plan [--at 2026-07-01T21:00:00Z] [--with-riigikogu]
#                                          # what a firing would do; writes nothing
#   juristid-nightly-cycle.sh --manual     # an authorised recovery run, now
#
# Usage: juristid-nightly-cycle.sh [--plan [--at ISO-UTC]] [--manual] [--with-riigikogu]
#
# One cycle a night, starting at 00:00 Europe/Tallinn. The host keeps UTC and has
# no Tallinn zone, so cron fires at 21:00 AND 22:00 UTC and this script decides
# which firing is 00:00 in Tallinn (lib.sh `tallinn_clock`, the EU rule). The
# other firing exits at once and leaves no trace.
#
# The steps are the jobs cron ran one by one before, unchanged (ADR 0229 §2):
#
#   lovable            scripts/deploy/juristid-lovable-sync.sh (its own gates: the
#                      runner's flock, the release lock, web healthy, pause file,
#                      root-only credentials, `lovable_sync due`, and in APPLY a
#                      fresh backup set verified to level 2 or nothing is written)
#   register-watch     manage.py watch_registers   (its own advisory lock)
#   riigikogu          manage.py watch_riigikogu   (PR #723; only with
#                      --with-riigikogu, once that code is on main)
#   register-sweep     manage.py sweep_registers   (its own advisory lock)
#   government-agenda  manage.py watch_government_agenda --outcome-json
#
# Order (ADR 0229 §3): lovable first and alone; then the registers lane
# (register-watch → riigikogu → register-sweep, in order) beside the agenda lane.
# `set -e` guards only the setup: every step runs as `run_step … || true`, its exit
# code is captured, and one failing step never stops another. One hard need:
# register-sweep needs register-watch to have finished (SUCCEEDED,
# SUCCEEDED_NO_NEW or PARTIAL), else it would offer drafts a Teema already
# follows as new; every other order is soft.
#
# An exit code is not an outcome (ADR 0229 §4): after each step the cycle asks
# `manage.py nightly_outcome`, which reads the step's own run record (and, for
# the agenda, its OUTCOME-JSON line), and records SUCCEEDED, SUCCEEDED_NO_NEW,
# PARTIAL, SKIPPED_DELIBERATE, SKIPPED_UNEXPECTED, FAILED or INTERRUPTED.
#
# Locks (ADR 0229 §5), in this order and no other: the nightly cycle's lock
# (lib.sh `take_cycle_lock`, flock on fd 7, held from the start to the end of the
# cycle and inherited by every step it starts — a release or a restore takes the
# same lock first and refuses while it is held, and the cycle refuses to start
# under them), then the Lovable runner's flock (waited for before the lovable
# step, so an administrator's on-demand run still going is finished first and
# never runs beside the night's sync; then kept, and handed to the sync script
# on fd 5 as JURISTID_SYNC_LOCK_FD, which the script proves with lib.sh
# `lock_fd_proven` before it relies on it — no gap between the two in which an
# on-demand firing could take it), then the release lock directory (waited
# for before every step, for a release kit that does not take the cycle lock yet),
# then the steps' own locks. The 5-minute on-demand processor takes only the
# runner's flock and the release lock: a request that arrives during the night
# waits in the database (PENDING) and is served by the next firing that finds the
# runner free — never beside the night's sync, never dropped (a request nobody
# claims for two hours is marked EXPIRED, by name, never deleted).
#
# State, per Tallinn night D (ADR 0229 §6), in $NIGHTLY_DIR/state:
#   D.started    written once the cycle holds its lock, before any step
#   D.outcomes   one line per step as it ends (how far an interrupted night got)
#   D.completed  the night's summary, written last
#   D.refused    the cycle could not take its lock (a release or a restore held it)
#   D.recovered  an authorised --manual run's summary for night D
# A night with D.started is never started again by cron: a cycle that died
# halfway is reported by the daily monitor (juristid-monitor.sh) and recovered
# by a person (--manual), never re-run blindly. last-cycle.txt is the newest summary.
#
# Pause the whole cycle: touch $NIGHTLY_DIR/PAUSED. One step: touch
# $NIGHTLY_DIR/PAUSED-<step>. Both are deliberate (SKIPPED_DELIBERATE).
#
# Test seams (tests/test_nightly_cycle_script.py runs this against a fake docker
# and flock): JURISTID_NIGHTLY_APPDATA, JURISTID_NIGHTLY_NOW (epoch seconds),
# JURISTID_CYCLE_LOCK, JURISTID_SYNC_LOCK, JURISTID_HOST_LOCK,
# JURISTID_NIGHTLY_LOCK_WAIT_SECONDS and JURISTID_NIGHTLY_LOCK_RETRY_SECONDS.
# Cron sets none of them. Never `set -x`.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

APPDATA=${JURISTID_NIGHTLY_APPDATA:-/mnt/user/appdata/juristid-main}
REPO=$APPDATA/repo
NIGHTLY_DIR=$APPDATA/nightly
LOG_DIR=$NIGHTLY_DIR/logs
STATE_DIR=$NIGHTLY_DIR/state
RETENTION_DAYS=30
WATCH_ENV=$APPDATA/config/register-watch.env
SYNC_PAUSE=$APPDATA/l100/sync/PAUSED
WEB=juristid-main-web
SYNC_LOCK=${JURISTID_SYNC_LOCK:-/tmp/juristid-lovable-sync.lock}
HOST_LOCK=${JURISTID_HOST_LOCK:-/tmp/juristid-host-lock}
LOCK_WAIT=${JURISTID_NIGHTLY_LOCK_WAIT_SECONDS:-1200}
LOCK_RETRY=${JURISTID_NIGHTLY_LOCK_RETRY_SECONDS:-30}

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
NIGHT=$TALLINN_DATE

SYNC_SUFFIX=""
RUN_NAME=$NIGHT
if [ "$MANUAL" = 1 ]; then
  SYNC_SUFFIX=" --manual"
  RUN_NAME="manual-$(date -u -d "@$NOW" +%Y%m%dT%H%M%SZ)"
fi

# name|lane|hard need|soft order|what it runs (for the plan)
STEPS=(
  "lovable|first|-|-|bash $REPO/scripts/deploy/juristid-lovable-sync.sh$SYNC_SUFFIX"
  "register-watch|registers|-|after lovable|docker exec --env-file $WATCH_ENV $WEB python manage.py watch_registers >> $APPDATA/register-watch.log"
  "riigikogu|registers|-|after register-watch|docker exec --env-file $WATCH_ENV $WEB python manage.py watch_riigikogu >> $APPDATA/riigikogu.log"
  "register-sweep|registers|register-watch|after riigikogu|docker exec --env-file $WATCH_ENV $WEB python manage.py sweep_registers >> $APPDATA/register-sweep.log"
  "government-agenda|agenda|-|after lovable|docker exec --env-file $WATCH_ENV $WEB python manage.py watch_government_agenda --outcome-json >> $APPDATA/government-agenda.log"
)

# The reason a firing does nothing, or "" when it is the night's cycle.
not_due_reason() {
  if [ "$MANUAL" = 1 ]; then return 0; fi
  if [ "${TALLINN_TIME%%:*}" != 00 ]; then echo "NOT_MIDNIGHT_IN_TALLINN"; return 0; fi
  if [ -e "$STATE_DIR/$NIGHT.started" ] || [ -e "$STATE_DIR/$NIGHT.completed" ]; then
    echo "ALREADY_STARTED"
    return 0
  fi
}

if [ "$PLAN" = 1 ]; then
  reason="$(not_due_reason)"
  echo "Nightly cycle plan (docs/adr/0229) — writes nothing, runs nothing."
  echo "UTC $(date -u -d "@$NOW" +%FT%TZ) = Tallinn $TALLINN_DATE $TALLINN_TIME (UTC+$TALLINN_OFFSET)"
  if [ -n "$reason" ]; then echo "Decision: NOT_DUE $reason (this firing exits at once)"; else echo "Decision: DUE"; fi
  if [ -e "$NIGHTLY_DIR/PAUSED" ]; then echo "PAUSED: $NIGHTLY_DIR/PAUSED is present; nothing would run"; fi
  echo "Locks: $JURISTID_CYCLE_LOCK (whole cycle) > $SYNC_LOCK (lovable) > $HOST_LOCK (each step)"
  echo "Steps (lane | hard need | order | command):"
  for entry in "${STEPS[@]}"; do
    IFS='|' read -r name lane need order command <<<"$entry"
    note=""
    if [ "$name" = riigikogu ] && [ "$WITH_RIIGIKOGU" = 0 ]; then note="  [SKIPPED_DELIBERATE NOT_INSTALLED: needs PR #723 and --with-riigikogu]"; fi
    if [ -e "$NIGHTLY_DIR/PAUSED-$name" ]; then note="  [SKIPPED_DELIBERATE PAUSED]"; fi
    printf '  %-18s %-10s needs %-15s %-21s %s%s\n' "$name" "$lane" "$need" "$order" "$command" "$note"
  done
  echo "Lanes after lovable run side by side: registers (in order) || agenda."
  exit 0
fi

REASON="$(not_due_reason)"
if [ -n "$REASON" ]; then exit 0; fi

mkdir -p "$LOG_DIR" "$STATE_DIR"
chmod 0750 "$NIGHTLY_DIR" "$LOG_DIR" "$STATE_DIR" 2>/dev/null || true

# The cycle's lock, for the whole cycle (fd 7, inherited by every step).
lock_rc=0
take_cycle_lock "nightly-cycle night=$RUN_NAME" || lock_rc=$?
if [ "$lock_rc" != 0 ]; then
  holder="$(cycle_lock_owner)"
  case "$holder" in
    owner=nightly-cycle*) exit 0 ;; # a duplicate firing: the running cycle owns the night
  esac
  if [ "$lock_rc" = 1 ]; then why="held by: $holder"; else why="flock or $JURISTID_CYCLE_LOCK unavailable"; fi
  printf 'refused %s %s\n' "$(date -u +%FT%TZ)" "$why" >"$STATE_DIR/$RUN_NAME.refused"
  exit 1
fi

LOG="$LOG_DIR/$(date -u -d "@$NOW" +%Y%m%dT%H%M%SZ)$([ "$MANUAL" = 1 ] && echo -manual || true).log"
STATUS_DIR="$(mktemp -d)"
trap 'rm -rf -- "$STATUS_DIR"' EXIT
say() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >>"$LOG"; }

START=$(date -u +%s)
SINCE=$(date -u -d "@$START" +%FT%TZ)
say "START nightly cycle $RUN_NAME, Tallinn $TALLINN_DATE $TALLINN_TIME (UTC+$TALLINN_OFFSET)"
if [ -e "$NIGHTLY_DIR/PAUSED" ]; then
  say "SKIP paused on the host ($NIGHTLY_DIR/PAUSED)"
  printf 'paused %s\n' "$(date -u +%FT%TZ)" >"$STATE_DIR/$RUN_NAME.completed"
  exit 0
fi
printf 'started=%s pid=%s\n' "$SINCE" "$$" >"$STATE_DIR/$RUN_NAME.started"
: >"$STATE_DIR/$RUN_NAME.outcomes"

# record NAME OUTCOME RC SECONDS REASON
record() {
  local line
  line="$(printf '%-18s %-19s rc=%s %ss %s' "$1" "$2" "$3" "$4" "${5:--}")"
  printf '%s\n' "$line" >"$STATUS_DIR/$1"
  printf '%s\n' "$line" >>"$STATE_DIR/$RUN_NAME.outcomes"
  say "STEP $line"
}

outcome_of() { awk '{print $2}' "$STATUS_DIR/$1" 2>/dev/null || echo NONE; }

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

# classify NAME RC [REPORTED] — `nightly_outcome` reads the step's own record.
classify() {
  local name=$1 rc=$2 reported=${3:-} out line args
  args=(--step "$name" --since "$SINCE" --rc "$rc")
  if [ -n "$reported" ]; then args+=(--reported "$reported"); fi
  # A recovery's Lovable run is recorded MANUAL, not SCHEDULED.
  if [ "$MANUAL" = 1 ] && [ "$name" = lovable ]; then args+=(--manual); fi
  out="$(docker exec --env-file "$WATCH_ENV" "$WEB" python manage.py nightly_outcome "${args[@]}" </dev/null 2>>"$LOG")" || out=""
  line="$(printf '%s\n' "$out" | grep '^NIGHTLY-OUTCOME ' | tail -n 1 || true)"
  if [ -z "$line" ]; then
    echo "FAILED OUTCOME_UNKNOWN (nightly_outcome gave no answer; step exit $rc)"
    return 0
  fi
  printf '%s %s\n' \
    "$(printf '%s' "$line" | sed -nE 's/.*"outcome": "([A-Z_]+)".*/\1/p')" \
    "$(printf '%s' "$line" | sed -nE 's/.*"reason": "([^"]*)".*/\1/p')"
}

run_manage() {
  local command=$1 log=$2
  shift 2
  docker exec --env-file "$WATCH_ENV" "$WEB" python manage.py "$command" "$@" \
    </dev/null >>"$APPDATA/$log" 2>&1
}

# run_step NAME NEED — the gates every step shares, the step, its outcome.
run_step() {
  local name=$1 need=$2 holder rc started verdict reported="" tmp need_outcome sync_args
  if [ -e "$NIGHTLY_DIR/PAUSED-$name" ]; then
    record "$name" SKIPPED_DELIBERATE - 0 "PAUSED ($NIGHTLY_DIR/PAUSED-$name)"
    return 0
  fi
  if [ "$name" = riigikogu ] && [ "$WITH_RIIGIKOGU" = 0 ]; then
    record "$name" SKIPPED_DELIBERATE - 0 "NOT_INSTALLED (PR #723; --with-riigikogu once it is on main)"
    return 0
  fi
  if [ "$name" = lovable ] && [ -e "$SYNC_PAUSE" ]; then
    record "$name" SKIPPED_DELIBERATE - 0 "PAUSED on the host ($SYNC_PAUSE)"
    return 0
  fi
  if [ "$need" != - ]; then
    need_outcome="$(outcome_of "$need")"
    case "$need_outcome" in
      SUCCEEDED | SUCCEEDED_NO_NEW | PARTIAL) ;;
      SKIPPED_DELIBERATE)
        record "$name" SKIPPED_DELIBERATE - 0 "NEEDS $need, which was skipped on purpose"
        return 0 ;;
      *)
        record "$name" SKIPPED_UNEXPECTED - 0 "NEEDS $need, which was $need_outcome"
        return 0 ;;
    esac
  fi
  holder="$(host_lock_holder)"
  if [ -n "$holder" ]; then
    record "$name" SKIPPED_UNEXPECTED - 0 "HOST_LOCK held after ${LOCK_WAIT}s by: $holder"
    return 0
  fi
  say "STEP $name start"
  started=$(date -u +%s)
  rc=0
  case "$name" in
    lovable)
      # An on-demand run still going holds the runner's flock: the night waits
      # for it (bounded), so the two never run side by side. The night then
      # KEEPS the lock and hands it to the sync script on fd 5
      # (JURISTID_SYNC_LOCK_FD; the script proves it with lib.sh
      # `lock_fd_proven`), so no on-demand firing can take it between the two
      # and leave the night «another synchronisation is running». A firing in
      # that time finds the lock held and leaves its request PENDING for the
      # next one. Lock order: cycle lock → (release-lock wait) → runner flock →
      # release lock; the second wait below is for a later lock while holding
      # an earlier one, never the reverse.
      exec 5<>"$SYNC_LOCK"
      if ! flock -w "$LOCK_WAIT" 5; then
        exec 5>&-
        record "$name" SKIPPED_UNEXPECTED - 0 "SYNC_BUSY the Lovable runner's lock was held for ${LOCK_WAIT}s"
        return 0
      fi
      # The runner's lock may have taken up to LOCK_WAIT: look at the release
      # lock again, so the sync does not meet a release that started meanwhile.
      holder="$(host_lock_holder)"
      if [ -n "$holder" ]; then
        flock -u 5
        exec 5>&-
        record "$name" SKIPPED_UNEXPECTED - 0 "HOST_LOCK held after ${LOCK_WAIT}s by: $holder"
        return 0
      fi
      sync_args=()
      if [ "$MANUAL" = 1 ]; then sync_args=(--manual); fi
      JURISTID_SYNC_LOCK_FD=5 bash "$REPO/scripts/deploy/juristid-lovable-sync.sh" "${sync_args[@]}" \
        </dev/null >>"$LOG" 2>&1 || rc=$?
      # Released only now: the next on-demand firing serves what waited.
      flock -u 5 || true
      exec 5>&-
      ;;
    register-watch) run_manage watch_registers register-watch.log || rc=$? ;;
    riigikogu) run_manage watch_riigikogu riigikogu.log || rc=$? ;;
    register-sweep) run_manage sweep_registers register-sweep.log || rc=$? ;;
    government-agenda)
      tmp="$STATUS_DIR/agenda.out"
      docker exec --env-file "$WATCH_ENV" "$WEB" python manage.py watch_government_agenda \
        --outcome-json </dev/null >"$tmp" 2>&1 || rc=$?
      cat "$tmp" >>"$APPDATA/government-agenda.log"
      reported="$(grep '^OUTCOME-JSON ' "$tmp" | tail -n 1 | cut -d' ' -f2- || true)"
      ;;
  esac
  verdict="$(classify "$name" "$rc" "$reported")"
  record "$name" "${verdict%% *}" "$rc" "$(($(date -u +%s) - started))" "${verdict#* }"
}

# Every step in an `||` context: `set -e` never ends the cycle inside one.
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
summary="cycle $RUN_NAME (Tallinn night $NIGHT), started $SINCE, ended $(date -u +%FT%TZ)"
for entry in "${STEPS[@]}"; do
  name="${entry%%|*}"
  if [ -f "$STATUS_DIR/$name" ]; then
    line="$(cat "$STATUS_DIR/$name")"
  else
    # A lane that died before its step could report: the step was interrupted.
    line="$(printf '%-18s %-19s rc=%s %ss %s' "$name" INTERRUPTED - 0 "did not report")"
    printf '%s\n' "$line" >>"$STATE_DIR/$RUN_NAME.outcomes"
  fi
  summary+=$'\n'"$line"
  case "$(printf '%s' "$line" | awk '{print $2}')" in
    PARTIAL | SKIPPED_UNEXPECTED | FAILED | INTERRUPTED) problems+=("$name") ;;
  esac
done
if [ "${#problems[@]}" -gt 0 ]; then
  summary+=$'\n'"verdict problems: ${problems[*]}"
  rc=1
else
  summary+=$'\n'"verdict ok"
  rc=0
fi
printf '%s\n' "$summary" >"$STATE_DIR/$RUN_NAME.completed"
if [ "$MANUAL" = 1 ]; then
  # An authorised recovery of tonight's (or the last) night: the daily monitor
  # reads it in place of an interrupted or failed scheduled cycle.
  printf '%s\n' "$summary" >"$STATE_DIR/$NIGHT.recovered"
fi
printf '%s\n' "$summary" >"$NIGHTLY_DIR/last-cycle.txt.tmp" && mv -f "$NIGHTLY_DIR/last-cycle.txt.tmp" "$NIGHTLY_DIR/last-cycle.txt"
printf '%s\n' "$summary" >>"$LOG"
say "END rc=$rc${problems:+ not ok: ${problems[*]}}"
find "$LOG_DIR" -maxdepth 1 -type f -name '*.log' -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true
find "$STATE_DIR" -maxdepth 1 -type f -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true
exit "$rc"
