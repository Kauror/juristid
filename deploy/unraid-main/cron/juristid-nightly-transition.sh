#!/usr/bin/env bash
#
# The cron change of docs/adr/0229 §7, and its rollback (§8), as one reviewed,
# recoverable script. NOT RUN BY ANYTHING: an authorised deployment runs it on
# the host, then `update_cron`. tests/test_nightly_cycle_transition.py runs it on
# a copy of the host's cron files of 25.11.2026
# (tests/fixtures/nightly_cycle/dynamix/), with a failure injected after every
# filesystem operation.
#
#   juristid-nightly-transition.sh --cron-dir /boot/config/plugins/dynamix \
#       --nightly-cron /mnt/user/appdata/juristid-main/repo/deploy/unraid-main/cron/juristid-nightly.cron \
#       --apply | --rollback [--force-recover]
#
# Every run first writes, then advances, a journal in --cron-dir,
# juristid-nightly-transition.journal (update_cron reads only *.cron). It names
# the step reached and the sha256 and size of every file: the originals
# (ORIG_*) and the files as --apply installs them (NEW_*). Steps, in order:
#
#   PREPARING      the originals' fingerprints are recorded; nothing live changed.
#                  Then: byte-for-byte copies juristid-lovable-sync.cron.pre-0229
#                  and juristid-register-watch.cron.pre-0229 (cp -p), the staged
#                  new files *.new-0229, and a check that each copy equals its
#                  live file and the journal by sha256 and size.
#   BACKED_UP      the complete backup set is verified. Only now does a live
#                  file change: juristid-lovable-sync.cron loses the nightly
#                  `15 21,22 … juristid-lovable-sync.sh` line (the on-demand
#                  `2-59/5` line stays) by an atomic rename of the staged file;
#   SYNC_REPLACED  juristid-register-watch.cron is removed (the 03:30 watch, the
#                  04:20 sweep and the agenda lines; its copy is the backup);
#   WATCH_REMOVED  juristid-nightly.cron, the one cycle line, is installed by an
#                  atomic rename of the staged copy;
#   APPLIED        done. juristid.cron (backup, monitors) is never touched.
#   ROLLING_BACK   --rollback has started.
#
# --apply on a fresh directory refuses, changing nothing, when a file it changes
# is missing or does not hold the line it removes, when a copy or the cycle file
# already exists, or when the result would not hold exactly one cycle line.
# --apply after an interruption (journal PREPARING … WATCH_REMOVED) checks that
# every file is in a state the journal explains and finishes the remaining
# steps; otherwise it refuses and says why. After APPLIED it refuses.
#
# --rollback compares every live file with the journal: the sync file must be
# its original or as installed, the watch file original or absent, the cycle
# file absent or as installed. A file in any other state was edited after the
# apply, and the rollback refuses, naming the file and showing the change,
# unless --force-recover is given: then the edited file is kept beside it as
# <name>.edited-<UTC time> and the original is restored. A file is restored
# only from a copy whose sha256 and size equal the journal's. The originals come
# back with their bytes, mode and mtime (cp -p, then rename); the copies, staged
# files and the journal are removed last, so an interrupted rollback re-runs.
#
# Exit codes: 0 done; 1 REFUSED (nothing changed by this run); 2 a check after a
# change failed (the journal names the state; the message says what to run);
# 64 usage. An interruption prints INTERRUPTED with the step reached.
# Either way the next step is `update_cron` (and `grep juristid /etc/cron.d/root`).
#
# JURISTID_TRANSITION_FAIL_AFTER=N (tests only; nothing sets it) exits 99 right
# after the N-th filesystem operation of the run, to prove the recovery.

set -euo pipefail

CRON_DIR=""
NIGHTLY_CRON=""
ACTION=""
FORCE_RECOVER=0
SN=0
SUFFIX=".pre-0229"
STAGED=".new-0229"
RESTORING=".restore-0229"
SYNC=juristid-lovable-sync.cron
WATCH=juristid-register-watch.cron
CYCLE=juristid-nightly.cron
JOURNAL_NAME=juristid-nightly-transition.journal
NIGHT_LINE_PATTERN='^15 21,22 \* \* \* bash .*/scripts/deploy/juristid-lovable-sync\.sh &> /dev/null$'
SYNC_NOTE="# 2026-11 (docs/adr/0229): the nightly 15 21,22 line moved into juristid-nightly.cron (the cycle)."
FP_RE='^[0-9a-f]{64} [0-9]+$'

usage() {
  echo "Usage: juristid-nightly-transition.sh --cron-dir DIR --nightly-cron FILE --apply|--rollback [--force-recover]"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --cron-dir) CRON_DIR="${2:-}"; shift 2 ;;
    --nightly-cron) NIGHTLY_CRON="${2:-}"; shift 2 ;;
    --apply) ACTION=apply; shift ;;
    --rollback) ACTION=rollback; shift ;;
    --force-recover) FORCE_RECOVER=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done
[ -n "$CRON_DIR" ] && [ -d "$CRON_DIR" ] || { usage >&2; echo "--cron-dir must be a directory" >&2; exit 64; }
[ -n "$ACTION" ] || { usage >&2; echo "--apply or --rollback is required" >&2; exit 64; }
[ "$FORCE_RECOVER" = 0 ] || [ "$ACTION" = rollback ] || { usage >&2; echo "--force-recover belongs to --rollback only" >&2; exit 64; }

D="$CRON_DIR"
J="$D/$JOURNAL_NAME"
CHANGED=0
OPS=0
LAST_OP="(none)"
STEP=""
ORIG_SYNC=""
ORIG_WATCH=""
NEW_SYNC=""
NEW_CYCLE=""

refuse() { echo "REFUSED: $*; nothing changed" >&2; exit 1; }
fail() { echo "ERROR: $*" >&2; exit 2; }

on_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ] && [ "$CHANGED" = 1 ]; then
    if [ "$ACTION" = apply ]; then
      echo "INTERRUPTED: --apply stopped (exit $rc) after operation $OPS ($LAST_OP); the journal $J names the step reached. Re-run --apply with the same arguments to finish, or --rollback to undo." >&2
    else
      echo "INTERRUPTED: --rollback stopped (exit $rc) after operation $OPS ($LAST_OP); the journal $J names the step reached. Re-run --rollback with the same arguments to finish." >&2
    fi
  fi
}
trap on_exit EXIT

# op NAME — record that a filesystem operation happened (and, in tests, stop here).
op() {
  OPS=$((OPS + 1))
  LAST_OP="$1"
  CHANGED=1
  if [ "${JURISTID_TRANSITION_FAIL_AFTER:-}" = "$OPS" ]; then
    echo "INJECTED FAILURE after operation $OPS ($1)" >&2
    exit 99
  fi
}

# fp FILE — "sha256 size", or "absent".
fp() {
  local h s
  if [ -f "$1" ]; then
    h="$(sha256sum < "$1")"
    h="${h%% *}"
    s="$(wc -c < "$1")"
    [[ "$h" =~ ^[0-9a-f]{64}$ ]] && [[ "$s" =~ ^[[:space:]]*[0-9]+[[:space:]]*$ ]] || fail "could not fingerprint $1 (sha256sum/wc failed)"
    echo "$h ${s//[[:space:]]/}"
  else
    echo absent
  fi
}

# step_no STEP — sets SN to the step's place in the order (0: unknown).
step_no() {
  case "$1" in
    PREPARING) SN=1 ;; BACKED_UP) SN=2 ;; SYNC_REPLACED) SN=3 ;;
    WATCH_REMOVED) SN=4 ;; APPLIED) SN=5 ;; ROLLING_BACK) SN=6 ;; *) SN=0 ;;
  esac
}

write_journal() {
  STEP="$1"
  {
    echo "# juristid-nightly-transition.sh journal (docs/adr/0229 §8). Written by the script; never edit it."
    echo "VERSION=1"
    echo "STEP=$STEP"
    echo "UPDATED=$(date -u +%FT%TZ)"
    echo "ORIG_SYNC=$ORIG_SYNC"
    echo "ORIG_WATCH=$ORIG_WATCH"
    echo "NEW_SYNC=$NEW_SYNC"
    echo "NEW_CYCLE=$NEW_CYCLE"
  } > "$J.tmp"
  mv -f -- "$J.tmp" "$J"
  op "journal STEP=$STEP"
}

# advance STEP — write the journal unless it already records STEP or a later one.
advance() {
  local have
  step_no "$STEP"; have=$SN
  step_no "$1"
  [ "$have" -ge "$SN" ] || write_journal "$1"
}

jget() { sed -n "s/^$1=//p" "$J" | head -n 1; }

load_journal() {
  [ "$(jget VERSION)" = 1 ] || refuse "the journal $J is not a version-1 journal of this script; compare the files by hand"
  STEP="$(jget STEP)"
  ORIG_SYNC="$(jget ORIG_SYNC)"
  ORIG_WATCH="$(jget ORIG_WATCH)"
  NEW_SYNC="$(jget NEW_SYNC)"
  NEW_CYCLE="$(jget NEW_CYCLE)"
  step_no "$STEP"
  [ "$SN" -gt 0 ] || refuse "the journal $J names an unknown step '$STEP'"
  [[ "$ORIG_SYNC" =~ $FP_RE ]] && [[ "$ORIG_WATCH" =~ $FP_RE ]] || refuse "the journal $J has no readable ORIG_SYNC/ORIG_WATCH"
  if [ "$STEP" != PREPARING ] && { [ -n "$NEW_SYNC" ] || [ "$STEP" != ROLLING_BACK ]; }; then
    [[ "$NEW_SYNC" =~ $FP_RE ]] && [[ "$NEW_CYCLE" =~ $FP_RE ]] || refuse "the journal $J (STEP=$STEP) has no readable NEW_SYNC/NEW_CYCLE"
  fi
}

render_sync() { # SOURCE TARGET — the sync file without the nightly line.
  { grep -vE "$NIGHT_LINE_PATTERN" "$1" || true; echo "$SYNC_NOTE"; } > "$2"
}

cycle_lines() { # FILE... — non-comment lines that run the cycle.
  cat -- "$@" 2> /dev/null | grep -v '^[[:space:]]*#' | grep -c 'juristid-nightly-cycle\.sh' || true
}

others_cycle_lines() {
  local files=() f
  for f in "$D"/*.cron; do
    [ -f "$f" ] && [ "${f##*/}" != "$CYCLE" ] && files+=("$f")
  done
  if [ "${#files[@]}" -eq 0 ]; then echo 0; else cycle_lines "${files[@]}"; fi
}

# ---- --apply -------------------------------------------------------------------------------------------

stage_cycle() {
  cp -- "$NIGHTLY_CRON" "$D/$CYCLE$STAGED"
  op "stage $CYCLE$STAGED"
}

apply_validate_fresh() {
  local f
  for f in "$SYNC" "$WATCH"; do
    [ -f "$D/$f" ] || refuse "$D/$f is missing"
    [ ! -e "$D/$f$SUFFIX" ] || refuse "$D/$f$SUFFIX exists but there is no journal $J: not left by this script; compare it with $f by hand"
  done
  [ ! -e "$D/$CYCLE" ] || refuse "$D/$CYCLE already exists"
  grep -qE "$NIGHT_LINE_PATTERN" "$D/$SYNC" || refuse "$SYNC holds no nightly 15 21,22 line"
  [ "$(others_cycle_lines)" = 0 ] || refuse "a cron file already runs the cycle"
}

apply_validate_resume() {
  local live_sync live_watch live_cycle
  case "$STEP" in
    APPLIED) refuse "already applied (journal $J: STEP=APPLIED); to undo it run --rollback" ;;
    ROLLING_BACK) refuse "a --rollback was interrupted (journal $J: STEP=ROLLING_BACK); finish it first: re-run --rollback" ;;
  esac
  echo "RESUMING: the journal $J says an earlier --apply stopped at STEP=$STEP"
  live_sync="$(fp "$D/$SYNC")"
  live_watch="$(fp "$D/$WATCH")"
  live_cycle="$(fp "$D/$CYCLE")"
  [ "$(others_cycle_lines)" = 0 ] || refuse "a cron file other than $CYCLE runs the cycle — added since the interrupted apply; remove it by hand or run --rollback"
  if [ "$STEP" = PREPARING ]; then
    [ "$live_sync" = "$ORIG_SYNC" ] || refuse "$D/$SYNC is not what the interrupted apply recorded (journal $ORIG_SYNC, now $live_sync): edited since; run --rollback"
    [ "$live_watch" = "$ORIG_WATCH" ] || refuse "$D/$WATCH is not what the interrupted apply recorded (journal $ORIG_WATCH, now $live_watch): edited since; run --rollback"
    [ "$live_cycle" = absent ] || refuse "$D/$CYCLE exists although the interrupted apply had not installed it; run --rollback"
    return 0
  fi
  [ "$(fp "$D/$SYNC$SUFFIX")" = "$ORIG_SYNC" ] || refuse "the verified copy $D/$SYNC$SUFFIX no longer matches the journal ($ORIG_SYNC, now $(fp "$D/$SYNC$SUFFIX")); restore the originals by hand from the deployment kit's copies"
  [ "$(fp "$D/$WATCH$SUFFIX")" = "$ORIG_WATCH" ] || refuse "the verified copy $D/$WATCH$SUFFIX no longer matches the journal ($ORIG_WATCH, now $(fp "$D/$WATCH$SUFFIX")); restore the originals by hand from the deployment kit's copies"
  [ "$live_sync" = "$ORIG_SYNC" ] || [ "$live_sync" = "$NEW_SYNC" ] || refuse "$D/$SYNC is neither the original nor as the apply installs it (now $live_sync): edited since; run --rollback"
  [ "$live_watch" = "$ORIG_WATCH" ] || [ "$live_watch" = absent ] || refuse "$D/$WATCH is neither the original nor removed (now $live_watch): edited since; run --rollback"
  [ "$live_cycle" = absent ] || [ "$live_cycle" = "$NEW_CYCLE" ] || refuse "$D/$CYCLE is not the file the apply staged (journal $NEW_CYCLE, now $live_cycle); run --rollback"
  if [ "$live_cycle" = absent ] && [ "$(fp "$D/$CYCLE$STAGED")" != "$NEW_CYCLE" ]; then
    [ "$(fp "$NIGHTLY_CRON")" = "$NEW_CYCLE" ] || refuse "--nightly-cron $NIGHTLY_CRON is not the file the interrupted apply staged (journal $NEW_CYCLE); pass that file or run --rollback"
  fi
}

do_apply() {
  [ -n "$NIGHTLY_CRON" ] && [ -f "$NIGHTLY_CRON" ] || refuse "--nightly-cron must name the repository's juristid-nightly.cron"
  [ "$(cycle_lines "$NIGHTLY_CRON")" = 1 ] || refuse "$NIGHTLY_CRON does not hold exactly one cycle line"

  if [ -e "$J" ]; then
    load_journal
    apply_validate_resume
  else
    apply_validate_fresh
    ORIG_SYNC="$(fp "$D/$SYNC")"
    ORIG_WATCH="$(fp "$D/$WATCH")"
    write_journal PREPARING
  fi

  # 1. The complete backup set, verified, before any live file changes.
  if [ "$STEP" = PREPARING ]; then
    cp -p -- "$D/$SYNC" "$D/$SYNC$SUFFIX"
    op "copy $SYNC$SUFFIX"
    cp -p -- "$D/$WATCH" "$D/$WATCH$SUFFIX"
    op "copy $WATCH$SUFFIX"
    render_sync "$D/$SYNC$SUFFIX" "$D/$SYNC$STAGED"
    op "stage $SYNC$STAGED"
    stage_cycle
    [ "$(fp "$D/$SYNC")" = "$ORIG_SYNC" ] && [ "$(fp "$D/$SYNC$SUFFIX")" = "$ORIG_SYNC" ] ||
      fail "the copy $SYNC$SUFFIX does not equal $SYNC and the journal by sha256 and size; no live file was changed; re-run --apply (it copies again) or --rollback"
    [ "$(fp "$D/$WATCH")" = "$ORIG_WATCH" ] && [ "$(fp "$D/$WATCH$SUFFIX")" = "$ORIG_WATCH" ] ||
      fail "the copy $WATCH$SUFFIX does not equal $WATCH and the journal by sha256 and size; no live file was changed; re-run --apply (it copies again) or --rollback"
    ! grep -qE "$NIGHT_LINE_PATTERN" "$D/$SYNC$STAGED" || fail "the staged $SYNC$STAGED still holds the nightly line; no live file was changed; run --rollback"
    [ "$(fp "$D/$CYCLE$STAGED")" = "$(fp "$NIGHTLY_CRON")" ] || fail "the staged $CYCLE$STAGED is not $NIGHTLY_CRON; no live file was changed; re-run --apply or --rollback"
    NEW_SYNC="$(fp "$D/$SYNC$STAGED")"
    NEW_CYCLE="$(fp "$D/$CYCLE$STAGED")"
    write_journal BACKED_UP
  fi

  # 2. The sync file without the nightly line (atomic rename of the staged file).
  if [ "$(fp "$D/$SYNC")" != "$NEW_SYNC" ]; then
    if [ "$(fp "$D/$SYNC$STAGED")" != "$NEW_SYNC" ]; then
      render_sync "$D/$SYNC$SUFFIX" "$D/$SYNC$STAGED"
      op "stage $SYNC$STAGED"
      [ "$(fp "$D/$SYNC$STAGED")" = "$NEW_SYNC" ] || fail "the re-staged $SYNC$STAGED is not what the journal recorded; run --rollback"
    fi
    mv -f -- "$D/$SYNC$STAGED" "$D/$SYNC"
    op "replace $SYNC"
  fi
  advance SYNC_REPLACED

  # 3. The register watch file goes (its original is the verified copy).
  if [ -e "$D/$WATCH" ]; then
    rm -f -- "$D/$WATCH"
    op "remove $WATCH"
  fi
  advance WATCH_REMOVED

  # 4. The one cycle line (atomic rename of the staged copy).
  if [ ! -e "$D/$CYCLE" ]; then
    if [ "$(fp "$D/$CYCLE$STAGED")" != "$NEW_CYCLE" ]; then
      stage_cycle
      [ "$(fp "$D/$CYCLE$STAGED")" = "$NEW_CYCLE" ] || fail "the re-staged $CYCLE$STAGED is not what the journal recorded; run --rollback"
    fi
    mv -f -- "$D/$CYCLE$STAGED" "$D/$CYCLE"
    op "install $CYCLE"
  fi

  [ "$(fp "$D/$SYNC")" = "$NEW_SYNC" ] && [ ! -e "$D/$WATCH" ] && [ "$(fp "$D/$CYCLE")" = "$NEW_CYCLE" ] ||
    fail "the result is not what the journal recorded; run --rollback"
  [ "$(fp "$D/$SYNC$SUFFIX")" = "$ORIG_SYNC" ] && [ "$(fp "$D/$WATCH$SUFFIX")" = "$ORIG_WATCH" ] ||
    fail "a .pre-0229 copy changed during the apply; restore the originals by hand from the deployment kit's copies"
  local n
  n="$(cycle_lines "$D"/*.cron)"
  [ "$n" = 1 ] || fail "the result holds $n cycle lines; run --rollback"
  advance APPLIED
  echo "applied; now run update_cron and check: grep juristid /etc/cron.d/root"
}

# ---- --rollback ----------------------------------------------------------------------------------------

# show_change FILE EXPECTED_FILE_OR_EMPTY — what changed, for a refusal.
show_change() {
  echo "  $1: expected $2, found $(fp "$1")" >&2
  if [ -n "${3:-}" ] && [ -f "$3" ] && [ -f "$1" ] && command -v diff > /dev/null 2>&1; then
    diff -u --label "as installed" --label "now" -- "$3" "$1" | sed 's/^/    /' >&2 || true
  fi
}

restore_original() { # NAME ORIG_FP
  local name="$1" orig="$2" stamp
  if [ "$(fp "$D/$name")" = "$orig" ]; then return 0; fi
  if [ -e "$D/$name" ] && [ "$(fp "$D/$name")" != "${3:-}" ]; then
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    mv -f -- "$D/$name" "$D/$name.edited-$stamp"
    op "keep the edited $name as $name.edited-$stamp"
    echo "kept the edited $name as $D/$name.edited-$stamp"
  fi
  cp -p -- "$D/$name$SUFFIX" "$D/$name$RESTORING"
  op "copy $name$SUFFIX to $name$RESTORING"
  mv -f -- "$D/$name$RESTORING" "$D/$name"
  op "restore $name"
}

do_rollback() {
  local f live_sync live_watch live_cycle edited=0 expected_sync="" expected_cycle=""
  if [ ! -e "$J" ]; then
    for f in "$SYNC" "$WATCH"; do
      [ ! -e "$D/$f$SUFFIX" ] || refuse "there is no journal $J, but $D/$f$SUFFIX exists: not left by this script. Restore by hand: cp -p each *.cron$SUFFIX over its file, rm $CYCLE, then update_cron"
    done
    if [ -e "$J.tmp" ]; then
      rm -f -- "$J.tmp"
      op "remove $JOURNAL_NAME.tmp"
      echo "rolled back: an --apply had stopped before it changed anything; removed its draft journal"
      return 0
    fi
    refuse "nothing to roll back: no journal $J and no $SUFFIX copy in $D"
  fi
  load_journal

  live_sync="$(fp "$D/$SYNC")"
  live_watch="$(fp "$D/$WATCH")"
  live_cycle="$(fp "$D/$CYCLE")"
  # What the apply installed, to show a later edit against (a scratch file outside --cron-dir).
  if [ "$(fp "$D/$SYNC$SUFFIX")" = "$ORIG_SYNC" ] && [ -n "$NEW_SYNC" ]; then
    expected_sync="$(mktemp)"
    render_sync "$D/$SYNC$SUFFIX" "$expected_sync"
    [ "$(fp "$expected_sync")" = "$NEW_SYNC" ] || { rm -f -- "$expected_sync"; expected_sync=""; }
  fi
  if [ -n "$NIGHTLY_CRON" ] && [ -n "$NEW_CYCLE" ] && [ "$(fp "$NIGHTLY_CRON")" = "$NEW_CYCLE" ]; then
    expected_cycle="$NIGHTLY_CRON"
  fi

  # 1. Every live file must be in a state the journal explains, or --force-recover.
  local edits=()
  if [ "$live_sync" != "$ORIG_SYNC" ] && { [ -z "$NEW_SYNC" ] || [ "$live_sync" != "$NEW_SYNC" ]; }; then
    edits+=("$SYNC")
    echo "CHANGED SINCE THE APPLY: $D/$SYNC is neither the original nor as the apply installed it" >&2
    show_change "$D/$SYNC" "${NEW_SYNC:-$ORIG_SYNC} (installed) or $ORIG_SYNC (original)" "$expected_sync"
  fi
  if [ "$live_watch" != "$ORIG_WATCH" ] && [ "$live_watch" != absent ]; then
    edits+=("$WATCH")
    echo "CHANGED SINCE THE APPLY: $D/$WATCH was removed by the apply and is now a different file" >&2
    show_change "$D/$WATCH" "absent (removed) or $ORIG_WATCH (original)" "$D/$WATCH$SUFFIX"
  fi
  if [ "$live_cycle" != absent ] && { [ -z "$NEW_CYCLE" ] || [ "$live_cycle" != "$NEW_CYCLE" ]; }; then
    edits+=("$CYCLE")
    echo "CHANGED SINCE THE APPLY: $D/$CYCLE is not the file the apply installed" >&2
    show_change "$D/$CYCLE" "${NEW_CYCLE:-absent} (installed)" "$expected_cycle"
  fi
  [ -z "$expected_sync" ] || rm -f -- "$expected_sync"
  if [ "${#edits[@]}" -gt 0 ]; then
    [ "$FORCE_RECOVER" = 1 ] || refuse "${edits[*]} changed after the apply installed it (above); compare by hand, then re-run --rollback --force-recover to keep each edited file as <name>.edited-<time> and restore the original"
    edited=1
  fi
  # 2. A file is restored only from a copy that equals the journal.
  if [ "$live_sync" != "$ORIG_SYNC" ] && [ "$(fp "$D/$SYNC$SUFFIX")" != "$ORIG_SYNC" ]; then
    refuse "cannot restore $SYNC: its copy $D/$SYNC$SUFFIX is $(fp "$D/$SYNC$SUFFIX"), not the journal's $ORIG_SYNC; restore it by hand from the deployment kit's copies"
  fi
  if [ "$live_watch" != "$ORIG_WATCH" ] && [ "$(fp "$D/$WATCH$SUFFIX")" != "$ORIG_WATCH" ]; then
    refuse "cannot restore $WATCH: its copy $D/$WATCH$SUFFIX is $(fp "$D/$WATCH$SUFFIX"), not the journal's $ORIG_WATCH; restore it by hand from the deployment kit's copies"
  fi
  [ "$STEP" = ROLLING_BACK ] && echo "RESUMING: the journal $J says an earlier --rollback stopped"

  # 3. The cycle goes first (never two nightly syncs), then the originals come back.
  advance ROLLING_BACK
  if [ -e "$D/$CYCLE" ]; then
    if [ "$(fp "$D/$CYCLE")" = "$NEW_CYCLE" ]; then
      rm -f -- "$D/$CYCLE"
      op "remove $CYCLE"
    else
      f="$CYCLE.edited-$(date -u +%Y%m%dT%H%M%SZ)"
      mv -f -- "$D/$CYCLE" "$D/$f"
      op "keep the edited $CYCLE as $f"
      echo "kept the edited $CYCLE as $D/$f"
    fi
  fi
  restore_original "$WATCH" "$ORIG_WATCH" absent
  restore_original "$SYNC" "$ORIG_SYNC" "$NEW_SYNC"
  [ "$(fp "$D/$SYNC")" = "$ORIG_SYNC" ] && [ "$(fp "$D/$WATCH")" = "$ORIG_WATCH" ] && [ ! -e "$D/$CYCLE" ] ||
    fail "the restored files are not the originals the journal recorded; the copies and the journal are kept; compare by hand"

  # 4. Cleanup, the journal last.
  for f in "$SYNC$STAGED" "$CYCLE$STAGED" "$SYNC$RESTORING" "$WATCH$RESTORING" "$JOURNAL_NAME.tmp" "$SYNC$SUFFIX" "$WATCH$SUFFIX"; do
    if [ -e "$D/$f" ]; then
      rm -f -- "$D/$f"
      op "remove $f"
    fi
  done
  rm -f -- "$J"
  if [ "$edited" = 1 ] || compgen -G "$D/*.edited-*" > /dev/null; then
    echo "NOTE: edited files were kept beside the originals: $(cd "$D" && ls -- *.edited-* 2> /dev/null | tr '\n' ' ')"
  fi
  echo "rolled back; now run update_cron and check: grep juristid /etc/cron.d/root"
}

if [ "$ACTION" = apply ]; then
  do_apply
else
  do_rollback
fi
exit 0
