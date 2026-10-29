# ADR 0180 — Audit rows record how their actor arrived, and one-time links are serialised and epoch-bound

- Status: Proposed (security remediation of 2026-10-22, findings csf_2a1004b90bbd9b1e5be3ba77 and csf_42d9970ac23d6f55f4b19163)
- Amends: 0016 (shared gate), 0145 §7 (one-time links)
- Does not change: 0034 (persona candidates), the shared gate, persona selection, roles and capabilities

## Context

Under `AUTH_MODE=shared_gate` one department password opens the door and a
persona is then **picked from a list**. `ChangeEvent.actor` and
`SecurityAuditEvent.actor` hold that persona. The gate and persona events
already carried `authenticated_via` in their `detail`; ordinary business rows —
a stage change, a note, a deletion — carried nothing, so «Marko» on a history
row read as «Marko, personally verified, did this», which the deployment cannot
support.

Separately, `tokens.issue` ran invalidate-then-insert without serialising two
issues for one person, and the password-reset mail job — run after the response,
possibly much later — minted a link without asking whether the account's
credentials had changed since the request.

## Decision

1. **Provenance on every new audit row, written centrally.** `ChangeEvent` and
   `SecurityAuditEvent` gain `auth_method`, `auth_assurance`,
   `authenticated_principal`, `acting_persona` and `correlation_id` (abstract
   `AuthProvenance`, migration `audit/0038`). `record_change_event` and
   `record_security_event` fill them from `app.audit.provenance.current_provenance()`;
   no caller passes them and no caller can forget them.
2. **Derived from the session at the moment of writing.** `AuditContextMiddleware`
   (right after `SessionMiddleware`) keeps the request in a `ContextVar` and gives
   it one `correlation_id`; the provenance is read when a row is written, so a
   gate pass, a persona choice or a sign-in in the same request is described as
   it then was.

   | Mode / context | method | assurance | principal | persona |
   |---|---|---|---|---|
   | shared gate, before the password | `shared_gate` | `none` | — | — |
   | shared gate, behind it | `shared_gate` | `shared_secret` | **never** | the chosen persona |
   | personal password (dormant) | `local_password` | `individual_password` / `individual_mfa` | the account | the account |
   | Cloudflare Access | `cloudflare_access` | `external_idp` | the account | the account |
   | `AUTH_MODE=none` (dev sign-in) | `dev_login` | `none` | — | the synthetic account |
   | no request (command, import, job) | `system` | `system` | — | — |

   `actor` is unchanged: workflow association, ownership and every query that
   reads it behave exactly as before. A background job queued by a request runs
   as `system` under that request's `correlation_id` (`provenance.correlated`);
   outside both, the composer `operation_id` is the correlation if there is one.
3. **History is not rewritten.** Rows written before this keep empty values —
   «not recorded». No migration backfills or infers an authentication method.
4. **History does not claim personal verification.** Where a history list names
   the actor of a shared-gate row it says the name was chosen:
   «Marko Mets (valitud ühisparooliga)» in `Kõik muudatused` and in Haldus →
   Kasutajad → Ajalugu; in the compact `Teema käik` lines and the Muudatused
   page's initials the same words are in the name's tooltip (and the screen
   reader text on the initials). Old rows render as they did. Business-record
   authorship (`Entry.author`, `Submission.sent_by`, an owner) is workflow
   association and is not an audit claim; it is unchanged.
5. **One-time links are serialised and bound to the account's credentials.**
   `tokens.issue` locks the account row before invalidating and inserting, and a
   partial unique index `accounts_token_one_live_per_purpose` (unused and not
   invalidated) allows one live link per person and purpose (migration
   `accounts/0005`, which first invalidates — never deletes — any older
   duplicate). Each link stores `credential_stamp`, a digest of the account's
   password hash, login address and active flag at issue; `peek` and `consume`
   refuse a link whose stamp is no longer the account's, and `consume` locks the
   account before the link, the order `issue` uses. The reset job re-checks
   eligibility and the stamp the request saw under the lock and issues nothing
   otherwise. Deliberately **not** the session `security_epoch`: that also moves
   on every role and permission change, and an administrator adjusting a new
   colleague's rights before they activate must not kill the invitation. A
   derived stamp also cannot be forgotten by a future code path that changes a
   password.

   **The message is sent after the commit, never under a lock.** The reset job
   and `send_setup_link` decide and issue under the locks, commit, and only then
   hold the SMTP conversation (up to the transport's timeout) — so a slow mail
   server holds up neither the person's sign-in nor other administration
   changes. A password change committed while the message is in flight kills
   the link by its stamp. A message that does not leave invalidates its own
   link (only that one); an invitation becomes INVITED afterwards only if the
   message left and its link is still live. `bootstrap_first_administrator`
   alone still sends inside its transaction, because a failed send must roll
   back the whole appointment, on an account nobody can sign in to yet.

## Accepted risk recorded (finding #1, csf_8dbe9b434cfd0c1d12e79d27)

Choosing a persona behind the shared gate — including the department head's —
**does not verify individual identity**, and the chosen persona keeps its role's
capabilities. That is the accepted pilot design and is not changed here; this
record only makes the audit trail say so. Personal sign-in (0145) stays dormant.

## Consequences

- Every new row costs one `ContextVar` read and up to two session reads; no
  extra query.
- Readers that want «who really acted» must read `auth_method` and
  `authenticated_principal`, never `actor` alone.
- No setting, environment variable or Compose change. Two additive migrations.

## Status update 2026-10-29 — merged and deployed

This section records what happened after the decision; it does not amend the decision, and the status is unchanged (no formal acceptance was recorded).

- **Merged** in the security remediation integration (merge `9a2e834a, #514 = #506–#513, 2026-10-22`; #510, migrations `audit/0038`, `accounts/0005`).
- **Deployed** to production as `1270ca01` (2026-10-23, the release of #521, which carried the security baseline merged before it).
