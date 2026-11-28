# Backup remediation: the operator's final procedure (r98, ADR 0240)

**Status: prepared, not applied.** Nothing here is run by a script, a deploy or CI. It needs
the owner's separate, written authorization for the day and for the production host, and it is
not a step of releasing r98 or any other release. It makes the procedure that
[RECOVERY.md](RECOVERY.md#tightening-what-already-exists--a-targeted-one-time-operator-step)
already holds complete and checkable; where the two differ, **RECOVERY.md's commands stand and
this page adds the gates around them**.

Read-only assessment first (changes nothing, prints counts):

```sh
bash scripts/deploy/juristid-backup-assess.sh /mnt/user/backups/juristid-main
```

Recorded 28.11.2026 12:30Z, on the r95 scripts: 65,179 objects readable by group or other,
3,364 world-writable directories (all in `evidence/`), 194 sets of which 60 carry `VERIFIED-L2`,
nothing exported over SMB, no container mounts the share, no off-host copy.

## 0. Authorization and what is *not* part of it

| Needed | Not part of it |
|---|---|
| The owner's written go-ahead naming the date and window | Setting `JURISTID_BACKUP_REQUIRE_PRIVATE=1` (a later gate, §8) |
| An operator with root on the host | Any off-host copy, account, purchase or transfer |
| The r96 scripts on the host (`grep -c share_set_proof "$REPO/scripts/deploy/lib.sh"` = 1) | Verifying the 134 older sets in bulk (§9 is a plan) |
| A named second person who can read the manifest and the proof afterwards | Deleting, moving or re-encrypting any backup |

## 1. Exact target state

Ownership never changes. Only modes change. Identities that may read after the operation:

| Object | Owner:group | Mode | Who can read |
|---|---|---|---|
| `$B` (`/mnt/user/backups/juristid-main`), `sets/`, `evidence/`, `legacy-source/`, `fingerprints/` and every directory under them | unchanged (root, or 10001 in the pools) | `0700` | the owner of that directory, and root |
| every set file and pool file | unchanged | `0600` | its owner, and root |
| `operator-reports/` | root | `0700` (already) | root |
| the parent share `/mnt/user/backups` | `99:100` | **not touched** (it belongs to every backup) | as before |
| **the Lovable proof exception**: the set a nightly APPLY takes fresh, its directory and exactly three files | root:**10001** | directory `0750`; `VERIFIED-L2`, `SHA256SUMS`, `database.dump` `0640` (never `manifest.json`) | root, and group 10001 on that one set only |

Authorized identities: **root** (the host's backup, verify and restore scripts, run by the
operator) and, for the proof exception only, **gid 10001** (the application container's
group, through a read-only bind mount of one set). Nobody else: no other container, no SMB or
NFS client, no Tailscale peer. The pools are content-addressed and shared by every set;
`evidence/` and `legacy-source/` are tightened as pools, once, not set by set.

**The proof exception is made by the sync, not by this procedure.** `share_set_proof`
(`scripts/deploy/lib.sh`) sets the directory to `0750 root:10001` and the three files to
`0640 root:10001` on the set each APPLY (and OneNote import) takes fresh. Older sets need no
group access. So: tighten everything, including the newest set, and let the next APPLY re-grant
its own three files. If this procedure runs after a night's APPLY, that night's three files are
tightened too, and the **next** night's set proves itself again; nothing else reads them.

## 2. Preconditions (all must be true; write each answer into the before-state file)

1. A window outside 20:30–23:00 UTC (backup 20:45, cycle 21:00 and 22:00, the on-demand sync
   polling every 5 minutes).
2. No competing activity: no `juristid-backup`, `verify-backup`, `restore` or `lovable-sync`
   process (`ps`), no `/tmp/juristid-host-lock`, no OneNote import request pending
   (`manage.py data_sync_requests list --status pending` is empty), no restore rehearsal running.
3. The last nightly set verified to level 2 (`VERIFIED-L2` present, from the assessment).
4. Free space on `/mnt/user` for the manifest (≈ 10 MB) and logs.
5. Nothing unexpected: **no symbolic link**, no `*.partial`/`*.tmp`, no ACL, no second owner, no
   container mount of `backups` (the assessment prints each count; any non-zero stops the run
   until it is understood — `chmod` follows links, and the procedure's `find` never does).

## 3. A fresh manifest, immediately before — and proof it is whole

RECOVERY.md step 1 writes the mode manifest. Add, **in the same minute and under the host lock**:

```sh
B=/mnt/user/backups/juristid-main; L=/mnt/user/appdata/juristid-main/backup-logs
T=$(date -u +%Y%m%dT%H%M%SZ); umask 077
find "$B" -printf '%m\t%U:%G\t%p\0' > "$L/confidentiality-modes-$T.nul"
entries=$(tr -cd '\0' < "$L/confidentiality-modes-$T.nul" | wc -c)
sha256sum "$L/confidentiality-modes-$T.nul" | tee "$L/confidentiality-modes-$T.sha256"
echo "entries=$entries" | tee -a "$L/confidentiality-before-$T.txt"
test "$entries" -eq "$(find "$B" | wc -l)" || echo "MANIFEST INCOMPLETE: stop"
```

The manifest must carry the **owner:group** too (the RECOVERY.md line omits it; the rollback
below restores modes only, ownership never changes, but the column proves nothing moved). A
rollback is refused unless `sha256sum -c` of the manifest passes and its entry count equals the
count recorded here. Keep a second copy of the manifest, its checksum and the before-state file
**off the backup tree and off the host's `appdata`** (the operator's own machine), so a damaged
host cannot take the rollback with it.

## 4. The run (RECOVERY.md steps 2–5, in this order)

1. Take the host lock (`mkdir /tmp/juristid-host-lock …`, `trap … EXIT`). While it is held the
   backup, sync and verify scripts refuse to start; confirm one does (`juristid-backup-assess.sh`
   is read-only and ignores the lock; `juristid-lovable-sync.sh --on-demand` must print «lock»).
2. `chmod 0700` the four gate directories on **every physical path** (`/mnt/user`, `disk4`,
   `mediapool`): four directories per path, never recursive.
3. The artifacts by kind and in batches of 2,000, exactly as RECOVERY.md step 4 writes them
   (`setdirs 0700`, `setfiles 0600`, `pooldirs 0700`, `poolfiles 0600`, `fpdirs`, `fpfiles`).
   Time the `sets` steps first (1,217 objects) and extrapolate before touching the pools. **Shared
   evidence pools** are one operation for all sets: do not interleave pool and set batches, and do
   not stop between a pool's directories and files for longer than the run needs (a half-done run
   is safe; every object is either as before or tightened, and re-running resumes).
4. **Stop at once, and roll back, if:** any command errors, a symlink or unexpected owner appears,
   a backup/verify/sync process starts despite the lock, free space drops, or the operator is
   unsure. Do not "fix forward" a run you do not understand.

## 5. Proof (all must pass before the lock is released)

| Check | Pass |
|---|---|
| `bash scripts/deploy/juristid-backup-assess.sh` | `open_objects_*`, `world_accessible_*` and `world_writable_dirs_*` are **all 0**, `symlinks=0`, `acl_entries_on_top_dirs=0` |
| `find "$B" -type d -perm -0002 \| wc -l` | **0** — the world-writable `evidence/` directories are gone |
| `find "$B" -perm /077 \| wc -l` | **0**, apart from the fresh set's proof exception if this runs after an APPLY (name it in the log) |
| Unauthorized read | as an unprivileged account (`su -s /bin/sh nobody -c 'ls "$B"'`), every path is refused; the same for a read-only container run as `--user 1234:1234` over `$B` |
| Level 2 on the newest and one older set | `juristid-verify-backup.sh … --level 2` (no `--record`) prints PASS for both |
| Fresh backup | the next scheduled nightly backup completes without `CONFIDENTIALITY WARNING` and verifies to level 2 |
| Restore check | a restore of one older set into a scratch root (RECOVERY.md «Rehearsing against a real set») from a now-`0600` set; delete the scratch root afterwards |
| Lovable proof | RECOVERY.md's probe: the newest (private) set answers **no** for all four names; after the next APPLY the fresh set answers yes for `VERIFIED-L2`, `SHA256SUMS`, `database.dump` and **no** for `manifest.json`, and the sync log has no `proof could not be opened` |

Release the lock only when the table is green. Record the operator, time, entry count, checksum
and each result in `operator-reports/` (root, `0700`).

## 6. Rollback — exact

Precondition: the host lock is held, and the manifest's checksum and entry count match §3.

```sh
sha256sum -c "$L/confidentiality-modes-$T.sha256" || exit 1
test "$(tr -cd '\0' < "$L/confidentiality-modes-$T.nul" | wc -c)" -eq "$ENTRIES_FROM_BEFORE_STATE" || exit 1
while IFS=$'\t' read -r -d '' mode owner path; do chmod "$mode" -- "$path"; done < "$L/confidentiality-modes-$T.nul"
```

Rehearse the loop **once on a copy first**: take 300 entries from the manifest, create a scratch
tree with the same relative names, tighten it, roll it back, `diff` the mode listings. Never
rehearse on the real tree. Then re-run §5's level-2 check. Ownership and content were never
changed, so nothing else needs undoing. Rolling back **re-opens** the backups: say so in the
record and take the decision to stay open deliberately, with the owner.

## 7. After it

- Run the assessment again the next morning and the morning after a Lovable APPLY.
- Quarterly restore rehearsal (level 3), and a rehearsal after any change to a backup script
  (`juristid-backup*.sh`, `lib.sh`, `verify`, `restore`). Put the date in `operator-reports/`.

## 8. `JURISTID_BACKUP_REQUIRE_PRIVATE=1` stays unset until

§5 passed **and** one nightly backup and one Lovable APPLY ran after it with no confidentiality
warning and a verified proof **and** the share is still not exported **and** the owner says so
in writing (RECOVERY.md step 7 names the three cron lines). Until then a destination that is
opened again only produces a warning.

## 9. Verifying the 134 sets that lack `VERIFIED-L2`: a plan, not a bulk run

Level 2 reads files and a dump listing; it takes seconds per set. It still reads the pools, so it
runs in the same window as §2, one batch at a time, never in one sweep. Order of value:

1. **The sets a rollback would use**: the set named in each release record as its pre-deploy
   backup (the release history in `docs/release-notes/` and the deployment records list them),
   newest first. These are the sets the owner would actually restore.
2. **The newest unverified sets** (the last 30 days), because they are the likeliest restore points.
3. **One per calendar month** (the first set of the month) for the older history.
4. **Every tenth of the rest**, as a sample. A sampled failure widens the sample; it never ends it.

At most 10 sets per window, `--level 2`, no `--record` (the marker `VERIFIED-L2` is written only by
the nightly script, so an old set's missing marker stays honest: «verified on <date> by the operator»
goes in the operator report, not in the set). Stop on the first failure and report it; do not repair
a set in place. The result is a list of «verified», «failed» and «not verified (history)», which the
owner may accept as is.

## 10. Off-host

Options D and B (a Koda-controlled destination) remain preferred, subject to the owner's approval
and to the organization's data-handling requirements (ADR 0240 §3). Nothing in this procedure
transfers, copies or uploads anything outside the host.
