# ADR 0153 — PostgreSQL JIT is off for Juristid

- **Status:** accepted
- **Date:** 2026-10-10
- **Context:** codebase audit finding AUD-039; the product owner's decision D-1
  of 2026-10-10 («`jit=off` for the Juristid database/workload»).
- **Complements:** the register's conditional `DISTINCT` (AUD-001), which
  removes the statement that crossed the threshold first.

## Context

The production database runs the official `postgres:18` image. Its default
configuration has `jit = on` and `jit_above_cost = 100000`. PostgreSQL decides
whether to JIT-compile a statement from the planner's *estimated* cost, not from
how long it will run.

The audit measured this in the production image, offline, on 2,509 synthetic
Matters:

| Statement | With JIT | Same rows, JIT not triggered | Time spent compiling |
| --- | ---: | ---: | ---: |
| Administrator register | 411 ms | 13.5 ms | 342 ms |
| Specialist register, `olek=koik` | 233 ms | 18 ms | 154 ms |
| Specialist register, activity sort | 331 ms | 44 ms | 230 ms |

Two of the administrator's Osakond statements also crossed the threshold. Plan
costs grow with the number of open Matters. Pages would therefore slow down in
steps rather than gradually, and administrators would see it first. This product
runs short OLTP statements and no long analytical scans, which are the only
statements JIT pays off on.

## Decision

1. **JIT is off for the Juristid workload.** It is set in two places, and
   each covers the gap the other leaves.
   - **Each Compose database service** (`deploy/unraid-main`, `deploy/unraid-test`,
     both `deploy/recovery-rehearsal` files, the development `docker-compose.yml`)
     starts PostgreSQL with `command: ["postgres", "-c", "jit=off"]`. This is the
     server default. It holds for a database restored from a dump, because
     `pg_dump` does not carry `ALTER DATABASE` settings.
   - **The migration `core/0002_jit_off_for_this_database`** runs
     `ALTER DATABASE <current database> SET jit = off`. Every environment that
     migrates gets the setting, including CI's service containers, which cannot
     be given a server command line, and every test database. It is reversible
     with `RESET`.
2. **The setting is checked.** `deployment_readiness` reports whether the
   connection's `SHOW jit` is `off` and refuses readiness when it is not.
   The release smoke runs that command. A test asserts `SHOW jit` is `off` on
   the test database.
3. **Not a one-time manual `ALTER`.** Nothing depends on an operator having
   typed a statement on the host.

## Consequences

- The measured 150–350 ms of compilation is gone from every affected
  statement. Latency no longer jumps by that amount when a plan's cost crosses
  the threshold.
- **Production applies it in two steps, neither of them now.** The migration
  runs at the next release's `migrate`, and new connections then answer
  `jit = off`. No restart is needed. The Compose `command:` changes the `db`
  service definition, which takes effect only when that container is
  deliberately recreated. Releases use `up -d --no-deps web intake-reader
  searchindex` and never move `db`. The running `db` container already differs
  from `compose.yml` in its logging since ENG-071. Recreating it is a separate,
  announced maintenance step for the owner.
- If analytical statements ever appear, JIT can be enabled for those sessions
  with `SET jit = on`. Turning it back on for the whole database is a `RESET`
  and a change to this ADR.
- The local Windows PostgreSQL build has no JIT (`pg_jit_available() = false`).
  The setting is still applied there, and has no effect.
