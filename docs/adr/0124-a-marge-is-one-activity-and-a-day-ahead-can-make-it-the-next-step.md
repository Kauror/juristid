# 0124 — A `Märge` is one activity, and a day ahead can make it the next step

**Status:** accepted
**Date:** 2026-09-30

**The owner's simplification of `+ Märge · Tavaline`**, one small UX change.
The panel asked for what happened and then, in a second pair of boxes, for the
next step and its day. It now asks for one activity and one day, and a day
after today offers to make that activity the next step. **No migrations**, no
new model and no new next-step mechanism: the step is written by the service the
old boxes called.

1. **One `Tegevus`**, placeholder «Kirjuta, mida tegid või mis on järgmine
   tegevus». `Järgmine tegevus` and `Millal?` leave the panel.
2. **A day after today offers `Märgi järgmiseks tegevuseks`, ticked.** Ticked,
   the save also makes that sentence and that day the Matter's `Järgmiseks`
   through `set_next_action_for_new_work`. Unticked, the `Märge` is information
   and moves no step. Past, today and no day offer nothing and make no step.
3. **The server decides**, on the application's clock, in the form and again in
   the use case. The box the page hides is disabled, so it is not sent either.
4. **Two records stay two.** Correcting the `Märge` never moves the step it
   made; the step is corrected where every step is.
5. **One plan is not printed twice in one row.** Teema käik leaves off the
   folded «→» step pill while the step is the row's own activity.

---

## Context

`+ Märge · Tavaline` asked four questions (docs/adr/0105 §4): *mis juhtus, lisa
failid, uus hetkeseis, järgmine tegevus · millal*. A lawyer describing one piece
of work — «Saatsin ministeeriumile kirja», or «Saadan ministeeriumile kirja,
2.10» — had to decide which of two concepts it was before the panel would take
it. The planned activity was the common case of the second pair, and it meant
writing the same sentence twice, or writing a plan into `Mis juhtus?`, a
question about the past, where it became an `Eesolev` row and no step at all
(docs/adr/0121 §3).

## 1. One activity

The sentence box is `Tegevus`. «What happened» is a question in the past tense
and made a plan read like a mistake; one word holds both tenses, and the
placeholder says so. It stays optional (docs/adr/0105 §4) except in the one case
§2 makes it the step. `Kuupäev`, the file affordance, `Uus hetkeseis` and
`Salvesta` are unchanged, in the same order. There is no longer a bottom row of
`Järgmine tegevus` / `Millal?`.

`DEVELOPMENT_NEEDS_SOMETHING`, the refusal of a press carrying nothing, is
reworded to the panel's controls — «Kirjuta tegevus, lisa fail või vali uus
hetkeseis.» — because it named a `järgmine tegevus` box the panel no longer has.
The rule behind it is unchanged, and the use case still counts a separate
`next_text` as content for the callers that pass one.

## 2. The day says which it is, and the person confirms it

**Past or today:** the save is the record of something done. No checkbox is
shown, no `NextAction` is created, and an open one is neither superseded nor
cleared.

**After today:** `Märgi järgmiseks tegevuseks` appears on the date's own row,
**ticked**, because «write what I will do, pick the day, save» is the ordinary
way a lawyer sets their next step. Saved ticked, the `Märge` is recorded as it
always was *and* `set_next_action_for_new_work(text=<Tegevus>,
target_date=<Kuupäev>)` runs in the same transaction and operation — the call the
old `Järgmine tegevus` + `Millal?` made, storing `DO` / `DEADLINE` / `EXACT`
(docs/adr/0052 §3). It supersedes an open step exactly as that call always did;
this decision changes nothing about the one-open invariant.

**After today, unticked:** «Ministeerium avaldab tulemused 15.10» is worth
recording and is nobody's task. The `Märge` is saved, reads `Eesolev` in Teema
käik until its day (docs/adr/0121 §3), and no step is created, updated or
cleared. This is why the answer is a box and not an inference: a future date
alone never makes a step.

**A step is its sentence.** Ticked on a day ahead with no `Tegevus` is refused on
the box with the refusal every step control gives («Kirjuta järgmine tegevus.»,
`NEXT_STEP_NEEDS_SENTENCE`, docs/adr/0106), and nothing is written. The
constant moved to `app.workflow.services` so the use case can raise it without
importing a form.

**No day, no step.** An emptied `Kuupäev` is «kuupäev teadmata» as before, and is
never ahead.

## 3. The server is the rule

«Today» is `timezone.localdate()` — the application's `TIME_ZONE`,
Europe/Tallinn — and nothing else:

* `MatterProgressForm` drops `as_next_step` for any day that is not after today,
  so the cleaned value is the answer the view hands on, and draws the offer from
  the same comparison (`next_step_offered`);
* `workspace.add_procedural_development(as_next_step=True)` applies the rule
  again on its own clock and refuses an empty sentence before writing, because a
  form is not a boundary. A past, today's or empty day makes the flag inert.
  Passing both `as_next_step` and a separate `next_text` raises `ValueError`:
  two answers to one question;
* the panel carries the server's day as `data-today`, and
  `bindNextStepOffers` (static/js/app.js) shows the row for a day after it,
  hides it otherwise, and re-ticks the box each time it appears. **Hidden is
  disabled**, so a tick left from a moment the date was ahead is not sent. That
  is a convenience; a stale or hand-made `as_next_step=on` beside a past or
  today's date is ignored by both server layers.

With scripting off the row is what the server drew for the day it was drawn
with, and the save applies the rule anyway.

## 4. Correcting afterwards

`ProceduralDevelopmentEditForm` takes the panel's word — its label is `Tegevus`
— and nothing else. It offers no `Märgi järgmiseks tegevuseks`: a step the save
once wrote is a `NextAction` of its own, corrected through `Muuda` in
`PRAEGUNE TEGEVUS`, and correcting the sentence or the day of the `Märge` never
moves it (the rule docs/adr/0091 §5.4 and the `Muuda` tests already held for a
step written from the old boxes). Nothing is copied that a later correction would
have to keep in step, and no historical record is rewritten.

## 5. One plan is not printed twice in one row

Teema käik folds a step a save wrote under that save's row as a «→ sentence ·
day» pill (docs/adr/0092 §6). A step made here *is* the row's activity, so the
row would read «Saadan kirja · 2.10 · Eesolev» and then «→ Saadan kirja 2.10»
under it. `timeline._with_next_steps` leaves the pill off exactly while the
step's sentence and day equal the `Märge`'s title and day
(`_step_is_the_activity`). A row whose step was written from the old separate
boxes, or a `Märge` corrected since, differs from its step and keeps the pill.
Nothing else about the chronology changes: the `Märge` is drawn at once and
marked `Eesolev` as before, and the open step is read in `PRAEGUNE TEGEVUS`.

## Consequences

* A step is made from `+ Märge` only with a day after today. **A step with no day,
  or dated today, is no longer made from this panel**; it is made where a step is
  edited (`Muuda` beside an open step, docs/adr/0106) or on `Uus teema`. This
  follows directly from «do not show the next-activity box for a past or today's
  date», and is recorded here so it is not rediscovered as a gap.
* `Oluline tähtaeg`, `Jõustumine` and `Töövõit` are untouched: their own forms,
  endpoints, services, validation and reporting. A future one of them is still
  never a `NextAction`; an upcoming `Oluline tähtaeg` is still *surfaced* as the
  next step where none is set (docs/adr/0120 §2).

## Supersedes and narrows

* docs/adr/0105 §4 — the four questions: *järgmine tegevus* leaves the panel, and
  «`Järgmine tegevus` still needs its day» no longer describes a control.
* docs/adr/0106 §4 — `MatterProgressForm`'s `Järgmine tegevus` block is gone, so
  the undated step is no longer made from `+ Märge`; `NextActionForm` and
  `ComposerForm` keep the rule unchanged.
* docs/adr/0121 §3 — «Nothing dated ahead becomes the next step except through
  §1's rule» gains its one explicit exception: the person ticking `Märgi
  järgmiseks tegevuseks`, which writes an ordinary `NextAction`. The `Märge`
  itself still never *is* the step and is never read as one.

## Not changed

The `NextAction` model and its services, `PRAEGUNE TEGEVUS`, `Minu asjad`, the
portfolio, the register and Statistika filters, Teema käik (beyond §5's pill)
and `Menetluse kulg`,
the three named `Märke liik` kinds, every other create or edit form, date
precision anywhere, file upload and `DocumentLink`, grouped uploads,
`Uus hetkeseis`, permissions and the business-write boundary, the audit trail
(the step's `NEXT_ACTION_SET` shares the save's `operation_id`, as it did), the
search index (`INDEX_VERSION` unchanged). No migrations.
