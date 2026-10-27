#!/usr/bin/env bash
# The nightly Lovable → Juristid synchronisation, host side (docs/adr/0192).
#
#   juristid-lovable-sync.sh             # from cron: 15 21,22 * * * (UTC)
#   juristid-lovable-sync.sh --manual    # now, with every check the nightly run makes
#
# A short-lived container of the image the site is serving now runs
# `manage.py lovable_sync`; nothing stays running and no compose service changes.
# The host keeps UTC and has no Tallinn zone, so cron fires twice and the
# container says which firing is 00:15 Europe/Tallinn (`lovable_sync due`).
#
# Order: one runner (flock) → pause file → no release in progress (host lock) →
# web healthy → credentials file present and root-only → due? → in APPLY mode a
# backup set taken AND level-2 verified → the run → log. A backup that fails is
# not retried: the run goes ahead with no backup set and writes nothing
# (REVIEW_ONLY), which Haldus and production_status then show.
#
# Pause: `touch $SYNC_DIR/PAUSED` (host) or `lovable_sync control --mode off`
# (database; also what Haldus shows). Final cutover: `lovable_sync control
# --cut-over …`, then remove the cron file. The credentials stay on the host:
# Docker reads them as root (--env-file) and the container sees only the values.
# Never `set -x`: the environment carries the service login. Docker's --env-file
# takes values literally: write KEY=value with no quotes.
set -euo pipefail

APPDATA=/mnt/user/appdata/juristid-main
REPO=$APPDATA/repo
BACKUP_ROOT=/mnt/user/backups/juristid-main
ENV_FILE=$APPDATA/config/juristid.env
CREDENTIALS=$APPDATA/config/lovable-sync.env
SYNC_DIR=$APPDATA/l100/sync
LOG_DIR=$SYNC_DIR/logs
LOG_RETENTION_DAYS=30
RUN_TIMEOUT=1200

TRIGGER=scheduled
case "${1:-}" in
  "") ;;
  --manual) TRIGGER=manual ;;
  *) echo "usage: $0 [--manual]" >&2; exit 64 ;;
esac

mkdir -p "$LOG_DIR" && chmod 0750 "$LOG_DIR"
LOG="$LOG_DIR/$(date -u +%Y%m%dT%H%M%SZ)-$TRIGGER.log"
exec >>"$LOG" 2>&1
say() { echo "$(date -u +%FT%TZ) $*"; }

exec 9>/tmp/juristid-lovable-sync.lock
flock -n 9 || { say "SKIP another synchronisation is running"; exit 0; }

if [ -e "$SYNC_DIR/PAUSED" ]; then say "SKIP paused on the host ($SYNC_DIR/PAUSED)"; exit 0; fi
# The release lock, taken (not only looked at): a release cannot start under a run.
if ! mkdir /tmp/juristid-host-lock 2>/dev/null; then
  say "SKIP a release holds /tmp/juristid-host-lock ($(cat /tmp/juristid-host-lock/owner 2>/dev/null))"
  exit 0
fi
echo "owner=lovable-sync pid=$$ $(date -u +%FT%TZ)" > /tmp/juristid-host-lock/owner
trap 'rm -rf /tmp/juristid-host-lock' EXIT

# The image the site is SERVING (its id), never whatever its tag points at now.
IMAGE="$(docker inspect -f '{{.Image}}' juristid-main-web 2>/dev/null)" || IMAGE=""
HEALTH="$(docker inspect -f '{{.State.Health.Status}}' juristid-main-web 2>/dev/null)" || HEALTH=""
if [ -z "$IMAGE" ] || [ "$HEALTH" != healthy ]; then say "FAIL web is not healthy (${HEALTH:-absent})"; exit 1; fi

if [ ! -f "$CREDENTIALS" ]; then say "FAIL $CREDENTIALS is missing"; exit 1; fi
if [ "$(stat -c '%u %a' "$CREDENTIALS" 2>/dev/null || true)" != "0 600" ]; then
  say "FAIL $CREDENTIALS must be owned by root with mode 600"; exit 1
fi

one_off() {
  timeout "$RUN_TIMEOUT" docker run --rm --name "juristid-main-lovable-sync-$$" \
    --network juristid-main-internal \
    --env-file "$ENV_FILE" --env-file "$CREDENTIALS" -e JURISTID_LOVABLE_SYNC=1 \
    --cpus 1 --memory 1g --pids-limit 256 --log-driver none \
    --entrypoint python "$IMAGE" manage.py lovable_sync "$@" </dev/null
}

DUE="$(one_off due --trigger "$TRIGGER" | tail -1)" || DUE=""
say "${DUE:-<no answer>} (image ${IMAGE:7:12})"
case "$DUE" in
  "DUE APPLY") MODE=apply ;;
  "DUE COMPARE") MODE=compare ;;
  "NOT_DUE "*) exit 0 ;;
  *) say "FAIL the due check gave no answer (container or database)"; exit 1 ;;
esac

SET=""
if [ "$MODE" = apply ]; then
  say "backup"
  if (cd "$REPO" && scripts/deploy/juristid-backup.sh --project juristid-main \
        --compose-file deploy/unraid-main/compose.yml --data-root "$APPDATA" \
        --backup-root "$BACKUP_ROOT" >"$LOG.backup" 2>&1 </dev/null); then
    SET_PATH="$(sed -nE 's/.*Backup set: +([^ ]+).*/\1/p' "$LOG.backup" | tail -1)"
    if [ -n "$SET_PATH" ] && (cd "$REPO" && scripts/deploy/juristid-verify-backup.sh \
          --project juristid-main --compose-file deploy/unraid-main/compose.yml \
          --set "$SET_PATH" --backup-root "$BACKUP_ROOT" --level 2 >"$LOG.verify" 2>&1 </dev/null); then
      SET="sets/$(basename "$SET_PATH")"
      say "backup $SET verified (level 2)"
    else
      say "WARN backup verification failed: the run writes nothing tonight"
    fi
  else
    say "WARN backup failed: the run writes nothing tonight"
  fi
fi

say "run"
rc=0
one_off run --trigger "$TRIGGER" ${SET:+--backup-set "$SET"} || rc=$?
say "END rc=$rc"
find "$LOG_DIR" -maxdepth 1 -type f -name '*.log*' -mtime +"$LOG_RETENTION_DAYS" -delete || true
exit "$rc"
