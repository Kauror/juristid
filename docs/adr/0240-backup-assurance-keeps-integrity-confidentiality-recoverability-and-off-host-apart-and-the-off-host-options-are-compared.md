# 0240 — Backup assurance keeps integrity, confidentiality, recoverability and off-host availability apart; the off-host options are compared

Date: 2026-11-28 · Status: **Proposed** for the owner's decisions below; the assessment script is implemented in
the r98 candidate (R98-16, R98-17). Nothing in production is changed: no mode, no retention, no
`JURISTID_BACKUP_REQUIRE_PRIVATE`, no transfer, no account.

Builds on 0022 (backup and recovery), 0232 (backups are written private; the Lovable proof share) and the
prepared `deploy/unraid-main/RECOVERY.md` («Tightening what already exists») and `OFFHOST-BACKUP.md`.

## 1. Four questions, never one

| Question | Evidence | Today (read-only, 28.11.2026 12:30Z) |
|---|---|---|
| **Integrity** — does the set verify? | `VERIFIED-L2` in the set | newest set `20261128T110113Z`, 1 h old, complete, **has** `VERIFIED-L2`; 60 of 194 sets carry the marker (older sets predate it or were never verified to level 2) |
| **Confidentiality** — who can read it? | modes, ACLs, exports, mounts | **not private**: 65,179 objects group/other-accessible; 3,364 world-writable directories (all in `evidence/`); no ACL, no symlink, no `*.partial`; `backups` not exported over SMB, not mounted by any container |
| **Recoverability** — can it be restored? | a restore rehearsal of a real set | the newest set has manifest, dump and checksums; **the last restore rehearsal of a production set cannot be read from the host by this script** (operator reports hold it) |
| **Off-host availability** — does it survive losing the host? | an independent copy elsewhere | **none** (`offhost_configured=no`); the owner's 13.10.2026 decision keeps this inactive |

A recent backup is not necessarily recoverable; a verified backup is not necessarily confidential; a copy on the
same host is not a disaster-recovery copy. `scripts/deploy/juristid-backup-assess.sh [ROOT]` reports all four as
key=value lines and **changes nothing** (no chmod, no write, no lock); it was run read-only against production
for the figures above. Size for planning: `evidence/` 9.6 GB, `legacy-source/` 49 MB, one set ≈ 31 MB, all 194
sets 2.7 GB (so ≈ 12.4 GB now, ≈ 31 MB/day plus new evidence).

## 2. Remediation: review of the prepared procedure, and what r98 adds

RECOVERY.md's procedure (manifest of modes → lock → the four gate directories → artifacts by kind in batches →
level-2 + a scratch restore + the Lovable probe → rollback from the manifest) was reviewed and **kept**. It
covers the Lovable proof contract exactly: the sync takes a fresh set per APPLY and opens its own proof, so the
three files `VERIFIED-L2`, `SHA256SUMS`, `database.dump` (never `manifest.json`) are readable by group 10001 on
that set only (`share_set_proof`), and older sets need no group access. Additions the brief asks for, to be
applied by the operator **with the owner's separate approval**:

1. **Manifest integrity.** After step 1, record `sha256sum` of the `.nul` manifest and its entry count
   (`tr -cd '\0' < file | wc -c`) in the before-state file; before any rollback, re-check both. A rollback
   from an unchecked manifest is refused. Rehearse the rollback loop once on a **copy** of a few hundred
   entries (a scratch directory), never on the real tree, before the real run.
2. **Authorized identities.** Run as root on the host with the host lock; after tightening, the only readers
   are root and (for the proof files of the sync's own fresh set) gid 10001. List `getent group` and
   `docker ps` identities in the before-state so the owner sees who could read before and after.
3. **Symlinks.** `find … -type l` must print nothing before step 3 (today: 0); `chmod` follows links, so any
   hit stops the run until understood. The procedure's `find` never follows links.
4. **Shared evidence pools.** `evidence/` and `legacy-source/` are shared by every set (content-addressed
   objects): tightening a pool is tightening **all** sets' objects at once, which is why the four gate
   directories are done first and the 3,364 `0777` directories are the first thing to close. The parent share
   `/mnt/user/backups` (`0777 99:100`) belongs to every backup and is not touched.
5. **Verification and rollback** stay as written, plus the new manifest check and a post-run run of the
   assessment script whose open/world counts must all be 0.

## 3. Off-host disaster recovery — options

Neither production data nor secrets leave the host in this release; no account is created; nothing is bought.
The prepared mechanism is restic with client-side encryption (OFFHOST-BACKUP.md). Four **destinations**:

| | A. Separate physical storage, rotated by hand | B. Encrypted push to a Koda-controlled remote host | C. Encrypted S3-compatible object storage (EU region, object lock) | D. Existing organisational infrastructure (what Koda already runs) |
|---|---|---|---|---|
| RPO | the rotation interval (weekly–monthly) | 1 day (nightly) | 1 day | 1 day |
| RTO | hours (fetch the disk) | hours (copy ≈ 12 GB back) | hours (download ≈ 12 GB; transfer time/egress to be quoted) | hours; depends on that system's access process |
| Confidentiality / encryption | must be encrypted before it leaves the room (LUKS or restic); a lost disk is then ciphertext | restic client-side: the remote host sees ciphertext only | restic client-side: the provider sees ciphertext only; **a new third-party processor** for Koda's legal material | restic ciphertext into whatever store; the host organisation sees ciphertext only |
| Key custody | the repository key and the secrets escrow held by ≥ 2 named custodians (OFFHOST §3.4) | same | same; plus a separate prune key that can delete, held off the host | same |
| Config and secrets recovery | RECOVERY.md's secrets escrow must travel separately from the data | same | same | same |
| Storage | one ≈ 16–32 GB disk | ≈ 15 GB growing ≈ 1 GB/month | ≈ 15 GB; **price not verified here** | whatever quota exists |
| Operational effort | highest (a person, every time) | low once set up; needs a host to exist | low once set up; needs an account and a contract | low; needs a request to Koda IT |
| Restore verification | manual drill | `restic check` + a quarterly scratch restore | same | same |
| Data-handling implications | stays inside Koda's custody; physical-loss risk handled by encryption | stays inside Koda | **leaves Koda's control to a provider; requires a data-processing agreement, location confirmation and the owner's explicit decision** | depends on that infrastructure's agreements; the master specification already names an Azure target |

**Recommendation.** Do not choose a provider in this round. In order: **D or B** (Koda-controlled, no new
processor) as the target if Koda IT can offer one; **A** as an interim measure that can start without any new
contract (an encrypted disk kept at another site, refreshed monthly, RPO ≈ 1 month); **C** only if neither is
available, and only after a data-processing agreement. In every case: restic's client-side encryption, a push
key that cannot delete, a separate prune key with custodians, `restic check` after every push, a restore
rehearsal into a scratch root at least quarterly, and the secrets escrow stored apart from the data.

## Owner decisions needed

1. Approve the tightening of existing backup permissions (and when), using RECOVERY.md plus the additions in §2.
2. Only after (1) and a clean nightly cycle: whether to set `JURISTID_BACKUP_REQUIRE_PRIVATE=1`.
3. The off-host destination class (A/B/C/D) and who holds the repository and prune keys.
4. Restore-rehearsal cadence (proposed: quarterly, and after any change to backup scripts).
5. Whether the 134 sets without `VERIFIED-L2` should be verified (read-only, level 2) or left as history.
