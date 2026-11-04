#!/usr/bin/env bash
#
# Inventory, and on request remove, superseded Juristid release-image ARCHIVES
# on the host: the `juristid-main-web-<sha12>.tar.gz` files (and their
# `.sha256` sidecars) that each release kit copies onto the host and nothing
# ever deletes.
#
# WHY THIS EXISTS
#
# The host's daily `prune_docker.sh` already keeps only the newest four
# `juristid-main-web` IMAGES (plus whatever a container runs). The archive each
# image was loaded from is never pruned, so by November 2026 the host held 118
# of them (about 18 GiB) for images Docker had long since removed. This script
# applies the same policy to the archives: an archive is kept while its image
# is still kept, and is a removal candidate once it is not.
#
# WHAT IT WILL NEVER TOUCH
#
# Only regular files whose basename is exactly
# `juristid-main-web-<12 hex>.tar.gz` or `…tar.gz.sha256`, directly inside a
# fixed list of release directories under --data-root. Never a symlink, never a
# directory, never a kit's logs or scripts, never anything under evidence,
# derivatives, postgres, legacy-source, config, l100, quarantine or the backup
# root, never a Docker image or volume, never another application's files.
#
# PROTECTED (never planned for removal)
#
#   * the tag of every running `juristid-main-web` container;
#   * every `juristid-main-web` tag still present in Docker (the prune window);
#   * every SHA named by CURRENT_*/PREVIOUS_*/ROLLBACK_* lines in the
#     `release.env` of a protected release's kit (the rollback target);
#   * every --keep <sha>.
# If no running `juristid-main-web` container is found the plan is refused:
# without the current release the policy has nothing to anchor to.
#
# MODES
#
#   inventory (default)  read-only; one TSV line per archive: class, sha, bytes,
#                        mtime, path. Writes nothing.
#   plan --out FILE      read-only apart from FILE: the removal candidates, and
#                        the sha256 of FILE that `apply` must be given back.
#   apply --plan FILE --expect-sha256 HEX --log FILE
#                        takes the host lock, re-derives the protected set,
#                        re-checks EVERY line (path shape, regular file, no
#                        symlink, same size and mtime, sha not protected now)
#                        and refuses the whole plan if any line fails; then
#                        removes the files and appends one line per file to
#                        --log. Idempotent: a file already gone is logged as
#                        such, not an error.
#
# EXIT STATUS: 0 done, 1 refused or failed (nothing removed by a refusal).

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../deploy/lib.sh
. "$SCRIPT_DIR/../deploy/lib.sh"

IMAGE_REPO="juristid-main-web"
ARCHIVE_RE="^${IMAGE_REPO}-[0-9a-f]{12}\\.tar\\.gz(\\.sha256)?$"
PLAN_HEADER="# juristid-release-artifacts plan v1"

#: Directories (relative to --data-root) whose archives this script manages,
#: each scanned at depth 1 and in its immediate subdirectories (kits). The data
#: root itself is scanned at depth 1 only.
RELEASE_DIRS="releases deploy-staging release-images release-staging operator/releases"

MODE="inventory"
DATA_ROOT="/mnt/user/appdata/juristid-main"
OUT=""
PLAN=""
EXPECT_SHA=""
LOG=""
EXTRA_KEEP=""

usage() {
  sed -n '2,52p' "$0" | sed 's/^# \{0,1\}//'
}

if [ $# -gt 0 ] && [[ "$1" != --* ]]; then
  MODE="$1"
  shift
fi
while [ $# -gt 0 ]; do
  case "$1" in
    --data-root) DATA_ROOT="${2:?}"; shift 2 ;;
    --out) OUT="${2:?}"; shift 2 ;;
    --plan) PLAN="${2:?}"; shift 2 ;;
    --expect-sha256) EXPECT_SHA="${2:?}"; shift 2 ;;
    --log) LOG="${2:?}"; shift 2 ;;
    --keep) EXTRA_KEEP="$EXTRA_KEEP ${2:?}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

case "$MODE" in inventory|plan|apply) ;; *) die "unknown mode: $MODE (inventory|plan|apply)" ;; esac
require_command docker "this script reads the image list"
require_command sha256sum "plans are checksummed"
require_command stat "sizes are compared"
require_directory "$DATA_ROOT" "data root"
[ -L "$DATA_ROOT" ] && die "refusing: --data-root is a symlink: $DATA_ROOT"
DATA_ROOT="$(cd -- "$DATA_ROOT" && pwd -P)"

short() { printf '%s' "${1:0:12}"; }

running_tags() {
  docker ps --format '{{.Image}}' | sed -n "s#^${IMAGE_REPO}:\\([0-9a-f]\\{12,40\\}\\)\$#\\1#p" \
    | while read -r t; do short "$t"; printf '\n'; done | sort -u
}

present_tags() {
  docker images "$IMAGE_REPO" --format '{{.Tag}}' | grep -E '^[0-9a-f]{12,40}$' \
    | while read -r t; do short "$t"; printf '\n'; done | sort -u || true
}

rollback_tags_for() {
  local tag="$1" env
  for env in "$DATA_ROOT"/releases/"$tag"*/release.env; do
    [ -f "$env" ] && [ ! -L "$env" ] || continue
    { grep -E '^(CURRENT|PREVIOUS|ROLLBACK)[A-Z_]*=' "$env" || true; } | { grep -oE '[0-9a-f]{12,40}' || true; } \
      | while read -r t; do short "$t"; printf '\n'; done
  done
  return 0
}

PROTECTED=""
RUNNING=""
derive_protected() {
  RUNNING="$(running_tags)"
  local base t
  base="$(printf '%s\n%s\n' "$RUNNING" "$(present_tags)")"
  for t in $EXTRA_KEEP; do base="$base"$'\n'"$(short "$t")"; done
  PROTECTED="$base"
  for t in $base; do PROTECTED="$PROTECTED"$'\n'"$(rollback_tags_for "$t")"; done
  PROTECTED="$(printf '%s\n' "$PROTECTED" | grep -E '^[0-9a-f]{12}$' | sort -u || true)"
}

is_protected() { printf '%s\n' "$PROTECTED" | grep -qx "$1"; }

archive_files() {
  local d
  find "$DATA_ROOT" -mindepth 1 -maxdepth 1 -type f
  for d in $RELEASE_DIRS; do
    [ -d "$DATA_ROOT/$d" ] && [ ! -L "$DATA_ROOT/$d" ] || continue
    find "$DATA_ROOT/$d" -mindepth 1 -maxdepth 2 -type f
  done
}

list_archives() {
  archive_files | while IFS= read -r path; do
    base="${path##*/}"
    [[ "$base" =~ $ARCHIVE_RE ]] || continue
    [ -L "$path" ] && continue
    tag="${base#"${IMAGE_REPO}"-}"
    tag="${tag:0:12}"
    read -r bytes mtime < <(stat -c '%s %Y' -- "$path")
    printf '%s\t%s\t%s\t%s\n' "$tag" "$bytes" "$mtime" "$path"
  done | sort -t$'\t' -k4
}

classify() {
  local tag="$1"
  if printf '%s\n' "$RUNNING" | grep -qx "$tag"; then printf 'KEEP-RUNNING'
  elif is_protected "$tag"; then printf 'KEEP-PROTECTED'
  else printf 'REMOVE'
  fi
}

derive_protected

case "$MODE" in
  inventory)
    printf 'class\tsha\tbytes\tmtime\tpath\n'
    total=0; removable=0
    while IFS=$'\t' read -r tag bytes mtime path; do
      class="$(classify "$tag")"
      printf '%s\t%s\t%s\t%s\t%s\n' "$class" "$tag" "$bytes" "$(date -u -d "@$mtime" +%FT%TZ)" "$path"
      total=$((total + bytes))
      [ "$class" = REMOVE ] && removable=$((removable + bytes))
    done < <(list_archives)
    printf '# protected: %s\n' "$(printf '%s' "$PROTECTED" | tr '\n' ' ')" >&2
    printf '# archive bytes: %s total, %s removable\n' "$total" "$removable" >&2
    [ -n "$RUNNING" ] || printf '# WARNING: no running %s container; plan/apply would refuse\n' "$IMAGE_REPO" >&2
    ;;

  plan)
    [ -n "$OUT" ] || die "plan needs --out FILE"
    [ -n "$RUNNING" ] || die "refusing: no running ${IMAGE_REPO} container, so the current release is unknown"
    {
      printf '%s\n' "$PLAN_HEADER"
      printf '# data_root=%s\n' "$DATA_ROOT"
      printf '# generated=%s\n' "$(date -u +%FT%TZ)"
      printf '# protected=%s\n' "$(printf '%s' "$PROTECTED" | tr '\n' ',' | sed 's/,$//')"
      while IFS=$'\t' read -r tag bytes mtime path; do
        [ "$(classify "$tag")" = REMOVE ] || continue
        printf 'REMOVE\t%s\t%s\t%s\t%s\n' "$tag" "$bytes" "$mtime" "$path"
      done < <(list_archives)
    } > "$OUT"
    count="$(grep -c '^REMOVE' "$OUT" || true)"
    bytes="$(awk -F'\t' '/^REMOVE/ {s+=$3} END {print s+0}' "$OUT")"
    note "plan: $OUT"
    note "files: $count  bytes: $bytes"
    note "sha256: $(sha256sum < "$OUT" | cut -d' ' -f1)"
    ;;

  apply)
    [ -n "$PLAN" ] && [ -f "$PLAN" ] || die "apply needs --plan FILE"
    [ -n "$EXPECT_SHA" ] || die "apply needs --expect-sha256 (printed by plan)"
    [ -n "$LOG" ] || die "apply needs --log FILE"
    actual="$(sha256sum < "$PLAN" | cut -d' ' -f1)"
    [ "$actual" = "$EXPECT_SHA" ] || die "refusing: plan changed since it was reviewed (sha256 $actual)"
    [ "$(head -1 "$PLAN")" = "$PLAN_HEADER" ] || die "refusing: not a release-artifacts plan"
    grep -qx "# data_root=$DATA_ROOT" "$PLAN" || die "refusing: plan was made for a different data root"
    [ -n "$RUNNING" ] || die "refusing: no running ${IMAGE_REPO} container"

    take_host_lock "juristid-release-artifacts" || die "host lock held: $(host_lock_owner)"
    trap release_host_lock EXIT

    # Validate every line before removing anything.
    lines=0
    while IFS=$'\t' read -r action tag bytes mtime path; do
      case "$action" in '#'*|'') continue ;; REMOVE) ;; *) die "refusing: unexpected plan line: $action" ;; esac
      lines=$((lines + 1))
      base="${path##*/}"
      [[ "$base" =~ $ARCHIVE_RE ]] || die "refusing: not an archive name: $path"
      [ "${base#"${IMAGE_REPO}"-}" != "$base" ] && [ "${base:${#IMAGE_REPO}+1:12}" = "$tag" ] \
        || die "refusing: tag does not match file name: $path"
      case "$path" in "$DATA_ROOT"/*) ;; *) die "refusing: outside data root: $path" ;; esac
      case "$path" in *'/../'*|*'/./'*) die "refusing: non-canonical path: $path" ;; esac
      is_protected "$tag" && die "refusing: $tag is protected now (running, kept image or rollback target)"
      [ -e "$path" ] || [ -L "$path" ] || continue
      [ -L "$path" ] && die "refusing: symlink: $path"
      [ -L "${path%/*}" ] && die "refusing: symlinked directory: ${path%/*}"
      [ -f "$path" ] || die "refusing: not a regular file: $path"
      dir_real="$(cd -- "${path%/*}" && pwd -P)"
      case "$dir_real" in "$DATA_ROOT"|"$DATA_ROOT"/*) ;; *) die "refusing: resolves outside data root: $path" ;; esac
      read -r now_bytes now_mtime < <(stat -c '%s %Y' -- "$path")
      [ "$now_bytes" = "$bytes" ] && [ "$now_mtime" = "$mtime" ] \
        || die "refusing: changed since the plan (size/mtime): $path"
    done < "$PLAN"

    removed=0; gone=0; freed=0
    while IFS=$'\t' read -r action tag bytes mtime path; do
      [ "$action" = REMOVE ] || continue
      if [ -f "$path" ]; then
        rm -f -- "$path"
        removed=$((removed + 1)); freed=$((freed + bytes))
        printf '%s\tREMOVED\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$tag" "$bytes" "$path" >> "$LOG"
      else
        gone=$((gone + 1))
        printf '%s\tALREADY-GONE\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$tag" "$bytes" "$path" >> "$LOG"
      fi
    done < "$PLAN"
    note "plan lines: $lines  removed: $removed  already gone: $gone  bytes freed: $freed"
    note "log: $LOG"
    ;;
esac
