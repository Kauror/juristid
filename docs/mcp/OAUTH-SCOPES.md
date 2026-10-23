# MCP OAuth scopes: reading and preparing changes

Juristid's authorization server (`web`, `app/mcp_access`) issues two scopes.
This page is the contract between it, the MCP server (`mcp`), the write
gateway (`mcp-writer`) and the approval page, and the operator runbook for the
write permission. Phase 1 (read-only) is described in
[OPERATIONS.md](OPERATIONS.md) and docs/adr/0185; the write design is
docs/adr/0189.

## The two scopes

| scope | setting | what it allows |
|---|---|---|
| `juristid.read` | `MCP_SCOPE` | the eight read tools. Every connection gets it. `/mcp` requires it and nothing more. |
| `juristid.write` | `MCP_WRITE_SCOPE` | *preparing* changes: a write tool may create a pending proposal, and commit one **after the person approved it in Juristid with their personal authenticator code**. The scope alone never changes a record. |

A token's scope is decided once, at the consent step, and can afterwards only
shrink.

### How the granted scope is decided

1. **The request** (`oauth.granted_scope`). `juristid.read`,
   `juristid.write` and the ignored generic scopes (`offline_access`,
   `openid`, `profile`, `email`) are accepted; any other scope is refused
   with `invalid_scope`. While `MCP_WRITES_ENABLED` is off, `juristid.write`
   is narrowed away here, without an error, so existing clients keep working.
2. **The consent step** (`services.authorize`, under the grant's row lock,
   after the person's own current code is verified). The authorization code's
   scope is `juristid.read`, plus `juristid.write` only if it was requested
   **and** `services.may_write(user, grant)` holds at that moment. Otherwise
   the code is for read alone (RFC 6749 §3.3). The `AUTHORIZATION_GRANTED`
   event records `scope` and, when narrowed, `requested_scope`.
3. **The code exchange** (`services.exchange_code`) narrows again with
   `may_write`: a write permission revoked between consent and exchange is
   honoured. The token response's `scope` is the real one.
4. **Every refresh** (`services.refresh`): a requested scope must be a subset
   of the presented token's (else `invalid_scope`), and the result is narrowed
   to what `may_write` allows *now*. After `revoke-write` the write scope
   drops out of the family for good; getting it back means connecting again.
   Nothing ever widens a family.

### `may_write(user, grant)` — asked afresh at every step

All of:

- `may_connect(user, grant)`: grant ACTIVE and the person's, account active,
  business-read role, not refused by `grant_refusal`;
- the grant's write decision stands (`write_enabled_at` set,
  `write_revoked_at` not);
- `may_write_business_content(user)`: SPECIALIST or DEPARTMENT_HEAD. A READER
  never writes. A technical ADMINISTRATOR without one of those roles never
  writes — not with a break-glass grant, not with any token;
- `settings.MCP_WRITES_ENABLED` on the process asking.

Nothing caches it. It is asked by the consent step, the exchange, the refresh,
the write gateway (`verification.writer_for`) and the approval page
(`services.confirm_write_code`).

### The consent page

The page shows what was *requested*, identically for every persona until that
persona's own current code has been verified (MCPSEC-01):

- read request: «Ainult lugemine: otsing ja teemade, dokumentide ning ajaloo
  lugemine.»
- write request (writes on): «Lugemine ja muudatuste ettevalmistamine: … —
  iga muudatus vajab eraldi kinnitust Juristidis.» plus the note that nothing
  takes effect without the person's own confirmation and that without the
  write permission only reading is granted.

A persona without the write permission who approves a write request gets a
read connection; the client learns that from the token response.

### Discovery and challenges

- `scopes_supported` in `/.well-known/oauth-authorization-server` (`web`) and
  in the protected-resource metadata (`mcp`): `["juristid.read"]`, plus
  `"juristid.write"` only while `MCP_WRITES_ENABLED` is on.
- `app.mcp_server.auth.challenge(resource, *, error, description, scope=None)`:
  `scope` defaults to `MCP_SCOPE`. A write tool called with a read-only token
  answers with `challenge(resource, error="insufficient_scope",
  scope=auth.write_step_up_scope())`, i.e.
  `scope="juristid.read juristid.write"`. The scope string is checked against
  RFC 6749 scope-token characters.
- `RequireBearer` at `/mcp` still requires only `juristid.read`.

### For the write gateway and the approval page

- `verification.verify_access_token(raw)` → `VerifiedAccess`, now with
  `family_id: uuid.UUID` (the token family; bind proposals to it). A
  hand-built `VerifiedAccess` defaults to `NO_FAMILY` (`UUID(int=0)`), which
  no token has.
- `verification.writer_for(access) -> User | None`: everything `reader_for`
  re-reads, plus the token row's family equals `access.family_id`, the token
  row's scope contains `juristid.write`, and `may_write`. Pure reads.
- `services.confirm_write_code(*, user, grant_id, code, ip_address, throttle)
  -> AuthorizationOutcome`: the approval page's code check. Same
  authenticator, same single-use TOTP step (a code spent connecting cannot
  approve, and the other way round), same lock ladder (5 wrong codes lock;
  15 min → 1 h → 4 h → suspension), same throttle rows — pass
  `app.mcp_access.views.throttle_keys(request, client_ip(request))`. Statuses:
  `ok`, `bad_code`, `no_grant` (grant not this persona's, not ACTIVE, or
  `may_write` false — no code checked; answer it like `bad_code`), `locked`
  (`wait` seconds), `suspended`, `unreadable` (answer like `bad_code`),
  `throttled` (`wait`). Records `WRITE_CONFIRMED` or
  `WRITE_CONFIRMATION_REFUSED`. Call it outside a transaction the caller
  would roll back on refusal, or the wrong-code count goes with it.

## Operator runbook

Every command records an `McpAccessEvent` with the command, the OS user and
the host. Reading is never touched by the write commands.

```
manage.py mcp_access grant-write --user <upn>
manage.py mcp_access revoke-write --user <upn> --reason "<why>"
manage.py mcp_access revoke-all-writes --reason "<why>"
manage.py mcp_access list                     # last column «muutmine»: jah | tühistatud | ei
```

**grant-write** refuses (and records `WRITE_REFUSED`) an inactive account, an
account without SPECIALIST/DEPARTMENT_HEAD (READER, ADMINISTRATOR), an account
`grant` would refuse (e.g. synthetic on real data), an account without an open
grant, and a grant that already has write. It may be given to a grant still
waiting for enrolment; it works once the grant is ACTIVE. It applies to
connections made after it: the person disconnects and connects ChatGPT again
(the consent page then says «Lugemine ja muudatuste ettevalmistamine»). It
runs only while `MCP_ENABLED` is on and warns when `MCP_WRITES_ENABLED` is
off.

**revoke-write** ends one account's write permission at once. No token is
revoked: the next write call is refused (`writer_for` → None), the approval
page refuses (`confirm_write_code` → `no_grant`), the next refresh drops the
scope, and reading continues. Granting write again later needs a new
connection.

**revoke-all-writes** is the write kill switch: every grant's write permission
ends, every read connection keeps working.

Both revoke commands run even with `MCP_ENABLED` off.

### Turning all AI writing off, independently of reading

Fastest first; each is enough on its own:

1. `MCP_WRITES_ENABLED=0` on `mcp-writer` (and `mcp`, `web`) and recreate
   those containers: `may_write` is False everywhere, write tools disappear,
   metadata stops advertising `juristid.write`, new and refreshed tokens are
   read-only. Grants keep their write decision for when it is switched back.
2. `manage.py mcp_access revoke-all-writes --reason "…"`: takes effect at the
   next call without a restart; turning writes back on later needs
   `grant-write` per person and a new connection.
3. Stop `mcp-writer`: write tools fail closed; reading is unaffected.

`revoke` / `revoke-all` end reading too (and so writing).
