#!/usr/bin/env bash
#
# Puts the keys `web` needs for AI access into juristid.env — generated, never
# printed — and proves each one is there, BEFORE anybody sets MCP_ENABLED=1
# (docs/mcp/OPERATIONS.md, first-time enablement step 3).
#
# WHY A SCRIPT AND NOT A SED LINE
#
# The runbook used to say `sed -i "s|^# *MCP_AUTHENTICATOR_KEY=.*|…|"`. On a
# juristid.env that lacks the commented template line that is a silent no-op,
# and with MCP_ENABLED=1 `web` then refuses to start on real data
# (mcp_access.E004): all of Juristid is down until somebody finds out why
# (security review NEW-09).
#
# WHAT IT DOES, PER KEY (MCP_WEB_KEYS in lib.sh)
#
# - A line that already holds a real value — the only line naming the key,
#   40 or more characters, not a placeholder — is KEPT. Never rotated here: a
#   new MCP_AUTHENTICATOR_KEY makes every enrolled authenticator unreadable,
#   which is a decision, not a side effect of running this twice.
# - Otherwise every line naming the key — the template's commented one, a
#   placeholder, a short value, a duplicate — is dropped and one generated line
#   is appended.
# - Then the file must name the key exactly once, `KEY=` and 40 or more
#   characters, or this exits 1 and says not to switch AI access on.
#
# The file is rewritten in place, so it keeps its inode, owner and mode.
# MCP_ENABLED is never touched: the operator sets it, after this said ok.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

ENV_FILE=""

usage() {
  cat <<'USAGE'
Usage:
  juristid-mcp-web-keys.sh --env-file PATH

  --env-file  web's environment file, juristid.env — never juristid-mcp.env

Generates each missing key straight into the file and prints only key names.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument '$1'" ;;
  esac
done

[ -n "$ENV_FILE" ] || { usage >&2; die "--env-file is required"; }
require_file "$ENV_FILE" "environment file"
[ -w "$ENV_FILE" ] || die "cannot write $ENV_FILE"
case "$(basename -- "$ENV_FILE")" in
  juristid-mcp.env | juristid-mcp-tunnel.env)
    die "$ENV_FILE is an AI-access service's file. These keys belong to web, in juristid.env, and the mcp process must never hold them (juristid.E046)."
    ;;
esac
require_command openssl "the keys are generated with openssl rand"

tmp=""
cleanup() {
  [ -z "$tmp" ] || rm -f -- "$tmp"
}
trap cleanup EXIT

new_key() {
  openssl rand -base64 48 | tr -d '\n/+='
}

problems=0
for key in "${MCP_WEB_KEYS[@]}"; do
  before="$(mcp_web_key_state "$ENV_FILE" "$key")"
  if [ "$before" = ok ]; then
    note "ok   $key: already set, kept (never rotated here)"
    continue
  fi
  value="$(new_key)"
  [ "${#value}" -ge "$MCP_WEB_KEY_MIN_LENGTH" ] || die "openssl produced a key shorter than $MCP_WEB_KEY_MIN_LENGTH characters"
  tmp="$(mktemp "$ENV_FILE.XXXXXX")"
  # Every line but the ones naming the key. grep exits 1 when it selects
  # nothing (a file holding only that key), 2 when it cannot read.
  status=0
  grep -vE "^[[:space:]#]*${key}=" "$ENV_FILE" >"$tmp" || status=$?
  [ "$status" -le 1 ] || die "could not read $ENV_FILE"
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  value=""
  cat -- "$tmp" >"$ENV_FILE"
  rm -f -- "$tmp"
  tmp=""
  after="$(mcp_web_key_state "$ENV_FILE" "$key")"
  if [ "$after" = ok ]; then
    note "ok   $key: generated (it was $before)"
  else
    printf 'FAIL: %s is %s after writing it\n' "$key" "$after" >&2
    problems=$((problems + 1))
  fi
done

if [ "$problems" -gt 0 ]; then
  die "$problems key(s) are not in place. Do NOT set MCP_ENABLED=1: web would refuse to start."
fi
note "Every key web needs for AI access is in $ENV_FILE. Only now set MCP_ENABLED=1."
