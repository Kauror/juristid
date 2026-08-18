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

- `entra_object_id` — nullable, unique, **immutable once assigned** (enforced in
  `save()`), reserved for the Entra `oid` claim;
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
