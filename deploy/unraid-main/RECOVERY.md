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

## Backing up

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
  evidence/                     append-only mirror
  legacy-source/                append-only mirror
  sets/20260822T190000Z/
    database.dump               PostgreSQL custom format
    manifest.json               what this set is, in non-secret metadata
    SHA256SUMS
```

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
it should".

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

That argument holds only because evidence is append-only. It is: existing
evidence is immutable through a database trigger, and removal goes through
legal-hold rules rather than through the filesystem.

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
| 2 | the archive is a PostgreSQL dump whose contents list the right tables, **and** both mirrors still hold what the manifest recorded | seconds |
| 3 | the set restores and the application reads the register back out of it | minutes |

Levels 1 and 2:

```bash
scripts/deploy/juristid-verify-backup.sh --project juristid-main --compose-file deploy/unraid-main/compose.yml --set /mnt/user/backups/juristid-main/sets/20260822T190000Z
```

The backup script runs both before it renames a set into place, so a set that
exists has already passed them.

### The mirror check, and what it is not

A set is `database.dump` plus the two mirrors it names. The manifest has always
recorded how many files each mirror held and how many bytes they came to;
nothing read those numbers back, so a set could pass every check it had while
the evidence it depends on had been emptied — and the way that gets discovered
is by needing it.

Level 2 now recomputes both and compares:

* **fewer files than recorded** is a failure. Objects are gone.
* **fewer bytes with the right file count** is a failure. Something was
  truncated in place.
* **more of either** is reported and is not a failure. The mirrors are shared
  between sets rather than copied per set, and evidence is append-only, so an
  older set verified today is *supposed* to find more than it recorded.

It runs before the compose file is even required, because there is no reason to
make somebody start a container to be told the evidence is missing. Pass
`--backup-root` if a set has been moved away from its mirrors, and
`--no-mirror-check` to verify a set as a file rather than as a backup — which is
what a set copied without its mirrors is.

**It hashes nothing.** The evidence tree is ~7.4 GB and a routine check that
reads all of it is a check somebody switches off. Proving the bytes is
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
recorded before. It runs on every pull request.

That proves the procedure. It does not prove any particular production set, and
nothing except restoring one would.

### Fingerprinting the live system

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py recovery_fingerprint --skip-evidence-bytes --out /mnt/user/backups/juristid-main/fingerprints/fp-before-<what-you-are-about-to-do>.json
```

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
CI rehearsal uses it and how the two files in
`/mnt/user/backups/juristid-main/fingerprints/` were used for the release they
are named after. Name the file after the operation, not after "latest": a file
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

```bash
docker compose -p juristid-recovery-rehearsal -f deploy/recovery-rehearsal/compose.real-data.yml up -d --wait db
scripts/deploy/juristid-verify-backup.sh --project juristid-recovery-rehearsal --compose-file deploy/recovery-rehearsal/compose.real-data.yml --set /mnt/user/backups/juristid-main/sets/<stamp> --backup-root /mnt/user/backups/juristid-main --level 2
scripts/deploy/juristid-restore.sh --project juristid-recovery-rehearsal --compose-file deploy/recovery-rehearsal/compose.real-data.yml --set /mnt/user/backups/juristid-main/sets/<stamp> --backup-root /mnt/user/backups/juristid-main --data-root <scratch root>
docker compose -p juristid-recovery-rehearsal -f deploy/recovery-rehearsal/compose.real-data.yml run --rm -T web python manage.py deployment_readiness
docker compose -p juristid-recovery-rehearsal -f deploy/recovery-rehearsal/compose.real-data.yml run --rm -T web python manage.py recovery_fingerprint --out /app/derivatives/fp-restored.json
docker compose -p juristid-recovery-rehearsal -f deploy/recovery-rehearsal/compose.real-data.yml run --rm -T web python manage.py check_evidence_integrity
```

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
change it. Do not answer a permissions problem with a blanket `chmod`: the point
of the ownership is that a process which should only read cannot write.

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d db
```

```bash
scripts/deploy/juristid-restore.sh --project juristid-main --compose-file deploy/unraid-main/compose.yml --set /mnt/user/backups/juristid-main/sets/20260822T190000Z --backup-root /mnt/user/backups/juristid-main --data-root /mnt/user/appdata/juristid-main
```

The script verifies the set before writing anything, **refuses a database that
already holds tables**, copies evidence with `--ignore-existing` so it can never
overwrite something newer than the backup, and stops at the first `pg_restore`
error rather than reporting a count nobody reads.

If it refuses because the database is not empty: that is the intended behaviour,
and there is no flag for it. Restoring over live data replaces every row written
since the dump and nothing brings those back. If replacing them really is the
intention, drop and recreate the database by hand, deliberately, and run the
script again.

### Then verify, before anything is published

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d --no-build web extractor searchindex
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
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py recovery_fingerprint --compare /mnt/user/backups/juristid-main/fingerprints/fp-before-<the-operation>.json
```

The fingerprint taken **before the operation this restore is undoing** — not a
"latest" file, and not one taken beside the backup. See
[Fingerprinting the live system](#fingerprinting-the-live-system) for why a
fingerprint cannot describe a set.

If there is no such fingerprint — a disk failure gave nobody the chance to take
one — say so rather than reaching for the nearest file. The comparison is then
unavailable, `check_evidence_integrity` below still runs, and what the restore
has been proved to be is "structurally consistent", not "everything came back".

Any difference in canonical counts, evidence digests, page-XML digests or
migration leaves exits non-zero and names what moved. Rebuildable counts are
reported and deliberately not compared: a restored database is *supposed* to
have an empty search projection.

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

### PostgreSQL major versions

A dump taken from PostgreSQL 18 is restored by PostgreSQL 18. The Compose file
pins `postgres:18` and a test keeps it pinned; minor versions inside 18 are
fine and expected.

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
| `JURISTID_SHARED_GATE_PASSWORD` | may be regenerated, and the department told the new one. It is hashed at process start and stored nowhere. |
| Cloudflare tunnel credential | may be reissued — see below. |

So the only secret whose loss is genuinely awkward is the database password, and
even that is recoverable given superuser access to a restored cluster.

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
new columns and does not select them. `manage.py migration_plan`, run from the
target image before the migration, said whether they were additive — which is
when that question is answerable cheaply, and why the deployment sequence asks
it there rather than here.

### The migrations were not additive

Rolling the code back does not roll the schema back, and `migrate` backwards is
not a general answer — several migrations here install triggers and constraints
whose reverse drops a guarantee rather than a column.

The answer is the backup taken immediately before the migration, which is why
the deployment sequence takes one. Restoring it loses everything written since,
so the decision is: how much work has happened since the deployment, and is
losing it better than staying on the broken release. That is a judgement, not a
command.

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

### Three layers, and only one of them is the Juristid backup

Three different things on this host make copies of Juristid data. They are not
interchangeable, and reaching for the wrong one during an incident is the
mistake worth naming.

| | What it is | What it holds | When |
| --- | --- | --- | --- |
| **The Juristid backup set** | `juristid-backup.sh` — the thing this file is about | `database.dump` + the evidence and page-XML mirrors, with a manifest and checksums | whenever somebody runs it |
| **Unraid *Appdata Backup*** | a host plugin, unrelated to this repository | tar archives of the containers' appdata volumes, taken with the containers **stopped** | weekly |
| **The historical source corpus** | the read-only import material | the Excel register, the OneNote export, the migration audit | copied once, never since |

**Only the first is a set-consistent Juristid backup.** Appdata Backup stops the
containers and tars their volumes, so what it produces is crash-consistent in
the strong sense — nothing was running — but it is a host-level operational
safety net, not this application's backup. It carries no manifest, no
`SHA256SUMS`, no `pg_restore --list`, and nothing in this repository verifies or
restores it. Treat it as a second chance, never as the plan.

**Do not disable it.** It is currently the only thing copying some
configuration state off the appdata tree — the tunnel credential among it — and
until the secrets have a deliberate backup of their own, switching it off would
quietly remove a recovery path nobody replaced. Its cost is real and should be
recorded rather than hidden: it stops `juristid-main-web` and `juristid-main-db`
for the length of the run. In the 31.08.2026 run that was **41 minutes** —
`juristid-main-db` stopped at 01:01:17Z and started again at 01:42:22Z — most of
it spent tarring the ~7 GB evidence tree that the Juristid backup already
mirrors. The plugin's cron is `00 01 * * 1` and the host runs in UTC, so the
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
independent. Array parity protects against that disk failing; it protects
against nothing else — not a deletion, not ransomware, not filesystem
corruption, not the loss of the host or the room.

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
parity protects against that disk failing and against nothing else. This is the
one that outranks everything below, and the contract a fix has to meet is in
[What an off-host copy has to satisfy](#what-an-off-host-copy-has-to-satisfy).

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
gap instead of choosing one.

## Scenarios

| | What happened | What it costs |
| --- | --- | --- |
| A | A bad application deploy. Database and evidence are fine. | Redeploy the previous reviewed commit. Minutes. |
| B | Database corruption or loss. | Restore the newest set. Everything since that dump is gone. |
| C | The evidence directory is lost. | Restore the evidence mirror, then `recovery_fingerprint` to prove every row's bytes came back and `check_evidence_integrity` to prove nothing came back that no row refers to. |
| D | The whole disk or host is lost. | Needs an off-host copy. **There is not one yet.** |
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
* whether the weekly Appdata Backup's ~41-minute stop is a price worth paying
  weekly, and what would have to exist first — a deliberate backup of the
  configuration and tunnel credential — before it could be narrowed or switched
  off.

Nothing in this repository encodes an answer to any of them, on purpose. A
retention period invented by an engineer and written into a script becomes
policy the day somebody reads it as one.
