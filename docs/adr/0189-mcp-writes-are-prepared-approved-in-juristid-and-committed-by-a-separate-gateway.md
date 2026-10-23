# ADR 0189 — MCP writes are prepared, approved in Juristid by their person, and committed by a separate gateway

- Status: Proposed (owner's brief «JURISTID — MCP Phase 2: Secure Write Operations,
  Multi-Agent Delivery and Production Deployment», 2026-10-23)
- Amends: 0185 §1 («no write tool exists») and §2 (the tool list), 0180 (one new
  `auth_method`)
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
