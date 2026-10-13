#!/usr/bin/env bash
# Copy one verified Juristid backup set off the host, encrypted, with restic.
#
# DRAFT. UNTESTED AGAINST A REAL DESTINATION. No off-host destination has been
# approved (deploy/unraid-main/OFFHOST-BACKUP.md, "OWNER ACTION REQUIRED"), so
# this has never pushed a byte anywhere. It exists so that the day one is
# approved, the sequence is reviewed code rather than something typed.
#
# It refuses to run unless the owner-supplied destination values are present.
# There is no default destination and there must never be one: a backup that
# "just works" with no configuration has picked a place to send the Chamber's
# data on somebody's behalf.
#
# WHAT IT DOES, IN ORDER, STOPPING AT THE FIRST FAILURE
#   1. refuses without JURISTID_OFFHOST_ENV_FILE, JURISTID_OFFHOST_PASSWORD_FILE
#      and a digest-pinned JURISTID_OFFHOST_RESTIC_IMAGE, and refuses a
#      repository on this host's own storage
#   2. verifies the set at levels 1 and 2 with the existing verifier
#   3. writes the recovery-identity record <backup-root>/offhost-manifests/<set>.json
#      (set id, SHA256SUMS hash, git SHA, image id, fingerprint hash; no secret)
#   4. `restic backup` of sets/, evidence/, legacy-source/, fingerprints/ and
#      offhost-manifests/, the backup root mounted READ-ONLY, tagged set:<id>
#   5. `restic check`, then lists the snapshot for that set
#
# It never deletes anything, here or at the destination: forget/prune is a
# custodian's job with a key that never lives on this host.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VERIFY="$SCRIPT_DIR/../../scripts/deploy/juristid-verify-backup.sh"
HOST_TAG="kocorp-juristid-main"

usage() {
  cat <<'USAGE'
Usage:
  offhost-backup.sh --set DIR --project NAME --compose-file PATH [--backup-root DIR]

  --set           the backup set to copy (<backup-root>/sets/<stamp>)
  --project       the Compose project whose db container runs level 2
  --compose-file  that project's Compose file
  --backup-root   where sets/, evidence/ and legacy-source/ live.
                  Defaults to the grandparent of --set.

Required environment (no defaults, see deploy/unraid-main/OFFHOST-BACKUP.md):
  JURISTID_OFFHOST_ENV_FILE       mode 600; RESTIC_REPOSITORY=... and the push credential
  JURISTID_OFFHOST_PASSWORD_FILE  mode 600; this host's restic repository key
  JURISTID_OFFHOST_RESTIC_IMAGE   restic/restic@sha256:<digest>
USAGE
}

die() {
  echo "offhost-backup: $*" >&2
  exit 2
}

SET_DIR=""
PROJECT=""
COMPOSE_FILE=""
BACKUP_ROOT=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --set) SET_DIR="${2:-}"; shift 2 ;;
    --project) PROJECT="${2:-}"; shift 2 ;;
    --compose-file) COMPOSE_FILE="${2:-}"; shift 2 ;;
    --backup-root) BACKUP_ROOT="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

# -- 1. the destination exists only if the owner supplied it ---------------

ENV_FILE="${JURISTID_OFFHOST_ENV_FILE:-}"
PASSWORD_FILE="${JURISTID_OFFHOST_PASSWORD_FILE:-}"
RESTIC_IMAGE="${JURISTID_OFFHOST_RESTIC_IMAGE:-}"

[ -n "$ENV_FILE" ] || die "JURISTID_OFFHOST_ENV_FILE is not set. No off-host destination is configured; nothing was copied."
[ -n "$PASSWORD_FILE" ] || die "JURISTID_OFFHOST_PASSWORD_FILE is not set. Nothing was copied."
[ -n "$RESTIC_IMAGE" ] || die "JURISTID_OFFHOST_RESTIC_IMAGE is not set. Nothing was copied."
case "$RESTIC_IMAGE" in
  *@sha256:*) ;;
  *) die "JURISTID_OFFHOST_RESTIC_IMAGE must be pinned by digest (restic/restic@sha256:...), not a tag." ;;
esac

for secret in "$ENV_FILE" "$PASSWORD_FILE"; do
  [ -f "$secret" ] || die "$secret does not exist."
  mode="$(stat -c %a "$secret")"
  [ "$mode" = "600" ] || die "$secret is mode $mode; it must be 600."
done

repository_line="$(grep -E '^RESTIC_REPOSITORY=.+' "$ENV_FILE" || true)"
[ -n "$repository_line" ] || die "$ENV_FILE has no RESTIC_REPOSITORY. No destination; nothing was copied."
repository="${repository_line#RESTIC_REPOSITORY=}"
# A repository on this host's own array is not off-host, whatever it is called.
case "$repository" in
  /mnt/user/*|/mnt/disk[0-9]*|/mnt/cache*|/boot/*|local:*)
    die "RESTIC_REPOSITORY points at this host's own storage. That is not an off-host copy." ;;
esac

# -- the set ---------------------------------------------------------------

[ -n "$SET_DIR" ] || { usage >&2; die "--set is required."; }
[ -n "$PROJECT" ] || { usage >&2; die "--project is required (level 2 needs a db container)."; }
[ -n "$COMPOSE_FILE" ] || { usage >&2; die "--compose-file is required."; }
[ -d "$SET_DIR" ] || die "$SET_DIR is not a directory."
SET_DIR="$(cd -- "$SET_DIR" && pwd)"
SET_ID="$(basename -- "$SET_DIR")"
case "$SET_ID" in
  *.partial) die "$SET_ID is an unfinished run, not a set." ;;
esac
[ -n "$BACKUP_ROOT" ] || BACKUP_ROOT="$(cd -- "$SET_DIR/../.." && pwd)"
[ -f "$SET_DIR/manifest.json" ] || die "$SET_DIR has no manifest.json."
[ -f "$SET_DIR/SHA256SUMS" ] || die "$SET_DIR has no SHA256SUMS."
command -v docker >/dev/null 2>&1 || die "docker is required."

# -- 2. source consistency, with the existing verifier ---------------------

echo "== levels 1 and 2 on $SET_ID"
"$VERIFY" --project "$PROJECT" --compose-file "$COMPOSE_FILE" \
  --set "$SET_DIR" --backup-root "$BACKUP_ROOT" --level 2

# -- 3. the recovery identity ----------------------------------------------

REVISION="$(sed -n 's/^  "application_revision": "\(.*\)",$/\1/p' "$SET_DIR/manifest.json")"
[ -n "$REVISION" ] || REVISION="unknown"
SUMS_SHA="$(sha256sum "$SET_DIR/SHA256SUMS")"
SUMS_SHA="${SUMS_SHA%% *}"

IMAGE_ID="unknown"
if [ "$REVISION" != "unknown" ]; then
  IMAGE_ID="$(docker image inspect --format '{{.Id}}' "juristid-main-web:${REVISION:0:12}" 2>/dev/null || echo unknown)"
fi

FP_NAME=""
FP_SHA=""
if [ "$REVISION" != "unknown" ] && [ -f "$BACKUP_ROOT/fingerprints/fp-before-release-${REVISION:0:12}.json" ]; then
  FP_NAME="fp-before-release-${REVISION:0:12}.json"
  FP_SHA="$(sha256sum "$BACKUP_ROOT/fingerprints/$FP_NAME")"
  FP_SHA="${FP_SHA%% *}"
fi

MANIFEST_DIR="$BACKUP_ROOT/offhost-manifests"
install -d -m 700 "$MANIFEST_DIR"
RECORD="$MANIFEST_DIR/$SET_ID.json"
[ ! -e "$RECORD" ] || die "$RECORD already exists. This set was copied before, or a run stopped half way; read the record and decide, nothing is overwritten."
set -o noclobber
cat >"$RECORD" <<RECORD
{
  "record_version": 1,
  "set_id": "$SET_ID",
  "set_sha256sums_sha256": "$SUMS_SHA",
  "application_revision": "$REVISION",
  "image_id": "$IMAGE_ID",
  "fingerprint_file": "$FP_NAME",
  "fingerprint_sha256": "$FP_SHA",
  "fingerprint_note": "taken from the live system beside the set, not inside its snapshot; near-identical, not a proof",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "restic_image": "$RESTIC_IMAGE"
}
RECORD
set +o noclobber
echo "identity record: $RECORD"

# -- 4. encrypt and upload; the backup root is read-only to the uploader ---

restic() {
  docker run --rm \
    --env-file "$ENV_FILE" \
    -e RESTIC_PASSWORD_FILE=/run/restic-password \
    -v "$PASSWORD_FILE:/run/restic-password:ro" \
    -v "$BACKUP_ROOT:/data:ro" \
    "$RESTIC_IMAGE" "$@"
}

echo "== restic backup, tagged set:$SET_ID"
restic backup --host "$HOST_TAG" --tag "set:$SET_ID" --tag "rev:$REVISION" \
  --exclude '*.partial' \
  /data/sets /data/evidence /data/legacy-source /data/fingerprints /data/offhost-manifests

# -- 5. check what arrived -------------------------------------------------

echo "== restic check"
restic check
echo "== the snapshot for $SET_ID"
restic snapshots --host "$HOST_TAG" --tag "set:$SET_ID"

echo ""
echo "Copied $SET_ID off-host. This proves the push and the repository's structure."
echo "It does not prove a restore: see OFFHOST-BACKUP.md section 5."
