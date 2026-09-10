# ADR 0068 — The operational snapshot records which step it photographed

- Status: accepted
- Date: 2026-09-10
- Stage: pilot (shared-gate phase)
- Related: ADR 0005 (authorization and visibility inheritance), ADR 0013 (the
  search projection and why visibility is derived live), ADR 0017 (statistics
  and operational snapshots), ADR 0038 (child visibility in projections),
  ADR 0042 (department-wide lawyer access)

## Context

`OperationalMatterSnapshot` is the one table in the product that stores derived
history rather than answering from canonical tables, and it exists for one
question those tables cannot answer: *how many active Matters had no next action
last March?*

Each row copies three facts off the Matter's open `NextAction` — `kind`,
`date_semantics` and `target_date`. The capture is deliberately unauthorized:
`snapshot_population()` says so, because a nightly command that photographed
only what its runner could see would make the department's history depend on who
ran the cron. Authorization was to happen on the way out.

It did not, completely. `visible_snapshots(viewer)` scoped rows with
`matter_visibility_q(..., prefix="matter__")` — **the Matter's visibility, and
nothing else**. `NextAction` is a `VisibilityInheritingModel`: a step can carry
its own `visibility_override` and be RESTRICTED below a Matter the whole
department reads. So the three copied columns were a projection of a child
authorized by its parent, which is precisely what ADR 0038 forbids:

> a projection may never be broader than its source

This is F-4 of the 2026-09-09 restricted-data leakage audit, the one finding
that audit left open. It was latent — `visible_snapshots` had no callers, so
nothing rendered the columns and there was no observable to write a two-world
regression test against. It becomes a real disclosure on the day a surface reads
the table, which is why it is closed before that surface exists rather than
after.

## Decision

**Blank the columns on read; never drop the row.** The same audit's F-2 found
five populations that dropped a *visible* Matter the moment a restricted child
appeared, and established that this is itself the disclosure: a reader who
watches a named file leave a list learns that restricted work happened on it. A
row that stays with three empty columns discloses nothing, because it is the
same shape as a day on which the Matter genuinely had no next action. That
indistinguishability is the property, not a side effect — it is what stops the
table being an existence oracle.

**Decide the blanking from the step's identity, derived live.**
`OperationalMatterSnapshot` gains one nullable `next_action` foreign key
(`SET_NULL`), and `visible_snapshots` asks `child_visibility_q` — unchanged, the
same predicate `NextAction.objects.visible_to` uses everywhere else — of the
**live** row it points at. Restricting a step therefore blanks it out of every
photograph already taken, on the next query, with nothing stored being
rewritten.

The blanking is expressed in SQL, as `Case`/`When` annotations
(`visible_next_action_kind`, `visible_next_action_date_semantics`,
`visible_next_action_date`, beside a `next_action_is_visible` flag), because the
first surface to read this table will be a chart. A blanking applied in Python
after the rows arrive would be right in the template and silently wrong in the
`values().annotate()` beneath it. `next_action_facts()` on the model prefers the
annotations, so `has_next_action` and `was_overdue()` on a row read through the
selector are scoped too, and a template cannot walk around the selector by
asking the instance.

The predicate is asked as an `Exists` subquery rather than through the foreign
key. Its participation half reaches through `collaborators`, and a second
many-to-many join inside an annotation multiplies rows behind the `DISTINCT`
that `apply()` has already applied — the fan-out `scoped_count()` exists to warn
about. A subquery cannot fan out.

## Alternatives considered

**Store the action's own visibility beside the copied facts.** One column, no
subquery, and rejected: it goes stale in the fail-open direction. A lawyer who
decides on Tuesday that Monday's step was sensitive restricts the live action,
and every photograph taken before that moment keeps publishing its kind and its
date with nothing on screen looking wrong. That is this same defect moved a day
later, and it is the failure ADR 0005 removed a stored visibility column to be
rid of. `VisibilityInheritingModel` says it in its own docstring: the effective
visibility is never stored, because any write that bypasses the service —
`update()`, a data migration, a shell session — leaves a value that reads as
*less* restrictive than the truth.

**Scope the capture.** Rejected by the shape of the table. A photograph taken
through a reader's eyes is a photograph of that reader, and a history whose
content depends on who ran the cron is not a history. This is stated in
`snapshot_population()` and remains true.

**Blank for every reader outside `ROLES_WITH_RESTRICTED_ACCESS`.** Hole-free and
needs no column, but it blanks the next-action columns wholesale for the
shared-gate `DepartmentViewer` — the likeliest first consumer of an operational
trend. Complying by showing nothing is a quiet way of not solving the problem.

**Probe the live table for *any* restricted step on the Matter.** No column, and
more precise than the previous option, but it blanks a Matter's whole history
because of one step taken on one day, and it has a hole the pointer does not: a
step hard-deleted out from under the row takes the evidence of its own
restriction with it, and the copied facts become readable.

## Consequences

`migrations: reporting/0002_operationalmattersnapshot_next_action` — one
nullable foreign key. **No data migration**, and deliberately no backfill: rows
captured before it cannot say which step they copied, because nothing recorded
the answer. Their next-action facts blank for any reader who does not already
see every restricted child, which is the safe direction, and manufacturing the
missing answer is the invented history this module refuses on principle
(Stage-2E brief 52). Production holds a small number of such rows and no reader
of them, so the practical cost is nil.

Confidentiality does not depend on the migration having run: a row with a null
pointer blanks. The migration buys back the *usefulness* of the columns for
non-privileged readers going forward, not their safety.

`capture()` writes one extra foreign key per row and is otherwise unchanged. It
stays unauthorized, and the docstring now says what that costs and where the
cost is paid.

No role semantics change, no write-authorization change, no change to any
rendered surface — `visible_snapshots` still has no callers. This closes the
last open finding of the 2026-09-09 audit.
