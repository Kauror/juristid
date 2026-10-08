# 0126 — The next step is set where it is read, and the act that does it can finish it

**Status:** accepted
**Date:** 2026-10-01

**The real-time lawyer loop**, on the canonical `NextAction` that already
exists: *set the next step → do the work → record the real event → finish the
step → set the next one*. Two controls and one chronology rule. **No
migrations**, no new model, no second task concept, no change to the
`NextAction` services or to what COMPLETED and SUPERSEDED mean.

1. **`+ Määra järgmine tegevus` in `PRAEGUNE TEGEVUS`** whenever the Matter has
   no open step the reader can see. It is `Muuda`'s own form, endpoint and
   service — one disclosure partial, `#lisa-jargmine`, in two states.
2. **`Märgi praegune tegevus tehtuks` on `Koja arvamus` and on a round's
   `Lõpeta kaasamine`**, drawn only while there is an open step, unticked, and
   naming it. Ticked, the same save finishes that step through
   `complete_next_action`: COMPLETED, never superseded, and no `Mida tegid?`
   note is written for it.
3. **After such a save the column is drawn again**, so the zone that asked
   about the finished step now offers `+ Määra järgmine tegevus`. No wizard.
4. **`Teema käik` keeps one row for the act**: the finished step folds under
   «Arvamus välja» (or the round) as `✓ Tehtud <step>`, the way a `Märge`
   already folds the step it set (docs/adr/0092 §6).

---

## Context

The living-dossier QA found the architecture sound and the daily loop awkward
in two places.

**A Matter with no open step had no direct way to get one.** `+ Järgmine
tegevus` left the launcher (docs/adr/0097 §8.2), and from docs/adr/0124 the
ordinary route was `+ Märge` with a day after today and `Märgi järgmiseks
tegevuseks` ticked. 0124's own consequences recorded the gap: a step with no
day, or dated today, could then be made only by `Muuda` — which is drawn only
*beside an open step* — or on `Uus teema`. So right after a lawyer finished
something, the page offered no way to say what came next except to file a
future-dated `Märge` describing it, and «kontrolli menetluse seisu» with no day
yet could not be recorded at all.

**The real event did not finish the step it was.** «Vormista ja saada Koja
seisukoht» is done by registering the opinion. The page then still showed the
step open, and the only completion it offered was `Mida tegid?` → `Salvesta
tegevus`, which writes an `Entry`. The file ended up with «Arvamus välja» and a
second, generic note saying the same thing — the duplicate a history should not
carry — or with a step left open after the work was done.

This is **not** history reconstruction. Nothing here sets a planned-on date,
backdates a completion, or lets anybody record that a step was done on another
day: a completion is stamped with the moment of the save, as it always was.

## 1. `+ Määra järgmine tegevus`

While a step is open, `Muuda` sits beside it (docs/adr/0075 §10). With none,
the same zone now draws `+ Määra järgmine tegevus` under its one line —
«Järgmine samm on määramata», the upcoming `Oluline tähtaeg`
(docs/adr/0120 §2), the register's Excel instruction, or the waits — for a
writer on an open Matter.

**One control in two states, not two controls.** Both are
`templates/matters/partials/next_action_panel.html`: the same `<details
id="lisa-jargmine">`, the same `next_action_form.html`, the same
`matters:set_action` and the same `set_next_action_for_new_work` that `+ Märge`'s
ticked day ahead and `Uus teema` call. `NextActionForm` is unchanged: with no
step to replace it writes new work, `DO` / `DEADLINE`, at the precision chosen
(`Täpne päev`, `Kuu`, `Kvartal`, `Aasta`) and with no day at all when none is
known (docs/adr/0052 §3, 0079, 0106); `Muuda` keeps a replaced step's kind and
date meaning (docs/adr/0075, amended 2026-09-26). `WAIT` / `MONITOR` remain
stored, displayed and reviewable exactly as before and are still not asked of a
person (docs/adr/0054).

`_workspace_refusal` no longer counts `action_form` among the forms that need
an open step, because `#lisa-jargmine` is now on every open Matter's column: a
refused save comes back opened in whichever host the fresh column draws.

Nothing about the milestone, the Excel instruction or a waiting round becomes a
step by being shown above the control, and nothing is prefilled from them.

## 2. `Märgi praegune tegevus tehtuks`

On `+ Arvamus / tagasiside · Koja arvamus` and on each waiting round's
`Lõpeta kaasamine`. One partial
(`matters/partials/complete_current_action_option.html`), one form field
(`completes_current_action_field`), one use-case check
(`workspace._named_open_action`).

* **Drawn only when there is a step to finish**: the open step this reader may
  see (`selectors.current_action_of`), on an open Matter. A milestone, an Excel
  instruction, a round's own `Tagasisidet ootame kuni` and a step restricted
  below the reader offer nothing. A feedback wait is not a `NextAction`
  (docs/adr/0086) and is never read as one.
* **Unticked, and naming the step.** The label carries the step's sentence and
  date, so what the box finishes is read before it is ticked and is the
  checkbox's accessible name. Nothing on an opinion or a round says whether it
  is what the step asked for, so no text, date or kind is compared and nothing
  is ever completed without the tick.
* **It posts the step's own id.** The view fetches it through
  `NextAction.objects.visible_to` (404 otherwise, AUTH-003); the use case locks
  the Matter, re-reads the open step and refuses the **whole save** with
  `STALE_ACTION_REFUSAL` unless it is the named one — the stale-tab rule
  `Mida tegid?` has always followed (docs/adr/0075 §4). The check runs
  *before* anything is written, because a Koja arvamus stores its bytes as it
  goes and a refusal after that would leave them orphaned; the Matter lock is
  held to the completion, so the answer cannot change in between.
* **Finished, not replaced.** `complete_next_action` writes COMPLETED and
  `NEXT_ACTION_COMPLETED`, stamped now. SUPERSEDED still means only what
  `set_next_action` does to the step a new one replaces. No `Entry` is written.
* **Both records stay what they were.** The `Submission` is registered through
  `register_sent_opinion_on_open_matter` and the round is closed through
  `complete_engagement_feedback`, unchanged, with every evidence check, lock,
  event and refusal they had. The completion joins their transaction and their
  operation.

`Mida tegid?` → `Salvesta tegevus` is unchanged and remains the way to finish a
step whose work has no record of its own; it now reaches the same
`_named_open_action`.

### Considered and not done

* **Inferring the completion** from the step's text, its kind, the opinion or
  the round. Refused for the reason above.
* **Ticked by default.** Same reason: a default is a guess made in the
  person's name.
* **A link from a round to "its" step.** A schema change for a relationship
  nobody records today, and a second way of saying what the step is.
* **`+ Ülevaade / uudis`.** A *planned* write-up is an intention and must not
  finish anything; a published one is reached through two doors (the panel and
  the planned row's `Avaldatud`). Whether either should offer the box is a
  separate decision, deliberately not taken here.

## 3. After the save

A Koja arvamus already re-renders the whole column. `Lõpeta kaasamine` swaps
only its own row, which would leave `PRAEGUNE TEGEVUS` asking about the step it
just finished — so when the box was ticked the response is the column, by
`HX-Retarget: #teema-vaade` (the way `Muuda kulgu` answers), and the row swap is
unchanged otherwise. Either way the zone now reads «Järgmine samm on määramata»
with `+ Määra järgmine tegevus` under it. No step is opened for the person and
no wizard follows: what comes next is theirs to say, when they know it.

## 4. One act, one row

`NEXT_ACTION_COMPLETED` has always been a chronology event: a clause
(«märkis eelmise sammu tehtuks») on a `Mida tegid?` note's row, or a muted row
of its own. Saved with an opinion it would have been the second — a muted line
beside «Arvamus välja» naming no step. So it folds onto the record's row
instead, exactly as docs/adr/0092 §6 folds a stage move and a step set by a
`Märge`:

* `RECORD_OPERATION_EVENT_TYPES` gains `SUBMISSION_SENT` and
  `ENGAGEMENT_FEEDBACK_CLOSED` — read, never rendered, as
  `PROCEDURAL_DEVELOPMENT_RECORDED` is — and `FOLDED_EFFECTS` says what each may
  fold: a development its stage and step as before; a send and a finished wait
  only `NEXT_ACTION_COMPLETED`.
* The row prints `✓ Tehtud <step>` in the existing pill language, the sentence
  only — the step's planned day is not when it was done.
* **Only an explicit `operation_id` folds anything**, onto a record this reader
  can see; a restricted record leaves the completion standing as its own row,
  as a restricted development leaves its stage change (docs/adr/0092 §7).
* Paging keeps ENG-018's contract: the development-only exclusion in
  `_ChronologySources` becomes one over the three folding families, so an
  effect whose record lies before a page's bound is neither an anchor nor a
  stray row.

`Mida tegid?` rows are unchanged.

## Supersedes and narrows

* **docs/adr/0075 §5** — «naming the next one is a deliberate act through
  `+ Järgmine tegevus`»: it is `+ Määra järgmine tegevus`, in
  `PRAEGUNE TEGEVUS`. **§10** — «once the step is finished the chip appears»:
  the control appears in that zone, not in the launcher.
* **docs/adr/0097 §8.2** — «exactly one *ordinary* way to set one … otherwise
  `+ Märge`»: with no open step, `+ Määra järgmine tegevus` is the direct way,
  and a ticked future `+ Märge` stays a second door onto the same service.
* **docs/adr/0124, Consequences** — «a step with no day, or dated today, is …
  made where a step is edited (`Muuda` beside an open step) or on `Uus teema`»:
  and with `+ Määra järgmine tegevus` on a file with none.
* **docs/adr/0092 §6** — the fold gains two record families and one effect.
* **docs/adr/0086 §6** — `Lõpeta kaasamine` may also finish the open step, when
  ticked.

## Not changed

The `NextAction` model, its constraints and every service in
`app.workflow.services`; the one-open-step invariant; what COMPLETED, CANCELLED
and SUPERSEDED mean; `DO` / `WAIT` / `MONITOR` and their lateness and review
rules; `Vaatasin üle`; ADR 0124's `Märgi järgmiseks tegevuseks` and its
day rule; `Mida tegid?`; `Uus teema`; `Minu asjad`, `Osakond`, the `Teemad`
register and Statistika, which read open steps exactly as before and so show a
step set here like any other; the `Submission` and `MatterEngagement` services
and the evidence invariant; signed-container and upload validation;
permissions and the business-write boundary; the search index
(`INDEX_VERSION` unchanged). **No migrations.**

## Amendment of 2026-10-08 — a check is finished only with what it found

When the current action is an `Arvamuse järelkontroll` (docs/adr/0146), §2's
`Märgi praegune tegevus tehtuks` is not drawn on `Koja arvamus` or
`Lõpeta kaasamine`. `_named_open_action` refuses it if posted.

A send also schedules its own check. §4's fold gains `NEXT_ACTION_SET` for
`SUBMISSION_SENT`, so «Arvamus välja» reads «→ Kontrolli, kas adressaat …»
under it instead of a second row.
