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
#   1. read what the set says it contains, and take the refusals that need no
#      tool at all — a corrupt membership list, storage that is not empty
#   2. verify the set (levels 1 and 2) — a restore from a corrupt archive
#      leaves a half-populated database that looks like a working one
#   3. refuse a database that still holds a register
#   4. evidence and page XML into the data root, never overwriting
#   5. the database
#   6. hand back to the operator, who verifies before anything is published
#
# Step 1 comes before docker and rsync are even required, and that is the same
# principle the backup states as "paths before tools": an operator who aimed
# this at storage that still holds something needs to be told that, not told to
# install something.
#
# Evidence first, so that the moment rows exist, their bytes already do. The
# copy uses --ignore-existing because evidence is immutable: an object already
# present is by definition the same object, and a restore that overwrites one is
# a restore that can destroy something newer than the backup.
#
# WHAT GETS COPIED, AND WHY THAT WAS WRONG
#
# `<backup-root>/evidence` is a shared byte pool holding every object any set
# has ever named. This script used to copy all of it, which is the same thing as
# restoring a set only while nothing has ever left the active tree.
#
# On 2026-09-12 something did. An operational reset emptied active evidence; the
# pool kept its 20068 pre-reset objects, because these pools never delete. The
# set taken that day is a true record of a deployment with no evidence at all,
# and restoring it reconstructed a database with no rows beside twenty thousand
# orphaned files — a state that has never existed.
#
# From manifest version 3 each set carries its own membership inventory, and
# this script copies the objects that inventory names and nothing else. An empty
# inventory restores nothing, which is the correct answer and not a special
# case: zero is an ordinary membership, and the same code path serves it.
#
# Sets at version 1 or 2 record no membership, and nothing can invent one for
# them after the fact. They keep the old pool-wide copy, said out loud — except
# where their own manifest shows a guard was relaxed for an empty tree, which is
# the one case where the pool-wide copy is known to be wrong.
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
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

JURISTID_PROJECT="$JURISTID_PRODUCTION_PROJECT"
JURISTID_COMPOSE_FILE=""
SET_DIR=""
BACKUP_ROOT=""
DATA_ROOT=""
DB_NAME="juristid"
DB_USER="juristid"
DATABASE_ONLY=0
ACCEPT_LEGACY_POOL=0

usage() {
  cat <<'USAGE'
Usage:
  juristid-restore.sh --compose-file PATH --set DIR --backup-root DIR
                      --data-root DIR [--project NAME] [--database-only]
                      [--db-name NAME] [--db-user NAME]
                      [--accept-legacy-pool-restore]

  --set            the backup set to restore (holds database.dump)
  --backup-root    where the evidence/ and legacy-source/ byte pools live
  --data-root      the appdata tree to restore them into
  --database-only  restore the database and leave the storage trees alone

  --accept-legacy-pool-restore
                   for a manifest version 1 or 2 set whose own manifest says a
                   tree was empty on purpose. Such a set records no membership,
                   so the only filesystem restore available copies the whole
                   shared pool — which for that tree is known to be wrong. The
                   flag says you have read that and want it anyway.

From manifest version 3 a set names its own objects and only those are copied.
Refuses a database that already contains a register, and refuses to restore a
version 3 set onto storage that is not empty. Restores nothing partially.
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
    --accept-legacy-pool-restore) ACCEPT_LEGACY_POOL=1; shift ;;
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
require_file "$SET_DIR/manifest.json" "manifest"

if [ "$DATABASE_ONLY" -eq 0 ]; then
  [ -n "$BACKUP_ROOT" ] || { usage >&2; die "--backup-root is required unless --database-only"; }
  [ -n "$DATA_ROOT" ] || { usage >&2; die "--data-root is required unless --database-only"; }
  require_directory "$BACKUP_ROOT/evidence" "evidence byte pool"
  require_directory "$BACKUP_ROOT/legacy-source" "legacy-source byte pool"
  require_directory "$DATA_ROOT" "data root"
fi

note "Juristid restore"
note "  project      $JURISTID_PROJECT"
note "  backup set   $SET_DIR"

# --------------------------------------------------------------------------
# 1. What this set says it contains
# --------------------------------------------------------------------------
#
# Read from the manifest with a scoped `sed` rather than a JSON parser, for the
# reason the verifier gives at length: `jq` is not on the Unraid host and a
# recovery script is the last place to acquire a dependency.

manifest_field() {
  sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p" "$SET_DIR/manifest.json" | head -n 1
}

manifest_string_in() {
  sed -n "/\"$1\"/,/}/p" "$SET_DIR/manifest.json" |
    sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1
}

MANIFEST_VERSION="$(manifest_field manifest_version)"
MANIFEST_VERSION="${MANIFEST_VERSION:-1}"

#: From this version a set names its own objects, and this script copies those
#: and nothing else.
readonly FIRST_MEMBERSHIP_MANIFEST=3

# Whether the operator relaxed the empty-tree guard when this set was taken. It
# is an audit note about a flag, not a measurement — a stale `--allow-empty` on
# a tree that had filled up again is recorded identically — so it is read for
# exactly one purpose: on a pre-membership set it marks the case where the only
# available filesystem restore is *known* to be wrong, rather than merely
# unproven.
RELAXED_TREES="$(sed -n 's/.*"empty_source_allowed_for"[[:space:]]*:[[:space:]]*\[\(.*\)\].*/\1/p' "$SET_DIR/manifest.json" | head -n 1)"

# The membership inventory of one pooled tree, named by the manifest and checked
# for shape before anything reads it as a list of destinations.
#
# This is called in a command substitution, so a `die` in here exits the
# subshell rather than the script — and the script stops anyway, because `set
# -e` fails the assignment that the substitution belongs to. The message has
# already reached stderr by then. Keep it an assignment: put this call in a
# condition or a pipeline and the refusal becomes a warning.
inventory_path_for() {
  local label="$1" object="$2" name
  name="$(manifest_string_in "$object" inventory_file)"
  [ -n "$name" ] ||
    die "this set says manifest version $MANIFEST_VERSION but names no membership inventory for $label. A set at this version states which objects are its own; this one cannot, so what it would restore is unknown."
  case "$name" in
    */* | . | ..)
      die "the manifest names '$name' as $label's membership inventory. That is a path, and a membership inventory is a plain file inside the set."
      ;;
  esac
  require_file "$SET_DIR/$name" "$label membership inventory"
  printf '%s' "$SET_DIR/$name"
}

# Before the set is verified, before a container is started, and before a single
# byte is written. These sets are trusted artifacts; the check is here because a
# restore turns each of these strings into a path it writes to, and a list that
# can leave its own tree is a list that can write anywhere.
require_sane_inventory() {
  local label="$1" inventory="$2" offender duplicates
  if offender="$(inventory_first_unsafe "$inventory")"; then
    die "$label's membership inventory names '$offender', which is not a relative path inside the tree. Nothing has been written. This set's membership is corrupt and it must not be restored from."
  fi
  # Assigned rather than tested inline, so that a `die` inside the measurement
  # stops the script through `set -e` instead of becoming an empty string that
  # the test then reports as a duplicate.
  duplicates="$(inventory_duplicate_count "$inventory")"
  [ "$duplicates" -eq 0 ] ||
    die "$label's membership inventory names at least one path more than once. The backup writes each path once, so this list was not written by it. Nothing has been written."
}

if [ "$DATABASE_ONLY" -eq 0 ]; then
  if [ "$MANIFEST_VERSION" -ge "$FIRST_MEMBERSHIP_MANIFEST" ]; then
    require_nul_tools
    EVIDENCE_INVENTORY="$(inventory_path_for evidence evidence_snapshot)"
    LEGACY_INVENTORY="$(inventory_path_for legacy-source legacy_source_snapshot)"

    require_sane_inventory evidence "$EVIDENCE_INVENTORY"
    require_sane_inventory legacy-source "$LEGACY_INVENTORY"

    # Exact membership is only exact onto storage that holds nothing else. The
    # refusal is deliberate and the alternative was considered: `rsync --delete`
    # would make any target exact, by deleting whatever it found — which is the
    # one thing a recovery script must never do to a tree it did not put there.
    # So this refuses and leaves the files alone, and the operator decides.
    for tree in evidence legacy-source; do
      present="$(count_files "$DATA_ROOT/$tree")"
      [ "$present" -eq 0 ] ||
        die "$DATA_ROOT/$tree already holds $present file(s). This set names exactly which objects belong in it, and that can only be reconstructed onto empty storage — anything already there would survive the restore and become part of a state this backup never described. Nothing has been changed. Move or remove those files deliberately, with deploy/unraid-main/RECOVERY.md open, and run this again. This script will not delete them for you."
    done
    unset tree present
  elif [ -n "$RELAXED_TREES" ] && [ "$ACCEPT_LEGACY_POOL" -eq 0 ]; then
    die "this set is manifest version $MANIFEST_VERSION and its manifest records that the empty-tree guard was relaxed for $RELAXED_TREES. A set at that version carries no membership, so the only filesystem restore available copies the whole shared pool — and for a tree the operator called empty that is known to produce objects the set never described. Restore the database alone with --database-only, or pass --accept-legacy-pool-restore if you have read deploy/unraid-main/RECOVERY.md and want the pool-wide copy anyway."
  fi
fi

require_command docker "the database is restored inside the deployment's own container"
[ "$DATABASE_ONLY" -eq 1 ] || require_command rsync "the evidence tree is restored with it"

# --------------------------------------------------------------------------
# 2. The set is intact before anything is written
# --------------------------------------------------------------------------

step "Verifying the set before restoring from it"
"$SCRIPT_DIR/juristid-verify-backup.sh" \
  --project "$JURISTID_PROJECT" \
  --compose-file "$JURISTID_COMPOSE_FILE" \
  --set "$SET_DIR"

# --------------------------------------------------------------------------
# 3. Refuse a database that still holds something
# --------------------------------------------------------------------------

step "Checking the target database is empty"

existing="$(juristid_compose exec -T db psql --no-password -U "$DB_USER" -d "$DB_NAME" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'" | tr -d '\r\n ')"

if [ "${existing:-0}" != "0" ]; then
  die "the database '$DB_NAME' already holds $existing table(s). Restoring over it would replace every row written since the dump, and nothing would bring those back. If that is genuinely what you intend, drop and recreate the database by hand with the runbook open — deploy/unraid-main/RECOVERY.md — and run this again."
fi

note "  the target database is empty"

# --------------------------------------------------------------------------
# 4. Evidence and page XML
# --------------------------------------------------------------------------

# One tree out of the shared pool and into the data root.
#
# For a set that names its members, `--files-from` drives the copy from the
# set's own list: rsync transfers those paths and looks at nothing else in the
# pool, so history the set never named cannot come back. `--from0` because the
# list is NUL-delimited, and `--files-from` implies `--relative`, which is what
# puts `aa/object.bin` back at `aa/object.bin` rather than flat.
#
# An empty list transfers nothing. That is the ordinary behaviour of an ordinary
# list, not a special case — which is deliberate, because a `if members == 0`
# branch would be a fix for one number rather than for the concept.
#
# --ignore-existing, never --delete. Evidence is immutable, so an object that is
# already there is the same object; and a restore that deletes is a restore that
# can destroy data newer than the backup it came from.
restore_tree() {
  local label="$1" inventory="$2" pool="$3" target="$4"

  if [ -n "$inventory" ]; then
    rsync -a --numeric-ids --ignore-existing --from0 --files-from="$inventory" \
      "$pool/" "$target/"
    note "  $label: $(inventory_count "$inventory") object(s), exactly the membership this set recorded"
  else
    rsync -a --numeric-ids --ignore-existing "$pool/" "$target/"
    note "  $label: the whole shared pool — manifest version $MANIFEST_VERSION records no membership, so this is every object the pool holds and not necessarily this set's"
  fi
}

if [ "$DATABASE_ONLY" -eq 0 ]; then
  step "Evidence and page XML"
  mkdir -p "$DATA_ROOT/evidence" "$DATA_ROOT/legacy-source" "$DATA_ROOT/derivatives"

  restore_tree "evidence" "${EVIDENCE_INVENTORY:-}" "$BACKUP_ROOT/evidence" "$DATA_ROOT/evidence"
  restore_tree "page XML" "${LEGACY_INVENTORY:-}" "$BACKUP_ROOT/legacy-source" "$DATA_ROOT/legacy-source"

  evidence_files="$(count_files "$DATA_ROOT/evidence")"
  legacy_files="$(count_files "$DATA_ROOT/legacy-source")"
  note ""
  note "  evidence      $evidence_files files"
  note "  page XML      $legacy_files files"
  note ""
  note "  The application runs as uid 10001. If these were restored as another"
  note "  user, chown them to 10001:10001 before starting the web container —"
  note "  and never with a blanket chmod: the point of the ownership is that a"
  note "  process which should only read cannot write."
fi

# --------------------------------------------------------------------------
# 5. The database
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
# 6. Hand back
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

  3. prove the restored store and database still describe each other. This is
     the different question: a fingerprint cannot see an object no row refers
     to. Structural only — --verify-sha reads the whole store:
       docker compose ... exec -T web python manage.py check_evidence_integrity

  4. rebuild what was deliberately not backed up:
       docker compose ... exec -T web python manage.py rebuild_document_derivatives --all
       docker compose ... exec -T web python manage.py rebuild_search_index

  5. only then start the tunnel and let anybody in.

deploy/unraid-main/RECOVERY.md has the full sequence, including the parts this
script deliberately does not automate.
NEXT
