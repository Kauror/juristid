# 0115 — CI proves what it says it proves

**Status:** accepted
**Date:** 2026-09-25

## Context

Four findings, each a way for CI to be green over less than it claimed.

* **ENG-051 — skips counted as passes.** 24 browser tests skipped on every run:
  * 22 scenarios of a feature switched off by ADR 0088;
  * one test of a control that ADR 0074 §20 removed;
  * one test waiting for a seed shape that never existed.

  A missing `E2E_BASE_URL` or `E2E_GATE_*` would have skipped the whole browser
  gate and reported it green.
* **ENG-052 — data migrations never ran over data.** The `postgres-safety`
  step-back reversed and re-applied RunPython migrations on an empty schema.
* **ENG-053 — `pyproject.toml` could disagree with `uv.lock`.** Every install
  and export used `--frozen`, which trusts `uv.lock` without comparing it with
  `pyproject.toml`. A raised floor could be ignored by CI, the image and
  pip-audit alike.
* **ENG-110 — the completeness proof used a copy of the arguments.** It
  collected with hand-copied pytest arguments. An `--ignore` added to the
  workflow left it green over 7% less of the suite.

## Decision

**Skips are allow-listed in CI** (`ci_skip_policy.py`, enforced from the root
`conftest.py`).
* **The rule:** in GitHub Actions, or with
  `JURISTID_ENFORCE_EXPECTED_SKIPS=1`, any skip whose test and stated reason no
  entry names fails the run and is printed. Local runs skip freely.
* **The list:**
  * **The document-reading scenarios.** An optional capability, switched off in
    every deployment. Its server side is covered by pytest with the flag on;
    the browser scenarios are what a reinstatement runs.
  * **One Windows-only platform skip.**

  Each entry carries its reason.
* **Missing E2E variables** fail the fixture in CI instead of skipping.
* **The removed control's test** is retired. Its absence is asserted elsewhere,
  and its route is exercised in pytest.
* **The ownerless-area test** no longer depends on the seed. It asserts in the
  browser what holds for any seed, and the row links are asserted on the
  server-rendered page with a world that has one. The seed was not changed:
  seeding an ownerless area moves three visual baselines that already carry
  unrelated drift (ENG-037), and refreshing them would hide that drift.

**Data migrations run over rows.**
* **Seeded step-back.** The step-back now starts from a seeded leaf, so every
  RunPython migration inside its range reverses and re-applies over data.
* **The rest are declared.** Those the range cannot reach have a named populated
  test. `workflow.0004` sits behind `matters.0008`, whose reverse deliberately
  refuses a Matter with two senders.
* **A guard holds the list complete.** It compares the migrations on disk with
  the workflow's targets and the declared proofs, so a new data migration
  without a populated proof is a red build.

**The lockfile must agree.** The quality job runs `uv lock --check`. It fails
the build and tells the developer to re-lock; nothing re-locks in CI.

**The completeness proof reads the workflow** (`ci_workflow_selection.py`).
* **Where the arguments come from:** each suite's selecting arguments are parsed
  from its own `run:` line.
* **What is dropped and what fails closed:** the known output and shard flags
  are dropped, and any unknown flag stops the parser.
* **What the selection is compared with:** the union of the jobs' selections
  must equal the unfiltered universe (`pytest`, and `pytest e2e`). An `--ignore`,
  `-k`, `-m`, `--deselect` or path change is therefore a missing-collection
  failure.
* **Shared definition:** `report_shard_health.py` uses the same definition.

## Consequences

* **Failures land where the cause is.** A new skip, a new data migration, a
  dependency edit or a narrowed test command now fails CI where it is
  introduced, rather than going green.
* **Costs.** The migration job gains one `seed_e2e_data` run (about 20 s). The
  quality job gains a lock check (milliseconds) and three more collections in
  the completeness proof.
