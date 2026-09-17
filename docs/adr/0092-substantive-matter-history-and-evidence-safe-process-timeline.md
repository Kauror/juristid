# 0092 — Substantive Matter history, and an evidence-safe process timeline

**Status:** accepted
**Date:** 2026-09-17

*Extends docs/adr/0074 §14–§16* on what the chronology projects and what it is
called. That record's central claim — two visual row kinds, milestones projected
from canonical records rather than duplicated out of the audit stream, and a head
that is a label and a count — is unchanged and is what this builds on. What moves
is the last two sources that still came out of an audit row, the grouping of one
act's several writes, and the section's name.

*Extends docs/adr/0091 §5* by reading `MatterProceduralDevelopment` as the
canonical answer to «the ministry sent a new version», which is what that record
was created for. Nothing about the record changes.

*Extends docs/adr/0079* by applying its precision vocabulary to two more
projections — a work victory's business period and a sent opinion's date. No new
precision is invented and no formatter is added.

*Preserves docs/adr/0032* exactly: `Rohkem ei tegele` remains
`Disposition.MONITORING_STOPPED` and is not a process stage, and `Jõustunud` does
not close a Matter.

*Preserves docs/adr/0089 §2 and docs/adr/0091 §5.6* exactly: a `Menetluse link`
is a reference, and nothing in this record infers a procedural development, a
stage or a date from one.

*Narrows nothing.* No record is retired, no column is dropped, no vocabulary is
reworded and no history is rewritten.

## Context

The second structured lawyer round asked one question of the Matter page:

> Mis selle teemaga päriselt juhtus?

The page answered it with the application talking about itself. A single
`+ Menetluse areng` save — the ministry sent a revised draft, the file moved to
`Kooskõlastusringil`, the lawyer set a step to read it — drew **three** rows, so
the reason for two of them sat two rows away from the fact. A confirmed `Töövõit`
was dated to the afternoon somebody pressed `Kinnita`, which for a 2019 win put it
at the top of the file under this year's date. A sent `Koja arvamus` was read from
the send event, whose `occurred_at` for a reconstructed historical opinion is the
day of the import. An open `Järgmiseks` read prominently at the top of the page
and again, halfway down, as «määras järgmise sammu».

And the horizontal strip above the list was labelled `Teema käik` while drawing
`Alustatud`, `Arvamuse tähtaeg`, `Koja arvamus` and `Jõustumine` — a mix of acts
that happened and a deadline three weeks out. Two different questions were sharing
one name, so neither had an answer a reader could trust.

Underneath that was a second question the product could not answer at all: *where
is this procedure now, and what do we actually know about how it got here?*
`Hetkeseis` says where it is. Nothing said which earlier steps the file records,
and nothing distinguished «this did not happen» from «nobody wrote it down» —
which for the register archive is the ordinary case, because a Matter is
frequently first filed when the bill is already in the Riigikogu.

## 1 — The architectural decision, stated first

**This is a projection package.** No model, no migration, no table, no search
index change, no reindex, no archive rebuild and no new reporting metric. Every
fact it renders already exists in a canonical record, and Package C
(docs/adr/0091) was specifically the round that closed the missing-data gap.

The one write in the diff is **two additional keys in an existing JSON payload**
(§12), which is not a schema change and carries no migration.

**One substantive-history projection, evolved rather than replaced.**
`app/matters/timeline.py` already had the properties this package needs: it
projects canonical domain records rather than dumping audit rows, it uses
`ChangeEvent.operation_id` to group the writes of one operation, it keeps
field-level noise off the lawyer-facing list, and it filters every source through
`visible_to` before sorting or rendering. A second engine beside it would have
been two answers to «what happened on this file», and the day they disagreed
there would have been no way to tell which was meant. `ChronologyMilestone` and
`TimelineItem` are the Package D read model; no `MatterHistoryItem` type was
added because none would have made the interface clearer.

**No workflow engine.** No `WorkflowStep` table, no state machine, no transition
rules, no configurable nodes, no editor, no drag-and-drop and no BPM graph.
AGENTS.md lists a generic workflow engine among the things this repository does
not introduce without measured need and explicit approval, and a rail answering
«which of five generic steps» does not establish one.

## 2 — Two sections, two questions, two names

`Teema käik` and `Menetluse kulg` are kept apart, and the naming is part of the
decision rather than a label on it.

| section | question | source |
| --- | --- | --- |
| `Menetluse kulg` | where does the external procedure stand | `Hetkeseis` + explicit stage history |
| *(the dated strip)* | which dated points does this file have | `app/matters/process_timeline.py`, unchanged |
| `Teema käik` | what actually happened on this file | the canonical records, projected |

The chronology section's heading was `Ajajoon`, which is the name of a widget
rather than an answer to the lawyers' question; it is `Teema käik` now. **The
`#ajajoon` id and the `?ajajoon=` parameter are untouched**, because they are what
a shared link addresses and renaming an anchor because a heading changed would
break every link already sent.

The dated strip keeps every column, every label, every source and every state
docs/adr/0074 §12 and docs/adr/0083 decided. Its `aria-label` moves from
`Teema käik` to `Menetluse tähtajad`, which is the whole diff: a strip holding a
deadline three weeks out is not a history, and those two words now name the
section it sits inside.

## 3 — Every primary row is projected from a canonical record

Two sources still came out of the audit stream, and both are read from their own
record now.

**`Submission`.** `SUBMISSION_SENT` leaves `TIMELINE_EVENT_TYPES`. The event said
the same thing one step further away: its `occurred_at` is the moment somebody
pressed the button, and its addressees were a copy in a payload rather than the
`SubmissionRecipient` rows that are canonical. The row now reads `sent_at` — the
day somebody supplied, which for an opinion reconstructed from the register is the
day it went out and not the day of the import — the `ADDRESSEE` rows, the `Liik`
where it is not the default `Ametlik arvamus`, and `final_version`, so the exact
bytes that went out are one click from the row that stands for the send.

Several `Submission`s per Matter are ordinary and each draws its own row. Nothing
elects a final opinion, nothing collapses two into one, and each keeps its own
date, its own recipients and its own evidence (docs/adr/0061, master
specification 6.4).

The event is not deleted, is still written, is still audited and is still readable
on `Kõik muudatused` (§11). What it stopped being is the source of a business
date.

**The canonical source inventory**, as projected today:

| record | headline | business date |
| --- | --- | --- |
| `MatterProceduralDevelopment` | `Menetluse areng: …` | `occurred_on` + precision, may be unknown |
| `MatterExternalPosition` | `Meile saadetud tagasiside: …` / `Teiste arvamus: …` / the historical heading | `stated_on` + precision, may be unknown |
| `MatterEngagement` | `Kaasamine: …` | `occurred_on` + precision, may be unknown |
| `Submission` (SENT) | `Arvamus välja` | `sent_at`, never null on a SENT row |
| `MatterWebsiteOverview` | `Ülevaade / uudis` | `published_on`, may be unknown |
| `MatterWorkVictory` (confirmed) | `Töövõit` | `period_date` + precision, may be unknown |
| `MatterImportantDate` | the record's own title | its period |
| `MatterEffectiveDate` | `Jõustus` / `Jõustub` | `date_value` + precision |
| `Entry` | the author's own note | `occurred_at` |
| `MATTER_CREATED`, `MATTER_STAGE_CHANGED`, `MATTER_CLOSED`, `MATTER_REOPENED`, `SUBMISSION_WITHDRAWN` | Matter-level acts | the event's own day |

The last row is deliberate: those five are facts about the Matter itself, the
Matter *is* their subject, and there is no child record holding a business date
they could be read from instead.

**What is not a primary row**, and none of these changed in this package: a
`DocumentVersion` upload that supports a record reads under that record's row, a
field correction is not a row at all, an `updated_at` is not an event, and a
`MatterProceduralLink` is a reference rather than something that happened.

## 4 — The business-date contract

This is the strict part, and it is what most of the server-side tests are about.

**A business date is exact, approximate at one of docs/adr/0079's precisions, or
genuinely unknown.** Unknown means unknown: the row prints «Kuupäev teadmata» and
nothing is substituted for it — not `created_at`, not `updated_at`, not an audit
timestamp, not an upload timestamp, not the operation's moment and not today.

**Internal timestamps place a row and never describe it.** A record whose date is
unknown still has to appear somewhere in a list that is ordered, so it sits on the
day it was recorded — which is the only day this system knows anything about — and
prints «Kuupäev teadmata» rather than that day. The two answers are produced by
two different functions on purpose, and each `*_chronology_day` docstring states
which it is.

**An approximate date places its row on the period's anchor and prints the
period.** A `MONTH` row stores 1 March so that *märts* can sort between *veebruar*
and *aprill*; it prints *märts 2026* and never `1.3.2026`, which is a day nobody
named.

**The work-victory defect.** `MatterWorkVictory` has carried its own business
period — `period_date`, `period_end`, `date_precision` — since Stage 2G, and its
own model docstring says the period is «never `created_at`, never the Matter's
reporting year and never a commencement date». The chronology was using
`confirmed_at` for both the placement and the printed date, so a 2019 win reviewed
this morning sat at the top of the file under today's date. Fixed here.
`confirmed_at` decides only *whether* the row exists — a machine's candidate is a
proposal and earns no row until a person confirms it — and `period_date` decides
where it sits, `display_period` what it says. A confirmed victory with no period
reads «Kuupäev teadmata».

## 5 — Evidence reads under the fact it evidences

Unchanged from docs/adr/0075 §10 and extended by one case: a `Submission`'s
`final_version` is a column on the record rather than a `DocumentLink`, so the
generic link pass cannot see it and it is read explicitly — through the same
`Document.visible_to` filter, so a final text restricted below its Matter
contributes no link and no filename.

A document never becomes a primary row of its own when it supports a record that
already has one. `DocumentVersion` history stays where it is: on the document's
own page and in `Dokumendid`.

## 6 — Grouping: `operation_id`, and nothing else

One lawyer act can write several canonical records. `+ Menetluse areng` files the
development, moves `Hetkeseis` and sets `Järgmiseks` in one transaction and one
`composer_operation`. Those read as **one** substantive act:

```
Menetluse areng: Ministeerium saatis eelnõu uue versiooni     12.10.2026
  → Hetkeseis  Kooskõlastusringil
  → Vaatan uue versiooni läbi · 16.10.2026
```

**Only an explicit operation identifier groups anything.** Not a shared minute,
not a shared author, not a similar title, not the same organisation and not a
matching filename. There is no time window, no prose parsing and no similarity
matching in this package, and adding one would be manufacturing a relationship
nobody recorded.

The record carries no operation column, deliberately: an operation is an audit
fact about *how* something was written, not a property of the fact
(docs/adr/0091 §5.2). So the row that ties a development to the stage change is
its own `PROCEDURAL_DEVELOPMENT_RECORDED` event, read and rendered nowhere —
exactly the role `ENTRY_ADDED` already plays for a note.

The stage clause is the stage-change event's own summary, which is the stage's
label at the time it was recorded. It is **not** read off `Matter.stage`: that is
where the file stands *now*, which is a different claim from what this act did to
it.

## 7 — Permission before projection

Non-negotiable, and it is how the existing module already worked. Every source is
filtered through its own `visible_to` **before** anything is projected, grouped,
counted, sorted or attached; the audit stream goes through
`app.audit.visibility.scope_change_events`, which closes the gap between «this
event hangs off a Matter you may read» and «this event is about a child you may
read». Nothing is built and then hidden.

The one new question this package raises is whether grouping can become a channel.
It cannot, and the ordering is why: **an effect folds only onto a record that is
already on this reader's page.** A development restricted below its Matter
contributes no row, so its stage change stands alone — which is exactly what a
stage change made from the header looks like, and says nothing about a record
nobody may read. A `NextAction` restricted below its Matter contributes no clause
for the same reason.

Tested in both directions: a restricted `Submission` reveals no headline, no date,
no recipient and no filename; a restricted `MatterExternalPosition` reveals no
source, no note, no date and no count; a restricted `MatterProceduralDevelopment`
reveals no existence; a restricted document leaves the act it evidences readable
and its own filename absent.

## 8 — The open `Järgmiseks` is current work, not history

`PRAEGUNE TEGEVUS` is where an open instruction is read and acted on. A
`NEXT_ACTION_SET` row that would otherwise stand **alone** in the history,
pointing at the action this reader can see is still open, is not drawn: printed in
both places it reads as two instructions, and a reader scrolling for what is owed
finds the older copy first.

Three things this deliberately does not do. It does not hide the action once it is
finished or superseded — then it *is* history, and the row comes back. It does not
touch a step set inside a save that has a row of its own: a note or a
`Menetluse areng` keeps its `→ …` strip, because that is the act's own consequence
rather than a second copy of the instruction. And it does not delete or suppress
the audit row, which is on `Kõik muudatused` like every other write.

The page reads the open action once and hands it to the history, the same seam
`matter_intelligence` already is, so `PRAEGUNE TEGEVUS` and `Teema käik` cannot
ask two differently scoped questions about one file.

## 9 — What `Teema käik` shows

Server-rendered, compact, scan-friendly, usable at 375 px, keyboard accessible and
readable without opening anything. Each row exposes the business date or «Kuupäev
teadmata», the act, the source or organisation where there is one, a concise
business description, the evidence, the lawyer's own note under its own label, and
the grouped stage and step effects.

It shows **no raw audit JSON, no database model names, no internal identifiers and
no operation identifiers**. A lawyer has no use for a UUID, and an operation id is
a fact about how a save was made.

## 10 — Performance

The history loads its sources in a bounded query shape: one read per canonical
source, one scoped read of the audit stream, and one read for each attachment pass
over the whole page. Doubling the number of rows adds no queries, which is what
`test_the_history_does_not_cost_a_query_per_row` measures by building the same
projection twice at two populations under one budget.

No persistent cache is introduced and nothing permission-sensitive is memoised
between requests.

## 11 — The technical audit surface

Making the primary history read as a case history means it does not draw a row for
every write. That is only acceptable if those writes stay readable, so this package
adds `Kõik muudatused` — one read-only page per Matter, linked from the foot of
`Teema käik`.

It was buildable **because the authorization already existed**: `scope_change_events`
is the same chokepoint the chronology reads through, so the page needed no new
authorization architecture and could not be got wrong in a new way. Had it needed
one, the decision would have been to leave the gap documented rather than ship an
unsafe view — security over completeness.

What it shows: when, who, which kind of change, and the summary the write itself
recorded. What it does not: `ChangeEvent.payload`, any primary key, any
`operation_id`, and `SecurityAuditEvent`, which is a compliance record with its own
readers (master specification 16.5). It is a page rather than a third tab, because
two tabs is the whole of this record's navigation and an audit log is looked up
rather than navigated between. Django admin is not used and is not linked.

## 12 — `Menetluse kulg`: choosing a rail

A deterministic read-only projection. Nothing is stored, nothing is editable and
nothing here has a write path.

**`Matter.track` is canonical where it is known and it is optional.** An empty
value means *nobody has said*, and nothing backfills, infers or writes one. Two of
its seven values safely choose a presentation — `DOMESTIC` and `EU_INITIATIVE` —
and the other five do not. `NATIONAL_TRANSPOSITION` is the clearest case: a
`Seadus` transposing a directive runs the domestic procedure *and* the file is
about a European instrument, so the track alone does not choose. It falls through.

**Second: Package A's reviewed legal-instrument grouping.**
`DOMESTIC_LEGAL_INSTRUMENT_KEYS` and `EU_LEGAL_INSTRUMENT_KEYS` are how the
siseriiklik/ELiga-seotud distinction stays answerable from stored data since
docs/adr/0090 §4. Using `Õigusakt` to choose a coarse display template is a
**projection**, and it must never write `Matter.track`: that column has seven
values, it is answered by a person, and no instrument type entails one. A Matter
carrying both a domestic and a European instrument draws no rail, because there is
no reading of it that picks one.

**Third: nothing.** No rail, and `Hetkeseis` in the header goes on answering
«where is this» as it always has. A rail is also not drawn when one could be chosen
and the file records nothing that places it on one — five nodes all reading
«Teadmata» is a heading spent announcing that the application knows nothing.

**Stage keys in the audit payload.** `MATTER_STAGE_CHANGED` recorded the stage's
*label*, which is the department's to reword — version 2.0 of the vocabulary
reworded three of them without moving a row — so a surface asking «which stage was
this» from the history could only match on a string that is allowed to change.
`change_stage` now also records `from_key` and `to_key`. Additive, no migration,
nothing backfilled: rows written before this carry labels alone and are resolved
live through the reviewed vocabulary, which includes the version-1.0 wording of the
three reworded labels.

## 13 — `Menetluse kulg`: the four states, and the late-entry rule

Four states, because there are four honest answers and the usual two would force a
lie for two of them:

| state | word | means |
| --- | --- | --- |
| `CURRENT` | `Praegu` | the Matter's own `Hetkeseis` maps here |
| `RECORDED` | `Kirjas` | canonical evidence says this node was reached |
| `UNKNOWN` | `Teadmata` | could be in the past; nothing says it happened |
| `POSSIBLE` | `Võimalik` | a generic later step, not recorded |

**The word is on the page.** Each node prints its state as text and the stylesheet
decorates what the text already says. A rail whose four states were four hues would
say nothing with the stylesheet off, nothing to a screen reader and nothing on a
printout — and «this may have happened and nobody wrote it down» is precisely the
state that cannot survive being a shade of grey.

**The late-entry rule.** A current stage proves the current stage. It does not
prove every prior milestone. A Matter first created when the bill was already in
the Riigikogu reads:

```
Algus         Teadmata
Kooskõlastus  Teadmata
Valitsus      Teadmata
Riigikogu     Praegu
Jõustumine    Võimalik
```

and never marks the first three complete. Only explicit recorded evidence promotes
an earlier node, and the word «tehtud» does not appear in this component's
vocabulary at all.

**Recorded means recorded.** The evidence is the Matter's current stage and the
explicit `MATTER_STAGE_CHANGED` history, and nothing else. Not a title, not a
filename, not an organisation's name, not a `MatterProceduralLink`'s kind, not a
URL host, not the current date and not a node's position in the list.

**No dates on the rail.** A stage-change event proves that a stage was recorded and
its `occurred_at` is the moment somebody typed it in — so printing that beside
`Kooskõlastus` would date a step of somebody else's procedure to a day in this
application's own life, which is the substitution §4 refuses on the history.

**`Muu` is not forced onto a node.** It is a real answer somebody gave — this
proceeding is not one of the nine shapes the vocabulary names — and placing it
anywhere would be the rail asserting a position the person explicitly declined to
give. The same holds for a European stage on a domestic file. Either reads beside
the rail, in its own words.

## 14 — `Menetluse kulg`: two generic V1 templates

Deliberately generic, and two.

```
DOMESTIC   Algus · Kooskõlastus · Valitsus · Riigikogu · Jõustumine
EU         Algus / konsultatsioon · Eesti seisukoht · EL menetlus ·
           Vastu võetud · Ülevõtmine / jõustumine
```

Mapped from the reviewed stage keys, never from labels: `idea`→Algus,
`consultation`→Kooskõlastus, `government`→Valitsus, `parliament`→Riigikogu,
`awaiting_entry`/`in_force`→Jõustumine; and on the EU rail
`idea`/`consultation`→Algus / konsultatsioon, `estonian_eu_position`→Eesti
seisukoht, `eu_procedure`→EL menetlus,
`awaiting_transposition`/`awaiting_entry`/`in_force`→Ülevõtmine / jõustumine.

`awaiting_entry` and `in_force` share a node because they are the same point of
the procedure read from two sides — waiting for it and past it — and the node's
*state* is what tells them apart.

`Vastu võetud` maps no stage key, which is an omission in the vocabulary rather
than in this list: there is no `Hetkeseis` value for «the EU institutions adopted
it», so the node can honestly only ever read `Teadmata` or `Võimalik`. Inventing a
stage to fill it, or quietly dropping the step, would both be this module deciding
something the department has not.

**No separate government-regulation and minister-regulation rails in V1.** The
reviewed `Õigusakt` value is `Määrus`, which does not say which, and adding a
subtype for the sake of a prettier rail would be inventing a classification nobody
chose (docs/adr/0090 §4). Instrument-specific refinement is deferred (§17).

## 15 — Disposition is not a process stage

`Rohkem ei tegele` is `Disposition.MONITORING_STOPPED` and is not a node.
The ministry does not stop drafting because this office stopped reading, so the
rail stays at the last known legal stage and `Koda ei tegele edasi` reads beside
it as a separate sentence. `Jõustunud` likewise does not close a Matter. Both are
docs/adr/0032's separation, unchanged.

## 16 — Archive and old data

Historical Matters carry none of Package C's structured records, and they degrade
to a thinner history rather than a richer false one. Nothing manufactures a
`MatterProceduralDevelopment` from an old note, nothing infers
`RECEIVED`/`DISCOVERED` provenance, nothing invents a date, nothing reads a legal
stage out of a document name and no archive record is rewritten. A `LEGACY`
external position keeps the neutral heading it has always had, because nothing
about it changed and a file that started announcing a gap in itself would be
asking somebody to close one that cannot honestly be closed (docs/adr/0091 §3.4).

## 17 — What is deliberately deferred

Not built here, and each waits for lawyers to use V1 first: a separate VTK
workflow; separate government-regulation and minister-regulation workflows; a
detailed Koda-initiative workflow; a directive-specific transposition graph; a
configurable workflow editor; drag-and-drop nodes; automatic node inference; and
automated register fetching.

## Alternatives considered

**A second history engine beside `timeline.py`.** Rejected. Two projections of
«what happened on this file» is two answers, and the day they disagreed there
would be no way to tell which was meant. The existing module already had the four
properties that matter — canonical sources, `operation_id` grouping, no
field-level noise, permission before sorting — so the work was to extend them, not
to restate them somewhere else.

**A `MatterHistoryItem` table, or an `Event` model.** Rejected, and not narrowly:
a persisted projection is a second place for facts the domain already holds, and
therefore a second thing that can disagree with the records it was built from. It
also fails the permission rule by construction — a stored row has to be filtered
after the fact, which is the leak class AUTH-003 closed.

**Grouping by a time window.** Rejected. Two records written in the same minute by
the same person about the same organisation are routinely two separate acts, and a
window that grouped them would silently merge two facts into one on exactly the
busy files where it matters most. `operation_id` is an explicit statement made at
write time and is the only grouping evidence used.

**Copying the stage onto `MatterProceduralDevelopment`.** Rejected in
docs/adr/0091 §5.2 and not reopened: a copy is a second place for one fact. The
operation identifier ties the two writes without either record holding the other.

**Deriving `Matter.track` from `Õigusakt`.** Rejected. A `Seadus` transposing a
directive is a domestic instrument on a `NATIONAL_TRANSPOSITION` track, which is
precisely the file such a rule would be wrong about. The grouping chooses a
*presentation* and writes nothing.

**Marking earlier nodes complete from a later current stage.** Rejected — this is
the late-entry rule, and it is the single most load-bearing decision in §13. It
would have made the rail look better on every archive Matter by asserting three
milestones nobody recorded.

**Replacing the dated strip with the rail.** Rejected. They answer two different
questions — which dated points this file has, and which step of a procedure it is
on — and the strip's columns, sources and states are a reviewed decision
(docs/adr/0074 §12, docs/adr/0083) that this package has no finding against. What
was wrong was its *name*, and that is what changed.

## Consequences

- One projection, one place: `app/matters/timeline.py` is the substantive history
  and `app/matters/legal_process.py` is the rail, and neither reads the other.
- Two audit event types stop drawing a chronology row (`SUBMISSION_SENT`; and
  `NEXT_ACTION_SET` for the currently open step) and both stay fully auditable.
- A work victory moves in the chronology for every record whose business period
  differs from its confirmation — which is most of the archive, and is the point.
- `MATTER_STAGE_CHANGED` payloads written from now on carry stable stage keys.
  Older rows are resolved by label, live, and nothing is backfilled.
- A new read-only page exists per Matter, reachable only from the Matter it
  belongs to and scoped by the same chokepoint as everything else.
- Matter-detail visual baselines move.

## Reversibility

High. Every change is a projection or a template: restoring `SUBMISSION_SENT` to
`TIMELINE_EVENT_TYPES`, returning the work victory to `confirmed_at`, dropping the
effect folding, removing `legal_process.py` and deleting one URL each undo
independently, and none of them leaves data behind. The two payload keys are
additive and ignored by every reader that does not want them.
