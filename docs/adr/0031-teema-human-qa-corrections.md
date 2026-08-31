# 0031 — What hands-on use changed about the Teema workspace

- **Status:** Accepted
- **Date:** 2026-08-24
- **Amends** ADR 0030 (the approved Teema workspace redesign). Where the two disagree, this one holds.
- **Depends on** ADR 0010 (server-rendered HTML, HTMX fragments, browser tests as the verifier) and ADR 0011 (NextAction semantics), both unchanged.

## Context

ADR 0030 was implemented, merged and deployed to production. A lawyer then used
it on real data for a working session, and eight things came back.

That is the point of deploying it. What follows is not a second redesign: the
domain model, the authorization chokepoints, the audit trail, the composer's
one-save contract and the two-tab structure are all untouched. What changed is
where three things sit, one bug in who owns a next step, one bug in a calendar,
and a set of small refusals that made routine work slower than the OneNote page
this replaces.

Two of the eight were decisions ADR 0030 made deliberately and argued for. They
lost to use, and the reasoning below says why — because a decision reversed
without a recorded reason gets made again.

## Decision

### 1. `Koja seisukoht` is a rail fact, not a main-column band

ADR 0030 §12 moved the position out of a tab and into the main reading flow, on
the argument that what Koda argued is the second question anybody asks and
should not be behind a tab. The first half of that is right and stands. The
second half was wrong about the shape.

In the main column it was a full-width band holding a summary, a rationale, an
inline editor and — under it — a separate strip listing sent opinions. On a real
Matter that is two sentences and a PDF occupying the width of the page, pushing
the composer and the chronology below the fold. The department's own mockup put
it in the 300px rail, and the mockup was right: this is read-first reference, not
a work surface.

So the rail states the position, condensed, with the sent opinion beside it and
one link onward. Writing happens on the Arvamused surface, where the recipients,
the channel and the exact evidence already live.

**The sent-opinion strip is gone entirely, not moved.** It repeated the latest
canonical opinion that the rail block already shows. One fact, one place.

### 2. There is a full edit page again, beside the inline controls

ADR 0030 §22 removed the edit page in favour of inline controls, on the argument
that changing an owner should not mean re-submitting every other value. That
argument is correct and the inline controls stay.

It is not the only case. A Matter filed wrongly has several facts wrong at once,
and correcting it through five separate controls in two regions of the page —
each with its own save and its own re-render — is one job the page refused to
admit was one job. `Muuda teemat` is that job: one form, one save, one
transaction, either all of it or none of it.

**What the page does not offer is a decision, and is stated on the page.** The
reference, the origin and the imported source reference are shown and refused:
where a record came from is not somebody's to edit today. A better interpretation
is recorded by adding a reference, never by rewriting one. The data class keeps
its own deliberate surface (ADR 0024), and nothing here creates taxonomy.

Every field goes through the service that already owns it, so an edit made here
is audited exactly as the same edit made inline. A rename gets its own event —
`MATTER_TITLE_CHANGED`, carrying both strings, because the old title is how
somebody finds a Matter again after it stopped being called what they remember.

### 3. `Minu töö` is one list, banded by date

ADR 0030 kept the Stage-1 split: DO actions banded by urgency in one column,
WAIT and MONITOR in another. The split encoded a real distinction, and the
distinction survives — but the page's organising question does not respect it:

> **When do I need to care about this again?**

That has one answer per action and it is a date, whatever the mode. A ministry's
answer expected on Thursday and an opinion due on Thursday are both Thursday's
problem, and two columns made a lawyer read two lists and merge them in their
head — which is the merge the page should be doing for them.

One chronological list, five bands: passed, today, the next seven days, later,
undated.

**Unifying the list is not unifying the vocabulary.** The date says where an
action sits in time; the mode chip on the row says what the date *means*; and
only DO + DEADLINE is ever coloured or worded as late. A passed WAIT or MONITOR
review is warning-toned and reads `Ülevaatus möödas`, never `Tähtaeg möödas`
(master specification 18.8). The band heading is warning rather than danger for
exactly this reason: it holds both.

`Järgmine samm puudub` stays a separate, non-chronological section. An absence is
not a date and must not be given a position in time.

### 4. An open next step follows the file when the file changes hands

A bug, not a design decision. `set_next_action` defaults `responsible` to the
Matter's owner, so an action nobody named a person for is the *owner's* action.
`assign_matter` never moved it: after a handover the new owner's own file had no
next step in `Minu töö`, and somebody who no longer owned the Matter still
carried it.

`assign_matter` now moves the open action — and only when its responsible person
*is* the previous owner. Somebody deliberately made responsible for one step on a
colleague's file stays responsible; moving that would be the system overruling a
decision a person made. The handover is named in the assignment event's payload
rather than raised as a second event: one thing happened.

The same report exposed a second, latent defect. The old banding required
`DEADLINE` semantics for the near bands and a beyond-horizon date for the far
one, so a DO carrying any other semantics and dated inside the next week fell
into no band at all and vanished from the page. The register's own parser
produces exactly that combination for a source naming a vague month. Banding now
reads the date and nothing else.

### 5. A date box starts on today — when the box is the only thing it says

> **Amended 2026-08-30.** `Arvamuse tähtaeg` on `Uus teema` has moved from the
> defaulting list below to the exception list — by the rule this section states,
> not against it. When this was written nothing read that field's emptiness: the
> date was stored and shown on the Matter header, and a pre-filled today cost
> nothing. It is now the third source of the shared work model
> (`app/matters/work_items.py`), so an empty box means *no commitment* and a
> filled one means *a deadline exists on this day*. Under the default, a Matter
> created and left alone was due on the day it was entered and overdue on every
> deadline surface the next morning, against a promise nobody had made. `Saabus`
> keeps its default: an arrival date is an observation, and nothing reads its
> emptiness. The two lists below are corrected to match; no other decision in
> this section changes.

`Saabus`, `Toimus`, the Kaasamine date, the intake date and the header's
`+ Tähtaeg` all pre-fill with the current date. `initial` fills an unbound form
only, so a posted value always wins, a validation error keeps what was typed,
and a deliberately cleared date stays cleared.

**Five date boxes deliberately keep no default**, and this is the more important
half of the decision. A form that reads a field's *emptiness* as a signal cannot
have that field defaulted, because there a default does not save typing — it
states a fact nobody gave:

- the composer's date controls (`next_date`, `deadline_date`) — a step with no
  date is the one combination the domain refuses, being a deadline that cannot
  be met, missed or planned against. A default answers that refusal with a
  deadline nobody chose. `deadline_date` also offers a month, a quarter and a
  year, and a day pre-filled while somebody means "III kvartal" is noise in the
  one control whose whole job is to say how precisely a date is known. (Since
  ADR 0052 `next_date` is an exact date with no period control behind it, and
  keeps no default for the first reason;)
- `final_sent_on` — a send date with no chosen file is an opinion claimed
  without its evidence, which the form refuses. Defaulted, it refused every
  ordinary closure that was not also recording a sent opinion;
- `PeriodForm.exact_date` — `Jõustub üldises korras` means the date is *not
  known*, and a form carrying one is refused. Defaulted, that save refused
  itself;
- `Arvamuse tähtaeg` on `Uus teema` (`MatterCreateForm.response_deadline`) —
  added by the amendment above. Its emptiness is now meaningful: blank means no
  response commitment exists, and a stored date means Koda has promised an
  opinion by that day. Defaulting today invents a deadline nobody stated, and
  since the field became a source of the shared work model that invention is
  not inert — the Matter falls due on the day it was entered and reads as
  overdue on every deadline surface the next morning.

The first four were found by the browser lane after the first, broader
implementation was pushed; the fifth arrived later, when the field it names
gained a reader. They are named here rather than quietly narrowed because the
distinction is the rule, not the exception list: **default a date box only when
nothing reads its emptiness.**

`Muuda teemat` has no default for a different reason. It is always opened on a
Matter that already exists, so a default would only apply where a date is
genuinely empty — and there, pre-filling today would state a fact nobody gave.

`Toimus` = today is recorded as *now*, not as midnight. The box is pre-filled, so
leaving it alone is the ordinary case, and stamping 00:00 on something written at
half past two is a small untruth on every routine save. Any other day is that
day, at its start.

This reverses one line of ADR 0027's reasoning about `Kaasamine` — that a record
may be about a consultation from 2019 and a pre-filled box is answered by
pressing save. The overwhelming case is recording something that just happened.
Backdating is one edit; typing today is every time.

### 6. Month navigation does not close the calendar

A bug with a single cause. `buildCalendar` empties the panel before redrawing it,
which detaches the button that was clicked; the click then reached the document,
where the outside-close check asked whether the wrapper contained `event.target`
— and a detached node is contained by nothing. The panel closed the instant it
had been rebuilt.

Two independent fixes, so neither depends on the other: the navigation handlers
stop propagation, because navigating a month is not a click outside the calendar;
and the outside-close is now a single delegated document listener that reads
containment from the event's composed path rather than from the live tree. The
per-input listeners it replaces also leaked, one per date field ever rendered.

### 7. Mode chip selection is carried by colour, not by opacity

> **The Teema composer no longer has mode chips (ADR 0052, 2026-08-31).** They
> are still on Uus teema, still built from `.modechip`, and everything below
> still describes how they look and why. What changed is where they are asked.

The composer's chips showed their unselected state as `opacity: 0.55` against a
selected `1`. On the dark surface that is two greys, one slightly greyer.
Selection changes the background and the border now, and every label is painted
at full strength, unselected and selected alike.

Selected, each mode keeps the shape it has everywhere else in the product: TEEN
the brand fill, OOTAN a solid bright border, JÄLGIN the same treatment and still
dashed, and `Ei muuda` neutral with no brand colour near it. Unselected, the
three share a quiet resting state and JÄLGIN keeps its dashed border — the one
shape difference that survives both states. Shape, not colour, is what carries
the meaning for a reader who cannot separate the colours at all.

### 8. `Kaasamine` has exactly one path

The composer offered `+ Kaasamine` with a kind and a date; the Kaasamine section
asks for the title, the participants and the link the record is actually for.
Two entry points for one act, with different fields, is how two people record the
same consultation twice and how one of the two records ends up poorer. The
composer's version is gone. The section, which shows what is already on the file
while you add to it, is the one path.

## Consequences

- One migration, `audit.0015`: a `choices` change on an existing column for the
  new event type. No data moves, no row is rewritten, the append-only trigger is
  untouched.
- `MATTER_TITLE_CHANGED` is deliberately **not** in
  `matters.timeline.TIMELINE_EVENT_TYPES`. Renaming a record is maintenance, not
  authored chronology — the same rule that keeps visibility and data-class
  changes out of the narrative. It is in the full history.
- `Muuda teemat` is a second surface that can change most of a Matter. It is
  guarded by `may_write_business_content` and by `get_visible_matter`, answers
  404 rather than 403 to a reader, and writes exclusively through named services.
  Adding a field to the form without a service to carry it is the mistake this
  arrangement exists to make impossible.
- Nothing in this round touched historical data, the Excel cutover, the opinion
  archive, taxonomy vocabulary, closure semantics, Töövõit governance, or the
  authorization architecture.
