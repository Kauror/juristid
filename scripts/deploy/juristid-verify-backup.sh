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
#            the schema that should be in it. Catches a dump that was taken
#            against the wrong database, or truncated before the data. Needs a
#            PostgreSQL 18 `pg_restore`, which is why it borrows the deployment's
#            own db container. This script, by default.
#
#   LEVEL 3  the set restores into a disposable database and the application
#            can read the register back out of it. Catches everything the first
#            two cannot, which is most of what actually goes wrong. That is the
#            rehearsal — `.github/workflows/ci.yml`, job `recovery`, and
#            deploy/unraid-main/RECOVERY.md — and it runs on synthetic data,
#            never on production.
#
# Level 1 and 2 say a file is intact. Only level 3 says a system comes back.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

JURISTID_PROJECT="$JURISTID_PRODUCTION_PROJECT"
JURISTID_COMPOSE_FILE=""
SET_DIR=""
LEVEL=2

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

  --set            a backup set directory (holding database.dump and SHA256SUMS)
  --level          1 = checksums only, 2 = also read the archive's contents
  --compose-file   required for level 2; the db container runs pg_restore
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --project) JURISTID_PROJECT="${2:-}"; shift 2 ;;
    --compose-file) JURISTID_COMPOSE_FILE="${2:-}"; shift 2 ;;
    --set) SET_DIR="${2:-}"; shift 2 ;;
    --level) LEVEL="${2:-}"; shift 2 ;;
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
