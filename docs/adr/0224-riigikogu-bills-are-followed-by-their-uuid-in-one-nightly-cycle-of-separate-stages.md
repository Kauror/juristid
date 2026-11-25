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

## Consequences

- A Teema that never names its bill can follow it, and a renamed bill stays followed.
- The register watch's Riigikogu reads are spaced by the shared ledger as well: a little
  slower, and no longer able to burst against the cycle.
- New tables in a new app (`riigikogu`, migration 0001); `register_watch` 0009 adds the
  tie's who/when and the notices' invitation counts; `intelligence` 0006 adds three
  date kinds (ADR 0225). All additive.
