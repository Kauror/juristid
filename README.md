# Juristid — Koda Õigusloome

Internal legislative matter and advocacy management system for Eesti Kaubandus-Tööstuskoda.

## Authoritative product specification

The authoritative product and architecture source is:

- `docs/master-specification.md`

Implementation must not silently contradict or expand that specification. Material departures require an ADR and explicit approval.

## Delivery approach

Development proceeds in bounded stages. Do not build later-stage functionality early merely because it is described in the master specification.

1. Stage 0 — decisions, source snapshots, architecture skeleton and CI
2. Stage 1 — core vertical slice
3. Stage 2 — evidence, search and thin consultation
4. Stage 2.5 — secure real-data pilot gate
5. Stage 3 — lawyer pilot
6. Stage 4 — reporting, management operations and migration tooling
7. Stage 5 — production hardening
8. Stage 6 — cutover
9. Post-cutover Phase 2 — advocacy depth
10. Later Phase 3 — historical backfill, advanced integrations and AI

## Current status

Pre-implementation. Start with `prompts/stage-0-coding-agent.md`.
