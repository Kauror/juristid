# Juristid — Koda Õigusloome

Internal legislative matter and advocacy management system for Eesti
Kaubandus-Tööstuskoda.

> **No real Koda, member or otherwise confidential data may be used in this
> repository, in local development, in tests or in prompts.** Real departmental
> data may only enter an environment that has passed the
> [Secure Pilot Gate](docs/secure-pilot-gate.md). Local development uses
> synthetic data only.

## Current stage

**Stage 0 — decisions, skeleton and CI.** There is no product UI yet: no Minu
töö, Saabunud, Teemad, composer, submissions, consultation, search UI or
statistics. What exists is the application skeleton, the foundational schema,
the authorization boundary, the design-token foundation, the architecture
decisions and the pipeline that keeps them honest.

See [`prompts/stage-0-coding-agent.md`](prompts/stage-0-coding-agent.md) for the
authorized scope and [`docs/open-decisions.md`](docs/open-decisions.md) for what
must be decided before Stage 1.

## Authoritative specification

[`docs/master-specification.md`](docs/master-specification.md) is the source of
truth for product, data, architecture, security, migration and delivery.
Implementation must not silently contradict or expand it; material departures
need an ADR and explicit approval. Contributor rules live in
[`AGENTS.md`](AGENTS.md).

## Requirements

- Docker and Docker Compose (the supported way to run everything), or
- Python 3.13, [uv](https://docs.astral.sh/uv/) and PostgreSQL **18 or later**
  for running the application directly.

PostgreSQL 18 is a hard requirement: the Estonian full-text search configuration
must be present. `manage.py check_search_capabilities` verifies it.

## Run it with Docker

```bash
docker compose up --build
```

This starts PostgreSQL 18 and the application, applies migrations, verifies the
search capabilities, seeds synthetic data and serves on
<http://localhost:8000>. Sign in through **Arenduse sisselogimine** as one of
the synthetic users.

Useful variants:

```bash
docker compose up -d --wait
```

```bash
docker compose exec web python manage.py migrate
```

```bash
docker compose down -v
```

The last one also removes the database and evidence volumes.

## Run it without Docker

```bash
uv sync
```

```bash
cp .env.example .env
```

Point `.env` at a PostgreSQL 18 database, then:

```bash
uv run python manage.py migrate
```

```bash
uv run python manage.py check_search_capabilities
```

```bash
uv run python manage.py seed_dev_data
```

```bash
uv run python manage.py runserver
```

`seed_dev_data` refuses to run unless `DJANGO_DEBUG=1` and
`REAL_DATA_ALLOWED=0`.

## Tests and checks

Everything below is exactly what CI runs. The test suite needs a PostgreSQL 18
database; set `POSTGRES_*` or `DATABASE_URL` first.

```bash
uv run ruff format --check .
```

```bash
uv run ruff check .
```

```bash
uv run mypy
```

```bash
uv run python manage.py check
```

```bash
uv run python manage.py makemigrations --check --dry-run
```

```bash
uv run pytest --cov
```

To run the suite against the compose database without installing PostgreSQL
locally, start only the database container:

```bash
docker compose up -d db
```

```bash
POSTGRES_HOST=127.0.0.1 POSTGRES_SSLMODE=disable uv run pytest --cov
```

The application image is built without development dependencies, so the test
runner lives on the host rather than inside the web container.

## Migrations

```bash
uv run python manage.py makemigrations
```

```bash
uv run python manage.py migrate
```

```bash
uv run python manage.py migrate <app> <migration>
```

A fresh database migrates from zero; `tests/test_migrations.py` fails if any
model change is missing a migration. In deployed environments migrations are a
separate controlled step, never applied on container start.

## Repository layout

```
AGENTS.md                  contributor and agent rules
README.md
config/                    Django project: settings, urls, wsgi/asgi
app/
  core/                    shared bases, UUIDv7, authorization chokepoint, checks
  accounts/                custom User (from migration 0001), break-glass access
  organisations/           institutions, aliases, predecessor links
  taxonomy/                PolicyArea, Tag, tag aliases
  workflow/                stage vocabulary, track and closure enums, legacy status mapping
  matters/                 the canonical Matter, reference allocation, tag assignment
  documents/               Document and immutable DocumentVersion evidence
  audit/                   append-only ChangeEvent and SecurityAuditEvent
  search/                  PostgreSQL search capabilities and extensions
  legacy_import/           ImportBatch and immutable MatterSourceReference
docs/
  master-specification.md  authoritative specification
  adr/                     architecture decision records
  data-contracts/          export contract, per-era Excel contracts, snapshot manifest
  metric-catalog/          metric definition and coverage rules
  secure-pilot-gate.md     checklist gating real data
  open-decisions.md        what business owners still have to decide
static/css/                design tokens and baseline styles
templates/                 Stage-0 shell, landing page, design-token reference
tests/                     synthetic factories and the invariant suite
scripts/                   development container entrypoint
```

## Design system

`static/css/tokens.css` holds the token architecture: primitives, semantic
tokens, dark theme (the MVP theme) and a light theme block that proves a future
light theme needs different values rather than different components.

**Every colour value is provisional.** The official Koda CVI package has not
been supplied, so the current values are contrast-oriented placeholders, not
brand values. See [ADR 0009](docs/adr/0009-design-token-foundation.md). The
running application shows them at `/disainisusteem/`.

## Secrets

`.env` and every `.env.*` file except `.env.example` are gitignored. No secret
belongs in Git, in the container image or in a client bundle. In a non-debug
process, a missing `DJANGO_SECRET_KEY` is a startup error and the development
key is rejected by a system check.
