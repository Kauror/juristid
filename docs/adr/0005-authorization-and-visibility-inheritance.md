# 0005 — Authorization and visibility inheritance

- **Status:** Accepted (Stage 0)
- **Date:** 2026-08-18

## Context

The specification calls one centralized authorization chokepoint and inherited
child visibility non-retrofittable. Restricted records must stay absent from
lists, search, snippets, counts, exports and downloads — not merely hidden on a
detail page.

## Decision

**One chokepoint**

`app/core/authorization.py` owns the rules. It exposes `scope_for_user(user)`
and the `Q` builders every queryset uses. No module writes its own visibility
condition. Reading Matters is `Matter.objects.visible_to(user)`; reading child
records is `<Model>.objects.visible_to(user)`.

**Two scopes**

`NORMAL` and `RESTRICTED`, ordered by restrictiveness in `app/core/enums.py`.
More scopes are not added without a specification change.

**Who may read RESTRICTED content**

1. the Matter owner;
2. an explicit collaborator on the Matter;
3. the `DEPARTMENT_HEAD` role;
4. a holder of a valid, unexpired, unrevoked `BreakGlassGrant`.

`ADMINISTRATOR` and `is_superuser` are deliberately **not** on that list.
Technical administration is not business access. Both cases are covered by
tests that fail if anyone widens the rule.

**Break-glass**

`accounts.BreakGlassGrant`: user, granter, written reason, start, expiry,
optional revocation. Maximum duration 24 hours, only the department head (or a
system owner) may grant one, and grant/revoke both write a
`SecurityAuditEvent`. It is emergency access, not a second permanent role.

**Inheritance by derivation, with nothing stored**

A child record (`app.core.models.VisibilityInheritingModel`) carries exactly one
visibility field: `visibility_override`, which may only add restriction.

The **effective** visibility — the more restrictive of the Matter's visibility
and the child's own override — is **never stored**. It is computed:

- in SQL, by `app.core.authorization.child_visibility_q` on every read, and by
  `effective_visibility_expression()` when a list needs to display it;
- in Python, by the `effective_visibility` property, for a single object.

*This supersedes an earlier Stage-0 design that stored the derived value in an
`effective_visibility` column maintained on save.* That column was a latent
authorization bug. It stayed correct only while every write went through
`set_matter_visibility()`, and anything that bypassed that service — a bulk
`update()`, a data migration, a shell session, a future importer, a Stage-1
service someone forgets to wire up — left a stale value behind. A stale copy of
this particular fact reads as *less* restrictive than the truth, which is a
confidentiality leak rather than a display glitch. Deriving it removes the
failure mode instead of guarding against it.

Consequently `set_matter_visibility()` no longer propagates anything; it exists
to record the ChangeEvent. A write that bypasses it changes what children are
visible just as correctly and immediately, and only misses the audit entry.

**Why not a cross-table trigger keeping the column in step?** That would also be
correct, and it is what a denormalised design would require. It is simply more
machinery than deriving the value, and it leaves a redundant column a future
migration could still get wrong. Audit append-only behaviour and evidence
immutability *do* use triggers, because there the database is the only place
that can guarantee them — there is nothing to derive.

**Cost.** Child reads join the Matter. At the specification's scale (12,000+
Matters, a few hundred thousand child rows) that is an indexed join on a foreign
key, and list queries annotate the value in the same query rather than querying
per row. If measurement ever shows this is the real bottleneck, the answer is
the materialised search projection that ADR 0006 already plans, not a
hand-maintained column on a transactional table.

## Consequences

- No write path can produce a visibility leak, because no write path maintains
  the value. `bulk_update`, `bulk_create`, raw SQL and data migrations are safe
  by construction, and tests prove it.
- Every new child model must subclass `VisibilityInheritingModel`, implement
  `parent_visibility()` and be read through its `visible_to()` queryset. That is
  the review checklist item for Stage 1.
- Reading one child's effective visibility touches its Matter. Lists use
  `with_effective_visibility()` or `select_related("matter")` rather than the
  property per row.

## Reversibility

Low, by design. Adding a role to the restricted list is a one-line change with
an accompanying test; changing the inheritance model is not.
