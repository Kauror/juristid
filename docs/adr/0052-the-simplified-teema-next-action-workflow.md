# ADR 0052 — The simplified Teema next-action workflow

**Status:** accepted
**Date:** 2026-08-31
**Supersedes in part:** ADR 0030 §4 (*The description is the next step*), and the
parts of ADR 0031 that describe the composer's mode row and the words the
`Järgmiseks` row prints beside a date.

## Context

The Teema composer asked a lawyer to classify their own work. Before it would
record a next step, the form wanted one of `TEEN`, `OOTAN`, `JÄLGIN` or
`Ei muuda`, and — one disclosure deeper — what the date beside it meant:
`Tähtaeg`, `Vaatan üle` or `Oodatav`. The `Järgmiseks` row then printed the
answer back on every visit, as a coloured chip and as a word in front of the
date.

Two things are wrong with that, and hands-on use surfaced both.

**It is a vocabulary, not a fact.** `WAIT` and `MONITOR` were not observed in
the department's work; they were introduced to describe it. A lawyer who has to
remember to check whether a ministry replied does not think "this is an OOTAN".
They think *kontrollida, kas ministeerium vastas* — which is an action, on a
day, and it fits `DO` exactly.

**It broke the one thing the composer had to get right.** ADR 0030 §4 made the
description *be* the next step: choosing a mode turned the entry body into the
action's text, verbatim. That only ever worked because the mode was the thing
that said "and this sentence is also an instruction". It meant a lawyer could
not write *what happened* and *what happens next* in one save without one
sentence standing in for both — and «Ministeerium lubas saata uue versiooni
nädala lõpuks» is a terrible next step, because it is not one.

The source the register was built from settles it. The Excel `JÄRGMISEKS`
column was free text. Nobody classified anything; they wrote the next thing
down.

## Decision

### 1. The stored domain is unchanged

`ActionKind` and `DateSemantics` keep all their values, and no row is rewritten.
`DO` / `WAIT` / `MONITOR` and `DEADLINE` / `REVIEW_ON` / `EXPECTED_AROUND`
remain valid historical and imported data, and every service, selector,
statistic and register parser that reads them keeps reading them. There is no
migration in this change, and none was needed.

What changed is that the native Teema composer no longer asks a person to pick
one.

### 2. `body` and `next_text` are two fields

`ComposerForm` now carries both, and neither is derived from the other in either
direction. No NLP, no sentence splitting, no summarisation, no copying. What
somebody types into `Järgmiseks` is what the `NextAction` stores, after the
form's ordinary trim and 2000-character cap.

This is a straight reversal of ADR 0030 §4. That decision refused a second box
as duplicate data entry, which was right about the box it was looking at — the
old form's second box asked the *same* question in different words. These two
ask different questions, and a single save answering both is the whole point:

    body:       Ministeerium lubas saata uue versiooni nädala lõpuks.
    next_text:  Vaadata uus eelnõu versioon üle

Two facts, two canonical records, one transaction.

### 3. A native next step is `DO` / `DEADLINE` / `EXACT`

Recorded internally, never shown, and never posted by the page — the form has no
field that carries a kind or a date meaning, so a crafted POST cannot name one
either.

The values are honest rather than merely convenient. On this surface the date
means *when I will have done this*, which is what a `DO` with a `DEADLINE`
already means. The reading it replaces — "when do I expect somebody else to
act?" — is now written as what it is: an action to check, on the day to check
it.

### 4. One date question, and no precision machinery

`Millal?` keeps the four quick spans (`Täna`, `Homme`, `+1 nädal`,
`+2 nädalat`) and the exact box behind `Kuupäev…`, unchanged, still resolved on
the server in Europe/Tallinn and still submitted through the one date field the
chips write into.

The `next` precision group is deleted rather than hidden. A lawyer's own working
day is a day. The approximate-period control genuinely earns its place on
`Oluline tähtaeg`, where a consultation really does end "in the autumn", and it
is untouched there — as is every stored approximate `NextAction`, which still
renders at the precision it was recorded to.

### 5. The validation contract

| `body` | `next_text` | `next_date` | Result |
| --- | --- | --- | --- |
| set | — | — | `Entry`. The open step, if any, stays open and unchanged. |
| — | set | set | `NextAction`. **No empty `Entry`.** |
| set | set | set | Both, in one transaction. |
| any | set | — | Refused on `next_date`: «Vali järgmise tegevuse kuupäev.» |
| any | — | set | Refused on `next_text`: «Kirjuta järgmine tegevus.» |
| — | — | — | The existing attachment, deadline and closure paths still save on their own. Nothing at all is still refused. |

An undated step is never quietly filed for today. Deciding when somebody will do
their own work is not the application's to decide.

### 6. The `Järgmiseks` row shows text, date and a way to finish it

The mode chip is gone and so is the word in front of the date. A historical
`WAIT` or `MONITOR` keeps its stored kind, semantics and precision, renders as
its sentence and its date, and can be completed from this surface like anything
else; a step with no date shows no date rather than being given one.

Lateness survives as the state of the row and the colour of the date, with the
day count riding along — `is_overdue` is unchanged and still means `DO` +
`DEADLINE` + a date in the past, which is every step this composer creates.

`Minu töö`, the register and the timeline are unchanged and still say which kind
a step is. This decision is about the Teema detail surface.

### 7. `✓ Tehtud` writes no entry

Completing a step goes through the existing `complete_next_action`. The action
becomes `COMPLETED`, stays in the history, and raises the canonical
`NEXT_ACTION_COMPLETED` event — which is already the evidence that the work was
done.

It does **not** manufacture an entry reading "Helistasin ministeeriumisse". The
system knows what was completed; putting that sentence in a lawyer's record
under their name would be the application writing their file for them. Anything
worth recording is written in the composer, in their own words, as a separate
save.

### 8. Completing is not the same as replacing

Saving a new `Järgmiseks` while one is open goes through `set_next_action`, so
the old row becomes `SUPERSEDED` and the new one `OPEN` — as it always has. A
superseded step is not a completed one, and nothing infers otherwise. If the
work was actually done, the lawyer presses `Tehtud`; the distinction stays
legible in the audit history even though neither word appears in the UI.

### 9. `Tehtud` swaps one row

`complete_action` used to re-render `#teema-vaade`, which is the row **and the
open composer under it**. Finishing a step therefore threw away everything
somebody had typed about it — at precisely the moment they were most likely to
be typing it.

It now renders `next_action_row.html` into `#jargmiseks-rida`. The completion is
persisted before the response either way; the chronology catches up on the next
render of the page. `Lükka edasi` and `Vaatasin üle` keep their existing
full-column swap, which is pre-existing behaviour this change does not touch.

### 10. `Sildid` leaves the Teema detail page

The card printed «Silte ei ole.» on nearly every Matter. It is removed from the
read surface — and **only** from the read surface. `Tag` and `TagAssignment`,
every imported assignment, the legacy importer's tag support and the tag field
on the Teema edit form are all untouched. No backfill, no deletion, no data
migration.

`Muu valdkond` moves with the card's removal but is not part of it: it is not a
tag, nothing counts it, and it only lived there because that card is where the
taxonomy-adjacent things had collected. It is now a row of the `Teema andmed`
facts block, with the same inline edit it had before.

## Consequences

- The Uus teema form (`NextActionForm`, `templates/matters/matter_create.html`)
  was left asking for a kind and a date meaning, as a deliberate follow-up
  surface: it was being changed concurrently by another branch when this
  decision was taken. **That follow-up is now done** — see the addendum below.
- `default_date_semantics` no longer has a caller. It stays in
  `app/workflow/enums.py` with its mapping: DO → DEADLINE, WAIT →
  EXPECTED_AROUND, MONITOR → REVIEW_ON is the domain's own statement of what
  each kind's date ordinarily means, and §1 of this decision is that the stored
  domain does not move. It is simply not a question any form asks now.
- Reporting and search read stored values, so nothing downstream moved.
- No schema change. `makemigrations --check` stays clean.

## Addendum — Uus teema, 2026-08-31

The follow-up named in the first consequence above is implemented. Uus teema
now uses the same native contract as the composer, and this section records
what that changed. Nothing above is rewritten; the decision is unchanged and
this is its second application.

### What the page asks

`Järgmiseks` over a free-text box, then `Millal?` — `Täna`, `Homme`,
`+1 nädal`, `+2 nädalat` and the exact box behind `Kuupäev…`. The four spans
are the composer's own, rendered from the same `quick_date_choices` and bound
by the same `data-quickdate-group` contract, so the arithmetic happens once, on
the server, in Europe/Tallinn.

Gone from the page: the TEEN / OOTAN / JÄLGIN mode row, and the
Tähtaeg / Oodatav aeg / Vaatan üle chips beside the date. Nothing replaced
them — they are not a control this surface has any more.

### What the form contract is

`NextActionForm` no longer has a `kind` field or a `date_semantics` field.
They were removed from the contract rather than hidden in the template, so a
POST naming `kind=WAIT` or `date_semantics=EXPECTED_AROUND` arrives as an
unknown key and cannot reach the stored record. A native step is
`DO` / `DEADLINE` / `EXACT`, written in `as_service_kwargs` and read from
nothing.

`target_date` lost its `initial=timezone.localdate`. A blank new-Teema form no
longer silently carries today as a factual next-action date.

### What is refused

The composer's table, on this surface, with `Pealkiri` in the place of `body`:

| `next-text` | `next-target_date` | Result |
| --- | --- | --- |
| — | — | Matter created. No `NextAction`. |
| set | set | Matter + `NextAction`, one transaction. |
| set | — | Refused on the date: «Vali järgmise tegevuse kuupäev.» |
| — | set | Refused on `Järgmiseks`: «Kirjuta järgmine tegevus.» |

The last row is why the view's "did somebody ask for a next step" signal now
reads **either** half. It read `next-text` alone, which was right while the
date arrived pre-filled — a date meant nothing then. With the default gone, a
date is a choice somebody made, and dropping it silently would have created the
Teema without the step they asked for.

A refusal writes nothing at all. The Matter, its files, its personal note, its
addressee and its next step are one `transaction.atomic` block, and the next
action is validated before it opens.

### What did not change

`ActionKind`, `DateSemantics`, the `NextAction` schema, the register parser,
the historical import, reporting, `Minu töö`, the register filters and the
timeline. Every stored `WAIT` and `MONITOR` row keeps its kind and still
renders as one. `makemigrations --check` is clean; there is no migration.

The responsible-person rule is untouched: the field is still unrendered, still
populated from `assignable_users()`, still defaults to the Vastutaja chosen on
the same form, and an explicitly named colleague still wins. Eligibility is
still enforced by `set_next_action_for_new_work`.

`.modechip` and `.modeselect` stay in `static/css/app.css` with no renderer.
They are the shared shape language for a stored action kind — TEEN filled,
OOTAN solid-outlined, JÄLGIN dashed — and any surface showing a historical kind
is entitled to reach for them. What was deleted is only what was specific to
the retired panel: `.nextpanel__line`, `.nextpanel__text`, `.nextpanel__date`,
`.nextpanel .chiprow`, `.chip--meaning`, and the `bindDerivedMeaning`
derivation in `static/js/app.js` that moved the meaning chips when a kind was
chosen.
