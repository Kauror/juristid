# ADR 0183 — Credential changes end the sessions they opened, and signing out of Access stays signed out

- Status: Proposed (security hardening round 2, 2026-10-22; findings csf_19450e2c79870c38f835fdfb, csf_c366770b828e8fd3866de865, csf_92343c922fb9e450b8bb88d3)
- Amends: 0016 (shared gate), 0145 §8–§9 (second factor, sessions)
- Does not change: `AUTH_MODE` or its default, the shared gate and persona selection, the 12-hour absolute and 8-hour idle limits, 0034 (persona candidates), 0180 (audit provenance); personal sign-in, MFA and Cloudflare Access stay dormant

## Context

Three places where a credential changed and a session made under the old one
went on working.

1. **The shared gate** (csf_19450e2c). A gate session recorded only *when* the
   password was typed (`shared_gate:passed_at`). Rotating
   `JURISTID_SHARED_GATE_PASSWORD` changed who could open the door from then on
   and nothing about who was already through it: every open session — persona,
   HTMX writes, downloads — lasted until it aged out, up to twelve hours. The
   runbook's only immediate remedy was to rotate `DJANGO_SECRET_KEY` as well,
   which also resets every throttle.
2. **A pending second factor** (csf_c366770b, dormant `local_password`). A
   correct password with a code still to come left `{user, at}` in the session.
   A password reset or change, or a security-epoch bump, in the five minutes
   before the code did not touch it, so the attacker's half-finished sign-in
   stayed completable with a password the owner had already replaced.
3. **Cloudflare Access sign-out** (csf_92343c92, dormant). «Välju» flushed the
   local session; the next request carried the same signed assertion, which the
   middleware rightly verified, and signed the person straight back in.

## Decision

1. **A gate session records which password it was opened under.**
   `shared_gate.credential_version()` is `salted_hmac(purpose, password,
   sha256)` under `SECRET_KEY`, cut to 128 bits, stored as
   `shared_gate:credential` when the gate view has just verified the password.
   `has_passed` — the one check the middleware, the context processor, the
   decorators, the views and the audit provenance all use — also requires that
   value to equal the current one, compared in constant time.
   - Derived from configuration, so every worker agrees with no shared state
     and no in-memory cache. `_hash_for`'s PBKDF2 digest cannot serve: its salt
     is random per process.
   - Keyed and truncated, so the session table holds nothing the password could
     be guessed against offline; never the plaintext, never a hash of it.
   - **Mismatch ends the whole session.** The middleware records
     `SESSION_ENDED` (`path = shared_gate`, `reason = shared_password_changed`)
     once and calls `logout`, which flushes everything — persona, gate state,
     anything else — and the request goes to the gate (an HTMX request gets the
     usual sign-in 401, ENG-012). No ended session can be revived by rotating
     back.
   - **Persona changes carry the version; they never refresh it.**
     `current_pass()` returns `GatePass(passed_at, credential)` and
     `open_gate(carried=…)` writes back exactly what it was handed. Only the
     gate view, after verifying the password, opens with a fresh time and the
     current version. A carried pass with no version stays one that does not
     pass.
   - Sessions opened before this release carry no version and are treated as
     opened under an old password: everybody types the password once after the
     deploy. Grandfathering them would let a pre-release session survive a
     rotation made in the same maintenance window.
   - The absolute and idle limits are untouched; `SECRET_KEY` rotation is no
     longer needed to end gate sessions (it still ends them, as before).
2. **A pending second factor is bound to the account's credential state.**
   `_begin_pending` stores `binding = salted_hmac(purpose,
   tokens.credential_stamp(user) ∥ security_epoch)`: the password hash, login
   address and active flag (round 1's link stamp, 0180 §5) **and** the epoch,
   which is right here because the pending marker is session-level and the epoch
   also moves on a factor reset, a role or capability change and a
   deactivation. `pending_user` refuses a marker whose binding no longer
   matches (or has none), drops it and records `AUTHENTICATION_FAILED`
   (`stage = second_factor`, `reason = credentials_changed`); the person starts
   again from the password. `verify_second_factor` asks once more after the
   code, with the account read afresh, and signs in that fresh account. MFA is
   not enabled anywhere by this.
3. **Signing out under Access ends Access's session too, and stays signed out
   until it has.**
   - «Välju» records `SIGNED_OUT` (`path = cloudflare_access`), flushes the
     session and redirects to `cloudflare_access.logout_url()`: the team
     domain's `https://<team>.cloudflareaccess.com/cdn-cgi/access/logout`, built
     from `CF_ACCESS_TEAM_DOMAIN` only when that is a plain `https` host under
     `.cloudflareaccess.com` with no path, query, fragment, port or
     credentials; otherwise this host's own `/cdn-cgi/access/logout` (a path,
     which Cloudflare's edge answers). Nothing in the request influences it, so
     there is no open redirect.
   - The fresh session keeps a keyed fingerprint of the assertion it was signed
     out with (`cf_access:signed_out_assertion`). The middleware refuses to sign
     anybody in from that same assertion and renders a «Oled välja logitud» page
     offering the team logout and this host's logout — a page, not a redirect,
     so there is no loop. A different assertion (a new Access sign-in) signs in
     as usual and clears the marker. Verification and refusal of forged or
     foreign assertions are unchanged.
   - Rejected: `Clear-Site-Data: "cookies"` — it would clear the app's
     `CF_Authorization` cookie, but browsers apply it to the whole registrable
     domain, signing people out of every other `orgusaar.ee` service.

## What remains outside the application

- **Access.** Whether the team-domain logout also invalidates an already-issued
  *application* token is Cloudflare's behaviour, not ours. The local marker
  covers the signing-out browser either way; another browser holding the same
  token is not covered until Cloudflare ends it. Before activation, check once
  on the real Access application that after «Välju» the next visit asks for the
  Koja sign-in; if it does not, use the application-level logout (the page's
  second button) or shorten the Access application's session duration in
  Cloudflare One. No Cloudflare policy change is needed for the local part.
- **Pending sign-in race.** The binding is re-read after the code without a row
  lock; a credential change committed in the microseconds between that read and
  `login()` is still caught on the next request, because `login` stores the
  session hash of the account it read and Django compares it per request.

## Consequences

- No migration, no new setting, no environment or Compose change; no search
  rebuild.
- Deploying ends every open shared-gate session once (one password prompt per
  person). Rotating the shared password ends every open gate session at its
  next request; the runbook says so.
- Revocation is evaluated lazily, at a session's next request: a session opened
  under password A that made no request while B was configured would pass again
  if A were configured once more. The runbook therefore says never to rotate
  back to an earlier password (2026-10-22 review).
- A local-password deployment that rotates `SECRET_KEY` also abandons sign-ins
  waiting for their code (they were ended by the session hash already).

## Status update 2026-10-29 — merged and deployed

This section records what happened after the decision; it does not amend the decision, and the status is unchanged (no formal acceptance was recorded).

- **Merged** in security hardening round 2 (merge `b6b27abc, #519 = #515–#518, 2026-10-22`).
- **Deployed** to production as `1270ca01` (2026-10-23, the release of #521, which carried the security baseline merged before it).
