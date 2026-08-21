# Juristid — the real-data instance on the Unraid host

The Chamber's twenty-year legislative register and the OneNote material attached
to it, in one system. This is the environment that holds the real thing.

| | |
| --- | --- |
| LAN URL | **none — there is no host port** |
| Auth mode | `shared_gate` (temporary; see below) |
| Public URL | `https://juristid.orgusaar.ee` — behind the shared gate |
| Compose project | `juristid-main` |
| Containers | `juristid-main-web`, `juristid-main-db`, `juristid-main-extractor`, `juristid-main-tunnel` |
| Network | `juristid-main-internal` (its own bridge) |
| Appdata | `/mnt/user/appdata/juristid-main/` |
| Evidence | `…/evidence` — **back this up** |
| Derivatives | `…/derivatives` — rebuildable, needs no backup |
| Page XML | `…/legacy-source` — **back this up**; source evidence |
| Source corpus | `/mnt/user/juristid-main/source/` — **read-only**, mounted `:ro` |
| Secrets | `/mnt/user/appdata/juristid-main/config/juristid.env`, mode 600, never in Git |

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

Fill in `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, the hostname, and the three
Cloudflare Access values. The audience tag comes from Cloudflare One → Access →
Applications → this application → Overview → *Application Audience (AUD) Tag*.

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

Back the database up before `apply` and after `verify`:

```bash
docker exec juristid-main-db pg_dump -U juristid juristid | gzip > /mnt/user/backups/juristid-main-$(date +%F-%H%M).sql.gz
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
docker compose -p juristid-main -f compose.yml restart web
```

Deploying a new build:

```bash
git -C /mnt/user/appdata/juristid-main/repo pull
docker compose -p juristid-main -f compose.yml build
docker compose -p juristid-main -f compose.yml run --rm web python manage.py migrate
docker compose -p juristid-main -f compose.yml up -d
```

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
