# AI-assisted changes — approval, investigation, switching off

How a change a colleague's ChatGPT prepares becomes a saved change in Juristid,
how to find out afterwards exactly what happened, and how to stop it. The
decision is ADR 0189; the read-only connection it builds on is
[OPERATIONS.md](OPERATIONS.md) and ADR 0185.

Every command below runs **on the host, from the checkout**, under the rules of
`deploy/unraid-main/OPERATOR.md` (no printed environment, no `set -x`):

```bash
cd /mnt/user/appdata/juristid-main/repo
```

## How one change happens

1. **Prepare.** In ChatGPT the colleague asks for a change («lisa teemale
   2026_225 märge…»). ChatGPT calls a `prepare_<operation>` tool. The `mcp`
   process forwards the call, with the person's own bearer token and the
   gateway key, to the write gateway `mcp-writer` (`POST /v1/prepare`). The
   gateway verifies the token itself (scope `juristid.write`, grant ACTIVE and
   write-enabled, account active, epoch, a business-write role), validates the
   arguments, previews the change through the operation and stores a
   **proposal** (PENDING, `MCP_WRITE_PROPOSAL_SECONDS`, default 15 min).
   Nothing in the register changes. ChatGPT gets the approval link.
2. **Approve.** The colleague opens
   `https://<origin>/mcp-ligipaas/muudatus/<proposal id>/` in their browser:
   shared gate, **their own persona**, and the page shows exactly what was
   prepared — the Teema and its link, each field «praegu → pärast», the
   consequences, what does not change, warnings, expiry. «Kinnita» needs the
   current code from their «Juristidi MCP autentija» (the same authenticator,
   lock ladder and throttle as connecting ChatGPT). A code typed in the same
   gate session less than `MCP_WRITE_REVERIFY_SECONDS` (5 min) ago is reused,
   but every proposal still needs its own «Kinnita». «Lükka tagasi» declines.
   If the record changed since the preview, the page says so and offers no
   «Kinnita». Anybody else — another persona, a guessed link — gets one
   generic refusal.
3. **Commit.** Back in ChatGPT the colleague (or ChatGPT on their word) calls
   `commit_prepared_action`. The gateway (`POST /v1/commit`) locks the
   proposal, checks it is the same person, grant, client and token family,
   that the approval exists, matches the stored arguments' digest and is
   younger than `MCP_WRITE_APPROVAL_SECONDS` (10 min), asks again whether the
   account may still write, takes the record's locks, re-checks it has not
   moved on, and runs the canonical service — once. A second commit of the
   same proposal returns the same receipt and saves nothing more.

A model's tool call alone never saves anything. Without step 2 the commit
answers `awaiting_approval`.

## What the record's history says

Every audit row the change writes carries `auth_method = mcp`,
`auth_assurance = individual_mfa`, `authenticated_principal` = the colleague
whose token it was, no persona, and the proposal's `correlation_id`. In
«Kõik muudatused», «Teema käik» and Haldus the name reads
«Mari Maasikas (ChatGPT kaudu, isiklikult kinnitatud)».

## Investigating an AI-assisted change

```bash
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web \
  python manage.py mcp_writes list --user mari.maasikas@koda.ee
docker compose -p juristid-main -f deploy/unraid-main/compose.yml exec -T web \
  python manage.py mcp_writes show <proposal id>
```

`list` shows proposals newest first (id, time, status, operation, user,
failure code); `--status PENDING|APPROVED|EXECUTED|DECLINED|EXPIRED|FAILED|STALE`
narrows it. `show` prints one proposal's ids, target Matter id and reference,
status, times, the approval (time, method `totp` / `recent_totp`, validity) and
every event — `PREPARED`, `APPROVED` / `APPROVAL_REFUSED` / `DECLINED`,
`COMMITTED` / `COMMIT_REPLAYED` / `COMMIT_REFUSED` / `FAILED` / `EXPIRED` —
with its codes. Neither prints arguments, preview text, saved values, tokens
or codes: to see the content, open the record in Juristid. To join the change
to its business audit rows, filter `audit_changeevent` / `audit_securityauditevent`
by the `correlation_id` `show` prints.

What the codes mean: `stale` — the record moved on after preparing or
approval; `approval_mismatch` — the stored arguments or the approval do not
match the digest (treat as tampering; check database access); `expired` — not
approved, or not committed, in time; `forbidden` at commit — the account lost
its write right between approval and commit; `internal_error` with
`retryable=True` — nothing was saved and the approval still stands;
`principal_mismatch` on `COMMIT_REFUSED` — somebody else's token tried this
proposal id.

## Switching writes off

* **Everybody, immediately, reading untouched:** set `MCP_WRITES_ENABLED=0` in
  `juristid.env`, `juristid-mcp.env` and `juristid-mcp-writer.env` and
  recreate `web`, `mcp` and `mcp-writer` (`--no-deps`, never a plain
  `up -d`). With it off no write tool is listed, the approval page is a 404
  and the gateway refuses every call. Stopping only `mcp-writer` has the same
  effect for commits and prepares (the `mcp` process gets no answer).
* **Every grant's write right, keeping the setting:** `manage.py mcp_access
  revoke-all-writes --reason "…"` (the write kill switch; `--reason` is
  required); read access stays.
* **One person:** `manage.py mcp_access revoke-write --user <upn> --reason "…"`.
  Their next prepare or commit is refused, including a proposal they already
  approved.
* **Everything AI, read included:** OPERATIONS.md «Switching it off».

Pending and approved proposals simply expire; nothing needs cleaning up.
