#!/usr/bin/env bash
#
# Restore a backup set into an empty deployment.
#
# The dangerous direction of a restore is not failing. It is succeeding onto
# data that was still good — a restore aimed at a live database replaces
# everything written since the dump, and no part of that is recoverable
# afterwards. So this script refuses a database that already holds a register,
# and the refusal is not overridable by a flag: dropping a live database is a
# decision to take deliberately, at a prompt, with the runbook open, not a flag
# somebody adds because the script complained (deploy/unraid-main/RECOVERY.md).
#
# ORDER
#
#   1. verify the set (levels 1 and 2) — a restore from a corrupt archive
#      leaves a half-populated database that looks like a working one
#   2. evidence and page XML into the data root, never overwriting
#   3. the database
#   4. hand back to the operator, who verifies before anything is published
#
# Evidence first, so that the moment rows exist, their bytes already do. The
# copy uses --ignore-existing because evidence is immutable: an object already
# present is by definition the same object, and a restore that overwrites one is
# a restore that can destroy something newer than the backup.
#
# What this script does NOT do, on purpose:
#
#   * rebuild derivatives or the search index — those are separate commands with
#     their own progress and their own cost (docs/adr/0014)
#   * start the tunnel or expose anything publicly — an unverified restore must
#     not be reachable
#   * restore secrets — they never enter a backup set
#   * apply business data — a restore brings back what was there, and every
#     import, promotion and cutover stays a separate reviewed command

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/deploy/lib.sh
. "$SCRIPT_DIR/lib.sh"

JURISTID_PROJECT="$JURISTID_PRODUCTION_PROJECT"
JURISTID_COMPOSE_FILE=""
SET_DIR=""
BACKUP_ROOT=""
DATA_ROOT=""
DB_NAME="juristid"
DB_USER="juristid"
DATABASE_ONLY=0

usage() {
  cat <<'USAGE'
Usage:
  juristid-restore.sh --compose-file PATH --set DIR --backup-root DIR
                      --data-root DIR [--project NAME] [--database-only]
                      [--db-name NAME] [--db-user NAME]

  --set            the backup set to restore (holds database.dump)
  --backup-root    where the evidence/ and legacy-source/ mirrors live
  --data-root      the appdata tree to restore them into
  --database-only  restore the database and leave the storage trees alone

Refuses a database that already contains a register. Restores nothing partially.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --project) JURISTID_PROJECT="${2:-}"; shift 2 ;;
    --compose-file) JURISTID_COMPOSE_FILE="${2:-}"; shift 2 ;;
    --set) SET_DIR="${2:-}"; shift 2 ;;
    --backup-root) BACKUP_ROOT="${2:-}"; shift 2 ;;
    --data-root) DATA_ROOT="${2:-}"; shift 2 ;;
    --db-name) DB_NAME="${2:-}"; shift 2 ;;
    --db-user) DB_USER="${2:-}"; shift 2 ;;
    --database-only) DATABASE_ONLY=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument '$1'" ;;
  esac
done

[ -n "$JURISTID_COMPOSE_FILE" ] || { usage >&2; die "--compose-file is required"; }
[ -n "$SET_DIR" ] || { usage >&2; die "--set is required"; }

require_known_project "$JURISTID_PROJECT"
require_file "$JURISTID_COMPOSE_FILE" "compose file"
require_directory "$SET_DIR" "backup set"
require_file "$SET_DIR/database.dump" "database dump"
require_command docker "the database is restored inside the deployment's own container"

if [ "$DATABASE_ONLY" -eq 0 ]; then
  [ -n "$BACKUP_ROOT" ] || { usage >&2; die "--backup-root is required unless --database-only"; }
  [ -n "$DATA_ROOT" ] || { usage >&2; die "--data-root is required unless --database-only"; }
  require_directory "$BACKUP_ROOT/evidence" "evidence mirror"
  require_directory "$BACKUP_ROOT/legacy-source" "legacy-source mirror"
  require_directory "$DATA_ROOT" "data root"
  require_command rsync "the evidence tree is restored with it"
fi

note "Juristid restore"
note "  project      $JURISTID_PROJECT"
note "  backup set   $SET_DIR"

# --------------------------------------------------------------------------
# 1. The set is intact before anything is written
# --------------------------------------------------------------------------

step "Verifying the set before restoring from it"
"$SCRIPT_DIR/juristid-verify-backup.sh" \
  --project "$JURISTID_PROJECT" \
  --compose-file "$JURISTID_COMPOSE_FILE" \
  --set "$SET_DIR"

# --------------------------------------------------------------------------
# 2. Refuse a database that still holds something
# --------------------------------------------------------------------------

step "Checking the target database is empty"

existing="$(juristid_compose exec -T db psql --no-password -U "$DB_USER" -d "$DB_NAME" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'" | tr -d '\r\n ')"

if [ "${existing:-0}" != "0" ]; then
  die "the database '$DB_NAME' already holds $existing table(s). Restoring over it would replace every row written since the dump, and nothing would bring those back. If that is genuinely what you intend, drop and recreate the database by hand with the runbook open — deploy/unraid-main/RECOVERY.md — and run this again."
fi

note "  the target database is empty"

# --------------------------------------------------------------------------
# 3. Evidence and page XML
# --------------------------------------------------------------------------

if [ "$DATABASE_ONLY" -eq 0 ]; then
  step "Evidence and page XML"
  mkdir -p "$DATA_ROOT/evidence" "$DATA_ROOT/legacy-source" "$DATA_ROOT/derivatives"

  # --ignore-existing, never --delete. Evidence is immutable, so an object that
  # is already there is the same object; and a restore that deletes is a restore
  # that can destroy data newer than the backup it came from.
  rsync -a --numeric-ids --ignore-existing "$BACKUP_ROOT/evidence/" "$DATA_ROOT/evidence/"
  rsync -a --numeric-ids --ignore-existing "$BACKUP_ROOT/legacy-source/" "$DATA_ROOT/legacy-source/"

  evidence_files="$(find "$DATA_ROOT/evidence" -type f | wc -l | tr -d ' ')"
  legacy_files="$(find "$DATA_ROOT/legacy-source" -type f | wc -l | tr -d ' ')"
  note "  evidence      $evidence_files files"
  note "  page XML      $legacy_files files"
  note ""
  note "  The application runs as uid 10001. If these were restored as another"
  note "  user, chown them to 10001:10001 before starting the web container —"
  note "  and never with a blanket chmod: the point of the ownership is that a"
  note "  process which should only read cannot write."
fi

# --------------------------------------------------------------------------
# 4. The database
# --------------------------------------------------------------------------

step "Database"

CONTAINER_PATH="/tmp/juristid-restore-$$.dump"
cleanup() {
  juristid_compose exec -T db rm -f "$CONTAINER_PATH" >/dev/null 2>&1 || true
}
trap cleanup EXIT

juristid_compose cp "$SET_DIR/database.dump" "db:$CONTAINER_PATH"

# --exit-on-error, so a restore stops at the first failure rather than
# continuing and reporting a count of errors at the end that nobody reads. A
# partially restored database is the failure mode this whole script is arranged
# against.
juristid_compose exec -T db pg_restore \
  --no-password \
  --username="$DB_USER" \
  --dbname="$DB_NAME" \
  --exit-on-error \
  "$CONTAINER_PATH" ||
  die "pg_restore failed. The database is now partially restored and must not be started against: drop it, recreate it, and restore again. Do not continue to a public cutover from here."

note "  restored"

# --------------------------------------------------------------------------
# 5. Hand back
# --------------------------------------------------------------------------

step "Not finished"
cat <<'NEXT'
The data is back. Nothing has been verified and nothing is published yet.

  1. prove the code matches the schema and the mounts:
       docker compose ... exec -T web python manage.py deployment_readiness

  2. prove the canonical state came back, against the fingerprint taken before
     the loss if there is one:
       docker compose ... exec -T web python manage.py recovery_fingerprint \
         --compare /path/to/fingerprint.json

  3. rebuild what was deliberately not backed up:
       docker compose ... exec -T web python manage.py rebuild_document_derivatives --all
       docker compose ... exec -T web python manage.py rebuild_search_index

  4. only then start the tunnel and let anybody in.

deploy/unraid-main/RECOVERY.md has the full sequence, including the parts this
script deliberately does not automate.
NEXT
