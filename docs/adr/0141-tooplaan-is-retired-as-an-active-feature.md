# 0141 — `Tööplaan` is retired as an active feature

**Status:** accepted
**Date:** 2026-10-06

The owner no longer wants `Tööplaan` in the product. The interactive feature of
docs/adr/0133 is removed; the data it wrote stays. **No migrations**, no data
migration, no backfill, and no existing row is rewritten or deleted.

## Decisions

1. **The workflow is the current action.** `PRAEGUNE TEGEVUS` → `✓ Tehtud` or
   `Muuda` → optionally `Uus hetkeseis` → optionally `Järgmine tegevus`
   (docs/adr/0140). With no step open, `+ Määra järgmine tegevus` writes one
   (docs/adr/0126 §1). Nothing is suggested: what comes next is what the lawyer
   writes.

2. **No plan on the Teema page.** The `TÖÖPLAAN` section and its rows,
   `Soovitus`, `+ Lisa samm`, `Muuda plaani`, `+ Lisa tavapärane tööplaan`,
   `Soovitatud järgmisena` / `Alusta` and the current typed step's own form
   under `PRAEGUNE TEGEVUS` are gone. The page runs `PRAEGUNE TEGEVUS` →
   `LISA TEEMALE` → `Tegevused`. `Menetluse kulg`'s own `+ Lisa samm`
   (docs/adr/0119) is a different feature — the external procedure's steps —
   and stays.

3. **No plan-step control in any form.** `+ Kaasamine`, `+ Koja arvamus` and
   `+ Ülevaade / uudis` lose `Tööplaani samm, mille see täidab`
   (`taidab_sammu`, docs/adr/0135) and the typed launch's hidden
   `plan_action` / `plan_step` (docs/adr/0133 §6). A value posted by a tab
   drawn before this change is ignored, not refused: the record saves and no
   step moves. `Märgi praegune tegevus tehtuks` on `+ Koja arvamus`
   (docs/adr/0126 §2) is not a plan control and stays.

4. **No plan side effects.** Nothing seeds a plan (`Uus teema`, `Saabunud`),
   starts a step, fulfils one with a saved record, or completes one:
   `complete_next_action` no longer touches the action's `plan_step`, `Muuda`
   no longer carries it, and `set_next_action` no longer writes it. No
   `PLAN_*` audit event is written any more.

5. **Historical data is dormant, not deleted.** `MatterPlanStep`, its
   constraints, `NextAction.plan_step`, the `PLAN_*` event types and their
   visibility rule stay in the schema; existing rows stay exactly as they
   are. An old open action that still names a step works as any other: it
   completes and is changed normally, its own row keeps the link, and the
   replacement or next action carries none. Nothing reads the rows to draw,
   count or suggest anything. `ENGAGEMENT_ADDED` and `WEBSITE_OVERVIEW_PUBLISHED`
   stay in `Teema käik`'s fold (docs/adr/0133 §7), so old rows read as before.

6. **Removed code.** `app/workflow/plan.py`, `app/matters/plan_view.py`, the
   eight `plaan/` routes and their views, `StartPlanStepForm`,
   `PlanRevisionForm`, `PlanMoveForm`, `PlanStepForm`, `PlanLaunchForm`,
   `drawn_under_the_current_step`, the plan templates (`work_plan.html`,
   `plan_editor.html`, `plan_step_edit.html`, `plan_step_fields.html`,
   `plan_fulfil_option.html`), the plan-step editor binding in `ux.js` and the
   plan CSS. A crafted POST to an old `plaan/` address answers 404.

## Not decided here

Whether the dormant schema — `MatterPlanStep`, `NextAction.plan_step`, the
`PLAN_*` choices — is eventually dropped is left to a later code-health round.
Dropping it is a migration and a decision about historical evidence, and this
round makes neither.
