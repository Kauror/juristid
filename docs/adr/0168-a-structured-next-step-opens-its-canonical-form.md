# 0168 — A structured next step opens its canonical form

**Status:** accepted — owner's decision D4 of 2026-10-14 (QA remediation round).
**Date:** 2026-10-14

**No migrations**, no data migration, no backfill, no search-index rebuild. No
existing row is rewritten: the columns this uses
(`MatterPlanStep.fulfilled_by_operation` / `fulfilled_by_record`) have existed
since `workflow/0012` and were left unused by docs/adr/0141.

## Context

The owner reported three defects directly:

* «Kaasa liikmeid / küsi tagasisidet» → `Alusta` opened a two-field box under
  `PRAEGUNE TEGEVUS` (`Mida on vaja teha?`, `Millal?`) that wrote a generic
  current step with the suggestion's words. Nobody was asked who was engaged,
  by when, or where; the real `Alusta kaasamist` form sat untouched in
  `LISA TEEMALE`.
* «Saada Koja arvamus» → `Alusta` did the same: a generic step, and later a
  generic `✓ Tehtud` / `Mida tegid?` that could «finish» sending an opinion
  that was never recorded.
* «Ootame tagasisidet» → `Tehtud` already opened `Lisa tagasiside` with
  scripting (docs/adr/0142, amendment of 2026-10-07), but without scripting it
  was a bare `#lisa-kaasamine` anchor to a closed panel.

Reproduced on `574ef23c` before the change (agent F's worklog, `probe-current.txt`):
`POST …/soovitus/<step>/alusta/` for each structured step answered 200, wrote an
`OPEN` `NextAction` linked to the step and **no** `Kaasamine`, `Submission` or
`Ülevaade`; the page then offered the generic `Mida tegid?` beside it. After a
`Kaasamine`, `Järgmisena?` suggested «Koonda tagasiside ja kujunda Koja
seisukoht».

## Decision

### §1 `Alusta` on a structured step opens its canonical form

`Järgmisena?` keeps its one suggestion (docs/adr/0141 §3). When the suggested
step's operation has a canonical form (`app.matters.step_launch`):

| step (`operation`) | form under `LISA TEEMALE` |
|---|---|
| «Kaasa liikmeid / küsi tagasisidet» (`ENGAGEMENT`) | `+ Kaasamine · Alusta kaasamist` |
| «Saada Koja arvamus» (`SUBMISSION`) | `+ Koja arvamus / kohtumine · Koja arvamus` (the family was `+ Arvamus / tagasiside` until 2026-10-17) |
| «Koosta kodulehe ülevaade» (`WEBSITE_OVERVIEW`) | `+ Ülevaade / uudis` |

`Alusta` is a link. With scripting it opens every level of the launcher, names
the step in the form (`samm`, and a visible «Samm: …» line with `Loobu`),
scrolls the form into view and focuses its first field (`static/js/ux.js`,
`data-open-canonical`). **Opening writes nothing** — no `NextAction`, no record,
no plan event. A generic step (`GENERIC`, e.g. «Tutvu materjaliga») keeps its
small `Alusta` editor exactly as before. No simplified form for a structured
step is drawn under `PRAEGUNE TEGEVUS`.

The `start_plan_step` endpoint is unchanged (tests and the ADR 0161 alert still
rely on actions started from such steps before this decision); the page simply
no longer offers it for a structured step. (Amended 2026-10-17 by owner
decision D9: the endpoint now refuses a structured step on the server; see
the amendment below.)

### §2 Without scripting

The link is the Teema page with `?ava=<kaasamine|koja-arvamus|ulevaade>&samm=<step>`
and the form's anchor; `matter_detail` opens the same family and sub-choice and
names the step (`step_launch.apply_open_request`). An unknown value, a step that
is not this Matter's, not of that form's operation or no longer ahead is
ignored. `Tehtud` on «Ootame tagasisidet» is `?ava=tagasiside&kaasamine=<round>`,
which opens `Lisa tagasiside` with that round chosen — only when it is one of
this reader's open rounds. Reads only.

### §3 The step that opened the form is the one it finishes

The three views (`add_engagement_compact`, `add_koda_opinion`,
`add_website_overview`) read `samm` (`step_launch.posted_step_id`: a step of
another Matter or a malformed id is a 404) and, in **one transaction**:

1. lock the Matter and check the step (`app.workflow.plan.launched_step`): on
   this Matter, of the operation this form performs (`STEP_WRONG_OPERATION`),
   still ahead (`STEP_NOT_OPEN`). A refusal writes nothing;
2. call the canonical service, **unchanged** — `add_matter_engagement`,
   `add_matter_koda_opinion` (every evidence, recipient, deadline, follow-up and
   once-token rule as it was), `add_matter_website_overview`;
3. `fulfil_launched_step`: the step becomes `COMPLETED` with
   `fulfilled_by_operation` / `fulfilled_by_record` naming the record, and one
   `PLAN_STEP_COMPLETED`.

A news item recorded from «Koosta kodulehe ülevaade» is not the overview the
step asked for and finishes nothing (the reading `RecordedWork` already uses).

**Repeats.** The first save finished the step under the lock, so a second press
or a replayed POST is refused with `STEP_NOT_OPEN` and writes nothing; the Koja
arvamus once-token still refuses its own repeat first. **Invalid saves** come
back through `_workspace_refusal` with the form still naming the step, so the
corrected save finishes it. **Cancelling** (`Loobu`, the chip, Escape) writes
nothing and the step stays suggested. A form opened from its own chip carries no
`samm` and behaves exactly as before.

After «Kaasa liikmeid» is finished by its round, the round is the waiting state:
«Ootame tagasisidet» under `PRAEGUNE TEGEVUS` (docs/adr/0132). No «Ootan
tagasisidet» step is invented.

### §4 A current structured step is finished through the same form

A current step that was started from a structured step — before this decision,
or carried by `Muuda` — offers `✓ Tehtud` as the same link (§1, §2), naming the
step. The save does §3 and then completes the step's open action through the
one completion service (`complete_next_action`, which promotes the earliest
planned row as `✓ Tehtud` always did). If the Koja arvamus form's own `Märgi
praegune tegevus tehtuks` was ticked too, the action is completed once and the
step is only told which record did it. While the ADR 0161 alert says the record
already shows the step done, the plain `✓ Tehtud` (`Mida tegid?`) is kept,
because the work is already on the file and the canonical form would record it
twice.

### §5 «Koonda tagasiside ja kujunda Koja seisukoht» is no longer suggested

`STANDARD_PLAN` is version 2: «Tutvu materjaliga», «Koosta kodulehe ülevaade»,
«Kaasa liikmeid / küsi tagasisidet», «Saada Koja arvamus». `form-position` is
in `RETIRED_STEP_KEYS`: a Matter seeded with version 1 keeps its row (history,
never rewritten), and `recommendation_for` passes over it while it is ahead. A
step somebody had already started from it stays current work and is finished
like any other; `RecordedWork` and the ADR 0161 proof keep their
`form-position` reading for that case. Only this one suggestion is removed.

### §6 «Ootame tagasisidet» → `Tehtud` → `Lisa tagasiside`

Unchanged in substance (docs/adr/0142, amendment of 2026-10-07): the one form,
the chosen round, its own view. The wait ends only when that save succeeds; an
invalid save or a cancelled form leaves the round waiting. How a reply relates
to the round is the reply contract (QA remediation agent E); this decision only
routes to it.

## Amends

* **0133 §6** — the typed launch returns, but as the `LISA TEEMALE` form itself,
  not as a second instance under the step, and a launched save finishes the step
  by the record rather than through a step action.
* **0141 §2 and §4** — a save may again name a step, as `samm`, only when the
  form was opened from that step; `Alusta` on a structured step starts no
  action.
* **0144 §2** — the `form-position` row of the table applies only to version-1
  plans, and is never suggested.
* **0161** — the alert's reach narrows naturally: structured steps are no
  longer started as generic actions from the page.

## Consequences

* The owner's three paths open the real forms; nothing is recorded by opening
  one, and a launched save cannot be duplicated by a second press.
* The relationship «this record did that step» is stored, by id, on the step.
* `Järgmisena?` goes from a finished consultation straight to the opinion.

## Reversibility

By code. Remove the `recommendation.launch` / `current_launch` branches from
`current_action.html`, the `launch_step.html` include from the three forms and
the `samm` handling from the three views; restore `form-position` to
`STANDARD_PLAN` and empty `RETIRED_STEP_KEYS`. No data needs undoing.

## Amendment of 2026-10-17 — the server refuses a structured `Alusta` (owner decision D9)

The 2026-10-17 audit (DC-G1) found that §1's canonical-form rule was kept only
by which link the template drew. A stale tab or a crafted POST to
`matters:start_plan_step` could still turn «Kaasa liikmeid / küsi tagasisidet»,
«Saada Koja arvamus» or «Koosta kodulehe ülevaade» into the generic
`NextAction` that §1 removed.

`app.workflow.plan.activate_plan_step` now refuses a step whose operation is in
`STRUCTURED_OPERATIONS` (`WEBSITE_OVERVIEW`, `ENGAGEMENT`, `SUBMISSION`, held
equal to `step_launch.FORMS_BY_OPERATION`). The refusal is
`STEP_OPENS_ITS_OWN_FORM`, and nothing is written. The route answers 400 with
that sentence, in the suggestion's own slot. A generic step starts exactly as
before.

Structured steps made current before this record are untouched. They are still
finished through their canonical form, and the ADR 0161 alert still reads
them. The tests that need such a step build it with
`tests/legacy_plans.start_as_before_adr_0168`, through the same lock and
`_start` the service used, because the service no longer creates one.
Pinned by `tests/test_hidden_write_routes.py`.
