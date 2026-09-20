# 0098 — A file reads in phases, and the road ahead is conditional

**Status:** accepted
**Date:** 2026-09-20

**Succeeds ADR 0092 §13–§17.** That record built `Menetluse kulg` as two generic
rails with four honest node states and deferred everything instrument-specific
until lawyers had used them (§17). They have. This record picks up three of the
deferred items in the only form the data supports, and adds the thing the second
feedback round actually asked for: a lawyer should be able to read a three-year
file in the shape the procedure had, and be reminded of the route ahead without
having to memorise it.

0092's load-bearing decisions are **unchanged and not restated**: the late-entry
rule, the four states in words, `Rohkem ei tegele` is not a node, no dates on a
node, one projection rather than two, permission before projection, and
`operation_id` as the only grouping evidence.

The Teema page's lower half now answers two questions instead of one and a half:

```
MENETLUSE KULG      Praegu · Ees võib olla · Kirjas olevad kuupäevad
TEEMA KÄIK          VTK · Kooskõlastusring · Valitsuses · Riigikogus · …
```

Nine decisions:

1. **One phase vocabulary, used by the rail and by the history.**
2. **A phase is a presentation concept and never a `Hetkeseis`.**
3. **One explicit association, on `Menetluse areng`, and intervals for the rest.**
4. **An interval must be unambiguous, or the row is unplaced.**
5. **Unplaced is an ordinary answer, and no history is backfilled.**
6. **Eight instrument-aware patterns, none of which invents an obligation.**
7. **The road ahead is a reminder, and it writes nothing.**
8. **The dated strip moves into `Menetluse kulg` and keeps every decision.**
9. **A phase collapses, and expanded is the state the server sends.**

**Two additive migrations, no backfill.**
`matters/0033_development_process_phase` adds one optional `CharField` with a
`CHECK` on the code vocabulary; `intelligence/0002_important_date_kind` adds one
optional `CharField` with two values and a default. No historical row is read,
rewritten or classified, and `makemigrations --check` is clean.

---

## Context

The lawyers supplied eight worked examples of proceedings they actually run — a
VTK that becomes a law, a bill with no VTK, a file Koda meets in the Riigikogu, a
Government regulation, a ministerial one, a Koda proposal that progresses, one
Koda stops pursuing, and an EU directive from consultation to transposition — and
asked the Teema page to answer four questions from them: where is this now, what
has actually happened grouped into understandable phases, what might come next,
and which dates should I keep in mind.

Three of the four already had answers. `Hetkeseis` says where the file is.
`app/matters/timeline.py` projects what happened from canonical records.
`app/matters/process_timeline.py` draws the dated points. What had no answer at
all was the second half of the second question — *grouped into understandable
phases* — and the third.

The blocking problem was not rendering. It was that **nothing in the domain said
which phase an act belonged to**. `Matter.stage` says where the file stands now
and `Õigusakt` says how it is classified now; neither dates an opinion sent last
spring, and ADR 0091 §5.2 had already refused to copy the stage onto the record
that could have carried it.

## 1 — One phase vocabulary, used twice

Eleven phases, in `app/matters/process_phases.py`:

```
Algus · VTK · Koja ettepanek · Kooskõlastusring · Valitsuses · Riigikogus ·
Jõustumine · ELi konsultatsioon · Eesti seisukoht · ELi menetlus · Ülevõtmine
```

Ten are the lawyers' own words, verbatim. `Algus` is this repository's and is
deliberately the blandest word available: `Hetkeseis` offers `Idee`, real files
hold it, and a vocabulary with nowhere to put it would leave them unable to say
where they are.

The rail's nodes and the history's section headings are the **same list**. Two
lists would mean a section headed `Kooskõlastusring` under a node called
`Kooskõlastus`, and a reader working out for themselves whether those are the
same thing. Three of 0092's generic node labels are reworded by this — `Algus /
konsultatsioon` → `ELi konsultatsioon`, `Kooskõlastus` → `Kooskõlastusring`,
`Valitsus` → `Valitsuses` — and no key moved.

## 2 — A phase is a presentation concept and never a `Hetkeseis`

The two do not line up, and the places they come apart are the whole reason this
is a separate vocabulary rather than a reuse of `StageVocabulary`:

- **`VTK` is not another spelling of `Idee`.** A väljatöötamiskavatsus is a
  document with its own consultation round, and a file sits on `Idee` for a year
  with no VTK in existence. `vtk` maps **no stage key** and is reached only by a
  recorded act.
- **`Kooskõlastusring` occurs more than once.** The VTK's round and the bill's
  round are two occurrences a year apart, which a single-valued current stage
  cannot express at all.
- **`Jõustumise ootel` and `Jõustunud` share the `Jõustumine` phase** and are not
  the same answer. The distinction rides on `ProcessNode.stage_label`, exactly as
  0092 §13's amendment left it.

A stage answers *where is this*; a phase answers *which part of the story is this
act in*. Selecting a pattern writes no `Matter.track`, no `Õigusakt` and no
`Hetkeseis`, and drawing one writes nothing at all.

## 3 — One explicit association, and intervals for everything else

`MatterProceduralDevelopment.process_phase` — optional, bounded by a `CHECK`,
blank by default. It is **the only explicit phase in the product**, and it is on
that record because that record is the one that says what the procedure did.

Every row gets its phase in this order, stopping at the first answer that is
actually supported:

1. the record's **own explicit phase**, where it has one;
2. a **business-dated interval** between two such records — `[start, next start)`,
   decided on business dates alone;
3. otherwise **unplaced**.

And these are evidence of nothing: a title, a filename, an organisation's name, a
`Menetluse link`'s host, an upload or import time, a `created_at`, today's date,
two records having been *entered* on the same day or in a particular order, and
the file's current `Hetkeseis`.

**A business date, never a placement date.** `engagement_chronology_day` and its
siblings fall back to `created_at` so an undated record still has somewhere to
sit; that fallback places a row and deliberately never describes it, and grouping
is a description. `phase_history.business_day` reads each record's own business
column and answers `None` where it is empty.

**A bare `Hetkeseis` edit places nothing.** It proves somebody recorded a value;
its timestamp is the moment they typed it. This is also what stops a corrected
mistake from leaving a phase behind that the procedure never visited.

**Why not the same-operation stage change.** `+ Märge` can move the stage in the
same transaction as the step, and 0092 §6 established `operation_id` as the one
grouping fact. It is deliberately **not** used to derive a phase: an operation
groups one save and does not say which side of a transition the sentence belongs
to. «VTK saadeti kooskõlastusringile» reads under `VTK` and «Eelnõu saadeti
kooskõlastusringile» begins `Kooskõlastusring`, and no rule over `from`/`to`
distinguishes them. The person says, in a select they are looking at.

**Proposed visibly, never guessed invisibly.** `+ Märge` pre-selects the phase
the file's own `Hetkeseis` places it on, in a control **beside the date box**.
That position is the whole of its honesty: somebody filing a step from 2019 is
looking at the word while they type the year. «Etapp määramata» is one click
away, `Muuda` corrects it afterwards, and the select offers only the phases this
file's own procedure has — a ministerial regulation is not offered `Riigikogus`.
`Uus teema` is not touched and nothing is mandatory anywhere.

## 4 — An interval must be unambiguous, or the row is unplaced

The last dated step leaves an interval running to the present, and that reading
holds **only while nothing contradicts it**. A current `Hetkeseis` naming a
different phase does contradict it: it proves the file left, without saying when.
Everything after that last step is then genuinely ambiguous and reads under
`Etapiga sidumata` — which is what §7's own rule ladder says to do with an
interval that is not unambiguous.

This is the decision most likely to look like a defect and is not one. The fix is
one `+ Märge` away: recording the step that moved the file dates the phase, and
everything inside it groups. That is the one correction that fixes a section
rather than a row.

The phase the file *is* on still reads, as a section with `Praegu`, **no date and
no rows**. Nothing invents an event to fill it.

## 5 — Unplaced is ordinary, and nothing is backfilled

No historical row is classified, and there is nothing to classify one *from*:
deriving a phase from a title is the prose-matching this repository refuses
everywhere else. Every row written before this column existed is blank and stays
blank.

So on the day this ships **every Matter in the register has no placed row** — and
the chronology therefore renders exactly as it always has: one flat list, no
headings, and no `Etapiga sidumata`. `PhaseHistory.grouped` is false and the
section degrades to what it was. A heading announcing «none of this could be
placed» above every row of every file would be the application announcing a gap
nobody can close (docs/adr/0092 §16).

`Etapiga sidumata` is a heading and never a queue. It carries one sentence saying
these records are fine and the phase will follow if a step is written down; it is
not red, carries no count and no icon, and nothing asks anybody to clear it.

## 6 — Eight patterns, none of which invents an obligation

```
Riigisisene menetlus    Algus · Kooskõlastusring · Valitsuses · Riigikogus · Jõustumine
VTK ja eelnõu           Algus · VTK · Kooskõlastusring · [Valitsuses] · [Riigikogus] · [Jõustumine]
Määruse menetlus        Algus · Kooskõlastusring · [Valitsuses] · Jõustumine
Koja ettepaneku menetlus  Koja ettepanek · [Kooskõlastusring] · [Valitsuses] · [Riigikogus] · [Jõustumine]
ELi menetlus            ELi konsultatsioon · Eesti seisukoht · ELi menetlus · Jõustumine · [Ülevõtmine]
ELi konsultatsioon      ELi konsultatsioon · Eesti seisukoht · [ELi menetlus] · [Jõustumine] · [Ülevõtmine]
Direktiivi menetlus     ELi konsultatsioon · Eesti seisukoht · ELi menetlus · Jõustumine · Ülevõtmine
ELi määruse menetlus    ELi konsultatsioon · Eesti seisukoht · ELi menetlus · Jõustumine
```

`[…]` is **conditional** — a phase the pattern says may not apply at all, which
is a different claim from one that has not happened yet. It says «kui menetlus
jätkub» in words on the node, not in a shade of grey.

Four of these are corrections rather than additions:

- **`EL määrus` has no `Ülevõtmine` node.** A regulation applies directly and
  nobody transposes it. The single generic European rail ended *every* European
  file on `Ülevõtmine / jõustumine`, which on this instrument is not a harmless
  extra step — it is the rail inventing a legal obligation that by definition
  does not exist. `ELi õiguse ülevõtmise ootel` on such a file reads beside the
  rail in its own words rather than being given a node that would resolve the
  contradiction by picking a side.
- **`Jõustumine` and `Ülevõtmine` are two nodes, not one.** An EU act being in
  force does not mean Estonia has transposed it, and the old shared node made
  those indistinguishable.
- **`Määrus` gets one pattern with a conditional `Valitsuses`, and no
  `Riigikogus`.** The reviewed `Õigusakt` value does not say whose regulation it
  is, and 0090 §4 refused to add a `Määruse liik`. Asserting `Valitsuses` is
  wrong for every ministerial one, omitting it is wrong for every Government one,
  and *may not apply* is the only reading that is true either way. The **absence**
  of a Government record is never read as evidence of a ministerial regulation.
  Suggesting Parliament for a regulation would teach a reader something false.
- **`VTK` + `Seadus` is one file, not two roadmaps.** The commonest real
  combination on the lawyers' first example. Two instrument patterns would
  otherwise read as «mixed» and draw nothing, withdrawing the roadmap from
  exactly the file the full example was written about; the VTK pattern contains
  the whole law path and is the superset.

**Choosing is still a projection.** The family is `Menetlusliik` where it decides
one and the reviewed `Õigusakt` grouping otherwise; the specific pattern is the
instrument's own where it names exactly one within that family. A file whose
instruments straddle the two families — `direktiiv ja seadus`, a real register
value — gets **nothing**, because there is no reading of it that picks one road,
and choosing arbitrarily is worse than saying nothing.

`strateegia-arengukava-tegevuskava`, `muu-siseriiklik` and `muu-eli-dokument`
name no pattern of their own and fall through to their family's generic one. The
department gave no route for them, and inventing a plausible-looking one to fill
the screen would be this module deciding something nobody has.

## 7 — The road ahead is a reminder, and it writes nothing

`Ees võib olla` names the next **one to three** phases, in words, undated, with
the rest of the route behind a disclosure. A list of six speculative steps reads
as a schedule.

It creates no `NextAction`, sets no deadline, assigns nobody, makes no file read
as late and is stored nowhere. What *this office* does next is `PRAEGUNE TEGEVUS`
— a different question about a different actor — and it stays exactly where it
is. There are no percentages, no «3 of 7», no checkmarks and no dates: a step
nobody has recorded has no date, and inventing one is the whole thing this
section must not do.

**History and guidance have different rules, and the page keeps them apart.**
History shows only what the records support. Guidance may show unrecorded future
phases, conditional and undated.

## 8 — The dated strip moves, and keeps every decision

`Menetluse tähtajad` rendered at the head of `Teema käik`. Once the road ahead
arrived the page carried three diagram-shaped things stacked above the working
area, and the strip was the one answering the *other* section's question: which
dates are written down is «where is this going», not «what happened».

It now renders under `Kirjas olevad kuupäevad` inside `Menetluse kulg`. The strip
itself is **byte-for-byte what it was** — the same six labels, the same canonical
sources, the same `reached / today / ahead` grammar that keeps a future date from
reading as something that already happened (docs/adr/0074 §12, docs/adr/0083).
Only its heading and its position changed.

**This reverses 0092's «Replacing the dated strip with the rail» rejection, and
narrowly.** That record was right that the two answer different questions and
that the strip's decisions were sound; nothing here disputes either. What changed
is the *count* — a third block made the stacking the problem — and the strip was
moved rather than replaced, into the section whose question it was already
answering.

**The three blocks are independent.** A file with no `Õigusakt` and no
`Menetlusliik` draws no rail and no road ahead, and its `Alustatud` and
`Arvamuse tähtaeg` still read: guarding the dates on the rail would have
withdrawn recorded information from every unclassified Matter in the register as
a side effect of a layout change.

## 9 — `Ülevõtmise tähtaeg` is a kind, not a word to search for

`MatterImportantDate.kind` — `Muu tähtaeg` by default, plus
`ELi õiguse ülevõtmise tähtaeg`. One surface has to **recognise** a transposition
deadline rather than read it, and the alternative was matching «ülevõtmine» in a
free-text title, which would miss «direktiivi rakendamise kuupäev» and wrongly
claim «ülevõtmise arutelu». `Oluline tähtaeg` stays free text by design and this
is not the start of a taxonomy. Nothing is backfilled: a row written before the
column says `OTHER` because nobody has said otherwise.

## 10 — A phase collapses, and expanded is what the server sends

A three-year file is long, and the question is frequently what happened in the
Riigikogu while the answer sits under nine months of consultation round. So each
phase heading carries a collapse control.

**It hides the rows between one heading and the next**, because the headings are
siblings of the rows rather than wrappers around them — §Alternatives says why.
A closed section keeps its heading at full legibility: what collapses is the
content, not the answer to «which phase is this». «Näita varasemaid» is never
hidden, because it is how the next page is asked for.

**Expanded is the state the server sends, and the control arrives `hidden`.**
Nothing about what a reader sees on arrival depends on script: every row is on
the page and every phase is open, and `static/js/ux.js` then shows a button that
works. A button that did nothing without JavaScript would be worse than no
button. `aria-expanded` carries the state, the caret is drawn rather than typed
so it is not read out beside the button's own name, and the control is an
ordinary element in the tab order — the standing rule that every keyboard move
has a visible control that does the same thing.

## 11 — Permission, and what a heading may not leak

Every source is scoped **before** phase selection, grouping, counting, sorting
and ordering — `phase_history.build` reads the list `matter_timeline` has already
filtered, and never a second query of its own.

A restricted `Menetluse areng` is the one record whose leak would be structural
rather than textual, because it is what *opens* a phase. For a reader who may not
see it, it opens nothing: no heading, no date on a heading, no count, and no gap
where a section would have been. They see fewer sections and the rows group by
the evidence they can actually read.

The Matter's own `Hetkeseis` may still be shown — that column is not restricted —
and it is shown as a section with `Praegu`, no date and no rows, which is what
the reader is entitled to know without learning when or how the file got there.

## Alternatives considered

**A `process_phase` column on every record that can appear in the chronology.**
Rejected. Six nullable columns is six places for one concept, and it is
unnecessary: an opinion's phase follows from its own send date and the intervals
around it, which is both cheaper and more truthful — a lawyer who does not know
which round an opinion answered does not know the round happened.

**A `MatterPhase` association table.** Rejected for the reason 0092 rejected
`MatterHistoryItem`: a persisted projection is a second place for facts the
domain already holds, and it fails the permission rule by construction.

**Deriving the phase from the same-operation stage change.** Rejected — see §3.
An `operation_id` groups one save and does not say which side of a transition its
sentence belongs to, and the lawyers' own examples place transition sentences on
both sides.

**Rendering «every transition event belongs to the phase it leaves».** Rejected,
explicitly. The examples do not establish that rule: «VTK saadeti
kooskõlastusringile» reads under `VTK` and «Eelnõu saadeti kooskõlastusringile»
begins `Kooskõlastusring`, and both are transitions.

**Placing post-anchor rows in the last known phase when the current stage
disagrees.** Rejected — see §4. It would file a 2026 opinion under a 2025 round
on exactly the neglected files where a reader is least able to notice.

**`<details>` per phase section.** Rejected as the *mechanism*, not as the
feature — see §11. «Näita varasemaid» swaps its own button for the next batch
*in place*, so a section longer than one page would have to be opened on one page
and closed on the next, and the spine down the left of the list would break into
one element per section. The headings are siblings of the rows, a section that
runs past the fold carries its own heading again marked «jätkub», and collapsing
hides the rows between one heading and the next.

**Separate government-regulation and minister-regulation patterns.** Rejected
again, on 0092 §14's reasoning unchanged: the data does not say whose.

**A `Määruse liik` field to make the above answerable.** Rejected. It is a
mandatory classification nobody asked for, on the commonest instrument in the
register, to make a diagram symmetrical.

**Backfilling a phase onto historical rows from their titles.** Rejected. There
is nothing to backfill from.

## Consequences

- One vocabulary, one projection, one association. `process_phases.py` holds the
  words and the patterns, `phase_history.py` groups, `legal_process.py` draws the
  rail, `timeline.py` is still the only history.
- The Teema page's lower half is two sections rather than two sections and a
  stray strip.
- Every existing Matter reads exactly as it did until somebody records a step.
- `Menetluse areng` gains one optional question; nothing else on any form does.
- Two additive migrations, no data migration, no reindex: nothing here changes
  what search stores or how it scores.

## Reversibility

High. The columns are optional and additive, so reverting the projection leaves
two unread columns and no data loss. Retiring a pattern is a code change.
Retiring the grouping is deleting one module and one template branch: the flat
chronology is what renders when `PhaseHistory.grouped` is false, which is already
the majority case on day one.
