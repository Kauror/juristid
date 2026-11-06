#!/usr/bin/env bash
# Verify a release-image artifact before, and after, it is loaded (G-02).
#
#   juristid-verify-release-artifact.sh --dir <unpacked artifact> --tag <sha12>
#       [--revision <full 40-char sha>] [--archive-sha256 <hex>]
#       [--loaded-image-id sha256:<hex>]
#
# `.github/workflows/release-image.yml` builds the production image afresh, so
# CI's green run on the source says nothing about these bytes. The workflow
# therefore runs CI's two image gates — the runtime check from inside the
# image, and the pinned Trivy gate on fixable HIGH and CRITICAL — on the image
# it archives, and writes into the manifest the id each gate saw and the id the
# archive holds. This script is the receiving half: it refuses an artifact
# unless
#
#   * the archive hashes to its `.sha256` file and to the manifest's
#     `archive_sha256` (and to `--archive-sha256`, the digest the release kit
#     recorded, when given);
#   * the manifest names one image id four times over — `image_id`,
#     `scanned_image_id`, `runtime_checked_image_id`, `archive_image_id` — so
#     the image in the archive is the image that was scanned and checked;
#   * both gates say PASS and the fixable HIGH/CRITICAL count is 0;
#   * the OS package list beside the archive is the one the manifest hashed;
#   * with `--loaded-image-id` (what `docker image inspect` reports after
#     `docker load`), the loaded image is the scanned image.
#
# A manifest from before this check existed has no `scanned_image_id`, and is
# refused: an image nobody scanned is exactly what this is here to stop.
#
# Reads files only. It starts no container, touches no stack and needs no
# Docker; the caller passes the loaded id in. Exit 0 and one summary line on
# success, exit 1 and the reason otherwise.
set -euo pipefail

usage() { sed -n '2,6p' "$0" >&2; exit 2; }
die() { echo "release artifact refused: $*" >&2; exit 1; }

DIR=""
TAG=""
REVISION=""
EXPECTED_ARCHIVE_SHA=""
LOADED_ID=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dir) DIR="${2:-}"; shift 2 ;;
    --tag) TAG="${2:-}"; shift 2 ;;
    --revision) REVISION="${2:-}"; shift 2 ;;
    --archive-sha256) EXPECTED_ARCHIVE_SHA="${2:-}"; shift 2 ;;
    --loaded-image-id) LOADED_ID="${2:-}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done
[ -n "$DIR" ] && [ -n "$TAG" ] || usage
[[ "$TAG" =~ ^[0-9a-f]{12}$ ]] || die "--tag must be the 12-character revision prefix: $TAG"
[ -d "$DIR" ] || die "no such directory: $DIR"

ARCHIVE="juristid-main-web-$TAG.tar.gz"
MANIFEST="$DIR/release-manifest-$TAG.txt"
PACKAGES="$DIR/os-packages-$TAG.txt"
for file in "$DIR/$ARCHIVE" "$DIR/$ARCHIVE.sha256" "$MANIFEST" "$PACKAGES"; do
  [ -f "$file" ] || die "missing $file"
done

# `key: value`, the first line whose first word is exactly `key:`.
field() { awk -v k="$1:" '$1==k { sub(/^[^:]*:[[:space:]]*/, ""); print; exit }' "$MANIFEST"; }
required() {
  local value
  value="$(field "$1")"
  [ -n "$value" ] || die "the manifest has no $1 (built before the release job scanned its image?)"
  printf '%s\n' "$value"
}
image_id_shaped() { [[ "$1" =~ ^sha256:[0-9a-f]{64}$ ]]; }

# Every value is read into a variable first: a failed read then stops the
# script with its own reason, under `set -e`, instead of becoming an empty
# string some comparison below reports as a different problem.
archive_sha="$(required archive_sha256)"
revision="$(required revision)"
image_id="$(required image_id)"
scanned="$(required scanned_image_id)"
checked="$(required runtime_checked_image_id)"
archived="$(required archive_image_id)"
runtime_check="$(required runtime_check)"
image_scan="$(required image_scan)"
fixable="$(required image_scan_fixable_high_critical)"
packages_sha="$(required os_packages_sha256)"

# -- the archive is the archive the manifest describes
( cd "$DIR" && sha256sum -c --strict --quiet "$ARCHIVE.sha256" ) || die "$ARCHIVE does not match $ARCHIVE.sha256"
listed="$(cut -d' ' -f1 "$DIR/$ARCHIVE.sha256")"
[ "$archive_sha" = "$listed" ] || die "manifest archive_sha256 $archive_sha is not the .sha256 digest $listed"
if [ -n "$EXPECTED_ARCHIVE_SHA" ]; then
  [ "$listed" = "$EXPECTED_ARCHIVE_SHA" ] || die "archive digest $listed is not the expected $EXPECTED_ARCHIVE_SHA"
fi
[ "${revision:0:12}" = "$TAG" ] || die "manifest revision $revision is not tag $TAG"
if [ -n "$REVISION" ]; then
  [ "$revision" = "$REVISION" ] || die "manifest revision $revision is not $REVISION"
fi

# -- one image id: archived, scanned, runtime-checked, read back from the archive
image_id_shaped "$image_id" || die "manifest image_id is not an image id: $image_id"
[ "$scanned" = "$image_id" ] || die "scanned_image_id $scanned is not image_id $image_id"
[ "$checked" = "$image_id" ] || die "runtime_checked_image_id $checked is not image_id $image_id"
[ "$archived" = "$image_id" ] || die "archive_image_id $archived is not image_id $image_id"

# -- both gates passed
case "$runtime_check" in PASS*) ;; *) die "runtime_check is '$runtime_check', not PASS" ;; esac
case "$image_scan" in PASS*) ;; *) die "image_scan is '$image_scan', not PASS" ;; esac
[ "$fixable" = 0 ] || die "image_scan_fixable_high_critical is $fixable, not 0"

# -- the package inventory is the scanned image's
packages="$(sha256sum "$PACKAGES" | cut -d' ' -f1)"
[ "$packages_sha" = "$packages" ] || die "os-packages-$TAG.txt is not the list the manifest hashed"

# -- and, once loaded, the loaded image is that image
loaded_note=""
if [ -n "$LOADED_ID" ]; then
  [ "$LOADED_ID" = "$image_id" ] || die "the loaded image is $LOADED_ID, not the scanned $image_id"
  loaded_note=", and is the image loaded"
fi

echo "release artifact verified: $ARCHIVE $listed holds $image_id, scanned and runtime-checked$loaded_note"
