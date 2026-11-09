#!/usr/bin/env bash
# The nightly Lovable → Juristid synchronisation, host side (docs/adr/0192).
#
#   juristid-lovable-sync.sh             # from cron: 15 21,22 * * * (UTC)
#   juristid-lovable-sync.sh --manual    # now, with every check the nightly run makes
#   juristid-lovable-sync.sh --on-demand # from the same cron file every few minutes:
#                                        # runs only an administrator's waiting request
#
# A short-lived container of the image the site is serving now runs
# `manage.py lovable_sync`; nothing stays running and no compose service changes.
# The host keeps UTC and has no Tallinn zone, so cron fires twice and the
# container says which firing is 00:15 Europe/Tallinn (`lovable_sync due`).
#
# Order: one runner (flock) → no release in progress (host lock) → web healthy →
# the observation's retention (`lovable_sync prune-observations`, docs/adr/0201
# §8: local only, no credentials, every firing; the second firing of a Tallinn
# night is refused DUPLICATE and skipped quietly; its failure is logged and never
# changes this script's exit status) → pause file → credentials file present and
# root-only → due? → in APPLY mode a backup set taken AND level-2 verified → the
# run → log. Retention comes before every check that can end the night early, so
# it holds while the pilot is OFF or paused and the observation is off or failing. A backup that fails is
# not retried: the run goes ahead with no backup set and writes nothing
# (REVIEW_ONLY), which Haldus and production_status then show.
#
# After the pilot run, when it was due, the wider observation (`lovable_sync
# observe`, docs/adr/0201) runs in the same way; it gates itself on the control
# row and its failure never changes this script's exit status.
#
# On demand (docs/adr/0213; brief 2026-11-09 §7 «Sünkrooni nüüd»): the web never
# reaches Lovable and never runs a synchronisation itself; it records a
# `DataSyncRequest`. This mode asks the database whether one waits (`lovable_sync
# due --on-demand`, read-only) and, only then, runs it through `manage.py
# data_sync_requests run-next --kind lovable` — the same engine as the night,
# `run_sync(trigger=MANUAL)`, which always reads Lovable fresh. Every gate still
# applies (one runner, release lock, web healthy, pause file, root-only
# credentials, and in APPLY mode a backup set taken and verified to level 2,
# handed to the run as LOVABLE_SYNC_BACKUP_SET). It skips the observation's
# retention and the wider observation (both are the night's), and a firing with
# nothing to do leaves no log file behind. It is the same cron file, one more
# line; there is no second scheduler.
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
ON_DEMAND=0
case "${1:-}" in
  "") ;;
  --manual) TRIGGER=manual ;;
  --on-demand) TRIGGER=manual; ON_DEMAND=1 ;;
  *) echo "usage: $0 [--manual|--on-demand]" >&2; exit 64 ;;
esac

mkdir -p "$LOG_DIR" && chmod 0750 "$LOG_DIR"
LABEL=$TRIGGER
if [ "$ON_DEMAND" = 1 ]; then LABEL=on-demand; fi
LOG="$LOG_DIR/$(date -u +%Y%m%dT%H%M%SZ)-$LABEL.log"
# An on-demand firing keeps its log only once it has work or fails (KEEP_LOG=1).
KEEP_LOG=1
if [ "$ON_DEMAND" = 1 ]; then KEEP_LOG=0; fi
HOST_LOCKED=0
cleanup() {
  if [ "$HOST_LOCKED" = 1 ]; then rm -rf /tmp/juristid-host-lock; fi
  if [ "$KEEP_LOG" = 0 ]; then rm -f "$LOG"; fi
}
trap cleanup EXIT
exec >>"$LOG" 2>&1
say() { echo "$(date -u +%FT%TZ) $*"; }

exec 9>/tmp/juristid-lovable-sync.lock
flock -n 9 || { say "SKIP another synchronisation is running"; exit 0; }

# The release lock, taken (not only looked at): a release cannot start under a run.
if ! mkdir /tmp/juristid-host-lock 2>/dev/null; then
  say "SKIP a release holds /tmp/juristid-host-lock ($(cat /tmp/juristid-host-lock/owner 2>/dev/null))"
  exit 0
fi
HOST_LOCKED=1
echo "owner=lovable-sync pid=$$ $(date -u +%FT%TZ)" > /tmp/juristid-host-lock/owner

# The image the site is SERVING (its id), never whatever its tag points at now.
IMAGE="$(docker inspect -f '{{.Image}}' juristid-main-web 2>/dev/null)" || IMAGE=""
HEALTH="$(docker inspect -f '{{.State.Health.Status}}' juristid-main-web 2>/dev/null)" || HEALTH=""
if [ -z "$IMAGE" ] || [ "$HEALTH" != healthy ]; then
  KEEP_LOG=1; say "FAIL web is not healthy (${HEALTH:-absent})"; exit 1
fi

if [ "$ON_DEMAND" = 0 ]; then
  # Retention of the observation's raw data (docs/adr/0201 §8). No credentials
  # and no Lovable: the same image and database only.
  PRUNE_ARGS=(prune-observations)
  if [ "$TRIGGER" = manual ]; then PRUNE_ARGS+=(--manual); fi
  prc=0
  timeout "$RUN_TIMEOUT" docker run --rm --name "juristid-main-lovable-prune-$$" \
    --network juristid-main-internal --env-file "$ENV_FILE" \
    --cpus 1 --memory 1g --pids-limit 256 --log-driver none \
    --entrypoint python "$IMAGE" manage.py lovable_sync "${PRUNE_ARGS[@]}" </dev/null || prc=$?
  case "$prc" in
    0) say "prune END ok" ;;
    2) say "prune SKIP already done for this night" ;;
    *) say "WARN prune failed rc=$prc (the pilot run's status is unchanged)" ;;
  esac
fi

if [ -e "$SYNC_DIR/PAUSED" ]; then say "SKIP paused on the host ($SYNC_DIR/PAUSED)"; exit 0; fi

if [ ! -f "$CREDENTIALS" ]; then KEEP_LOG=1; say "FAIL $CREDENTIALS is missing"; exit 1; fi
if [ "$(stat -c '%u %a' "$CREDENTIALS" 2>/dev/null || true)" != "0 600" ]; then
  KEEP_LOG=1; say "FAIL $CREDENTIALS must be owned by root with mode 600"; exit 1
fi

SET=""
one_off_manage() {
  timeout "$RUN_TIMEOUT" docker run --rm --name "juristid-main-lovable-sync-$$" \
    --network juristid-main-internal \
    --env-file "$ENV_FILE" --env-file "$CREDENTIALS" -e JURISTID_LOVABLE_SYNC=1 \
    -e LOVABLE_SYNC_BACKUP_SET="$SET" \
    --cpus 1 --memory 1g --pids-limit 256 --log-driver none \
    --entrypoint python "$IMAGE" manage.py "$@" </dev/null
}
one_off() { one_off_manage lovable_sync "$@"; }

if [ "$ON_DEMAND" = 1 ]; then
  DUE="$(one_off due --trigger manual --on-demand | tail -1)" || DUE=""
else
  DUE="$(one_off due --trigger "$TRIGGER" | tail -1)" || DUE=""
fi
# Nothing waits: an on-demand firing ends here and leaves no log.
if [ "$DUE" = "NOT_DUE NO_REQUEST" ]; then exit 0; fi
KEEP_LOG=1
say "${DUE:-<no answer>} (image ${IMAGE:7:12})"
case "$DUE" in
  "DUE APPLY") MODE=apply ;;
  "DUE COMPARE") MODE=compare ;;
  "DUE OFF") MODE=off ;;
  "NOT_DUE "*) exit 0 ;;
  *) say "FAIL the due check gave no answer (container or database)"; exit 1 ;;
esac

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

if [ "$ON_DEMAND" = 1 ]; then
  # The administrator's request, through the request runner (interface 4): it
  # claims the request, runs `run_sync(trigger=MANUAL)` with the set above
  # (`lovable_sync.host_backup_set()`; a paused synchronisation refuses and the
  # request is recorded REFUSED) and records the result. Nothing else runs.
  say "request"
  rc=0
  one_off_manage data_sync_requests run-next --kind lovable || rc=$?
  say "END rc=$rc"
  exit "$rc"
fi

say "run"
rc=0
one_off run --trigger "$TRIGGER" ${SET:+--backup-set "$SET"} || rc=$?
say "END rc=$rc"

# The wider observation (docs/adr/0201), the same night and the same container
# pattern, after the pilot run. `observe` itself refuses unless
# `lovable_sync control --observe on` (OBSERVE_OFF, exit 2) — that is the gate.
# Its outcome is logged and never changes the pilot run's exit status.
OBSERVE_ARGS=(observe)
if [ "$TRIGGER" = manual ]; then OBSERVE_ARGS+=(--manual); fi
say "observe"
orc=0
one_off "${OBSERVE_ARGS[@]}" || orc=$?
case "$orc" in
  0) say "observe END ok" ;;
  2) say "observe SKIP refused (off, duplicate or locked; see the line above)" ;;
  *) say "WARN observe failed rc=$orc (the pilot run's status is unchanged)" ;;
esac
find "$LOG_DIR" -maxdepth 1 -type f -name '*.log*' -mtime +"$LOG_RETENTION_DAYS" -delete || true
exit "$rc"
