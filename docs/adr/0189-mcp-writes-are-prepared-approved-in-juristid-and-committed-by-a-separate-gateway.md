# ADR 0189 — MCP writes are prepared, approved in Juristid by their person, and committed by a separate gateway

- Status: Proposed (owner's brief «JURISTID — MCP Phase 2: Secure Write Operations,
  Multi-Agent Delivery and Production Deployment», 2026-10-23)
- Amends: 0185 §1 («no write tool exists») and §2 (the tool list), 0180 (one new
  `auth_method`), 0052 (for MCP only: a person may state the kind of a new next step)
- Related, unchanged: 0005 (authorization chokepoint), 0016 (the shared gate), 0042/0181
  (who may read and write), 0167 (save-once), 0186 (a personal factor behind the shared
  gate)

## Context

Phase 1 (0185) let an individually authorized colleague ask Juristid from ChatGPT. The
owner now wants the same colleague to say «Add a note to 2026_225 that the ministry
promised a revised version in November», check what Juristid will save, confirm it, and
get a link to the saved record — for a bounded set of ordinary, reversible changes.

Three facts constrain it:

1. A model-generated tool call is not a person's decision. ChatGPT may call a tool
   because a retrieved document told it to, and «the user said yes» in a transcript is
   not something Juristid can verify.
2. The `mcp` process is read-only twice over — the SELECT-only role `juristid_mcp` and
   read-only transactions — and that is the security property Phase 1 was accepted on.
   It must survive.
3. The shared gate's persona proves nobody (0016). The grant's personal authenticator
   does (0185 §3).

## Decision

### 1. Two stages, and only a person in Juristid joins them

- **Prepare.** A `prepare_<operation>` tool validates one typed change against the
  canonical rules, resolves its exact target through the caller's own visibility,
  computes current-versus-proposed and the consequences, and stores a **proposal**:
  user-, grant-, client- and token-family-bound, expiring (15 min), with a digest of
  its canonical arguments and a snapshot of the state it was judged against. No business
  record changes.
- **Approve.** The person opens the proposal's link on Juristid itself
  (`/mcp-ligipaas/muudatus/<id>/`, behind the gate, as their own persona), reads the
  preview Juristid stored — not the model's paraphrase — and presses «Kinnita» with a
  current code from **their grant's authenticator** (the same single-use step, lock
  ladder and throttles as `/oauth/authorize`). A code accepted in the same gate session
  spares them typing another for five minutes; every approval is still its own button
  press for its own proposal. The approval is a row only `web` can write.
- **Commit.** `commit_prepared_action` executes the approved proposal once: token,
  grant, write scope and business capability re-verified; approval bound to the same
  person, grant and argument digest, unexpired; the canonical locks taken and the state
  recomputed under them (a moved deadline, a new owner or a closed Matter make it
  stale); the canonical service(s) run in a savepoint under the MCP audit provenance;
  the receipt is stored. Repeating the commit returns the same receipt; two commits at
  once execute once; a failure halfway leaves nothing.

The model can prepare as much as it likes. It cannot approve.

### 2. The write boundary is a separate process with its own role

`mcp` stays exactly as read-only as Phase 1 left it. Writes go to a new Compose service,
**`mcp-writer`** (same image, `manage.py run_mcp_write_gateway`,
`JURISTID_PROCESS_ROLE=mcp_writer`):

- reachable only from `mcp`, over an `internal: true` network `mcp_write` that holds
  the two of them and nothing else — not the tunnel client, not `web`, no port;
- connects to PostgreSQL as **`juristid_mcp_writer`**: SELECT on the schema except
  sessions; INSERT/UPDATE only on an explicit allow-list of the tables the approved
  canonical services write, plus its own proposal and event tables; **no** write on
  accounts, capabilities, credentials, MCP grants/tokens/authenticators or approvals;
  no DELETE, TRUNCATE or DDL beyond what is listed and justified in
  `app/mcp_writes/db_role.py`; checked at start-up;
- holds no browser secret, no MFA, authenticator or token-derivation key, no mail
  credentials and no evidence mount;
- **verifies the forwarded bearer token itself**. The gateway key shared with `mcp`
  proves only which container is calling; a user id, persona or role is never accepted
  from the request.

So a compromised `mcp` can at most prepare proposals for the people whose tokens pass
through it, and a compromised `mcp-writer` still cannot mint approvals, tokens or
grants, or change who may do what.

### 3. A write scope that is granted, never assumed

`juristid.write` is a second OAuth scope. An operator enables it per grant
(`mcp_access grant-write`), only for an active account acting under SPECIALIST or
DEPARTMENT_HEAD; an ADMINISTRATOR without a business role, a READER and a pending
account are refused. A client asking for it without a write-enabled grant is quietly
narrowed to `juristid.read`; refresh never widens and drops `write` once revoked.
Every write additionally asks the existing capability for that change
(`may_write_business_content`, `may_assign_work`, …) at prepare and at commit. A
read-only token calling a write tool gets an `insufficient_scope` challenge.

### 4. Operations, not CRUD

Ten typed operations over named canonical services (`app/mcp_writes/operations/`,
docs/mcp/WRITE-OPERATIONS.md): create a Matter, edit its title or brief summary, assign
it, add a shared chronology note, record a meeting, record a procedural link, set or
change the next action, complete the current action with the outcome its kind
requires, request / move / correct an opinion-response deadline (three explicit modes),
add an important date with its precision. Nothing deletes, uploads, fetches, sends,
marks an opinion sent or touches the deferred participation workflow.

Three choices the operations make deliberately, each shown in the preview:

- **The kind of a step is stated.** The web form stopped asking for WAIT or MONITOR
  (0052) and derives the kind; the owner's brief asks ChatGPT to set «waiting for the
  Ministry of Finance's reply, review on 15 November» directly, so `set_next_action`
  takes `do`/`wait`/`monitor` and the date's meaning. The service has always accepted
  them. The preview still never prints the kind's own label (0054) — it says what the
  date means. Changing the current step keeps its kind, meaning and precision unless
  the person says otherwise, as «Muuda» does.
- **A closed Matter takes no MCP change**, assignment included, although the page lets a
  closed Matter change hands: reopening and handing on stay in the browser.
- **No «correct» mode for a deadline.** No service rewrites a recorded deadline as if it
  never existed; `move` keeps the request and its history, `new_request` opens a new one.

### 5. Provenance: the person, through ChatGPT

Every row a commit writes carries `auth_method = mcp`, `auth_assurance =
individual_mfa`, `authenticated_principal` = the token's account and the proposal's
correlation id; Teema käik shows «ChatGPT kaudu, isiklikult kinnitatud» after the name.
`McpWriteEvent` (append-only) records every prepare, approval, decline, commit, replay
and refusal with ids and codes only.

### 6. Off until switched on

`MCP_WRITES_ENABLED` defaults off on every process: no write tool is listed, the scope
is neither advertised nor granted, the approval page answers 404 and the gateway
refuses. Writing can be stopped without touching reading by any one of: the flag,
`docker compose … stop mcp-writer`, `mcp_access revoke-all-writes`.

## Consequences

- New app `app.mcp_writes` (proposals, approvals, events; migration `0001`),
  `mcp_access/0002` (grant write fields), an audit migration (choices only). No existing
  table changes shape; nothing is rewritten.
- A new service, network, env file and DB role on KOCORP; `web` gains one route.
- ChatGPT keeps a frozen tool list; enabling writes means refreshing the connector.
- Residual risk: a person who knows the gate password **and** holds a colleague's
  authenticator can approve as that colleague — the same assurance 0185 states.

## Status update 2026-10-25 — where AI writes stand

Four different things are easy to collapse into «writes are on». They are
recorded separately here (verified 25.10.2026, read-only checks of production):

| | State | What it means |
| --- | --- | --- |
| 1. Code deployed | **Yes** — since `156b9800` (23.10.2026); production now runs `c32608155e05`, migrations current | `app.mcp_writes`, `mcp_access/0002` and the audit choices are on the host |
| 2. Gateway activated | **Yes** — `mcp-writer` runs and is healthy; `MCP_WRITES_ENABLED=1` on `web`, `mcp` and `mcp-writer`; the server lists the write tools | §6's first switch is on. Any of §6's switches still turns writing off without touching reading |
| 3. Write scope granted | **No** — no grant has `juristid.write` | The only granted account is a READER, which §3 refuses write by design (`mcp_access grant-write` names SPECIALIST/DEPARTMENT_HEAD only). Every token in use is `juristid.read` |
| 4. Successful end-to-end write | **None** — 0 proposals have ever been prepared | Prepare → approve → commit has never run against production data; only the test suite (CI) has exercised it |

So nothing has been written to Juristid through MCP. Moving to state 3 needs an
operator's `grant-write` for a SPECIALIST or DEPARTMENT_HEAD account and that
person's new connection (docs/mcp/OAUTH-SCOPES.md); state 4 is that person's first
approved change. Neither is a deployment step, and neither is implied by state 2.
Reading (ADR 0185) is enabled and was verified end to end on 25.10.2026.

## Status update 2026-10-29 — after the code audit of 2026-10-28

The status stays **Proposed**. Remediated on branch
`remediation/a-lovable-security` (not merged or deployed by this note):

- **Re-verification window (C-03).** The owner decided on 2026-10-29 that
  production runs `MCP_WRITE_REVERIFY_SECONDS=0`: every approval needs its own
  fresh authenticator code, and nothing is remembered between approvals (a
  window left from a non-zero setting is ignored). The code default stays 300 s
  until the operator changes the production environment. Independently of the
  setting, every check that needs no code — pending, not expired, the arguments
  digest, the grant's write right, not stale — now runs **before** the code is
  spent and again under the proposal's lock afterwards, so a proposal that
  cannot be approved spends no code and opens no window, and the window (when
  there is one) is opened only by a code that approved something. Prepare →
  approve → commit, proposal identity, one-time codes and replay protection are
  unchanged.
- **Correlation (C-07).** `X-Correlation-Id` from `mcp` is accepted by the
  gateway only as a canonical, non-nil UUID; missing, malformed or already a
  proposal's, it is replaced by a fresh one (logged as replaced). A prepared
  proposal keeps the prepare call's id, so the tool call's log line, the
  proposal, its PREPARED / APPROVED / COMMITTED events and the business audit
  rows of the commit share one id; the commit call's own id is kept on its
  events as `call_correlation_id`. The id is a label only: it never selects a
  proposal, grant or user.
- **The writer's database role (C-04).** juristid.E050 is an error in the
  `mcp_writer` process role whatever `REAL_DATA_ALLOWED` says, so a gateway
  connected as a write-capable role never starts.

Production state is as the 2026-10-25 update records; nothing above changes it.
