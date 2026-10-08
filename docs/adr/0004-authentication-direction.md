# 0004 — Authentication: synthetic local sign-in now, Entra ID for real data

- **Status:** Accepted (Stage 0)
- **Date:** 2026-08-18

## Context

Microsoft Entra ID is required from the Secure Pilot Gate onwards. Stage 0 must
be usable on a developer machine without any tenant, while making sure the user
table does not have to be rewritten when Entra arrives.

## Decision

**User model, from migration 0001**

`accounts.User` (`AbstractBaseUser` + `PermissionsMixin`) with:

- `entra_object_id` — nullable, unique, reserved for the Entra `oid` claim, and
  **immutable once assigned, enforced by a database trigger**
  (`accounts/migrations/0002`). The model's `save()` refuses the change too, but
  that guard is absent from `QuerySet.update()`, `bulk_update()`, a data
  migration or a shell session. Identity is the one fact that must not drift, so
  the rule lives where nothing can route around it. The initial NULL to UUID
  assignment stays permitted: that is exactly what the first Entra sign-in does
  to an account that already exists;
- `upn` as `USERNAME_FIELD`, normalised to lowercase;
- `display_name`, `email`, `role`, `is_active`, `is_staff`;
- `is_synthetic` — a development-only account, with a database constraint that
  a synthetic user can never carry an `entra_object_id`.

**Local development**

- `DEV_LOGIN_ENABLED` (default off) exposes `/konto/arendus-sisselogimine/`,
  which lists only `is_synthetic` users and signs one in without a password.
- Two system checks refuse to start a process where `DEV_LOGIN_ENABLED` is on
  with `DEBUG` off, or where it is combined with `REAL_DATA_ALLOWED`.
- Every sign-in attempt, successful or not, writes a `SecurityAuditEvent`.

**Production direction**

**Superseded on 2026-10-08 for the production direction — see the amendment at
the end of this document.** The planned individual authenticator is now personal
sign-in in this application (`AUTH_MODE=local_password`, ADR 0145), built dormant;
Entra ID is deferred, not rejected, and the column reserved for it below stands.

- OIDC against Entra ID, authorization-code flow with PKCE, using
  **`mozilla-django-oidc`** as the current candidate library: small, standard
  OIDC, no opinion about the rest of the stack.
- On first sign-in a user is matched by `entra_object_id`, falling back to
  `upn`; the object id is then written once and never changed.
- No local-password fallback in production. MFA and Conditional Access are
  configured in the tenant, not in this application.
- Role assignment source (Entra group claim versus locally administered `role`)
  is decided together with the department head before the Secure Pilot Gate.

**Not built now:** the OIDC integration itself. Building it before an actual
tenant, redirect URI and group model exists would be speculative. The
abstraction it needs — an immutable external identity column and a role field —
exists and is tested.

## Alternatives considered

- **`django-allauth` with the Microsoft provider** — capable, but a large
  surface for one identity provider.
- **MSAL directly** — most control, most code to own.
- **Building Entra auth in Stage 0** — rejected: no tenant, no app registration,
  and no way to test it honestly.

## Consequences

- Local development needs no Microsoft dependency at all.
- Switching on Entra is an additive migration (nothing to backfill) plus a
  backend.
- Synthetic accounts are structurally distinguishable from real ones.

## Reversibility

Library choice: high. The user model's Entra column: low, which is why it is in
migration 0001.

---

## Amendment, 2026-10-08 — personal sign-in is the planned individual authenticator; Entra ID waits

- Status: accepted, amending «Production direction» above
- Scope: which individual authenticator the real-data deployment is to move to,
  and whether this application may hold passwords; nothing about the user model

### What was decided before

That production would authenticate individuals through Entra ID over OIDC, that
there would be no local-password fallback, and that MFA and Conditional Access
would be the tenant's business rather than this application's.

### Why it is superseded

The owner decided on 2026-10-08 that the department's individual sign-in will be
a **personal password verified by this application, with a second factor**, and
placed Entra ID / Microsoft 365 single sign-on outside the current round. The
reason is practical rather than architectural: no tenant registration is in
scope, and the department needs to administer its own accounts — add a lawyer,
switch off one who left, appoint a second administrator — from the application
rather than from a host shell.

### What is decided now

ADR 0145. In short: a fourth `AUTH_MODE`, `local_password`; Argon2id passwords
under a NIST SP 800-63B-4 policy; TOTP as the second factor, mandatory for
account administrators; one-time activation and reset links; a capability layer
over the existing roles; an account administration page; and an operator-only
bootstrap for the first administrator. **All of it ships dormant**: production
stays on the shared gate until a separate, approved activation
(`docs/LOCAL_AUTH_ACTIVATION_RUNBOOK.md`). MFA for this mode is therefore this
application's responsibility, and it takes it.

### What this amendment does not change

- **The user model.** `entra_object_id` stays, nullable, unique and immutable —
  database trigger and `save()` guard both unchanged. An Entra mode added later
  would be a fifth `AUTH_MODE` value against the same table, with no rewrite.
- **`upn` as `USERNAME_FIELD`**, normalised to lower case. Under personal sign-in
  it is the login address.
- **Synthetic accounts** remain structurally distinguishable and can never carry
  an Entra identity. Neither individual authenticator — Cloudflare Access or
  personal sign-in — signs a synthetic account in on a real-data deployment.
- **The development sign-in** (`DEV_LOGIN_ENABLED`) is still refused outside
  `DEBUG`, beside real data, and beside any real authenticator.
- **Role assignment** is still the locally administered `role`, now with
  individual capabilities over it (ADR 0145 §3); an Entra group claim remains an
  option for whenever that mode is built.
