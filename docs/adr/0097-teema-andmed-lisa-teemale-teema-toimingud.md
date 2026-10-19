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
not one. The category is now «what happened on this file».

**The word goes from the screens a lawyer works on.** Not on the panel, not on
the chronology row — `DEVELOPMENT_HEADLINE` reads `Märge`, so a `Märge` that was
filed comes back a `Märge` — not in a refusal, not in the correction form's
accessible name, and not in the audit *summary*, which is the record's own title
and always was.

**It stays in one place, deliberately: the audit event's type label**
(`PROCEDURAL_DEVELOPMENT_RECORDED` → «Menetluse areng lisatud»). That log names
the *record* rather than the control, every historical event was written under
that name, and relabelling them would make the change log say a thing nobody
recorded. Nothing about the display name is stored, so the two can differ
without either becoming wrong.

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

**Narrowed on 2026-10-01 by [0126](0126-the-next-step-is-set-where-it-is-read-and-the-act-can-finish-it.md) §1:**
the chip stays out of the launcher. With no open step, `PRAEGUNE TEGEVUS` draws
`+ Määra järgmine tegevus` — `Muuda`'s own form in its other state — and a
ticked future `+ Märge` (0124) remains a second door onto the same service,
never a second step.

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

**`Lõpeta teema` is retired by docs/adr/0131 §11** (2026-10-02): a Matter ends when its `Hetkeseis` says so — «Jõustunud» or «Rohkem ei tegele» — and `close_matter` behind it is unchanged.

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

## Amendment 2026-10-15 — `+ Lisa · Muu` creates one `Entry(kind=OTHER)`

Owner's incremental UX round, stage 2. §6.3 said no launcher path creates a bare
`Entry`. **One does again, narrowly:** `+ Lisa · Muu`, the fifth choice after
`Arvamuse tähtaeg`, `Oluline tähtaeg`, `Jõustumine` and `Töövõit`, records
supporting material that fits no structured kind — a document met during the
process, background for the analysis, somebody else's paper, a page published
elsewhere — as `Entry(kind=EntryKind.OTHER)` (`workspace.add_matter_other`).
Only `OTHER`, and only from this form; `Tavaline` stays retired and no generic
task composer returns.

1. **Fields.** `Kirjeldus` is required and is the entry's body (`Entry` cannot be
   stored without one, and a link or file with no word on why it is on the file is
   what this form exists to avoid). `Millal?` is required (see 3). `Link` and
   `Failid` are optional. No title, organisation, status or follow-up.
2. **`Entry.url`, one additive column** (`matters/0049_entry_url`, blank,
   default empty in the database too — `db_default=""`, so the release still
   serving during `migrate` and a code-only rollback keep inserting entries;
   `EntryRevision.url` likewise). A link inside `body` would be
   opaque, uncorrectable as a link and invisible to the revision chain. It is
   validated by the shared web-address rule (`normalize_entry_url`: http(s), a
   real host, **no userinfo** — `https://user:pw@…`, `https://user@…` and
   `https://:pw@…` are refused, as `Ülevaade / uudis` and `Menetluse link`
   refuse them — and refused rather than truncated), on creation and on
   correction alike; shown by its host as the labelled new-window link other
   chronology rows use, and corrected through `edit_entry`, whose revision keeps
   the earlier address as it keeps the earlier wording. **Correcting a `Muu`
   link creates an `EntryRevision`, and a Teema holding a corrected entry cannot
   be permanently deleted (docs/adr/0165), so the link-correction form says so
   in one line** (owner decision D13, 2026-10-17). A correction overtaken
   by another writer's (409) shows that writer's description **and** link
   («Praegune link», the whole address as plain text) beside the person's own
   unsaved values, and merges nothing. Only
   a `Muu` entry's `Muuda` offers the link box; every other kind keeps the
   body-only correction. **A link is an `OTHER` entry's only**: `add_entry` and
   `edit_entry` refuse a non-empty `url` on any other kind
   (`ENTRY_URL_ONLY_FOR_OTHER`), and the `matters_entry_url_only_other`
   constraint (same migration) refuses it in the database; every existing row has
   an empty `url`, so it holds from the start. Search indexing is unchanged (the
   description is indexed as every entry body is); no rebuild.
   **Reversing `0049` is a data loss** (recorded 2026-10-18, JTEST-012): a
   backward migrate to `0048` drops both columns *with their values* — every
   `Muu` link and every earlier link a revision kept — while the entries
   themselves survive, and migrating forward again restores the columns empty.
   A rollback of this release is therefore code-only; the schema step back is
   not a rollback (`tests/test_entry_url_migration_reversal.py`;
   `deploy/unraid-main/RECOVERY.md` §"A backward migrate across `matters.0049`
   drops Muu links"; `docs/production-readiness.md` 5.2).
   During a code-only rollback to `9ecbfd21`, a correction of a linked `Muu`
   entry writes an `EntryRevision` with an empty `url` (the database default),
   because that release's `edit_entry` does not name the column; the entry keeps
   its link, and the revision wrongly says the earlier link was empty (JDATA-001,
   accepted and documented in `RECOVERY.md`).
3. **The date is the day the material or event occurred.** `Millal?` is a
   required single day with the existing date picker, **empty until the person
   fills it** — nothing is pre-filled and no day is invented. It is stored as the
   start of that local day (`start_of_local_day`) in `Entry.occurred_at`, which
   means when the work happened, not when it was typed; the chronology shows the
   row on that day and orders it by that day like every other entry. (Owner's
   review of #454: recording the save timestamp instead was rejected.) **The day
   may not be after today** (Tallinn time; «Toimumise kuupäev ei saa olla
   tulevikus.», on the form and in `add_matter_other` alike), because every entry
   is counted as work done on its day by `app.matters.activity`, so a day ahead
   moved «Viimane tegevus», «Muutusteta 30 p» and Osakond's weeks while the day
   cannot be corrected afterwards — a plan is `Järgmine tegevus` or `Oluline
   tähtaeg`, and like a sent `Koja arvamus` this is an exception to
   docs/adr/0121 §3 (JQA-001, owner decision 2026-10-17).
4. **Nothing follows from it.** No `NextAction`, no `Hetkeseis` or plan change, no
   wait, no `Submission`, no reporting definition touched. A closed Teema refuses
   it under the same lock as every other new record; readers and the gate visitor
   cannot reach the route. Files go through `capture_supporting_evidence` in the
   same transaction, so a refused file leaves no entry.
5. **Not in this stage:** adding files to an existing `Muu` entry later. `Entry`
   has no `+ Lisa fail` route today (the `Märge`, `Väline seisukoht` and
   `Kaasamine` have their own), and adding one is a separate decision.

## Amendment 2026-10-17 — `Kohtumine / arutelu` creates one `Entry(kind=MEETING)`

Owner's incremental UX round, stage 4. This is the **second narrow exception** to
§6.3's «no launcher path creates a bare `Entry`», after `+ Lisa · Muu` (amendment
of 2026-10-15): a meeting or discussion held about the Teema — with a ministry,
members, another organisation, internally — is recorded from the launcher as
`Entry(kind=EntryKind.MEETING)` («Kohtumine»), through
`workspace.add_matter_meeting`. Only `MEETING`, and only from this form; no
generic composer returns.

1. **The family is renamed.** `+ Arvamus / tagasiside` held only `Koja arvamus`
   once stage 3 moved every other position under `+ Kaasamine · Lisa tagasiside`
   (docs/adr/0170). It becomes **`+ Koja arvamus / kohtumine`**, with two
   sub-choices: `Koja arvamus` (unchanged; still the first and the default,
   because «Saada Koja arvamus» opens `arvamus-koja`, docs/adr/0168) and
   **`Kohtumine / arutelu`** (`arvamus-kohtumine`). The sub-choice label above
   the chips is `Mida lisad` rather than `Kelle arvamus`, which a meeting is not.
   Feedback does not come back into this family. Panel and family ids are
   unchanged (`lisa-arvamus`, `arvamuse-liik`), so nothing that opens
   `Koja arvamus` moves.
2. **Fields.** `Kuupäev` — required, one day, the existing date picker, **empty
   until the person fills it** (nothing pre-filled, nothing inferred), stored as
   `start_of_local_day(day)` in `Entry.occurred_at` exactly as `Muu` does, so the
   chronology shows and orders the row on the day the meeting took place. **The
   day may not be after today** («Kohtumise kuupäev ei saa olla tulevikus.», on
   the form and in `add_matter_meeting` alike), for the reason `Muu`'s may not
   (amendment of 2026-10-15, 3): a meeting not yet held is a plan, not work done.
   `Lühikokkuvõte` — required, the entry's body, through the same sanitising
   every entry body has (`sanitize_entry_html` in `add_entry`); a summary that is
   empty once sanitised is refused under the field («Kirjuta kohtumise
   lühikokkuvõte.»). `Failid` — optional, several, `capture_supporting_evidence`
   in the same transaction, so a refused file leaves no entry, document or
   evidence behind. No link, no title, no status, no follow-up.
3. **Participants are named in the summary — no organisation field.**
   `Entry.organisation` exists, `add_entry` accepts it, the chronology row shows
   its name and search indexes it, but **`Muuda` cannot correct it**:
   `edit_entry` and `EntryEditForm` change the body (and `Muu`'s link) only. A
   picker that writes a value nobody can fix afterwards is the uncorrectable
   field this ADR removed elsewhere (§2–§4), so the form has none. No
   participants model, no M2M, no migration. Offering one organisation would need
   an organisation correction on `edit_entry` (with its revision) first — a
   separate, owner-level decision.
4. **The meeting's date cannot be corrected — a limitation, owner decision
   open.** `edit_entry` is deliberately «a correction, not a new fact»:
   `occurred_at` is untouched and `EntryRevision` keeps no date. A meeting saved
   on the wrong day is today fixed by deleting the row (the ordinary `Kustuta` on
   a user-created chronology block, docs/adr/0102 §5) and saving it again. Making
   `occurred_at` correctable, with the earlier value kept in the revision chain,
   is an owner decision for `Muu` and `Kohtumine` together; nothing was invented
   for it here.
5. **Nothing follows from it.** It is **not** a `MatterEngagement` —
   `EngagementKind.MEETING` is a consultation round's kind and stays that — so it
   opens no round and no feedback wait and closes none. It creates and completes
   no `NextAction`, changes neither `Hetkeseis`, the plan, `Järgmiseks` nor the
   opinion deadline, is not a `Submission` and is not counted as a formal opinion
   in any report. Corrections go through the existing `Muuda` (body), with
   `EntryRevision` and the `ENTRY_EDITED` change event. A closed Teema refuses it
   under the same lock as every other new record (`business_write_required` on
   the route, `lock_open_matter_for_business_write` in the service); readers, the
   gate visitor and accounts without a business role cannot reach it. Its
   summary is searchable as every entry body is, under the Matter's visibility;
   its files sit in `Dokumendid` under the same rule.
6. **One shared helper.** `add_matter_other` and `add_matter_meeting` are thin
   wrappers over a private `_add_dated_entry_with_files` (lock, `add_entry` with
   the stated day, `capture_supporting_evidence`), so the two exceptions cannot
   drift apart; `Muu`'s public behaviour is unchanged. Search indexing is
   unchanged; no rebuild, no migration.

## Amendment 2026-10-17 — each `LISA TEEMALE` form saves once

Every compact `LISA TEEMALE` form — `Alusta kaasamist`, `Lisa tagasiside`,
`Oluline tähtaeg`, `Jõustumine`, `Töövõit`, `Muu`, `Kohtumine / arutelu` and,
as before, `Koja arvamus` — carries a save-once token: the same drawn form
records once, a refused save spends nothing, and a deliberate repeat is the form
opened again (owner decision D3; docs/adr/0167, amendment of 2026-10-17).

## Amendment, 2026-10-19 — see docs/adr/0172

The owner's acceptance fixes of 2026-10-19 amend this record: the amendment of 2026-10-15 — `Millal?` of `+ Lisa · Muu` starts at today's Tallinn day; a `Muu` sorts at the end of its day and its closed line carries its first words (0172 §4–§5).
