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
#            the schema that should be in it, **and** every object this set
#            names is present in the shared byte pool at the size it was sealed
#            against. Catches a dump taken against the wrong database or
#            truncated before the data, and a member that has been removed,
#            truncated or never copied. Needs a PostgreSQL 18 `pg_restore`,
#            which is why it borrows the deployment's own db container. This
#            script, by default.
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
# TWO QUESTIONS ABOUT THE SAME DIRECTORY
#
# `<backup-root>/evidence` is a shared byte pool: append-only, holding every
# object any set has ever named. Two different things are asked of it.
#
# **Is the pool still there?** File count and total bytes, recomputed and
# compared against what the manifest recorded beside this set. Fewer is a
# failure; more is expected, because the pool is shared and grows under every
# later backup.
#
# **Does this set still have its own objects?** From manifest version 3, each
# set carries a membership inventory naming the paths that were active when it
# was taken, and level 2 proves every one of them is a regular file in the pool
# and that they add up to exactly the byte total the set recorded. This is the
# question a restore actually depends on, and a pool-wide count cannot answer
# it: "the pool holds at least N files" is satisfiable entirely by history that
# has nothing to do with this set. A set naming zero objects beside a pool of
# twenty thousand is complete, and a set naming one object that is gone is not,
# however large the pool is.
#
# Neither is an integrity check. Nothing here hashes an evidence object, and it
# cannot: the evidence tree is ~7.4 GB, and a routine verification that reads
# all of it is a verification somebody switches off. What the byte total does
# catch without reading a byte is a member truncated in place, because
# membership is fixed and its sum therefore has to be exact. Proving the
# contents is `recovery_fingerprint` without `--skip-evidence-bytes`, and it
# stays a deliberate exercise (deploy/unraid-main/RECOVERY.md).

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
  --level            1 = checksums only, 2 = also read the archive's contents,
                     check the byte pools against the manifest, and prove every
                     object this set names is still in them
  --compose-file     required for level 2; the db container runs pg_restore
  --backup-root      where evidence/ and legacy-source/ live. Defaults to the
                     grandparent of --set, which is where the backup puts them.
  --no-mirror-check  verify only the set. For a set deliberately copied without
                     its byte pools — which is not a complete backup.
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

# A string inside one named object, scoped the same way.
manifest_string() {
  local object="$1" field="$2"
  sed -n "/\"$object\"/,/}/p" "$SET_DIR/manifest.json" |
    sed -n "s/.*\"$field\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1
}

# A number at the top level, where there is no object to scope to.
MANIFEST_VERSION="$(manifest_field manifest_version <"$SET_DIR/manifest.json" | head -n 1)"

#: From this version a set states its own membership and level 2 proves it.
#: Earlier sets record only the pool they sat beside, and nothing can turn that
#: into a membership after the fact.
readonly FIRST_MEMBERSHIP_MANIFEST=3

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

# --------------------------------------------------------------------------
# The set's own objects
# --------------------------------------------------------------------------
#
# Everything above is about the pool. This is about the set: the exact objects
# it named when it was sealed, each one proved present and whole.
#
# Order matters here and each step earns its place. The inventory is read for
# shape before it is read for content, so a corrupt one is refused rather than
# followed; the count is compared before the pool is touched, so a truncated
# inventory is a cheap failure; existence is proved before sizes are summed,
# because a sum has nothing useful to say about a file that is not there.
check_one_membership() {
  local label="$1" object="$2" pool="$3"
  local inventory_name inventory recorded_files recorded_bytes
  local actual_files actual_bytes duplicates offender

  inventory_name="$(manifest_string "$object" inventory_file)"
  [ -n "$inventory_name" ] ||
    die "the manifest says version $MANIFEST_VERSION but names no membership inventory for $label. A set at this version states which objects are its own; this one cannot, so what it would restore is unknown."

  # A name, not a path. The manifest is trusted operational metadata and this
  # still gets checked, because the alternative is a field that decides which
  # file the verifier opens.
  case "$inventory_name" in
    */* | . | ..)
      die "the manifest names '$inventory_name' as $label's membership inventory. That is a path, and a membership inventory is a plain file inside the set."
      ;;
  esac

  inventory="$SET_DIR/$inventory_name"
  require_file "$inventory" "$label membership inventory"

  if offender="$(inventory_first_unsafe "$inventory")"; then
    die "$label's membership inventory names '$offender', which is not a relative path inside the tree. Refusing to read any further from it: a restore follows this list, and a list that can leave its own tree is a list that can write anywhere."
  fi

  duplicates="$(inventory_duplicate_count "$inventory")"
  [ "$duplicates" -eq 0 ] ||
    die "$label's membership inventory names $duplicates path(s) more than once. Membership is a set, and the backup writes each path once — so this list was not written by the backup."

  recorded_files="$(manifest_number "$object" file_count)"
  recorded_bytes="$(manifest_number "$object" total_bytes)"
  actual_files="$(inventory_count "$inventory")"

  [ "$actual_files" -eq "${recorded_files:-0}" ] ||
    die "$label's membership inventory names $actual_files object(s); the manifest says this set has ${recorded_files:-0}. The two halves of the set disagree about what it contains."

  [ -d "$pool" ] ||
    die "$label byte pool not found at $pool. This set names ${recorded_files:-0} object(s) that have to come from it. Pass --backup-root if the pools live elsewhere, or --no-mirror-check to verify the set as a file rather than as a backup."

  if offender="$(inventory_first_missing "$pool" "$inventory")"; then
    die "$label member '$offender' belongs to this set and is not a regular file in $pool. The set cannot restore what it names; do not treat it as a backup of that object."
  fi

  # Exactly equal, unlike the pool's total. The pool grows; membership does not,
  # so a member that has been truncated in place shows up here as arithmetic.
  actual_bytes="$(inventory_selected_bytes "$pool" "$inventory")"
  if [ -n "$recorded_bytes" ] && [ "$actual_bytes" -ne "$recorded_bytes" ]; then
    die "$label's $actual_files member(s) come to $actual_bytes byte(s) in $pool; this set was sealed against $recorded_bytes. Every object is present, so something was changed in place rather than removed."
  fi

  note "  $label: $actual_files member(s), $actual_bytes byte(s), every one present."
}

check_mirrors() {
  check_one_mirror "evidence" "evidence_mirror" "$BACKUP_ROOT/evidence"
  check_one_mirror "legacy-source" "legacy_source_mirror" "$BACKUP_ROOT/legacy-source"
}

check_membership() {
  if [ "${MANIFEST_VERSION:-1}" -lt "$FIRST_MEMBERSHIP_MANIFEST" ]; then
    note ""
    note "  Manifest version ${MANIFEST_VERSION:-1} records no membership, only the pool this set was sealed beside. What a full restore from it would write is whatever the pool holds on the day — which is the same thing only while nothing has ever left the active tree. See deploy/unraid-main/RECOVERY.md."
    return 0
  fi
  require_nul_tools
  check_one_membership "evidence" "evidence_snapshot" "$BACKUP_ROOT/evidence"
  check_one_membership "legacy-source" "legacy_source_snapshot" "$BACKUP_ROOT/legacy-source"
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
  step "Level 2 — the byte pools still hold what the manifest recorded"
  check_mirrors
  step "Level 2 — every object this set names is still in the pool"
  check_membership
else
  note ""
  note "Pool and membership checks skipped by request. This set is being verified as a file, not as a backup: nothing here says the objects it names still exist."
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
