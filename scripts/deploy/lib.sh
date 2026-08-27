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
#   2. The identity variables are exported once, before the build, so every
#      later command in that shell resolves the same image. Prefixing individual
#      commands is how `migrate` ends up resolving
#      `juristid-main-web:local` — Compose's fallback tag, the one hand-built
#      images overwrite — and a schema change made by an unreviewed build is the
#      failure this whole sequence exists to prevent.
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
  docker compose -p $project -f $compose_file build
  docker compose -p $project -f $compose_file run --rm web python manage.py migration_plan
  # Release-specific pre-migration audits, where the release note asks for one:
  # the same run --rm shape, for the same reason — the new release's check,
  # against the schema it has not migrated yet. Stop on a finding.
  scripts/deploy/juristid-backup.sh --project $project --compose-file $compose_file ...
  docker compose -p $project -f $compose_file run --rm web python manage.py migrate
  docker compose -p $project -f $compose_file up -d
  docker compose -p $project -f $compose_file exec -T web python manage.py deployment_readiness
  curl -s https://juristid.orgusaar.ee/healthz   # revision must equal $target
PLAN
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
