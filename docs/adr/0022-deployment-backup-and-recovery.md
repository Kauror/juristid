# 0021 — Deployment, backup and recovery on the Unraid host

- **Status:** Accepted
- **Date:** 2026-08-22
- **Supersedes in practice:** the deployment half of ADR 0008, which was written
  before a real deployment existed

## Context

ADR 0008 kept the hosting choice open and named Azure Container Apps as the
leaning. That decision is still open on paper. In fact the system has been
running on an Unraid host for weeks, holding the Chamber's twenty-year register,
and everything that decides whether that data survives — how it is deployed, how
it is backed up, what a restore does — was living in a README rather than in a
decision.

An audit of that arrangement found the gaps that matter are not exotic:

* the documented backup was `pg_dump | gzip > file`, whose exit status is
  gzip's, so a dump that died halfway produced a file that passed `gzip -t`;
* nothing said how to restore anything, on any host;
* evidence and the OneNote page XML were marked "back this up" with no
  procedure and no statement of how they stay consistent with a database dump
  taken separately;
* the deployment step was `git pull`, which deploys whatever `main` has become
  rather than what was reviewed;
* the running build's commit came from an environment variable a human had to
  remember to update;
* the tunnel — the only route in — ran `cloudflare/cloudflared:latest`;
* the existing backups sit on the same physical disk as the data they protect,
  and were being described as disaster recovery.

None of these is a subtle failure. All of them are the kind that is discovered
on the day they matter.

## Decision

### Deployment carries code and schema, never business data

A deployment applies migrations. It does not import the register, promote the
current register, apply an opinion archive, backfill owners or run a cutover.
Those write the Chamber's record, each has its own review gate, and none may
happen because a container restarted. Migrations stay a deliberate step and
never run at container start; a test asserts no service's start-up command can
reach either.

### A deployment targets a commit, not a branch

The deployment takes a full 40-character SHA and refuses an abbreviation: two
commits can share a prefix and the resolution is silent, and a branch name is
whatever that branch has become since somebody read it.

`scripts/deploy/juristid-deploy-preflight.sh` verifies the commit exists, that
it is ahead of what is running, that the checkout is clean, that the Compose
file resolves with no host port and a read-only corpus, that the environment
file exists at mode 600, and that there is room for a backup. It changes
nothing and prints the commands.

**An unexpectedly dirty checkout stops the deployment**, and the response is
never `git reset --hard` or `git clean`. Local changes on a production server
are evidence of something, and deleting them destroys the only record that it
happened.

### The image knows which commit it is

`GIT_SHA` is a build argument written into the image, and `APPLICATION_REVISION`
falls back to it. Three separate facts, kept separate: the **source revision**
answers what code this is, the **build stamp** answers when the image was made,
and the **image tag** is a name somebody chose. Only the first is a version, and
it is no longer something a person has to remember.

`manage.py deployment_readiness` refuses to call a real-data deployment ready
when the build cannot say what commit it came from.

### The migration plan is read before it is applied

`manage.py migration_plan` lists pending migrations and flags the operations
that are not purely additive — `RemoveField`, `DeleteModel`, `RenameField`,
`RenameModel`, `RunPython`, `RunSQL` — using Django's own migration executor
rather than grepping files.

This matters because of the deployment shape: the previous release keeps serving
while migrations run, so there is a window of **old application, new schema**.
That window is safe when migrations are additive and unsafe when they are not.
Rather than assume additivity, the plan says which it is, before it is applied.

**When a migration is not additive, the deployment takes a maintenance window.**
Six users and an announced ten minutes is better than a silent compatibility
gamble, and this system has no architecture that would make zero downtime true.

### Three health questions, kept apart

| | Proves | Where |
| --- | --- | --- |
| Liveness | the process is up and its database answers | `/healthz`, the image HEALTHCHECK |
| Readiness | schema applied, mounts correct, configuration coherent | `manage.py deployment_readiness` |
| Data integrity | the canonical state is what it should be | `manage.py recovery_fingerprint` |

Readiness is a command, not an endpoint, for two reasons: it loads the migration
graph and probes every mount, which has no business on a request path; and it
reports mount contracts, auth mode and PostgreSQL major, which a publicly
reachable endpoint has no reason to publish.

The readiness check probes the mounts by writing a file rather than by reading
permission bits, because `:ro` is a mount option the kernel enforces and
`os.access` knows nothing about it. That is how it can tell that the historical
corpus is genuinely read-only rather than merely intended to be.

### Backups produce a set, and a set is verifiable

`scripts/deploy/juristid-backup.sh` produces a database dump in PostgreSQL
**custom format**, a manifest of non-secret metadata, SHA-256 checksums, and two
append-only mirrors — evidence, and the OneNote page XML.

Custom format rather than `.sql.gz` for one reason above all: it removes the
pipeline. `pg_dump` compresses on its own, so there is no second process whose
success can stand in for the first one failing. It also makes
`pg_restore --list` possible, which is the difference between "this file
decompresses" and "this file contains the tables it should". Existing `.sql.gz`
backups remain restorable and are not invalidated.

The set is built under a `.partial` name and renamed only after it has been
written and checked, so a crash leaves something obviously incomplete rather
than something plausible.

**Nothing deletes.** No retention rule has been agreed, and creating backups
safely is the more urgent half.

### Database and evidence are made consistent by ordering

`pg_dump` is transactionally consistent with itself and with nothing else. This
system writes evidence bytes before the `DocumentVersion` row that describes them
commits, so a filesystem copy taken before the dump can lack an object whose row
the dump contains.

The mirrors are therefore synchronised **twice**, once before the dump and once
after. Any object whose row is in the dump had its bytes written before the dump
began, so one of the two passes holds it. The reverse — an object with no row —
is an orphan, which is harmless and already has a command.

This argument depends on evidence being append-only, which it is: existing
evidence is immutable through a database trigger, and removal goes through
legal-hold rules rather than the filesystem (ADR 0003, ADR 0014).

### Verification has three levels, and only the third means much

1. checksums and non-emptiness;
2. `pg_restore --list` finds the tables that should be there;
3. the set restores into a disposable database and the application reads the
   register back out of it.

Level 3 is the `recovery` job in CI, on synthetic data: seed, back up with the
production script, destroy the database and the storage trees, restore with the
production restore script, and require the canonical fingerprint — row counts,
evidence byte digests, page-XML digests, migration leaves — to match. Failure
injection runs beside it: a missing evidence mount, an absent destination, a
corrupted archive and a truncated archive must each fail loudly and leave
nothing that looks like a backup.

That proves the *procedure*. Nothing except restoring a particular production
set proves that set, and this repository never touches production.

### A restore refuses to destroy

`scripts/deploy/juristid-restore.sh` verifies the set before writing anything,
refuses a database that already holds tables, restores evidence with
`--ignore-existing` so it can never overwrite an object newer than the backup,
and stops at the first `pg_restore` error.

The refusal has no override flag. Restoring over live data replaces every row
written since the dump; if that is genuinely the intention, dropping the
database is a thing to do deliberately, at a prompt, with the runbook open.

### Storage classes decide the backup plan

Canonical (PostgreSQL, evidence, page XML) must be backed up. Rebuildable
(derivatives, the search projection) must not be mistaken for it and is rebuilt
after a restore. Source (the historical corpus) is authoritative read-only input
with its own recovery path. Secrets are never in a set at all.

`recovery_fingerprint` encodes this: canonical counts are compared, rebuildable
counts are reported and never compared, because a correct restore comes back
with an empty search projection.

### Nothing secret is ever in a backup

Not the environment file, not the database password, not the shared gate
password, not the tunnel credential, and not the manifest — which carries counts
and digests only, so a set is safe to copy elsewhere. `RECOVERY.md` states which
secrets must be restored and which may simply be regenerated; only the database
password is genuinely awkward, and even that is recoverable.

### The tunnel is pinned and waits for a working origin

`cloudflare/cloudflared` is pinned to a version tag with a written update
procedure, not `latest`: an unrelated `docker pull` must not be able to replace
the ingress of a real-data system. The tunnel now depends on `web` being
**healthy**, not merely started, so a deployment does not put 502s on the public
hostname while gunicorn boots.

Container health proves the tunnel process started and nothing more. The honest
verification that the public route works is external and manual, and
`RECOVERY.md` says so rather than adding a healthcheck that always passes.

### A local recovery copy is not disaster recovery

The backups described here protect against a bad deployment, a mistaken command,
a failed migration and a directory deleted by hand. They do not protect against
the disk, the host, the filesystem or the building, because they share it.

This ADR names the contract an off-host copy has to satisfy — different failure
boundary, the whole set, verified after arrival, written by a credential that
cannot delete, restorable by somebody who has lost the host, and rehearsed — and
does **not** choose a destination. That is an operations decision with cost and
custody implications, recorded in `docs/open-decisions.md`.

Until it exists, the honest statement is: protected against operator error,
**not** protected against loss of the host.

## Alternatives considered

**Migrations at container start.** Rejected, and it was already rejected. It
turns every restart into a schema change, including restarts nobody is watching.

**Restore automation that drops the database first.** Rejected. It would make
the dangerous case — restoring over data that was still good — the easy one.

**A scheduled backup on this host.** Not adopted here. Adding a cron entry means
editing a file generated from the Unraid flash drive, and getting that wrong
removes the host's own mover and parity jobs. The schedule is a decision to
design deliberately, not a line to add in passing.

**A cloud backup provider.** Not adopted. Choosing one is a management decision
about cost and custody, and inventing one in a repository is how a project
acquires a vendor nobody agreed to.

**Kubernetes, Terraform, Ansible, an external secrets manager.** Not adopted.
One small internal application on an existing host; explicit recoverability is
the objective and none of these advances it.

## Consequences

- A deployment takes longer to start and cannot be done from memory. That is the
  intent: the steps it adds are the backup and the two things that make a
  rollback possible.
- The repository can now make a stronger claim than "the commands look right":
  a synthetic set is created, destroyed and restored on every pull request, with
  evidence digests checked.
- The unresolved risk is named rather than papered over. Loss of the host is
  currently unrecoverable, and it is written where an operator will read it.

## Reversibility

High. The scripts are shell and the commands are read-only; the Compose changes
are a pinned tag, a build argument and a health condition. If the deployment
target ever moves to a managed platform, the storage-class distinctions and the
verification levels are the parts worth carrying over — the mechanics are not.
