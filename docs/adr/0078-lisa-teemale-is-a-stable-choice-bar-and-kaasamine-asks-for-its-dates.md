# 0078 — `LISA TEEMALE` is a stable choice bar, and `+ Kaasamine` asks for its dates

*Accepted 2026-09-14.*

Two findings from using the Teema workspace on real files. They are unrelated as
defects and are decided together because they are the same panel.

## 1 — The control moved when you used it

`LISA TEEMALE` offers seven operations. Since ADR 0075 each has been a
`<details>` whose summary is a chip, and the seven chips wrap across one row.
An open panel took:

```css
#teema-vaade-wrap .cx-panel[open] { flex: 1 1 100%; order: 1; }
```

Which does exactly what it says, and is wrong for this control. The element that
grows *is* the element you press: clicking `+ Kaasamine` made its chip a
full-width flex item at the end of the ordering, so the chip jumped to the head
of the next line and every chip after it shifted along. Choosing a second
operation moved them all again. A person who has just clicked a control finds it
somewhere else, and the row they were reading is a different row.

No value of `order` fixes this, because the geometry follows from one element
being both the control and the container.

### Decision

**The launcher is a stable choice bar of controls, and the form is a separate
element below it.** Each operation is a visually-hidden radio, its visible
`<label>` chip, and its `.cx-panel` form, in that order:

* every chip is `flex: none` and never changes size, line or position;
* the chosen form is the only item that ever takes `order: 1` and
  `flex: 1 1 100%`, so it lands on its own line under the whole row;
* `:checked + .disclosure-chip` paints the active chip in the existing accent
  tokens, and `:checked + .disclosure-chip + .cx-panel` reveals its form.

Two CSS rules, and the launcher cannot reflow when a form opens because nothing
in the launcher changes.

### Why a radio group

One `name` gives the product's "only one open at a time" rule to the browser.
`ux.js` no longer closes the other six on a `toggle`; it adds exactly one thing
a radio group cannot do, which is going back to nothing chosen when you click
the chip that is already active.

It also keeps the properties the `<details>` had:

* **Scripting off.** The bar still opens every form, switches between them, and
  keeps the panel a refusal came back in — the server renders `checked` on the
  radio it used to render `open` on. What is lost is the click-to-close.
* **Keyboard.** A native radio group is arrow-key navigable and announced as a
  group of seven. The radio is clipped rather than `display: none` precisely so
  it stays focusable, and the focus ring is drawn on the chip.
* **Accessible state.** `:checked` is the state, exposed natively, rather than a
  class painted on. Each radio carries `aria-controls` naming its form.

`role="tablist"` was considered and rejected: it would owe a full tab keyboard
contract, and a radio group already has one.

`Muuda` in `PRAEGUNE TEGEVUS` stays a `<details>`. It is alone on its line, has
no siblings to displace and nothing to be chosen instead of, so a native
disclosure is still the honest markup for it.

## 2 — `+ Kaasamine` was dating consultations for you

ADR 0074 §9 decided the panel would ask for no engagement date, and
`add_engagement_compact` passed `timezone.localdate()` on every save:

> The day the work is being recorded. The target deliberately does not ask for
> an engagement date, and the application's convention for «this happened as
> part of the work I am writing down now» is today in Europe/Tallinn.

Half of that is true and is kept. The other half is not what a lawyer does: a
consultation is routinely typed up days or months after it happened, and the
panel answered that by filing it as having happened today — a false fact,
written by the server, with no box on the screen anybody could have corrected.

### Decision

**`Kaasamise kuupäev` is a visible, optional box, pre-filled with today.**

Today stays the default because the overwhelming case is recording something
that just happened, and retyping today's date every time is the friction people
actually complain about. What changes is that the default is *visible*: it can
be read before saving, changed, and emptied. An emptied box stores `NULL`, which
is «kuupäev teadmata» — a fact `MatterEngagement.occurred_on` has always been
able to hold and nothing has ever been able to mean. No code puts today back.

This supersedes ADR 0074 §9 for the panel. The stored model is unchanged and no
existing row means anything different than it did.

## 3 — `Tagasisidet ootame kuni`

A lawyer starting a consultation says «ootan vastuseid kuni 22.09» in the same
breath. The file has had nowhere to keep that.

### Decision

**One new nullable column, `MatterEngagement.feedback_deadline`, and it is
inert.**

It records what was asked *of other people*. It is not work:

* no `NextAction`, no `MatterImportantDate`, not `Matter.response_deadline`;
* no work item, no overdue badge, not even when the day has passed;
* no count, no statistic, no filter, no register sort;
* not indexed, because nothing reads it that way.

The distinction is load-bearing. What is owed *by this office* is work and is
already modelled; what was asked of a ministry or of the membership is a
recorded fact about the round. A column that generated a task would make every
historical consultation somebody types in overdue on the day it is entered.

Optional and undefaulted: today is a plausible engagement date and never a
plausible reply-by date, so a pre-filled one would be answered by pressing
`Salvesta`. A past deadline is accepted, because a consultation recorded months
late had its deadline months ago. The only refusal is a deadline *before* the
engagement it belongs to, which is not a late round but a typo, and it is
reported on the deadline field because the engagement date is the anchor.

Shown, when it is set, on the engagement's own chronology row —
`Tagasisidet ootame kuni 22.9.2026`, after the kind and the response count. When
it is not set the row says nothing at all: every row written before today has
this absence, and «Määramata» would be an absence the reader has to decode.

### The migration

One additive nullable `DateField`. No database default, no `RunPython`, no
backfill, no table rewrite. Every existing row reads back `NULL`, which is the
truthful answer to a question nobody was asked until now — and deriving a
deadline from `occurred_on`, `created_at`, the note or a provider link would put
a date on the file that nobody set.

`update_engagement` takes it behind the existing `_UNSET` sentinel, so every
caller that predates the column — the importer, the register enrichment, the
opinion mapping refresh — leaves it exactly as it was.

## Explicitly not decided here

Date *precision* for either date (`Täpne päev` / `Kuu` / `Kvartal` / `Aasta`),
an editing UI for a recorded engagement, a response-obligation model, a
feedback-deadline work item, a deadline filter, and any new statistic. All
remain open questions.
