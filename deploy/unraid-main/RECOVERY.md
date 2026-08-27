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
| Prove the procedure | the `recovery` job in CI, on synthetic data |
| Restore | `scripts/deploy/juristid-restore.sh`, then verify, then publish |
| Off-host copy | **not yet arranged** — see [Disaster recovery](#disaster-recovery) |

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
| Secrets | `…/juristid-main/config/juristid.env` | secret — backed up separately, never in a set |
| Tunnel credential | `…/juristid-main/cloudflared` | secret — backed up separately, or reissued |

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
| 2 | the archive is a PostgreSQL dump whose contents list the right tables | seconds |
| 3 | the set restores and the application reads the register back out of it | minutes |

Levels 1 and 2:

```bash
scripts/deploy/juristid-verify-backup.sh --project juristid-main --compose-file deploy/unraid-main/compose.yml --set /mnt/user/backups/juristid-main/sets/20260822T190000Z
```

The backup script runs both before it renames a set into place, so a set that
exists has already passed them.

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
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py recovery_fingerprint --skip-evidence-bytes --out /mnt/user/backups/juristid-main/fingerprint-latest.json
```

Non-secret: counts, digests and schema state, no content and no filenames. Keep
one beside the backups. After a restore it is what turns "the site loads" into
"every canonical row and every evidence object came back", and it is the input
to `--compare`.

Drop `--skip-evidence-bytes` to re-hash every stored object. That reads the
whole evidence tree, so it is a deliberate exercise rather than a routine one.

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

1. install the reviewed application version — a checkout at a known commit;
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
docker compose -p juristid-main -f deploy/unraid-main/compose.yml up -d web extractor searchindex
```

Named rather than unqualified, unlike a redeploy: the tunnel is not behind a
profile on this stack, and nothing is published until the checks below have
passed. `searchindex` belongs in the list — a restored database keeps whatever
rebuild obligations it was carrying when the dump was taken, and the worker is
what discharges them (ADR 0041).

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py deployment_readiness
```

That fails closed on an unapplied migration, a migration the code does not have,
a missing mount, a writable source corpus, a PostgreSQL major that is too old,
or a build that cannot say which commit it is.

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web python manage.py recovery_fingerprint --compare /mnt/user/backups/juristid-main/fingerprint-latest.json
```

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

### A backup on the same disk is not disaster recovery

The backups described above are a **local recovery copy**. They protect against
the failures people actually cause: a bad deployment, a mistaken command, a
migration that went wrong, a directory deleted by hand. Those are the common
ones, and this covers them well.

They do not protect against the disk, the array, the filesystem, the host, or
the building. A copy that shares a failure boundary with the original is not a
second copy of anything.

At the time of writing, at least one evidence backup has been verified
byte-for-byte and sits on the same physical disk as the production evidence.
That verification is real and worth having. It is not disaster recovery, and
calling it that is how a system ends up believing it has a recovery plan.

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
* the **RPO** — how much work the Chamber is willing to lose;
* the **RTO** — how long it may take to come back;
* the off-host destination and who holds its credentials.

Nothing in this repository encodes an answer to any of them, on purpose. A
retention period invented by an engineer and written into a script becomes
policy the day somebody reads it as one.
