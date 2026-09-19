# 0094 — `Uus teema` answers with menus, and asks for one date

**Status:** accepted
**Date:** 2026-09-19

Four corrections to the creation screen, from one round of lawyer feedback. None
of them changes what a Teema *is*: no migration, no vocabulary change, no new
enum value, no altered `Stage` or `Disposition` semantics, no `Matter.track`
write, nothing rewritten on a historical record.

What changes is how much the page asks for, and how much of the page moves while
somebody answers it.

1. `Valdkonnad` is a real multi-select menu rather than a disclosure.
2. `Hetkeseis` is a real single-select menu rather than a permanently drawn chip
   row.
3. `Menetluse link` is open on arrival and asks for an address, not a
   classification.
4. `Arvamuse tähtaeg` is the one date the page asks for, it is the last question
   on it, and `Järgmiseks` is gone from creation.

## Context

ADR 0088 folded `Valdkonnad` and `Menetluse link` behind `<details>` because the
page had grown four blocks each asking for attention before a question had been
answered. ADR 0091 §3 restated the argument for `Valdkonnad` specifically: a
field whose answer is short and whose vocabulary is long should cost one line
when it is not being answered.

Both records are right about the page at rest and neither is about the page being
*used*. A `<details>` folds its contents into the document: opening `Valdkonnad`
pushed `Hetkeseis`, `Õigusakt`, `Menetluse link` and the submit button down the
page while the reader was still ticking boxes, and shutting it pulled them back
up. The lawyers asked for a *rippmenüü* — a dropdown — and a disclosure is not
one however it is styled. The complaint is not that the vocabulary was hidden; it
is that choosing a value re-laid out the form under their hands.

`Hetkeseis` had the opposite shape and the same cost. Eleven chips drawn on every
visit, on the recorded argument that eleven fit on two lines. They do. What that
argument left out is that a lawyer answers `Hetkeseis` once and then reads past
it on every subsequent visit — two lines spent on an answered question, directly
above two more for `Õigusakt`.

`Menetluse link` asked two questions where the lawyer had one answer. The address
is on screen at the moment the file is opened; *which of five official sources it
belongs to* is a classification the form demanded before it would accept the
thing they came to record — and `EIS` arrived pre-selected, so the commonest case
was a chip nobody clicked standing in for an answer nobody gave.

And the bottom of the form asked for the same date twice. `Arvamuse tähtaeg` —
`Matter.response_deadline` — sat beside `Saabus` in the file row. Eight blocks
lower, `Koostan arvamuse` (ADR 0091 §1) asked for a date whose only job was to
establish the opinion-preparation step. Below *that*, `Järgmiseks` offered a
free-text first step with its own `Millal?` chips. Two of the three are the same
question under two names, and the third could only ever collide with them: a
Matter has one open `NextAction`, so answering both had to be a refusal
(`TWO_FIRST_STEPS_REFUSAL`). Three dates, two of them meaning the same thing, and
a refusal to arbitrate between the two that did not.

## Decision

### 1. The `chipmenu` primitive

One component, used twice. A `<details class="chipmenu">` whose `<summary>` is a
pill carrying the field's name and its current answer, and whose panel is
**absolutely positioned** — taken out of flow, overlaying whatever is below it.

The measurable promise, and the one the browser suite asserts rather than
inspecting class names: **the y-position of the field after the menu does not
move when the menu opens.**

`<details>` rather than a scripted `<div>`, deliberately. The element already
gives the trigger one tab stop, opening on Enter and on Space, its own expanded
state exposed to assistive technology, and a control that works with JavaScript
switched off entirely. A hand-built menu gives all of that up and then
reimplements most of it worse. What the script adds is only what a *menu* needs
and a fold does not:

- **Escape closes it**, and returns focus to the trigger;
- **a click outside closes it** (`mousedown`, so the menu is gone before the
  click lands on what was under it);
- **a single-select menu closes once it is answered.**

`aria-expanded` is written by the script and not by the template. A `<summary>`
already exposes its expanded state natively, so a server-rendered attribute
would be a second copy of the same fact — and `<details>` still toggles with
scripting off, so that copy would go stale and announce the opposite of what the
reader sees. Written by the script, it exists only where something keeps it true.

The panel is `width: 100%` of its own row. That is the whole of the narrow-width
answer: a panel that cannot be wider than the row it belongs to cannot put a
horizontal scrollbar on the document, whatever the longest label in it measures.
No positioning library was added for two menus.

### 2. `Valdkonnad` — multi-select, and the trigger counts

Same checkboxes, same `policy_areas` name, same values, same many-to-many, same
`Muu` affordance, same twenty-one governed areas from
`selectable_policy_areas()`. Ticking one does not close the menu; ticking a
second adds it; unticking one leaves the rest.

**The trigger carries a count, not the names.** `Valdkonnad · 3`. A pill on one
line cannot hold three Estonian policy areas, and ellipsised to
«Maksujõuetus, Energee…» the names say less than a number does about whether the
question has been answered — and the names themselves are one click away and
ticked. `MatterCreateForm.policy_area_chosen_count` renders it on load, so a
browser with scripting off gets the right number on every render;
`bindChipSummaries` keeps it true while the menu is open.

**`Muu`'s free-text box lives outside the panel**, under the trigger, and so do
both refusals. Inside, they would be unreachable the moment the menu was shut:
somebody who ticks `Muu` has to type a valdkond, and «Kirjuta, millise
valdkonnaga on tegemist.» printed inside a closed panel is an error nobody can
see — and it is exactly the error that brings a save back. `Muu` still creates no
`PolicyArea` and no `Tag`; the text is `Matter.policy_area_other` and stays free
text.

Which is why `policy_area_disclosure_open` is gone rather than renamed. The fold
rendered itself open on a refusal or a ticked `Muu` because what had to be read
was inside it. Nothing that has to be read is inside it now, so the menu is shut
on **every** render — including a refused one, where the trigger still carries
the count and every box is still ticked.

### 3. `Hetkeseis` — single-select, and the trigger names the answer

`Hetkeseis · Riigikogus`. The name and not a count, because the field holds
exactly one value and the name *is* the compact answer. The asymmetry with
`Valdkonnad` is the two controls saying what kind of question they are, in the
same way the `×` on an `Õigusakt` chip and the absence of one here already do.

«Määramata» is named on the trigger like any other answer. It is a real chip —
`blank=True` exists so that "not decided yet" can be chosen and un-chosen — and
it is what a fresh form arrives with selected. Nothing is invented: the trigger
reports the option the form itself holds.

Selecting a value replaces the previous one (native radio behaviour) and closes
the panel: the question is over at that point and the answer is on the trigger,
and leaving the panel up would hide `Õigusakt` behind something already answered.

**Every business distinction stands.** Same eleven stages from
`StageVocabulary`, same `DescribedRadioSelect` pointing each chip at the
department's own explanation, same `aria-describedby`. `Rohkem ei tegele` is
still a disposition and not a stage. No stage is inferred from anything. Nothing
on this page writes `Matter.track` (ADR 0090 §4).

The Hetkeseis panel is deliberately **not** height-capped, unlike Valdkonnad's.
A scroll container clips its absolutely positioned descendants, and every
explained chip carries a `.stagehelp` bubble that has to be able to hang outside
the chip it opens from. Eleven chips need no cap: the panel is out of flow, so a
tall one costs the page nothing the reader does not already see.

### 4. `Menetluse link` — an address, and a name for it

Open on arrival. No `<details>`, no summary, no `chosen_summary`, no
`disclosure_open` — a labelled `<fieldset>` with two boxes in it and a refusal
that is simply in the page where it happened. ADR 0088's argument was about
*four* competing blocks; there are two now, and what the fold cost instead was a
click before the box could be typed into, on the one question whose answer is
already on the reader's screen.

**The source chips are withdrawn from this surface.** `Nimetus valikuline`
becomes `Nimetus` — a label change only; the field is still `required=False` and
nothing about its validation moved. The explanatory paragraph under the boxes is
deleted and not replaced.

A row recorded here is stored under `ProceduralLinkKind.OTHER` —
«Muu menetluslink», which is what an unclassified link truthfully is, and a value
the enum already documents as *a real answer rather than a gap*.

- **No new enum value and no migration.** The other four values are untouched.
- **Not a hidden input, a constant.** `ProceduralLinkCreateForm.STORED_KIND`.
  The kind is not the lawyer's statement on this surface, so it is not part of
  the request either: a forged POST carrying `menetlus-kind=EIS` is read by
  nothing, because there is no field to bind it to.
- **Nothing is inferred from the address.** No hostname rule, no fetch, no
  scrape, no guess. ADR 0089 §2 says at length why: a ministry runs several
  registers, an EU file is read on EUR-Lex one month and a consultation page the
  next, and a register that moved domain would silently reclassify every row
  stored under a host rule. Automatic classification is a possible future
  enhancement and is explicitly not attempted here.
- **Nothing historical is rewritten**, and the four withdrawn values are still
  offered where a person states the fact deliberately: the Teema page's own
  `+ Menetluse link` panel and the correction form. `PROCEDURAL_LINK_NEEDS_KIND`
  stays, because both of those still ask the question and both still refuse an
  unanswered one.

### 5. One `Arvamuse tähtaeg`, and it is the last question

`MatterCreateForm.response_deadline` moves out of the file row to the bottom of
the form, directly above `Loo teema`, and it is the only date a lawyer enters
while filing. The heading is `Arvamuse tähtaeg`, which is what this date is
called on the process strip, in the rail, on Minu asjad and in the register — so
the page and the record use one word.

`InitialOpinionActionForm` and its `Koostan arvamuse` box are retired. A date
entered in the one remaining box now does both things it used to take two boxes
to say:

- it records the obligation on `Matter.response_deadline`, as it always did;
- and it establishes the canonical `Koostan arvamuse` step, exactly once,
  through `establish_opinion_preparation_action` — unchanged, still idempotent
  against an equivalent open step, still `DO` / `DEADLINE` / `EXACT`, still
  reading its sentence from `OPINION_PREPARATION_TEXT` so that what a person is
  shown and what saves are one string.

**This is a deliberate reversal of ADR 0091 §1.2 on one point**, and it is the
only product-model change in this record. That ADR declined to establish the step
from the consultation deadline, listing it among the dates it would not invent
from: «not today, not seven days out, not the consultation deadline». The
reasoning was that the two are different facts — what Koda owes, versus when the
lawyer plans to have written it — and it is sound. What made it wrong in practice
is that the page then asked for both, in two places, under two names, and the
lawyers read them as one question. A distinction nobody can act on at capture
time is not being preserved by asking twice; it is being converted into double
entry, which is the thing this product exists to remove.

The distinction itself is **not** abolished. `Matter.response_deadline` and
`NextAction.target_date` remain separate columns with separate meanings, ADR 0078
§3 stands, `process_timeline.py` still draws the obligation and not the plan, and
a lawyer who plans to finish earlier than the deadline changes the step in the
composer — which is an act with its own audit row. What changed is only what the
*creation screen* asks: one date, and both facts start from it.

**Nothing is invented from a blank box.** No obligation, no step, no undated
commitment, no default. `response_deadline` still carries no `initial`, unlike
`Saabus` directly beside it, and for the reason it never did: `Arvamuse tähtaeg`
is a commitment and usually somebody else's, and a Matter created and left alone
must not be overdue the next morning (ADR 0078 §2).

### 6. `Järgmiseks` is off the creation screen

The whole block goes: the text box, `Millal?`, `Täna`, `Homme`, `+1 nädal`,
`+2 nädalat`, the `Kuupäev…` disclosure and the quick-date chips. `NextActionForm`
is no longer bound on this view, `quick_dates` is no longer in its context, and
there are no hidden inputs left behind — so no stale form state can create a
generic first step from here.

A first step on the capture path is `Koostan arvamuse`, from the date above. A
*different* plan is stated where a plan is changed: the Teema composer, which is
unchanged and still offers everything it did. `TWO_FIRST_STEPS_REFUSAL` is
retired with the collision it arbitrated — one question cannot disagree with
itself. `workflow_one_open_action_per_matter` is untouched and remains the
enforcement, in the database, where it always was.

## Alternatives considered

**A native `<select multiple>` for `Valdkonnad`.** Rejected, and rejected before
by ADR 0025: it hides multi-selection behind a modifier key nobody uses, it is
unstyleable, and on a touch device it is a platform sheet rather than the page.
The lawyers asked for a dropdown, not a listbox.

**A scripted `<div role="listbox">` pair.** Rejected. It would have to
reimplement the trigger's tab stop, its Enter/Space activation and its expanded
state, and would leave the vocabulary unreachable with scripting off — which this
form does not do anywhere else.

**Keep `<details>` and only restyle it.** Rejected explicitly: that is the shape
the feedback was about. A fold that still lengthens the document has not answered
the complaint, whatever it looks like.

**Automatic classification of a procedural link from its URL.** Rejected for
this round, on ADR 0089 §2's own reasoning. Recorded as a possible future
enhancement rather than declined permanently.

**Keeping two dates and renaming only the lower heading.** Rejected. It would
have put two identically named date boxes on one page, given a personal work plan
the canonical name of a formal obligation, and left the double entry in place
under better labels.

**Dropping `Matter.response_deadline` from creation instead.** Rejected. It is
the field every deadline surface in the product reads, and losing it on the
capture path would mean a Teema filed from a consultation carries no obligation
until somebody goes back and adds one.

## Consequences

- No schema migration. `makemigrations --check --dry-run` reports no changes.
- `templates/matters/partials/initial_opinion_action.html` is deleted;
  `InitialOpinionActionForm` and `TWO_FIRST_STEPS_REFUSAL` are removed.
- `ProceduralLinkCreateForm` loses its `kind` field, its `clean`, its
  `chosen_summary` and its `disclosure_open`.
- `MatterCreateForm` loses `policy_area_summary` and
  `policy_area_disclosure_open`, and gains `policy_area_chosen_count` and
  `stage_summary`.
- Two visual baselines change materially (`uus-teema`, `uus-teema-viga`): the
  page is shorter by the two retired panels, the source chip row and two
  paragraphs, and two vocabularies are pills rather than chip rows.
- A menu open at the moment of a screenshot overlays the field below it. That is
  the intended behaviour and is what the layout scenario measures.

## Reversibility

Wholly reversible, in four independent pieces. The menus are a template shape
plus one CSS component plus one binder; reverting them restores the chip rows
with no data implication at all. The procedural-link default is one constant and
one template block. The date merge is the only part with a behavioural
consequence, and reverting it means restoring `InitialOpinionActionForm` and
moving `response_deadline` back into the file row — the Matters created in
between keep a `response_deadline` and a `Koostan arvamuse` step, which is what
either arrangement produces.
