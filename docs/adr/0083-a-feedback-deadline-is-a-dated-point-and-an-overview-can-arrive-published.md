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

  **Superseded on 2026-09-16 for the label and its rationale — see the
  amendment at the end of this document.** The button reads `Lisa ülevaade`.
  The reasoning above is right about `Salvesta` and wrong about what replaced
  it: the empty form is the common case, but it is no longer the only one this
  form reaches, and a label naming it is untrue of every submission that
  carries an address and a day.
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

---

## Amendment, 2026-09-16 — the button names the record, not one of its states

- Status: accepted, amending the primary-action bullet in §2's *Decision*
- Scope: the label on `+ Kodulehe ülevaade`'s submit button, and the reasoning
  that chose it. Nothing about the lifecycle, the two-path form, the koda.ee
  boundary, the refusal, the audit events or the closed-Matter rules changes.

### What was decided before

§2 decided one form with two optional boxes and three answers, and then chose
the button's words from the first of the three: **`Lisa planeeritud
ülevaade`**, because «a button has to promise the thing the empty form does,
and the empty form is the common case».

### Why it is superseded

The argument is sound against `Salvesta`, which promises nothing, and it stops
being sound the moment the same decision gives the form a second outcome. A
button that says `planeeritud` describes one of the two records this form can
create, and is untrue of the other: on every submission that carries a
`Kodulehe link` and an `Avaldamise kuupäev` the row is filed `PUBLISHED`, and
the control that did it named a plan.

That is worse than vague. `Salvesta` left the reader to work out what the form
produced; `Lisa planeeritud ülevaade` told them, incorrectly, and told them
before they had filled anything in — so the label that was chosen to stop the
panel reading as broken would instead have contradicted the panel's own
`Kui ülevaade on juba avaldatud` legend two lines below it.

The common-case argument also proves less than it was asked to. Which outcome
is common is a fact about this month's usage; which outcomes exist is a fact
about the form. A primary action is named from the second.

### What is decided now

**The primary action is `Lisa ülevaade`.** It names the record the form
creates, and is true of both paths through it:

| filled in | outcome | is `Lisa ülevaade` true of it |
| --- | --- | --- |
| neither | `PLANNED` | yes — an overview was added |
| both | `PLANNED → PUBLISHED` in one act | yes — an overview was added |
| one | refused, naming the other | nothing was added, and nothing was promised |

The word `planeeritud` is gone from the form altogether rather than moved: the
plan is still the empty form's outcome, and the place that says so is the
optional pair's own legend, where the reader is choosing between the two.

Implemented on main in `2e97c9b` («Say `Lisa ülevaade`, because the form no
longer only plans»), which is why this is recorded as an amendment rather than
a decision waiting to be built.

### What this amendment does not change

- **The two-path form, unchanged.** Neither box filled is a `PLANNED` row; both
  filled is `PLANNED → PUBLISHED` in one act through the existing
  `publish_website_overview`; one filled is refused, naming the other, with
  what was typed still in the boxes.
- **The koda.ee URL boundary, the audit vocabulary and the two audit events**
  are exactly as §2 decided. The publication still goes through one reviewed
  service rather than a second way in.
- **`PLANNED → PUBLISHED / CANCELLED`** and its terminal states, the correction
  path for an address already recorded including on a closed Matter (ADR 0081
  §5), the closed-Matter guard on everything that creates business content, and
  optimistic concurrency — all untouched.
- **No `initial` on the date**, for the reason §2 gave: a pre-filled date would
  make «neither filled» unreachable.
- **A planned row's disclosure still reads `Lisa link ja avaldamiskuupäev`.**
  That label already named the next action rather than the transition, and this
  amendment is about the panel's button alone.
- **§1 is untouched.** `Tagasiside tähtaeg` is still a process-strip column, one
  per `Kaasamine` carrying a reply-by date, with no lateness reading and no work
  item — and everything that decision left alone is still left alone.
- **Still no title, no description, no attachment, and no option on Uus teema**
  (ADR 0081 §2).
- **No migration and no data change.** The stored rows, their states and their
  history are what they were; a button's words are not a fact about a record.

### Reversibility

Higher than the decision it amends: the label is one string in
`templates/matters/partials/add_to_matter.html`, pinned by
`tests/test_feedback_deadline_strip_and_overview_form.py` and by
`e2e/test_website_overview.py`'s `PLAN_BUTTON`. Reverting it would mean
re-adopting a sentence that is false on one of the form's two paths, which is
the reason this amendment exists.
