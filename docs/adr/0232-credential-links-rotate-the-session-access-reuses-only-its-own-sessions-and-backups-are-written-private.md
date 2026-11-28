# 0232 — Credential links rotate the session, Access reuses only its own sessions, and backups are written private

Date: 2026-11-26 · Status: Implemented in the r96 candidate (SEC-006, SEC-007, SEC-011, SEC-012; built and tested; **not active in production** until release 96 is deployed. The authentication mode of every deployment is unchanged. Backups: only objects written after the deploy are private; existing artefacts stay as they are until the owner-approved remediation of RECOVERY.md, and `JURISTID_BACKUP_REQUIRE_PRIVATE` stays unset until then. The shared gate's persona risk remains an accepted, deferred residual risk)

Numbering (after integration in r96): 0230 parser isolation (SEC-008), 0231
nightly deadlines (SEC-010), 0232 this record, 0233 multipart boundary (SEC-013),
0234 MCP approval and budgets (SEC-009/014).

## Context

A Codex Security scan of `e49a0683` (production r94) reported, among others:

- **SEC-006 (HIGH)** — an activation or password-reset link's proof was written
  into whatever session the browser already had, and that session was never
  rotated. An identifier planted beforehand — a sibling host's cookie, a shared
  machine, a gate session somebody else opened — was a session the planter could
  use to set the password the link was for. Under the shared gate this is the
  path an administrator's own credentials are enrolled through (ADR 0186); in the
  dormant personal sign-in (ADR 0145) it is every link.
- **SEC-012 (MEDIUM)** — under `AUTH_MODE=cloudflare_access` the middleware reused
  any authenticated session whose account's address matched the verified
  assertion. That is a statement about the *account*: a persona chosen behind
  the shared gate, a development sign-in or a personal-password session for the
  same person would have carried on after a switch to Access, with its own
  expiry, gate keys and administrator state.
- **SEC-007 (MEDIUM)** — `lovable_inventory.ledger_csv` wrote values typed in the
  Lovable register through a bare `csv.writer`; every other CSV export already
  used `csv_safe_row`.
- **SEC-011 (MEDIUM)** — `juristid-backup.sh` created the dump, manifest,
  inventories and set directory with the caller's umask (022 under cron) and
  copied pool objects with the evidence tree's own 0644.

The owner excluded one more finding from correction (§5).

## Decision

### 1. A link's proof is a session-identifier boundary (SEC-006)

`local_views.keep_link_proof` is the one place a link enters a session — the
page itself, and the middleware for a link that arrives in front of the shared
gate. It now:

1. drops any proof the session held, the moment another `?kood=` arrives;
2. keeps a proof only if the link opens something *now*, decided by
   `link_holder_here`: the credential-link throttle (a locked network is refused
   before any lookup), `tokens.peek` (shape, purpose, expiry, single use,
   credential stamp), `credentials.link_holder` (eligibility), and under the
   shared gate `admin_session.may_receive_administrator_link`. A link that opens
   nothing writes **no session** and is counted once against the network;
3. writes the proof and then **rotates the session identifier and the CSRF
   secret** (`cycle_key` deletes the old row; `rotate_token`).

The page re-asks `link_holder_here` on every GET and POST and drops a proof that
stopped opening anything. Setting the password rotates identifier and CSRF
secret again. A personal sign-in drops a proof left from before it. Link pages
answer with `Referrer-Policy: no-referrer`, the arriving-link redirect in front of
the gate too; the secret still leaves the address bar with the first redirect
and is never in the gate's `next`, a log line or an error.

Unchanged: one-time semantics and the row locks of `tokens.consume`; the shared
gate's return to the bare link page after the password; the administrator's
enrolment (link → password → «Haldus» sign-in → authenticator); persona
selection, which the rotation carries across (`cycle_key` keeps the contents).

### 2. Access reuses a session only if Access established it (SEC-012)

At an Access sign-in the middleware writes a provenance marker,
`cloudflare_access.SESSION_MARKER`: `via=cloudflare_access`, the account, the
verified address, the assertion's `sub` and its `exp`. On every later request,
after the assertion is verified as before, the session is reused only if
`cloudflare_access.session_holds` finds all of: the marker, written by this mode,
for this account; the same address and the same `sub` (or none on either side);
an account still active and not synthetic; and the establishing assertion not yet
expired. Otherwise the whole session is flushed and the request is decided from
the assertion alone, exactly like a first visit — so a session from the shared
gate, `none` or `local_password` can never acquire Access provenance by matching
an address, and a mode change revalidates by construction (the other modes
already end sessions they did not establish). `set_expiry` is never 0 (Django's
"until the browser closes"). Sign-out, the signed-out assertion fingerprint and
the no-redirect denial page are unchanged.

### 3. Every spreadsheet-bound CSV goes through `csv_safe_row` (SEC-007)

`ledger_csv` applies `csv_safe_row` to the header and every row: a string that,
after leading whitespace, control or format characters, begins with `= + - @`
and is not a number gets a leading apostrophe. Numbers, booleans, blanks, column
order and Unicode text are unchanged. `ledger.json`, written beside it from the
same rows, is the machine-interchange record and keeps every value exactly as
the source held it. The other CSV writers (`reporting/exports.py`,
`legacy_import/reporting.py`, `backfill_legacy_owners`) already used the boundary;
XLSX goes through `xlsx_safety`. No other CSV writer exists.

### 4. Backups are created private; the destination is checked (SEC-011)

`juristid-backup.sh` sets `umask 077` before its first write (set directory 0700;
dump, manifest, SHA256SUMS and inventories 0600) and copies pool objects with
`rsync --no-perms`, so a **new** object is 0600 (directories 0700) with its owner
kept (10001 on the host, the application's account), and an object already in
the pool keeps its mode. Before writing, it reports any access beyond the owner on
the backup root, `sets/` and both pools (`lib.sh: access_beyond_owner`) —
`confidentiality note` for group, `CONFIDENTIALITY WARNING` for other bits or an
ACL — and `--require-private-destination` / `JURISTID_BACKUP_REQUIRE_PRIVATE=1`
refuses. Tightening what already exists is a one-time, targeted operator step
(deploy/unraid-main/RECOVERY.md, «Backup confidentiality»), never a recursive
chmod a nightly run does by itself. Modes are in no digest, inventory or count, so
set integrity, membership, retention, level-2 verification, the restore and the
CI recovery rehearsal are unchanged. Off-host disaster recovery is out of scope.

**Amendment (r96 integration, 2026-11-26).** One reader of a set is not root: the
Lovable lane's one-off containers (sync and OneNote import) check the proof of the
set they were given as the application's account (10001), read-only at
`/backup-set` (`lovable_sync.backup_proof_problem`). With the set 0700/0600 every
APPLY and import would have been refused (`PROOF_MISSING`). After verification,
`juristid-lovable-sync.sh` therefore gives exactly the set directory (0750) and
`VERIFIED-L2`, `SHA256SUMS` and `database.dump` (0640) to group 10001
(`lib.sh: share_set_proof`) — never to others, nothing else in the set. That
account already holds the database's credentials, so nothing is exposed it could
not read. Tests: `tests/test_backup_confidentiality.py` (the r96 section).

**Amendment (r96 integration): the MCP authenticator's enrolment link.**
`mcp_access.views.enrol` keeps a link's proof in the session like the account
links do, so its arrival now rotates the identifier and the CSRF secret too
(`local_views.rotate_session`); a gate session planted beforehand no longer reaches
the address step or the secret. Not changed there: a link arriving in front of the
gate still travels in the gate's `next` (as before r96), and a link that opens
nothing still writes its proof (the page then answers 400). Test:
`tests/test_mcp_access_enrolment.py::test_a_planted_gate_session_holds_no_enrolment_link`.

### 5. The shared gate's persona risk is accepted for now (owner deferral)

Somebody who knows the department password can choose any persona, including
the department head's, and act with that persona's business permissions. The
scan reports this, and it is **not a false positive and not resolved**. The owner
decided on 26.11.2026 that during controlled testing the shared password and
persona selection stay exactly as they are: no individual authentication is made
mandatory, Cloudflare Access and personal sign-in stay off, persona switching is
not restricted. It is recorded as an **explicitly accepted, temporary residual
risk**, bounded by what already holds: the gate is the only door (no data before
it); administration needs a verified administrator session with a personal
password and authenticator (ADR 0186), never a persona; the audit records the
persona and `SHARED_SECRET` assurance, never a principal; rotating the shared
password ends every session it opened (ADR 0183). It ends when an individual
authenticator is activated (ADR 0145 or Access), which §1 and §2 prepare. No
correction in this record weakens any of those boundaries.

## Consequences

- No migration, no setting, no new dependency. The dormant modes are exercised
  only by tests that set the mode in their own settings fixture.
- A well-formed link that opens nothing no longer creates a session row before
  the gate (it used to until the page answered), and its throttle charge moves
  from the page to the link's arrival.
- A browser that followed a link gets a new session cookie and CSRF cookie; an
  open form from before the link is refused once (403) and works after a reload.
- The nightly backup log gains confidentiality lines; with the destination as it
  is today they may be warnings until the operator step is done.

## Deployment and rollback

The code ships in the image; `scripts/deploy/juristid-backup.sh` and `lib.sh` are
read from the host checkout the nightly cycle runs (`juristid-backup-nightly.sh
--repo`), so the backup change takes effect when that checkout is moved to the
release. No cron line changes. Rolling back is the previous image and checkout:
sets written by either version verify and restore under either, Access sessions
without a marker are re-established on the next request, and no data changed.
