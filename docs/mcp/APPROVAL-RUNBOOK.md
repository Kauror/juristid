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
   shared gate, **their own persona**, and the page shows what will be saved,
   derived again by Juristid from the proposal's arguments (since r96,
   docs/adr/0234: never the gateway's stored copy) — the Teema and its link,
   each field «praegu → pärast», the consequences, what does not change,
   warnings, expiry. The form is bound to exactly that preview: if anything
   changed between showing it and pressing «Kinnita», the page shows the new
   preview and asks again, spending no code. «Kinnita» needs the
   current code from their «Juristidi MCP autentija» (the same authenticator,
   lock ladder and throttle as connecting ChatGPT). Production runs with
   `MCP_WRITE_REVERIFY_SECONDS=0` (owner's decision 2026-10-29): **every**
   approval needs its own fresh code, and nothing is remembered between them.
   With a positive value (the code default is 300 s) a code that approved a
   change in the same gate session less than that long ago is reused, but
   every proposal still needs its own «Kinnita». A proposal that cannot be
   approved — expired, changed since the preview, its arguments no longer
   matching, the grant no longer allowed to write — is refused **before** the
   code is checked, so it spends no code and opens no reuse window.
   «Lükka tagasi» declines.
   If the record changed since the preview, the page says so and offers no
   «Kinnita». Anybody else — another persona, a guessed link — gets one
   generic refusal.
   **«Kinnita» saves the change at once** (since r96): in one transaction
   Juristid locks the proposal and the record, checks everything again
   (pending, unexpired, the grant's write right, that the ChatGPT connection
   that prepared it is still live, that the record has not moved on and the
   preview is still the one shown), writes the approval and runs the canonical
   service — once. The page says «Kinnitatud ja salvestatud». There is no
   «Tühista kinnitus» any more: there is no approved-but-unsaved state.
3. **Receipt.** Back in ChatGPT the colleague (or ChatGPT on their word) calls
   `commit_prepared_action` — a name kept for compatibility: it does not
   approve, authorise or execute anything, it only fetches the receipt. The
   gateway (`POST /v1/commit`) checks it is the same person, grant, client and
   token family and answers the saved record's receipt (executed), the link
   again (waiting for approval), or an error for a declined, expired, failed
   or stale proposal; it executes nothing. A second call returns the same
   receipt (`replayed`) and saves nothing more.

A model's tool call alone never saves anything. Without step 2 the receipt
request answers `awaiting_approval`. Repeated calls are budgeted (docs/adr/0234 §4):
past a limit ChatGPT is told `rate_limited` with how long to wait; the
approval page itself is never limited, and the receipt of a saved change is
always answered.

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
`COMMITTED` (written by the approval page since r96) / `RECEIPT_DELIVERED` /
`COMMIT_REPLAYED` / `COMMIT_REFUSED` / `FAILED` / `EXPIRED` /
`FORMAT_RETIRED` — with its codes. Identical refusals past the first five in
ten minutes are counted, not each written out (`aggregated` on the sixth);
`mcp_writes budgets` shows the counts. Neither prints arguments, preview text, saved values, tokens
or codes: to see the content, open the record in Juristid. To join the change
to its business audit rows, filter `audit_changeevent` / `audit_securityauditevent`
by the `correlation_id` `show` prints.

What the codes mean: `stale` — the record moved on after preparing or
approval; `approval_mismatch` — the stored arguments or the approval do not
match the digest (treat as tampering; check database access); `expired` — not
approved in time; `forbidden` on a receipt request or at «Kinnita» — the account
lost its write right; `internal_error` with `retryable=True` on the approval
page — nothing was saved and the proposal is still waiting for approval;
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
