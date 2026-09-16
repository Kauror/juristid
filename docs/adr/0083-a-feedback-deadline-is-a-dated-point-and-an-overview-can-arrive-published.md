# 0083 — A feedback deadline is a dated point, and an overview can arrive already published

**Status:** accepted
**Date:** 2026-09-15

> **§2 is narrowed by ADR 0085 (2026-09-16), on one point and no other.** The
> panel's two optional boxes and its three answers are exactly as decided here,
> and the date still carries **no `initial`** — a pre-filled one would make
> «neither filled» unreachable, which is this section's own reasoning and is
> still right. What is new is that the box fills itself *visibly* at the moment
> somebody starts typing an address, which is the moment the published path is
> chosen; an untouched form still submits two empty boxes. The record is also
> now called `Ülevaade / uudis` and its address may be any public
> `http`/`https` page, which supersedes ADR 0081 §3 rather than anything here.
> §1, the feedback-deadline half, is untouched.

Two findings from using the Teema page after the 2026-09-15 release. They are
unrelated as defects and are decided together because each one narrows a
decision the same page already carries.

**Narrows ADR 0074 §12.1** on one point: `MatterEngagement.feedback_deadline`
draws a process-strip column. The engagement itself stays retired from the
strip, and `MatterImportantDate` and `StageVocabulary` stay retired entirely.

**Narrows ADR 0081 §1 and §2** on one point: `+ Kodulehe ülevaade` asks for the
address and the publication date, optionally. Everything else those sections
decided — no title, no description, no attachment, no Uus teema option — stands.

## 1 — A reply-by date was only readable by scrolling

ADR 0078 §3 gave `Kaasamine` a `feedback_deadline` and put it on the
engagement's own chronology row, which is where the fact belongs. In use that
turned out to be the only place it is, and the chronology is below the fold: a
lawyer opening a file could not see that members owe answers by the 22nd without
scrolling past everything else on the page and reading a `sub` line.

The strip above it exists for exactly that question. ADR 0074 §12.4 says it
carries two kinds of truthful information — acts that have happened, and
**canonical dated points the file is known to be heading for** — and a
consultation round asking for answers by a named day is the second of those as
plainly as `Arvamuse tähtaeg` is.

### Decision

**`Tagasiside tähtaeg` is a process-strip column, one per `Kaasamine` that
carries a reply-by date.**

* The engagement is **not** a milestone and §12.1 stands: a consultation with no
  `feedback_deadline` draws nothing, however many rounds a file has run. What
  draws a column is the dated point, never the act.
* «Keda kaasati» reads as the column's `title`, the same mechanism that tells
  two `Jõustumine` columns apart. Not a title invented here.
* Exact day. `feedback_deadline` stayed on ADR 0079 §11's exact-only list when
  `Kaasamise kuupäev` left it (ADR 0082 §2), so there is no precision to read.
* Scoped through the child's own `visible_to` before the strip is derived, so a
  restricted round cannot change the column count, the connector count or the
  spacing for a reader who may not open it (AUTH-003, ADR 0074 §13).
* Ordered by `(feedback_deadline, pk)`; same-day ties fall back to the phase
  sequence, where a feedback deadline precedes `Koja arvamus` because members
  answer Koda before Koda answers the ministry.

### It changes nothing else, and that is the point

`Matter.response_deadline` is untouched — different source, different label,
different sentence. The column creates no `NextAction`, no work item, no badge,
no lateness and no overdue reading; it does not reach Minu asjad, Tähtajad, the
department surfaces, statistics, the search projection or the archive
projection; and nothing completes or cancels because a date went past. Every one
of those non-effects is what ADR 0078 §3 decided when the column was added, and
drawing the date on a strip is a *reading* of the record, not a new obligation.

**Two deadlines on one strip is the honest state, not a collision.** What this
office owes and what it asked of other people are different facts that can fall
on one day, and a strip that merged them would turn an internal collection date
into an official obligation.

## 2 — The overview panel looked like an unusable text box

ADR 0081 §1 shaped `+ Kodulehe ülevaade` as one button and no fields:

> at the moment a lawyer decides that this Matter should be written up on
> koda.ee there is no address, no publication date and no headline: the page
> does not exist.

That is right about the case it describes and silent about the other one. An
overview is frequently recorded **after** the page is already up — somebody
writes the file up, publishes it, and only then goes back to the Matter — and
those people had to file a plan and then publish it from a second control in
order to state something that was already true.

Worse was what the panel looked like. A form with no fields, a paragraph of
static help text and a button saying `Salvesta` reads as a text area that failed
to load, not as a complete form. The first thing the control taught a new
reader was that it was broken.

### Decision

**One form, two optional boxes, three answers.**

| filled in | outcome |
| --- | --- |
| neither | `PLANNED`, exactly as before |
| both | `PLANNED → PUBLISHED` in one act |
| one | refused, naming the other, with what was typed still in the boxes |

* The primary action says **`Lisa planeeritud ülevaade`** rather than
  `Salvesta`. A button has to promise the thing the empty form does, and the
  empty form is the common case.
* The optional pair sits under its own legend — «Kui ülevaade on juba
  avaldatud» — with a note saying plainly that filling both records it as
  published. The alternative is explained where the decision is made rather
  than in a paragraph above the form.
* **No `initial` on the date**, unlike the publish form. There, today is a
  helpful default because the form only ever publishes. Here an empty submit is
  a valid answer, and a pre-filled date would make «neither filled» unreachable:
  every plan would arrive carrying a publication date nobody typed.
* Half a publication is refused rather than quietly filed as a plan. Dropping an
  address somebody pasted would lose the one fact they opened the panel to
  record.
* The publication reuses `publish_website_overview`, so the koda.ee boundary,
  the both-or-neither rule and the audit events stay in one reviewed place, and
  the lifecycle is the documented `PLANNED → PUBLISHED` rather than a fourth way
  in. **Two audit events**, because the row genuinely passed through both
  states.

**A planned row now says what to do next.** Its disclosure read `Avalda`, which
names the lifecycle transition and leaves the reader to discover it wants two
things. It now reads **`Lisa link ja avaldamiskuupäev`** — the next action
itself, which is what a planned row exists to prompt.

### What is unchanged

The `PLANNED → PUBLISHED / CANCELLED` lifecycle and its terminal states; the
correction path for an address already recorded, including on a closed Matter
(ADR 0081 §5); the closed-Matter guard on everything that creates business
content; optimistic concurrency; the audit vocabulary; and the absence of any
search, archive, reporting, work-list or deadline coupling. No title, no
description, no attachment, and still no option on Uus teema.

## Alternatives considered

**Draw every `Kaasamine` on the strip, not only the dated ones.** Rejected —
that is exactly what ADR 0074 §12.1 retired, and for a reason that has not
changed: it made the strip a second, shorter copy of the chronology.

**Put the feedback deadline in the header band beside `Arvamuse tähtaeg`.**
Rejected. The header says where the file stands *now* and holds one official
obligation; a second deadline there would read as a second thing this office
owes.

**Give the feedback deadline a lateness reading, since it is a deadline.**
Rejected, and it is the decision most worth stating. What was asked of a
ministry or of the membership is not a promise this office made, and a column
that went red would make every historical consultation somebody types in overdue
on the day it is entered — the failure ADR 0078 §3 exists to prevent.

**Two submit buttons on the overview panel** — «Lisa planeeritud» and «Lisa
avaldatud». Rejected: it forces a decision before the form is filled, and the
fields themselves already say which path is being taken.

**A direct `create-as-published` service, skipping `PLANNED`.** Rejected. It
would be a second way into the same state with its own copy of the koda.ee
boundary, and the day the two disagreed is the day a publication accepted an
address a correction would have refused.

## Consequences

A Matter running consultation rounds gets a longer strip. That is the
information the strip exists to carry, and a file with no reply-by dates draws
exactly what it drew before.

The strip's vocabulary is six labels rather than five. Any future addition
should meet the same test this one did: a *canonical dated point the file is
heading for*, not a record that happens to have a date on it.

## Reversibility

High, and separate per half. Removing the feedback-deadline source leaves every
stored row untouched — the strip is derived and there is no table behind it.
Removing the two optional boxes leaves `plan_website_overview` and
`publish_website_overview` exactly as they are, because the panel calls them
rather than replacing them.
