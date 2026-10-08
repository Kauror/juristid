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
