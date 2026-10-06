# 0138 — A completion record is never less restricted than the action it completes

**Status:** accepted
**Date:** 2026-10-04

A sibling of docs/adr/0137, one level up. 0137 says a *file* is never less
restricted than the record it is evidence for; this says the *record* written to
complete an action is never less restricted than that action. **No migrations**,
no new model or field, and **no existing row is rewritten**.

1. **`PRAEGUNE TEGEVUS` writes the completion `Entry` with the action's own
   restriction.** `complete_current_action` creates the `Entry` with
   `completion_visibility_override(current)`: `RESTRICTED` when the named
   `NextAction` carries `RESTRICTED`, empty otherwise.
2. **The rule lives in the caller that knows the relationship.** `add_entry`
   is unchanged: it is a primitive many paths use, and only
   `complete_current_action` knows that this `Entry` is the completion record of
   that exact action.
3. **Copied at creation, never joined, never backfilled.**

## Context

`complete_current_action` (`Mida tegid?`, docs/adr/0075 §3) is one operation:
the note saying what was done, its files, and the completion of the one open
`NextAction` the form named, re-read under the Matter's lock
(`_named_open_action`). The note is the account of the work done for that action.

It was written by `add_entry` with no override, so it inherited the Matter. A
`NextAction` restricted below a `NORMAL` Matter is hidden from a reader who may
see the Matter but not restricted records — but the `Entry` recording its
completion was not. Reproduced on `e0535ebb`: for that reader the action is not
in `NextAction.visible_to`, while the completion `Entry` is in
`Entry.visible_to`, is a row of `Teema käik`, prints its body on the Teema page,
and is a search hit. (The action's own sentence did not leak: the
`✓ Tehtud <step>` fold reads the action through its own visibility.)

**Why the Matter's visibility is not enough.** The Matter's answer is the
*widest* scope any child may have; a child restricted below it exists precisely
so that some Matter readers do not see it. A record derived from that child and
authorized by the Matter alone re-publishes the child to exactly the readers the
restriction excludes.

No panel offers a restriction today; the shell, the admin and importers write
one. Production holds no restricted `NextAction` (§5), so the gap was latent.

## Decision

### §1 The supported derivation — exactly one

`complete_current_action`: the `Entry` it creates gets
`completion_visibility_override(current)`, read off the `NextAction`'s own
`visibility_override` column and nothing else — not its text, owner, plan step,
date, files or the Matter. `NORMAL` or empty passes nothing, so the `Entry`
inherits the Matter exactly as before; a restricted Matter therefore needs no
redundant override on its children. An action linked to a `Tööplaan` step
(docs/adr/0133) is an ordinary `NextAction` and gets the same rule.

### §2 Copied at creation; fail closed

The restriction is copied onto the `Entry`'s own column at creation, the way
every `VisibilityInheritingModel` is authorized. It is not a live join: relaxing
the action later leaves the `Entry` restricted until somebody relaxes it, so an
error hides a record rather than exposing one. An `Entry` written before its
action was restricted is not rewritten either.

**It composes with 0137.** A file captured in the same save is evidence for the
`Entry`, so 0137 restricts it through the `Entry` this rule restricted: neither
the note nor its file reaches a reader who may not see the action.

### §3 Sibling paths reviewed — independent records are not derived

Every operation that completes a `NextAction` and writes a record in the same
save (`complete_next_action`'s five callers, all in `app/matters/workspace.py`):

| Path | Record written | Kind | Changed |
| --- | --- | --- | --- |
| `complete_current_action` | `Entry` (`Mida tegid?`) | **A — completion record of the action** | yes |
| `add_matter_engagement` from the current plan step | `MatterEngagement` | B — an independent consultation that also finishes the step | no |
| `add_engagement_feedback` with `complete_action_id` | none new — closes an existing round | B — the round has its own visibility | no |
| `add_matter_koda_opinion` with `complete_action_id` / from the step | `Submission` (+ its files) | B — a sent letter, a first-class fact | no |
| `add_matter_website_overview` from the current plan step | `MatterWebsiteOverview` | B — a publication, a first-class fact | no |

A category-B record is a separate professional fact that merely also finishes a
step. **It does not inherit the step's restriction — the owner's decision,
2026-10-04.** Restriction belongs to the information itself, not to every
workflow edge that led to it: a restricted internal task may well end in an
ordinary Chamber opinion, and the reverse. Each independent record keeps its
own visibility rule.

### §4 The next step chosen in the same save does not inherit

`complete_current_action` may also start the next step (a `Tööplaan` step or
`Muu tegevus`, docs/adr/0133 §4). That step is **new work**, not the record of
the work done, so **it does not inherit the restriction of the step before it —
the owner's decision, 2026-10-04.** It is written by the ordinary creation rule
(`set_next_action_for_new_work`, `start_checked_step`) with no override, and a
test pins that answer.

Editing the *same* step with `Muuda` is different: the replacement is the same
work and keeps its restriction (docs/adr/0139).

### §5 No backfill

No row is rewritten. The read-only inventory run on production on 2026-10-04
(`juristid-main`, `BEGIN READ ONLY … ROLLBACK`) found **zero** completion
entries of this shape and **zero** restricted `NextAction`s at all. The
relationship is proved only by a shared, non-null `ChangeEvent.operation_id`
between `NEXT_ACTION_COMPLETED` (`workflow.NextAction`) and `ENTRY_ADDED`
(`matters.Entry`) — never by text, time, author or proximity:

```sql
BEGIN READ ONLY;
SELECT na.id AS action_id, en.id AS entry_id, en.matter_id,
       m.visibility AS matter_visibility, en.removed_at IS NOT NULL AS entry_removed
FROM audit_changeevent done
JOIN audit_changeevent added
  ON added.operation_id = done.operation_id
 AND added.event_type = 'ENTRY_ADDED' AND added.object_type = 'matters.Entry'
JOIN workflow_nextaction na ON na.id = done.object_id
JOIN matters_entry en ON en.id = added.object_id
JOIN matters_matter m ON m.id = en.matter_id
WHERE done.event_type = 'NEXT_ACTION_COMPLETED'
  AND done.object_type = 'workflow.NextAction'
  AND done.operation_id IS NOT NULL
  AND na.visibility_override = 'RESTRICTED'
  AND en.visibility_override IN ('', 'NORMAL');
ROLLBACK;
```

Files on such an entry are linked to an *unrestricted* record, so 0137 §3's
query does not see them; join `documents_documentlink.entry_id` to the rows
above for those. A completion with no `operation_id` (written before operations
existed) cannot be tied to its `Entry` from stored relationships and is counted,
not guessed — also zero on production.

## Consequences

* A note recording the completion of a restricted step is never listed in
  `Teema käik`, printed, counted or found in search by a reader who may not see
  the step; with 0137, neither is its file.
* `tests/test_completion_entry_inherits_action_restriction.py` asserts it
  through the real operation, with the controls, copy semantics, the stale-step
  refusal, the plan-step case and §4's pinned answer.

## Decided since

* 2026-10-04, the owner: category-B records do **not** inherit a completed
  step's restriction (§3), and the next step does **not** inherit the
  restriction of the one it follows (§4). Neither question is open.
* A replacement of the same step by `Muuda` keeps its restriction
  (docs/adr/0139).

## Not decided here

* Rewriting existing entries (§5).
* A restriction control on `PRAEGUNE TEGEVUS` or any other panel.

## Amendment, 2026-10-06 — `Tööplaan` retired (docs/adr/0141)

The rows above about a record saved «from the current plan step» describe a
launch that no longer exists; those records now finish nothing. The rule for
the completion `Entry` is unchanged, including for an old action that still
names a plan step.
