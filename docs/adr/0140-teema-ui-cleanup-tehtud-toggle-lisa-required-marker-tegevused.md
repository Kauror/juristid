# 0140 — Teema UI cleanup: a `✓ Tehtud` toggle, a simpler completion, `+ Lisa`, one required-field rule, `Tegevused`

**Status:** accepted
**Date:** 2026-10-05

The owner's UI cleanup round for the Teema page: less UI, fewer decisions,
faster routine use. **No migrations**, no new model or field, and no existing
row is touched. The domain rules underneath are unchanged; where a service
changed, it gained one optional argument and lost one path.

## Decisions

1. **`✓ Tehtud` is a toggle that never moves.** The `PRAEGUNE TEGEVUS` row is
   the task, its date, `✓ Tehtud` and `Muuda`. `✓ Tehtud` is the label of a
   clipped checkbox (`#tehtud-valik`) on the row; the completion form is a
   sibling panel (`#tehtud`) that alone takes `order: 1` and the line below
   the row — the `LISA TEEMALE` construction from 2026-09-14. As a
   `<details>`, the open panel took the whole line and the chip just pressed
   jumped to the head of the next one. Pressing it again closes the form,
   natively, with scripting off too. `Lisa märge` is removed from the row;
   recording something that happened while the step stays open is `+ Lisa`.
   `Muuda` keeps its meaning.

2. **`Järgmine tegevus`, written directly.** The `Järgmisena` radio layer —
   the `Tööplaan` steps still ahead, `Muu tegevus`, `Praegu ei määra` — is
   removed. The form asks one optional sentence; left empty, no step is
   opened, and a day without a sentence is dropped. No `Tööplaan` step is
   offered, suggested or started from this form, so
   `complete_current_action` loses `next_step_id` (and
   `NEXT_IS_THE_CURRENT_STEP`): the next action goes through
   `set_next_action_for_new_work` as `Muu tegevus` always did. Amends
   docs/adr/0133 §4 for this form; `Alusta` and the plan rows still start a
   plan step, and completing a plan-linked step still completes its plan step
   (`complete_next_action`).

3. **`Uus hetkeseis` in the same save.** The completion form offers the control
   `+ Lisa · Tavaline` and `+ Koja arvamus` offer — `NextStageChoiceField`, the
   same offered order (`offered_next_stages`), the same whole-vocabulary
   validation, `Jätan muutmata` as the default. `complete_current_action`
   takes an optional `stage` and wraps its writes in the same
   `stage_transition` the `Märge` path uses: the note, its files, the
   completion and the next action belong to the period current before the
   move; the move writes its own `MATTER_STAGE_CHANGED` in the same operation;
   the stage the file already holds moves nothing; a stage that ends the Matter
   closes it on the way out, and beside a next action it is refused before
   anything is written (`TERMINAL_STAGE_MAKES_NO_STEP`, docs/adr/0131 §10). The
   completion note keeps the action's restriction (docs/adr/0138).

4. **`Millal?` has `+1 päev`, `+1 nädal`, `+1 kuu`.** The existing quick-date
   chips (`.cx-when`, `bindQuickDates`), resolved on the server in
   Europe/Tallinn (`next_step_date_choices`; `+1 kuu` is a calendar month,
   `add_months`), writing into the ordinary date box, which is still the field
   submitted and can be overridden by hand. The date's meaning and precision
   rules are unchanged.

5. **`+ Märge` reads `+ Lisa`, and `Märke liik` reads `Lisa liik`.** Words
   only: the four kinds, their models, services, ids (`lisa-marge`,
   `marge-tavaline`, …) and history wording («Märge: …», «Märge lisatud») are
   unchanged.

6. **One required-field rule for every form.** Optional fields carry no marker;
   required ones carry a small red `*` after the label (`.req-mark`, with
   «(kohustuslik)» for a screen reader); the word «valikuline» is gone from the
   UI. The marker is read off the field, never typed: `{% field_label field %}`
   (app/core/templatetags/form_labels.py) renders the label and the marker when
   `Field.required` is true or the field is declared with `marks_required` —
   for a field declared `required=False` whose `clean` refuses an empty value
   with its own sentence (`Mida tegid?`, `Keda kaasati`, `Mis tähtaeg`, the
   `Koja arvamus` file, date and recipients, …). A label that is not a form
   field's uses `{% required_mark %}`; a file drop zone, which has no visible
   label, carries the marker in its text when its field is required.
   Conditionally required fields (an external position's text-or-link-or-file,
   the work-win date) carry no marker.

7. **`Tegevused · N kirjet` is a heading.** The chronology is a `<section>`
   whose head is the visible title `Tegevused` and the reader-visible count;
   it no longer folds as a whole. The `Hetkeseis` period accordions inside
   still open and close one by one. The anchor stays `#ajajoon`.

8. **No filter over `Tegevused`.** The `Alates` / `Kuni` / `Liik` filter
   (historical regression UX-012, 2026-10-04) is removed with its module
   (`app/matters/timeline_filter.py`), its view plumbing and its styles; its
   query parameters are ignored.

## Not changed, recorded

* The older, unrendered `?ajajoon=koik|sissekanded|sundmused` slice is still
  honoured by `matter_timeline(only=…)` and passed through by «Näita
  varasemaid»; removing it is a change to the chronology selector, not to this
  page's UI.
* `MatterProceduralLink.label`'s model `help_text` still begins «Valikuline —»;
  it shows only in the Django admin, and changing it would need a migration.
