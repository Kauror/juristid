# Juristid — Koda Õigusloome

Internal legislative matter and advocacy management system for Eesti
Kaubandus-Tööstuskoda.

> **No real Koda, member or otherwise confidential data may be used in this
> repository, in local development, in tests or in prompts.** Real departmental
> data may only enter an environment that has passed the
> [Secure Pilot Gate](docs/secure-pilot-gate.md). Local development uses
> synthetic data only.

## Current stage

**Stage 0 — complete, merged.** Application skeleton, foundational schema, the
central authorization boundary, the design-token foundation, the architecture
decisions and the CI pipeline that keeps them honest.

**Stage 1 — complete, merged.** The core lawyer workflow, and there is a real
production UI: Minu töö, Saabunud, Teemad, the Matter page with its three tabs,
the unified Sissekanne composer, `Järgmiseks` next actions, Submissions with
immutable final evidence, and global search — all in the Koda CVI dark-mode
interface.

**Stage 2A — complete, merged.** The legacy register import (per-era workbook
contracts, offline inspection, dry run and apply, immutable provenance) and the
rebuildable `SearchDocument` projection behind Estonian full-text search.

**Stage 2A.5 — complete, merged.** The rehearsal's operational foundation:
`Ülevaade`, the ministries as public reference data, `Saabunud` as a multi-file
intake surface, and the corrections a week of real browser use produced.

**Stage 2B — complete, merged.** Content extraction and everything that depends on it:
`DocumentDerivative` and locator-aware text fragments, parsers for PDF, DOCX,
XLSX, PPTX, TXT, CSV, EML, MSG and images, local OCR for scanned pages, a
database-backed extraction worker, email attachments captured as evidence with
provenance back to the exact message, safe previews and thumbnails, and search
extended to entries, submissions and document contents with child authorization
evaluated live.

**Stage 2D — in review.** Historical corpus integration: the OneNote desktop
archive as first-class history beside the register. `LegacySourcePage` and its
resources, a case-file rendering that keeps each file inside the narrative that
introduces it, an operator queue for the matches a person still has to settle,
and Cloudflare Access as the production authenticator. See
[ADR 0015](docs/adr/0015-historical-corpus-integration.md).

**Stage 2E — implemented on a feature branch, pending integration.** Statistics,
reporting and data quality: a code-defined, versioned metric catalogue, a
Statistika workspace of five tabs, coverage on every number, drill-through from
every chart segment, CSV exports and a daily operational snapshot. Nothing here
is deployed yet. See
[ADR 0017](docs/adr/0017-statistics-and-the-metric-catalogue.md).

**Synthetic rehearsal — deployed.** An instance on the Unraid host running
invented data only, for the class of defect CI structurally cannot reach. See
[`deploy/unraid-test/`](deploy/unraid-test/).

**Real-data instance — deployed.** A separate stack with no host port at all,
reachable only through a Cloudflare tunnel and only behind an authenticator: the
shared gate today, Cloudflare Access as the next hardening step
([ADR 0016](docs/adr/0016-authentication-modes-and-the-shared-gate.md)). How it
is deployed is [`deploy/unraid-main/README.md`](deploy/unraid-main/README.md);
how it is backed up and restored is
[`deploy/unraid-main/RECOVERY.md`](deploy/unraid-main/RECOVERY.md) and
[ADR 0022](docs/adr/0022-deployment-backup-and-recovery.md).

**No real data in this repository.** The repository is public. Only code and
generated synthetic fixtures enter Git: every fixture the tests read is built at
test time from code you can read, so a checked-in file could only be a real one
that arrived by accident.

**Deliberately unbuilt.** Stage 2C: Kaasamine and structured member responses.
Later still: EIS and Riigikogu integration, advocacy outcomes and attribution,
and anything involving embeddings, semantic search or AI summarisation. Each stage is authorized by its own written brief; nothing is
built ahead of one.

[`docs/open-decisions.md`](docs/open-decisions.md) lists what still belongs to
named people rather than to the code.

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

Search reads a rebuildable projection. Ordinary writes keep it current by
themselves; a rebuild is needed after bulk changes such as renaming an
organisation or editing its aliases.

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

`Statistika` is the fifth main-navigation item and five tabs: Üldpilt, Teemad,
Koja tegevus, Ajalooline materjal and Andmekvaliteet. It answers what the corpus
says about Koda's work, which is a different question from Ülevaade's "what
needs attention now" — and keeping them apart is why it has its own destination
rather than another panel on the dashboard.

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
