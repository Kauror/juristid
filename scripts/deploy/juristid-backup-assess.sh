#!/usr/bin/env bash
# Read-only backup assessment (r98, R98-16): what exists, how old, whether it was verified, and who
# can read it. It changes nothing: no chmod, no write under the backup root, no lock. Output is a
# short key=value report (no file names beyond set stamps) for the operator, never secrets.
#
#   bash scripts/deploy/juristid-backup-assess.sh [BACKUP_ROOT]
#
# Four things are kept apart, because «a backup exists» is said about all of them:
#   integrity        VERIFIED-L2 present in the newest sets (level-2 verification marker)
#   confidentiality  open (group/other-accessible) objects, world-writable objects, ACLs, symlinks
#   recoverability   the newest set's manifest, database dump and checksum list exist; the last
#                    restore rehearsal is NOT knowable here (operator reports hold it)
#   off-host         none unless an off-host repository is configured (OFFHOST-BACKUP.md): this
#                    script only reports whether the configuration marker exists
set -euo pipefail
ROOT="${1:-/mnt/user/backups/juristid-main}"
now=$(date +%s)

say() { printf '%s=%s\n' "$1" "$2"; }

[ -d "$ROOT" ] || { say status "no backup root at the given path"; exit 2; }
say root_mode "$(stat -c '%a %U:%G' "$ROOT")"
say sets_mode "$(stat -c '%a %U:%G' "$ROOT/sets" 2>/dev/null || echo missing)"

sets=$(find "$ROOT/sets" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort)
say sets_total "$(printf '%s\n' "$sets" | grep -c .)"
say sets_partial "$(find "$ROOT/sets" -mindepth 1 -maxdepth 1 \( -name '*.partial' -o -name '*.tmp' \) 2>/dev/null | wc -l)"

newest=$(printf '%s\n' "$sets" | grep -v partial | tail -1 || true)
if [ -n "$newest" ]; then
  say newest_set "$(basename "$newest")"
  say newest_age_hours "$(( (now - $(stat -c %Y "$newest")) / 3600 ))"
  for f in manifest.json database.dump SHA256SUMS VERIFIED-L2; do
    if [ -e "$newest/$f" ]; then say "newest_has_$f" yes; else say "newest_has_$f" no; fi
  done
fi
verified=0; unverified=0
for s in $sets; do
  if [ -e "$s/VERIFIED-L2" ]; then verified=$((verified + 1)); else unverified=$((unverified + 1)); fi
done
say sets_with_VERIFIED_L2 "$verified"
say sets_without_VERIFIED_L2 "$unverified"

# Confidentiality: counts only. -perm /077 = any group or other access; /007 = any other access.
for dir in "$ROOT" "$ROOT/sets" "$ROOT/evidence" "$ROOT/legacy-source" "$ROOT/fingerprints"; do
  [ -d "$dir" ] || continue
  name=$(basename "$dir"); [ "$dir" = "$ROOT" ] && name=root
  say "open_objects_$name" "$(find "$dir" -perm /077 2>/dev/null | wc -l)"
  say "world_accessible_$name" "$(find "$dir" -perm /007 2>/dev/null | wc -l)"
  say "world_writable_dirs_$name" "$(find "$dir" -type d -perm -0002 2>/dev/null | wc -l)"
done
say symlinks "$(find "$ROOT" -type l 2>/dev/null | wc -l)"
if command -v getfacl >/dev/null 2>&1; then
  say acl_entries_on_top_dirs "$(getfacl -p "$ROOT" "$ROOT/sets" 2>/dev/null | grep -c '^user:[^:]\|^group:[^:]')"
else
  say acl_entries_on_top_dirs "not checked (getfacl missing)"
fi

# Exposure: SMB export of the share, and any container mount of it.
if [ -r /etc/samba/smb-shares.conf ]; then
  say smb_exports_backups "$(grep -ci '^\[backups\]' /etc/samba/smb-shares.conf)"
fi
if command -v docker >/dev/null 2>&1; then
  say containers_mounting_backups "$(docker ps -aq | xargs -r docker inspect -f '{{.Name}} {{range .Mounts}}{{.Source}} {{end}}' 2>/dev/null | grep -ci 'backups')"
fi

# Off-host: only whether a configuration marker exists (the procedure is OFFHOST-BACKUP.md).
if [ -e /mnt/user/appdata/juristid-main/config/offhost-backup.env ]; then
  say offhost_configured yes
else
  say offhost_configured no
fi
df -P "$ROOT" | awk 'NR==2 {print "free_kb=" $4 "\nused_percent=" $5}'
say assessed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
