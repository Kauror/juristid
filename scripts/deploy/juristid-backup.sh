#!/usr/bin/env bash
#
# Take a complete, verifiable backup set: the database, the evidence tree and
# the OneNote page XML.
#
# WHAT THIS REPLACES
#
#   docker exec juristid-main-db pg_dump -U juristid juristid | gzip > out.sql.gz
#
# That line has one failure mode worth a script. If `pg_dump` dies halfway —
# disk full, connection dropped, the container restarted — `gzip` still succeeds
# on what it did receive, the redirect still produces a file, and the shell
# still reports success, because the exit status of a pipeline is the exit
# status of its last command. The result is a truncated dump that passes
# `gzip -t`, sits in the backup directory looking exactly like the good ones,
# and is discovered on the day it is needed.
#
# Two changes remove that. `set -o pipefail` plus `set -e` make an upstream
# failure fatal, and the custom format removes the pipeline altogether: pg_dump
# compresses on its own, so there is nothing downstream to mask anything. The
# custom format also buys `pg_restore --list`, which is the difference between
# "this file decompresses" and "this file contains the tables it should".
#
# LAYOUT
#
#   <backup-root>/evidence/         append-only mirror of the evidence tree
#   <backup-root>/legacy-source/    append-only mirror of the OneNote page XML
#   <backup-root>/sets/<stamp>/     database.dump, manifest.json, SHA256SUMS
#
# The two mirrors are shared rather than copied per set, because evidence is
# immutable and 4 GiB of it copied nightly fills a disk without adding a single
# recoverable byte. What each set records is which mirror state it belongs with.
#
# CONSISTENCY
#
# `pg_dump` is transactionally consistent with itself. It is not consistent with
# a filesystem someone is still writing to, and this system writes evidence
# bytes *before* the DocumentVersion row that describes them commits. So the
# mirror is synchronised twice, once before the dump and once after: any object
# whose row entered the dump had its bytes written before the dump began, so
# either the first pass or the second one has it. The reverse — an object in the
# mirror with no row — is harmless and already has a name: an orphan.
#
# That argument depends on evidence being append-only. It is: existing evidence
# is immutable through a database trigger, and deletion goes through legal-hold
# rules rather than through the filesystem (docs/adr/0003, docs/adr/0014).
#
# THIS SCRIPT NEVER DELETES ANYTHING. Not an old backup set, not a mirror entry,
# not a partial artifact belonging to another run. Retention is an operations
# decision nobody has taken yet (docs/open-decisions.md), and creating backups
# safely is the more urgent half.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

JURISTID_PROJECT="$JURISTID_PRODUCTION_PROJECT"
JURISTID_COMPOSE_FILE=""
DATA_ROOT=""
BACKUP_ROOT=""
DB_NAME="juristid"
DB_USER="juristid"
# Refuse rather than fill the disk. A dump that runs out of space mid-write is
# the failure this whole script exists to make impossible.
MINIMUM_FREE_MIB=2048
RUN_TOC_CHECK=1

usage() {
  cat <<'USAGE'
Usage:
  juristid-backup.sh --compose-file PATH --data-root DIR --backup-root DIR
                     [--project NAME] [--db-name NAME] [--db-user NAME]
                     [--minimum-free-mib N] [--no-toc-check]

  --compose-file   the deployment's compose.yml, named explicitly
  --data-root      the appdata tree holding evidence/ and legacy-source/
  --backup-root    where the mirrors and the backup sets live
  --project        juristid-main (default) or juristid-recovery-rehearsal
  --no-toc-check   skip the pg_restore --list pass (it needs the db container)

Exits non-zero on any failure, and leaves no artifact that could be mistaken
for a complete backup set.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --project) JURISTID_PROJECT="${2:-}"; shift 2 ;;
    --compose-file) JURISTID_COMPOSE_FILE="${2:-}"; shift 2 ;;
    --data-root) DATA_ROOT="${2:-}"; shift 2 ;;
    --backup-root) BACKUP_ROOT="${2:-}"; shift 2 ;;
    --db-name) DB_NAME="${2:-}"; shift 2 ;;
    --db-user) DB_USER="${2:-}"; shift 2 ;;
    --minimum-free-mib) MINIMUM_FREE_MIB="${2:-}"; shift 2 ;;
    --no-toc-check) RUN_TOC_CHECK=0; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument '$1'" ;;
  esac
done

[ -n "$JURISTID_COMPOSE_FILE" ] || { usage >&2; die "--compose-file is required"; }
[ -n "$DATA_ROOT" ] || { usage >&2; die "--data-root is required"; }
[ -n "$BACKUP_ROOT" ] || { usage >&2; die "--backup-root is required"; }

require_known_project "$JURISTID_PROJECT"
require_file "$JURISTID_COMPOSE_FILE" "compose file"
require_directory "$DATA_ROOT" "data root"
require_directory "$BACKUP_ROOT" "backup root"

EVIDENCE_SOURCE="$DATA_ROOT/evidence"
LEGACY_SOURCE="$DATA_ROOT/legacy-source"
EVIDENCE_MIRROR="$BACKUP_ROOT/evidence"
LEGACY_MIRROR="$BACKUP_ROOT/legacy-source"

# Paths before tools: an operator who pointed this at the wrong tree needs to be
# told that, not told to install something.
require_directory "$EVIDENCE_SOURCE" "evidence tree"
require_directory "$LEGACY_SOURCE" "legacy-source tree"

require_command docker "a Compose deployment cannot be backed up without it"
require_command rsync "the evidence mirror is built with it"

free_mib=$(( $(free_kib "$BACKUP_ROOT") / 1024 ))
if [ "$free_mib" -lt "$MINIMUM_FREE_MIB" ]; then
  die "only ${free_mib} MiB free at $BACKUP_ROOT; ${MINIMUM_FREE_MIB} MiB required. Refusing to start a backup that may not finish. Do not free space by deleting Docker objects on this host."
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SET_DIR="$BACKUP_ROOT/sets/$STAMP"
# Built under a name no reader would mistake for a finished set, and renamed
# only once everything in it has been written and checked. A crash therefore
# leaves something obviously incomplete rather than something plausible.
PARTIAL_DIR="$SET_DIR.partial"

[ -e "$SET_DIR" ] && die "a backup set already exists at $SET_DIR"
[ -e "$PARTIAL_DIR" ] && die "an unfinished backup set is already at $PARTIAL_DIR. Look at it before running again; this script never removes another run's work."

mkdir -p "$BACKUP_ROOT/sets" "$EVIDENCE_MIRROR" "$LEGACY_MIRROR"
mkdir "$PARTIAL_DIR"

note "Juristid backup"
note "  project      $JURISTID_PROJECT"
note "  data root    $DATA_ROOT"
note "  backup set   $SET_DIR"

# --------------------------------------------------------------------------
# A mount that is not there looks exactly like a tree with nothing in it
# --------------------------------------------------------------------------
#
# If the evidence bind mount is missing, the source directory is empty, rsync
# copies nothing, and every command in this script succeeds. That is the one way
# a backup can report success while recording that the Chamber has no evidence
# at all, so it is checked rather than assumed.

count_files() {
  find "$1" -type f 2>/dev/null | wc -l | tr -d ' '
}

sync_tree() {
  local label="$1" source="$2" mirror="$3"
  local source_count mirror_count
  source_count="$(count_files "$source")"
  mirror_count="$(count_files "$mirror")"

  if [ "$source_count" -eq 0 ] && [ "$mirror_count" -gt 0 ]; then
    die "$label at $source is empty but its mirror holds $mirror_count file(s). That is a missing mount, not an empty tree. Nothing has been changed."
  fi

  # No --delete, deliberately. These trees are append-only, so there is nothing
  # legitimate to delete, and a mirror that can delete is a mirror that can
  # propagate an accident at the speed of rsync.
  rsync -a --numeric-ids "$source/" "$mirror/"
}

step "Evidence and page XML, first pass"
sync_tree "evidence" "$EVIDENCE_SOURCE" "$EVIDENCE_MIRROR"
sync_tree "legacy-source" "$LEGACY_SOURCE" "$LEGACY_MIRROR"

# --------------------------------------------------------------------------
# The database
# --------------------------------------------------------------------------

step "Database"
DUMP="$PARTIAL_DIR/database.dump"

# `exec -T` so nothing allocates a TTY and mangles a binary stream. The password
# never appears: the connection is over the container's local socket as the
# database's own superuser, so there is nothing to pass and nothing to leak into
# a process list or a log.
juristid_compose exec -T db pg_dump \
  --format=custom \
  --no-password \
  --username="$DB_USER" \
  --dbname="$DB_NAME" >"$DUMP"

looks_like_custom_dump "$DUMP" || die "the dump at $DUMP is not a PostgreSQL custom-format archive. pg_dump failed and what it wrote is not a backup. The partial set is at $PARTIAL_DIR."

DUMP_BYTES="$(file_size_bytes "$DUMP")"
DUMP_SHA="$(sha256_of "$DUMP")"
note "  database.dump  ${DUMP_BYTES} bytes"
note "  sha256         ${DUMP_SHA}"

step "Evidence and page XML, second pass"
# Catches any object whose row entered the dump while the first pass was
# running. Safe to repeat because the objects are immutable.
sync_tree "evidence" "$EVIDENCE_SOURCE" "$EVIDENCE_MIRROR"
sync_tree "legacy-source" "$LEGACY_SOURCE" "$LEGACY_MIRROR"

# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------
#
# Non-secret metadata only: no passwords, no keys, no Cloudflare credential, no
# filenames from the corpus. Enough to make a restore deterministic — which set,
# which dump, which digest, which mirror state, which code.

step "Manifest"

EVIDENCE_FILES="$(count_files "$EVIDENCE_MIRROR")"
LEGACY_FILES="$(count_files "$LEGACY_MIRROR")"
EVIDENCE_BYTES="$(du -sk "$EVIDENCE_MIRROR" | awk '{ print $1 * 1024 }')"
LEGACY_BYTES="$(du -sk "$LEGACY_MIRROR" | awk '{ print $1 * 1024 }')"

# Best effort. The application's own view of itself is worth recording, and a
# database that cannot answer must not stop a dump that already succeeded.
APP_REVISION="$(juristid_compose exec -T web sh -c 'cat /app/GIT_SHA 2>/dev/null || true' 2>/dev/null | tr -d '\r\n' || true)"
PG_VERSION="$(juristid_compose exec -T db psql --no-password -U "$DB_USER" -d "$DB_NAME" -tAc 'SHOW server_version_num' 2>/dev/null | tr -d '\r\n' || true)"

cat >"$PARTIAL_DIR/manifest.json" <<MANIFEST
{
  "manifest_version": 1,
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "project": "$JURISTID_PROJECT",
  "application_revision": "${APP_REVISION:-unknown}",
  "postgresql_server_version_num": "${PG_VERSION:-unknown}",
  "database": {
    "format": "postgresql-custom",
    "file": "database.dump",
    "size_bytes": $DUMP_BYTES,
    "sha256": "$DUMP_SHA"
  },
  "evidence_mirror": {
    "path_relative_to_backup_root": "evidence",
    "file_count": $EVIDENCE_FILES,
    "total_bytes": $EVIDENCE_BYTES
  },
  "legacy_source_mirror": {
    "path_relative_to_backup_root": "legacy-source",
    "file_count": $LEGACY_FILES,
    "total_bytes": $LEGACY_BYTES
  },
  "not_included": [
    "derivatives — rebuildable from evidence",
    "search projection — rebuildable from the database",
    "secrets — the environment file and the tunnel credential are backed up separately, never here",
    "the historical source corpus — read-only input with its own recovery path"
  ]
}
MANIFEST

# Relative names, so the file verifies from inside the set wherever the set
# ends up — including on the machine it is restored to.
(
  cd "$PARTIAL_DIR"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum database.dump manifest.json
  else
    shasum -a 256 database.dump manifest.json
  fi
) >"$PARTIAL_DIR/SHA256SUMS"

# --------------------------------------------------------------------------
# Verify before claiming
# --------------------------------------------------------------------------

if [ "$RUN_TOC_CHECK" -eq 1 ]; then
  step "Structural verification"
  "$SCRIPT_DIR/juristid-verify-backup.sh" \
    --project "$JURISTID_PROJECT" \
    --compose-file "$JURISTID_COMPOSE_FILE" \
    --set "$PARTIAL_DIR"
fi

mv "$PARTIAL_DIR" "$SET_DIR"

step "Done"
note "Backup set:      $SET_DIR"
note "Evidence mirror: $EVIDENCE_MIRROR ($EVIDENCE_FILES files)"
note "Page XML mirror: $LEGACY_MIRROR ($LEGACY_FILES files)"
note ""
note "This is a local recovery copy. It protects against an operator mistake and"
note "a bad deployment. It is NOT disaster recovery until a copy of it lives on"
note "different hardware — see deploy/unraid-main/RECOVERY.md."
