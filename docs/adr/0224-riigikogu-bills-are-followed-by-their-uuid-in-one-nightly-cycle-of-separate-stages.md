# 0224 — Riigikogu bills are followed by their UUID, read in one nightly cycle of separate stages

Date: 2026-11-24 · Status: proposed (coordinator's brief «Riigikogu legislative intelligence programme», 2026-11-24; development authorisation only — nothing activated, nothing deployed)

## Context

ADR 0217 follows a bill when a Teema's title names it («… (1082 SE)»): on 17.11.2026, 28
of 230 open Teemad did. A Teema that began as a ministry draft, carries a different title,
follows two bills, or had a bill withdrawn and replaced is not followed. The register
watch, the new-drafts sweep (ADR 0218) and the new Riigikogu reading below all call the
same API, which publishes its limits: «Ühelt IP-aadressilt on lubatud kuni 1 päring
sekundis, ühe URLi ehk teekonna kohta kuni 12 päringut minutis» (riigikogu.ee/riigikogu/
avaandmed, read 24.11.2026; licence CC BY-SA 3.0 Estonia). The owner decided on
24.11.2026 that everything new runs **once a night**, in one coordinated cycle, with no
daytime polling.

## Decision

1. **A bill's identity is its Riigikogu UUID** (`riigikogu.Bill.uuid`). Number, type and
   membership find and label a bill; a number restarts with each membership and is never
   the identity alone. A title change updates the title and nothing else.
2. **There is one register of Teema ↔ bill ties: `register_watch.WatchTarget`** with source
   RIIGIKOGU and the UUID as identifier, as ADR 0217 already stores it. A new origin,
   ``LINKED``, marks a tie a person made — on the Teema's «Registritest» tab by number, from
   an invitation, or by linking or adopting a «Uued eelnõud» candidate whose documents
   include the bill (`candidates._tie_bills`). Who tied it and when is kept on the tie
   (`linked_by`, `linked_at`): a monitoring setting, like a lawyer's search words, not a
   fact on the Teema's timeline. A Teema may follow several bills; unlinking keeps the tie
   inactive so nothing is offered twice. Nothing ties a bill by text similarity: a shared
   number or similar title is a *proposal* that names every Teema it could be
   (`bills.proposals`), shown, never applied. Teemad are never merged.
3. **Every call to api.riigikogu.ee takes its turn from one ledger** (`riigikogu.ApiCall`,
   under a PostgreSQL advisory lock): at least 1.2 s between calls and at most 11 calls a
   minute per path — under the published limits, across processes, including the register
   watch's own reads (`sources.riigikogu.get_json` now goes through `riigikogu.client`).
   The fenced door of ADR 0217 §5 is unchanged; a `FetchError` now carries the HTTP status
   and a numeric `Retry-After`. 429 waits for `Retry-After` (else 5, 15, 45 s, at most a
   bounded number of times); a 5xx or a dropped connection is retried twice; any other 4xx
   is the answer. Each stage has a request budget and stops as PARTIAL when it is spent.
4. **Identity and history of what is read.** Every document is one row by its UUID
   (`ParliamentDocument`) whichever endpoint showed it. An unchanged rediscovery updates
   only `last_seen_at`; changed metadata or a changed file records a `DocumentChange`; a
   moved date supersedes the old row (`ExternalDeadline`, state SUPERSEDED) and both stay.
   An empty or malformed answer is an error, never «everything disappeared»
   (`client.rows`); a committee agenda item is marked cancelled only after two *complete*
   readings without it.
5. **One nightly cycle, stages apart** (`manage.py watch_riigikogu`, `riigikogu.cycle`):
   BILLS (amendment deadlines) → INVITATIONS → AGENDAS → PROTOCOLS → STENOGRAMS →
   RESPONSES → ANALYSIS. Each stage has its own `StageRun` — status, window, counts,
   errors, requests, 429s — and a checkpoint: the end of its last SUCCEEDED window. The
   next window starts 7 days before it (3 for stenograms), so a letter registered late is
   still seen; a PARTIAL or FAILED stage never moves the checkpoint. One stage's failure
   is that stage's: the others run, and nothing here touches the Lovable synchronisation.
   ANALYSIS is recorded DISABLED (ADR 0227). **Place in the night.** No schedule of its own: the
   owner decided on 25.11.2026 that one coordinated nightly cycle runs all routine
   synchronisation, monitoring and analysis (ADR 0229). There `watch_riigikogu` runs after
   the register watch, so both do not queue on the shared ledger, and before the new-drafts
   sweep, so an invitation's candidate exists when the sweep reads the Riigikogu's new bills.
   One advisory lock keeps two cycles from overlapping.
6. **Health is per stage and never «zero findings» for a failure** (`cycle.health`):
   never run, disabled, failed, partial, stale (no success in 30 h) and ok are distinct.
   Monitooring → Riigikogu shows a line for any stage that is not ok; the public record
   on these pages names a Teema only through `Matter.objects.visible_to`.
7. **Switches.** `JURISTID_RIIGIKOGU_MONITORING` (off everywhere, production included) and
   the existing `JURISTID_REGISTER_WATCH_FETCH` must both be on for any reading. Turning
   the cycle on, adding its cron line and its first supervised run are owner decisions
   recorded with a release.

## What the switch disables

`RIIGIKOGU_MONITORING` (`JURISTID_RIIGIKOGU_MONITORING`, default off) is the one switch
for every new parliamentary action, read by `riigikogu.bills.switched_on()`; it is
deliberately *not* `cycle.enabled()`, because `REGISTER_WATCH_FETCH` is already on in
production for the register watch and must not switch anything new on with it
(amendment 2026-11-25, coordinator's release review item 6). While it is off:

| Entry point | Network | Writes | Off | On |
|---|---|---|---|---|
| `riigikogu.views.link_bill` («Seo eelnõu», by number) | `bills.find` → api.riigikogu.ee | `Bill`, `WatchTarget` LINKED | 404 before any read or write | as designed |
| `riigikogu.views.unlink_bill` | none | `WatchTarget.active` | 404 | as designed |
| `riigikogu.views.decide` (kuupaev, seo, meeldetuletus, pole_vaja, kinnita, vastus) | none | `Invitation`, `MatterImportantDate` RK_*, `ExternalDeadline`, personal reminder | 404, invitation stays NEW | as designed |
| `riigikogu.views.propose_victory` | none | `MatterWorkVictory` candidate | 404 (also while document AI is off, and for a mocked analysis in production) | as designed |
| `register_watch.candidates._tie_bills` (from `attach`, `adopt_if_new`) | none | `Bill`, `WatchTarget` LINKED — which the existing nightly watch would then read | does nothing; the candidate is attached / adopted exactly as in r92 | ties the candidate's bills |
| Teema «Registritest» Riigikogu section (`register_watch.views.teema_panel` → `selectors.teema`, `_teema.html`) | none | none | not drawn at all: the tab is the register watch's, as in r92 | drawn, with its forms |
| Monitooring → Riigikogu (`riigikogu.views.monitooring`, `selectors.monitooring`) | none | none | read-only: stored public record only (empty before the first night), no decision forms, says the monitoring is not switched on | forms for the deciders |
| Monitooring tab count (`candidate_views._riigikogu_waiting`), notices (`notices._waiting`, `candidate_notices`) | none | none | counts rows only the cycle writes — 0 before activation | counts |
| The nightly cycle (`cycle.run`, ADR 0229 step) | — | `StageRun` DISABLED only | records DISABLED, reads nothing (also needs `REGISTER_WATCH_FETCH`) | reads |

Not disabled, by design: the register watch and the new-drafts sweep (ADR 0217, 0218)
read the Riigikogu exactly as before under `REGISTER_WATCH_FETCH` alone; their calls now
take their turn from the shared ledger (§3), which spaces them and changes no result.

`RIIGIKOGU_DOCUMENT_AI` (ADR 0227) is separate and also off: `analysis.run` raises before
it writes a row, whatever model it is given (a `MockModel` too); every surface reads
stored analyses through `analysis.shown`, which returns none while the switch is off and
never a `mocked` row where `APPLICATION_ENVIRONMENT` is `production`. The demonstration
command (`seed_riigikogu_demo`) refuses to run in production and stores no analysis while
document AI is off. Tests: `tests/test_riigikogu_disabled_boundaries.py` (each boundary
off and on).

Switching either on is an environment change on the host, visible in the deploy kit's
configuration fingerprint like every other `JURISTID_*` switch, and an owner decision
recorded with the release. Rolling back to r92 after a parliamentary date was accepted is
`manage.py riigikogu_rollback_guard` (ADR 0225 §3; procedure in the release worklog
ROLLBACK-AFTER-ACTIVATION.md).

## Consequences

- A Teema that never names its bill can follow it, and a renamed bill stays followed.
- The register watch's Riigikogu reads are spaced by the shared ledger as well: a little
  slower, and no longer able to burst against the cycle.
- New tables in a new app (`riigikogu`, migration 0001); `register_watch` 0009 adds the
  tie's who/when and the notices' invitation counts; `intelligence` 0006 adds three
  date kinds (ADR 0225). All additive.
