# Juristid — the real-data instance on the Unraid host

The Chamber's twenty-year legislative register and the OneNote material attached
to it, in one system. This is the environment that holds the real thing.

| | |
| --- | --- |
| LAN URL | **none — there is no host port** |
| Public URL | behind Cloudflare Access; the hostname lives in the host's env file |
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
only route in is the Cloudflare tunnel, which means the only route in goes
through Cloudflare Access. You cannot reach this by typing the server's LAN
address, and `tests/test_deployment_unraid_main.py` fails if a port ever appears.

**The application verifies Access itself.** Cloudflare adds a signed assertion
to each request it forwards; `app/accounts/middleware.py` verifies the RS256
signature against the team's published keys, checks the audience tag and the
issuer, and only then believes the email. A request header on its own is
attacker-controlled — anybody who could reach the container directly could set
it to anything. There is no fallback to the unsigned
`Cf-Access-Authenticated-User-Email` header.

**Nobody is provisioned automatically.** A verified email that matches no active,
non-synthetic account is refused. Widening an Access policy in a Cloudflare
dashboard must not hand somebody a seat inside a system of confidential member
material.

**The unsafe combinations refuse to start.** `manage.py check` fails on real data
with `DEBUG` (E004), real data with the synthetic sign-in (E003), real data with
no authenticator (E006), Access enabled but unconfigured (E007), and the
synthetic sign-in behind Access (E008).

There is no shared PIN here. The rehearsal's exists because everything behind it
is invented; a shared code is not identity and has no place in front of this.

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

Create the tunnel locally on the host — `cloudflared tunnel login`, then
`cloudflared tunnel create juristid-main` — so the credential is generated here
and never leaves. Put its JSON and a `config.yml` routing the hostname to
`http://web:8000` in `/mnt/user/appdata/juristid-main/cloudflared/`.

Then create the Access application for the same hostname, with one policy:
action *Allow*, selector *Emails*, value the addresses that should get in. Access
must exist before the tunnel is started — the application refuses requests that
arrive without a valid assertion, so a tunnel without Access serves 403s rather
than serving the register.

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

Use the addresses Cloudflare Access will assert. Do not invent `.invalid`
identities here and do not use somebody else's address as a placeholder.

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
