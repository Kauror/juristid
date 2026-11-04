#!/usr/bin/env bash
#
# Read-only inventory of a Juristid clone's branches and worktrees, classified
# for housekeeping. Writes nothing: no fetch, no prune, no delete. Run
# `git fetch --prune origin` yourself first if the remote view should be fresh.
#
#   juristid-git-inventory.sh [--repo DIR] [--base origin/main] [--keep REGEX]
#
# Output: TSV on stdout, one line per item:
#
#   kind      remote-branch | local-branch | worktree
#   class     KEEP | SAFE | REVIEW
#   name      branch name or worktree path
#   sha       tip / HEAD
#   reason    why
#
# SAFE means every one of these holds, and nothing weaker:
#   remote-branch  tip is an ancestor of --base; not main/HEAD; not matched by
#                  --keep (default: archive/, dependabot/, release/); no tag
#                  points at it; not checked out in any worktree.
#   local-branch   the same, against the local tip, plus not the current branch
#                  of any worktree.
#   worktree       not the main checkout; clean (`status --porcelain` empty,
#                  so no untracked files); HEAD is an ancestor of --base or is
#                  contained in some remote branch; not locked.
# Open pull requests are NOT known to this script (it never calls the network):
# a SAFE remote branch with an open PR must still be kept — check with
# `gh pr list --state open --json headRefName` before deleting anything, and
# delete one branch at a time with a lease on the recorded SHA:
#   git push --force-with-lease=<branch>:<sha> origin :<branch>
# Gitignored files inside a SAFE worktree (dev uploads, test reports) are
# deleted by `git worktree remove`; the `ignored` column counts them.

set -euo pipefail

REPO="."
BASE="origin/main"
KEEP_RE='^(main|HEAD|archive/.*|dependabot/.*|release/.*)$'

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:?}"; shift 2 ;;
    --base) BASE="${2:?}"; shift 2 ;;
    --keep) KEEP_RE="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'ERROR: unknown argument: %s\n' "$1" >&2; exit 1 ;;
  esac
done

g() { git -C "$REPO" "$@"; }
g rev-parse --verify --quiet "$BASE^{commit}" >/dev/null || { printf 'ERROR: unknown base %s\n' "$BASE" >&2; exit 1; }

checked_out="$(g worktree list --porcelain | sed -n 's#^branch refs/heads/##p')"
is_checked_out() { printf '%s\n' "$checked_out" | grep -qxF "$1"; }
in_base() { g merge-base --is-ancestor "$1" "$BASE"; }
tagged() { [ -n "$(g tag --points-at "$1")" ]; }

printf 'kind\tclass\tname\tsha\treason\tignored\n'

g for-each-ref --format='%(refname:strip=3)%09%(objectname)' refs/remotes/origin | while IFS=$'\t' read -r name sha; do
  [ "$name" = HEAD ] && continue
  if [[ "$name" =~ $KEEP_RE ]]; then class=KEEP; reason="kept by name"
  elif tagged "$sha"; then class=KEEP; reason="tagged"
  elif is_checked_out "$name"; then class=REVIEW; reason="checked out in a worktree"
  elif in_base "$sha"; then class=SAFE; reason="tip in $BASE (check open PRs before deleting)"
  else class=REVIEW; reason="$(g rev-list --count "$BASE..$sha") commits not in $BASE"
  fi
  printf 'remote-branch\t%s\t%s\t%s\t%s\t\n' "$class" "$name" "$sha" "$reason"
done

g for-each-ref --format='%(refname:strip=2)%09%(objectname)' refs/heads | while IFS=$'\t' read -r name sha; do
  if [[ "$name" =~ $KEEP_RE ]]; then class=KEEP; reason="kept by name"
  elif is_checked_out "$name"; then class=KEEP; reason="checked out in a worktree"
  elif tagged "$sha"; then class=KEEP; reason="tagged"
  elif in_base "$sha"; then class=SAFE; reason="tip in $BASE"
  else
    off="$(g rev-list --count "$sha" --not --remotes)"
    class=REVIEW; reason="not in $BASE; $off commits on no remote"
  fi
  printf 'local-branch\t%s\t%s\t%s\t%s\t\n' "$class" "$name" "$sha" "$reason"
done

main_path="$(g worktree list --porcelain | sed -n '1s/^worktree //p')"
g worktree list --porcelain | awk '
  /^worktree /{p=substr($0,10); h=""; l=0}
  /^HEAD /{h=$2}
  /^locked/{l=1}
  /^$/{if(p!="") print p "\t" h "\t" l; p=""}
  END{if(p!="") print p "\t" h "\t" l}' | while IFS=$'\t' read -r path head locked; do
  ignored=""
  if [ "$path" = "$main_path" ]; then class=KEEP; reason="main checkout"
  elif [ ! -d "$path" ]; then class=REVIEW; reason="missing directory (git worktree prune candidate)"
  elif [ "$locked" = 1 ]; then class=KEEP; reason="locked"
  elif [ -n "$(git -C "$path" status --porcelain)" ]; then class=REVIEW; reason="uncommitted or untracked changes"
  elif in_base "$head" || [ "$(g rev-list --count "$head" --not --remotes)" = 0 ]; then
    class=SAFE; reason="clean; HEAD in $BASE or on a remote branch"
    ignored="$(git -C "$path" status --porcelain --ignored | grep -c '^!!' || true)"
  else class=REVIEW; reason="$(g rev-list --count "$head" --not --remotes) commits on no remote"
  fi
  printf 'worktree\t%s\t%s\t%s\t%s\t%s\n' "$class" "$path" "$head" "$reason" "$ignored"
done
