#!/usr/bin/env bash
#
# Answer one question, read-only: **is the newest complete Juristid backup older
# than N hours?**
#
# WHY THIS EXISTS
#
# The backup script is good and the sets it writes are real. What nothing could
# answer until now is whether one has been taken *lately*. The audit that
# prompted this found 34 proper sets on the host and no schedule of any kind
# behind them — every one had been taken by hand before a deployment — with a
# worst observed gap of about 41 hours. A backup regime nobody is measuring is
# indistinguishable, from the outside, from one that stopped last week.
#
# WHAT IT DOES NOT DO
#
# **It takes no view on how old is too old.** `--max-age-hours` is required and
# has no default, deliberately. The RPO — how much work the Chamber is willing
# to lose — is a decision for the people who would lose it, and a number written
# into a script becomes policy the day somebody reads it as one
# (docs/open-decisions.md, deploy/unraid-main/RECOVERY.md).
#
# **It does not verify anything.** A set is *complete* here if it is named like
# a set, is not a `.partial`, and holds the three files a finished set holds.
# Whether those files are intact is `juristid-verify-backup.sh`, and whether the
# system comes back from them is the rehearsal. Freshness and integrity are
# separate questions and a check that blurred them would answer neither.
#
# **It writes nothing and deletes nothing.** It reads directory names.
#
# EXIT STATUS
#
#   0  a complete set exists and is within the limit
#   1  the arguments or the backup root are wrong
#   2  no complete set exists at all
#   3  the newest complete set is older than the limit
#
# Distinct codes rather than "non-zero", so a monitor can tell "the backups have
# stopped" from "there have never been any" from "you typed the path wrong" —
# three problems with three different first moves.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

readonly EXIT_FRESH=0
readonly EXIT_NONE=2
readonly EXIT_STALE=3

#: The three files `juristid-backup.sh` writes into a set before it renames the
#: directory into place. All three, because a set missing any one of them is the
#: shape a crashed run leaves and is exactly what must not count as a backup.
readonly REQUIRED_FILES="database.dump manifest.json SHA256SUMS"

BACKUP_ROOT=""
MAX_AGE_HOURS=""

usage() {
  cat <<'USAGE'
Usage:
  juristid-check-backup-age.sh --backup-root DIR --max-age-hours N

  --backup-root     where the backup sets live (the directory holding sets/)
  --max-age-hours   how old the newest complete set may be. Required; there is
                    deliberately no default, because the RPO is a decision.

Exit status:
  0  fresh    1  bad arguments    2  no complete set    3  stale
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --backup-root) BACKUP_ROOT="${2:-}"; shift 2 ;;
    --max-age-hours) MAX_AGE_HOURS="${2:-}"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument '$1'" ;;
  esac
done

[ -n "$BACKUP_ROOT" ] || { usage >&2; die "--backup-root is required"; }
[ -n "$MAX_AGE_HOURS" ] || { usage >&2; die "--max-age-hours is required. There is no default: how much work may be lost is a decision, not a constant."; }
case "$MAX_AGE_HOURS" in
  *[!0-9]* | "") die "--max-age-hours must be a whole number of hours, not '$MAX_AGE_HOURS'." ;;
esac
[ "$MAX_AGE_HOURS" -gt 0 ] || die "--max-age-hours must be greater than zero."

require_directory "$BACKUP_ROOT" "backup root"

SETS_DIR="$BACKUP_ROOT/sets"
if [ ! -d "$SETS_DIR" ]; then
  printf 'CRITICAL: no sets directory at %s. No Juristid backup has ever been taken here.\n' "$SETS_DIR"
  exit "$EXIT_NONE"
fi

# --------------------------------------------------------------------------
# A stamp is a time, and the name is the only place it is recorded
# --------------------------------------------------------------------------
#
# `20260831T102328Z`, written by `date -u` in the backup script. Read from the
# name rather than from the directory's mtime: an mtime is changed by copying,
# by rsync without `-t`, and by a filesystem that does not preserve it, and a
# freshness check that trusted one would call a set from March "taken today"
# the moment somebody moved the tree.
#
# The shape is matched with a shell `case` glob rather than with `grep`. Not
# only because piping into `grep -q` inverts its own answer under `pipefail` —
# the trap this repository has already been caught by twice — but because the
# stamp is a fixed-width literal and a glob says exactly that with no second
# process at all.
#
# It is matched strictly, so a `.partial`, a hand-made `sets/old-stuff` and a
# stray file are all simply not sets. None of them is an error — an operator is
# allowed to keep a directory beside the sets — they are just not counted as one.
readonly STAMP_GLOB='[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z'

epoch_of_stamp() {
  local stamp="$1" iso
  iso="${stamp:0:4}-${stamp:4:2}-${stamp:6:2} ${stamp:9:2}:${stamp:11:2}:${stamp:13:2}"
  # GNU first, BSD second. Neither is assumed present, and a stamp that cannot
  # be parsed is reported rather than treated as the epoch — which would make
  # every set look infinitely old and turn an unreadable name into a false
  # alarm about the backups.
  date -u -d "$iso UTC" +%s 2>/dev/null ||
    date -u -j -f '%Y-%m-%d %H:%M:%S' "$iso" +%s 2>/dev/null ||
    return 1
}

is_complete_set() {
  local directory="$1" file
  for file in $REQUIRED_FILES; do
    [ -f "$directory/$file" ] || return 1
  done
  return 0
}

newest_stamp=""
newest_epoch=0
complete=0
incomplete=0
partial=0
unreadable=""

for entry in "$SETS_DIR"/*; do
  [ -d "$entry" ] || continue
  name="$(basename "$entry")"
  case "$name" in
    *.partial) partial=$((partial + 1)); continue ;;
    $STAMP_GLOB) : ;;
    *) continue ;;
  esac

  if ! is_complete_set "$entry"; then
    # A directory named like a set that is missing one of the three files. Not
    # a `.partial`, so a rename happened and something else went wrong; it is
    # counted and named, and it does not satisfy the check.
    incomplete=$((incomplete + 1))
    continue
  fi

  if ! epoch="$(epoch_of_stamp "$name")"; then
    unreadable="$unreadable $name"
    continue
  fi

  complete=$((complete + 1))
  if [ "$epoch" -gt "$newest_epoch" ]; then
    newest_epoch="$epoch"
    newest_stamp="$name"
  fi
done

[ -z "$unreadable" ] || note "WARNING: could not read a date from:$unreadable"
[ "$incomplete" -eq 0 ] || note "WARNING: $incomplete directory/directories named like a set are missing one of: $REQUIRED_FILES"
[ "$partial" -eq 0 ] || note "NOTE: $partial unfinished .partial director(y/ies) present. They are not backups; look at them."

if [ "$complete" -eq 0 ]; then
  printf 'CRITICAL: no complete backup set in %s.\n' "$SETS_DIR"
  exit "$EXIT_NONE"
fi

now="$(date -u +%s)"
age_seconds=$((now - newest_epoch))
[ "$age_seconds" -ge 0 ] || age_seconds=0
age_hours=$((age_seconds / 3600))
limit_seconds=$((MAX_AGE_HOURS * 3600))

# `>` and not `>=`. A set taken exactly at the limit is inside it: the boundary
# belongs to the good side, so a check running on the hour against a backup
# taken on the hour does not alarm on arithmetic.
if [ "$age_seconds" -gt "$limit_seconds" ]; then
  printf 'CRITICAL: newest complete Juristid backup is %s, %sh old (limit %sh). %s complete set(s) at %s.\n' \
    "$newest_stamp" "$age_hours" "$MAX_AGE_HOURS" "$complete" "$SETS_DIR"
  exit "$EXIT_STALE"
fi

printf 'OK: newest complete Juristid backup is %s, %sh old (limit %sh). %s complete set(s) at %s.\n' \
  "$newest_stamp" "$age_hours" "$MAX_AGE_HOURS" "$complete" "$SETS_DIR"
exit "$EXIT_FRESH"
