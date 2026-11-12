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
# The proof of the verification (audit 2026-11-11 DR-05): `juristid-verify-backup.sh
# --record` writes VERIFIED-L2 into the set once level 2 has passed, and the set
# is mounted READ-ONLY into the run's container at /backup-set
# (LOVABLE_SYNC_BACKUP_DIR). The engine writes only when that proof names the
# set, is level 2, was written after the set was taken and less than two hours
# before the run, and the dump still hashes to it (`lovable_sync.backup_proof_problem`);
# a bare or old set name writes nothing. A backup this script could not take
# (another job held its lock, the disk was full) or could not verify therefore
# leaves the APPLY run REVIEW_ONLY, never writing on an older set: the nightly
# 20:45Z set skipped on 10.11 (the host lock was held) was never an APPLY input,
# and an APPLY run never falls back to it.
#
# After the pilot run, when it was due, the wider observation (`lovable_sync
# observe`, docs/adr/0201) runs in the same way; it gates itself on the control
# row and its failure never changes this script's exit status.
#
# On demand (docs/adr/0213; brief 2026-11-09 §7 «Sünkrooni nüüd»): the web never
# reaches Lovable and never runs a synchronisation itself; it records a
# `DataSyncRequest`. This mode asks the database, per lane, whether one waits
# (`manage.py data_sync_requests poll --kind <lane>`: no credentials, no
# Lovable; it also records the runner's heartbeat, so Haldus can say plainly
# whether a server runner exists) and, only then, runs it through `manage.py
# data_sync_requests run-next` — for Lovable the same engine as the night,
# `run_sync(trigger=MANUAL)`, which always reads Lovable fresh. The night's
# gates still apply (one runner, release lock, web healthy) before anything is
# asked. The host-side gates of a lane (the pause file and the root-only
# credentials for Lovable; the capture corpus for OneNote) are checked before
# its poll and handed to it as `--problem CODE`: a waiting request is then
# REFUSED by name at once instead of waiting two hours to expire. In APPLY mode
# a backup set is taken and verified to level 2 and handed to the run as
# LOVABLE_SYNC_BACKUP_SET. The on-demand mode skips the observation's retention
# and the wider observation (both are the night's), and a firing with nothing to
# do leaves no log file behind. It is the same cron file, one more line; there is
# no second scheduler.
#
# The OneNote lane (brief §7 «Impordi OneNote'i materjalid» / «OneNote'i impordi
# kontroll») is asked first. If a request waits, it takes a backup set and
# verifies it to level 2, then runs `data_sync_requests run-next --kind onenote`
# in a one-off container with JURISTID_ONENOTE_CAPTURE_IMPORT=1,
# REAL_DATA_ALLOWED=1, the verified set as JURISTID_ONENOTE_BACKUP_SET and the
# host's capture corpus mounted READ-ONLY at /capture
# (JURISTID_ONENOTE_CAPTURE_ROOT). That container never receives the Lovable
# credentials. The Lovable lane follows; the host pause file pauses only the
# Lovable lane.
#
# Test seams (tests/test_ls2_d_host_script.py runs this script against a fake
# docker): JURISTID_SYNC_APPDATA, JURISTID_SYNC_BACKUP_ROOT,
# JURISTID_SYNC_CAPTURE_SOURCE, JURISTID_SYNC_LOCK and JURISTID_HOST_LOCK
# (scripts/deploy/lib.sh's name) replace the host paths. Cron sets none of them.
#
# Pause: `touch $SYNC_DIR/PAUSED` (host) or `lovable_sync control --mode off`
# (database; also what Haldus shows). Final cutover: `lovable_sync control
# --cut-over …`, then remove the cron file. The credentials stay on the host:
# Docker reads them as root (--env-file) and the container sees only the values.
# Never `set -x`: the environment carries the service login. Docker's --env-file
# takes values literally: write KEY=value with no quotes.
set -euo pipefail

APPDATA=${JURISTID_SYNC_APPDATA:-/mnt/user/appdata/juristid-main}
REPO=$APPDATA/repo
BACKUP_ROOT=${JURISTID_SYNC_BACKUP_ROOT:-/mnt/user/backups/juristid-main}
ENV_FILE=$APPDATA/config/juristid.env
CREDENTIALS=$APPDATA/config/lovable-sync.env
SYNC_DIR=$APPDATA/l100/sync
LOG_DIR=$SYNC_DIR/logs
LOG_RETENTION_DAYS=30
RUN_TIMEOUT=1200
# The host's sealed OneNote capture corpus, mounted read-only into the import.
CAPTURE_SOURCE=${JURISTID_SYNC_CAPTURE_SOURCE:-/mnt/user/juristid-main/source}
# One runner at a time (flock) and the release lock shared with every host job.
SYNC_LOCK=${JURISTID_SYNC_LOCK:-/tmp/juristid-lovable-sync.lock}
HOST_LOCK=${JURISTID_HOST_LOCK:-/tmp/juristid-host-lock}
ONENOTE_TIMEOUT=3600

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
  if [ "$HOST_LOCKED" = 1 ]; then rm -rf "$HOST_LOCK"; fi
  if [ "$KEEP_LOG" = 0 ]; then rm -f "$LOG"; fi
}
trap cleanup EXIT
exec >>"$LOG" 2>&1
say() { echo "$(date -u +%FT%TZ) $*"; }

exec 9>"$SYNC_LOCK"
flock -n 9 || { say "SKIP another synchronisation is running"; exit 0; }

# The release lock, taken (not only looked at): a release cannot start under a run.
if ! mkdir "$HOST_LOCK" 2>/dev/null; then
  say "SKIP a release holds $HOST_LOCK ($(cat "$HOST_LOCK/owner" 2>/dev/null))"
  exit 0
fi
HOST_LOCKED=1
echo "owner=lovable-sync pid=$$ $(date -u +%FT%TZ)" > "$HOST_LOCK/owner"

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

# A backup set taken and verified to level 2, its proof recorded in it; its name
# in BACKUP_SET and its host directory in BACKUP_DIR, or "" for both.
take_backup() {
  BACKUP_SET=""
  BACKUP_DIR=""
  say "backup"
  # This run already holds the host lock ($HOST_LOCK), so a backup that asked
  # for the same one would be refused ("another operation holds …") and nothing
  # would ever be written (production, 2026-11-10). The backup takes a lock of
  # its own instead, as the release scripts do (JURISTID_HOST_LOCK=…r52-backup);
  # the host lock this run holds keeps releases and restores out meanwhile.
  if (cd "$REPO" && JURISTID_HOST_LOCK="$HOST_LOCK.sync-backup" scripts/deploy/juristid-backup.sh --project juristid-main \
        --compose-file deploy/unraid-main/compose.yml --data-root "$APPDATA" \
        --backup-root "$BACKUP_ROOT" >"$LOG.backup" 2>&1 </dev/null); then
    SET_PATH="$(sed -nE 's/.*Backup set: +([^ ]+).*/\1/p' "$LOG.backup" | tail -1)"
    # Only a set this backup root holds, named as a set is (review of #617): any
    # other path the log might name is never verified, mounted or handed over.
    if ! [[ "$SET_PATH" =~ ^"$BACKUP_ROOT"/sets/[0-9]{8}T[0-9]{6}Z$ ]]; then
      say "WARN the backup named no set under $BACKUP_ROOT/sets: nothing is written"
      SET_PATH=""
    fi
    if [ -n "$SET_PATH" ] && (cd "$REPO" && scripts/deploy/juristid-verify-backup.sh \
          --project juristid-main --compose-file deploy/unraid-main/compose.yml \
          --set "$SET_PATH" --backup-root "$BACKUP_ROOT" --level 2 --record >"$LOG.verify" 2>&1 </dev/null) \
        && [ -f "$SET_PATH/VERIFIED-L2" ]; then
      BACKUP_SET="sets/$(basename "$SET_PATH")"
      BACKUP_DIR="$SET_PATH"
      say "backup $BACKUP_SET verified (level 2)"
    else
      say "WARN backup verification failed: nothing is written"
    fi
  else
    say "WARN backup failed: nothing is written"
  fi
}

# No Lovable credentials here: the database and the image only.
plain_manage() {
  timeout "$RUN_TIMEOUT" docker run --rm --name "juristid-main-sync-request-$$" \
    --network juristid-main-internal --env-file "$ENV_FILE" \
    --cpus 1 --memory 1g --pids-limit 256 --log-driver none \
    --entrypoint python "$IMAGE" manage.py "$@" </dev/null
}

# The Lovable container: Docker reads the credentials file as root; the container
# sees only the values, never the command line or this log. An APPLY run's set
# (SET, SET_DIR) is mounted read-only with its proof; with no set, nothing is.
SET=""
SET_DIR=""
one_off_manage() {
  local proof=()
  if [ -n "$SET" ] && [ -n "$SET_DIR" ]; then
    proof=(-v "$SET_DIR:/backup-set:ro" -e LOVABLE_SYNC_BACKUP_DIR=/backup-set)
  fi
  timeout "$RUN_TIMEOUT" docker run --rm --name "juristid-main-lovable-sync-$$" \
    --network juristid-main-internal \
    --env-file "$ENV_FILE" --env-file "$CREDENTIALS" -e JURISTID_LOVABLE_SYNC=1 \
    -e LOVABLE_SYNC_BACKUP_SET="$SET" "${proof[@]}" \
    --cpus 1 --memory 1g --pids-limit 256 --log-driver none \
    --entrypoint python "$IMAGE" manage.py "$@" </dev/null
}
one_off() { one_off_manage lovable_sync "$@"; }

# The Lovable lane's host-side gates, as a code ("" when it may run).
credentials_problem() {
  if [ ! -f "$CREDENTIALS" ]; then echo NO_CREDENTIALS; return; fi
  if [ "$(stat -c '%u %a' "$CREDENTIALS" 2>/dev/null || true)" != "0 600" ]; then
    echo CREDENTIALS_MODE
  fi
}

orc_onenote=0
if [ "$ON_DEMAND" = 1 ]; then
  ONOTE_PROBLEM=""
  if [ ! -d "$CAPTURE_SOURCE" ]; then ONOTE_PROBLEM=NO_CAPTURE_SOURCE; fi
  ONOTE="$(plain_manage data_sync_requests poll --kind onenote ${ONOTE_PROBLEM:+--problem "$ONOTE_PROBLEM"} | tail -1)" || ONOTE=""
  case "$ONOTE" in
    "DUE ONENOTE")
      KEEP_LOG=1
      say "onenote request (image ${IMAGE:7:12})"
      take_backup
      timeout "$ONENOTE_TIMEOUT" docker run --rm --name "juristid-main-onenote-import-$$" \
        --network juristid-main-internal --env-file "$ENV_FILE" \
        -e JURISTID_ONENOTE_CAPTURE_IMPORT=1 -e REAL_DATA_ALLOWED=1 \
        -e JURISTID_ONENOTE_CAPTURE_ROOT=/capture \
        -e JURISTID_ONENOTE_BACKUP_SET="$BACKUP_SET" \
        -v "$CAPTURE_SOURCE:/capture:ro" \
        -v "$APPDATA/evidence:/app/evidence" \
        -v "$APPDATA/legacy-source:/app/legacy-source" \
        -v "$APPDATA/derivatives:/app/derivatives" \
        --cpus 1 --memory 2g --pids-limit 256 --log-driver none \
        --entrypoint python "$IMAGE" manage.py data_sync_requests run-next --kind onenote \
        </dev/null || orc_onenote=$?
      say "onenote END rc=$orc_onenote"
      ;;
    "NOT_DUE NO_REQUEST") ;;
    "REFUSED "*) KEEP_LOG=1; say "onenote request $ONOTE (host)"; orc_onenote=1 ;;
    *) KEEP_LOG=1; say "FAIL the OneNote poll gave no answer (${ONOTE:-none})"; orc_onenote=1 ;;
  esac

  # The Lovable lane: its host-side gates become the poll's problem code.
  LOVABLE_PROBLEM=""
  if [ -e "$SYNC_DIR/PAUSED" ]; then LOVABLE_PROBLEM=HOST_PAUSED; else LOVABLE_PROBLEM="$(credentials_problem)"; fi
  DUE="$(plain_manage data_sync_requests poll --kind lovable ${LOVABLE_PROBLEM:+--problem "$LOVABLE_PROBLEM"} | tail -1)" || DUE=""
  case "$DUE" in
    "DUE APPLY") MODE=apply ;;
    "DUE COMPARE") MODE=compare ;;
    "DUE OFF") MODE=off ;;
    # Nothing waits: the firing ends here and leaves no log unless OneNote had work.
    "NOT_DUE "*) exit "$orc_onenote" ;;
    "REFUSED "*) KEEP_LOG=1; say "lovable request $DUE (host)"; exit 1 ;;
    *) KEEP_LOG=1; say "FAIL the Lovable poll gave no answer (${DUE:-none})"; exit 1 ;;
  esac
  KEEP_LOG=1
  say "$DUE (image ${IMAGE:7:12})"
  if [ "$MODE" = apply ]; then
    take_backup
    SET="$BACKUP_SET"
    SET_DIR="$BACKUP_DIR"
  fi
  # The administrator's request, through the request runner (interface 4): it
  # claims the request, refuses it if the mode is not the one it was confirmed
  # in (MODE_CHANGED), runs `run_sync(trigger=MANUAL)` with the set above
  # (`lovable_sync.host_backup_set()`; a paused synchronisation refuses and the
  # request is recorded REFUSED) and records the result. Nothing else runs.
  say "request"
  rc=0
  one_off_manage data_sync_requests run-next --kind lovable || rc=$?
  say "END rc=$rc"
  if [ "$rc" = 0 ]; then rc=$orc_onenote; fi
  exit "$rc"
fi

if [ -e "$SYNC_DIR/PAUSED" ]; then say "SKIP paused on the host ($SYNC_DIR/PAUSED)"; exit 0; fi

if [ ! -f "$CREDENTIALS" ]; then KEEP_LOG=1; say "FAIL $CREDENTIALS is missing"; exit 1; fi
if [ "$(stat -c '%u %a' "$CREDENTIALS" 2>/dev/null || true)" != "0 600" ]; then
  KEEP_LOG=1; say "FAIL $CREDENTIALS must be owned by root with mode 600"; exit 1
fi

DUE="$(one_off due --trigger "$TRIGGER" | tail -1)" || DUE=""
say "${DUE:-<no answer>} (image ${IMAGE:7:12})"
case "$DUE" in
  "DUE APPLY") MODE=apply ;;
  "DUE COMPARE") MODE=compare ;;
  "DUE OFF") MODE=off ;;
  "NOT_DUE "*) exit 0 ;;
  *) say "FAIL the due check gave no answer (container or database)"; exit 1 ;;
esac

if [ "$MODE" = apply ]; then
  take_backup
  SET="$BACKUP_SET"
  SET_DIR="$BACKUP_DIR"
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
