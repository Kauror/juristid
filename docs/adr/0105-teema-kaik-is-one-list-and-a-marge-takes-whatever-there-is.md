# 0105 — `Teema käik` is one list, and a `Märge` takes whatever there is

**Status:** accepted
**Date:** 2026-09-22

**Four changes to one page, all in the same direction: fewer things between the
lawyer and the file.** The history stops being sectioned, its rows get a line
shorter, the `Ülevaade / uudis` row stops explaining itself, and `+ Märge` stops
refusing saves whose content was already complete.

None of it changes a record's meaning. One `CHECK` constraint is dropped, one
field becomes optional, and everything else is presentation.

---

## 1. `Teema käik` is one chronological list

**Decision.** The chronology renders every row in one list, newest first, with no
phase headings and no `Etapiga sidumata`. `app/matters/phase_history.py` is
deleted, along with `UNPLACED_LABEL`, `TimelineItem.phase`, `.opens_phase`,
`.phase_continues`, `TimelinePage`, the `timeline_phases` context key, the
`.uxtl__phase*` rules and the collapse script.

**Context.** ADR 0092 grouped the chronology into the phases the file recorded,
so a three-year proceeding read as `VTK`, then `Kooskõlastusring`, then
`Riigikogus`. The reasoning was sound and the result had two costs, and the second
is what decided this:

* **`Etapiga sidumata`.** Most records cannot be placed — nothing is backfilled,
  and a note with no date or an opinion sent before the file's first recorded step
  never will be. They collected under a heading at the foot of the page, and no
  wording fixed what a heading does: the owner's round of 2026-09-21 had already
  removed the paragraph under it because the paragraph read as an apology, and the
  heading still read as a queue somebody owed work on.
* **The list stopped being chronological.** Rows arrived newest-*phase* first,
  unplaced last — so a reader scrolling for «what happened in September» met the
  newest phase, then an older one, then a group filed under no phase at all. A row
  from May could sit below a row from the previous January. This is the part no
  amount of relabelling reaches: a list whose order is two orders is a list a
  reader cannot scan.

And the thing the grouping was for is already on the page. ADR 0092 §2 put
`Menetluse kulg` *above* `Teema käik` precisely so that collapsing the history
would not take «where is the procedure» with it. That rail draws the phases in the
procedure's own order, marks each one the file recorded `Kirjas`, dates it from
the record that proves it, and says `Praegu` on the one the file is on. Two
sections were answering one question, inches apart, and the one that answered it
better was not the one reordering the rows.

**What is kept.** `MatterProceduralDevelopment.process_phase` is untouched. It is
still asked on `+ Märge`, pre-selected from the file's own `Hetkeseis`, corrected
on the row, and it is still the only thing that marks a node `Kirjas`
(`legal_process.recorded_phase_keys`). What went is a *rendering*, not an
association.

**Alternatives considered.**

* *Drop the `Etapiga sidumata` heading and keep the phase sections.* Rejected, and
  it is the change this round was first read as asking for. Without the heading
  the unplaced rows still sort to the foot of the page, out of date order, with
  nothing saying why — a silent reordering is worse than a labelled one.
* *Interleave the unplaced rows and emit a heading whenever the phase changes.*
  Rejected. A phase interval is chronological by construction, so any unplaced row
  inside one splits it and the heading is drawn twice; a file with a dozen undated
  records reads as a dozen sections.
* *Keep the grouping behind a toggle.* Rejected. Two renderings of the history is
  two answers to «what happened», and the day they disagree nobody can say which
  was meant — the reason ADR 0092 itself gives for the grouping never being a
  second projection.

## 2. A milestone row is three lines, not five

**Decision.** The headline, the day and the controls that correct the row are one
line, inside a new `.uxtl__head` element: the row's headline paragraph and its
`.uxtl__editactions` as siblings in a wrapping flex row. Applied to `Märge`,
`Kaasamine`, `Väline seisukoht`, `Arvamus välja` and the generic milestone rows
(`Oluline tähtaeg`, `Jõustumine`, `Töövõit`, `Ülevaade / uudis`).

**Context.** An ordinary `Arvamus välja` read as four lines, of which three were
content:

```
Arvamus välja 22.9.2026
läks koja arvamus · sotsiaalse sidususe ministeerium
Muuda
[arvamus.pdf]
```

The third was a whole line spent on one quiet word, in a surface that is read far
more often than it is corrected. It now reads:

```
Arvamus välja · 22.9.2026 · Muuda
läks koja arvamus · sotsiaalse sidususe ministeerium
[arvamus.pdf]
```

**A wrapper, and not the chips inside `.uxtl__ms`.** That element is a `<p>`, and
the HTML tree builder closes an open `p` at a `<details>` start tag — so a
paragraph carrying `Muuda` and `Kustuta`'s disclosure parses into three boxes with
the disclosure as a *sibling*, which is the defect `row_remove.html` documents and
`tests/test_ui_contract.py` refuses. `.uxtl__head` is a `<div>` for that reason,
and the nesting is asserted with a parser rather than a string window, because the
markup reads the same either way and only the tree differs.

`flex-wrap`, so a long headline keeps its controls instead of squeezing them: at
375px the headline takes the first line and the chips wrap under it, which is
where they used to be and costs nothing that was not already spent. `Kustuta`'s
confirmation takes the full width once it is open, so the question has room to be
asked in.

A work entry keeps its own controls under its text. They are inside
`#sissekanne-<pk>-sisu`, which is the swap target a correction replaces, and
moving them into the meta line above would leave `Muuda` visible beside the form
it had just opened.

## 3. An `Ülevaade / uudis` row is its address

**Decision.** The published row drops `Avaldatud` and the link label
`Ava ülevaade või uudis`, and prints the address itself. `Paranda link` becomes
`Muuda`.

**Context.** The row said four things, of which one was the answer:

```
Ülevaade / uudis 14.3.2026
Avaldatud
Ava ülevaade või uudis   Paranda link
```

`Avaldatud` restates the section it is in — the chronology means «this has
happened» — and `Ava ülevaade või uudis` names what a link is for in place of
*which page*. A file written up three times showed three identical rows, and the
only way to tell them apart was to follow them.

`MatterWebsiteOverview.link_display` decides how the address prints: host, path,
query and fragment, with the scheme dropped because it is on every one of them,
any `userinfo` dropped, the trailing slash dropped, and a cut at 72 characters
marked `…`. The `href` is the whole stored URL.

**This reverses ADR 0085 §2's second sentence and keeps its first.** That ADR
labelled the link because the address may now be anywhere on the public web, so
`Ava kodulehel` would have promised a koda.ee page the record no longer
guarantees. True — and the cure was a label that promised nothing at all. The
address is the honest answer to both: it does not claim a site, and it says which
one. The dropped `userinfo` keeps red-team finding F-2's rule, and
`normalize_website_overview_url` has refused credentials on the way in since
ADR 0081 §3, so this is for a historical row alone.

The **cancelled** row keeps its `Tühistatud` sub-line. A dropped plan is the one
state nothing else on the row would say.

## 4. `+ Märge` takes whatever there is

**Decision.** Every control on the panel is optional, and any one of them is a
whole save: a comment, a file, a new `Hetkeseis`, a next step, or any combination.
`MatterProceduralDevelopment.title` becomes `blank=True` and the
`matters_development_title_required` `CHECK` constraint is dropped
(`0037_development_title_optional`). The panel asks its four questions in the
order somebody answers them — *mis juhtus, lisa failid, uus hetkeseis, järgmine
tegevus* — with the date and `Etapp` under the sentence rather than above it.

**Context.** `Mis juhtus?` was declared `required=False` and then refused in
`clean_title`, so it was optional in the contract and required in the product. A
lawyer whose whole answer was the paper that had just arrived — or the file
reaching the Riigikogu, or «vaatan uue versiooni üle, 25.09» — was refused until
they wrote a sentence restating it, and what they wrote was the filename.

**A blank title is nothing, and the row says so rather than inventing one.** The
chronology headline falls back to the word `Märge`, in the presentation layer
(`timeline.development_milestone`). Nothing derives a headline from the note, the
filename, the stage or the next step: that is the prose-matching this repository
refuses everywhere, and a stored title somebody never typed would be unfixable
because nobody could tell it from one they did.

**What is still refused is a press carrying nothing at all** — no sentence, no
file, no stage and no step — as one sentence naming all four ways to answer
rather than «this field is required» under whichever box the form checked first.
The rule lives in `workspace.add_procedural_development`, where the four can be
seen together, and the form repeats it beside the controls; the service raises it
before taking the Matter's row lock, because a save that was never going to write
anything should not queue behind one.
**Superseded on 2026-09-26 for what counts as a stage, when the rule is decided,
and what a correction may leave — see the amendment at the end of this document.**

**The one date that stays paired is the next step's.** `Kuupäev` clears to
«kuupäev teadmata» as it already did, and `Järgmine tegevus` still needs its day.
A step with no date appears in nobody's `Tähtajad` and in nobody's `Minu asjad`;
`set_next_action` refuses `DO`/`DEADLINE` with no date for that reason (ADR 0052
§3, §5), and a panel that quietly wrote a shape of step the rest of the product
cannot show would be worse than a refusal one keystroke from being answered. The
refusal is pinned to the empty date box, not to the sentence somebody did write.

`Etapp` **stays on the panel**, which is the one decision §1 could have taken the
other way. Its documented purpose was the grouped history, and that is gone — but
it is also what marks a node `Kirjas` on `Menetluse kulg`, which is now the only
place the phases are drawn. It is one pre-selected select beside the date box, and
removing it would have made the rail unfillable from the control that records the
steps.

`ProceduralDevelopmentEditForm` takes the same change, so a title can be cleared
as well as written: a record that can be created in a shape and not corrected into
it is a one-way door. Its label becomes `Mis juhtus?`, matching the panel — it
said `Mis menetluses juhtus`, the narrower wording ADR 0097 §6 stopped using.

**Alternatives considered.**

* *Fill a blank title with a constant on the way in.* Rejected. It stores a word
  nobody typed in a business column, and `Muuda` then opens on a sentence the
  lawyer has to delete.
* *Write the other facts without a `MatterProceduralDevelopment` when there is no
  title.* Rejected. A file needs a record to hang its `DocumentLink` on, and a
  save that sometimes writes a row and sometimes does not is two operations
  wearing one button.
* *Make the next step's date optional too, with `date_semantics=REVIEW_ON`.*
  Rejected as out of proportion: it reintroduces a second date meaning to a
  product that simplified to one (ADR 0052 §1), and it would make this panel
  disagree with `PRAEGUNE TEGEVUS`'s own `Muuda` about whether a step needs a day.

---

## Consequences

* `app/matters/phase_history.py` and `tests/test_matter_process_phases.py`'s
  grouping half are gone; that file's remaining subject is the rail, the dated
  strip and the `Etapp` controls, and its worked examples are read off
  `recorded_phase_keys` and one ordered list.
* One migration, `matters/0037`, dropping a `CHECK` and widening a field. No data
  migration, no backfill, no search-index version change.
* The visual baselines holding `Teema käik` and `Lisa teemale` change by
  construction: rows are shorter, the sections are gone and the panel is
  reordered.
* `opinion_manage.html` still says `Ava ülevaade või uudis`. That is a list of
  write-ups beside an opinion rather than a chronology row whose subject is the
  address, and it is deliberately out of this round.

---

## Amendment, 2026-09-26 — a `Märge` is judged by what it writes, on the locked Matter

- Status: accepted, amending §4's «what is still refused» paragraph and its
  placement of the rule before the row lock
- Scope: what counts as content when `+ Märge` is saved, where that is decided,
  and what `Muuda` on a `Märge` may leave behind (ENG-060). Nothing else in this
  ADR moves.

### What was decided before

A press carrying «no sentence, no file, no stage and no step» was refused, the
rule read what was **posted**, and it was raised before the Matter's row was
locked so that an empty save would not queue behind a lock. `Muuda` on a stored
`Märge` asked no such question at all.

### Why it is superseded

**A posted stage is not a written one.** Choosing the `Hetkeseis` the file
already has makes `change_stage` write nothing, so «Uus hetkeseis: <the current
one>» and nothing else passed the rule and stored a dated row saying nothing —
no title, no file, and not even a stage event beside it. The audit reproduced it
through the panel (ENG-060).

**The current stage cannot be read before the lock.** The view fetches the
Matter before the transaction; a colleague's stage change committed in between
makes that instance answer for a moment that has passed. Comparing against it
would refuse a real change and accept an empty one exactly when two people are
working on the same file.

**And a correction was a second door to the same row.** `Muuda` could clear the
title, the note and the date of a file-less `Märge`, leaving the row the capture
rule exists to prevent.

### What is decided now

- **A save must write something**: a sentence (`Mis juhtus?`), a note, a file, a
  `Hetkeseis` that **moves** the file, or a next step. The date and `Etapp` are
  not content — the panel fills both in. A note counts because it is a sentence
  the row states; the panel does not ask for one (docs/adr/0097 §6.2), so this
  changes nothing a person sees there.
- **It is decided on the locked Matter**, after
  `lock_open_matter_for_business_write`, by
  `app.matters.services.development_save_says_something`. The refusal of an empty
  press now queues behind the row lock; being right about the stage is worth
  that. The panel still asks first, from the stage the page was drawn with, so the
  sentence appears beside the controls — as a pre-check only; the operation's
  answer is the one that counts.
- **A correction may not take the last words off a file-less row.** If `Muuda`
  would leave a `Märge` with no title, no note and no linked file, it is refused
  with its own sentence pointing at `Kustuta` (docs/adr/0102). Clearing the
  title while a note remains is an ordinary correction. A row whose content was
  always elsewhere — a `Märge` that was only a stage change — stays correctable
  in its date, because correcting it takes nothing off it.
- **What the original save moved is not the row's content.** A `Hetkeseis` change
  or a next step written by the same operation are facts about the Matter and the
  `NextAction`, correctable on their own surfaces; they do not make a wordless,
  file-less row acceptable. Whether removing a `Märge` should take them with it
  is a separate open question (ENG-020) and this amendment does not answer it.
- A refused save or correction writes no row and no `ChangeEvent`.

### What this amendment does not change

§4's decision that every control is optional and any one of them is a whole
save; the titleless row reading `Märge`; `ProceduralDevelopmentEditForm` being
able to clear a title; docs/adr/0106's undated next step; no schema, no
migration and no search-index version change.
