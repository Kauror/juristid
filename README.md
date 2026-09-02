# Juristid — Koda Õigusloome

Internal legislative matter and advocacy management system for Eesti
Kaubandus-Tööstuskoda.

> **No real Koda, member or otherwise confidential data may be used in this
> repository, in local development, in tests or in prompts.** Real departmental
> data may only enter an environment that has passed the
> [Secure Pilot Gate](docs/secure-pilot-gate.md). Local development uses
> synthetic data only.

## Where this is

**Every numbered stage is merged.** Stage 0 laid the skeleton, the foundational
schema, the central authorization boundary, the design-token foundation and the
CI pipeline that keeps the decisions honest. Stage 1 built the core lawyer
workflow. Stage 2 arrived in pieces, each in its own pull request:

| | |
| --- | --- |
| 2A | the legacy register import — per-era workbook contracts, offline inspection, dry run and apply, immutable provenance — and the rebuildable `SearchDocument` projection behind Estonian full-text search |
| 2A.5 | the rehearsal's operational foundation, and the corrections a week of real browser use produced |
| 2B | content extraction and everything that depends on it: `DocumentDerivative`, parsers for PDF, DOCX, XLSX, PPTX, TXT, CSV, EML, MSG and images, local OCR, a database-backed worker, email attachments captured as evidence, and search extended to entries, submissions and document contents |
| 2D | the OneNote desktop archive as first-class history beside the register, and `AUTH_MODE` as the authentication seam |
| 2E | a code-defined, versioned metric catalogue and the `Statistika` workspace, with coverage on every number and drill-through from every figure |
| 2E.1 | the corrections real data made obvious once there was some |
| 2F | the current portfolio and the department-head view |
| 2G | structured Matter facts — important dates, entry into force, work victories |
| 2H, 2H.1, 2H.2 | the historical opinions archive, its applied candidates, and making the whole corpus searchable evidence |
| 2I | the historical cutover state, and what a closed archive row may claim |

**Since then the work has been named by decision rather than by number**, because
the stages had stopped describing it. The final register cutover replaced the
year-only rule for current work with a reviewed snapshot; the approved v2 design
was implemented over the application that already existed rather than beside it;
`Ülevaade` and `Osakonna töö` became one `/osakond/`; `Arvamused` became a
section of `Teemad`; the persona switcher, department-wide lawyer access and one
HTTP write boundary settled who may read and write what; the test suite was
sharded; and a remediation programme has been closing what a full-codebase
review found.

**[`docs/adr/README.md`](docs/adr/README.md) is the record, not this section.**
Fifty-seven decisions, each with its status, context, alternatives and
consequences. A hand-maintained prose list of stages is a thing that goes stale
between releases; the ADR index is written as part of the change it describes.

**The phase is pre-QA, behind the shared gate.** Every ADR from 0040 onward
records it that way, and it is what `AUTH_MODE=shared_gate` means on the
real-data instance: one department password, then a persona, with Cloudflare
Access — implemented, verified against the team's published keys, and not yet
the mode in force — as the hardening step after it
([ADR 0016](docs/adr/0016-authentication-modes-and-the-shared-gate.md)).
[`docs/production-readiness.md`](docs/production-readiness.md) is the gate
sequence for anything that changes production.

**What the running build calls itself is a label, not a gate.**
`APPLICATION_STAGE` defaults to `Stage 2I` in `config/settings.py` and is what
the footer of every page, the signed-out landing and `/healthz` report. A
deployment does not pin it: a stage copied into a `.env` is a stage that goes
stale where nobody looks, which is why
[`deploy/unraid-main/.env.example`](deploy/unraid-main/.env.example) deliberately
omits it and a test keeps it omitted.

**Synthetic rehearsal — deployed.** An instance on the Unraid host running
invented data only, for the class of defect CI structurally cannot reach. See
[`deploy/unraid-test/`](deploy/unraid-test/).

**Real-data instance — deployed.** A separate stack with no host port at all,
reachable only through a Cloudflare tunnel and only behind an authenticator. How
it is deployed is [`deploy/unraid-main/README.md`](deploy/unraid-main/README.md);
how it is backed up and restored is
[`deploy/unraid-main/RECOVERY.md`](deploy/unraid-main/RECOVERY.md) and
[ADR 0022](docs/adr/0022-deployment-backup-and-recovery.md).

**No real data in this repository.** The repository is public. What enters Git
is code, the synthetic fixtures the tests build from it at test time, the brand
assets and web fonts the interface needs, and the browser suite's visual
baselines — screenshots of a seeded synthetic world.
`tests/test_repository_data_safety.py` fails if a workbook or a row-level import
report is ever tracked, and every directory an operator is told to use for real
material is ignored.

**Deliberately unbuilt.** `Kaasamine` exists and is deliberately small: which
channel, what it was called, an optional link and an optional date. It is a
pointer to outreach, not a campaign tool — no recipient list, no response store,
no click tracking, no provider integration, because the Chamber already buys
that product and a worse copy inside the case file would bury the one fact the
file needs ([ADR 0027](docs/adr/0027-matter-engagement.md)). **Structured member
responses are the part that is not built**, and that ADR is why. Later still:
EIS and Riigikogu integration, advocacy outcomes and attribution, and anything
involving embeddings, semantic search or AI summarisation. Each is authorized by
its own written brief; nothing is built ahead of one.

[`docs/open-decisions.md`](docs/open-decisions.md) lists what still belongs to
named people rather than to the code. `/haldus/arendus/` in the running
application lists what the v2 design could not settle on its own
(`app/core/development_status.py`).

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

## The legacy register

The department's register lives in one workbook with a sheet per year. Every
year has a reviewed contract under `docs/data-contracts/`, and no sheet is
parsed without one.

### Where the real workbook may be

**Not on your laptop.** `private-data/`, `import-input/` and `import-output/`
are gitignored *path conventions* and nothing more. They keep real material out
of Git; they do not make it acceptable for that material to be on the machine in
the first place.

[Secure Pilot Gate](docs/secure-pilot-gate.md) item 10 — *no production or member
data copy on developer or home machines* — is already **in force**, before the
rest of the gate. So:

- **inspecting the real workbook** may happen only on an approved,
  Koda-controlled workstation or environment, under whatever handling rules
  apply to the file there;
- **applying an import to a database** additionally requires the full Secure
  Pilot Gate, and `--apply` refuses to run unless `REAL_DATA_ALLOWED=1`;
- **CI and ordinary development are synthetic only**, always. The test fixtures
  generate their own workbooks; no real one is ever downloaded, committed or
  uploaded.

Within an approved environment, the ignored directories are the right places to
put things, and `tests/test_repository_data_safety.py` fails if a workbook or an
import report is ever tracked. The row-level CSV reports reproduce source content
and stay local; only `summary.json` and `summary.md` carry counts alone and are
safe to share.

If you have a copy of the real register on an unapproved machine — including one
made to try these commands — delete it. The original is authoritative and lives
where the department keeps it.

### The commands

Look at a snapshot without touching anything. **Needs no database**, which is
the point: requiring one pushes people back to opening the real file in Excel,
which is how a register gets edited by accident.

```bash
uv run python manage.py inspect_legacy_register private-data/register.xlsx --report-dir import-output/inspection
```

See exactly what an import would do, reading the database and writing nothing:

```bash
uv run python manage.py import_legacy_register private-data/register.xlsx --dry-run --report-dir import-output/dry-run
```

Perform it. There is no default mode — neither `--dry-run` nor `--apply` is
assumed, because the safe assumption is the one people stop reading:

```bash
uv run python manage.py import_legacy_register private-data/register.xlsx --apply --report-dir import-output/apply
```

Validate the contracts and regenerate their overview:

```bash
uv run python manage.py check_era_contracts
```

### The current portfolio

Two operations run once, after an import, in this order. Both read the
provenance the import already stored — neither opens a workbook, and neither
touches a source byte. Both default to nothing: `--dry-run` and `--apply` are
separate words for the same reason the importer has no default mode.

Put the register's owners back. The register writes a first name and an account
holds a full one; where exactly one known person carries that name, the owner is
restored, and where two people are named in one cell nobody is assigned:

```bash
uv run python manage.py backfill_legacy_owners --dry-run
```

```bash
uv run python manage.py backfill_legacy_owners --apply --mapping-file private-data/mapping.toml
```

Activate one reviewed register year as current work. The year must be listed in
`REVIEWED_CURRENT_YEARS`; any other year can be analysed and cannot be applied,
because deciding that a year represents current work is the department's call
and not a flag:

```bash
uv run python manage.py promote_current_register --year 2026 --dry-run
```

```bash
uv run python manage.py promote_current_register --year 2026 --apply
```

Both reports are aggregate: counts, classifications and reasons, no matter
titles and no source cells. The distinct owner values nobody could identify are
source content, and are written only on request and only into ignored local
storage:

```bash
uv run python manage.py backfill_legacy_owners --dry-run --unresolved-file import-output/owner-backfill/unresolved.csv
```

Promotion writes no snapshot and rewrites none. The next
`capture_operational_snapshot` run records the new operational state.

## Search

It covers matters, entries, submissions, `Kaasamine`, the historical OneNote
pages and the contents of documents. A search result says which of those it is
and where inside it the match was — `lk 17`, `slaid 3`, `leht "Kulud"` — and
clicking one opens that document rather than merely its matter. Each kind is its
own row rather than text folded into the Matter's, because a child record may be
more restricted than its Matter and a Matter row cannot express that.

A rename is different. Renaming an organisation, editing its aliases, renaming a
tag or a policy area changes the indexed text of every record naming them —
thousands of rows from one small edit — so that is deferred rather than done in
the request. The mutation records a durable obligation in its own transaction,
and a worker discharges it with one atomic full rebuild (ADR 0041). On the
deployment that worker is its own container; locally it is a command:

```bash
uv run python manage.py run_search_refresh_worker --once
```

```bash
uv run python manage.py check_search_freshness
```

Both of the manual routes still work and are always safe:

```bash
uv run python manage.py rebuild_search_index
```

```bash
uv run python manage.py refresh_matter_search 2026_184
```

The projection is derived data and safe to delete: nothing in the domain reads
from it.

It covers matters, entries, submissions and the contents of documents. A search
result says which of those it is and where inside it the match was — `lk 17`,
`slaid 3`, `leht "Kulud"` — and clicking one opens that document rather than
merely its matter.

## Content extraction

Uploading a file does not wait for its contents to be read; a worker does that
afterwards. On the deployment it is its own container, and locally it is a
command:

```bash
uv run python manage.py run_extraction_worker
```

```bash
uv run python manage.py extract_pending_documents --limit 25
```

Everything it produces — extracted text, OCR, thumbnails, the search index — is
derived and rebuildable from the original bytes:

```bash
uv run python manage.py rebuild_document_derivatives --matter 2026_184
```

OCR needs Tesseract with Estonian and English language data. The image ships
both; this says whether the runtime you are on actually has them, because an
engine missing a language falls back to English and returns confident nonsense:

```bash
uv run python manage.py check_ocr_runtime
```

The evidence itself is the half PostgreSQL cannot check on its own — the bytes
live outside it, so "this row points at an object that is not there" is
invisible to every constraint in the schema. This asks, read-only, and exits
non-zero if anything is wrong:

```bash
uv run python manage.py check_evidence_integrity
```

Add `--verify-sha` to read every stored object and check it against its recorded
checksum. That is a maintenance window on a large store rather than a health
check, which is why it is never implied.

## Statistics

`Statistika` is its own destination — on the bar at generous widths and under
«Veel» below that — and six tabs: Ülevaade, Teemad, Tegevus, Ajalugu,
Andmekvaliteet and Definitsioonid. It answers what the corpus says about Koda's
work, which is a different question from Osakond's "what needs attention now" —
and keeping them apart is why it has a destination of its own rather than
another panel on the department page.

Three rules run through it, and they are the reason to trust a number on it.

**Every metric has a versioned definition in code.** `app/reporting/
metric_catalogue.py` is the reviewable artefact: population, time basis,
eligibility, exclusions, earliest reliable period, source-era limitations,
thresholds and coverage. Every card carries a *Kuidas arvutatakse?* panel built
from that entry, and `/statistika/definitsioonid/` renders the whole catalogue.
There is no screen for editing a definition.

**Authorization precedes aggregation.** Every count starts from the authorized
population and is filtered, grouped and exported only after that. A restricted
Matter contributes nothing to a total, a bar, a coverage denominator or a CSV
row. Counting first and hiding rows at render time leaves the hidden rows inside
the numbers, and nothing on screen looks wrong.

**Every number opens exactly what it counted**, and where an honest link is
impossible there is no link. The test suite follows each link through the real
view and compares the count the page reports with the number the card claimed.

Trends are short, and the pages say so. Structured `Submission` records begin
with this system; a year before that has no measurement, and a missing
measurement is not a zero.

The one table that is not answered from canonical records:

```bash
uv run python manage.py capture_operational_snapshot
```

It photographs the open FULL portfolio once, for one day, and is idempotent per
date. It wants a daily run in production — `0 3 * * *` — installed as a
deployment step. There is deliberately no backfill: pointing it at last March
would write today's portfolio under March's date, which is today's picture with
a false caption.

See [ADR 0017](docs/adr/0017-statistics-and-the-metric-catalogue.md).

## Tests and checks

Everything below is part of what CI runs, and it is the part that needs no
browser. The test suite needs a PostgreSQL 18 database; set `POSTGRES_*` or
`DATABASE_URL` first.

**It is not the whole of what CI runs, and a green run here is not a green
build.** `pyproject.toml` sets `testpaths = ["tests"]`, so a bare `pytest` does
not touch `e2e/` at all — 32 browser files and 35 visual baselines. CI also
shards the suite five ways and the browser suite six, and adds the jobs a single
machine cannot usefully repeat: visual regression, the compose smoke test, the
backup-and-restore rehearsal, migration safety, `check_era_contracts`,
`check_search_capabilities`, `check_ocr_runtime`, `manage.py check --deploy`,
shellcheck and `assert_shard_completeness.py`.
[`docs/ci-architecture.md`](docs/ci-architecture.md) says what each one proves.
CI remains the only full verifier.

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

### Where the suite may run

`uv run pytest` from the repository root is the canonical command, and it is
canonical in a load-bearing sense rather than a stylistic one. `pyproject.toml`
passes `--ds=config.test_settings`, which is the only form pytest-django
resolves ahead of a `DJANGO_SETTINGS_MODULE` inherited from the environment —
and the production image bakes that variable in as `config.settings`.

The suite refuses to start in an environment that belongs to a deployment. It
stops, with an explanation, if `REAL_DATA_ALLOWED` is on, if `JURISTID_RUNTIME`
is set, or if any writable storage root points at a deployment's data. So:

- run it in CI, in a local development checkout, or in a Compose project made
  for testing;
- never through a deployed stack. `docker compose -p juristid-main run … pytest`
  hands the process that stack's environment *and* its evidence bind mount. On
  2026-08-24 a run like that wrote 63 synthetic test files into the Chamber's
  real evidence store — the database was isolated and dropped afterwards, the
  filesystem was not.

`config/test_safety.py` holds the refusals and explains each one;
`tests/test_test_isolation.py` is the regression suite for them.

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
  submissions/             the canonical outbound Submission and its recipients
  reporting/               metric catalogue, selectors, Statistika views, snapshots
docs/
  master-specification.md  authoritative specification
  adr/                     architecture decision records
  data-contracts/          export contract, per-era Excel contracts, snapshot manifest
  metric-catalog/          the rules metric definitions obey (definitions live in code)
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
light theme needs different values rather than different components. `base.css`,
`app.css` and `ux.css` are the layers over it, and they consume semantic tokens
only.

**The values are CVI-mapped, not placeholders.** Chamber blue `#009FDA` and the
graphite-derived dark ramp come from the supplied Koda CVI usage, and the ramp is
the one already validated for colour-blind legibility. The typeface is FF DIN Pro
with Barlow as the approved visual fallback until the web licence lands. The
architecture is unchanged from Stage 0
([ADR 0009](docs/adr/0009-design-token-foundation.md)). The running application
shows the whole set at `/disainisusteem/`.

## Secrets

`.env` and every `.env.*` file except `.env.example` are gitignored. No secret
belongs in Git, in the container image or in a client bundle. In a non-debug
process, a missing `DJANGO_SECRET_KEY` is a startup error and the development
key is rejected by a system check.
