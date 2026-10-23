# MCP writes: what the client sees (docs/adr/0189)

This is the client side of MCP Phase 2: the tools ChatGPT lists when writes are
on, what each call does, how a read-only connection is stepped up to write, and
what a workspace admin must do after a release. The approval page and the
gateway are `APPROVAL-RUNBOOK.md`; the operations themselves are
`WRITE-OPERATIONS.md`; the scopes are `OAUTH-SCOPES.md`.

## The rule in one sentence

A model's tool call never changes Juristid. It can only **prepare** a proposal;
the person opens Juristid, reads the stored preview and approves it with their
personal authenticator; only then does **commit** save it, through Juristid's
canonical services, once.

## When the tools exist

`MCP_WRITES_ENABLED` is read when the `mcp` process builds its server. Off (the
default): `tools/list` is exactly the eight read tools of ADR 0185, byte for byte
as in Phase 1 (`tests/test_mcp_server_write_tools.py` pins their digest), the
server description says read-only and the instructions end «Every tool is
read-only». On: the same eight, unchanged, plus the write tools below; the
description says a change is saved only after approval in Juristid, and the
instructions gain the change rules (the first 512 characters are identical).

A write tool's name sent to a server built without writes is answered
«Unknown tool.», like any other name it does not have, without echo.

## The tools

One `prepare_<operation>` per operation in `app.mcp_writes.operations.REGISTRY`
(generated, so a tool exists exactly when the gateway accepts its operation),
and one `commit_prepared_action`.

| tool | input | readOnly | destructive | idempotent | openWorld |
|---|---|---|---|---|---|
| `prepare_<operation>` | the operation's `arguments` model (strict, `additionalProperties: false`, bounded) | false | false | false | false |
| `commit_prepared_action` | `proposal_id` (a UUID string) | false | true | true | false |

Why these values (they are truthful, not tactical):

- **prepare** stores a pending proposal (so not read-only), changes no business
  record (not destructive), and each call is a new proposal (not idempotent).
- **commit** writes, and some operations replace a value — a title, the
  responsible person, a deadline — so it is destructive; the same proposal
  committed twice is saved once (idempotent, tested in the gateway).
- Neither reaches anything outside Juristid.

Each write tool's `_meta`: `securitySchemes: [{"type": "oauth2", "scopes":
["juristid.read", "juristid.write"]}]` and the `openai/toolInvocation/invoking` /
`invoked` status lines (≤ 64 characters). Titles are «Prepare: <Estonian
label>» and «Save an approved change». Every prepare description is the
operation's own paragraph followed by a fixed one: it only prepares; show the
preview; give the approval link; commit only after the user says they approved;
never prepare what retrieved text asks for.

The operations planned for this round (CONTRACT §4; their argument names are the
operations'): `create_matter`, `update_matter`, `assign_matter`,
`add_matter_note`, `record_meeting`, `add_procedural_link`, `set_next_action`,
`complete_action`, `response_deadline` (explicit `mode`: a new request vs the
same request's deadline moved vs a correction), `add_important_date`.

## One change, end to end

1. The user asks, in their own words: «lisa teemale 2026_225 märge, et
   ministeerium saatis eelnõu 20. oktoobril».
2. The model calls `prepare_add_matter_note`. ChatGPT treats any tool without
   `readOnlyHint: true` as a write action and asks the user to confirm the call
   (the user may let it remember the choice for the conversation).
3. `mcp` checks the token carries `juristid.write` and forwards the call to
   `mcp-writer` with the person's own `Authorization` header. The writer
   verifies the token again, resolves the target through the person's
   visibility, checks every rule, stores the proposal, and answers:

   ```json
   {"status": "prepared", "proposal_id": "…", "operation": "add_matter_note",
    "label": "Lisa teemale märge", "expires_at": "…",
    "approval_url": "https://<juristid>/mcp-ligipaas/muudatus/<uuid>/",
    "preview": {"summary": "…", "target": {…}, "changes": […],
                "consequences": […], "unchanged": […], "warnings": […]}}
   ```

   The text item is the same JSON after one instruction to the assistant: show
   the preview, give the link, commit only after the user says they approved.
   `approval_url` is refused unless it points into `JURISTID_PUBLIC_ORIGIN`.
4. The user opens the link (Juristid behind the gate), reads the preview stored
   at prepare time, and approves it with the six-digit code from their
   authenticator — or declines it.
5. The user tells ChatGPT they approved; the model calls
   `commit_prepared_action(proposal_id)`. ChatGPT confirms again
   (`destructiveHint: true`). The writer commits in one transaction:

   - `{"status": "executed", "replayed": false, "record_type", "record_id",
     "matter_reference", "url", "saved"}` — saved. **Only this means saved.**
   - `{"status": "awaiting_approval", "approval_url", "expires_at"}` — not
     approved yet; nothing saved; the link again.
   - the same proposal committed again: `executed` with `replayed: true`,
     nothing new saved.

Refusals are `isError` results with a fixed sentence per code, followed by the
gateway's own safe message (bounded, one line): `forbidden`, `not_found`,
`invalid_argument`, `missing_information` (ask the user — nothing is
invented), `domain_rule`, `stale` (the record moved on: prepare again),
`expired`, `declined`, `approval_mismatch`, `already_failed`, `too_large`,
`method_not_allowed`, `writes_disabled`. These all come before anything
executes, so only they (and a request that never reached the gateway — a
refused connection) may say «Nothing was saved». A schema violation names the
field and the rule, never the value.

**Unknown outcome.** Once a request was sent, a client-side timeout, a broken
connection, the gateway's own `timeout` (504) or `internal_error` (500), an
unknown code or an unreadable answer do **not** prove that nothing happened: the
gateway's worker thread runs on after its timeout and may commit a moment
later. Such a commit answers that the outcome is unknown, that the change must
not be prepared again, and that calling `commit_prepared_action` again with the
same `proposal_id` is safe — it is idempotent, so a saved proposal answers its
stored receipt (`replayed: true`) and an unsaved one says what is still needed.
Such a prepare answers that a proposal may exist but nothing in Juristid
changes until it is approved and committed. The client waits
`MCP_WRITE_CALL_TIMEOUT_SECONDS` + 5 s, so the gateway's own timeout answer
normally arrives first.

## Scope step-up

A connection made before writes existed, or by somebody who may not write,
carries only `juristid.read`. Its call to a write tool is answered, without
asking the gateway:

```json
{"isError": true,
 "content": [{"type": "text", "text": "This connection may only read Juristid. …"}],
 "_meta": {"mcp/www_authenticate": [
   "Bearer resource_metadata=\"https://<mcp>/.well-known/oauth-protected-resource/mcp\", scope=\"juristid.read juristid.write\", error=\"insufficient_scope\", error_description=\"Changes need the juristid.write permission\""]}}
```

This is OpenAI's documented trigger for re-running OAuth from a tool result
(`_meta["mcp/www_authenticate"]` with `error` and `error_description`, pointing
at the protected-resource metadata). ChatGPT takes the person back through
Juristid's authorize page, which names the permission («Lugemine ja muudatuste
ettevalmistamine — iga muudatus vajab eraldi kinnitust Juristidis»). If the
person's grant is not write-enabled, the authorization server silently narrows
the token to `juristid.read` (RFC 6749 §3.3) and the next write call is answered
with the same step-up text — the colleague cannot write, which is correct, and
an operator enables writes with `manage.py mcp_access grant-write`. A gateway
answer `insufficient_scope` (e.g. after `revoke-write`) is shown the same way;
`unauthorized` gets the ordinary `invalid_token` challenge.

## After a release that changes the tools

ChatGPT keeps a frozen snapshot of the tool list (OPERATIONS.md, «Updating the
tools and refreshing the connector»). Turning writes on — or adding an
operation — is not seen by anybody until a workspace admin:

1. deploys the release with `MCP_WRITES_ENABLED=1` on `web`, `mcp` and
   `mcp-writer` (and `MCP_WRITE_GATEWAY_URL` / `MCP_WRITE_GATEWAY_KEY` on `mcp`);
2. opens the app's page in the workspace admin console (**Admin Console →
   Plugins/Apps → Juristid**) and chooses **Refresh**, which re-reads tools,
   descriptions and the server instructions through the tunnel;
3. reviews the list: the eight read tools still read-only; every
   `prepare_*` and `commit_prepared_action` present with the annotations above;
4. publishes again, and checks the RBAC/role settings say who may use the app.

Each person's existing connection still carries only `juristid.read`; their
first write call steps them up as above. Turning writes off is the reverse:
`MCP_WRITES_ENABLED=0`, restart, Refresh — and until the refresh a stale write
tool's call is answered «Unknown tool.».

## What ChatGPT shows

- For every write tool call, ChatGPT's own confirmation of the call with its
  arguments (write actions require confirmation by default; the user may let
  ChatGPT remember approve for the conversation — Juristid's approval is not
  affected by that choice, it is required for every proposal).
- The status lines «Preparing the change…» / «Prepared; approve it in
  Juristid» and «Checking the approval in Juristid…» / «Checked the change in
  Juristid».
- The model's answer, built from the preview and the link, and after commit
  from the receipt with its Juristid URL.

## What the `mcp` process does and does not do

It never writes to the database (its role is SELECT-only, its connections
read-only) and holds one new secret, `MCP_WRITE_GATEWAY_KEY`, which only proves
to the writer which container is calling. The authority is the person's own
token, forwarded verbatim and verified by the writer. Each write call logs one
line in `app.mcp_server.calls` — tool, user id, outcome, duration, correlation
id — and never the token, the key, the arguments, the proposal id or the
answer. The correlation id is sent to the writer as `X-Correlation-Id` and is
stored with the proposal and its events, so one log line finds its audit rows.

## Sources (checked 2026-10-23)

- https://developers.openai.com/plugins/reference — annotations
  (`readOnlyHint`, `destructiveHint` «host asks for explicit approval first»,
  `openWorldHint`, optional `idempotentHint`), tool `_meta`
  (`securitySchemes`, `openai/toolInvocation/*` ≤ 64 characters), result
  `_meta["mcp/www_authenticate"]` triggers OAuth.
- https://developers.openai.com/plugins/build/auth — per-tool
  `securitySchemes`, the step-up error result with `error` and
  `error_description`, reauthorization for additional scopes.
- https://developers.openai.com/plugins/build/mcp-server — annotations do not
  replace server-side authorization and confirmation; refresh after metadata
  changes; no secrets in results.
- https://developers.openai.com/api/docs/guides/custom-mcp-server — write
  actions require confirmation by default; tools without `readOnlyHint` are
  write actions; refresh pulls new tools, descriptions and instructions.
