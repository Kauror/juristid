#!/usr/bin/env bash
#
# Everything that has to be true before a deployment starts, checked without
# changing any of it.
#
# WHY AN EXPLICIT TARGET SHA
#
# The old instruction was `git pull`. What that deploys is whatever `main` has
# become since the person decided to deploy — which on a repository several
# agents push to is frequently not the commit that was reviewed. The reviewed
# thing is a commit, so the deployment takes a commit, in full, and refuses an
# abbreviation because two commits can share a prefix and the resolution is
# silent.
#
# WHY THIS DOES NOT MOVE THE CHECKOUT
#
# A preflight that mutates what it is inspecting cannot be run twice, and cannot
# be run by somebody who is only asking whether they are ready. So it verifies
# and prints the commands; the operator runs them.
#
# WHY A DIRTY CHECKOUT STOPS EVERYTHING
#
# Unexpected local changes in a production checkout mean somebody edited
# something on the server, and the only safe response is to find out what and
# why. `git reset --hard` and `git clean -fd` would delete the evidence of
# whatever was going on — a fix applied in an emergency and never committed, a
# half-finished investigation, a file somebody put there for a reason. This
# script prints the paths and stops. It never cleans, resets, stashes or
# checks out anything.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/deploy/lib.sh
. "$SCRIPT_DIR/lib.sh"

JURISTID_PROJECT="$JURISTID_PRODUCTION_PROJECT"
JURISTID_COMPOSE_FILE=""
REPO=""
TARGET=""
ENV_FILE=""
DATA_ROOT=""
BACKUP_ROOT=""

usage() {
  cat <<'USAGE'
Usage:
  juristid-deploy-preflight.sh --repo DIR --target SHA --compose-file PATH
                               [--project NAME] [--env-file PATH]
                               [--data-root DIR] [--backup-root DIR]

  --repo         the deployment checkout on the server
  --target       the full 40-character commit that was reviewed
  --compose-file the deployment's compose.yml, named explicitly

Read-only. Changes nothing, moves nothing, and prints the commands to run.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --project) JURISTID_PROJECT="${2:-}"; shift 2 ;;
    --compose-file) JURISTID_COMPOSE_FILE="${2:-}"; shift 2 ;;
    --repo) REPO="${2:-}"; shift 2 ;;
    --target) TARGET="${2:-}"; shift 2 ;;
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --data-root) DATA_ROOT="${2:-}"; shift 2 ;;
    --backup-root) BACKUP_ROOT="${2:-}"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument '$1'" ;;
  esac
done

[ -n "$REPO" ] || { usage >&2; die "--repo is required"; }
[ -n "$TARGET" ] || { usage >&2; die "--target is required"; }
[ -n "$JURISTID_COMPOSE_FILE" ] || { usage >&2; die "--compose-file is required"; }

require_known_project "$JURISTID_PROJECT"
require_directory "$REPO" "deployment checkout"
require_file "$JURISTID_COMPOSE_FILE" "compose file"
require_command git "the deployment target is a commit"

problems=0
fail() {
  printf 'FAIL: %s\n' "$*" >&2
  problems=$((problems + 1))
}
pass() {
  printf 'ok   %s\n' "$*"
}

note "Juristid deployment preflight"
note "  project      $JURISTID_PROJECT"
note "  checkout     $REPO"
note "  target       $TARGET"
note ""

# -- the target ------------------------------------------------------------

step "The commit"

case "$TARGET" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f])
    pass "the target is a full commit id" ;;
  *)
    die "'$TARGET' is not a full 40-character commit id. An abbreviation can match more than one commit and the resolution is silent, and a branch name is not a reviewed thing — it is whatever that branch has become. Nothing else here would mean anything, so this one stops immediately." ;;
esac

if ! git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  fail "$REPO is not a Git checkout"
else
  git -C "$REPO" fetch --quiet origin || fail "could not fetch from origin"

  if git -C "$REPO" cat-file -e "${TARGET}^{commit}" 2>/dev/null; then
    pass "the target commit exists in this checkout"
  else
    fail "commit $TARGET is not in this checkout, even after fetching. It was never pushed, or it is on a fork."
  fi

  head="$(git -C "$REPO" rev-parse HEAD)"
  if [ "$head" = "$TARGET" ]; then
    pass "the checkout is already at the target"
  elif git -C "$REPO" merge-base --is-ancestor "$head" "$TARGET" 2>/dev/null; then
    pass "the target is ahead of the running checkout ($(git -C "$REPO" rev-list --count "$head".."$TARGET") commit(s))"
  elif git -C "$REPO" merge-base --is-ancestor "$TARGET" "$head" 2>/dev/null; then
    printf 'WARN %s\n' "the target is BEHIND the running checkout. This is a rollback; read the schema section of deploy/unraid-main/RECOVERY.md before continuing, because rolling code back does not roll migrations back."
  else
    printf 'WARN %s\n' "the target and the running checkout have diverged. Find out why before deploying."
  fi

  dirty="$(git -C "$REPO" status --porcelain)"
  if [ -z "$dirty" ]; then
    pass "the checkout is clean"
  else
    printf 'FAIL: the checkout has local changes:\n%s\n' "$dirty" >&2
    printf 'FAIL: %s\n' "STOP and find out what these are. Do not reset, clean or check out over them: whatever they are, deleting them destroys the only record of it. Nothing in this deployment is urgent enough to be worth that." >&2
    problems=$((problems + 1))
  fi
fi

# -- the stack -------------------------------------------------------------

step "The Compose stack"

require_command docker "the deployment is a Compose stack"

if config="$(docker compose -p "$JURISTID_PROJECT" -f "$JURISTID_COMPOSE_FILE" config 2>&1)"; then
  pass "the Compose file resolves"
else
  fail "the Compose file does not resolve: $config"
  config=""
fi

if [ -n "$config" ]; then
  if printf '%s\n' "$config" | grep -qE '^[[:space:]]*published:'; then
    fail "a service publishes a host port. This stack is reachable only through the tunnel; a published port is a way around the authenticator."
  else
    pass "no service publishes a host port"
  fi

  if printf '%s\n' "$config" | grep -q 'network_mode: host'; then
    fail "a service uses host networking, which publishes every socket it listens on"
  else
    pass "no service uses host networking"
  fi

  if printf '%s\n' "$config" | grep -q '/srv/historical-source'; then
    if printf '%s\n' "$config" | grep -A4 '/srv/historical-source' | grep -q 'read_only: true'; then
      pass "the historical corpus is mounted read-only"
    else
      fail "the historical corpus is mounted writable. The importer must not be able to rewrite its own source."
    fi
  fi
fi

# -- the host --------------------------------------------------------------

step "The host"

if [ -n "$ENV_FILE" ]; then
  if [ -f "$ENV_FILE" ]; then
    pass "the environment file exists"
    mode="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE" 2>/dev/null || echo '')"
    case "$mode" in
      600 | 400) pass "its permissions are $mode" ;;
      '') printf 'WARN %s\n' "could not read the environment file's permissions" ;;
      *) fail "the environment file is mode $mode. It holds the database password and the shared gate password; it should be 600." ;;
    esac
  else
    fail "the environment file is missing: $ENV_FILE. The stack will not start, and the failure will be about a variable rather than about this."
  fi
fi

# The file Compose interpolates from is the one beside the Compose file, not the
# one the services read. A secret copied there would be read by Compose, echoed
# into `config` output, and — since it sits in the checkout — be one `git add`
# away from a public repository.
compose_dir="$(cd -- "$(dirname -- "$JURISTID_COMPOSE_FILE")" && pwd)"
if [ -f "$compose_dir/.env" ]; then
  fail "there is a .env beside the Compose file at $compose_dir/.env. The deployment's secrets belong in the appdata config directory; this one is inside the checkout of a public repository and Compose will interpolate from it."
else
  pass "no .env beside the Compose file"
fi

for pair in "data root:$DATA_ROOT" "backup root:$BACKUP_ROOT"; do
  label="${pair%%:*}"
  path="${pair#*:}"
  [ -n "$path" ] || continue
  if [ -d "$path" ]; then
    free_mib=$(( $(free_kib "$path") / 1024 ))
    pass "$label exists ($free_mib MiB free)"
    if [ "$free_mib" -lt 2048 ]; then
      fail "$label has under 2 GiB free. Do not make room by pruning Docker objects on this host."
    fi
  else
    fail "$label does not exist: $path"
  fi
done

# -- verdict ---------------------------------------------------------------

step "Verdict"

if [ "$problems" -gt 0 ]; then
  die "$problems problem(s). Nothing has been changed."
fi

cat <<NEXT
Ready. Nothing has been changed yet. The deployment, in order:

  git -C $REPO checkout --detach $TARGET
  docker compose -p $JURISTID_PROJECT -f $JURISTID_COMPOSE_FILE \\
    exec -T web python manage.py migration_plan
  scripts/deploy/juristid-backup.sh --compose-file $JURISTID_COMPOSE_FILE ...
  JURISTID_GIT_SHA=$TARGET JURISTID_IMAGE_TAG=${TARGET:0:12} \\
    docker compose -p $JURISTID_PROJECT -f $JURISTID_COMPOSE_FILE build
  docker compose -p $JURISTID_PROJECT -f $JURISTID_COMPOSE_FILE \\
    run --rm web python manage.py migrate
  JURISTID_GIT_SHA=$TARGET JURISTID_IMAGE_TAG=${TARGET:0:12} \\
    docker compose -p $JURISTID_PROJECT -f $JURISTID_COMPOSE_FILE up -d
  docker compose -p $JURISTID_PROJECT -f $JURISTID_COMPOSE_FILE \\
    exec -T web python manage.py deployment_readiness

deploy/unraid-main/README.md has the reasoning for each step.
NEXT
