# 0237 — The nightly cycle leaves a durable record per step, and every source says how fresh it is by its own rule

Date: 2026-11-28 · Status: Implemented in the r98 candidate (R98-09, R98-10, R98-11). **Not active in
production**: needs release 98 and migration `core/0004`. Nothing here adds a request to any source, a
scheduled job or a poller.

Builds on 0229 (one coordinated cycle), 0231 (bounded steps) and 0224 (Riigikogu stages). Replaces nothing.

## Context

`nightly_outcome.classify` is the one definition of what each step of the night did. Its verdict was kept
only as the *last* verdict per step, in the database cache for 14 days: there was no history, and nothing
said that a step had **no record** on a given night. Source freshness was spread over four pages, each with
its own words, and no page said, for a source, «attempted», «succeeded», «newest seen», «newest stored» and
«waiting» side by side.

## Decisions

1. **`NightlyStepRecord`** (`core/0004`): one row per `(cycle_start, step)`, written by `remember()` from
   the same `StepOutcome` the classifier returned (counts and codes only, never a title). The cycle script
   already passes `--since` (the cycle's start) to every step, so the correlation is the script's own; the
   command now hands it to `remember`. Best effort: a failed write costs history, never the cycle.
   Nothing before this release is back-filled — a night with no row is absent, not green and not failed.
2. **Haldus → Öised tsüklid**: the last thirty nights × five steps. A step with no record on a night that
   has some, once its finish-by time (`nightly_schedule.FINISH_BY`) has passed, reads **«Kirje puudub»**
   and counts as needing attention. AI operations are matched to a night **by time window** (the ledger has
   no cycle id; the page says so). The Riigikogu cycle's own UUID (`StageRun.cycle`) is shown, shortened.
   The host's backup result and the morning monitor's verdict live on the host and are **not** stored by the
   application; the page says that rather than showing a column it cannot fill.
3. **Haldus → Allikate värskus** (`app.core.integration_status`): per source — last attempt, last
   *successful* check, newest publication seen at the source and newest stored (the Government agenda), work
   waiting (unjudged findings, candidates and agenda items; **an unjudged item is not an error**), and the
   source's own cadence. The rules differ on purpose: daily steps are stale after 36 h without a success;
   the Government agenda is weekly and judged by its own check states (`NOT_FOUND`/`NOT_PUBLISHED` are an
   *expected delay*, `INACCESSIBLE`/`ACCESS_REFUSED`/`SOURCE_ERROR`/`PARSE_ERROR` need attention, a newer
   session seen than stored is a recorded discrepancy); the Riigikogu stages reuse `cycle.health()`; the
   register watch reuses `watch_health()`. A source with no success is **unknown**, not fresh.
4. **No extra polling.** Both pages read ledgers only. An empty valid response versus a changed source
   format is distinguished only where a source already records it (`GovernmentAgendaCheck.state`,
   `discovery_ok`); elsewhere the page states that the publication date is not tracked.

## Limits (not hidden)

* The history starts at deployment. The per-source «was the response parsed / were all records evaluated»
  distinction exists only for the Government agenda and the Riigikogu stages; the register watch and the
  sweep record counts and errors, not parse stages. Adding that instrumentation is a separate, proportionate
  change to each adapter and is not done here.
* No threshold is configurable; the 36 h and 9-day figures are constants in `integration_status.py`.
* The page does not know whether a source was **expected** on a night when its switch was off other than
  through the step's own `SKIPPED_DELIBERATE`.

## Final corrections (28.11.2026)

* **Expected cycles, not only recorded ones.** The history lists every Tallinn midnight from the
  first record to now. A cycle that left nothing is «Tsükkel puudub täielikult»; one inside its
  window is «Käib»; one whose steps are all deliberately skipped is «Tahtlikult vahele jäetud»;
  otherwise «Lõpetatud», «Osaline» or «Ebaõnnestunud» by `nightly_outcome`'s own outcomes. A night
  before the first record is never reconstructed; before any record the page says so and names the
  next scheduled start.
* **A recovery run never replaces the scheduled verdict.** `NightlyStepRecord` is unique on
  (cycle start, step, manual); a person's run is its own row, shown beside the night.
* **AI operations per night are an approximation** (a time window; the ledger has no cycle id) and
  the page says so.
* **«Completed» is not «fully successful».** `last_success` of the register watch and the new-draft
  sweep is the last run that finished with **no** source error; the last finished run, errors or
  not, is `last_completed`. A latest run that finished with read errors is «osaline» (attention),
  never fresh. Per-source coverage is recorded only as the run's error list and target count; the
  page says so. The Government agenda keeps its r95 discovery rules unchanged.

