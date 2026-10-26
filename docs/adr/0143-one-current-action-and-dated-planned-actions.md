# 0143 — One current action, and any number of dated planned actions

**Status:** accepted
**Date:** 2026-10-06

The owner's integrated UX round. **One migration**, `workflow/0013`: a new
`NextAction.status` choice, `PLANNED`, and a check constraint that a planned
action carries a date. No data migration, and no existing row is rewritten:
every action that was `OPEN` stays the current action.

## A. Planned actions

1. **One current action, many planned.** `OPEN` stays the current action and
   `workflow_one_open_action_per_matter` still allows one per Matter. `PLANNED`
   is a dated future action; any number may exist beside the current one, and
   `workflow_planned_action_is_dated` refuses one without a date.
2. **`+ Määra järgmine tegevus` is always offered on an open Matter.** With no
   current action it sets the current one, as before. With a current action it
   adds a planned one (`add_planned_action`): text and date both required, `DO`
   / `DEADLINE` / `EXACT`, the departed-owner rule new work follows. It never
   supersedes the current action or another planned one.
3. **`PLANEERITUD TEGEVUSED`** lists them under the current row, by date and
   then the order they were planned in, each with `Muuda` (a superseding
   planned row that keeps the responsible person and the restriction) and `×`
   (cancelled, kept in the history). Every write locks the Matter; a stale tab
   naming a row that is no longer planned is refused with nothing written.
4. **Completing the current action promotes the earliest planned one**
   (`promote_next_planned_action`, inside the completion's transaction). The
   row keeps its text, date, responsible person and restriction; only its
   status moves, audited as `NEXT_ACTION_SET` with `promoted`. A completion
   that names its own next step promotes nothing — the planned rows stay
   planned.
5. **The recommendation is the fallback only.** `Soovitatud järgmisena`
   (docs/adr/0141) shows when there is neither a current nor a planned action.
   Manual actions are never linked to a plan step by their words.
6. **Work surfaces show dated planned actions** beside current ones
   (`work_items.dated_actions`, the dashboard window), and the closed-Matter
   invariant counts both.
7. **Closing a Matter cancels the current action and every planned one**
   (`end_open_action_for_closure`, reason «Teema suleti»), each kept in the
   history.

Selectors audited for the «one `OPEN`» assumption: `current_next_action`,
the recommendation, the overdue and work-list queries still mean the current
action; only the dated-work queries above, the invariant and the owner hand-over
(`assign_matter`: planned steps that follow the file move with it) read `PLANNED`
too. The legacy cutover commands still cancel only `OPEN`; they predate this.

## B. Rail and chronology readability (no schema)

- `Koja arvamus` with nothing yet reads «Puudub»; `MENETLUSE LINK` with
  `+ Lisa`; `Seotud materjalid` is one heading with a count, an empty card is
  `+ Lisa` alone, and a row is the title as a link and `×` (Eemalda seos).
- `Saatja` in Teema andmed is stacked: the label above, one sender per line.
- `Tegevused`: a save that completed the current action reads «✓ <what was
  done>» with a small green check, and not «märkis eelmise sammu tehtuks»
  plus the same note again; a correction of that note refreshes the line. A
  row that only set a step is one line, «Järgmine samm – … 4.10.2026»
  («Planeeritud tegevus – …» for a planned one), and the next step under any
  other save is plain text, not a boxed pill. The chronology shows dates
  only; `Kõik muudatused` keeps the time.
- `Liige` sits at the end of the organisation search row.
- Required fields keep the red `*` of docs/adr/0140 §6; nothing says
  «valikuline».

## Amendment of 2026-10-07 — date first, and no type label in the chronology

**Date-first rows.** Every dated row in `PRAEGUNE TEGEVUS` opens on its day, in a column of its own:
- the current action, which reads «Kuupäev määramata» (muted) when it has no date;
- each planned action;
- a round's feedback wait, which reads «Tähtaeg määramata» when it has no deadline, followed by «Ootame tagasisidet»;
- the upcoming `Oluline tähtaeg`.

The text takes the rest of the row and wraps under itself, never under the date. The controls stay on their row. Planned rows are separated by a hairline, not boxed as cards.

**`+ Lisa · Tavaline` is gone** (docs/adr/0097 §6).
- Ordinary work is `+ Lisa tegevus`, and what was done is `✓ Tehtud`.
- `L` opens `✓ Tehtud` beside a task, and `+ Lisa tegevus` on a Matter without one.
- `add_note` and its records remain; no Märge is rewritten.

**The chronology prints a planned action's own words.** It no longer prefixes them with «Planeeritud tegevus –». «Järgmine samm – …» is unchanged.
- `TimelineItem.step_only` reads the `NEXT_ACTION_SET` event rather than the sentence's prefix.

## Amendment of 2026-10-08 — a planned row can be marked «Tehtud»

Each planned row offers `✓ Tehtud | Muuda | ×` (docs/adr/0144 §1). `✓ Tehtud` finishes that action on any day, with what happened and optional files, and promotes nothing; §A4's promotion stays the current action's. `×` reads «Kustuta planeeritud tegevus». §A5's recommendation is `Järgmisena?` and reads the record (docs/adr/0144 §2).

## Amendment of 2026-10-08 — an `Arvamuse järelkontroll` is a planned action that can be late

A sent opinion now schedules a dated check of whether the addressee answered
(docs/adr/0146). It is an ordinary `PLANNED` row with `NextAction.follow_up`
set:

- It is promoted by §A4 like any other planned row.
- Closure cancels it by §A7, but only once the person confirms (docs/adr/0146 §8).
- **§A6 gains one reading.** A planned check whose day has passed is overdue on
  the work surfaces (`NextAction.is_overdue`), which no other planned action is.
- New work written over a *current* check sends it back to the plan instead of
  superseding it.

## Amendment of 2026-10-10 — the step added without naming one is decided under the lock

Audit AUD-004 and AUD-040 reproduced two ways a person's current step was
silently superseded: two saves of `+ Määra järgmine tegevus` landing together,
and a page drawn before a colleague's save. Both forms decided «is a step
open?» on a read taken before the Matter's lock. The product owner's decision
D-7 (2026-10-10) settles the stale case. Both forms now call one use case,
`workspace.add_unnamed_step`, which locks the Matter and then decides:

- **No step is open** → the save is the current step (`DO` / `DEADLINE`).
- **A step is open and the save has a day** → a planned row on that day, as
  §A2 always said. It keeps the period a person chose on the current-step
  panel («oktoober 2026»), rather than becoming the 1st.
- **A step is open and the save has no day** → refused with the stale-step
  sentence (`STALE_ACTION_REFUSAL`), and nothing is written.

The open step stays open in every case, including one the saver may not see
(a step restricted below its Matter). That step is not superseded either, and
new work over it still inherits nothing (docs/adr/0139 §5). New work never
borrows the kind or date meaning of the step that is open. The owner the step
defaults to is read from the locked row (AUD-052 WF-4). The other new-work
doors are unchanged: `+ Märge` with a day ahead, `Järgmisena` after a
completion, `Alusta`, and `+ Menetluse areng` with a next step.

## Amendment of 2026-10-17 — `add_note` is a guarded test door (owner decision D9)

The 2026-10-07 amendment kept `add_note` reachable after `+ Lisa · Tavaline`
left. The route still writes a `Märge`, its files, a `Hetkeseis` and a next
step through `MatterProgressForm`, but no page posts to it any more. The
2026-10-17 audit (DC-G1) named it a hidden write route. The owner decided (D9)
that it goes once its test uses move to real doors. More than a hundred test
references post to it as a fixture door, in unit tests and in browser
tests. Moving them is not mechanical, because several are about a stage move or
a next step written from a `Märge`. So the route is **guarded** until then:

- It answers only where `ADD_NOTE_TEST_DOOR_ENABLED` is on. That setting
  defaults to `DEV_LOGIN_ENABLED`, so it is on in development and in the browser
  suite's servers. `config/test_settings.py` turns it on for the unit suite.
- Everywhere else it answers 404 and writes nothing, as a retired route does.
- With `REAL_DATA_ALLOWED` it answers 404 whatever the flag says, so a
  mistyped flag on a real-data deployment cannot reopen it.

`Märge` records already on a file are unchanged and are still corrected through
`Muuda`. The remaining test uses are listed in the PR that added this guard,
for the follow-up that removes the route. Pinned by
`tests/test_hidden_write_routes.py`.

`matters:update_position` (docs/adr/0061) is removed by the same decision. A
stale POST to it is a 404 (`tests/test_retired_write_doors.py`).

## Amendment of 2026-10-23 — `PRAEGUNE TEGEVUS` reads earliest day first (#525)

The owner's rule: an earlier day comes before a later one, whatever the row is.
The zone drew three blocks in a fixed order — the current action, the rounds
waiting for feedback (docs/adr/0086 §3), then the planned actions — each sorted
only within itself, so `02.11 — Ootame tagasisidet` read under
`04.11 — Koostan arvamuse`. That "current action first" order is superseded.

- The three are one list (`templates/matters/partials/current_work_rows.html`),
  ordered once in `app/matters/current_work.py`: by the day each row shows — a
  step's `target_date` (for a period, its stored anchor, the value
  `NextActionQuerySet.planned` already orders by, docs/adr/0079), a round's
  `feedback_deadline`. Undated rows follow every dated one and keep their
  «Kuupäev määramata» / «Tähtaeg määramata». On an equal day the current action
  comes first, then the rounds, then the planned actions — the former block
  order — and within one kind the source's own order stands.
- **Display only.** Which action is current, promotion of the earliest planned
  action, lateness, waits, completion and every row's controls are unchanged;
  each source is read through its own visibility exactly as before.
- The order is the DOM's, not a CSS `order`. A round's own answer
  (`Lõpeta kaasamine`, `Ootan tagasisidet`, a round's `Muuda`) still swaps only
  its chronology row and carries the list out of band, re-sorted; the current
  action and the planned rows ride in it as read-only lines marked
  `hx-preserve`, so the page keeps their elements — and anything typed in their
  forms — and only their position changes. The box keeps the id
  `praegune-ootused`.
- `+ Lisa tegevus` stays after the list, outside it. No schema, no migration.

Pinned by `tests/test_current_work_chronology.py`,
`tests/test_current_work_refreshes_after_round_answer.py` and
`e2e/test_current_work_chronology.py`.

## Amendment of 2026-10-26 — «Nihuta kuupäeva»

**Owner's brief «Final Feature Development», feature B.** A lawyer can move an
eligible internal task's day by **+1 päev**, **+1 nädal** or **+1 kuu** without
opening the full editor: from a Minu asjad work row's `⋯` menu, and from the
Minu asjad calendar's event details. The label is «Nihuta kuupäeva», never
«Pikenda tähtaega»: this changes a plan, not a statutory or external date.

**Eligible, and nothing else** (`app/matters/date_shift.is_shiftable`, and the
row-side twin `WorkItem.is_date_shiftable`): a `NextAction` that is `OPEN` or
`PLANNED`, on an open Matter, `ActionKind.DO` with `DateSemantics.DEADLINE`,
`DatePrecision.EXACT` with a recorded day, not an `Arvamuse järelkontroll`, and
carrying a responsible person; the person needs business write
(`business_write_required`) and visibility (`visible_to`). Not offered for an
`Arvamuse tähtaeg`, a statutory or external date, a commencement, an `Oluline
tähtaeg`, a feedback wait, a WAIT/MONITOR review date, an approximate, inferred
or undated step, a finished one, a closed Matter, or a row the reader may not
see. Broadening this list is a product decision.

**Arithmetic.** From the *stored* day, never today: a day, seven days, or one
calendar month clamped to the shorter month (`app.core.dates.add_months`:
31.1 → 28.2/29.2). The three proposals are computed on the server and the chosen
one is previewed before `Salvesta`; nothing is written by choosing.

**One write path.** `POST /teemad/<pk>/tegevus/<action_id>/nihuta/`
(`shift_action_date_view`, CSRF, writer-only, 404 for a hidden or foreign id)
calls `date_shift.shift_action_date`, which under the Matter's lock
(`lock_open_matter_for_business_write`) re-reads the named row *of this
Matter*, refuses it if it is no longer current/planned or its day is not the day
the person reviewed (`SHIFT_STALE`) or it is not eligible (`SHIFT_NOT_ELIGIBLE`),
recomputes the new day and checks it against the previewed one, then hands
every other value off the locked row to the canonical edit:

* current — `workspace.change_current_action` (supersedes the row; carries plan
  step and restriction), with the row's own `responsible` passed explicitly so
  the step is never reassigned to the Matter's owner;
* planned — `workflow.services.change_planned_action`, which now also carries
  the plan step and source sentence (as `Muuda` on the current step already
  did).

The audit trail is theirs: one `NEXT_ACTION_SET` naming the replaced row, which
keeps the old day. A repeated press or a concurrent second save names a row that
was just superseded and is refused — one move, one row, one event
(`tests/test_date_shift_concurrency.py`). Nothing else is written: no
completion, note, meeting, stage move, closure, opinion deadline, commencement
or consultation change. Post/redirect/get back to the desk it was pressed on
(only `matters:my_work` / `matters:person_work`), keeping horizon and calendar
period; the message «Kuupäev muudetud: 5.11 → 12.11.» appears only after the
save committed, a refusal says why. No migration.
