# 0096 — `Uus teema` is the master, and a Teema can be deleted

**Status:** accepted
**Date:** 2026-09-20

Four corrections from the owner's live audit of the Teema screens. Three are
about what the two pages look like and what they ask; the fourth adds an
operation the product has never had.

1. **`Uus teema` is the canonical design.** For every fact that exists on both
   the creation and the correction screen, `Muuda teemat` uses the master's
   label, control, vocabulary, `Muu` rule and order — and where the markup would
   otherwise exist twice, both pages include one partial.
2. **`Valdkonnad` and `Hetkeseis` are visible chips again.** This **reverses
   §2 of ADR 0094**, which made them menus.
3. **The ordinary Teema UI no longer exposes `Nähtavus`.** Not on `Muuda
   teemat`, not in the Teema header, not on `Saabunud`, and not through the
   inline-edit endpoint.
4. **A Teema can be deleted**, by the people who may edit it, through a
   confirmation page — and the deletion is a real purge with a documented
   residue rather than a flag.

Packages 1–3 need no migration. Package 4 adds two nullable columns and one
`choices` list entry, and touches no existing row.

---

## 1 — `Uus teema` is the master

### Context

`Uus teema` and `Muuda teemat` ask about the same record. They have been
improved separately, round after round, each time correctly and each time in
isolation: ADR 0073 unified the organisation picker across them, post-QA R2-07
moved `Muu valdkond` on the edit page for a reason that was true of the edit
page, ADR 0090 removed `Menetlusliik` and `Adressaat` from creation, ADR 0094
turned two creation controls into menus. Nothing in that history is a mistake.

The accumulation is. By the time the owner looked at the two screens together
they disagreed about the label on `Matter.brief_summary`, about whether
`Õigusakt`'s free text lives inside the fieldset or below it, about whether
`Valdkonnad` offers a `Muu` chip, about the order of the fields and about what
kind of control two of the vocabularies are. Each difference had a reason; the
*set* of them had none.

### Decision

`Uus teema` is the master. It is a direction rather than a resemblance, so that
a future disagreement between the two screens has an answer without anybody
having to reconstruct which of two rounds was later.

`Muuda teemat` may differ from it in exactly three ways, and each is a
consequence of the record already existing:

* **values arrive pre-filled**, including vocabulary rows the department has
  since withdrawn, which are offered back and marked «kasutusest väljas»;
* **three facts are answered there and nowhere else** — `Menetlusliik` and
  `Kellele`, which ADR 0090 §4 and §5 deliberately removed from creation, and
  `Sildid`, which is a classification applied to a record that exists. They
  stand below the master's own questions under the heading «Ainult olemasoleva
  teema kohta», so a reader meets the difference as a rule;
* **`Märkmed` is absent**, because the private scratch pad is
  `MatterPersonalNote` and is written where the thought occurs.

Three new partials carry the shared markup:
`templates/matters/partials/valdkonnad_field.html`, `hetkeseis_field.html` and
`oigusakt_field.html`. `MatterCreateForm` and `MatterEditForm` both mix in
`PolicyAreaChoicesMixin` and `LegalInstrumentChoicesMixin`, and both build their
three classification fields from the same factory functions.

**What is not shared is the business operation.** Creating a Matter and
correcting one are different transactions with different rules; they keep their
own form classes and their own services. Only the questions and the controls are
the same.

### Consequences

`brief_summary` is «Millest teema räägib» on both pages, with the master's
prompt in the box and no help line under it. `Õigusakt`'s free text is inside
the fieldset on both, revealed by the `Muu` chips and rendered open server-side
when one is ticked. The dates sit where the master puts them: `Saabus` above the
people, `Arvamuse tähtaeg` as the last of the shared questions.

`Muuda teemat` gains a `Muu` chip for `Valdkonnad` — see §2.

## 2 — `Valdkonnad` and `Hetkeseis` are visible chips

### Context

ADR 0094 §2 made both controls `chipmenu`s: a pill carrying the answer and a
panel taken out of flow, so that opening one overlaid the form rather than
lengthening it. The reasoning was sound and the implementation did what it
promised.

The owner's audit of the live page rejects the shape. A classification a lawyer
can read without opening anything is what `Õigusakt` — directly beneath the two
of them — has always been, and two vocabularies drawn as menus above a third
drawn as chips is one screen asking one kind of question in two kinds of way.

### Decision

Both controls go back to being a `<fieldset>` with a visible `<legend>` and a
chip row, exactly as `Õigusakt` is. No disclosure at all: the option of an
accordion open by default was available and rejected, because a control nobody
ever collapses is a control with a collapse affordance for nothing.

The `chipmenu` primitive is **removed**, not left unused — its CSS,
`bindChipMenus`, `bindChipSummaries`, `MatterCreateForm.policy_area_chosen_count`
and `MatterCreateForm.stage_summary` all go with it.

Nothing about the data moves. `Valdkonnad` is still checkboxes over
`selectable_policy_areas()`, `Hetkeseis` is still one radio group with a named
«Määramata», the per-stage explanations are still a `.stagehelp` sibling reached
through `aria-describedby`, and the retired-vocabulary rules of ADR 0032
§Amendment are untouched.

**`Muu valdkond` is now asked the same way on both pages**, which is the one
substantive change this reversal makes possible. Post-QA R2-07 gave the edit
page an always-visible box and no `Muu` chip, on the argument that a value
hidden behind a chip is a value a reader cannot check — an argument about a
*menu*: while the vocabulary was folded away, the chip that reveals the box was
folded away with it. Drawn at rest, the chip renders ticked and the box renders
open whenever the Matter holds a free-text area, server-side, so nothing is
hidden and one rule (`clean_policy_area_answer`) governs both pages: free text
belongs to the chip that reveals it, and `Muu` without text is refused.

## 3 — the ordinary Teema UI does not expose `Nähtavus`

### Context

Matter visibility has had a control on the creation screen, on the intake
screen, on the Teema header and on the edit page at various times. It has been
removed from creation twice (Uus teema brief 21) and has reappeared beside
`Sildid` on the edit page, where the only thing it could do was be answered by
mistake — and answering it wrongly is the one mistake on that page that takes a
file away from a colleague rather than mis-describing it.

### Decision

The ordinary lawyer-facing Teema product does not ask who may see a Matter, on
any surface. `visibility` is removed from `MatterEditForm`, `MatterFieldForm`
and `IncomingIntakeForm`; from `matter_edit.html`, `intake.html` and the Teema
header's ⋯ menu; and from `FIELD_SERVICES`, so `matters:update_field` answers
404 for it.

**The field is gone, not hidden.** There is no hidden input and no
`clean_visibility`, so a crafted `visibility=RESTRICTED` posted to the ordinary
edit route or to intake binds to nothing.

### What is deliberately untouched

`Matter.visibility`, the `Visibility` enum, every stored restricted record, the
`VisibilityInheritingModel` child rules, `visibility_override` on child records,
document restriction, `matter_visibility_q` and the whole authorization layer,
the restricted banner and badge that *state* a Matter's visibility, the
`MATTER_VISIBILITY_CHANGED` event, and `app.matters.services.set_matter_visibility`
itself. This is a product removal from the business UI, not an architectural one.

The cost is stated rather than argued away: material that has to be restricted
is filed NORMAL and restricted afterwards by somebody who decides it
deliberately, and it is department-wide in between. That window is the price of
the decision — the objection `templates/matters/intake.html` recorded as DS-15
is overruled, not answered.

## 4 — `Kustuta teema`

### 4.1 What the schema permits

Three facts, measured from Django's own metadata rather than assumed:

* **The `Matter` row cannot be removed.** `audit.ChangeEvent.matter` is
  `PROTECT`, and `audit_changeevent` carries a `BEFORE UPDATE OR DELETE` trigger
  (`audit/migrations/0002`). Every Matter has audit rows from `MATTER_CREATED`
  onwards. `matter.delete()` raises `ProtectedError`; nulling the pointer raises
  `restrict_violation`. Both doors are shut and neither may be opened — dropping
  the trigger, or making the model mutable for the duration, would trade the
  audit guarantee for a delete button.
* **An `Entry` that was ever corrected cannot be removed.**
  `matters.EntryRevision` is append-only in the database and hangs off `Entry`
  under `CASCADE`, so deleting such an entry issues a `DELETE` the trigger
  refuses.
* **Everything else can go**, including the `PROTECT` chains through `Document`
  → `DocumentVersion` → `DocumentDerivative` → `DocumentTextFragment`, provided
  the rows are removed in an order that puts every `PROTECT` holder before what
  it protects.

### 4.2 Decision — a purge with a tombstone

`app/matters/deletion.py` walks the ownership graph from one Matter — *reverse*
relations only, never following a foreign key outwards, so `Organisation`,
`Tag`, `PolicyArea`, `LegalInstrumentType` and the whole archive corpus are out
of the set by construction — and removes every owned row the database permits,
in a topological order computed from the `PROTECT`/`RESTRICT` edges.

What survives is the `Matter` row with `deleted_at` set, its `ChangeEvent`
history, and any `ImportRowLedger`. All three are append-only rows that point at
the Matter itself: they are the audit proof the architecture requires, and
nothing else remains.

**A second `Matter` is never owned.** The walk refuses to follow
`Matter.superseded_by`, and a Matter superseded by this one is a blocker.

**The tombstone is invisible by construction.** `Matter.objects` is a manager
that excludes `deleted_at__isnull=False`, and `Matter.all_objects` is the
unfiltered one the deletion machinery uses. The exclusion is in the manager
precisely because it must not be a rule several hundred call sites have to
remember: the register, search, reporting, the department pages, work items and
every selector not yet written fail closed without being touched. Django uses
`_base_manager` to follow a forward foreign key, so `ChangeEvent.matter` still
resolves for the audit trail.

### 4.3 The shape of the operation

A route of its own — `GET /teemad/<pk>/kustuta/` asks, `POST` acts — and never a
second button on the form that saves. No `confirm()`: a browser dialog cannot
name the record, is dismissed by the same reflex as every other dialog, and is
absent for anybody driving the page without scripting. The confirmation page
names the Teema, counts what is inside it, states that the act cannot be undone,
and offers `Loobu` as an equal alternative. CSRF as on every other POST.

After a successful deletion the reader is redirected to `/teemad/` with «Teema
kustutati.» — never to the Matter's own address, which now answers 404.

### 4.4 Refusals

Deletion refuses, in full and before the first row is removed, when:

* a `Document` under the Matter is under a **legal hold**;
* an **append-only row hangs off a business child** — today, a corrected
  `Entry`. This is a refusal rather than a residue, deliberately: a tombstone
  may keep the audit proof the architecture requires, and may not keep ordinary
  child business records in order to look complete;
* **another Matter names this one** as its successor;
* a row **outside** the Matter points into the owned set under any deletion
  behaviour — a cascade would destroy it, a `PROTECT` would abort halfway, and a
  cleared pointer would change it with nothing failing;
* an owned row **straddles the boundary**, holding a forward key into an owned
  model whose target is outside the set. `documents.EmailAttachmentLink` is the
  concrete case.

`Matter`-valued keys are exempt from the straddle test. `MatterRelation` and
`RelatedSuggestionDismissal` exist to name two Matters and are reached from
either end; deleting the row that says two files are related is the correct
consequence of one of them ceasing to exist.

The plan is rebuilt under the row lock inside the deleting transaction, because
the one rendered on the confirmation page was true when it was drawn.

### 4.5 Authorization

The same cohort that may open `Muuda teemat`: `business_write_required` —
SPECIALIST and DEPARTMENT_HEAD — plus `get_visible_matter`, so a restricted
Matter is a 404 to somebody who may not see it rather than a refusal confirming
it exists. No narrower permission was invented. The product asked for the
application's users to be able to delete a Teema, closing a Matter is already
this cohort, and the only narrower set in the codebase
(`ROLES_WITH_WORK_VICTORY_REVIEW`) is about claiming influence rather than about
data. Hiding the button is presentation; the route re-authorises and re-checks
every blocker.

### 4.6 Concurrency, evidence and search

The Matter is taken `FOR UPDATE NO KEY` — the strength every write in this
codebase takes on a Matter, per the lock-order discipline in
`app/matters/locks.py` — and the owned rows are removed underneath it. A writer
that already held the lock finishes first and its rows are then deleted; a
writer that arrives afterwards asks `Matter.objects` for a row the default
manager no longer returns and is refused by the surface it arrived at. A second
delete of the same Matter is a no-op rather than an error.

> **Amended by ADR 0111 (2026-09-24).** That held for writers of *this* Matter,
> not for writers of another Matter creating a pointer at it — a `Järglane`, a
> relation, a dismissal, a background citation — which locked only their own
> Matter and could commit alongside the deletion (ENG-073). Those writers now
> lock both Matters in ascending-id order and refuse a deleted counterpart.

**Evidence bytes are not deleted inside the transaction.** They live outside
PostgreSQL and cannot be rolled back, so removing them before the commit would
destroy evidence a rollback then claims still exists. After the commit the
objects are by definition unreferenced, which is exactly what
`prune_orphaned_evidence` finds and removes; a best-effort delete runs on commit
and a failure is logged, costs disk and nothing else, and is reclaimed by the
pruner. That is the repository's established pattern and not a new one.

**Search needs no rebuild and `INDEX_VERSION` does not move.** `SearchDocument`
rows are owned and are removed in the same operation, so a deleted Matter leaves
search immediately; and the projection is built from `Matter.objects`, so a
later rebuild cannot bring it back. Nothing about what is *eligible* for the
index changed, and bumping the version would order a production rebuild for no
reason.

### 4.7 The migration

`matters/0032_matter_deletion_tombstone` adds `deleted_at` and `deleted_by`,
both nullable, and records the manager and `Meta` changes. `audit/0025_matter_deleted_event`
adds `MATTER_DELETED` to a `choices` list, which is Python metadata. Neither
reads, writes or rewrites a row; every existing Matter keeps `deleted_at IS
NULL`, which is what "live" means.

---

## Superseded and amended

* **ADR 0094 §2** — `Valdkonnad` and `Hetkeseis` as menus. Reversed by §2 above.
  The rest of ADR 0094 stands: `Menetluse link` open on arrival, `Arvamuse
  tähtaeg` as the one date, `Järgmiseks` gone from creation.
* **Post-QA R2-07** — `Muu valdkond` as an always-visible box with no chip on
  `Muuda teemat`. Superseded by §2 above, on the grounds that the objection it
  answered was specific to a folded vocabulary.
* **`docs/design-v2-compatibility.md` DS-15** — `Nähtavus` kept on the intake
  screen. Overruled by §3 above, with the cost stated.
* **ADR 0090 §4, §5** — `Menetlusliik` and `Adressaat` absent from creation.
  **Unchanged**, and §1 above states them as the reason those two fields are
  edit-only rather than drift.

## Alternatives rejected

**An accordion open by default** for `Valdkonnad` and `Hetkeseis`. Permitted by
the brief and rejected: a control nobody ever collapses carries a collapse
affordance for nothing, and it would leave the three classifications as two
kinds of thing again.

**Hiding `Nähtavus` with CSS, or keeping a hidden input.** Refused outright. A
control that is not drawn but still bound is an accepted POST parameter behind
no control at all, which is the shape the removal exists to close.

**Dropping or conditionally relaxing the append-only triggers** so that
`matter.delete()` could succeed. This is the only way to make deletion remove
the `Matter` row, and it trades an architectural guarantee the whole audit model
rests on for a tidier row count. Refused.

**Making `EntryRevision.entry` nullable with `SET_NULL`, or dropping its FK
constraint.** Both would let a corrected `Entry` be deleted. The first is an
`UPDATE` the trigger refuses; the second replaces referential integrity with a
dangling identifier. Refused — the Matter is refused instead, with the reason
said out loud.

**Leaving the corrected `Entry` behind under the tombstone.** Rejected as
exactly the thing a tombstone may not be: an ordinary child business record kept
so that a deletion looks complete.

**A `Sulge teema` in deletion's clothing.** The product asked for deletion and
closure already exists; substituting one for the other would be answering a
different question.
