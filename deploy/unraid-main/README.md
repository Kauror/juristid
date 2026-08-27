# Juristid — the real-data instance on the Unraid host

The Chamber's twenty-year legislative register and the OneNote material attached
to it, in one system. This is the environment that holds the real thing.

| | |
| --- | --- |
| LAN URL | **none — there is no host port** |
| Auth mode | `shared_gate` (temporary; see below) |
| Public URL | `https://juristid.orgusaar.ee` — behind the shared gate |
| Compose project | `juristid-main` |
| Containers | `juristid-main-web`, `juristid-main-db`, `juristid-main-extractor`, `juristid-main-searchindex`, `juristid-main-tunnel` |
| Network | `juristid-main-internal` (its own bridge) |
| Appdata | `/mnt/user/appdata/juristid-main/` |
| Evidence | `…/evidence` — **back this up** |
| Derivatives | `…/derivatives` — rebuildable, needs no backup |
| Page XML | `…/legacy-source` — **back this up**; source evidence |
| Source corpus | `/mnt/user/juristid-main/source/` — **read-only**, mounted `:ro` |
| Secrets | `/mnt/user/appdata/juristid-main/config/juristid.env`, mode 600, never in Git |
| Backup and recovery | [`RECOVERY.md`](RECOVERY.md) — how to back up, verify, restore and roll back |

The synthetic rehearsal at `juristid-test` keeps running, on its own project,
network, database and appdata tree. Nothing here touches it, and it must not be
stopped or removed as part of this deployment.

## Why this environment is allowed to hold real data

Four properties, each enforced somewhere a mistake would be visible rather than
silent.

**There is no host port.** No service in `compose.yml` has a `ports:` key. The
only route in is the Cloudflare tunnel. You cannot reach this by typing the
server's LAN address, and `tests/test_deployment_unraid_main.py` fails if a port
ever appears.

**There is an authenticator in front of it.** `AUTH_MODE` is `shared_gate`
today and `cloudflare_access` when the Access application exists. Real data with
`AUTH_MODE=none` refuses to start (`juristid.E006`).

**Nobody is provisioned automatically.** In `cloudflare_access` mode a verified
email that matches no active, non-synthetic account is refused. In `shared_gate`
mode the persona list is exactly the accounts an administrator created.

**The unsafe combinations refuse to start.** `manage.py check` fails on real data
with `DEBUG` (E004), real data with the synthetic sign-in (E003), real data with
no authenticator (E006), `cloudflare_access` unconfigured (E007), the synthetic
sign-in beside a real authenticator (E008), an unknown mode (E009), a missing or
too-short gate password (E010, E011), a disabled rate limit (E012), and a gate
session cookie that is not `Secure` (E013).

## The shared gate — what it is, and what it is not

**Temporary.** It exists so the development phase can proceed without waiting
for a Cloudflare dashboard, and it is replaced by `AUTH_MODE=cloudflare_access`
with no other change.

**It authenticates the door.** One password for the department, supplied
host-side in `JURISTID_SHARED_GATE_PASSWORD`, hashed once at process start,
compared in constant time, and rate limited per client with an escalating,
capped lockout. Passing it proves somebody knows the department's password.

**It does not authenticate the person.** Behind the gate you pick whose work you
are looking at. That selection drives `Minu töö`, ownership filters and profile
context — and it is not evidence that the named human is at the keyboard. Every
audit row records both:

```
authenticated_via = SHARED_GATE
acting_as_user    = <the selected persona>
```

Passing the gate is logged as `SHARED_GATE_PASSED`, deliberately not as
`AUTHENTICATION_SUCCEEDED`, so nobody reading the trail later mistakes "somebody
typed the department password" for "this person signed in". Persona changes are
logged every time, with the previous and the chosen persona.

**What this means in practice.** Anyone with the password sees everything
NORMAL and can select any persona, including one entitled to RESTRICTED
material. The gate is the perimeter; the persona is a lens. One secret is shared
by several people, so it cannot be revoked for one of them — only rotated for
all. Anything that later needs "who did this" as evidence needs
`cloudflare_access` first. See `docs/adr/0016`.

### The flow

```
juristid.orgusaar.ee
    ↓  shared password
department Ülevaade          ← useful with no persona; NORMAL visibility only
    ↓  Vali kasutaja
selected persona → Minu töö
```

The landing dashboard renders for a *department scope*, not for an arbitrary
person's identity: NORMAL visibility, no participation, so nothing RESTRICTED
appears merely because the password was typed. Everything except Ülevaade needs
a persona, because authoring anything needs somebody to attribute it to.

Changing persona does not ask for the password again. Signing out closes both.

### Rotating the password

Edit `JURISTID_SHARED_GATE_PASSWORD` in the host's environment file and restart
the web container. Nothing is stored anywhere else, so nothing else has to
change. Everyone's session survives until it ages out
(`SHARED_GATE_SESSION_SECONDS`); to end them immediately, also rotate
`DJANGO_SECRET_KEY`.

## First deployment

Everything below runs on the Unraid host. Nothing in it destroys anything; the
one step that could is called out.

### 1. The source corpus

Copy the three source trees to `/mnt/user/juristid-main/source/`:

```
source/
  excel/Tööd eelnõudega.xlsx
  onenote-desktop-archive/
  migration-audit/
```

Then verify the transfer against the audit's own manifest — every file, by
SHA-256, not by size or count:

```bash
docker compose -p juristid-main -f compose.yml run --rm web python manage.py historical_import inspect
```

`inspect` reads and writes nothing. It reports the register's digest, the
archive manifest's canonical digest, and whether the planned counts reconcile
with the audit baseline. **If any line does not say "reconciles", stop.** A
corpus that half-imported is indistinguishable from a complete one to everybody
who reads it later.

After the copy, treat that directory as read-only source material. It is mounted
`:ro` into both application containers so the importer cannot rewrite it.

### 2. Configuration

```bash
install -d -m 700 /mnt/user/appdata/juristid-main/config
cp .env.example /mnt/user/appdata/juristid-main/config/juristid.env
chmod 600 /mnt/user/appdata/juristid-main/config/juristid.env
```

Fill in `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`,
`JURISTID_SHARED_GATE_PASSWORD` and the hostname.

Leave `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUDIENCE` empty for now: they are
the two values `AUTH_MODE=cloudflare_access` needs, and this stack runs
`shared_gate`. When the Access application exists, the audience tag comes from
Cloudflare One → Access → Applications → this application → Overview →
*Application Audience (AUD) Tag*.

There is deliberately no `APPLICATION_REVISION` and no `APPLICATION_STAGE` in
the template. Both used to be copied here and both went stale where nobody
looked; the stage now comes from the code and the revision from the image. See
the template's own comments.

### 3. Cloudflare

Create the tunnel locally on the host, so the credential is generated here and
never leaves:

```bash
cloudflared tunnel login
cloudflared tunnel create juristid-main
```

Put its JSON and a `config.yml` routing the hostname to `http://web:8000` in
`/mnt/user/appdata/juristid-main/cloudflared/`.

`juristid.orgusaar.ee` is the live hostname. It served the synthetic rehearsal
first, so the cutover is a change of which tunnel claims it — see **Cutover**
below. There is no second live hostname.

Cloudflare Access is the next hardening step and is **not** required to run:
with `AUTH_MODE=shared_gate` the application authenticates its own requests. When
the Access application exists, set `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUDIENCE`
and `AUTH_MODE=cloudflare_access`, and restart. Nothing else changes.

### 4. Start

```bash
docker compose -p juristid-main -f compose.yml build
docker compose -p juristid-main -f compose.yml up -d db
docker compose -p juristid-main -f compose.yml run --rm web python manage.py migrate
docker compose -p juristid-main -f compose.yml up -d
```

Migrations are a deliberate step, never container start-up work: on boot they
would run on every restart.

### 5. Accounts

Create the real people, by hand, once. There is no self-service and no
auto-provisioning:

```bash
docker compose -p juristid-main -f compose.yml run --rm web \
  python manage.py createsuperuser --upn <email> --display_name "<name>"
```

These become the persona list behind the gate, and later the accounts Cloudflare
Access asserts against. Use real addresses: do not invent `.invalid` identities
here and do not use somebody else's address as a placeholder.

## Importing the historical corpus

Six phases, in order. Each is a separate command rather than a flag, because
they have genuinely different consequences and a flag is easy to mistype.

```bash
C="docker compose -p juristid-main -f compose.yml run --rm web python manage.py"

$C historical_import plan          # reads everything, writes nothing
$C historical_import dry-run       # the real apply, rolled back
$C historical_import apply         # commits pages, links, Matters, the queue
$C historical_import materialise   # copies originals into evidence; resumable
$C historical_import status
$C historical_import verify
$C rebuild_search_index
```

`plan` needs no gate. `apply` refuses unless `REAL_DATA_ALLOWED` is on **and**
every baseline check reconciles.

`materialise` streams roughly 4.14 GiB and takes a while. It is resumable by
asking the database what is still missing, so interrupting it costs nothing:
re-run the same command. `--limit N` stops after N files, which is the right way
to watch the first few before committing to the rest.

Back up before `apply` and after `verify`. Not with a `pg_dump` pipeline — see
[`RECOVERY.md`](RECOVERY.md) for why that line could produce a truncated dump
and report success:

```bash
scripts/deploy/juristid-backup.sh --project juristid-main --compose-file deploy/unraid-main/compose.yml --data-root /mnt/user/appdata/juristid-main --backup-root /mnt/user/backups/juristid-main
```

## The register import

`historical_import` imports the OneNote side and links it to Matters that
already exist. The Excel register itself is imported by `register_import`
(ADR 0012) and must run first — otherwise every exact link reports its reference
as not found, which is correct behaviour and a wasted afternoon.

## Everyday operations

```bash
docker compose -p juristid-main -f compose.yml ps
docker compose -p juristid-main -f compose.yml logs -f web
docker compose -p juristid-main -f compose.yml logs -f extractor
docker compose -p juristid-main -f compose.yml logs -f searchindex
docker compose -p juristid-main -f compose.yml restart web
```

`searchindex` is the search freshness worker. It sleeps until a canonical change
owes the index a rebuild — an Organisation rename, an alias edit, a Tag or
PolicyArea rename, a person's display name — and then performs one atomic full
rebuild (ADR 0041). Nothing is owed most of the time, so an empty log is the
normal state. To ask the question directly:

```bash
docker compose -p juristid-main -f compose.yml exec -T web python manage.py check_search_freshness
```

If it says the index has been owed a rebuild for too long, the worker is what to
look at first. `rebuild_search_index` remains the manual answer and is always
safe to run.

## Deploying a new build

Deploy a **commit**, never a branch. `git pull` deploys whatever `main` has
become since somebody decided to deploy, and on a repository several people and
several agents push to, that is routinely not the thing that was reviewed. The
difference is invisible until something unreviewed is serving members' material.

So the target is a full 40-character SHA, and the preflight refuses an
abbreviation — two commits can share a prefix and the resolution is silent.

### 1. Write down what is running now

```bash
curl -s https://juristid.orgusaar.ee/healthz
```

The `revision` this returns is the rollback target, and it is the one fact that
becomes unavailable the moment the deployment goes wrong. Read it before, not
after.

### 2. Preflight

Read-only. It changes nothing, moves nothing, and prints the commands to run.

```bash
scripts/deploy/juristid-deploy-preflight.sh --repo /mnt/user/appdata/juristid-main/repo --target <full-40-char-sha> --compose-file deploy/unraid-main/compose.yml --env-file /mnt/user/appdata/juristid-main/config/juristid.env --data-root /mnt/user/appdata/juristid-main --backup-root /mnt/user/backups/juristid-main
```

It verifies that the commit exists, that it is ahead of what is running, that
the checkout is clean, that the Compose file resolves with no host port and a
read-only corpus, that the environment file exists and is mode 600, and that
there is room to write a backup.

**If it says the checkout is dirty, stop.** Somebody changed something on the
server, and finding out what matters more than this release. Do not
`git reset --hard`, do not `git clean`, do not check out over it: whatever those
changes are, deleting them destroys the only record that they existed. Nothing
in a deployment is urgent enough to be worth that.

### 3. Move the checkout to the reviewed commit

```bash
git -C /mnt/user/appdata/juristid-main/repo checkout --detach <full-40-char-sha>
```

Detached on purpose. The deployment is at a commit, not on a branch that can
move underneath it.

### 4. Name the release

Two variables, exported once, before anything is built. Everything after this
point — the build, the migration plan, the migration, the replacement — reads
them, and that is the point: one shell, one identity, no step that can quietly
resolve a different image.

```bash
export JURISTID_GIT_SHA=<full-40-char-sha>
```

```bash
export JURISTID_IMAGE_TAG=${JURISTID_GIT_SHA:0:12}
```

The SHA is passed into the build so the image can say what code it is. The tag
names the image, so the previous build stays on the host under its own name and
a rollback is a tag rather than a rebuild.

`juristid-main-web:local` is the fallback tag Compose uses when
`JURISTID_IMAGE_TAG` is unset, and it is deliberately the one tag that gets
overwritten. A `migrate` that runs against `:local` is a schema change made by
whatever was last hand-built on this host, which is not the thing that was
reviewed. Exporting both variables first is what stops that.

### 5. Build the target image

```bash
docker compose -p juristid-main -f compose.yml build
```

Build before reading the migration plan, deliberately. A build writes no
business data, mutates no database and does not replace the running
application — it only produces the candidate image. Everything that changes
something still happens after the backup in step 8.

### 6. Read the migration plan — from the target image

```bash
docker compose -p juristid-main -f compose.yml run --rm web python manage.py migration_plan
```

`run --rm`, not `exec`. The distinction is the whole point of the step.

Application source is `COPY`ed into the image and this stack bind-mounts no
source into `/app` — only evidence, derivatives, the OneNote source and the
read-only corpus. So the container that `exec` would enter is still the
**previously deployed** image, and moving the checkout in step 3 changed nothing
inside it. Asked there, `migration_plan` reads the old release's migration graph
and can answer "No pending migrations." for a release that carries several,
which is the reassuring answer given at exactly the wrong moment.

`run --rm web` starts a one-off container from the image built in step 5 — the
target code — against the running database. That is the pair the question is
about: **new code, current schema.** It reports and never migrates.

If everything is additive, the old web process keeps working against the new
schema while it is replaced, which is what makes the sequence below safe.

If anything is not additive, this is not a rolling deployment. Decide first
whether the release now serving survives the new schema; if it does not, tell
the department, take the application down, migrate, and bring it back. Six users
and an announced ten minutes is better than a silent compatibility gamble.

### 7. Release-specific pre-migration audits

Most releases have none, and this step is then nothing. Some do, and the reason
they belong *here* rather than after the migration is that a target-image
one-off container can answer a question about the **current** database before
the schema moves — which is when a finding is still cheap to act on.

The same `run --rm web` shape as step 6, for the same reason: the audit has to
be the new release's audit, because an audit the old image does not have cannot
be run by entering the old image.

**A release that installs a new integrity constraint.** Where the target release
adds a database-level guarantee over relationships that already exist, run that
release's read-only integrity check against the still-unmigrated database
first — for example:

```bash
docker compose -p juristid-main -f compose.yml run --rm web python manage.py check_evidence_integrity --skip-storage-scan
```

`--skip-storage-scan` keeps it to the relational question — "does any row point
at something it should not" — instead of walking the evidence store, which is a
maintenance window rather than a deployment step. The command reads; it does not
migrate and it writes nothing.

**If it reports relationship findings, stop.** No migration, no repair, no
"fix it and carry on". Rows that a new constraint would reject are a question
about which of the two records is right, and that is a human decision taken with
the register in front of you — not something a deployment decides at half past
eight. Nothing here repairs anything automatically, and nothing here should
grow that ability.

Whether a given release needs this step is a property of that release, and the
release note says so. Do not make it unconditional: a check for a constraint the
target release does not contain is a command that either does not exist in the
image or answers a question nobody asked.

**Today that release exists.** Main carries `submissions/0005` and `0006`, the
two migrations that install exactly this kind of guarantee over relationships
already in the database. A production instance that has not yet crossed them
owes the audit above before it does — once, on the release that installs them.
It is named here rather than left to a release note because the condition is a
fact about the database in front of you: `migration_plan` from step 6 lists
them as pending, and that listing is what makes this step apply.

### 8. Back up

Always, and immediately before the migration rather than that morning:

```bash
scripts/deploy/juristid-backup.sh --project juristid-main --compose-file deploy/unraid-main/compose.yml --data-root /mnt/user/appdata/juristid-main --backup-root /mnt/user/backups/juristid-main
```

This is the copy a failed migration is rolled back to. Everything written
between it and the failure is lost in that rollback, which is why it is taken
now — after the build and the plan, immediately before the first command that
changes the database — and not earlier. The build moving ahead of it does not
move it: a build is not a schema change, and the backup's job is to be the last
thing before one.

### 9. Migrate, then replace

Still the same shell, so still the same two variables, so still the same image
that step 6 read the plan from.

```bash
docker compose -p juristid-main -f compose.yml run --rm web python manage.py migrate
```

```bash
docker compose -p juristid-main -f compose.yml up -d
```

Migrations are a deliberate step, never container start-up work: on boot they
would run on every restart, including the restart that happens at three in the
morning because the host rebooted.

### 10. Post-flight

```bash
docker compose -p juristid-main -f compose.yml exec -T web python manage.py deployment_readiness
```

`exec`, and here that is the correct word. Steps 6, 7 and 9 asked questions
about code that was not running yet, so they had to start a container from the
target image. This one asks about the process that is now serving, so it enters
it. The rule is not "`run` is safe and `exec` is not" — it is that the command
must be aimed at whichever image the question is about.

Fails closed on an unapplied migration, a migration the database has that this
build does not, a missing or wrongly-mounted storage root, a PostgreSQL major
that is too old, or a build that cannot say which commit it came from. It reads
and reports; it never migrates.

Then confirm the running revision is the one that was deployed:

```bash
curl -s https://juristid.orgusaar.ee/healthz
```

`revision` should be the SHA that was built — the exact one, compared in full,
not the twelve characters the image tag happens to share with it. The footer's
build time moves with the image on its own, so a revision that changed beside a
build time that did not is a build that did not actually replace anything.

Anything beyond this is release-specific and the release note names it. A
release that changes how something is indexed, projected or derived may need a
rebuild afterwards; a release that changes none of those needs nothing here.
Run what that release asks for, against the running new image — so `exec`, like
the readiness check above.

### Rolling back

Code-only rollback is the same sequence with the previous reviewed SHA. Rolling
back *across* a migration is not, and it is not a command —
[`RECOVERY.md`](RECOVERY.md) has the decision tree.

### What a deployment must never do

A deployment carries code and schema migrations. It does **not** import the
register, promote the current register, apply an opinion archive, backfill
owners or run a historical cutover. Those write the Chamber's record, they each
have their own review gate, and none of them may happen because somebody
restarted a container. A test asserts that no service's start-up command can
reach one.

## Backup, restore, disaster recovery

All of it is in [`RECOVERY.md`](RECOVERY.md): what is canonical and what is
rebuildable, how a set is produced and verified, the restore order for a fresh
host, the rollback decision tree, and — stated plainly there — the fact that the
current backups are a local recovery copy rather than off-host disaster
recovery.

## What must not happen here

- **No `docker compose down -v`.** The `-v` removes volumes, and the evidence
  tree is the one thing in this system that cannot be regenerated.
- **No `docker system prune`, no `docker volume prune`, no host reboot as a
  troubleshooting step.** This host runs other people's services.
- **Nothing that stops or removes `juristid-test`** or any unrelated container.
- **No `git clean`** in the checkout.
- **No weakening of the safety checks** to get a process to start. If
  `manage.py check` refuses, the configuration is wrong, not the check.
- **No real data leaving this host.** Not into Git, not into CI, not into a PR
  comment, not into a screenshot, not into a log uploaded anywhere. The
  repository is public.
- **No test suite through this Compose project.** See below — this one has
  already happened.

### Never run the application test suite through this stack

Not `docker compose -p juristid-main run … pytest`, not `docker exec
juristid-main-web pytest`, not a one-off container attached to
`juristid-main-internal`. Tests belong in CI, in a development checkout, or in a
Compose project created for testing.

**This is not advice; it happened.** On 2026-08-24 a pytest run in a container
derived from this stack's image wrote 63 synthetic test files into
`/mnt/user/appdata/juristid-main/evidence`. Three things combined:

1. the image sets `DJANGO_SETTINGS_MODULE=config.settings`, and pytest-django
   reads the environment *before* `pyproject.toml` — so production settings won;
2. `docker compose run` inherits the service's environment and volumes, so
   `EVIDENCE_ROOT=/app/evidence` still pointed at the real evidence bind mount;
3. Django's test runner created and dropped its own `test_juristid` database, so
   the rows vanished and the bytes did not.

The consequence was 1,473 bytes of harmless fixtures and an integrity report
that has exited non-zero ever since. The same path was open to a larger file,
and the same misconfiguration decides which *database* a plain `manage.py`
command talks to — there the test runner would not have saved anything.

Three controls now make the mistake fail closed rather than write:

- `pyproject.toml` passes `--ds=config.test_settings`, the one form that beats
  an inherited `DJANGO_SETTINGS_MODULE`;
- `config/test_settings.py` refuses to finish importing when the environment has
  `REAL_DATA_ALLOWED` on, carries `JURISTID_RUNTIME`, or names a deployment's
  storage root — and both application services here set `JURISTID_RUNTIME`
  precisely so a test process started through them stops;
- `tests/conftest.py` gives every test its own temporary evidence, derivative
  and legacy-source directories, whether or not the test asks.

Any of the three alone would have prevented it. If a refusal ever gets in the
way, the environment is wrong, not the refusal.


## Cutover

`juristid.orgusaar.ee` is the live hostname and stays the live hostname. The
synthetic rehearsal used it first; moving it means pointing the DNS record at
this stack's tunnel, not creating a second name.

```bash
# The rehearsal keeps running, on its own project, network and database.
# Its data is preserved. Only the public name moves.
cloudflared tunnel route dns juristid-main juristid.orgusaar.ee
docker compose -p juristid-main -f compose.yml up -d tunnel
```

`juristid-test` is not stopped, not removed, and not migrated. If it needs a
public name of its own afterwards, give it an internal one — never a second
live one.

## Before calling it live

From a new private window, in this order:

| | Check |
| --- | --- |
| A | an unauthenticated visitor sees no Juristid data at all |
| B | a wrong password is rejected |
| C | repeated failures are rate limited |
| D | the correct password opens the department Ülevaade |
| E | the dashboard is useful with no persona selected |
| F | selecting a persona changes `Minu töö` |
| G | changing persona changes the profile context |
| H | persona switching appears in the security audit |
| I | RESTRICTED material does not appear in the department scope |
| J | there is no direct origin bypass — no host port answers |
| K | signing out ends both the persona and the gate |
| L | historical data survives a container restart |

A–L are covered by `tests/test_shared_gate.py` as logic. Doing them in a real
browser is what catches the difference between the logic and the deployment.
