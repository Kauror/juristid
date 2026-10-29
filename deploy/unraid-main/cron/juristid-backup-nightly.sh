#!/usr/bin/env bash
#
# One scheduled Juristid backup set, unattended: take the set with the
# production script, keep a log of the run, and tell somebody when it did not
# happen. Nothing here is new: `juristid-backup.sh` does the work and
# `juristid-check-backup-age.sh` answers the one question a schedule exists to
# answer. This wrapper is the glue a cron line needs and the backup script
# deliberately does not carry (it prints and exits; it schedules nothing).
#
# NOT INSTALLED BY ANYTHING. A cron line is a host change the owner approves
# (deploy/unraid-main/cron/juristid.cron.example), and the age limit it needs
# is the RPO, a decision (docs/open-decisions.md). Both are passed in; neither
# has a default.
#
#   juristid-backup-nightly.sh --repo /mnt/user/appdata/juristid-main/repo \
#       --data-root /mnt/user/appdata/juristid-main \
#       --backup-root /mnt/user/backups/juristid-main \
#       --log-dir /mnt/user/appdata/juristid-main/backup-logs \
#       --rpo-hours N
#
# The backup takes the host lock itself (lib.sh); a release or a restore that
# holds it makes this run exit non-zero with the owner's name in the log, and
# the age check the next morning is what says whether that mattered.

set -euo pipefail

REPO=""
DATA_ROOT=""
BACKUP_ROOT=""
LOG_DIR=""
RPO_HOURS=""
PROJECT="juristid-main"
NOTIFY="${JURISTID_NOTIFY:-/usr/local/emhttp/webGui/scripts/notify}"

usage() {
  echo "usage: juristid-backup-nightly.sh --repo DIR --data-root DIR --backup-root DIR --log-dir DIR --rpo-hours N [--project NAME]" >&2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2 ;;
    --data-root) DATA_ROOT="${2:-}"; shift 2 ;;
    --backup-root) BACKUP_ROOT="${2:-}"; shift 2 ;;
    --log-dir) LOG_DIR="${2:-}"; shift 2 ;;
    --rpo-hours) RPO_HOURS="${2:-}"; shift 2 ;;
    --project) PROJECT="${2:-}"; shift 2 ;;
    *) usage; echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

for required in REPO DATA_ROOT BACKUP_ROOT LOG_DIR RPO_HOURS; do
  [ -n "${!required}" ] || { usage; echo "--$(printf '%s' "$required" | tr '_A-Z' '-a-z') is required" >&2; exit 1; }
done
case "$RPO_HOURS" in *[!0-9]* | '') echo "--rpo-hours must be a whole number of hours" >&2; exit 1 ;; esac

mkdir -p "$LOG_DIR"
chmod 750 "$LOG_DIR" 2>/dev/null || true
LOG="$LOG_DIR/$(date -u +%Y%m%dT%H%M%SZ)-backup.log"

say() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

alert() {
  # Subject and body carry the outcome and a path, never a value from any file.
  if [ -x "$NOTIFY" ]; then
    "$NOTIFY" -e "Juristid backup" -s "$1" -d "$2" -i alert >/dev/null 2>&1 || true
  fi
}

say "START nightly backup, project $PROJECT, rpo ${RPO_HOURS}h"
say "docker compose: $(docker compose version 2>&1 | head -n 1 || echo 'not available')"

rc=0
bash "$REPO/scripts/deploy/juristid-backup.sh" \
  --project "$PROJECT" \
  --compose-file "$REPO/deploy/unraid-main/compose.yml" \
  --data-root "$DATA_ROOT" \
  --backup-root "$BACKUP_ROOT" >>"$LOG" 2>&1 || rc=$?

if [ "$rc" != 0 ]; then
  say "FAIL juristid-backup.sh exit $rc"
  alert "nightly backup failed (exit $rc)" "juristid-backup.sh did not seal a set; read $LOG. A held host lock means a release or a restore was running."
else
  say "OK set sealed"
fi

age_rc=0
bash "$REPO/scripts/deploy/juristid-check-backup-age.sh" --backup-root "$BACKUP_ROOT" --max-age-hours "$RPO_HOURS" >>"$LOG" 2>&1 || age_rc=$?
case "$age_rc" in
  0) say "OK newest set within ${RPO_HOURS}h" ;;
  2) say "CRITICAL no complete set"; alert "no complete Juristid backup set" "juristid-check-backup-age.sh found no complete set under $BACKUP_ROOT/sets." ;;
  3) say "CRITICAL newest set older than ${RPO_HOURS}h"; alert "Juristid backup older than the RPO" "The newest complete set is older than ${RPO_HOURS}h; read $LOG." ;;
  *) say "WARN age check exit $age_rc"; alert "Juristid backup age check could not run" "juristid-check-backup-age.sh exit $age_rc; read $LOG." ;;
esac

partials="$(find "$BACKUP_ROOT/sets" -maxdepth 1 -mindepth 1 -type d -name '*.partial' 2>/dev/null | wc -l | tr -d ' ')"
if [ "$partials" -gt 0 ]; then
  say "WARN $partials unfinished .partial run(s) present"
  alert "Juristid backup: unfinished runs" "$partials .partial director(y/ies) under $BACKUP_ROOT/sets; they are not backups and nothing removes them."
fi

# Logs older than 30 days go; the sets never do (nothing in the repository
# deletes a backup, RECOVERY.md).
find "$LOG_DIR" -maxdepth 1 -type f -name '*-backup.log' -mtime +30 -delete 2>/dev/null || true

say "END rc=$rc age_rc=$age_rc"
exit "$rc"
