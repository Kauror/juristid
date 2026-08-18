# 0001 — Application architecture and supported-version policy

- **Status:** Accepted (Stage 0)
- **Date:** 2026-08-18

## Context

The master specification locks the architectural characteristics (Django modular
monolith, PostgreSQL 18+, server-rendered HTML with HTMX, Docker deployment unit)
but deliberately leaves exact runtime and framework versions to an ADR, because
the product must outlive its 2026 dependency set.

Stage 0 has to pick concrete versions and a tool set that a small team — and AI
coding agents — can maintain for years.

## Decision

**Runtime and framework**

- Python **3.13** (`requires-python = ">=3.13,<3.14"`).
- Django **5.2 LTS** (`>=5.2,<6.0`), supported until April 2028.
- `psycopg` 3 as the database driver.
- `gunicorn` as the WSGI server, `whitenoise` for static assets in the container.

Django 5.2 supports Python 3.14 — support was added during the 5.2 series and is
present in the pinned 5.2.x release. Python 3.13 is therefore a **deliberately
conservative choice, not a compatibility limit**: it is the runtime with the
longest track record across the wider dependency set (psycopg, the lint and test
toolchain, the base container images) at the time Stage 0 was built, and this
product optimises for boring over new.

Move to 3.14 when there is a reason to — a needed language or standard-library
feature, or the point at which 3.13 approaches end of security support. It is a
one-line change to `requires-python` plus a CI run. One such reason already sits
in the codebase: `uuid.uuid7` is in the 3.14 standard library, and
`app.core.ids.uuid7` already delegates to it when the runtime provides it, so
the local implementation disappears on upgrade.

**Upgrade policy**

- Track the Django LTS line. Move to the next LTS within one minor release of
  its availability, never later than the end of extended support of the current
  one.
- Patch releases of Django, Python and dependencies are applied routinely.
- PostgreSQL major upgrades are planned deliberately (see ADR 0002).
- The dependency lock (`uv.lock`) is committed; CI installs from it with
  `--frozen`.

**Module boundaries**

Domain modules live under `app/` and are code boundaries inside one deployment
and one database:

```
core  accounts  organisations  taxonomy  workflow
matters  documents  audit  search  legacy_import
```

`submissions`, `consultations`, `reporting`, `integrations` and `advocacy` are
named in the specification and are created when the stage that needs them
arrives, not before.

All business state changes go through named service functions in
`<module>/services.py`. Views, forms and templates contain no workflow logic.

**Toolchain**

- `uv` for dependency resolution, locking and the virtual environment.
- `ruff` for both linting and formatting (rule sets `E F I UP B C4 DJ S RUF`).
  The `S` set is flake8-bandit, so no separate security linter is added.
- `mypy` with `django-stubs` for static checks.
- `pytest` + `pytest-django` + `factory_boy` for tests.
- `pip-audit` (via `uvx`, over an exported requirements file) as the dependency
  vulnerability baseline.

## Alternatives considered

- **Django 6.0** — newer, but not LTS. A product with a 10–15 year horizon and
  one maintainer benefits more from the LTS cadence.
- **Poetry / pip-tools** — both workable. `uv` gives one tool for the
  interpreter, the lock and the environment, and installs fast enough that CI
  and Docker layers stay simple. `pyproject.toml` remains standard, so moving
  back to pip is not a rewrite.
- **Separate linter + formatter + import sorter** — replaced by ruff alone.

## Consequences

- Version choices are visible in `pyproject.toml` and `uv.lock`, not scattered.
- Adding a runtime dependency is a reviewable change with an explicit reason.
- Anyone reading `app/` sees the specification's domain boundaries.

## Reversibility

High. Framework and toolchain choices are replaceable without touching the
domain model. A stack change (away from Django) is not reversible cheaply and
would require a new ADR against the master specification.
