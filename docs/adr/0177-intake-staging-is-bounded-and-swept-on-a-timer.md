# ADR 0177 — `Uus teema` staging is bounded on the way in and swept on a timer

- Status: Proposed (2026-10-22), security remediation of finding csf_3942255b466a4e213b958b16
- Deciders: the product owner; implemented in the intake-staging workstream
- Amends: 0064 (staging is swept by an operator), 0072 (the intake reader is the
  only queue consumer the stack deploys — it stays the only *queue* consumer)

## Context

A file chosen on `Uus teema` is staged on the pending-upload volume until
`Loo teema` promotes it (0064). The twenty-file cap applied per form, and a form
is one render (`open_session`, ENG-074), so one signed-in person could stage
twenty files of up to `MAX_EVIDENCE_UPLOAD_BYTES` into as many fresh forms as
they liked. What bounded the volume after that was expiry plus
`prune_intake_staging`, which nothing scheduled. On the production host the
volume lives in Docker's own storage, shared by every container.

## Decision

1. **Quotas before bytes.** `stage_uploads` takes one transaction-scoped
   advisory lock (`24604, 1`), then the session row, counts what staging holds
   and only then writes. Limits, from the environment with defaults:
   active forms per person (20) and in total (200) — a form is *active* when it
   is unconsumed, unexpired and holds a file, so an empty render costs nothing;
   staged bytes per person (2 GiB) and in total (10 GiB), counting every file of
   an unconsumed form, removed and expired ones included, because their bytes
   stay until the sweep; and free space left on the volume after the write
   (1 GiB, `shutil.disk_usage`). The rows the transaction inserts are the
   reservation; a failed write rolls them back and discards its bytes, including
   a partial object. The upload view asks the same question first, unlocked,
   with the declared sizes, so an over-quota request is answered before its
   files are read. A refusal is a sentence on the form, like the file cap; the
   three deployment-wide causes share one sentence and are logged for the
   operator.
   **Held uploads too.** A refused `Uus teema` post holds its files at the
   root of the same volume (`app.documents.pending`), up to 25 per gate
   session, and a fresh session can do it again. `hold` takes the same lock and
   refuses — holding nothing, with «Valitud faile ei saanud vormi juurde hoida…»
   among the form's messages — when the held bytes, measured on disk, would pass
   `PENDING_UPLOAD_MAX_HELD_BYTES` (2 GiB) or the volume would fall below the
   same headroom; a write that fails part-way removes what it wrote. A ceiling
   of its own rather than a share of the staging one, because held uploads have
   no rows: counting them into the row-based staging quota would put a
   directory scan on every staged upload for no stronger bound.
2. **A scheduled, bounded sweep.** A fourth release-image service,
   `intake-sweeper`, runs `prune_intake_staging --loop`: every 900 s, at most
   500 sessions and 5 GiB per run, each session locked (`SKIP LOCKED`),
   re-checked as stale and deleted in its own transaction. Only expired or
   consumed sessions are eligible. Each turn also deletes held uploads past
   `PENDING_UPLOAD_GRACE_HOURS` (at most 1000, regular files at the root only),
   which before were swept only when somebody held again. It mounts the staging volume read-write and
   nothing else; it is a container, not a host cron entry, for the reason 0041
   gives. Its healthcheck, `check_intake_staging`, measures convergence: red
   when a stale session has waited over 3600 s, when staging is at its byte
   ceiling, or when the volume is below its headroom.
3. **The sweep refuses rather than follows.** A row naming a key outside its
   own `intake/<session>/` prefix, an `intake` or session directory that is a
   symbolic link or resolves elsewhere, and an entry that is a link or not a
   regular file each leave the whole session untouched and are reported; a
   single run exits 1 when it refused anything.

## Alternatives

- **A turn of the intake reader's loop.** It already clears expired text, but it
  mounts staging read-only on purpose (0072); giving it write access to the
  files it parses is the wrong trade.
- **A host cron entry** (`scripts/deploy`). This host's cron is generated from
  the flash drive (0022, 0041); every recurring job here is a Compose service.
- **A per-owner advisory lock plus a global one.** The global lock alone already
  serialises every check-and-write; a second lock would add an ordering rule and
  nothing else at this stack's scale.

## Consequences

- A release names four application services where it named three; every runbook
  line that holds or starts the writers says `intake-sweeper` too.
- Staging is serialised deployment-wide for the length of one upload's write.
  At a few uploads a minute this is invisible; it is the price of a quota that
  two requests cannot both pass.
- Held uploads (`app.documents.pending`) share the volume and are not counted in
  the byte quotas; they are swept by age on the next hold, and the headroom check
  covers both.
- Reversible: the limits are settings, and stopping the service returns to the
  manual command.

## Status update 2026-10-29 — merged and deployed

This section records what happened after the decision; it does not amend the decision, and the status is unchanged (no formal acceptance was recorded).

- **Merged** in the security remediation integration (merge `9a2e834a, #514 = #506–#513, 2026-10-22`; #512 added the `intake-sweeper` service).
- **Deployed** to production as `1270ca01` (2026-10-23, the release of #521, which carried the security baseline merged before it).
- **Activated**: the `intake-sweeper` service is defined in `deploy/unraid-main/compose.yml`; it is recorded among the application services moved by release r24 (`216d71ae`, 2026-10-24) at the latest.
