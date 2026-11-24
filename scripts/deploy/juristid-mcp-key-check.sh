#!/usr/bin/env bash
#
# Reports the state of every MCP key slot — read-only, value-free (HR-18).
#
# Run it on the host before a deploy that carries a change to the key rule, or
# whenever a key helper refused: it says, per slot, only one of
#
#   ok          one line KEY=value; the value Compose reads is long enough, no placeholder
#   weak        that, but under the helper's minimum (the process may still start), empty
#               or a `replace-me` placeholder
#   missing     no line names the key (a commented line does not count)
#   duplicated  more than one line names it
#   malformed   named another way Compose reads: indented, export, `KEY = `, `KEY:`
#
# using the one rule (mcp_key_state in lib.sh) the preflight,
# juristid-mcp-web-keys.sh and juristid-mcp-writer-keys.sh use, at their
# minimum lengths. The slots:
#
#   juristid.env             MCP_AUTHENTICATOR_KEY, MCP_TOKEN_DERIVATION_KEY
#   juristid-mcp-writer.env  DJANGO_SECRET_KEY, POSTGRES_PASSWORD, MCP_WRITE_GATEWAY_KEY
#   juristid-mcp.env         MCP_WRITE_GATEWAY_KEY
#
# Only the files passed are read. Nothing is written, no temporary file is
# made, and no value — nor any part, length or digest of one — is printed,
# logged or kept in a variable: the rule reads values only inside one awk process.
#
# Exit 0 when every slot is ok, 1 when any is not, 2 when a file cannot be read
# or the arguments are wrong.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

WEB_ENV=""
MCP_ENV=""
WRITER_ENV=""

usage() {
  cat <<'USAGE'
Usage:
  juristid-mcp-key-check.sh [--web-env-file PATH] [--mcp-env-file PATH]
                            [--writer-env-file PATH]

  --web-env-file     juristid.env (web's AI-access keys)
  --mcp-env-file     juristid-mcp.env (the gateway key mcp presents)
  --writer-env-file  juristid-mcp-writer.env (the write gateway's secrets)

At least one. Prints `FILE KEY: state` per slot and never a value. Read-only.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --web-env-file) WEB_ENV="${2:-}"; shift 2 || { usage >&2; exit 2; } ;;
    --mcp-env-file) MCP_ENV="${2:-}"; shift 2 || { usage >&2; exit 2; } ;;
    --writer-env-file) WRITER_ENV="${2:-}"; shift 2 || { usage >&2; exit 2; } ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; printf 'ERROR: unknown argument %s\n' "$1" >&2; exit 2 ;;
  esac
done

if [ -z "$WEB_ENV$MCP_ENV$WRITER_ENV" ]; then
  usage >&2
  exit 2
fi

not_ok=0
# One slot: FILE KEY MIN. A file that cannot be read stops the check (exit 2):
# that is not a state of the key.
slot() {
  local file="$1" key="$2" min="$3" state
  if ! state="$(mcp_key_state "$file" "$key" "$min" 2>/dev/null)"; then
    printf 'ERROR: cannot read %s. Nothing was checked further.\n' "$file" >&2
    exit 2
  fi
  printf '%s %s: %s\n' "$(basename -- "$file")" "$key" "$state"
  [ "$state" = ok ] || not_ok=$((not_ok + 1))
}

if [ -n "$WEB_ENV" ]; then
  for key in "${MCP_WEB_KEYS[@]}"; do
    slot "$WEB_ENV" "$key" "$MCP_WEB_KEY_MIN_LENGTH"
  done
fi
if [ -n "$WRITER_ENV" ]; then
  slot "$WRITER_ENV" DJANGO_SECRET_KEY "$MCP_WRITER_SECRET_KEY_MIN_LENGTH"
  slot "$WRITER_ENV" POSTGRES_PASSWORD "$MCP_WRITER_DB_PASSWORD_MIN_LENGTH"
  slot "$WRITER_ENV" MCP_WRITE_GATEWAY_KEY "$MCP_GATEWAY_KEY_MIN_LENGTH"
fi
if [ -n "$MCP_ENV" ]; then
  slot "$MCP_ENV" MCP_WRITE_GATEWAY_KEY "$MCP_GATEWAY_KEY_MIN_LENGTH"
fi

[ "$not_ok" = 0 ] || exit 1
exit 0
