# 0141 — `Tööplaan` is retired from view; `Soovitatud järgmisena` stays

**Status:** accepted
**Date:** 2026-10-06

The owner no longer wants `Tööplaan` as a visible or administered feature, but
keeps one lightweight part of it: a suggested next step under
`PRAEGUNE TEGEVUS`. **No migrations**, no data migration, no backfill, and no
existing row is rewritten or deleted.

## Decisions

1. **No visible plan.** The `TÖÖPLAAN` section and its rows, `Soovitus`,
   `+ Lisa samm`, `Muuda plaani` and its editor (move, skip, restore, repeat,
   edit), `+ Lisa tavapärane tööplaan` and the current typed step's own form
   under `PRAEGUNE TEGEVUS` are gone, with their routes. The page runs
   `PRAEGUNE TEGEVUS` → `LISA TEEMALE` → `Tegevused`. `Menetluse kulg`'s own
   `+ Lisa samm` (docs/adr/0119) is a different feature and stays.

2. **No plan control in any form.** `+ Kaasamine`, `+ Koja arvamus` and
   `+ Ülevaade / uudis` lose `taidab_sammu` (docs/adr/0135) and the typed
   launch's hidden `plan_action` / `plan_step` (docs/adr/0133 §6); those
   records never fulfil a step. A stale post carrying them is ignored.
   `Märgi praegune tegevus tehtuks` is not a plan control and stays.

3. **`Soovitatud järgmisena` stays.** The background source is the existing
   `MatterPlanStep` sequence, seeded from the code-managed standard template
   when a person files real work (`Uus teema`, `Saabunud`), never drawn as a
   plan. While this reader has no current step, the first step still ahead
   (suggested or planned, not completed, not dismissed) is suggested, with
   `Alusta` and `×`. With a current step, or none left, nothing is suggested;
   `+ Määra järgmine tegevus` is always the manual alternative.

4. **Start.** `Alusta` writes the Matter's one `NextAction` through
   `set_next_action_for_new_work` with `plan_step` set (`activate_plan_step`),
   refusing while another action is open or once the step is no longer ahead.

5. **Dismiss.** `×` (`Eemalda soovitus`) stores the exact step shown as
   `SKIPPED` (`skip_plan_step`): persistent, audited as `PLAN_STEP_SKIPPED`,
   and the next step is suggested at once. The page posts the step id and the
   plan revision it was drawn from, so a stale tab refuses rather than
   dismissing a different suggestion; the current step is never dismissed. No
   new table.

6. **Advance and edit.** Completing a step-linked action completes the step
   (`complete_next_action`), so the next is suggested. `Muuda` carries
   `plan_step` to the replacement row — the same work, like its restriction
   (docs/adr/0139) — so the edited action still advances. Never shown in UI.

7. **Manual work is not the suggestion.** `+ Määra järgmine tegevus` and a
   `Järgmine tegevus` written in `✓ Tehtud` create unlinked work; nothing is
   inferred from similar words. The suggestion stays until it is started or
   dismissed.

8. **Removed code.** The shape-editing use cases in `app/workflow/plan.py`
   (add, edit, move, restore, repeat, record fulfilment), `plan_view`'s plan
   rows (replaced by `recommendation_for`), the seed/add/edit/skip-restore-
   repeat-move routes, their forms and templates, and the plan editor's CSS and
   script. The schema, the `PLAN_*` events and every existing row stay.
