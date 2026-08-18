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

**Inheritance, by derivation rather than validation**

A child record (`app.core.models.VisibilityInheritingModel`) carries:

- `visibility_override` — the only user-settable field, and only ever *more*
  restrictive;
- `effective_visibility` — derived on every save as
  `max(parent.visibility, override)`, not editable.

Because it is derived, a child cannot end up weaker than its Matter even if
someone sets the override to `NORMAL`. A check constraint additionally forbids
the row-level combination `override = RESTRICTED, effective = NORMAL`.

Changing a Matter's visibility runs `set_matter_visibility()`, which re-derives
every child. Tightening a Matter tightens everything; relaxing it leaves
individually restricted children restricted.

**Why a derived column rather than a cross-table trigger.** A trigger comparing
against the parent row would also be correct, but the derivation removes the
failure mode entirely rather than catching it, and it keeps the rule in one
readable place. Audit append-only behaviour and evidence immutability *do* use
triggers, because there the database is the only place that can guarantee them.

## Consequences

- Child queries never need to join the Matter to decide the common case: a
  `NORMAL` child implies a `NORMAL` Matter.
- Every new child model must subclass `VisibilityInheritingModel` and implement
  `parent_visibility()`. That is the review checklist item for Stage 1.
- Bulk `UPDATE`/`bulk_create` on child tables bypasses the derivation; services
  must not use them for visibility-bearing writes.

## Reversibility

Low, by design. Adding a role to the restricted list is a one-line change with
an accompanying test; changing the inheritance model is not.
