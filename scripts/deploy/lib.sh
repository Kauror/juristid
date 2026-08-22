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
