#!/usr/bin/env bash
#
# Prove a backup set is what it claims to be, in three levels of increasing
# expense and increasing meaning. The levels exist because "the backup is
# verified" is said about all three and only the last one is worth much.
#
#   LEVEL 1  the files are there, non-empty, and hash to what the set recorded.
#            Catches truncation, silent corruption on the disk, and a set that
#            was copied off the host badly. Costs seconds. This script.
#
#   LEVEL 2  the dump is a PostgreSQL archive and its table of contents lists
#            the schema that should be in it, **and** the two mirrors still hold
#            what the manifest says they held. Catches a dump taken against the
#            wrong database or truncated before the data, and a mirror that has
#            been emptied, truncated or never copied. Needs a PostgreSQL 18
#            `pg_restore`, which is why it borrows the deployment's own db
#            container. This script, by default.
#
#   LEVEL 3  the set restores into a disposable database and the application
#            can read the register back out of it. Catches everything the first
#            two cannot, which is most of what actually goes wrong. That is the
#            rehearsal — `.github/workflows/ci.yml`, job `recovery`, and
#            deploy/unraid-main/RECOVERY.md — and it runs on synthetic data,
#            never on production.
#
# Level 1 and 2 say a file is intact. Only level 3 says a system comes back.
#
# WHAT THE MIRROR CHECK IS AND IS NOT
#
# It is a structural check: file counts and total bytes, read from the manifest
# the backup wrote and recomputed from the mirrors. It runs in seconds over
# ~20,000 files and it catches the failures that are actually plausible — a
# mirror never copied to a second location, a tree emptied by a cleanup, an
# rsync that stopped partway.
#
# It is not an integrity check. It does not hash a single evidence object, and
# it cannot: the evidence tree is ~7.4 GB, and a routine verification that reads
# all of it is a verification somebody switches off. Proving the bytes is
# `recovery_fingerprint` without `--skip-evidence-bytes`, and it stays a
# deliberate exercise (deploy/unraid-main/RECOVERY.md).
#
# Nor is a count equal to a manifest a statement that no object is *extra*: the
# backup deliberately synchronises the mirror twice around the dump, so a set
# may legitimately be verified against a mirror that has grown since. A mirror
# that has grown is reported and is not a failure; one that has shrunk is.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

JURISTID_PROJECT="$JURISTID_PRODUCTION_PROJECT"
JURISTID_COMPOSE_FILE=""
SET_DIR=""
BACKUP_ROOT=""
LEVEL=2
CHECK_MIRRORS=1

#: Tables the archive must mention. Few, and chosen because their absence means
#: something specific: no Matter is not the register, no DocumentVersion is no
#: evidence index, no django_migrations is a dump with no schema state and
#: nothing to check a restore against.
readonly REQUIRED_TABLES="matters_matter documents_documentversion django_migrations"

usage() {
  cat <<'USAGE'
Usage:
  juristid-verify-backup.sh --set DIR [--level 1|2]
                            [--compose-file PATH] [--project NAME]
                            [--backup-root DIR] [--no-mirror-check]

  --set              a backup set directory (holding database.dump and SHA256SUMS)
  --level            1 = checksums only, 2 = also read the archive's contents
                     and check the mirrors against the manifest
  --compose-file     required for level 2; the db container runs pg_restore
  --backup-root      where evidence/ and legacy-source/ live. Defaults to the
                     grandparent of --set, which is where the backup puts them.
  --no-mirror-check  verify only the set. For a set deliberately copied without
                     its mirrors — which is not a complete backup.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --project) JURISTID_PROJECT="${2:-}"; shift 2 ;;
    --compose-file) JURISTID_COMPOSE_FILE="${2:-}"; shift 2 ;;
    --set) SET_DIR="${2:-}"; shift 2 ;;
    --backup-root) BACKUP_ROOT="${2:-}"; shift 2 ;;
    --level) LEVEL="${2:-}"; shift 2 ;;
    --no-mirror-check) CHECK_MIRRORS=0; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument '$1'" ;;
  esac
done

[ -n "$SET_DIR" ] || { usage >&2; die "--set is required"; }
case "$LEVEL" in
  1 | 2) : ;;
  3) die "level 3 is the restore rehearsal, not a check on a file. See deploy/unraid-main/RECOVERY.md." ;;
  *) die "--level must be 1 or 2" ;;
esac

require_known_project "$JURISTID_PROJECT"
require_directory "$SET_DIR" "backup set"
require_file "$SET_DIR/database.dump" "database dump"
require_file "$SET_DIR/manifest.json" "manifest"
require_file "$SET_DIR/SHA256SUMS" "checksum list"

# The mirrors are shared between sets and live two levels up from one, which is
# where `juristid-backup.sh` puts them. Derived rather than required, so the
# ordinary call is unchanged; named explicitly when a set has been moved.
if [ -z "$BACKUP_ROOT" ]; then
  BACKUP_ROOT="$(cd -- "$(dirname -- "$SET_DIR")/.." && pwd)"
fi

# --------------------------------------------------------------------------
# Reading the manifest without a JSON parser
# --------------------------------------------------------------------------
#
# `jq` is not on the Unraid host and a backup verifier is the last place to
# acquire a dependency. The manifest is written by `juristid-backup.sh` in a
# fixed shape, one field per line, so a scoped `sed` is enough — and it is
# scoped: the object name is matched first, so `file_count` is read from the
# block it belongs to rather than from whichever block happens to come first.
manifest_field() {
  sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p"
}

# A number inside one named object. Scoped, so `file_count` is read from the
# block it belongs to rather than from whichever block comes first in the file.
manifest_number() {
  local object="$1" field="$2"
  sed -n "/\"$object\"/,/}/p" "$SET_DIR/manifest.json" | manifest_field "$field" | head -n 1
}

# A number at the top level, where there is no object to scope to.
MANIFEST_VERSION="$(manifest_field manifest_version <"$SET_DIR/manifest.json" | head -n 1)"

check_one_mirror() {
  local label="$1" object="$2" directory="$3"
  local recorded_files recorded_bytes actual_files actual_bytes

  recorded_files="$(manifest_number "$object" file_count)"
  recorded_bytes="$(manifest_number "$object" total_bytes)"

  if [ -z "$recorded_files" ]; then
    note "  $label: the manifest records no file count. Nothing to check."
    return 0
  fi

  [ -d "$directory" ] ||
    die "$label mirror not found at $directory. The manifest says this set belongs with $recorded_files file(s), so this is not a complete backup. Pass --backup-root if the mirrors live elsewhere, or --no-mirror-check if you meant to verify the set alone."

  actual_files="$(count_files "$directory")"
  actual_bytes="$(tree_bytes "$directory")"

  if [ "$actual_files" -lt "$recorded_files" ]; then
    die "$label mirror holds $actual_files file(s); this set was sealed against $recorded_files. Objects are missing. Do not treat this set as a backup of the evidence it names."
  fi

  # Growth is legitimate and expected. Evidence is append-only, the mirror is
  # shared between sets rather than copied per set, and every backup taken after
  # this one adds to it. An older set verified today is therefore *supposed* to
  # find more than it recorded.
  if [ "$actual_files" -gt "$recorded_files" ]; then
    note "  $label: $actual_files file(s), $((actual_files - recorded_files)) more than when this set was sealed (append-only; expected on an older set)."
  else
    note "  $label: $actual_files file(s), exactly as recorded."
  fi

  # Bytes, only where the number means the same thing on both sides. Manifest
  # version 1 recorded `du -sk`, which is allocated blocks and therefore a
  # property of the filesystem rather than of the data; comparing it against a
  # copy would fail on a good off-host set. Version 2 records the sum of file
  # sizes, which is the same number anywhere.
  if [ "${MANIFEST_VERSION:-1}" -lt 2 ]; then
    note "  $label: byte total not compared — manifest version ${MANIFEST_VERSION:-1} recorded allocated blocks, which are not comparable across filesystems."
    return 0
  fi
  if [ -z "$recorded_bytes" ]; then
    return 0
  fi
  if [ "$actual_bytes" -lt "$recorded_bytes" ]; then
    die "$label mirror holds $actual_bytes byte(s); this set was sealed against $recorded_bytes. The file count is right, so something was truncated in place rather than removed."
  fi
  note "  $label: $actual_bytes byte(s), at least the $recorded_bytes recorded."
}

check_mirrors() {
  check_one_mirror "evidence" "evidence_mirror" "$BACKUP_ROOT/evidence"
  check_one_mirror "legacy-source" "legacy_source_mirror" "$BACKUP_ROOT/legacy-source"
}

note "Verifying $SET_DIR (level $LEVEL)"

# -- level 1 ---------------------------------------------------------------

step "Level 1 — the files are intact"

looks_like_custom_dump "$SET_DIR/database.dump" ||
  die "database.dump is empty or is not a PostgreSQL custom-format archive."

(
  cd "$SET_DIR"
  # `--status` is GNU coreutils and `-s` is the Perl `shasum`; neither accepts
  # the other's spelling, and the wrong one fails the check by failing to parse
  # its own arguments — which reads, from the outside, exactly like a corrupt
  # backup.
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum --check --status SHA256SUMS
  else
    shasum -a 256 -c -s SHA256SUMS
  fi
) || die "checksums do not match. This set has changed since it was written; do not restore from it."

note "  checksums match"
note "  $(file_size_bytes "$SET_DIR/database.dump") bytes of custom-format archive"

if [ "$LEVEL" -lt 2 ]; then
  note ""
  note "Level 1 only. This proves the file is intact, not that it restores."
  exit 0
fi

# -- level 2 ---------------------------------------------------------------

# -- level 2, the mirrors --------------------------------------------------
#
# The manifest has always recorded what the two mirrors held when the set was
# sealed. Nothing read it back, so a set could pass every check it had while the
# evidence it depends on had been emptied — and the way that is discovered is by
# needing it (pilot backup/DR audit).
#
# First, and before the compose file is even required: it costs seconds, it
# needs nothing but the filesystem, and there is no reason to make somebody
# start a container to be told the evidence is missing.

if [ "$CHECK_MIRRORS" -eq 1 ]; then
  step "Level 2 — the mirrors still hold what the manifest recorded"
  check_mirrors
else
  note ""
  note "Mirror check skipped by request. This set is being verified as a file, not as a backup."
fi

[ -n "$JURISTID_COMPOSE_FILE" ] || die "--compose-file is required at level 2: pg_restore has to come from a PostgreSQL 18 image, and the deployment already has one."
require_file "$JURISTID_COMPOSE_FILE" "compose file"
require_command docker "level 2 reads the archive with the deployment's own pg_restore"

step "Level 2 — the archive contains the schema it should"

# Copied in rather than streamed: `pg_restore --list` seeks through a custom
# archive, and a pipe is not seekable. A dump of this database is tens of
# megabytes, so the copy is cheap and the alternative is a check that works
# until the archive grows past a buffer.
CONTAINER_PATH="/tmp/juristid-verify-$$.dump"
cleanup() {
  juristid_compose exec -T db rm -f "$CONTAINER_PATH" >/dev/null 2>&1 || true
}
trap cleanup EXIT

juristid_compose cp "$SET_DIR/database.dump" "db:$CONTAINER_PATH"

TOC="$(juristid_compose exec -T db pg_restore --list "$CONTAINER_PATH")" ||
  die "pg_restore could not read the archive. It is not restorable, whatever its checksum says."

# A here-string, not a pipe into `grep -q`. `grep -q` exits as soon as it
# matches, the writer upstream then dies on SIGPIPE, and `pipefail` reports the
# pipeline as failed — so the piped form returns failure exactly when the table
# is present, and this check declared every well-formed dump unrecognisable.
missing=""
for table in $REQUIRED_TABLES; do
  grep -q -- "$table" <<<"$TOC" || missing="$missing $table"
done
[ -z "$missing" ] || die "the archive's table of contents does not mention:$missing. This dump was not taken from a Juristid database, or it was taken before the schema existed."

entries="$(grep -c -v '^;' <<<"$TOC" || true)"
note "  pg_restore read $entries archive entries"
note "  every required table is present"

note ""
note "Levels 1 and 2 passed: this file is intact and contains the right schema."
note "Neither proves it restores. That is the rehearsal — see RECOVERY.md."
