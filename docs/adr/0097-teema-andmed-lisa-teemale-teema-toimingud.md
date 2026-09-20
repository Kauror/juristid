# 0097 — `Teema andmed`, `Lisa teemale`, `Teema toimingud`

**Status:** accepted
**Date:** 2026-09-20

**Amends ADR 0096.** Three of that record's decisions stand exactly as written —
`Uus teema` is the master, `Valdkonnad` and `Hetkeseis` are visible chips, and
the ordinary Teema UI does not expose `Nähtavus` — and its deletion protocol is
untouched and is not restated here. What this record amends is §1's *scope*:
0096 kept three questions on `Muuda teemat` that `Uus teema` does not ask, and
named them as a deliberate exception. The owner withdrew the exception, and with
it three more decisions about where facts and events belong.

The Teema page now has three regions and the reader can say what each of them is
for:

```
TEEMA ANDMED        what this Matter is
LISA TEEMALE        what happened, and what Koda did about it
TEEMA TOIMINGUD     what you do to the Matter itself
```

Nine decisions:

1. **`Uus teema` is the master, without exceptions.** `EDIT_ONLY` is empty.
2. **`Sildid` leaves the ordinary Teema UI.** The vocabulary stays.
3. **`Menetlusliik` leaves the ordinary Teema UI.** The column stays.
4. **The Matter-level `Kellele` leaves the ordinary Teema UI.** `Koja arvamus`
   keeps its recipients.
5. **`Menetluse link` is `Teema andmed`**, asked on both Teema forms and no
   longer a content action.
6. **`Menetluse areng` retires as a word**, and `+ Märge` writes the structured
   record it wrote.
7. **`Töövõit` is an exact date** on new entry.
8. **`LISA TEEMALE` is four families**, with the distinctions asked second.
9. **`TEEMA TOIMINGUD` is a separate region**, holding `Lõpeta teema` and
   `Kustuta teema`.

**No migration.** Nothing here changes a column, a relation or a stored value.
Every question that leaves the interface leaves its data, its service, its audit
event and its importers exactly where they were; what is removed is the ordinary
UI's claim to have an answer. `makemigrations --check` is clean.

---

## 1 — `Uus teema` is the master, without exceptions

### Context

ADR 0096 §1 made the creation screen canonical and then carved out three fields:
`Menetlusliik`, `Kellele` and `Sildid` stood on `Muuda teemat` under a heading
reading «Ainult olemasoleva teema kohta», on the argument that they are
canonical facts, that they are absent from creation by earlier decision
(ADR 0090 §4, §5), and that a fact nobody could answer anywhere would be a fact
nobody could correct.

The argument is sound and the shape it produced is not. A question a lawyer
never meets while filing, waiting on the screen they open to fix a typo, is a
question only ever answered by whoever happens to be on that screen — and the
heading, which was doing its job, made the exception legible without making it
less of one. The owner looked at the two screens and asked why the correction
page has a section the filing page has no counterpart for.

### Decision

The exception is withdrawn. `MatterCreateForm` and `MatterEditForm` ask the same
questions; `EDIT_ONLY` in `tests/test_teema_live_audit_round_2.py` is the empty
set, which is the strongest form that contract has taken. The heading is gone
with the three fields under it.

`Muuda teemat` still differs from the master in the two ways ADR 0096 §1 names,
both consequences of the record existing: values arrive pre-filled, including
withdrawn vocabulary rows offered back and marked, and `Märkmed` is not on it.

### Consequences

Sections 2, 3 and 4 below are what this decision costs, one field each, and each
is stated separately because each has its own data and its own remaining
surfaces.

---

## 2 — `Sildid` leaves the ordinary Teema UI

### Context

`Tag` is a governed vocabulary with an `is_active` flag, an audit event and a
register filter. It was never asked for on `Uus teema`, so the only way to put a
tag on a Matter was to open the correction screen of a file that was already
right.

A vocabulary nobody is offered while filing is a vocabulary used by whoever
happens to open the edit page. That is how a governed taxonomy turns into
personal shorthand — and it is the opposite of what governance is for.

### Decision

`Sildid` is removed from `MatterEditForm`, from `Muuda teemat` and from the
ordinary Teema read surfaces. The field is **deleted, not hidden**: there is no
control, no hidden input and no `clean_tags`, so a crafted `tags=` in a POST to
`matters:matter_edit` binds to nothing, and `matter_edit` calls `set_tags` at all
— not even with a default, which would have cleared what a Matter carries.

### Consequences

`Tag`, `MatterTag`, `set_tags`, `TagAssignmentForm`, `MATTER_TAGS_CHANGED` and
every stored assignment are untouched. Historical tags survive every save of
`Muuda teemat`. The register's own tag filter is unaffected — it reads.

The tag architecture is deliberately **not** deleted. This is a decision about
which question the ordinary lawyer-facing product asks, and a later round that
wants tags back wants them on the master first.

---

## 3 — `Menetlusliik` leaves the ordinary Teema UI

### Context

The same shape as §2 and a sharper version of it. `Matter.track` is a real
classification that the register's importers state from the source. ADR 0090 §4
removed it from `Uus teema` — one classification question per dimension while
filing — and it stayed on `Muuda teemat` and on the Teema rail.

So a lawyer files a Teema without ever meeting the question, and then finds it
waiting on the screen they open to correct a mistake, where the only thing it
can be is answered by somebody reconstructing a procedural track from memory
months later.

### Decision

`Menetlusliik` is removed from `MatterEditForm`, from `MatterFieldForm`, from
`FIELD_SERVICES` and from the rail row that edited it. `matters:update_field`
answers 404 for `track`: the endpoint is not a route rather than a route behind
no button, which is the one shape that survives «the control is gone».

### Consequences

`Matter.track`, `Track`, `change_track` and `MATTER_TRACK_CHANGED` are untouched.
The importers, the register refresh and the final cutover all still write the
column; the register still filters on it and the reporting still reads it. A
Matter's track survives every save of `Muuda teemat`, because the view passes no
value and the service is therefore never called.

---

## 4 — The Matter-level `Kellele` leaves the ordinary Teema UI

### Context

`Matter.addressee_organisation` has ADR 0090 §5's history and one problem of its
own: beside `Saatja`, on the same screen, `Kellele` reads as *who Koda's opinion
went to*. It is not. That fact lives on the `Submission`, it is several
organisations rather than one, and it is the one that matters when an opinion is
sent.

Two counterparty questions on one screen, where the second is the one people
think the first is not, is a screen that produces wrong data confidently.

### Decision

`Kellele` and its typed carrier are removed from `MatterEditForm`, from
`MatterFieldForm`, from `FIELD_SERVICES` and from the rail row that edited it.
`matters:update_field` answers 404 for `addressee_organisation`.

`matter_edit` passes **no** `addressee_organisation` to `set_organisations`. That
is the load-bearing detail: the parameter's default is `_UNSET`, meaning «leave
this alone», and passing `None` instead would have been a decision — it would
clear a register addressee nobody was offered the chance to state.

`addressees_by_usage`, `addressee_name_field` and the picker mixin's Adressaat
slicing are removed with the question, having no caller left.

### Consequences

**This is about the ordinary Matter UI and nothing else.** `Koja arvamus` keeps
its recipients: still first-class, still several, still defaulted from the
Matter's `Saatja`, and still saved without touching it. `Seotud materjalid` still
prints an addressee on its cards. The column keeps every stored value and the
counterparty coverage report still reads it.

---

## 5 — `Menetluse link` is `Teema andmed`

### Context

A `MatterProceduralLink` says *where the proceeding this file is about is
happening*. ADR 0089 established it and put a `+ Menetluse link` chip in the
`LISA TEEMALE` launcher; ADR 0094 §3 added the same two boxes to `Uus teema`.

`LISA TEEMALE` is a list of things that happened. An address is not one of them
— it is a property of the Matter in the same way its `Saatja` and its `Õigusakt`
are — and it is normally known at the moment the file is opened.

### Decision

The question is asked in `Teema andmed` and nowhere else. `Uus teema` and
`Muuda teemat` both include `matters/partials/procedural_link_create.html`;
creation passes `ProceduralLinkCreateForm` and correction passes
`MatterLinkForm`, which arrives bound to the Matter's first link and corrects it
through `correct_procedural_link` under the revision the page was drawn from.

`+ Menetluse link` is gone from the launcher and
`matters:add_procedural_link` is not a route.

The user is asked for **`Link` and `Nimetus`**, and nothing else. No source
classification — every row this block writes is filed under
`ProceduralLinkCreateForm.STORED_KIND`, the enum's own `Muu menetluslink` — and
**nothing is inferred from the address**: no hostname rule, no fetch, no
scraping, no model. A correction preserves the kind its row already carries
rather than restamping it, so a link somebody deliberately filed as `EIS` stays
`EIS`.

### Consequences

A Matter carrying several links keeps every one of them; the second and third are
read and corrected on the Teema page's `Menetluse lingid` card, which has held a
`Paranda` per row since ADR 0089 §6. That card's empty state is now a quiet
`+ Lisa` pointing at `Muuda teemat` rather than nothing at all — an address
recorded nowhere, with no visible way to record one, is a capability that has
quietly left the product.

There is still **no deletion** of a procedural link, here or anywhere: a mistaken
row is corrected, because what the file recorded and who recorded it is part of
the file (ADR 0084 §8). Emptying the address of a link that exists is refused
rather than silently ignored.

Historical typed links keep their kinds, and `ProceduralLinkEditForm` still
offers the whole vocabulary where a person states the fact deliberately.

---

## 6 — `Menetluse areng` retires as a word

### Context

Two chips stood side by side in the launcher and asked the same question with
different amounts of ceremony:

* `+ Märge` — one box, wrote an `Entry`;
* `+ Menetluse areng` — a four-way `Täpsus` group, two text boxes, an optional
  stage and an optional next step, wrote a `MatterProceduralDevelopment`.

«Ministeerium saatis uue eelnõu versiooni» is a *menetluse areng*; «Rääkisin
Justiitsministeeriumiga» is a *märge*. A lawyer who has just done one of them is
not thinking about which of the two words the application wants — and «menetluse
areng» was never a phrase anybody asked for. It is an implementation abstraction
that reached the screen.

### Decision

One visible control, `+ Märge`, and **it writes the structured record**.

That is the opposite of the obvious reading, and it is deliberate.
`MatterProceduralDevelopment` exists for three reasons (ADR 0091 §5.1) and only
two of them were about the control: the date had to be allowed to be unknown,
the lawyer's note had to be a second field, and **a projection needs a title it
did not have to parse**. The third is untouched by anything here. `title` is
«what happened», stated; `Entry.body` is prose, and deriving a headline from its
first sentence is exactly the guessing this repository refuses everywhere else.

So the toolbar loses a concept and the database keeps a record. `MatterNoteForm`,
`ProceduralDevelopmentForm`, `matters:add_development` and `views.add_development`
are removed; `MatterProgressForm` posts to `matters:add_note`.

**What widens, stated rather than discovered.** «Rääkisin
Justiitsministeeriumiga» is now a `MatterProceduralDevelopment`, and under that
record's old reading — *one step the external procedure took* — a phone call is
not one. The category is now «what happened on this file». Nobody sees the word
«areng»: not on the panel, not on the timeline row, not in the audit summary a
reader sees.

### 6.1 — No `Täpsus`, and nothing inferred

`+ Märge · Tavaline` asks for one date, filled with today, clearable, always
`EXACT`. An emptied box is «kuupäev teadmata» rather than a refusal, which is the
one thing the four-way group bought that somebody writing up this morning's
events ever needed.

The group is **deleted from the form**, so a crafted `areng_precision=QUARTER`
reaches a form that never cleaned it. The column still stores all four values,
every historical row keeps its precision, and `ProceduralDevelopmentEditForm`
still offers the whole control when such a row is corrected — it decides per
*record*, which is where a statement about how well a date is known belongs.

`Uus hetkeseis` and `Järgmine tegevus` are optional and **never inferred**. No
text is read, no keyword matched, no model consulted; a save naming neither
changes neither, and nothing is read from or written to a `Menetluse link`. When
they are answered, they go through `change_stage` and the canonical `NextAction`
service in the *same* transaction as the note — a stage that moved without the
note that moved it would be a file claiming to be in the Riigikogu with nothing
saying how it got there.

### 6.2 — `Juristi märkus` is withdrawn from new entry

ADR 0091 §4 separated «what the ministry did» from «what this office makes of
it», and the distinction is real. Two text areas on the control a lawyer uses
every day, where the second is empty on nearly every save, is a form asking
somebody to classify their own sentence before it will take it.

`note` is deleted from the new-entry form and the column stores `""`. Historical
notes are kept and `ProceduralDevelopmentEditForm` still offers the box on a
record that has one. The label on the surviving box is `Mis juhtus?` rather than
`Mis menetluses juhtus` — the panel is no longer only about the procedure, and
the narrower wording would now refuse sentences it accepts.

### 6.3 — What retires with it

There is no UI path left that creates a bare `Entry` from the launcher. Entries
are still written — `PRAEGUNE TEGEVUS` writes one on every completed step, which
is the majority of them — still read, still corrected through `Muuda`, and still
carry their append-only `EntryRevision` history. **Nothing was migrated and no
historical row moved between tables.**

---

## 7 — `Töövõit` is an exact date

### Context

`+ Töövõit` carried the four-way precision composer, on the reading that a win
might be remembered as «kevad 2024».

The owner decided otherwise and gave the reason: a töövõit is something Koda
*achieved*, and the organisation should be able to say when it happened. A win
nobody can date to a day is a win nobody has finished establishing.

### Decision

`Millal` is one clearable box holding today. It is still **required** — an empty
box is refused rather than stored as `NULL`, which is the gap Stage-2G brief 22
closed — and a single day is stored as the period it is: `period_date` and
`period_end` the same date, `date_precision = EXACT`.

### Consequences

Historical `MONTH`, `QUARTER` and `YEAR` work victories keep their periods.
Nothing is backfilled, clamped or rewritten. `?toovoit=<aasta>` still reads
periods, and `DatePrecision` loses nothing — the Kaasamine, Jõustumine and
Oluline-tähtaeg panels are unchanged, and `Jõustumine` keeps its precision
deliberately, because a legal effective date genuinely can be «2027».

---

## 8 — `LISA TEEMALE` is four families

### Context

Twelve chips on one row, plus `Lõpeta teema` beside them. Every one of them was a
truthful distinction and the row was still wrong, because the lawyer standing in
front of it does not have a record type in mind. They have something that
happened, and they were being asked to classify it against the back of the
application before it would take it.

### Decision

Four peer choices, and the distinctions that remain are asked second, inside the
one that was chosen:

```
+ Märge                  Tavaline · Oluline tähtaeg · Jõustumine · Töövõit
+ Kaasamine              (no further question)
+ Arvamus / tagasiside   Meile saadetud · Teiste arvamus · Koja arvamus
+ Ülevaade / uudis       (no further question)
```

`Tavaline` and `Meile saadetud tagasiside` arrive chosen, because a family panel
that opens on more chips and no form is an extra click on every visit.

Nothing else is a peer of those four. `+ Järgmine tegevus` leaves the row as
well — see §8.2.

### 8.1 — Grouped on the screen, distinct in the database

This is a presentation change and deliberately not a data change.
`+ Arvamus / tagasiside` still writes three different records through three
different endpoints: `Meile saadetud tagasiside` and `Teiste arvamus` are two
provenances of one `MatterExternalPosition`, and `Koja arvamus` is a `Submission`
and is not that record at all. `Oluline tähtaeg`, `Jõustumine` and `Töövõit` keep
their own models, services and reporting.

A lawyer recording a win should not have to know that it becomes a special
reporting object; they should have to know that they are recording a win. **No
structured model is replaced to shorten a row of chips.**

### 8.2 — `+ Järgmine tegevus` leaves the row

There is at most one open `NextAction`, and there is now exactly one *ordinary*
way to set one: while a task is current, `Muuda` in `PRAEGUNE TEGEVUS`;
otherwise the optional `Järgmine tegevus` inside `+ Märge`, beside the thing that
prompted it. Two controls both offering to set «the next action» is how a lawyer
ends up believing they have two.

### 8.3 — One variable per sub-choice group

`WORKSPACE_PANELS` maps each operation to a *pair* — the family and the choice
inside it — so a refused `Oluline tähtaeg` reopens `+ Märge` **and** the right
chip within it.

`_workspace_choices` then gives each group a variable guaranteed to name one of
that group's own ids. Without it the failure is quiet rather than loud:
`open_choice` is global to the page, so a refusal from `+ Arvamus / tagasiside`
makes every `+ Märge` test false at once, that group renders with no radio
checked, and somebody opening `+ Märge` afterwards finds four chips and no form.
Nothing errors and nothing logs.

---

## 9 — `TEEMA TOIMINGUD` is a separate region

### Context

`+ Lõpeta teema` was a chip at the end of the launcher row and `Kustuta teema`
was on the edit page. `+ Märge`, `+ Kaasamine` and `+ Lõpeta teema` are not the
same kind of thing: the first two say *something happened, write it down*, and
the third says *this file is finished*. A row that mixes them is a row where the
most consequential control looks exactly like the most routine one.

### Decision

A section of its own, under the launcher and visually separated by a rule, with
the destructive control last and marked as destructive:

```
TEEMA TOIMINGUD     Lõpeta teema      Kustuta teema
```

It renders only for a writer. `Lõpeta teema` renders only while the Matter is
open; `Kustuta teema` renders on a closed Matter too, because a file closed by
mistake is one of the cases deletion exists for.

### Consequences

**The deletion itself is ADR 0096 §4 and is unchanged by this record.** The
confirmation page, the `deleted_at` tombstone, `Matter.all_objects`, the owned-
content purge, the evidence cleanup, the search removal, the `MATTER_DELETED`
event, the refusals — including the accepted limitation that a Matter with a
corrected `Entry` refuses deletion because `EntryRevision` is append-only — all
stand exactly as they were built and deployed. What moved is where the control
is drawn.

`Kustuta teema` is a link to that confirmation page rather than a panel with a
button in it. There is **no `confirm()` and no `hx-confirm`**: a browser dialog
is one keystroke from dismissed, says nothing about what is being deleted, and is
not something a server can require. The CSRF token on the confirmation form is.
