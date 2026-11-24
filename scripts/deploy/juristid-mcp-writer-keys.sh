#!/usr/bin/env bash
#
# Puts the secrets the MCP write gateway needs into its environment files —
# generated, never printed — and proves each one is there, BEFORE anybody
# starts `mcp-writer` or sets MCP_WRITES_ENABLED=1 (docs/mcp/OPERATIONS.md,
# Phase 2, step 2; docs/adr/0189).
#
# THE FILES AND WHAT EACH GETS
#
#   juristid-mcp-writer.env  DJANGO_SECRET_KEY       its own signing key
#                            POSTGRES_PASSWORD       juristid_mcp_writer's password
#                            MCP_WRITE_GATEWAY_KEY   the key `mcp` presents to it
#   juristid-mcp.env         MCP_WRITE_GATEWAY_KEY   the SAME value
#
# juristid.env (web's) gets nothing and must hold none of it: pass it with
# --web-env-file and this checks that it names no MCP_WRITE_GATEWAY_KEY
# (juristid.E057 would refuse `web`).
#
# PER SECRET
#
# - A line that already holds a real value — the only line naming the key, long
#   enough, not a placeholder — is KEPT, never rotated here (a new database
#   password must also be set on the role; a new signing key is harmless but a
#   decision). The gateway key is rotated only with --rotate-gateway-key, and
#   then in both files at once.
# - The gateway key must be equal in both files. One good copy is copied to
#   the other file; two good copies that differ are refused (this script does
#   not guess which one `mcp` and `mcp-writer` are running with).
# - A line that may still be the value in use — the key named in another
#   form than `KEY=` (malformed), two lines naming it, or a real value shorter
#   than the minimum (mcp_key_action in lib.sh, the rule
#   juristid-mcp-web-keys.sh uses too) — is never replaced: this exits 1 before
#   writing anything and asks for the line to be fixed by hand
#   (--rotate-gateway-key still replaces the gateway key; HR-18).
# - Otherwise — no line, a commented one, an empty value or a `replace-me`
#   placeholder — every line naming the key is dropped and one generated line
#   is appended. Each file is rewritten in place, so it keeps its inode, owner and
#   mode. MCP_WRITES_ENABLED is never touched.
#
# Exit 0 only when every secret is in place, verified; otherwise 1, and do not
# start `mcp-writer`.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

WRITER_ENV=""
MCP_ENV=""
WEB_ENV=""
ROTATE_GATEWAY=0

#: Minimum lengths, each at least the floor the process checks enforce:
#: E017 (signing key, 50), E019 (database password, 16 — the generated one is
#: 48+), E052 (gateway key, 32 — the generated one is 64).
#: Declared in lib.sh, where the read-only key check uses them too.
SECRET_KEY_MIN="$MCP_WRITER_SECRET_KEY_MIN_LENGTH"
DB_PASSWORD_MIN="$MCP_WRITER_DB_PASSWORD_MIN_LENGTH"
GATEWAY_KEY_MIN="$MCP_GATEWAY_KEY_MIN_LENGTH"

usage() {
  cat <<'USAGE'
Usage:
  juristid-mcp-writer-keys.sh --writer-env-file PATH --mcp-env-file PATH
                              [--web-env-file PATH] [--rotate-gateway-key]

  --writer-env-file  juristid-mcp-writer.env (the write gateway's)
  --mcp-env-file     juristid-mcp.env (the MCP server's)
  --web-env-file     juristid.env, only checked: it must not hold the gateway key
  --rotate-gateway-key
                     generate a new gateway key into both files (then recreate
                     `mcp` and `mcp-writer` together)

Prints only key names and states, never a value.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --writer-env-file) WRITER_ENV="${2:-}"; shift 2 ;;
    --mcp-env-file) MCP_ENV="${2:-}"; shift 2 ;;
    --web-env-file) WEB_ENV="${2:-}"; shift 2 ;;
    --rotate-gateway-key) ROTATE_GATEWAY=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument '$1'" ;;
  esac
done

[ -n "$WRITER_ENV" ] || { usage >&2; die "--writer-env-file is required"; }
[ -n "$MCP_ENV" ] || { usage >&2; die "--mcp-env-file is required"; }
require_file "$WRITER_ENV" "the write gateway's environment file"
require_file "$MCP_ENV" "the MCP server's environment file"
for file in "$WRITER_ENV" "$MCP_ENV"; do
  [ -w "$file" ] || die "cannot write $file"
done
[ "$(cd -- "$(dirname -- "$WRITER_ENV")" && pwd)/$(basename -- "$WRITER_ENV")" != \
  "$(cd -- "$(dirname -- "$MCP_ENV")" && pwd)/$(basename -- "$MCP_ENV")" ] ||
  die "--writer-env-file and --mcp-env-file are the same file"

# Root's and 600 (or 400), as the runbook installs them: these files hold a
# database password and the gateway key, and a group- or world-readable one is
# refused before anything is written into it (security re-review P3-2).
file_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null || echo ''
}
for file in "$WRITER_ENV" "$MCP_ENV"; do
  mode="$(file_mode "$file")"
  case "$mode" in
    600 | 400) ;;
    "") die "cannot read the permissions of $file" ;;
    *) die "$file is mode $mode: group or others may read it. Run: chmod 600 $file (owner root), then this again. Nothing was changed." ;;
  esac
done

case "$(basename -- "$WRITER_ENV")" in
  juristid.env | juristid-mcp.env | juristid-mcp-tunnel.env)
    die "$WRITER_ENV is not the write gateway's file (juristid-mcp-writer.env)."
    ;;
esac
case "$(basename -- "$MCP_ENV")" in
  juristid.env | juristid-mcp-writer.env | juristid-mcp-tunnel.env)
    die "$MCP_ENV is not the MCP server's file (juristid-mcp.env)."
    ;;
esac

# The database role each file names: the writer's own in its file, never in mcp's.
named_user() {
  sed -n 's/^[[:space:]]*POSTGRES_USER=//p' "$1" | tail -n 1 | tr -d "\"' \t\r"
}
[ "$(named_user "$WRITER_ENV")" = juristid_mcp_writer ] ||
  die "$WRITER_ENV must name POSTGRES_USER=juristid_mcp_writer (the write gateway's own role)."
case "$(named_user "$MCP_ENV")" in
  juristid_mcp_writer*)
    die "$MCP_ENV names the writer's database role; \`mcp\` holds only juristid_mcp (juristid.E058)."
    ;;
esac

if [ -n "$WEB_ENV" ]; then
  require_file "$WEB_ENV" "web's environment file"
  if grep -qE '^[[:space:]]*MCP_WRITE_GATEWAY_KEY=' "$WEB_ENV"; then
    die "$WEB_ENV holds MCP_WRITE_GATEWAY_KEY. Remove that line: web must never hold it (juristid.E057)."
  fi
fi

require_command openssl "the secrets are generated with openssl rand"

tmp=""
cleanup() {
  [ -z "$tmp" ] || rm -f -- "$tmp"
}
trap cleanup EXIT

# The gateway key in state ok, as Compose reads it (lib.sh). Kept in a
# variable, never printed.
key_value() {
  mcp_key_value "$1" "$2" "$GATEWAY_KEY_MIN"
}

# Drop every line naming KEY (commented template lines included), append one.
write_key() {
  local file="$1" key="$2" value="$3" status=0
  tmp="$(mktemp "$file.XXXXXX")"
  grep -vE "^[[:space:]#]*(export[[:space:]]+)?${key}[[:space:]]*[=:]" "$file" >"$tmp" || status=$?
  [ "$status" -le 1 ] || die "could not read $file"
  # A last line without a newline must not swallow the appended one.
  if [ -s "$tmp" ] && [ "$(tail -c 1 -- "$tmp" | od -An -c | tr -d ' ')" != '\n' ]; then
    printf '\n' >>"$tmp"
  fi
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  cat -- "$tmp" >"$file"
  rm -f -- "$tmp"
  tmp=""
}

# \r too: a Windows openssl ends its line with CRLF (see mcp_key_state).
new_secret() {
  openssl rand -base64 "$1" | tr -d '\r\n/+='
}

problems=0
verify() {
  local file="$1" key="$2" min="$3" before="$4" after
  after="$(mcp_key_state "$file" "$key" "$min")"
  if [ "$after" = ok ]; then
    note "ok   $key in $(basename -- "$file"): $before"
  else
    printf 'FAIL: %s in %s is %s after writing it\n' "$key" "$(basename -- "$file")" "$after" >&2
    problems=$((problems + 1))
  fi
}

# A secret that may be the value in use is never replaced here: a malformed
# line (not `KEY=` at the start), two lines naming it, or a real value
# shorter than the minimum (mcp_key_action refuse in lib.sh) — the role's
# password, or the key mcp and mcp-writer hold. Refuse before anything is
# written. Key names only. An explicit --rotate-gateway-key replaces the
# gateway key whatever its state.
refusal_check() {
  local file="$1" key="$2" min="$3" action state cost
  # Captured first, so an error from the rule stops this script.
  action="$(mcp_key_action "$file" "$key" "$min")"
  [ "$action" = refuse ] || return 0
  state="$(mcp_key_state "$file" "$key" "$min")"
  case "$key" in
    POSTGRES_PASSWORD) cost="the role's password must then be set again (mcp_writer_db_role --apply --password-stdin) before mcp-writer can connect" ;;
    MCP_WRITE_GATEWAY_KEY) cost="mcp and mcp-writer must then be recreated together" ;;
    *) cost="mcp-writer must then be recreated" ;;
  esac
  die "$(mcp_key_refusal "$file" "$key" "$state" "$min") Deleting the line instead makes this generate a new $key: $cost."
}
refusal_check "$WRITER_ENV" DJANGO_SECRET_KEY "$SECRET_KEY_MIN"
refusal_check "$WRITER_ENV" POSTGRES_PASSWORD "$DB_PASSWORD_MIN"
if [ "$ROTATE_GATEWAY" != 1 ]; then
  refusal_check "$WRITER_ENV" MCP_WRITE_GATEWAY_KEY "$GATEWAY_KEY_MIN"
  refusal_check "$MCP_ENV" MCP_WRITE_GATEWAY_KEY "$GATEWAY_KEY_MIN"
fi

# -- the writer's own two secrets -------------------------------------------------

for spec in "DJANGO_SECRET_KEY:$SECRET_KEY_MIN:64" "POSTGRES_PASSWORD:$DB_PASSWORD_MIN:48"; do
  key="${spec%%:*}"
  rest="${spec#*:}"
  min="${rest%%:*}"
  bytes="${rest#*:}"
  before="$(mcp_key_state "$WRITER_ENV" "$key" "$min")"
  if [ "$before" = ok ]; then
    note "ok   $key in $(basename -- "$WRITER_ENV"): already set, kept (never rotated here)"
    continue
  fi
  value="$(new_secret "$bytes")"
  [ "${#value}" -ge "$min" ] || die "openssl produced a $key shorter than $min characters"
  write_key "$WRITER_ENV" "$key" "$value"
  value=""
  verify "$WRITER_ENV" "$key" "$min" "generated (it was $before)"
done

# -- the gateway key, equal in both files -------------------------------------------

writer_state="$(mcp_key_state "$WRITER_ENV" MCP_WRITE_GATEWAY_KEY "$GATEWAY_KEY_MIN")"
mcp_state="$(mcp_key_state "$MCP_ENV" MCP_WRITE_GATEWAY_KEY "$GATEWAY_KEY_MIN")"
gateway=""
how=""
if [ "$ROTATE_GATEWAY" = 1 ]; then
  how="rotated in both files — recreate mcp and mcp-writer together"
elif [ "$writer_state" = ok ] && [ "$mcp_state" = ok ]; then
  if [ "$(key_value "$WRITER_ENV" MCP_WRITE_GATEWAY_KEY)" = "$(key_value "$MCP_ENV" MCP_WRITE_GATEWAY_KEY)" ]; then
    note "ok   MCP_WRITE_GATEWAY_KEY: already set and equal in both files, kept"
  else
    die "MCP_WRITE_GATEWAY_KEY differs between $(basename -- "$WRITER_ENV") and $(basename -- "$MCP_ENV"). Nothing was changed. Run again with --rotate-gateway-key to replace both."
  fi
  how="kept"
elif [ "$writer_state" = ok ]; then
  gateway="$(key_value "$WRITER_ENV" MCP_WRITE_GATEWAY_KEY)"
  how="copied from $(basename -- "$WRITER_ENV") (it was $mcp_state here)"
elif [ "$mcp_state" = ok ]; then
  gateway="$(key_value "$MCP_ENV" MCP_WRITE_GATEWAY_KEY)"
  how="copied from $(basename -- "$MCP_ENV") (it was $writer_state here)"
else
  how="generated (it was $writer_state / $mcp_state)"
fi
if [ "$how" != kept ]; then
  [ -n "$gateway" ] || gateway="$(new_secret 48)"
  [ "${#gateway}" -ge "$GATEWAY_KEY_MIN" ] || die "the gateway key is shorter than $GATEWAY_KEY_MIN characters"
  for file in "$WRITER_ENV" "$MCP_ENV"; do
    if [ "$(mcp_key_state "$file" MCP_WRITE_GATEWAY_KEY "$GATEWAY_KEY_MIN")" != ok ] || [ "$ROTATE_GATEWAY" = 1 ]; then
      write_key "$file" MCP_WRITE_GATEWAY_KEY "$gateway"
    fi
    verify "$file" MCP_WRITE_GATEWAY_KEY "$GATEWAY_KEY_MIN" "$how"
  done
  if [ "$(key_value "$WRITER_ENV" MCP_WRITE_GATEWAY_KEY)" != "$(key_value "$MCP_ENV" MCP_WRITE_GATEWAY_KEY)" ]; then
    printf 'FAIL: MCP_WRITE_GATEWAY_KEY is not equal in both files after writing it\n' >&2
    problems=$((problems + 1))
  fi
  gateway=""
fi

if [ "$problems" -gt 0 ]; then
  die "$problems secret(s) are not in place. Do NOT start mcp-writer or set MCP_WRITES_ENABLED=1."
fi
note "Every secret the write gateway needs is in place. Next: create the role (mcp_writer_db_role --apply --password-stdin from web), then start mcp-writer."
