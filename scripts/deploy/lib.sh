#!/usr/bin/env bash
# Shared guards for the deployment and recovery scripts.
#
# Sourced, never run. Everything here is about one class of accident: a correct
# command aimed at the wrong stack. This host runs `juristid-test` with invented
# data and several unrelated services, and a Compose command that relies on the
# current directory to find its project will happily operate on whichever one it
# discovers.
#
# So every script here takes the project and the Compose file explicitly, and
# refuses any project it does not recognise. `juristid-test` is named and
# refused rather than merely unmatched, because that is the one somebody
# actually types by mistake.

# The two projects these scripts may operate on, and nothing else.
readonly JURISTID_PRODUCTION_PROJECT="juristid-main"
readonly JURISTID_REHEARSAL_PROJECT="juristid-recovery-rehearsal"

# Declared here, set by whichever script sourced this. Every Compose invocation
# below reads them, so naming them in one place is what makes "the project and
# the file are never implicit" a property of the library rather than a habit of
# four separate scripts.
JURISTID_PROJECT="${JURISTID_PROJECT:-}"
JURISTID_COMPOSE_FILE="${JURISTID_COMPOSE_FILE:-}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

note() {
  printf '%s\n' "$*"
}

step() {
  printf '\n== %s\n' "$*"
}

require_known_project() {
  local project="$1"
  case "$project" in
    "$JURISTID_PRODUCTION_PROJECT" | "$JURISTID_REHEARSAL_PROJECT")
      return 0
      ;;
    juristid-test)
      die "refusing to touch juristid-test. It is the synthetic rehearsal, it must keep running, and nothing in this directory has any business operating on it."
      ;;
    *)
      die "unknown Compose project '$project'. These scripts operate on $JURISTID_PRODUCTION_PROJECT or $JURISTID_REHEARSAL_PROJECT and refuse everything else."
      ;;
  esac
}

require_file() {
  [ -f "$1" ] || die "$2 not found: $1"
}

require_directory() {
  [ -d "$1" ] || die "$2 not found: $1"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "$1 is not installed, and $2"
}

# Every Compose invocation in these scripts goes through here, so the project
# and the file are never implicit and never optional.
juristid_compose() {
  docker compose -p "$JURISTID_PROJECT" -f "$JURISTID_COMPOSE_FILE" "$@"
}

# The deployment sequence, in one place, because it is a safety property rather
# than a convenience.
#
# Two things about it are load-bearing and both were once wrong here:
#
#   1. `migration_plan` runs `run --rm web`, never `exec -T web`. Application
#      source is COPYed into the image and the production stack bind-mounts no
#      source into /app, so `exec` enters the *previously deployed* code. Moving
#      the checkout changes nothing inside a running container, and the old
#      image's migration graph does not contain the new release's migrations —
#      so it can answer "No pending migrations." for a release that carries
#      several. The reassuring answer, at the one moment it must be trustworthy.
#
#   2. The identity variables are exported once, before the first command that
#      resolves the release image, so every later command in that shell resolves
#      the same one. Prefixing individual commands is how `migrate` ends up
#      resolving `juristid-main-web:local` — Compose's fallback tag, the one a
#      hand-built image overwrites — and a schema change made by an unreviewed
#      build is the failure this whole sequence exists to prevent.
#
# And one thing the sequence deliberately does not contain: a build. The
# release image is built off the host by `.github/workflows/release-image.yml`
# for exactly one reviewed commit, carried over as an archive with its SHA-256
# and manifest, checked, and `docker load`ed under `juristid-main-web:<sha12>`
# before this plan is followed (deploy/unraid-main/README.md, "Deploying a
# release"). The host's image operations are `docker load` and `docker compose
# up`, nothing else: its writable Docker storage sits behind a USB parity disk
# and BuildKit has died mid-build on it. So the plan proves the loaded image is
# the target commit, and the replacement says `--no-build` — `compose.yml`
# still carries a `build:` stanza for CI, and the command rather than the
# operator's memory is what keeps this host from using it.
#
# `deployment_readiness` stays `exec`, and that is not an inconsistency: it asks
# about the process now serving, which by then is the new image. The rule is
# that the command is aimed at whichever image the question is about.
#
# Printed, never run. This function emits text; the operator runs it.
deployment_plan() {
  local project="$1" compose_file="$2" repo="$3" target="$4"

  # One command per line. A printed backslash continuation is consumed by the
  # heredoc itself rather than reaching the terminal, so it produced a joined
  # line the operator could copy but not read.
  cat <<PLAN
  curl -s https://juristid.orgusaar.ee/healthz   # write down the revision now serving
  git -C $repo checkout --detach $target
  export JURISTID_GIT_SHA=$target
  export JURISTID_IMAGE_TAG=${target:0:12}
  # The release image was built off-host for this commit and docker-loaded here
  # already (README, "Deploying a release"). Nothing below builds one. This line
  # must print $target, or stop: it is the image run --rm resolves next.
  docker run --rm --entrypoint cat juristid-main-web:${target:0:12} /app/GIT_SHA
  docker compose -p $project -f $compose_file run --rm web python manage.py migration_plan
  # Release-specific pre-migration audits, where the release note asks for one:
  # the same run --rm shape, for the same reason — the new release's check,
  # against the schema it has not migrated yet. Stop on a finding.
  scripts/deploy/juristid-backup.sh --project $project --compose-file $compose_file ...
  docker compose -p $project -f $compose_file run --rm web python manage.py migrate
  docker compose -p $project -f $compose_file up -d --no-build
  docker compose -p $project -f $compose_file exec -T web python manage.py deployment_readiness
  curl -s https://juristid.orgusaar.ee/healthz   # revision must equal $target
PLAN
}

# How many files a tree holds, and how many bytes they add up to.
#
# Shared, because the backup writes these two numbers into the manifest and the
# verifier now reads them back — and two implementations of "how big is this
# mirror" is how a check ends up disagreeing with the thing it is checking.
#
# `tree_bytes` sums the files' own sizes rather than asking `du`. `du` answers
# in allocated blocks, which is a property of the filesystem the tree happens to
# sit on: the same mirror copied to a destination with a different block size
# reports a different number, and a verifier comparing those would fail on a
# perfectly good off-host copy. The sum of file sizes is the same everywhere.
#
# `ls -ln` rather than `find -printf`, which is GNU-only, and in batches rather
# than one process per file, because these trees hold tens of thousands of
# objects. Column five is the size whatever the name contains.
#
# A directory that is not there holds no files, and says so with status 0. That
# is not pedantry: `find missing | wc -l` prints `0` and *fails*, `pipefail`
# carries the failure out to the command substitution every caller assigns from,
# and `set -e` then kills the script with no message at all. The restore asks
# this about a storage tree that a destroyed deployment has not recreated yet,
# so the answer has to be a number rather than a silent exit.
#
# A directory that exists and cannot be read still fails, which is the
# distinction worth keeping: absent is zero, unreadable is an error. Both
# functions carry the guard, because both are `find | …` and both are read
# through a command substitution.
count_files() {
  [ -d "$1" ] || { printf '0\n'; return 0; }
  find "$1" -type f 2>/dev/null | wc -l | tr -d ' '
}

tree_bytes() {
  [ -d "$1" ] || { printf '0\n'; return 0; }
  find "$1" -type f -exec ls -ln {} + 2>/dev/null | awk '$5 ~ /^[0-9]+$/ { total += $5 } END { print total + 0 }'
}

# Free space in KiB at a path, for a script that would rather refuse than write
# half a dump.
free_kib() {
  df -Pk "$1" | awk 'NR == 2 { print $4 }'
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{ print $1 }'
  else
    shasum -a 256 "$1" | awk '{ print $1 }'
  fi
}

file_size_bytes() {
  wc -c <"$1" | tr -d ' '
}

# A PostgreSQL custom-format archive begins with these five bytes. Cheap, and it
# separates a real dump from the two things a failed backup actually produces:
# an empty file, and a file containing an error message.
readonly PGDMP_MAGIC="PGDMP"

looks_like_custom_dump() {
  local path="$1"
  [ -s "$path" ] || return 1
  [ "$(head -c 5 "$path")" = "$PGDMP_MAGIC" ]
}

# --------------------------------------------------------------------------
# Set-local membership inventories
# --------------------------------------------------------------------------
#
# The mirrors under a backup root are a BYTE POOL: shared between every set,
# append-only, and holding every object that has ever existed. They are not a
# statement about any one set, and for a long time nothing else was, which made
# "restore this set" mean "copy the pool" — a distinction with no difference
# while the pool and the live tree were the same thing.
#
# The 2026-09-12 operational reset made them different things. Active evidence
# went to zero while the pool kept 20068 pre-reset objects, so restoring the
# clean set reconstructed a deployment with no rows and twenty thousand orphaned
# files. The set had no way to say which objects were its own.
#
# So each set now carries one MEMBERSHIP INVENTORY per shared tree: the exact
# relative paths that were active when the set was taken. The bytes stay in the
# pool, once; the membership is written down per set, and it is the inventory
# rather than the pool that a restore reads.
#
# The format is NUL-delimited relative paths, sorted. NUL because it is the one
# byte a Unix filename cannot contain, so a list delimited by it is right for
# exactly the names — spaces, tabs, newlines — that a line-based list gets
# wrong. Sorted so that the same tree produces the same file, which is what
# makes the inventory's own checksum mean something.

#: What a membership inventory is called inside a set. The suffix says the
#: format: NUL-separated ("files0", after `find -print0` and `rsync --from0`).
readonly INVENTORY_SUFFIX=".files0"

inventory_name_for() {
  printf '%s%s' "$1" "$INVENTORY_SUFFIX"
}

# `sort -z` and `xargs -0 -r` are what make a NUL-delimited list survive being
# read back. Both are GNU, both are on this host, on the CI runner and in the
# Git for Windows toolchain — and a system without them has to be told so,
# because the alternative is an inventory that is silently unordered or a byte
# total silently taken over the wrong thing.
require_nul_tools() {
  printf 'b\0a\0' | LC_ALL=C sort -z >/dev/null 2>&1 ||
    die "this sort does not support -z. A membership inventory is NUL-delimited because a filename may contain any byte but NUL; writing one without it would produce a list that is wrong for exactly the names it exists to survive."
  printf '' | xargs -0 -r true >/dev/null 2>&1 ||
    die "this xargs does not support -0 -r. Without -r an empty inventory would run 'ls' with no arguments, which lists the whole directory — so an empty set would measure the entire byte pool and call it its own."
}

# The active membership of one tree, written where the set can carry it.
#
# Taken from inside the tree, so every entry is relative to it: an inventory
# that recorded absolute paths would be a set that only restores onto the host
# it came from, and the point of a set is that it restores somewhere else.
capture_inventory() {
  local source="$1" destination="$2" entry
  ( cd -- "$source" && find . -type f -print0 ) |
    LC_ALL=C sort -z |
    while IFS= read -r -d '' entry; do
      printf '%s\0' "${entry#./}"
    done >"$destination"
}

# How many paths an inventory names. Counting NUL bytes rather than lines is
# the whole reason for the format: a filename containing a newline would make
# `wc -l` disagree with every consumer of the same file.
#
# The readability guard is not ceremony. `tr <missing | wc -c` prints `0` — the
# shell reports the failed redirect on stderr and the pipeline's last command
# succeeds on no input — so an unreadable inventory would otherwise measure as
# a set that names nothing, which is the one answer that must never be guessed.
inventory_count() {
  [ -r "$1" ] || die "membership inventory cannot be read: $1"
  tr -dc '\000' <"$1" | wc -c | tr -d ' '
}

# How many of those paths are repeats. A set's membership is a set; a list that
# names one object twice was not written by the backup.
inventory_duplicate_count() {
  local total unique
  total="$(inventory_count "$1")"
  unique="$(LC_ALL=C sort -zu <"$1" | tr -dc '\000' | wc -c | tr -d ' ')"
  # Either side coming back empty means the file could not be read partway
  # through, and the subtraction below would turn that into a confident wrong
  # statement about duplicates — a misleading refusal at the one moment the
  # message is what an operator has to act on.
  { [ -n "$total" ] && [ -n "$unique" ]; } ||
    die "membership inventory could not be measured: $1"
  printf '%s' "$(( total - unique ))"
}

# The first entry that must never be acted on, printed, with status 0 when there
# was one.
#
# Backup sets are trusted operational artifacts, and this check is here anyway:
# a restore reads these paths and writes to them, so a corrupt inventory must
# not become arbitrary filesystem writes. Everything an inventory may contain is
# a plain relative path inside its own tree.
inventory_first_unsafe() {
  local entry
  while IFS= read -r -d '' entry; do
    case "$entry" in
      "") printf '(an empty path)'; return 0 ;;
      /*) printf '%s' "$entry"; return 0 ;;
      .. | ../* | */.. | */../*) printf '%s' "$entry"; return 0 ;;
      . | ./* | */. | */./*) printf '%s' "$entry"; return 0 ;;
      *) : ;;
    esac
  done <"$1"
  return 1
}

# The first member that is not a regular file in the tree, printed, with status
# 0 when there was one. `[ -f ]` is a builtin, so this walks tens of thousands
# of entries without forking once.
inventory_first_missing() {
  local tree="$1" inventory="$2" entry
  while IFS= read -r -d '' entry; do
    [ -f "$tree/$entry" ] || { printf '%s' "$entry"; return 0; }
  done <"$inventory"
  return 1
}

# What the members add up to, in the tree they are being checked against.
#
# Only the listed files, which is the entire point: the pool around them may
# hold any amount of history, and a total that included it would be satisfied by
# objects this set never named. Batched through `xargs`, for the same reason
# `tree_bytes` is, and `-r` so an empty inventory measures nothing at all.
#
# Call `inventory_first_missing` first. This one is arithmetic over `ls`, and it
# has nothing useful to say about an entry that is not there.
#
# The inventory is redirected into the *subshell* rather than into `xargs`, so
# it is opened in the caller's directory before the `cd` rather than after it.
# Attached to the inner command, a relative --backup-root would be resolved
# against the tree being measured and the file would not be found.
inventory_selected_bytes() {
  local tree="$1" inventory="$2"
  ( cd -- "$tree" && xargs -0 -r ls -ln ) <"$inventory" |
    awk '$5 ~ /^[0-9]+$/ { total += $5 } END { print total + 0 }'
}
