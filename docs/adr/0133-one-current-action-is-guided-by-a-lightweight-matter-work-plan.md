# 0133 — One current action is guided by a lightweight Matter work plan

**Status:** accepted
**Date:** 2026-10-02

**`Tööplaan`: faint default suggestions under the one current action, and a
fast «done → what next?» loop.** A Matter may carry a short, editable plan of
the work likely to follow. A plan step is guidance, not a task: it has no date,
no responsible person and no lateness, reaches no work surface, count or
statistic, and is never a `Teema käik` row. It becomes work in exactly one
way — somebody starts it — and starting it writes the Matter's one canonical
`NextAction`, linked back to the step.

**Two migrations**, both additive and rolling-safe: `workflow/0011` (the
`MatterPlanStep` table and the nullable `NextAction.plan_step`) and
`audit/0032` (eight `PLAN_*` event types on `ChangeEvent.event_type`'s choices,
no SQL). **No data migration and no backfill.**

1. **Four questions stay four answers.** `Hetkeseis` — where the external
   process stands; `NextAction` — what the lawyer is doing next, at most one
   open per Matter; **`Tööplaan` — what will probably follow**; `Teema käik` —
   what happened. `Menetluse kulg`, `Arvamuse tähtaeg` and `Kaasamine` keep
   their own meanings and are not plan steps.
2. **One model, `workflow.MatterPlanStep`**, directly under the Matter.
3. **States: `SUGGESTED`, `PLANNED`, `COMPLETED`, `SKIPPED`. No stored
   «current».** A step is current exactly when an OPEN `NextAction` names it.
4. **The relation is `NextAction.plan_step`, explicit and never inferred.**
   Starting writes it; `Muuda` carries it onto the replacement row; completing
   the action completes its step, inside `complete_next_action`; superseding
   or cancelling it completes nothing.
5. **A code-managed, versioned template**, `standard-legislative` v1, seeded as
   durable `SUGGESTED` copies — on interactive creation only, idempotently, and
   on an existing Matter only when a writer asks.
6. **Typed steps finish through their canonical operation** — an overview, a
   consultation, an opinion — when, and only when, that operation is entered
   *from the current step*.
7. **Planning is audited, never chronology**; actual work keeps one row per act.
8. **`Arvamuse tähtaeg` on a new Teema is an obligation, not the current task**:
   `Uus teema` no longer writes `Koostan arvamuse` from it.

---

## Context

A Matter answers «what am I doing next?» with one `NextAction`, and that is
right: the department's need is one unambiguous answer per file, and every
work surface is built on `workflow_one_open_action_per_matter`. What it did not
answer is the question a lawyer asks the moment that step is done — **what
usually comes after this?**

The Chamber's ordinary course on an incoming law, proposal or draft is well
known to the people who have done it for years and invisible to everybody else:
read it; write it up for the website; ask the members; collect and weigh what
comes back; form the Chamber's position; send the opinion; perhaps publish it;
start again on the next draft. A new lawyer, a colleague covering an unfamiliar
file, or anybody returning to a Matter after three weeks had nothing on the
page to say where in that course the file was. The answer lived in heads, in
Excel and in OneNote.

Two existing behaviours made it worse:

* **The deadline occupied the current task.** `Uus teema` turned
  `Arvamuse tähtaeg` into the step `Koostan arvamuse` (docs/adr/0094 §5). A
  deadline three months away then filled `PRAEGUNE TEGEVUS` while every real
  task before it — reading, writing up, consulting — had no place to be.
* **Finishing and choosing were two acts.** `Mida tegid?` finished the step,
  and the next one was set afterwards through `+ Määra järgmine tegevus`
  (docs/adr/0126 §1): a second form to open and a sentence to retype, at the
  moment the lawyer most wants to move on.

## Decision

### 1. Not a second task engine

The plan is deliberately thin. It does **not** add tasks, assignments,
reminders, recurrence, dependencies, durations, a progress figure or a
workflow builder. The one-open-action invariant is untouched; there is one
date model (the `NextAction`'s) and one responsibility rule (the
`NextAction`'s). A future step that needs a date or a person gets them by
being started.

Starting a step **never replaces** an open action
(`CURRENT_ACTION_EXISTS`): the loop is *finish the current thing, then choose
the next*. A deliberate «take this one now instead» is not built; `Muuda`
beside the current step remains the way to change what it is.

### 2. `MatterPlanStep`

One table under the Matter, in `app.workflow` beside `NextAction` so the
relation between them is same-app and adds no migration cycle.

| column | meaning |
| --- | --- |
| `matter`, `position` | which plan, and where in it (dense from 0, not unique, so a swap is two plain updates) |
| `title` | the step's words; required |
| `source` | `TEMPLATE` or `CUSTOM` — provenance, never behaviour |
| `operation` | `GENERIC`, `WEBSITE_OVERVIEW`, `ENGAGEMENT`, `SUBMISSION` |
| `state` | `SUGGESTED`, `PLANNED`, `COMPLETED`, `SKIPPED` |
| `template_key`, `template_version`, `template_step_key` | the template copy's provenance; all three or none |
| `created_by`, `completed_at`/`_by`, `skipped_at`/`_by` | who and when |

Checks: every vocabulary; a non-empty title; `completed_at` exactly when
`COMPLETED`; `skipped_at` exactly when `SKIPPED`; template provenance all or
nothing; and **one copy of each template step per Matter**
(`workflow_plan_step_template_once`, partial on `source = TEMPLATE`). There is
**no** uniqueness on the operation or the title: repeating a consultation is
ordinary. No document bytes, notes, comments, stage history, statistics or
copies of `NextAction` fields live here.

No visibility of its own: the plan is read only on its Matter's page, after
the Matter is found visible, and inherits that answer. Its audit events are
Matter-level (`MATTER_LEVEL_EVENT_TYPES`).

### 3. States, and why «current» is not one of them

* `SUGGESTED` — a faint copy of a template step nobody has accepted.
* `PLANNED` — a person added, edited, restored or started it.
* `COMPLETED` — this occurrence was done: its action was completed.
* `SKIPPED` — deliberately not needed; kept, restorable, out of the compact list.

Whether a step is current is the canonical action's answer. Storing it as a
fifth state would be a second truth about the same fact, kept in step by every
path that supersedes, cancels or closes — and the first path that forgot would
leave a step «current» with nothing open. Derived, it cannot disagree.

### 4. The relation and the loop

`NextAction.plan_step` (nullable, `PROTECT`). Imported, legacy and ordinary
actions carry `NULL` forever; nothing infers one.

* **Starting** (`Alusta`, `activate_plan_step`) goes through
  `set_next_action_for_new_work` with `plan_step=`. The step's own words unless
  the person changed them, a day only if they gave one, `DO` / `DEADLINE`, the
  departed-owner rule, `NEXT_ACTION_SET` — all exactly as any other step. A
  `SUGGESTED` step becomes `PLANNED`.
* **Editing** (`Muuda`) now names the step it was drawn beside (`action_id`).
  `change_current_action` re-reads it under the lock, refuses a stale one, and
  supersedes with `carry_plan_step=True`, so a reworded or redated step stays
  in the plan. Without an `action_id` the save is new work, as before.
* **Completing** — through any door — completes the step, because
  `complete_next_action` does it (`_complete_plan_step`, stamped with the
  action's own end time). `Mida tegid?`, Minu asjad's `✓ Tehtud`, ADR 0126's
  tick and a typed operation all agree because there is one place to agree.
* **Superseding or cancelling** — a `+ Märge` dated ahead, an import, a
  closure — completes nothing. The step stays `PLANNED` and simply stops being
  current.

**`✓ Tehtud` → `Mida tegid?` → `Järgmisena`.** The completion form is behind an
explicit `✓ Tehtud` beside the step. `Mida tegid?` stays required and is never
filled from the title, a filename or «Tehtud». `Järgmisena` is optional and
offers every step still ahead, `Muu tegevus` (a sentence of the person's own,
tied to no step) and `Praegu ei määra`, which is the default. **The next
suggestion is never started for anybody.** The chosen next step, and an
optional exact day, are checked under the Matter's lock *before* anything is
written — on this Matter, still ahead, not the step being finished — and then
the note, its files, the completion and the new step land in one transaction
and one operation. A stale tab, a skipped step or another Matter's step
refuses the whole save.

**`Lisa märge`** beside the step is a label for `+ Märge` in `LISA TEEMALE`:
the ordinary composer, ordinary history, and the step untouched. No comment
thread, no sub-task note.

With nothing current, `PRAEGUNE TEGEVUS` names the first step ahead under
`Soovitatud järgmisena` with `Alusta`, and every step ahead in `TÖÖPLAAN` has a
one-click `Alusta` (the step's own words, no day).

### 5. The template, seeding and adoption

`app.workflow.plan.STANDARD_PLAN` — key `standard-legislative`, version 1:

| key | step | operation |
| --- | --- | --- |
| `read-material` | Tutvu materjaliga | `GENERIC` |
| `website-overview` | Koosta kodulehe ülevaade | `WEBSITE_OVERVIEW` |
| `consult-members` | Kaasa liikmeid / küsi tagasisidet | `ENGAGEMENT` |
| `form-position` | Koonda tagasiside ja kujunda Koja seisukoht | `GENERIC` |
| `send-opinion` | Saada Koja arvamus | `SUBMISSION` |

**Who seeds.** `seed_standard_plan` is called by name, never from
`create_matter`:

* `Uus teema` (`matter_create`), in the creation transaction — unless the
  chosen `Hetkeseis` files the Matter closed (docs/adr/0131 §10);
* `Saabunud` (`register_incoming(seed_plan=True)` from the `intake` view);
* `+ Lisa tavapärane tööplaan` on an open Matter, by a writer.

`create_matter`, `create_imported_matter`, the register importers and refresh,
cutovers, archive reconstruction and the seed commands do not, so nothing
recorded as history gains future work. **No existing Matter is backfilled**:
a plan on an old file would be intent nobody stated.

**Idempotent by step key**: a template step already on the plan, in any state,
is not copied again, and the database says the same. On a plan that already
holds custom steps, the missing template steps are **appended** after them in
the template's order; nothing a person wrote is reordered, renamed or removed.
`+ Lisa tavapärane tööplaan` disappears once every template step is present.

**No plan is not an empty section.** A Matter without a plan draws no
`Tööplaan` heading and no «none yet» sentence: a writer on an open, full record
sees one quiet row with `+ Lisa tavapärane tööplaan` and `+ Lisa samm`; a
reader sees nothing; an archive register row (`RecordMode.ARCHIVE`) is offered
nothing at all — it is history until it is promoted to active work
(TEEMA_TARGET_SPEC §F).

**Versioned copies.** Each seeded step stores its own title and operation with
the template key and version as provenance. A later version reaches no
existing Matter — no rename, no new step, no removal. A second template
(`Kiire arvamus`, `Koja enda ettepanek`, `ELi teema`) is an entry in
`PLAN_TEMPLATES`; no selection or administration surface is built.

### 6. Typed steps

When the current step's operation is typed, the canonical form that does its
work is drawn **under the step** (`#samm-toiming`): a second instance of the
`LISA TEEMALE` form, same fields, own ids (`drawn_under_the_current_step`),
posting the step and its action. Saving it completes both in the record's own
operation, with no `Mida tegid?` note:

* **`Koosta kodulehe ülevaade`** — finished by a *published* `Ülevaade / uudis`.
  The form records a publication (address required, docs/adr/0095 §5); an
  addressless save from the step is refused rather than filed as a plan. A
  `PLANNED` overview, from any door, finishes nothing.
* **`Kaasa liikmeid / küsi tagasisidet`** — finished by creating the
  `Kaasamine`. Asking is done; collecting is not: the round stays **open**
  (docs/adr/0132), reads on `PRAEGUNE TEGEVUS` and the work surfaces as it
  always does, and no «Ootan tagasisidet» step is invented. `Lõpeta kaasamine`
  later invents none either; the next suggestion is then simply there to start.
* **`Saada Koja arvamus`** — finished by registering the sent opinion through
  `register_sent_opinion_on_open_matter`, every evidence, recipient, date,
  withdrawal, stage and strip rule unchanged.

`_named_plan_action` checks, under the Matter's lock and **before** anything
is written: the Matter is open; the named action is still the open one; it
belongs to the named step; the step is on this Matter; its operation is the
one this save performs. Anything else refuses the whole save
(`PLAN_STEP_NOT_CURRENT`, `PLAN_STEP_WRONG_OPERATION`, `STALE_ACTION_REFUSAL`).
The view fetches both ids through the Matter and `visible_to`, so a foreign or
hidden one is a 404.

**ADR 0126's tick is unchanged and does not double up.** The `LISA TEEMALE`
`Koja arvamus` and `Lõpeta kaasamine` still offer `Märgi praegune tegevus
tehtuks`, unticked and naming the step; ticked, it completes the action — and
so its step, by §4. The form under the step carries no tick, because the launch
is the relationship. One way to finish the step per form, never two in one. A
record entered from `LISA TEEMALE` without the tick finishes nothing; nothing is
inferred from text, kind or date.

### 7. History

Eight audit events — `PLAN_SEEDED`, `PLAN_STEP_ADDED` (a repeat carries
`repeats`), `PLAN_STEP_CHANGED`, `PLAN_STEP_MOVED`, `PLAN_STEP_SKIPPED`,
`PLAN_STEP_RESTORED`, `PLAN_STEP_ACTIVATED`, `PLAN_STEP_COMPLETED` — readable on
`Kõik muudatused` and absent from `TIMELINE_EVENT_TYPES`. `Teema käik` gains no
«tööplaan lisatud», «liigutas sammu», «jättis vahele» or «aktiveeris».

Actual work keeps the operation-id discipline: a generic completion is the
`Mida tegid?` note, with «märkis eelmise sammu tehtuks» and the next step folded
on its row; `ENGAGEMENT_ADDED` and `WEBSITE_OVERVIEW_PUBLISHED` join
`RECORD_OPERATION_EVENT_TYPES`, each folding only `NEXT_ACTION_COMPLETED`, so a
round or an overview started from the step reads as one row with `✓ Tehtud`
under it — exactly as a send already did (docs/adr/0126 §4). The fold is
asked only of records this reader can see, and paging walks the same rows.
Starting a step is the `NEXT_ACTION_SET` it has always been.

**Stage periods (docs/adr/0131).** The plan spans future work and is bound to
no period; a stage change moves no step. Actual work is placed in the period it
happened in through the existing audit seam, and a completed step does not move.

### 8. `Arvamuse tähtaeg` is an obligation

`matter_create` no longer calls `establish_opinion_preparation_action`. A new
Teema with `Arvamuse tähtaeg` stores `response_deadline`, seeds the faint plan,
and has **no** current action. The deadline stays visible — the header's
`Arvamuse tähtaeg`, `Menetluse kulg`, and the secondary obligation line under
whatever step is current (`secondary_response_obligation`, unchanged) — and is
discharged only by the existing logic (a sent opinion, the register's
record). Completing a step, publishing an overview, creating a round or writing
a note discharges nothing.

`establish_opinion_preparation_action` and `OPINION_PREPARATION_TEXT` stay;
no `Koostan arvamuse` step is migrated, superseded or relinked.

### 9. The plan editor

`TÖÖPLAAN` sits directly under `PRAEGUNE TEGEVUS` and above `LISA TEEMALE`:
compact rows, each with a marker **and** its state in words (`Praegu`,
`Tehtud`, `Soovitus`; `Plaanis` for screen readers), skipped steps out of the
way, no percentage. Suggestions are quieter — hollow marker, secondary text
colour, a small label — at the page's ordinary secondary contrast, never
`--text-atypical` and never styled as disabled.

`+ Lisa samm` asks `Tegevus` and, optionally, `Seotud toiming` and where to put
it. `Muuda plaani` lists every step with only the acts its state allows: `↑`
/ `↓` among the steps still ahead, `Muuda` (words and linked operation — the
operation moves to `Tavaline tegevus` as the words of a typed step are
rewritten, and whatever is chosen is stored), `Jäta vahele`, `Taasta`, and
`Korda` on a finished step, which adds a new `CUSTOM` occurrence first among the
steps ahead and never reopens the old one. The current step is changed beside
it, never here; a finished step is history. Buttons, not dragging.

### 10. Concurrency and authorization

Every plan write locks the Matter through `lock_open_matter_for_business_write`
(closed Matters refuse). Shape edits post the plan revision — a digest of each
step's id and `updated_at` — and a stale one refuses (`STALE_PLAN_REFUSAL`)
rather than overwrite a colleague's order. Every endpoint is behind
`business_write_required`; the Matter through `get_visible_matter`; a step only
through its Matter (404 otherwise). Readers see the plan and no control; a
closed Matter's plan is readable and inert.

## Supersedes and narrows

* **docs/adr/0126, «no second task concept» and «`Uus teema`» under *Not
  changed*.** The plan is guidance, not a second canonical task system: the
  `NextAction` is still the only task. `Mida tegid?` gains `Järgmisena`; `Muuda`
  gains `action_id`; `NextAction` gains `plan_step`. The loop 0126 describes —
  *set the next step → do the work → record the real event → finish the step →
  set the next one* — stands, with guidance for the last move.
* **docs/adr/0094 §5 and docs/adr/0130 §1** — `Arvamuse tähtaeg` no longer
  «establishes the canonical `Koostan arvamuse` step»; it records the
  obligation only.
* **docs/adr/0075 §3** — «no `✓ Tehtud`»: the completion form is behind an
  explicit `✓ Tehtud`, and is the same form.
* **docs/adr/0092 §6, docs/adr/0126 §4** — the fold gains two record families.

## Not changed

`workflow_one_open_action_per_matter`; what OPEN, COMPLETED, CANCELLED and
SUPERSEDED mean; `DO` / `WAIT` / `MONITOR`, their dates, lateness and reviews;
`Vaatasin üle`; `+ Määra järgmine tegevus`; ADR 0124's `Märgi järgmiseks
tegevuseks`; the `Submission`, `MatterEngagement`, `MatterWebsiteOverview` and
`MatterExternalPosition` services and the evidence invariant; ADR 0132's round
lifecycle; ADR 0131's periods; `Minu asjad`, `Osakond`, the register and
Statistika, which read open actions exactly as before; the search index.

## Alternatives considered

* **A `WorkPlan` parent table.** Rejected: it would hold a template key and a
  revision, both of which the steps carry or derive, and add a row whose only
  job is to exist.
* **A stored `ACTIVE` state.** Rejected (§3).
* **Linking by text, order or date.** Rejected: an inference is a guess made in
  the person's name.
* **Several open actions, one per started step.** Rejected: it breaks the
  invariant every work surface is built on.
* **Auto-starting the next suggestion.** Rejected: guidance is not a decision.
* **Backfilling plans on existing Matters.** Rejected: invented intent.
* **Seeding inside `create_matter`.** Rejected: importers share it, and a flag
  there would be a brittle inference of «manual».
* **A template table and admin UI.** Deferred: one template today, reviewed as
  code.
* **Dates or owners on future steps.** Deferred: a second scheduling and
  assignment model beside the `NextAction`'s.

## Consequences

* A new Teema opens with five faint suggestions and no current step; a lawyer
  starts the first with one click.
* The `Koostan arvamuse` step no longer appears on new Matters; the browser
  suite's `give_first_step` starts a plan step instead.
* `PRAEGUNE TEGEVUS`'s completion form is one click further away and carries
  `Järgmisena`; the visual baselines of every Teema page move.
* Planning edits are visible on `Kõik muudatused` and nowhere in `Teema käik`.

## Reversibility

Reverse `workflow/0011` and `audit/0032` to drop the column, the table and the
choices — losing only plan data. Restoring `establish_opinion_preparation_action`
in `matter_create` restores the old first step for new Matters.
