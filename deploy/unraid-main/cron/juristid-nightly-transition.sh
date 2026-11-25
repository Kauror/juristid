#!/usr/bin/env bash
#
# The cron change of docs/adr/0229 §7, and its rollback, as one reviewed script.
# NOT RUN BY ANYTHING: an authorised deployment runs it on the host, then
# `update_cron`. tests/test_nightly_cycle_transition.py runs it on a copy of the
# host's cron files of 25.11.2026 (tests/fixtures/nightly_cycle/dynamix/).
#
#   juristid-nightly-transition.sh --cron-dir /boot/config/plugins/dynamix \
#       --nightly-cron /mnt/user/appdata/juristid-main/repo/deploy/unraid-main/cron/juristid-nightly.cron \
#       --apply | --rollback
#
# --apply, in --cron-dir:
#   1. keeps byte-for-byte copies juristid-lovable-sync.cron.pre-0229 and
#      juristid-register-watch.cron.pre-0229 (update_cron reads only *.cron);
#   2. removes the nightly Lovable line `15 21,22 * * * … juristid-lovable-sync.sh`
#      from juristid-lovable-sync.cron — the on-demand `2-59/5` line stays;
#   3. removes juristid-register-watch.cron (the 03:30 watch, the 04:20 sweep and
#      the Wednesday/Thursday agenda lines): its copy is step 1's;
#   4. installs juristid-nightly.cron, the one line that runs the cycle.
#   juristid.cron (the 20:45 backup and the monitors) is not touched.
# It refuses, changing nothing, when a .pre-0229 copy already exists, when a file
# it changes is missing or does not hold the line it removes, or when the result
# would not hold exactly one cycle line.
#
# --rollback puts the two copies back as they were, removes juristid-nightly.cron,
# and refuses, changing nothing, when a copy is missing.
#
# Either way the next step is `update_cron` (and `grep juristid /etc/cron.d/root`).

set -euo pipefail

CRON_DIR=""
NIGHTLY_CRON=""
ACTION=""
SUFFIX=".pre-0229"
SYNC=juristid-lovable-sync.cron
WATCH=juristid-register-watch.cron
CYCLE=juristid-nightly.cron
NIGHT_LINE_PATTERN='^15 21,22 \* \* \* bash .*/scripts/deploy/juristid-lovable-sync\.sh &> /dev/null$'

usage() {
  echo "Usage: juristid-nightly-transition.sh --cron-dir DIR --nightly-cron FILE --apply|--rollback"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --cron-dir) CRON_DIR="${2:-}"; shift 2 ;;
    --nightly-cron) NIGHTLY_CRON="${2:-}"; shift 2 ;;
    --apply) ACTION=apply; shift ;;
    --rollback) ACTION=rollback; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done
[ -n "$CRON_DIR" ] && [ -d "$CRON_DIR" ] || { usage >&2; echo "--cron-dir must be a directory" >&2; exit 64; }
[ -n "$ACTION" ] || { usage >&2; echo "--apply or --rollback is required" >&2; exit 64; }

refuse() { echo "REFUSED: $*; nothing changed" >&2; exit 1; }

cycle_lines() {
  cat "$CRON_DIR"/*.cron 2>/dev/null | grep -v '^[[:space:]]*#' | grep -c 'juristid-nightly-cycle\.sh' || true
}

if [ "$ACTION" = apply ]; then
  [ -n "$NIGHTLY_CRON" ] && [ -f "$NIGHTLY_CRON" ] || refuse "--nightly-cron must name the repository's juristid-nightly.cron"
  for f in "$SYNC" "$WATCH"; do
    [ -f "$CRON_DIR/$f" ] || refuse "$CRON_DIR/$f is missing"
    [ ! -e "$CRON_DIR/$f$SUFFIX" ] || refuse "$CRON_DIR/$f$SUFFIX already exists (applied before?)"
  done
  [ ! -e "$CRON_DIR/$CYCLE" ] || refuse "$CRON_DIR/$CYCLE already exists"
  grep -qE "$NIGHT_LINE_PATTERN" "$CRON_DIR/$SYNC" || refuse "$SYNC holds no nightly 15 21,22 line"
  [ "$(cycle_lines)" = 0 ] || refuse "a cron file already runs the cycle"
  [ "$(grep -v '^[[:space:]]*#' "$NIGHTLY_CRON" | grep -c 'juristid-nightly-cycle\.sh' || true)" = 1 ] ||
    refuse "$NIGHTLY_CRON does not hold exactly one cycle line"

  cp -p -- "$CRON_DIR/$SYNC" "$CRON_DIR/$SYNC$SUFFIX"
  cp -p -- "$CRON_DIR/$WATCH" "$CRON_DIR/$WATCH$SUFFIX"
  {
    grep -vE "$NIGHT_LINE_PATTERN" "$CRON_DIR/$SYNC$SUFFIX"
    echo "# 2026-11 (docs/adr/0229): the nightly 15 21,22 line moved into juristid-nightly.cron (the cycle)."
  } >"$CRON_DIR/$SYNC.new"
  mv -f -- "$CRON_DIR/$SYNC.new" "$CRON_DIR/$SYNC"
  rm -f -- "$CRON_DIR/$WATCH"
  cp -- "$NIGHTLY_CRON" "$CRON_DIR/$CYCLE"
  [ "$(cycle_lines)" = 1 ] || { echo "ERROR: the result holds $(cycle_lines) cycle lines; run --rollback" >&2; exit 1; }
  echo "applied; now run update_cron and check: grep juristid /etc/cron.d/root"
  exit 0
fi

for f in "$SYNC" "$WATCH"; do
  [ -f "$CRON_DIR/$f$SUFFIX" ] || refuse "$CRON_DIR/$f$SUFFIX is missing"
done
mv -f -- "$CRON_DIR/$SYNC$SUFFIX" "$CRON_DIR/$SYNC"
mv -f -- "$CRON_DIR/$WATCH$SUFFIX" "$CRON_DIR/$WATCH"
rm -f -- "$CRON_DIR/$CYCLE"
echo "rolled back; now run update_cron and check: grep juristid /etc/cron.d/root"
