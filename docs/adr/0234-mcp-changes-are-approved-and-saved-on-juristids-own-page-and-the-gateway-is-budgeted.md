# 0234 — MCP changes are approved and saved on Juristid's own page; the gateway is budgeted and cannot write business data

Date: 2026-11-26 · Status: Implemented in the r96 candidate (security round of 2026-11-26, Package E: SEC-009
and SEC-014; built and tested, with real database roles). **Not active in production**: the
migration `mcp_writes/0002` and the new code arrive with release 96; the gateway role loses its
business-table writes only when the operator runs `mcp_writer_db_role --apply` and verifies it
before the gateway restarts; the ChatGPT connector keeps showing the old tool descriptions until
a workspace admin refreshes and publishes it. SEC-009/014 residual risks are listed below)

Amends: 0189 §1 («Commit … executes the approved proposal»), §2 (the writer role's
allow-list) and §5 (who writes the business rows). Unchanged: 0189 §3 (the write scope),
§4 (the operations), §6 (off until switched on); 0185 (reading).

Numbering: 0230–0233 are left for the parallel packages A–D of the same round; the
integration owner renumbers if they land differently.

## Context

The external scan of `e49a0683` (r94) found, and the owner's brief confirmed:

1. **SEC-009.** The approval page showed the *stored* preview the write gateway wrote,
   separately from the canonical arguments it later executed; the approval digest
   covered the operation, the canonical arguments, the user and the grant but not the
   preview, the target, the client, the token family, the state or the expiry. And the
   gateway's role, `juristid_mcp_writer`, could UPDATE proposals and INSERT/UPDATE the
   business tables the approved operations write (0189 §2). The gateway is reachable,
   through `mcp`, from the AI client's side: a compromise of that process could have
   rewritten what a person was shown, or written business rows without any approval.
2. **SEC-014.** Each call was bounded (body size, nesting, two worker threads, a call
   timeout), but nothing bounded *many* calls: proposals, refusals and events could grow
   without limit, and every repeated refusal appended one more append-only row.

A longer digest would not have fixed (1): whoever can write the rows can recompute an
unkeyed digest, and whoever can write business tables needs no approval at all.

## Decision

### 1. The approval page is the trusted executor

A change is **saved by Juristid's approval page on `web`, in the same transaction as the
person's approval**, never by the gateway.

- **Derived, not stored.** The proposal now keeps the client's validated `arguments`.
  On every view and every POST the page runs the operation's own `prepare` again from
  them (a rolled-back savepoint, the person's own visibility) and shows *that* preview.
  Stored canonical arguments or target that differ from what the arguments resolve to
  now are a `mismatch` (the proposal FAILS, `approval_mismatch`); a different state is
  `stale`; a refusal from `prepare` now is `stale`. The stored `preview` is never shown.
- **Bound to what was shown.** The page puts the binding digest — SHA-256 over the
  format, proposal id, operation, arguments, canonical arguments, target Matter and
  record, user, grant, client, token family, state, preview and the proposal's expiry
  (`app.mcp_writes.review.binding_digest`) — in its form. «Kinnita» is accepted only
  with the same digest; a different one (anything changed between display and
  submission) answers 409 with the new preview and spends no code.
- **Checked again under the locks.** After the personal code (unchanged: the grant's
  authenticator, the shared lock ladder and throttles, `MCP_WRITE_REVERIFY_SECONDS`),
  one transaction locks the proposal row, takes the operation's canonical locks, checks
  pending/expiry/write right/live connection, derives everything again and compares the
  binding once more; only then it writes the `McpWriteApproval` (format 2, with the
  binding digest and copies of the canonical arguments, state and preview approved),
  runs the canonical service in a savepoint under the MCP audit provenance (the person
  as principal, the proposal's correlation id, «ChatGPT kaudu, isiklikult kinnitatud»),
  and marks the proposal EXECUTED with its receipt. A domain refusal leaves the approval,
  a FAILED proposal and its event; an unexpected error rolls the approval back and the
  proposal stays PENDING.
- **Bound to the connection.** A proposal whose token family no longer has an unexpired,
  unrevoked token with the write scope under the grant's current write decision
  (`access.family_live`) — a disconnect, a reused refresh token, `revoke-write`, an
  epoch bump — offers no approval.
- **No approval window to commit.** Approval and execution are one step, so the r94
  «approval waits for its commit» window and «Tühista kinnitus» no longer exist; an
  approval's `expires_at` equals its `approved_at`.

`commit_prepared_action` keeps its name, input schema, annotations and answer shapes, but
its role is now the **receipt**, not the commit: it neither authorises nor executes
anything. It reports the stored receipt (`executed`, `replayed` false the first time and
true afterwards), `awaiting_approval` with the link, or — for a declined, expired, failed
or stale proposal — the refusal code (`declined`, `expired`, `already_failed`, `stale`),
always saying nothing was saved. A client following the r94 descriptions still behaves
correctly (it calls the tool after the person approved, and gets the receipt).

Every description of the workflow was updated to say so (r96 final): the tool titles
and status lines («Get the receipt of a prepared change»), the prepare paragraph, the
receipt tool's description, the prepared / awaiting instructions and the server
instructions in `app/mcp_server`, the gateway's refusal texts, `schemas.py` field
descriptions, the approval page (shorter; it states that «Kinnita» saves the change at
once) and `docs/mcp/*`. A connector that already holds the r94 descriptions keeps
showing them until a workspace admin refreshes and publishes it (`OPERATIONS.md`,
«Updating the tools and refreshing the connector»); the repository text does not update
that snapshot.

### 2. The gateway's role cannot write business data or rewrite a proposal

`juristid_mcp_writer` keeps SELECT (minus `django_session`) and gets INSERT only on
`mcp_writes_mcpwriteproposal`, `mcp_writes_mcpwriteevent` and `audit_securityauditevent`,
and INSERT/UPDATE on its counter table — nothing else (`WRITE_ALLOWLIST`). The thirteen
business tables it could write until r94 are now on the never-writable list
(`FORMER_BUSINESS_WRITES`), so juristid.E050 refuses to start a gateway whose role still
holds them, and `mcp_writer_db_role --apply` revokes them.

Two triggers enforce the proposal's integrity for **every** role, the application's
included: a proposal is inserted PENDING with no result; afterwards what it proposes
(identity, format, user, grant, client, token family, operation, arguments, canonical
arguments, digest, target, state, preview, expiry, correlation id, creation time) never
changes, its status only moves forward, only EXECUTED carries a result, and it is never
deleted. Approvals and events stay append-only (0189).

So a compromised gateway can still *prepare* proposals for the people whose tokens pass
through it — which its person then sees, derived by trusted code, and must approve with
their own authenticator — and can no longer change an approved or pending proposal,
forge an approval, or write a business row.

### 3. Versioned formats

`format_version` on proposals and approvals: 1 = r94, 2 = this decision. Migration
`mcp_writes/0002` retires every outstanding format-1 proposal (PENDING or APPROVED) as
EXPIRED, `failure_code = format_retired`, with one `FORMAT_RETIRED` event; executed
proposals, receipts, approvals and events are untouched. Format-2 code never approves,
executes or reinterprets a format-1 proposal, including one a rolled-back release wrote
later (the column default is 1).

### 4. Budgets

Every prepare and commit call is counted, in fixed 10-minute and one-day windows, for
each stable principal the call has — the user, the grant, the token family and the
client (a SHA-256 prefix of its id) — and for the deployment, with one atomic
`INSERT … ON CONFLICT DO UPDATE … RETURNING` per window. **The stored-bytes allowance
is claimed, not read and added:** the deployment-wide day counter is taken with one
conditional statement (`INSERT … ON CONFLICT DO UPDATE … WHERE count + n <= limit
RETURNING`, `budgets.claim`), inside the prepare's own transaction and after the user
lock, so two users near the limit queue on the counter row and the second re-evaluates
the first's committed total. A refusal claims nothing; a rolled-back preparation
releases its claim. Lock order, always: the user's advisory lock, then the global
counter row (the per-user counter follows under the user lock); nothing takes them the
other way round, so there is no deadlock. (Before the r96 final round the check read the
counter and the add happened separately, so overlapping preparations by different
users could pass the same check and exceed the allowance; `tests/test_mcp_write_global_quota_race.py`.) A refresh keeps user, grant,
client and family; a re-authorization keeps user, grant and client. Over a limit the
gateway answers `rate_limited` (HTTP 429) with `retry_after_seconds`, the budget and the
principal *kind*, and says nothing was saved.

| Budget | Limit | Why |
| --- | --- | --- |
| prepare, per principal | 30 / 10 min, 300 / day | A person's session prepares a handful of changes; a model may retry invalid arguments two or three times. 30 is ten such changes with retries in ten minutes. |
| prepare, deployment | 150 / 10 min, 1 500 / day | Five people at full speed; the department is about twenty. |
| commit, per principal | 60 / 10 min, 600 / day | A model polls `awaiting_approval` while the person opens the page; five polls for each of twelve proposals. |
| commit, deployment | 300 / 10 min | Five people at full speed. |
| refused calls, per user | 30 / 10 min, then every call waits for the window | A loop of invalid or unknown requests stops itself; `awaiting_approval`, replays and `rate_limited` itself do not count. |
| outstanding proposals, per user | 25 | More than 25 unapproved changes at once is not a review anyone does; they expire in 15 minutes anyway. |
| stored bytes, per user / deployment | 8 MiB / 64 MiB per day | ~120 maximal (64 KiB) requests with their derived JSON per person per day; bounds the table's growth. |
| identical refusal events | 5 per 10 min, then one marked `aggregated`, then counted only | The trail keeps every kind of refusal, its first occurrences in full and the exact total in the counter; it no longer grows with a loop. |

Never budgeted: the approval page (the save happens there, behind the personal code and
its own throttles), and a commit call that finds the caller's own saved proposal — the
receipt of a saved change is always available. Every value is a setting
(`MCP_WRITE_*`, 0 switches one off). Haldus → Süsteemi seis shows counts only (waiting,
prepared, refused, rate-limited, counted-only), and `manage.py mcp_writes budgets` the
counters by principal key (ids and client hash prefixes, never a token or a client URL).

## Alternatives considered

- **A separate executor service** (`mcp-executor`, its own role with the business
  allow-list, called by the gateway at commit). Keeps «approve, then commit» but adds a
  service, a network, a role and keys, and the executor would still have to trust only
  web-written rows — i.e. re-derive like the page does. More moving parts for the same
  boundary.
- **SECURITY DEFINER functions** for the business writes. The canonical services are
  Python (locks, search projection, notices, audit); duplicating them in SQL would fork
  the business rules.
- **A keyed (HMAC) digest only.** Stops a forged digest but not a gateway that can write
  business tables directly — not the boundary the brief asks for.

## Consequences

- Migration `mcp_writes/0002` (additive columns with defaults, one table, two triggers,
  the format-1 retirement). The writer role must be re-applied after the deploy
  (docs/mcp/OPERATIONS.md, «Writer role after r96»); until it is, the old role keeps
  its business grants but the new code never uses them — E050 refuses to start the
  gateway, which is the reminder.
- `mcp-writer` needs no new secret, service or network.
- User-visible: «Kinnita» now saves at once; the «Tühista kinnitus» button is gone; the
  approval page says a change was saved, not that ChatGPT will save it.
- Rollback to r94 against this schema: possible (r94 writes rows with the column
  defaults, the triggers allow its transitions) after re-applying r94's role; format-2
  proposals still pending then are approvable under r94's rules — expire them first
  (`OPERATIONS.md`).

## Remaining gaps (stated, not resolved here)

- `web` is the executor and holds the application's full role, as it does for every
  browser write; the boundary is the personal code on Juristid's own page, not a
  narrower database role for `web`.
- The gateway can still read every business table except sessions (it must, to resolve
  and preview), and can still *insert* proposals and events for tokens it holds; that is
  bounded by the budgets, not prevented.
- The shared gate's persona selection stays the owner's accepted residual risk: a person
  who knows the gate password **and** holds a colleague's authenticator can approve as
  that colleague (0189 Consequences).

## Reversibility

Reverting the code and re-running the previous release's (r95, b70608dc) `mcp_writer_db_role --apply` restores its
behaviour (README-r96 holds the exact rollback gates, including expiring format-2 proposals first); the migration reverses (triggers, table and columns dropped; retired proposals
stay EXPIRED, their events are history).
