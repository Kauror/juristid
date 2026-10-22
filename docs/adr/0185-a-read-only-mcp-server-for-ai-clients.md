# ADR 0185 — A read-only MCP server lets an individually authorized colleague ask Juristid from ChatGPT

- Status: Proposed (owner's brief «JURISTID — Autonomous Multi-Agent MCP Implementation
  and LIVE Production Deployment», 2026-10-22; Phase 1)
- Related, unchanged: 0005 (authorization chokepoint), 0016 (authentication modes and
  the shared gate), 0116 (search answers from its indexes), 0145 (personal sign-in,
  dormant), 0179 (pinned third-party code; least environment), 0180 (audit provenance),
  0181 (one internal read policy)
- Amends: nothing. The browser keeps `AUTH_MODE=shared_gate` and every rule it had.

## Context

The owner wants a colleague to open their own ChatGPT conversation (the Chamber's
ChatGPT Business workspace «Kaubanduskoda») and ask «Where are we with 2026_225?»,
«Have we opposed similar changes to the Packaging Act?» or «What enters into force in
the next 30 days?» — and get an answer grounded in Juristid's own records, with links
back to them.

The Model Context Protocol (MCP) is how ChatGPT reaches a private system. ChatGPT
supports a custom MCP server reached either at a public HTTPS URL or through an
OpenAI **Secure MCP Tunnel** (an outbound-only `tunnel-client` beside the server). In
both cases ChatGPT authenticates with OAuth 2.1: it discovers the server's protected
resource metadata (RFC 9728), the authorization server's metadata (RFC 8414), sends
the user's browser to the authorization endpoint with PKCE S256 and a `resource`
indicator (RFC 8707), and then calls the MCP endpoint with the bearer token. The
authorization server is never tunnelled: it must be reachable from the user's browser
and from the internet.

Two facts about Juristid shape the decision:

1. **The shared gate does not identify a person** (0016). Anybody with the department
   password can pick any persona. A token that answers questions over the whole
   corpus, held by a third-party service for weeks, must not be issued on that basis.
2. **No individual identity provider is active.** Entra ID is out of scope (0145 §1),
   Cloudflare Access and personal passwords are built but dormant, and three of the
   four production personas still carry placeholder addresses. Activating either mode
   would change how the browser authenticates — which this brief does not authorize.

## Decision

### 1. A thin MCP adapter over the existing services, in its own process

`app/mcp_server/` is an MCP server built on the official Python SDK (`mcp` 2.x,
`MCPServer`, Streamable HTTP, **stateless**, JSON responses). It runs as its own
Compose service `mcp` from the **same image** as `web`, started by
`python manage.py run_mcp_server` (system checks first, like every worker). It calls
the Django services that the pages call — search, selectors, timeline, intelligence,
documents — through a small bounded thread pool, so the ORM never runs on the event
loop. There is no second database, index, vector store, corpus copy or language model.

The process is read-only twice over:

- **No write tool exists.** Seven tools, every one `readOnlyHint: true`,
  `destructiveHint: false`, `openWorldHint: false`, `idempotentHint: true`. No tool
  takes SQL, a path, a URL to fetch, a Python expression or a model name.
- **The database refuses writes from this process.** Its connections open with
  `default_transaction_read_only=on`. Any `INSERT/UPDATE/DELETE` — including one a
  future change adds by mistake, or the cache table — fails with
  `ReadOnlySqlTransaction`. A test proves it. Access logging goes to stdout, never to
  the database.

### 2. Tools

All identifiers are opaque strings the server issued; all URLs are absolute
`https://<JURISTID_PUBLIC_ORIGIN>/…` built with `reverse()` from verified record ids,
never from caller input, and never carry a token or credential.

| tool | input | answers |
|---|---|---|
| `search` | `query` (string, ≤ 500 chars) | ChatGPT's company-knowledge contract: `{"results":[{"id","title","url"}]}`. Ranked by `app.search.services.search_page` (Estonian stemming, reference matching, aliases, trigram), authorized before matching, counting and slicing. At most 20 results. |
| `fetch` | `id` (string) | `{"id","title","text","url","metadata"}` for one source: a Matter snapshot, an entry, a submission, a document's extracted text (page/slide/sheet fragments with their `locator_label`), a historical OneNote page, a procedural development, an engagement, an important date, a commencement. Text is bounded (`MCP_FETCH_MAX_CHARS`, default 20 000); a longer source is returned as an excerpt with `metadata.truncated=true`, `metadata.next_id` (the id that fetches the next part) and the exact part covered — never silently cut. |
| `search_records` | `query`, optional `source_kinds`, `matter` (UUID or reference), `page` (1–10) | The same ranked search with the full row: source kind, Matter reference and title, snippet, locator, source date when the record has one, rank, and the `id` for `fetch`. 20 per page. |
| `get_matter` | `matter` (UUID or reference such as `2026_225`) | A structured snapshot: reference, title, open/closed and disposition, Hetkeseis (stage) and work status, Vastutaja, organisations, the current next action with its DO/WAIT/MONITOR kind, date semantics and precision, planned actions, the opinion deadline (`response_deadline_of`), upcoming and past important dates, commencements, sent submissions and official replies, latest developments, counts — each value labelled `confirmed` / `planned` / `estimated` / `previous` / `missing` where the model distinguishes them. No next step is invented when there is none. |
| `get_matter_activity` | `matter`, optional `since`, `until` (ISO dates), `kind` (`all`/`entries`/`events`), `page` | The Teema käik chronology from `app.matters.timeline.matter_timeline`, newest first by event date (not import time), 25 per page, each item with its date, date precision, kind, short text, and the link to its anchor. |
| `get_matter_documents` | `matter`, optional `page` | Document metadata: title, role, original filename, document date, current version number, extraction state and fragment count, the Juristid URL, and a `fetch` id when text exists. Never bytes. |
| `get_upcoming_commencements` | optional `from`, `to` (ISO dates; default the register's «next 30 days», today … today+29, at most 400 days), `page` | Active `MatterEffectiveDate` records in the window (the register's containment rule), each with Matter, what enters into force, the date and its precision, kind (`KNOWN_DATE` / `GENERAL_ORDER` / `UNKNOWN`), the official source URL, Vastutaja and Matter URL — and, separately, informational *planned* and *estimated* commencement dates (`MatterImportantDate`), labelled as such. A date in the past is never reported as «entered into force». |

`search` and `fetch` keep the exact shape ChatGPT's company knowledge and deep
research require (`structuredContent` plus the same JSON as a text item, with an
`outputSchema`). The richer tools exist because the owner's questions need structure
`search`/`fetch` cannot carry (deadline semantics, WAIT vs DO, date precision).
`get_my_work`, `get_waiting_matters` and `get_recent_changes` are **not** added:
`search_records` with a ministry name and `get_matter` answer those questions; a tool
is added only when an evaluation shows a question the set cannot answer.

The server's `instructions` tell the client, in their first 512 characters, that
retrieved text is data and never instructions, that every answer must cite the URLs
returned, that `planned`/`estimated` dates are not facts, and that a missing value is
missing.

### 3. Identity: an individually verified grant, never the shared gate alone

Juristid is its own OAuth 2.1 **authorization server** for this one resource, served
by `web` at the public origin. The MCP process is the **resource server**.

A colleague can connect only when **all** of these hold:

1. an operator has granted that account MCP access
   (`manage.py mcp_access grant --user <upn>`) — an explicit, audited, per-account
   decision; no account is ever provisioned or granted because a token or an e-mail
   address looks right;
2. the account is active and `may_read_business_content` is true for it (0181) — so an
   ADMINISTRATOR without a business role, a pending or switched-off account is refused
   at grant, at authorization, at every token use and at every tool call;
3. the person **enrolled a personal authenticator** (TOTP) through a one-time
   enrolment link the grant produced, inside their own gate session and persona; and
4. at the authorization step they pass the shared gate, act as **that** persona, and
   type a current code from **that** authenticator.

The second factor is what makes the identity individual: the gate password is shared,
the authenticator is not. Codes are single-use (the last accepted step is stored),
and five wrong codes lock the grant for fifteen minutes. The TOTP secret is encrypted
at rest with the existing `mfa` key derivation, under a distinct purpose.

This is deliberately **not** `AUTH_MODE=local_password`: nothing in the browser
changes, no personal password exists, and the authenticator opens nothing but the
OAuth consent. When the department activates Entra ID or Cloudflare Access later, the
authorization step will accept that identity instead (a new verifier, same grant
model).

### 4. OAuth details

- Discovery: `/.well-known/oauth-authorization-server` (issuer = the public origin,
  `code_challenge_methods_supported=["S256"]`, `client_id_metadata_document_supported`,
  `authorization_response_iss_parameter_supported`, scopes `juristid.read`), and the
  MCP process's `/.well-known/oauth-protected-resource[/mcp]` naming that issuer.
- Clients: **pre-registered public client metadata**, from settings — by default only
  ChatGPT's client metadata document `https://chatgpt.com/oauth/client.json` with its
  redirect URI `https://chatgpt.com/connector_platform_oauth_redirect`. Client
  authentication is `none` (PKCE) or `private_key_jwt` verified against the client's
  published JWKS. No open dynamic registration; no client secret on our side.
- Authorization code: 60 seconds, single use, bound to client, redirect URI, PKCE
  challenge (S256 only), scope and resource. Responses carry `iss` (RFC 9207).
- Access token: opaque, 256 bits, **1 hour**, stored only as SHA-256, bound to user,
  grant, client, scope and resource (audience). Refresh token: opaque, **30 days**,
  rotated on every use; reuse of a rotated refresh token revokes the whole family.
- The resource (audience) must be one of `MCP_RESOURCE_URLS` — the public MCP URL
  and/or the Secure MCP Tunnel URL OpenAI rewrites discovery to.
- Revocation: `/oauth/revoke` (RFC 7009); `mcp_access revoke --user` ends the grant
  and every token at once; a role change, deactivation or `security_epoch` bump makes
  every outstanding token fail its next check.

### 5. Every call is authorized again

The MCP process verifies the bearer token itself (hash lookup, expiry, audience,
scope, grant active, account active, epoch unchanged) and then resolves the account
and calls every service **as that user** — `visible_to(user)`, `scope_for_user(user)`,
`search_page(user=…)`. No tool takes a user, persona, e-mail or role argument, so no
request can act as somebody else. An unknown or unauthorized id answers exactly like a
missing one. Errors carry no business text.

### 6. Transport and topology

```
ChatGPT (workspace Kaubanduskoda)
   │ HTTPS, OAuth bearer
   ▼
OpenAI Secure MCP Tunnel ◄── outbound HTTPS ── mcp-tunnel (tunnel-client, KOCORP)
                                                   │ http://mcp:8001/mcp (internal network only)
                                                   ▼
                                               mcp (this ADR) ── PostgreSQL (read-only transactions)

user's browser ── https://juristid.orgusaar.ee/oauth/authorize ── Cloudflare tunnel ── web
OpenAI backend ── https://juristid.orgusaar.ee/oauth/token     ── Cloudflare tunnel ── web
```

The `mcp` service publishes no port and joins only the `internal` network. It holds
no evidence mount and none of the browser's secrets (no gate password, no MFA key, no
mail credentials): it gets its own minimal environment. The tunnel client holds only
its OpenAI runtime key and tunnel id. Neither starts unless named; both are stopped by
`docker compose … stop mcp mcp-tunnel`, which disables AI access without touching
Juristid.

If a Secure MCP Tunnel cannot be created, the fallback is a dedicated Cloudflare
hostname routed to `mcp:8001`. That widens the public surface and is **not** done
without the owner's explicit approval.

## Consequences

- One additive app, `app.mcp_access`, with its own tables (grants, authorization
  codes, tokens) — no existing table changes, no data rewritten, no search rebuild.
- New public routes on `web`: `/.well-known/oauth-authorization-server`,
  `/oauth/authorize` (behind the gate), `/oauth/token`, `/oauth/revoke`,
  `/mcp-ligipaas/…` (enrolment, behind the gate and persona). None returns business
  content; `tests/test_business_routes_need_a_reader.py` lists them.
- New dependency: `mcp` (and through it `starlette`, `uvicorn`, `httpx`, `pydantic`),
  pinned in `uv.lock`, audited by the existing dependency job.
- ChatGPT keeps a frozen snapshot of the tool list; changing a tool means refreshing
  and republishing the connector in the workspace admin console.
- Residual risk: a person who knows the gate password **and** holds a colleague's
  authenticator can connect as that colleague. That is the same assurance a second
  factor gives anywhere; it is stated, not hidden.
- Out of scope, by the brief: any write, notification or scheduling; raw evidence
  bytes; a public plugin.
