# Juristid — synthetic rehearsal on the Unraid host

A LAN-only instance for real browser use: usability testing, workflow
rehearsal, and the class of defect CI structurally cannot reach. It runs on
invented data and nothing else.

| | |
| --- | --- |
| URL | <http://192.168.1.133:3020> |
| Compose project | `juristid-test` |
| Containers | `juristid-test-web`, `juristid-test-db` |
| Network | `juristid-test-internal` (its own bridge) |
| Appdata | `/mnt/user/appdata/juristid-test/` |
| Checkout | `/mnt/user/appdata/juristid-test/repo` |
| Secrets | `/mnt/user/appdata/juristid-test/config/juristid.env`, mode 600, never in Git |

## What this environment is, and what it is not

**Synthetic only.** `REAL_DATA_ALLOWED=0`, and it stays that way. The Unraid box
is not an approved Koda real-data environment: Secure Pilot Gate item 10 — *no
production or member data copy on developer or home machines* — is already in
force. Do not copy the register here. Do not run the importer against a real
workbook here, not even a dry run. That belongs on an approved Koda-controlled
workstation.

**It has no authentication.** `DEV_LOGIN_ENABLED=1` lets anyone who reaches the
site sign in as any seeded user by picking them from a list, and `DJANGO_DEBUG=1`
means Django returns a traceback on any unhandled error. Both are required for
the other — the application refuses to start with development sign-in unless
DEBUG is on (check `juristid.E002`) — and both are acceptable *only* because the
data is invented and the site is reachable only from the LAN.

That combination is exactly why this must not be published to the internet
without an authenticating proxy in front of it. Putting a passwordless,
traceback-serving Django instance on a public hostname is a different decision
from putting it on the LAN, and it needs to be made deliberately.

## Isolation

Nothing here touches anything else on the host. Its own Compose project, its own
bridge network, its own PostgreSQL, its own appdata subtree, one published port.
It joins no existing network, and the database publishes no host port at all —
it is reachable only from `juristid-test-internal`.

Always name both the project and the file, so a stray command cannot reach
another stack:

```bash
docker compose -p juristid-test -f compose.yml ps
```

Never run bare `docker compose down`, `docker system prune`, or
`docker restart $(docker ps -q)` on this host. Other people's services live here.

## First deployment

```bash
install -d -m 755 /mnt/user/appdata/juristid-test/{postgres,evidence,repo}
install -d -m 700 /mnt/user/appdata/juristid-test/config
```

The application runs as uid/gid 10001 and a bind mount keeps host ownership, so
the evidence directory has to be handed to it explicitly. A named volume would
inherit the image's ownership; a bind mount does not.

```bash
chown -R 10001:10001 /mnt/user/appdata/juristid-test/evidence
```

Clone, then copy `.env.example` to the config path and fill in the two secrets.
Generate them straight into the file rather than through the shell, so they
never reach history or a log:

```bash
openssl rand -base64 64 | tr -d '\n'
```

Build, then bring the database up on its own and wait for it to be healthy:

```bash
docker compose -p juristid-test -f compose.yml up -d db
```

Migrations are a **controlled one-off step**, never container start-up work. A
container that migrates on boot migrates again on every restart nobody intended:

```bash
docker compose -p juristid-test -f compose.yml run --rm web python manage.py migrate --noinput
```

Then the checks, the synthetic seed, and the search index:

```bash
docker compose -p juristid-test -f compose.yml run --rm web python manage.py check_search_capabilities
```

```bash
docker compose -p juristid-test -f compose.yml run --rm web python manage.py seed_dev_data
```

```bash
docker compose -p juristid-test -f compose.yml run --rm web python manage.py rebuild_search_index
```

`seed_dev_data` refuses to run unless `DJANGO_DEBUG=1` and `REAL_DATA_ALLOWED=0`.
Finally:

```bash
docker compose -p juristid-test -f compose.yml up -d web
```

## Updating

Deliberately manual. This instance does not track `main`; it runs a revision
somebody chose, and `/healthz` reports which one.

```bash
cd /mnt/user/appdata/juristid-test/repo && git fetch origin
```

Check out the approved revision, then record it so the running container can
say what it is:

```bash
git -C /mnt/user/appdata/juristid-test/repo rev-parse --short HEAD
```

Put that value in `APPLICATION_REVISION` in the env file, rebuild, migrate as a
separate step, and only then restart the web container:

```bash
docker compose -p juristid-test -f compose.yml build web
```

```bash
docker compose -p juristid-test -f compose.yml run --rm web python manage.py migrate --noinput
```

```bash
docker compose -p juristid-test -f compose.yml up -d web
```

Rebuild the search index only when the indexed content or the index version
changed — the projection maintains itself for ordinary edits. It is atomic, so
an interrupted rebuild leaves the previous complete index serving.

Confirm what is running:

```bash
curl -s http://192.168.1.133:3020/healthz
```

## Backup

Synthetic, but usability-test history is worth keeping. Backups live under their
own path and touch no existing backup system.

```bash
install -d -m 755 /mnt/user/backups/juristid-test
```

Database:

```bash
docker compose -p juristid-test -f compose.yml exec -T db pg_dump -U juristid -d juristid | gzip > /mnt/user/backups/juristid-test/juristid-$(date -u +%Y%m%d-%H%M%S).sql.gz
```

Evidence — `-a` preserves the uid 10001 ownership the application needs:

```bash
tar -C /mnt/user/appdata/juristid-test -czf /mnt/user/backups/juristid-test/evidence-$(date -u +%Y%m%d-%H%M%S).tar.gz evidence
```

Nothing is scheduled. Adding a cron entry on this host means editing
`/etc/cron.d/root`, which is generated from the flash drive — get that wrong and
Unraid's own mover and parity jobs disappear. Run these by hand, or design the
schedule deliberately.

Restore into a **running** database:

```bash
gunzip -c /mnt/user/backups/juristid-test/juristid-TIMESTAMP.sql.gz | docker compose -p juristid-test -f compose.yml exec -T db psql -U juristid -d juristid
```

## Rollback

Rollback never deletes data. The database and the evidence tree are untouched;
only the application image moves.

```bash
git -C /mnt/user/appdata/juristid-test/repo checkout <previous-sha>
```

```bash
docker compose -p juristid-test -f compose.yml build web
```

```bash
docker compose -p juristid-test -f compose.yml up -d web
```

Update `APPLICATION_REVISION` to match, or `/healthz` will report the wrong
commit.

**Schema is the exception.** Django migrations are not automatically
reversible, and rolling the code back does not roll the schema back. Rolling
back *across* a migration therefore needs a decision, not a command: either the
older code tolerates the newer schema — usually true for additive migrations,
which this project uses — or the database is restored from a dump taken before
the upgrade. Take that dump before any update that migrates. Never run
`migrate <app> <number>` backwards without reading what it does; several
migrations here install triggers and constraints whose reverse drops a
guarantee rather than a column.

## Health and logs

```bash
docker compose -p juristid-test -f compose.yml ps
```

```bash
docker compose -p juristid-test -f compose.yml logs --tail 100 web
```

`/healthz` returns the database state, environment, stage and revision.

## Restarting

Only ever the two Juristid containers, always by project and file:

```bash
docker compose -p juristid-test -f compose.yml restart web
```

The database, the evidence tree and the search index all survive a restart and a
full stop/start; the only thing lost is signed-in sessions.
