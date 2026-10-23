# Backup, restore and disaster recovery — the real-data instance

This is the one place that answers "how do we get it back". `README.md` beside
it covers deploying and running the stack day to day; everything about losing
things and recovering them is here.

Everything below runs on the Unraid host. Nothing in it may be run against
`juristid-test`, and the scripts refuse that project by name.

## The short version

| | |
| --- | --- |
| Back up | `scripts/deploy/juristid-backup.sh` — database, evidence, page XML |
| Check a backup | `scripts/deploy/juristid-verify-backup.sh` — levels 1 and 2 |
| Check a backup was taken *lately* | `scripts/deploy/juristid-check-backup-age.sh` |
| Prove the procedure | the `recovery` job in CI, on synthetic data |
| Prove a *real* set | `deploy/recovery-rehearsal/compose.real-data.yml` — done once, 03.09.2026, see [Rehearsing against a real set](#rehearsing-against-a-real-set) |
| Restore | `scripts/deploy/juristid-restore.sh`, then verify, then publish |
| Off-host copy | **not yet arranged** — see [Disaster recovery](#disaster-recovery) |
| Known gaps | [What is not yet fixed](#what-is-not-yet-fixed) — DR0, DR1-A, DR1-B, DR1-C |

## What has to survive, and what does not

Five storage classes, and the difference between them is the whole plan. Backing
everything up indiscriminately costs disk and hides which parts actually matter;
backing up the wrong subset is worse.

| Class | Where | Recovery class |
| --- | --- | --- |
| PostgreSQL | `…/juristid-main/postgres` | **canonical — must be backed up** |
| Evidence | `…/juristid-main/evidence` | **canonical — must be backed up** |
| OneNote page XML | `…/juristid-main/legacy-source` | **canonical — must be backed up** |
| Derivatives | `…/juristid-main/derivatives` | rebuildable — needs no backup |
| Held uploads and `Uus teema` staging | Docker volume `juristid-main_pending_uploads`, at `/app/pending-uploads` | ephemeral — needs no backup, and is in no set |
| Search projection | inside PostgreSQL | rebuildable — comes back empty and is rebuilt |
| Historical corpus | `/mnt/user/juristid-main/source` | source — read-only input, own recovery path |
| Secrets | `…/juristid-main/config/juristid.env` | secret — never in a set; **where it is backed up is not recorded** (DR1-C) |
| Tunnel credential | `…/juristid-main/cloudflared` | secret — never in a set; reissuable, but likewise unrecorded (DR1-C) |

**Derivatives are genuinely safe to skip.** Extracted text, thumbnails and the
search index are regenerated from the evidence by
`rebuild_document_derivatives --all` and `rebuild_search_index`, and a test
asserts the regenerated corpus is identical to the original down to the content
hashes. That test is the reason skipping them is a decision rather than a hope
(docs/adr/0014).

**The page XML is not a derivative.** It is source evidence, it is the only copy
the application controls, and it lives in its own directory precisely so that
nobody deletes it while clearing out rebuildable material (docs/adr/0015).

**Held uploads are nobody's evidence.** When a save on `Uus teema` is refused,
the files it had already received are kept for the length of the refusal so the
person does not lose them — a browser cannot put a file back into a file input.
They describe nothing, no row points at them, and the oldest of them is minutes
old; losing every one of them costs somebody one re-pick. There is nothing here
to back up and nothing to restore. They are swept by age on the next hold
(`app/documents/pending.py`).

**And the same directory holds `Uus teema` staging, under `intake/`.** A file
chosen while a Teema is being created is uploaded straight away so the
extraction worker can read it and offer the deadline, the sender and the
Menetlusliik back on the form; it becomes evidence only when `Loo teema`
succeeds, through the ordinary services, from those exact bytes. Until then it
is the same kind of thing as a held upload — one person's unfinished form,
described by no register row, worth one re-pick if it is lost — so it shares the
storage class and the same "no backup" answer (docs/adr/0064).

**It is a Docker volume, and that is a topology answer rather than a recovery
one.** This directory used to be an unmounted path inside each container, which
was right while one process both wrote and read it. Staging gave it a second
reader in a second container: `web` receives and stores the file, and the
`intake-reader` container opens those exact bytes to parse them. A container's
writable layer is private to that container, so unmounted meant the worker
looked for a file that existed only in `web` and every staged read failed. Both
services now mount one project-scoped named volume at `/app/pending-uploads` —
`web` read-write, because it stages, holds, promotes and deletes; `intake-reader`
read-only, because opening the object in `rb` is the whole of what it does with
it.

Three things follow, and none of them changes what this data *is*:

- **It is not backed up, and must not be added to one.** `juristid-backup.sh`
  does not know about this volume and the recovery fingerprint does not cover
  it. Being on a volume rather than in a container layer makes it survive an
  ordinary container replacement; that is convenient and means nothing. What
  bounds this data is expiry and `prune_intake_staging`, exactly as before.
- **Losing it costs unfinished forms and no evidence.** Anyone mid-`Uus teema`
  re-picks their file. Nothing that reached `Loo teema` is here at all — that
  is a Document with an immutable version, in the evidence tree, in the backup.
- **A restored PostgreSQL set may name bytes that are gone.** The staging rows
  in `matters_matterintakesession` and `matters_matterintakefile` are inside the
  database and therefore inside the set, while the volume is not; a restore can
  leave rows pointing at objects that no longer exist. This is harmless and
  already handled: those sessions have expired by the time anyone sees them, and
  `prune_intake_staging` removes an expired session whether or not its bytes are
  still there.

**It is not a bind mount under appdata.** There is deliberately no
`/mnt/user/appdata/juristid-main/pending-uploads`. A Docker volume keeps these
temporary uploaded documents out of the appdata tree, and so out of the SMB
share exported from it — which is a separate, known host-security matter that
this arrangement must not widen.

One further operational difference from held uploads is worth knowing: staged
files are **not** swept by the held-upload path, which only looks at the root of
the directory. A staging session is removed from its own row by `manage.py
prune_intake_staging`, and it cannot touch evidence — it reads neither the
evidence store nor `DocumentVersion`. For each session it removes, it deletes
everything under that session's own `intake/<session id>/` prefix, including an
object whose row never committed; it never lists or deletes any other session's
prefix.

### Staging quotas and the scheduled sweep

Since docs/adr/0177 (csf_3942255b466a4e213b958b16) staging is bounded on the way
in and swept on a timer, so this volume no longer depends on somebody
remembering a command.

**On the way in.** Before a staged file is written, under one database lock, the
application refuses — with a sentence on the form, never a 500 — a file that
would take a person past their active forms or bytes, staging past its
deployment-wide forms or bytes, or the volume below its free-space headroom. The
limits are environment variables with defaults; set them in
`/mnt/user/appdata/juristid-main/config/juristid.env` only to change a default:

| Variable | Default | What it bounds |
| --- | --- | --- |
| `INTAKE_STAGING_MAX_ACTIVE_SESSIONS_PER_OWNER` | 20 | one person's unfinished forms that hold a file |
| `INTAKE_STAGING_MAX_BYTES_PER_OWNER` | 2147483648 (2 GiB) | one person's staged bytes in unconsumed forms |
| `INTAKE_STAGING_MAX_ACTIVE_SESSIONS` | 200 | everybody's unfinished forms that hold a file |
| `INTAKE_STAGING_MAX_BYTES` | 10737418240 (10 GiB) | everybody's staged bytes in unconsumed forms |
| `INTAKE_STAGING_MIN_FREE_BYTES` | 1073741824 (1 GiB) | free space left on the volume after the write (staging and held uploads) |
| `PENDING_UPLOAD_MAX_HELD_BYTES` | 2147483648 (2 GiB) | everybody's held uploads (a refused form's files, at the volume's root) |
| `INTAKE_STAGING_SWEEP_MAX_SESSIONS` | 500 | sessions one sweep removes |
| `INTAKE_STAGING_SWEEP_MAX_BYTES` | 5368709120 (5 GiB) | staged bytes one sweep removes |
| `INTAKE_STAGING_SWEEP_INTERVAL_SECONDS` | 900 | pause between sweeps |
| `INTAKE_STAGING_SWEEP_STALE_SECONDS` | 3600 | how long a stale session may wait before the probe is red |

The free space is that of the filesystem under `/app/pending-uploads` — on this
host Docker's own storage, which every container shares. Before the first
release that carries this, read it, and lower the headroom if 1 GiB is not
available, or every staged file will be refused:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web df -h /app/pending-uploads
```

**The sweep.** The `intake-sweeper` service runs `prune_intake_staging --loop`:
one bounded sweep every 15 minutes of forms past their expiry and forms a Teema
has consumed, and of held uploads older than `PENDING_UPLOAD_GRACE_HOURS`. A form that is still open and not expired is never touched. The
container mounts the staging volume read-write and nothing else, so the evidence
tree is not reachable from it; inside the volume it follows no symbolic link and
leaves a session whose tree is not what the application wrote — a link, a
directory where a file belongs, a row naming a key outside its own session —
untouched and logged as an error. It is a release-image service like `web`, so
a release replaces it with the others; there is no host cron entry and nothing
is installed outside Compose.

Installing it is part of the first release that carries it. After the release's
usual dry run (README, "Deploying a release" — `db` and `tunnel` must not move),
the named form now lists four services:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d --no-build --no-deps web intake-reader searchindex intake-sweeper notifications
```

That starts no extractor — this stack has none — and does not touch `db` or
`tunnel`. To see it working:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml logs --tail 20 intake-sweeper
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T intake-sweeper python manage.py check_intake_staging
```

`check_intake_staging` is also the container's healthcheck. It is red when a
stale session has waited longer than `INTAKE_STAGING_SWEEP_STALE_SECONDS` — the
sweeper is stopped, failing, or refusing an unsafe session — or when staging is
full or the volume is short of its headroom. A one-off sweep by hand prints one
summary line and exits 1 if it refused a session:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T intake-sweeper python manage.py prune_intake_staging --dry-run
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T intake-sweeper python manage.py prune_intake_staging
```

A refused session is a staging tree somebody changed by hand. Look at it
(`ls -la` inside the container, under `/app/pending-uploads/intake/<id>`),
remove what does not belong there yourself, and the next sweep takes the rest.
Stopping the service (`stop intake-sweeper`) is safe at any time: nothing is
half-deleted, and expired forms simply wait for the next run. Every runbook that
holds the writers (`stop web intake-reader searchindex …`) holds it too, because
it connects to the database.

The MCP server (`mcp`, docs/adr/0185) is not a writer and need not be held: its
connections are read-only transactions, so a backup taken while it runs is the
same backup. During a *restore* stop it anyway (`stop mcp-tunnel mcp`), so that
nobody's ChatGPT is answered from a database that is being replaced.

The MCP write gateway (`mcp-writer`, docs/adr/0189) **is** a writer: it commits
approved AI changes as its own role, `juristid_mcp_writer`. Every runbook that
holds the writers holds it too, and a restore stops it with the other two
(`stop mcp-tunnel mcp mcp-writer`; stopping a service that is not running is a
no-op). It stays stopped until somebody starts it by name
(`docs/mcp/OPERATIONS.md`, "Switching AI writes off").

## Backing up

**Every command in this file runs from the checkout.** The scripts are named
`scripts/deploy/…` and the Compose file `deploy/unraid-main/compose.yml`, both
relative to the repository root, so start every shell this file is used in with:

```bash
cd /mnt/user/appdata/juristid-main/repo
```

Written out in full, because the arguments are the safety — the project and the
compose file are named rather than discovered, and the script refuses any
project it does not recognise:

```bash
scripts/deploy/juristid-backup.sh \
  --project juristid-main \
  --compose-file /mnt/user/appdata/juristid-main/repo/deploy/unraid-main/compose.yml \
  --data-root /mnt/user/appdata/juristid-main \
  --backup-root /mnt/user/backups/juristid-main
```

It produces:

```
/mnt/user/backups/juristid-main/
  evidence/                     shared byte pool, append-only
  legacy-source/                shared byte pool, append-only
  sets/20260822T190000Z/
    database.dump               PostgreSQL custom format
    manifest.json               what this set is, in non-secret metadata
    SHA256SUMS
    evidence.files0             which evidence objects are this set's
    legacy-source.files0        which page-XML files are this set's
```

The script writes only those. Other things live in the same root and are **not
part of any set**:

| Path | What it is | Restorable by `juristid-restore.sh`? |
| --- | --- | --- |
| `fingerprints/` | recovery fingerprints taken by hand before an operation ([Fingerprinting the live system](#fingerprinting-the-live-system)) | no — they are the comparison, not the data |
| `operator-reports/` | operator output from data operations, mode 700 | no |
| `evidence-2026-08-21-1601/`, `*.sql.gz`, `legacy-source-*.tar.gz` | pre-set backups from August 2026, before this layout existed | no; the `.sql.gz` dumps restore by hand with `psql` |

Whether the pre-set leftovers are kept, and whether they belong in an off-host
copy, is undecided ([`OFFHOST-BACKUP.md`](OFFHOST-BACKUP.md)). Nothing here
deletes them.

The dump is of the `juristid` database only (`pg_dump --dbname=juristid`). A
database kept beside it in the same cluster as a rollback point — the
`juristid_pre_*` copies some data operations leave — is in no set and lives only
in the cluster's data directory. The recovery point for such an operation is the
set taken before it, not the copy.

### The pool is bytes; the set is membership

Those two directories are a **byte pool**. They are shared between every set,
they never delete, and they hold every object that has ever existed. They are
not a statement about any one backup.

Which objects were *active* when a set was taken is a different fact, and until
12.09.2026 nothing wrote it down. "Restore this set" therefore meant "copy the
pool", which is the same thing only while nothing has ever left the active tree.

The operational reset made them different things. Active evidence went to zero;
the pool kept its 20 068 pre-reset objects, because these pools never delete. So
the set taken that day — an accurate record of a deployment holding no evidence
at all — would have restored an empty register beside twenty thousand orphaned
files, a state the system has never been in.

From **manifest version 3** each set carries one **membership inventory** per
pooled tree: `evidence.files0` and `legacy-source.files0`, listing the relative
paths that were active when the set was sealed. NUL-delimited and sorted, so the
list is right for exactly the filenames a line-based list gets wrong, and so the
same tree always produces the same bytes. They are covered by `SHA256SUMS`,
because a set whose dump is protected and whose membership can be edited is a
set whose restore can be steered without breaking a checksum.

The bytes still live once, in the pool. The membership is the set's own, and it
is what verification and restore obey.

**An empty inventory is an ordinary answer**, not a special case: it says this
set's tree held nothing, and restoring the set produces nothing. No sentinel
file, no invented row. Zero is a real production state now.

`empty_source_allowed_for` is unchanged and keeps its old meaning: an audit note
recording that the operator relaxed the empty-tree guard with `--allow-empty`.
It is **not** evidence that a tree was empty — a stale flag left in a runbook
after the tree filled up again is recorded identically, and the backup says so
out loud when that happens. What the tree held is the inventory, measured.

### Why the format changed

The instruction this replaces was:

    docker exec juristid-main-db pg_dump -U juristid juristid | gzip > out.sql.gz

That has one failure mode worth a script. If `pg_dump` dies halfway — disk full,
connection dropped, the container restarted — `gzip` still succeeds on what it
received, the redirect still produces a file, and the shell still reports
success, because a pipeline's exit status is its last command's. The result is a
truncated dump that passes `gzip -t`, sits among the good ones, and is
discovered on the day it is needed.

The custom format removes the pipeline rather than guarding it: `pg_dump`
compresses on its own, so there is no second process whose success can stand in
for the first one failing. It also buys `pg_restore --list`, which is the
difference between "this file decompresses" and "this file contains the tables
it should" — and `pg_restore --file=/dev/null`, which level 2 now runs as well:
it reads and decompresses every data block and restores nothing, so a dump cut
inside its data fails verification rather than a restore (ENG-141). `--list`
alone reads only the table of contents, and a dump written through a pipe has
no data offsets in it.

Older `.sql.gz` backups remain restorable and are not invalidated — restore one
with `psql` as the rehearsal runbook describes. New sets are custom format.

### Why the evidence is synchronised twice

`pg_dump` is transactionally consistent with itself and with nothing else. This
system writes evidence bytes *before* the `DocumentVersion` row describing them
commits, so a filesystem copy taken before the dump can be missing an object
whose row the dump contains.

So the mirror is synchronised twice, once before the dump and once after. Any
object whose row is in the dump had its bytes written before the dump began, so
one of the two passes has it. The reverse — an object in the mirror with no row
— is harmless and already has a name and a command: an orphan, and
`prune_orphaned_evidence`.

That argument covers objects being *written*. Objects are also removed: an
evidence object is immutable, but since Kustuta teema (ADR 0096) a deletion
removes a Teema's rows in one transaction and its objects straight after that
commits, and `prune_orphaned_evidence` removes objects no row names. A deletion
that commits while the dump runs leaves its rows in the dump and its objects
gone from the tree by the end — so a set whose membership was read only after
the dump restored that Teema without its bytes (ENG-114).

So membership is read at **both ends** of the dump. As the dump begins, the
script lists the trees and copies exactly those objects into the pool before
`pg_dump` starts; as it ends, it lists them again. The set names the union,
less any object that vanished before the dump began (its rows' deletion had
already committed, so the dump does not describe it). Every member must be in
the pool, or the backup stops and leaves `….partial` — run it again. An object
named whose row did not reach the dump is an orphan after a restore:
`check_evidence_integrity` names it and `prune_orphaned_evidence` reclaims it. A
pool with extras, never a set with gaps.

The pools themselves stay append-only: the script never removes anything from
them.

### What the script will not do

It never deletes anything: not an old set, not a mirror entry, not another run's
partial work. No retention policy has been agreed
(`docs/open-decisions.md`), and creating backups safely is the more urgent half.
When a retention rule is decided, it gets its own reviewed script.

A failed run leaves a directory named `…​.partial` and exits non-zero. That name
is deliberate: a crash produces something obviously incomplete rather than
something plausible.

Sets are named to the second, so two backups started inside the same second ask
for the same directory and the second one refuses rather than writing into a set
it did not create. That is the safe direction and it is left as it is — but it
is worth recognising the message, because "a backup set already exists" from a
run you started by hand a moment after the scheduled one is about the clock, not
about anything being wrong. Wait a second and run it again.

## Checking a backup

Three levels, because "the backup is verified" gets said about all three and
only the last one means much.

| Level | Proves | Cost |
| --- | --- | --- |
| 1 | the files are present, non-empty, and hash to what the set recorded | seconds |
| 2 | the archive is a PostgreSQL dump whose contents list the right tables, **and** both byte pools still hold what the manifest recorded, **and** every object this set names is still in them | seconds |
| 3 | the set restores and the application reads the register back out of it | minutes |

Levels 1 and 2:

```bash
scripts/deploy/juristid-verify-backup.sh --project juristid-main --compose-file deploy/unraid-main/compose.yml --set /mnt/user/backups/juristid-main/sets/20260822T190000Z
```

The backup script runs both before it renames a set into place, so a set that
exists has already passed them.

### Two questions about the same directory

**Is the pool still there?** The manifest has always recorded how many files
each pool held and how many bytes they came to; nothing read those numbers back,
so a set could pass every check it had while the evidence it depends on had been
emptied — and the way that gets discovered is by needing it. Level 2 recomputes
both and compares:

* **fewer files than recorded** is a failure. Objects are gone.
* **fewer bytes with the right file count** is a failure. Something was
  truncated in place.
* **more of either** is reported and is not a failure. The pools are shared
  between sets rather than copied per set, and nothing ever removes an object
  from a pool, so an older set verified today is *supposed* to find more than
  it recorded.

**Does this set still have its own objects?** From manifest version 3, level 2
also reads the set's membership inventory and proves every path in it is a
regular file in the pool, and that the members' sizes add up to **exactly** the
byte total the set recorded. Exactly, not at least: the pool grows, membership
does not — which is what lets a member truncated in place be caught by
arithmetic rather than by hashing the whole pool.

A pool-wide count cannot answer the second question. "The pool holds at least N
files" is satisfiable entirely by history that has nothing to do with this set —
a set naming zero objects beside a pool of twenty thousand is complete, and a
set naming one object that is gone is not, however large the pool is.

The inventory is also checked for shape before it is read for content: an entry
that is absolute, that contains `..`, or that repeats is refused rather than
followed. These are trusted operational artifacts and the check is there anyway,
because a restore turns each of those strings into a path it writes to.

It all runs before the compose file is even required, because there is no reason
to make somebody start a container to be told the evidence is missing. Pass
`--backup-root` if a set has been moved away from its pools, and
`--no-mirror-check` to verify a set as a file rather than as a backup — which is
what a set copied without its pools is.

`juristid-restore.sh` passes its own `--backup-root` to this verification, so a
set fetched back from an off-host copy is verified against the same pools it is
about to be restored from; with `--database-only` it passes `--no-mirror-check`,
because that restore reads no pool (ENG-054).

**A set at manifest version 1 or 2 records no membership**, and nothing can
invent one for it after the fact. Those sets stay verifiable on pool counts, and
the verifier names what it is not proving rather than passing quietly.

**It hashes nothing.** Two sizes, which are easy to confuse: the **pool** under
the backup root holds every object that ever existed (about 7.6 GB, 20 196
files, measured 13.10.2026), while the **live** evidence tree and a recent set's
membership are small (4.0 MB and 13 objects on the same day, after the
September reset). Both are dated observations; re-measure rather than quote
them. A routine check that reads the pool is a check somebody switches off,
and the live tree can grow back to the old size. Proving the bytes is
`recovery_fingerprint` without `--skip-evidence-bytes`, and that stays a
deliberate exercise.

**Byte totals are only compared on manifest version 2 and later.** Version 1
recorded `du -sk`, which counts allocated blocks — a property of the filesystem
the mirror happens to sit on, not of the data. Comparing that against a copy on
different storage would fail on a good off-host set and could pass on a bad
local one. Version 2 records the sum of the files' own sizes, which is the same
number anywhere. Older sets are still checked on file count, and the verifier
says which comparison it skipped and why.

### Has a backup been taken lately?

Verification says a set is intact. It says nothing about whether one was taken
this week. As of this writing nothing schedules the backup at all — every set on
the host was taken by hand before a deployment — so the answer has to be asked
for:

```bash
scripts/deploy/juristid-check-backup-age.sh --backup-root /mnt/user/backups/juristid-main --max-age-hours 24
```

Read-only. It reads directory names and the three files a finished set holds;
it opens no dump and starts no container.

| Exit | Meaning |
| --- | --- |
| 0 | a complete set exists and is inside the limit |
| 1 | the arguments or the backup root are wrong |
| 2 | no complete set exists at all |
| 3 | the newest complete set is older than the limit |

Three codes rather than "non-zero", because "the backups have stopped", "there
have never been any" and "you typed the path wrong" have three different first
moves.

**`--max-age-hours` is required and has no default.** The RPO — how much work
the Chamber is willing to lose — is a decision for the people who would lose it,
and a number written into a script becomes policy the day somebody reads it as
one. 24, 4 and 1 are all defensible; none of them is chosen here
(`docs/open-decisions.md`).

A `.partial` directory never counts as a set. It is reported, because an
unfinished run is worth looking at, but it is not a backup.

**Level 3 is not run against production.** It is the `recovery` job in
`.github/workflows/ci.yml`: a synthetic register is seeded, backed up by this
same script, the database and the storage trees are destroyed, the same restore
script brings them back, and the canonical fingerprint — row counts, evidence
byte digests, page-XML digests, migration leaves — has to match what was
recorded before. It runs on every pull request, and on main only when
`main-proof.yml` (or a person) dispatches `ci.yml` there; main pushes no longer
run it by themselves.

CI runs the command directly on the runner, not inside a container, so its
`--out` and `--compare` paths are ordinary runner paths. That proves what the
command measures. It does not prove how a file gets into or out of the
production `web` container — which is why the commands below differ in shape
from CI's.

That proves the procedure. It does not prove any particular production set, and
nothing except restoring one would.

### Fingerprinting the live system

**The `web` container does not mount the backup root.** `compose.yml` gives it
evidence, derivatives, page XML, the read-only corpus and the upload volume —
nothing under `/mnt/user/backups`. So a host path handed to `--out` or
`--compare` inside `exec` names a file the process cannot see: `--out` fails or
writes into the container's own layer, which the next release throws away, and
`--compare` reports that it cannot read the file. Earlier versions of this file
printed exactly that, and a release stopped on it (2026-10-03).

What works, and what every release since has run: the fingerprint goes to
**stdout**, and the **host** shell redirects it into the host file. From the
checkout:

```bash
cd /mnt/user/appdata/juristid-main/repo
```

```bash
mkdir -p /mnt/user/backups/juristid-main/fingerprints
```

```bash
FP=/mnt/user/backups/juristid-main/fingerprints/fp-before-<what-you-are-about-to-do>.json
```

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py recovery_fingerprint --skip-evidence-bytes > "$FP"
```

Then prove the file is a fingerprint before relying on it — the redirect
creates the file even when the command fails, and an empty file is not a
fingerprint:

```bash
test -s "$FP" && grep -q '"fingerprint_version"' "$FP" && echo "fingerprint OK: $FP"
```

No `OK`, or a non-zero exit from the `exec` above: move the file aside, keep
its error output, and stop. Never add `2>&1` to the capture — warnings would
land inside the JSON.

To compare later, hand the file to the container on **stdin**, then compare
against the copy inside it. `-T` is what lets the redirect reach the process:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web sh -c 'cat > /tmp/fp-before.json' < "$FP"
```

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py recovery_fingerprint --skip-evidence-bytes --compare /tmp/fp-before.json
```

It prints the current fingerprint first and the verdict last; the **exit
status** is the verdict. 0 and «Canonical state matches the earlier
fingerprint» means identical. Non-zero names every field that moved. A
fingerprint taken with `--skip-evidence-bytes` compares cleanly against one
taken without it and the other way round: the evidence comparison uses the
count, the byte total and the digest rollup the database records, never how
hard either run looked.

`/tmp` inside the container is gone when the container is replaced. Copy the
file in again after a `up -d` that recreated `web`, and with `run --rm`, which
starts a fresh container every time, use a mounted path instead (the rehearsal
below does).

Non-secret: counts, digests and schema state, no content and no filenames.

**A fingerprint describes the live system at the moment it was taken. It does
not describe a backup set, and it cannot.** This is worth stating plainly
because the file this runbook used to name — `fingerprint-latest.json` — read
like a property of the newest backup, and an operator comparing a restore
against it would have been comparing it against a different moment.

Three reasons it cannot be a property of a set, in the order they bite:

1. **`pg_dump` has its own snapshot.** The dump is consistent as of the instant
   it started. A `recovery_fingerprint` run beside it reads a second connection
   at a second instant, and every entry, upload or assignment in between makes
   the two disagree. A restore that came back *perfectly* would then be reported
   as wrong.
2. **The evidence and page-XML halves read a filesystem, which has no snapshot
   at all.** The backup's own consistency argument for the mirrors is
   deliberately a *superset* argument — the mirror may hold objects the dump
   does not, and that is correct and expected. A fingerprint asserts equality,
   and equality is the wrong relation for that half.
3. **A fingerprint of a set would have to come from the set.** Which means
   restoring it, which is level 3, which is the rehearsal.

So what a fingerprint is actually for is a **round trip**: take one before a
risky operation, do the operation, take another, `--compare`. That is how the
CI rehearsal uses it and how the files in
`/mnt/user/backups/juristid-main/fingerprints/` (`fp-before-release-<sha12>.json`,
one per release) were used for the release they are named after. Name the file after the operation, not after "latest": a file
called `latest` that is nine days old is worse than no file.

What a fingerprint leaves open, and what closed it: levels 1 and 2 prove a set
is intact and structurally right, and the CI level 3 proves the *procedure* on
synthetic data. None of them proves that a **production** set comes back. That
was closed on 03.09.2026 by restoring one — see
[Rehearsing against a real set](#rehearsing-against-a-real-set) — and it stays
closed only for the set that was tested. It is a thing that is done
periodically, not a property the system now has.

Drop `--skip-evidence-bytes` to re-hash every stored object. That reads the
whole evidence tree, so it is a deliberate exercise rather than a routine one.

### Rehearsing against a real set

The CI rehearsal restores synthetic data, which proves the procedure. Once in a
while the thing itself has to be proved: a real set, the real mirrors, the image
that is actually deployed.

**Done once, on 03.09.2026.** What it established, so the next reader does not
re-derive it:

| | |
| --- | --- |
| Set | `20260902T182850Z` — 18,245,754 bytes, 811 archive entries |
| Target | an empty, disposable **PostgreSQL 18.6** cluster; production is 18.6 |
| `pg_restore` | succeeded |
| Duration | **≈ 53m46s** end to end — 19,893 evidence files and 755 page-XML files copied, then the database |
| Application | `deployment_readiness` passed on the deployed image; no pending and no unknown migrations |
| Evidence bytes | **all 19,830 objects the restored database refers to were deep-hash verified** — `recovery_fingerprint` without `--skip-evidence-bytes`, ~2m33s, and the rollup matched what the database said it should be |
| Integrity | `check_evidence_integrity` found no missing and no truncated object; the only finding was the 63 known orphans, which are a pre-existing property of the store and restored faithfully |
| Canonical state | the restored fingerprint was **identical to live production's** — every canonical count, the evidence rollup and the page-XML rollup |

Most of the wall-clock is the evidence mirror, not the database. Treat that
figure as one measurement on a loaded parity-backed array, not as an RTO: an
RTO is a decision nobody has taken (`docs/open-decisions.md`).

**What it did not prove.** This matters more than the result, because a
rehearsal that is remembered as broader than it was becomes a false assurance:

* **It was a same-host, same-disk exercise.** The scratch stack ran on the same
  machine and the same array as production. It says nothing about surviving the
  loss of either — see
  [A backup on the same disk is not disaster recovery](#a-backup-on-the-same-disk-is-not-disaster-recovery).
* **No off-host recovery was proved**, because there is no off-host copy to
  recover from. Scenario D is still the one this system cannot answer.
* **Secrets and tunnel recovery were not proved.** The rehearsal generated
  throwaway values; it did not, and could not, demonstrate that the real
  environment file and the tunnel credential can be recovered. See
  [Secrets](#secrets) and [Cloudflare tunnel recovery](#cloudflare-tunnel-recovery).
* **The source corpus was not independently recovered.** It was mounted
  read-only from production, which proves the application is compatible with
  it — never that the corpus itself comes back.
* **The set was one release behind the running application.** Backups are taken
  before a deploy, so the newest set described the previous revision. It was
  harmless because that release carried no migration, and `deployment_readiness`
  confirmed the schema agreed. It would not have been harmless otherwise, and
  that is its own open item below.

**Running the next one.** `deploy/recovery-rehearsal/compose.real-data.yml` is
the stack to use; its header carries the preparation steps, and
`real-data.env.example` beside it is the throwaway environment. Do not reuse
`deploy/recovery-rehearsal/compose.yml` — that one is synthetic-only and
publishes a host port. Never copy production's environment file into either.

From the checkout, with the scratch root's variables exported as the stack's
header says:

```bash
cd /mnt/user/appdata/juristid-main/repo
docker compose -p juristid-recovery-rehearsal -f deploy/recovery-rehearsal/compose.real-data.yml up -d --wait db
scripts/deploy/juristid-verify-backup.sh --project juristid-recovery-rehearsal --compose-file deploy/recovery-rehearsal/compose.real-data.yml --set /mnt/user/backups/juristid-main/sets/<stamp> --backup-root /mnt/user/backups/juristid-main --level 2
scripts/deploy/juristid-restore.sh --project juristid-recovery-rehearsal --compose-file deploy/recovery-rehearsal/compose.real-data.yml --set /mnt/user/backups/juristid-main/sets/<stamp> --backup-root /mnt/user/backups/juristid-main --data-root "$JURISTID_REHEARSAL_DATA_ROOT"
docker compose -p juristid-recovery-rehearsal -f deploy/recovery-rehearsal/compose.real-data.yml run --rm -T web python manage.py deployment_readiness
docker compose -p juristid-recovery-rehearsal -f deploy/recovery-rehearsal/compose.real-data.yml run --rm -T web python manage.py recovery_fingerprint > "$JURISTID_REHEARSAL_DATA_ROOT/fp-restored.json"
docker compose -p juristid-recovery-rehearsal -f deploy/recovery-rehearsal/compose.real-data.yml run --rm -T web python manage.py check_evidence_integrity
```

The fingerprint is full-bytes (no `--skip-evidence-bytes`) and goes to stdout,
redirected into the scratch root on the host — the same shape as on production.

To compare it with the fingerprint production took at the release that set
belongs to, put that file where the rehearsal container can read it. Its
`derivatives/` is mounted at `/app/derivatives`; `run --rm` starts a fresh
container each time, so a copy into its `/tmp` would not survive to the next
command:

```bash
cp /mnt/user/backups/juristid-main/fingerprints/fp-before-release-<sha12>.json "$JURISTID_REHEARSAL_DATA_ROOT/derivatives/fp-before.json"
docker compose -p juristid-recovery-rehearsal -f deploy/recovery-rehearsal/compose.real-data.yml run --rm -T web python manage.py recovery_fingerprint --compare /app/derivatives/fp-before.json
```

That pair is "near-identical", not a proof: the release fingerprint was taken a
minute after the set, from the live system, and anything written in that minute
is a real difference (see the three reasons above).

Then take the stack down with the volume-removing form of
`docker compose down` — the one this runbook forbids everywhere else — and
delete the scratch root. It holds real member material, the rehearsal is the
only reason it exists, and nothing schedules its removal but you.

### `recovery_fingerprint` and `check_evidence_integrity` are not the same check

They both read evidence, they both hash bytes when asked to, and they are not
interchangeable. Reaching for one when the question calls for the other is the
mistake worth naming, because both exit zero and neither says it answered a
different question.

| | Asks | Needs | Finds |
| --- | --- | --- | --- |
| `recovery_fingerprint --compare` | is this the same canonical state as before? | an earlier fingerprint | a row, an evidence object or a migration leaf that did not come back |
| `check_evidence_integrity` | do the store and the database still describe each other? | nothing but the deployment | a row whose object is gone, an object nothing refers to, a size or checksum that disagrees, a document whose current version belongs to another document, a submission whose final evidence is filed under another teema or reads as less restricted than the submission itself, an extraction stuck mid-flight |

So a fingerprint comparison proves a *restore*. It cannot notice an orphaned
object, because an orphan was never in the fingerprint and never will be. The
integrity check proves the *live system* at one moment. It cannot notice that
forty Matters are missing, because it has nothing to compare against. A restore
wants both, in that order, and the runbook above runs both.

Its findings are not all the same kind of problem, and the summary line says
which. Missing bytes, a size or a checksum that disagrees: restore. A
relationship that is wrong — evidence filed under another teema, evidence less
restricted than the submission that relies on it — is not a storage fault at
all. The bytes are present and are what was hashed, a backup holds the same
relationship, and which side to change is a decision about the record of what
Koda sent. Do not repair those rows to make the check go quiet.

Neither is scheduled. `check_evidence_integrity` is cheap enough to be, and if
that is ever wanted it is the structural pass — never `--verify-sha`, which
reads the whole store. Nothing in this repository installs a cron entry; the
host's scheduling is an operations decision that has not been taken.

## Restoring

### Order

1. install the reviewed application version — a checkout at a known commit for
   the Compose file and the scripts, and that commit's release image, built
   off-host by `release-image.yml`, verified and `docker load`ed as
   `juristid-main-web:<sha12>` (`README.md`, "Deploying a release"). The host
   does not build it, on a fresh host least of all;
2. restore the secrets and the configuration (by hand, from wherever they are
   kept — never from a backup set);
3. create the appdata directories with the right ownership;
4. start **only** the database;
5. restore evidence and page XML;
6. restore the database;
7. verify — readiness, then the fingerprint;
8. rebuild derivatives and the search index;
9. recover the tunnel;
10. only now let anybody in.

Evidence before the database, so that the moment rows exist their bytes already
do. The tunnel last, because an unverified restore must not be reachable.

### The steps

The checkout from step 1 is the working directory for everything below:

```bash
cd /mnt/user/appdata/juristid-main/repo
```

```bash
install -d -m 700 /mnt/user/appdata/juristid-main/config
```

Put the environment file back at
`/mnt/user/appdata/juristid-main/config/juristid.env`, `chmod 600`. It is not in
any backup set and never will be; see [Secrets](#secrets).

```bash
install -d -o 10001 -g 10001 /mnt/user/appdata/juristid-main/evidence /mnt/user/appdata/juristid-main/derivatives /mnt/user/appdata/juristid-main/legacy-source
```

uid 10001 is the application user, fixed in the image so that a rebuild cannot
change it. It owns these mounts and nothing else: the code under `/app` in the
image is root's and not writable by it. Do not answer a permissions problem with a blanket `chmod`: the point
of the ownership is that a process which should only read cannot write.

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml --env-file /mnt/user/appdata/juristid-main/config/juristid.env up -d db
```

`--env-file` on this one command, and only here (ADR 0179). The database no
longer reads the application's environment file: it is given `POSTGRES_DB`,
`POSTGRES_USER` and `POSTGRES_PASSWORD` by interpolation, and the password is
needed only while an empty cluster is initialised. `--env-file` lends the
file's values to that interpolation without passing the file into any
container. Leave it out and the image refuses to initialise («superuser
password is not specified») — run the line again with it. The next command
that starts `db` without `--env-file` recreates it once with the password
empty, which is the day-to-day shape and harmless: the cluster already holds
the password, and the data is on the bind mount.

**On a fresh cluster, create the AI-access roles first.** Roles belong to the
cluster, not the database, so a dump does not bring them back — but a dump
taken after AI access was set up grants to `juristid_mcp` (and, after Phase 2,
`juristid_mcp_writer`), and `pg_restore` in one transaction fails at the first
grant to a role that does not exist. The restore script lists the roles the
dump names and refuses, before it writes anything, when one is missing. Create
each one it names bare — no privileges; the dump's own grants restore exactly
what production had:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T db psql -U juristid -d postgres -c 'CREATE ROLE juristid_mcp LOGIN' -c 'CREATE ROLE juristid_mcp_writer LOGIN'
```

Their passwords are set after the restore, from the restored environment
files, by `mcp_db_role` / `mcp_writer_db_role --apply --password-stdin`
([`docs/mcp/OPERATIONS.md`](../../docs/mcp/OPERATIONS.md)), and only if AI access
is started again. A cluster that was kept (the database alone dropped and
recreated) still has them.

```bash
scripts/deploy/juristid-restore.sh --project juristid-main --compose-file deploy/unraid-main/compose.yml --set /mnt/user/backups/juristid-main/sets/20260822T190000Z --backup-root /mnt/user/backups/juristid-main --data-root /mnt/user/appdata/juristid-main
```

The script verifies the set before writing anything, **refuses a database that
already holds tables**, copies evidence with `--ignore-existing` so it can never
overwrite something newer than the backup, and restores the database in **one
transaction** (`pg_restore --single-transaction --exit-on-error`), so the first
error rolls the whole database back rather than leaving a schema and part of
the rows.

If `pg_restore` fails, the database is therefore as empty as it was before the
run and needs no dropping. The storage trees are not: the evidence and page XML
were copied first, and a rerun onto the same data root is refused because they
are no longer empty. The failure message names both trees. Find the cause in
`pg_restore`'s own error, move `evidence/` and `legacy-source/` under the data
root aside — do not delete them — and run the script again (ENG-115).

If it refuses because the database is not empty: that is the intended behaviour,
and there is no flag for it. Restoring over live data replaces every row written
since the dump and nothing brings those back. If replacing them really is the
intention, drop and recreate the database by hand, deliberately, and run the
script again.

#### What it copies

For a **manifest version 3** set, the objects that set's inventories name and
nothing else — `rsync --files-from`, driven by the set rather than by the
directory. Objects the pool holds and the set never named do not come back. A
set that named none restores none.

That exactness is only available onto empty storage, so a version 3 restore
**refuses when `evidence/` or `legacy-source/` under the data root already
contains files**, names the count, and changes nothing. It does not empty them
for you: `rsync --delete` would make any target exact by deleting whatever it
found, which is the one thing a recovery script must never do to a tree it did
not put there. Move or remove those files deliberately, then run it again.
`--database-only` is exempt — it touches no storage at all.

For a **version 1 or 2** set there is no membership to obey, so the filesystem
restore is the old pool-wide copy and the script says so on the line where it
does it. One case is refused outright: a pre-membership set whose manifest
records `empty_source_allowed_for`. That flag means the operator relaxed the
empty-tree guard, so the pool-wide copy for that tree is not merely unproven but
*known* to produce objects the set never described. Restore the database alone
with `--database-only`, or pass `--accept-legacy-pool-restore` to say you have
read this and want the pool-wide copy anyway.

**The clean-state set taken on 12.09.2026 is one of those.** It is a good
database snapshot and a valuable historical artifact and it should be kept — but
it is manifest version 2, so it cannot prove exact filesystem restore fidelity,
and restoring its evidence tree would produce the 20 068 pre-reset objects.
Once this change is deployed, **take a new backup and run level 2 on it**: that
set will be the first one whose clean state is provable. Nothing needs to be
deleted for that to happen.

#### What a restore refuses to copy

*Security finding #14 (`csf_39e48feffb03b493cbb024ca`), October 2026 — partial
mitigation only; read the last paragraph.*

A backup writes regular files in directories and nothing else, and the evidence
tree is the immutable store the application serves bytes from. So a symbolic
link, a FIFO, a socket or a device anywhere in a backup is something the backup
did not put there, and a restore must not carry it into the store: a link there
is a pointer the application would follow to whatever file on the host it
names. Before this, verification tested members with `[ -f ]`, which follows a
link, and the copy was `rsync -a`, which preserves one.

Now, none of that crosses:

* **verification** (level 1) refuses a set whose own files include a link or a
  special file, and a `SHA256SUMS` that names anything but plain file names
  inside the set or leaves out `database.dump` or `manifest.json`. Level 2
  walks both byte pools without following links and refuses a pool that is a
  link, any link (to a file or to a directory), FIFO, socket or device in it,
  a member that is a link, and a membership inventory `SHA256SUMS` does not
  cover. Inventory paths that are absolute or contain `..` were already
  refused;
* **the restore** asks the same of both pools, and refuses an `evidence/` or
  `legacy-source/` under the data root that already holds a link or a special
  file, **before it copies anything or starts any container**. It walks the
  pools again immediately before the copy, and copies with `--no-links
  --no-specials --no-devices`;
* **after the copy and before `pg_restore`**, it proves the written trees hold
  regular files only and, for a version 3 set, exactly the members the set
  names at the byte total it was sealed against. If that proof fails the
  database has not been touched; move `evidence/` and `legacy-source/` under
  the data root aside — do not delete them, they show what happened — and do
  not restore from that backup root until it has been investigated.

A refusal here is not a fault to work around. Do not delete the offending
object to make the restore pass: find out how it got into a directory only the
backup writes. To see what a pool holds, read-only:

```bash
find -P /mnt/user/backups/juristid-main/evidence /mnt/user/backups/juristid-main/legacy-source ! -type f ! -type d
```

It should print nothing. Run it once after deploying this change: one hit in a
shared pool makes **every** set beside it unverifiable until it is explained.
Hard links are not refused and need not be — a hard link is a regular file, and
what is copied is its bytes, which is the case below.

**What this does not fix.** All of the above is about the *kind* of object, not
its bytes. `SHA256SUMS` sits inside the set it describes and the pools carry no
digests at all, so somebody able to rewrite **both** a backup set (or its pool)
**and** its `SHA256SUMS` can still forge content that verifies at every level
and restores. Closing that needs a manifest signed with a key kept somewhere
the backup host cannot write — a future infrastructure decision that the owner
has explicitly deferred, together with off-host backup and key management.
Finding #14 is therefore **only partially mitigated**: links and special files
can no longer be restored, forged regular files still can. Until a trust anchor
outside the host exists, the only checks that can notice forged evidence bytes
are `check_evidence_integrity --verify-sha` (each object against the digest the
restored database records for it) and `recovery_fingerprint --compare` against
a fingerprint kept somewhere the attacker could not also rewrite — and neither
helps against a database dump forged to match.

### Then verify, before anything is published

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d --no-build web intake-reader searchindex intake-sweeper notifications
```

Named rather than unqualified, unlike a redeploy: the tunnel is not behind a
profile on this stack, and nothing is published until the checks below have
passed. `--no-build` for the same reason as a deployment: the image was loaded
in step 1, and an `up` that found it missing must say so rather than build one
here. `searchindex` belongs in the list — a restored database keeps whatever
rebuild obligations it was carrying when the dump was taken, and the worker is
what discharges them (ADR 0041).

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py deployment_readiness
```

That fails closed on an unapplied migration, a migration the code does not have,
a missing mount, a writable source corpus, a PostgreSQL major that is too old,
or a build that cannot say which commit it is.

```bash
FP=/mnt/user/backups/juristid-main/fingerprints/fp-before-<the-operation>.json
```

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web sh -c 'cat > /tmp/fp-before.json' < "$FP"
```

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py recovery_fingerprint --compare /tmp/fp-before.json
```

The file stays on the host; the container gets a copy on stdin, because it
does not mount the backup root ([Fingerprinting the live system](#fingerprinting-the-live-system)).
No `--skip-evidence-bytes` here: after a restore, re-hashing every object the
database names is the point. The exit status is the verdict.

The fingerprint taken **before the operation this restore is undoing** — not a
"latest" file, and not one taken beside the backup. See
[Fingerprinting the live system](#fingerprinting-the-live-system) for why a
fingerprint cannot describe a set.

If there is no such fingerprint — a disk failure gave nobody the chance to take
one — say so rather than reaching for the nearest file. The comparison is then
unavailable, `check_evidence_integrity` below still runs, and what the restore
has been proved to be is "structurally consistent", not "everything came back".

Any difference in canonical counts, evidence digests, page-XML digests or
migration leaves exits non-zero and names what moved. The migration leaves are
the last *applied* migration of each app, read from the restored database, so a
restore missing a migration is caught, and a restore onto a newer build that has
not migrated yet is not mistaken for one — `deployment_readiness` is what says
that build still has migrations to apply. Rebuildable counts are reported and
deliberately not compared: a restored database is *supposed* to have an empty
search projection. Operational counts (the search debt queue, the gate throttle,
unfinished Uus teema forms) are reported and not compared either.

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py check_evidence_integrity
```

That is the structural pass and it is cheap: a handful of queries plus an
existence and size check per version, plus a walk of the store looking for
objects nothing refers to. It is what catches a restore that brought the
database back and the evidence tree back *separately* — an object with no row,
a row with no object, a document whose current version now belongs to another
document. Do **not** add `--verify-sha` here: it reads every stored byte, which
on this corpus is a maintenance window rather than a verification step, and the
fingerprint comparison above has already hashed the objects it compares.

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py rebuild_document_derivatives --all
```

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py rebuild_search_index
```

Only then:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d tunnel
```

**AI access stays off through all of this** (docs/adr/0185, 0189). `mcp`,
`mcp-writer` and `mcp-tunnel` are behind the `mcp` profile, so nothing above
starts them, and they hold no state of their own: there is nothing of theirs in
a backup set and nothing to restore. Their three environment files
(`juristid-mcp.env`, `juristid-mcp-writer.env`, `juristid-mcp-tunnel.env`) live
beside `juristid.env` in the config directory and come back, or are recreated,
the same way (see [Secrets](#secrets)). Start them — if they were running
before — only after everything above has passed, by
[`docs/mcp/OPERATIONS.md`](../../docs/mcp/OPERATIONS.md) "Starting and
restarting only the MCP component"; `mcp-writer` last, and only if AI writes
were deliberately on before the loss. The grants and tokens are rows in the
restored database: a token issued after the backup was taken is simply unknown
now, and that colleague reconnects.

### PostgreSQL major versions

A dump taken from PostgreSQL 18 is restored by PostgreSQL 18. The Compose file
pins `postgres:18` by tag *and* digest (ADR 0179), and tests keep both pinned;
a restore therefore starts the exact server the dump came from (18.6 since
2026-10-22). Moving to a newer minor inside 18 is fine and expected — as a
deliberate edit of the digest, never as a side effect of a pull.

Restoring into an older major does not work and should not be attempted.
Restoring into a *newer* major is a separate exercise — read that release's
upgrade notes first, rehearse it on the disposable stack in
`deploy/recovery-rehearsal/`, and do not do it as part of recovering from an
outage.

## Secrets

Nothing secret enters a backup set. Not the environment file, not the database
password, not the shared gate password, not the tunnel credential, and not the
manifest — which carries counts and digests only, so that a set is safe to copy
somewhere else.

That means the secrets need their own answer, and the honest one is that they
have two different answers:

| Secret | If the host is lost |
| --- | --- |
| `POSTGRES_PASSWORD` | **must be restored**, or the restored database will not accept the application. Recoverable another way: change it in the database and in the env file together. |
| `DJANGO_SECRET_KEY` | may be regenerated. Everyone is signed out, and nothing in the database depends on it. In a disaster that is an acceptable cost. |
| `JURISTID_SHARED_GATE_PASSWORD` | may be regenerated, and the department told the new one. It is hashed at process start and stored nowhere. Regenerating it ends every open gate session at its next request (docs/adr/0183). |
| Cloudflare tunnel credential | may be reissued — see below. |
| `LOCAL_AUTH_MFA_ENCRYPTION_KEY` | **keep it; losing it costs every administrator their authenticator.** Required beside the gate on real data (`juristid.E048`, docs/adr/0186 §7): the administrators' TOTP secrets are sealed under it. A new key does not unenrol anybody — every device stays on record and no code from it is accepted again. Recovery codes still work (they are hashed, not sealed). The way back is one account at a time, on the host: `manage.py reset_second_factor --list-undecryptable`, then `reset_second_factor --email … --note …` for each administrator, who re-enrols at their next `/haldus/sisene/` ([`docs/LOCAL_AUTH_ACTIVATION_RUNBOOK.md`](../../docs/LOCAL_AUTH_ACTIVATION_RUNBOOK.md) §15). Never in a backup set, and not derived from `DJANGO_SECRET_KEY`. |
| `MCP_AUTHENTICATOR_KEY`, `MCP_TOKEN_DERIVATION_KEY` (in `juristid.env`, only where AI access is set up) | may be regenerated, at a price: a new `MCP_AUTHENTICATOR_KEY` makes every colleague's AI-access authenticator unreadable, and each must be re-enrolled; a new `MCP_TOKEN_DERIVATION_KEY` costs at most one reconnect ([`docs/mcp/OPERATIONS.md`](../../docs/mcp/OPERATIONS.md), step 3). |
| `juristid-mcp.env`, `juristid-mcp-writer.env`, `juristid-mcp-tunnel.env` | may be regenerated: the database roles' passwords (`juristid_mcp`, `juristid_mcp_writer`) are reset with `mcp_db_role` / `mcp_writer_db_role --apply --password-stdin`, `MCP_WRITE_GATEWAY_KEY` by `scripts/deploy/juristid-mcp-writer-keys.sh`, the OpenAI runtime key from OpenAI ([`docs/mcp/OPERATIONS.md`](../../docs/mcp/OPERATIONS.md)). |

So the secrets whose loss is genuinely awkward are the database password —
recoverable given superuser access to a restored cluster — and
`LOCAL_AUTH_MFA_ENCRYPTION_KEY`, whose loss is recoverable only by resetting and
re-enrolling every administrator's authenticator. Back that key up with the
same care as `POSTGRES_PASSWORD`, separately from the backup sets.

Keep the environment file wherever the Chamber keeps its other credentials. That
destination is a management decision, not a technical one, and it is recorded in
`docs/open-decisions.md` rather than invented here.

## Cloudflare tunnel recovery

The tunnel is *locally managed*: its credential was created on this host by
`cloudflared tunnel login` and lives in `…/juristid-main/cloudflared`. No token
exists in Git, in the Compose file, or anywhere a copy could be made by reading
the repository.

If the credential is lost with the host, create a new tunnel and point the
hostname at it:

```bash
cloudflared tunnel login
```

```bash
cloudflared tunnel create juristid-main-2
```

```bash
cloudflared tunnel route dns juristid-main-2 juristid.orgusaar.ee
```

Put the new JSON and a `config.yml` routing the hostname to `http://web:8000`
into `…/juristid-main/cloudflared`, then start the tunnel service.

**Do not open a host port while the tunnel is broken.** No tunnel means the
application is unreachable, which is inconvenient. A published port means the
application is reachable without going through the edge, which is a different
category of problem and the one thing this deployment is arranged to prevent.

### What container health can and cannot prove

The tunnel container being `running` proves the process started. It does not
prove the tunnel is connected, that the route resolves, or that anything is
being served. There is no lightweight local check that proves those without
adding a dependency on the internet — which CI must not have and a healthcheck
should not have.

So the honest verification is external and manual: open the hostname in a
private window and confirm the authenticator answers. That is step J of the
"Before calling it live" table in `README.md`, and it is the only check that
actually proves the public route works.

## Rolling back a deployment

Rollback is not one procedure, because the failures are not one failure.

### The code is wrong; the schema is unchanged

Redeploy the previous reviewed commit. The database and the evidence are
untouched. This is the ordinary case and it is cheap — see `README.md`.

### The code is wrong; migrations were applied and they were additive

Usually still just a code rollback: the previous release does not know about the
new columns, does not select them, and can still insert rows without them.
`manage.py migration_plan`, run from the target image before the migration, said
whether they were additive — which is when that question is answerable cheaply,
and why the deployment sequence asks it there rather than here.

"Additive" there means exactly that: the previous release can read **and
write** the new schema. A NOT NULL column with no database default is not
additive, because the previous release's INSERT does not name it and fails on
it — after this rollback as much as during the deployment (ENG-014).

### The migrations were not additive

Rolling the code back does not roll the schema back, and `migrate` backwards is
not a general answer — several migrations here install triggers and constraints
whose reverse drops a guarantee rather than a column.

The answer is the backup taken immediately before the migration, which is why
the deployment sequence takes one. Restoring it loses everything written since,
so the decision is: how much work has happened since the deployment, and is
losing it better than staying on the broken release. That is a judgement, not a
command.

### A backward migrate across `matters.0049` drops Muu links

`matters.0049_entry_url` is additive, so the ordinary answer to a bad release
that applied it is a **code-only** rollback: the previous release inserts
entries without naming `url`, the database default fills in `''`, and the links
stay where they are (docs/adr/0097, amendment of 2026-10-15).

**One known imprecision while the code is rolled back to `9ecbfd21`** (JDATA-001).
That release does not know `url`, so correcting a `Muu` entry that has a link
writes an `EntryRevision` whose `url` is the database default `''` — the
revision says the earlier link was empty — while the entry itself keeps its
link. Revisions are append-only, so the row stays as written after the next
release returns. Nothing is lost from the entry; the history of that one
correction is wrong. If it matters, note which corrections happened during the
rollback window (`matters_entryrevision` rows with `url = ''` on entries whose
`url <> ''`, created after the rollback) in the release record.

Running `migrate matters 0048` instead is a data loss. The reverse of `0049` is
two `RemoveField`s and a dropped constraint, so **every `Muu` link
(`Entry.url`) and every earlier link a correction kept (`EntryRevision.url`)
is dropped with its column.** The entries themselves, their wording, their
dates and their files survive. Migrating forward again brings the columns back
*empty*; the links do not return. `tests/test_entry_url_migration_reversal.py`
proves both halves over populated columns.

If the schema really has to go back across `0049`, first export `id, url`
from `matters_entry` and from `matters_entryrevision` where `url <> ''`, and
keep the export with the release's backup set — or restore the set taken
before the release, which is the answer this document gives for every
non-additive step back.

### The migration failed partway

PostgreSQL runs each migration in a transaction where it can, so a failure often
leaves the schema untouched. Often is not always, and "often" is doing more work
in that sentence than it can carry: a release is usually several migrations, and
`migrate` applies them one after another. An earlier one can be committed while
a later one fails. So the question is never "did the migration apply" but "how
far did the release get".

Ask, before assuming — and ask the **target image**, the one whose migrations
were being applied:

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml run --rm web python manage.py migration_plan
```

The two variables from the deployment sequence must still be exported in this
shell, or `run` resolves `juristid-main-web:local` and answers about some other
build entirely:

```bash
export JURISTID_GIT_SHA=<the-sha-that-was-being-deployed>
```

```bash
export JURISTID_IMAGE_TAG=${JURISTID_GIT_SHA:0:12}
```

**Not `exec -T web`.** At this point in a failed deployment the running `web`
container is still the *previous* image — `up -d` had not happened yet — and its
migration graph does not contain the release's migrations at all. It cannot call
them pending, because it has never heard of them, so the reassuring reading
("still pending, so nothing was applied") is one the old image is incapable of
disproving. Asked from the target image, both halves of the report mean
something: what is still pending was not applied, and what is neither pending
nor unknown was.

Read the whole list, not one line of it:

- **Every migration in the release is still pending.** Nothing was applied.
- **Some are pending and some are not.** The release is partly applied. The
  schema is somewhere between two releases and neither the old code nor the new
  code has ever been tested against it.
- **None are pending.** They all applied; the failure was later than it looked,
  or it was in something `migrate` did after the schema change.

The middle case is the one to expect and the one that is easy to miss by
reading only the migration that appeared in the error. It is a restore decision,
not a "run it again" decision — re-running `migrate` on a partly-applied release
is how a schema that is merely inconsistent becomes one nobody can describe.

### A data migration wrote wrong business data

Code rollback does nothing: the rows are written. This needs either the backup
or a reviewed corrective migration, and it is the case that most deserves an
hour of thought before a command.

### Real work happened after the bad deployment

Then restoring the database is not a rollback; it is a second data loss. Say so
out loud before doing it.

## Disaster recovery

### Several layers, and only one of them is the Juristid backup

Several different things on this host make copies of Juristid data. They are not
interchangeable, and reaching for the wrong one during an incident is the
mistake worth naming.

| | What it is | What it holds | When |
| --- | --- | --- | --- |
| **The Juristid backup set** | `juristid-backup.sh` — the thing this file is about | `database.dump` + the evidence and page-XML mirrors, with a manifest and checksums | whenever somebody runs it |
| **Unraid *Appdata Backup*** | a host plugin, unrelated to this repository | tar archives of the containers' appdata volumes, taken with the containers **stopped** | weekly |
| **The KOCORP nightly USB snapshot** — INFERRED, verify on host | a host job outside this repository (`/mnt/user/appdata/kocorp-backup/run_backup.sh`, per operations notes) | rsync snapshots of `backups/`, `appdata/` — so very likely `config/juristid.env` and `cloudflared/` in plaintext — and `juristid-main/source`, onto a USB disk in the same host | nightly, about 23:30 UTC, per operations notes |
| **The historical source corpus** | the read-only import material | the Excel register, the OneNote export, the migration audit | copied once, never since |

**The USB snapshot row is not verified by this repository.** On 13.10.2026 a
read-only look at the host found the disk's UUID present and a `last-backup-ok`
marker dated 12.10.2026 23:49 UTC. What it copies, how long it keeps it, and
whether a restore from it works were **not** checked. Re-verify on the host
before relying on any of it. Even as described it is not off-host: same host,
same room, unencrypted, writable and deletable by root, and checked by no
Juristid tool. It does not meet the contract in
[What an off-host copy has to satisfy](#what-an-off-host-copy-has-to-satisfy).

**Only the first is a set-consistent Juristid backup.** Appdata Backup stops the
containers and tars their volumes, so what it produces is crash-consistent in
the strong sense — nothing was running — but it is a host-level operational
safety net, not this application's backup. It carries no manifest, no
`SHA256SUMS`, no `pg_restore --list`, and nothing in this repository verifies or
restores it. Treat it as a second chance, never as the plan.

**Do not disable it.** It is one of at most two things copying some
configuration state off the appdata tree — the tunnel credential among it; the
other is the USB snapshot above, if that is what it appears to be — and until
the secrets have a deliberate backup of their own, switching it off would
quietly remove a recovery path nobody replaced. Its cost is real and should be
recorded rather than hidden: it stops `juristid-main-web` and `juristid-main-db`
for the length of the run. In the 31.08.2026 run that was **41 minutes** —
`juristid-main-db` stopped at 01:01:17Z and started again at 01:42:22Z — most of
it spent tarring the ~7 GB evidence tree of that time. The live tree has since
shrunk to a few MB, so the stop is probably much shorter now (INFERRED from a
container uptime on 13.10.2026); treat 41 minutes as a historical measurement
and re-measure before using it in a decision. The plugin's cron is `00 01 * * 1` and the host runs in UTC, so the
window is Monday 01:00–01:45 UTC, which is 04:00–04:45 in Tallinn. (The
plugin's own log stamps that as "04:00" — it renders local time while the host
clock is UTC, which is worth knowing before comparing its lines against
`docker inspect`.) Whether that trade is worth making weekly is an operations
decision, and it is an open one.

**The historical corpus has no backup at all.** It is mounted read-only, it is
not in a set, and its recovery path is a manual re-export from the Chamber's own
systems. That is a real path and a slow one.

### A backup on the same disk is not disaster recovery

The backups described above are a **local recovery copy**. They protect against
the failures people actually cause: a bad deployment, a mistaken command, a
migration that went wrong, a directory deleted by hand. Those are the common
ones, and this covers them well.

They do not protect against the disk, the array, the filesystem, the host, or
the building. A copy that shares a failure boundary with the original is not a
second copy of anything.

At the time of writing that is not a theoretical caveat. Measured on the host on
01.09.2026, production's appdata, the Juristid backup sets, the historical source
corpus **and** the weekly Appdata Backup destination all resolve to the same
single physical data disk. One disk, four things that were supposed to be
independent; on 13.10.2026 they still all resolved to `/mnt/disk4`. At most,
array parity protects against that disk failing — and **parity may no longer
exist**: operations notes record it being removed on 10.09.2026. That is
unverified here; check the array on the host before counting on it. If it is
gone, one disk failure takes the data and every on-array backup together.
Parity, where present, protects against nothing else — not a deletion, not
ransomware, not filesystem corruption, not the loss of the host or the room.

The disk layout is an observation with a date on it, not architecture: shares
move, disks get added, and the next reader should re-check rather than trust
this paragraph. What is *not* an observation is the rule — a copy is only a copy
if it can survive the thing that takes the original — and no amount of
rearranging disks inside one chassis satisfies it.

### What an off-host copy has to satisfy

Not implemented here, because choosing a destination is an operations decision
with cost and custody implications, and inventing one in a repository is how a
project acquires a backup provider nobody agreed to. The contract it has to
meet, whatever it turns out to be:

1. **A different failure boundary.** Different physical hardware at minimum;
   somewhere else in the building is better; somewhere else entirely is the
   point.
2. **The whole set.** `database.dump`, both mirrors, the manifest and the
   checksums. A database dump alone restores a register whose evidence is gone.
3. **Verified after arrival, not before departure.** `SHA256SUMS` verifies at
   the destination, on the copy, with `juristid-verify-backup.sh --level 1`.
   Verification at the source proves the source.
4. **Written by something that cannot delete.** A push credential that can also
   remove objects turns one compromised host into zero backups.
5. **Restorable by somebody who has lost the host.** Which means the procedure,
   the credentials and the knowledge of where it is do not live only on the
   machine that just died.
6. **Rehearsed.** Not the whole thing, but at least: fetch a set from the
   destination and run level 1 and 2 against it.

Until that exists, the honest statement of this system's recovery position is:
protected against operator error and bad deployments, **not** protected against
loss of the host.

### The historical corpus

`/mnt/user/juristid-main/source` — the Excel register, the OneNote desktop
archive and the migration audit — is mounted read-only and is not in the backup
set. It is roughly 4 GiB of input material that was imported once.

Its recovery path is not a backup but a re-export: the workbook and the OneNote
notebook still exist in the Chamber's own systems, and the archive was produced
from them. That is a real path and it is a slow one — a manual export, hours of
somebody's day, and a result that will not be byte-identical to the original.

Two things follow. The imported material itself is already safe: it lives in
evidence and in `legacy-source`, both of which are backed up, and the register
is in PostgreSQL. What re-exporting would be needed for is running the import
*again* — which is a rare thing to want and a good reason to include the corpus
in whatever off-host copy gets arranged, rather than a reason to treat its loss
as an emergency.

**Open question:** whether `/mnt/user/juristid-main/source` is currently the only
copy of that particular export. If it is, that is worth fixing with a single
manual copy to a different disk, once — recorded in `docs/open-decisions.md`.

### What is not yet fixed

The 03.09.2026 rehearsal proved a real set restores. It also measured four
things that are still true afterwards. They are recorded here, deliberately
unimplemented: each one is an operations decision or a host change, and a
repository that quietly implements those acquires policy nobody agreed to.

**DR0 — canonical data, the backups and the source corpus are all on one
physical disk.** Not merely one host: measured on 01.09.2026 and again on
03.09.2026, production's appdata, the backup sets, the historical corpus and the
weekly Appdata Backup destination all resolve to the same data disk. Array
parity, if the array still has it (re-verify: see above), protects against that
disk failing and against nothing else. This is the one that outranks everything
below, and the contract a fix has to meet is in
[What an off-host copy has to satisfy](#what-an-off-host-copy-has-to-satisfy).
The prepared procedure is [`OFFHOST-BACKUP.md`](OFFHOST-BACKUP.md); it waits on
an owner-approved destination.

**DR1-A — a backup is taken before a deploy and never after, so there is no
recovery point for the revision that is running.** The newest set always
describes the previous release. On 03.09.2026 the deployed revision had *no*
set of its own, and the gap was survivable only because the release in between
carried no migration. Had it carried one, restoring the newest set would have
produced an application ahead of its own schema, which `deployment_readiness`
refuses — correctly, and at the worst possible moment. The fix is a backup after
a successful deploy as well as before it, or a schedule; both are the scheduling
decision this file already refuses to invent.

**DR1-B — every operator SSH key must live in the Unraid GUI key store.**
Registering a key through the WebGUI writes `/boot/config/ssh/root.pubkeys` and
regenerates `/root/.ssh/authorized_keys` **from it, by replacement**. Any key
that was appended to `authorized_keys` by hand is erased the next time anybody
touches that screen. It happened during the 03.09.2026 rehearsal and removed
two working keys. It presents as `Permission denied (publickey)` on a host where
every service is healthy, which reads like a flash-drive fault and is not one;
repeated retries then trip OpenSSH's `srclimit_penalise` and make it look worse.
Nothing in this repository can fix it — it is a host setting — but a recovery
runbook whose access channel can be revoked silently by an unrelated action is
a runbook with a hole in it, so: keep operator keys in the GUI store, and check
`stat -c %y /root/.ssh/authorized_keys` against `ls /boot/config/ssh/` before
concluding the flash drive has failed.

**DR1-C — where the secrets live is neither documented nor proved.** The backup
manifest excludes them on the grounds that "the environment file and the tunnel
credential are backed up separately, never here". On 03.09.2026 a search of
`/mnt/user/backups` found no copy of either. They may well exist somewhere
deliberate; the point is that the runbook does not say where, so nobody can
check, and a restore that recovers the database, the evidence and the page XML
still cannot bring the deployment back without them. See [Secrets](#secrets).
Naming the destination is a custody decision, which is why this file records the
gap instead of choosing one. Still open on 13.10.2026: the only copies anybody
can point to are probably the Appdata Backup archives and the plaintext USB
snapshot (both INFERRED). [`OFFHOST-BACKUP.md`](OFFHOST-BACKUP.md) proposes
escrowing the values in the Chamber's password manager, next to the off-host
repository keys, and keeps them out of every backup set.

## Scenarios

| | What happened | What it costs |
| --- | --- | --- |
| A | A bad application deploy. Database and evidence are fine. | Redeploy the previous reviewed commit. Minutes. |
| B | Database corruption or loss. | Restore the newest set. Everything since that dump is gone. |
| C | The evidence directory is lost. | Restore the evidence mirror, then `recovery_fingerprint` to prove every row's bytes came back and `check_evidence_integrity` to prove nothing came back that no row refers to. |
| D | The whole disk or host is lost. | Needs an off-host copy. **There is not one yet.** The procedure is prepared in [`OFFHOST-BACKUP.md`](OFFHOST-BACKUP.md) and waits on an owner-approved destination. Neither the local sets nor the CI rehearsal is an off-host copy. The owner decided on 13.10.2026 that off-host backup is not required for the current release cycle and accepted this residual risk; the procedure stays inactive. |
| E | The Cloudflare credential is lost. | Reissue the tunnel and move the DNS route. No host port, ever, as a workaround. |
| F | Derivatives are lost. | `rebuild_document_derivatives --all`. Slow, complete, no decision needed. |
| G | The search projection is corrupt. | `rebuild_search_index`. It is atomic, so the previous index serves until the new one is ready. |

A through C and E through G are recoverable today. **D is not**, and that is the
single most important sentence in this file.

## Not decided yet

These are operations decisions, recorded rather than invented
(`docs/open-decisions.md`):

* how often a backup is taken, and by what — nothing is scheduled today, and
  adding a cron entry on this host means editing a file generated from the flash
  drive, which is its own hazard;
* how long sets are kept, and what deletes them;
* the **RPO** — how much work the Chamber is willing to lose. It is the number
  `juristid-check-backup-age.sh --max-age-hours` takes, and the script refuses
  to invent one;
* the **RTO** — how long it may take to come back;
* the off-host destination and who holds its credentials;
* **how often a real set is rehearsed.** One has been restored
  ([Rehearsing against a real set](#rehearsing-against-a-real-set)), which is
  what closed the question of whether the procedure works on the real thing.
  Nothing schedules the next one, and a rehearsal done once is a fact about
  03.09.2026 rather than a property of the system;
* **a set-consistent recovery fingerprint** in the general case. The 03.09.2026
  rehearsal got one only because nothing had been written between the dump and
  the comparison, which is luck rather than design. See
  [Fingerprinting the live system](#fingerprinting-the-live-system) for why the
  current backup transaction model cannot produce one on demand;
* **a recovery point for the revision that is actually deployed.** Backups are
  taken before a deploy and nothing takes one after, so the newest set always
  describes the previous release. Restoring it into the running image is safe
  only while the release in between carried no migration — see
  [What is not yet fixed](#what-is-not-yet-fixed);
* whether the weekly Appdata Backup's stop (41 minutes when measured on
  31.08.2026; re-measure) is a price worth paying
  weekly, and what would have to exist first — a deliberate backup of the
  configuration and tunnel credential — before it could be narrowed or switched
  off.

Nothing in this repository encodes an answer to any of them, on purpose. A
retention period invented by an engineer and written into a script becomes
policy the day somebody reads it as one.
