# 0096 — One Teema model: andmed, lisamine, toimingud

**Status:** accepted
**Date:** 2026-09-20

The Teema is presented as three things, and a lawyer should be able to say which
of the three they are doing without knowing anything about the application's
object model:

    TEEMA ANDMED      what this Matter is
    LISA TEEMALE      what happened, and what Koda did about it
    TEEMA TOIMINGUD   what happens to the Matter itself

Ten decisions follow from that, across two forms, one launcher and one new
operation. **No business data is rewritten by any of them.** Two additive
migrations; zero `RunPython`; zero `RunSQL`.

## Context

Three rounds of lawyer feedback had each improved one control, and the product
had drifted into asking one question two ways and twelve questions in one row.

`Lisa uus teema` and `Muuda teemat` describe the same record and had become two
designs: chips on one and a bare row on the other, a `Muu` reveal on one and an
always-open box on the other, four questions on the edit page that creation had
stopped asking, and one — `Menetluse link` — that creation asked and editing
could not correct.

`LISA TEEMALE` offered twelve chips, and every one of them named a *record type*
this application keeps. That is a truthful list and the wrong question: a lawyer
who has just come off the phone has something that happened in mind, not a
record type, and was being asked to classify it against the back of the
application before it would take anything.

And `Valdkonnad` and `Hetkeseis` had been moved behind a floating menu one round
earlier (ADR 0094 §2) to stop an opening fold re-laying out the form. That
solved the reported problem and introduced a worse one: the first thing a lawyer
saw of the two questions that classify a file was two words and a caret.

## Decision

### 1. `Lisa uus teema` is the master interaction

For every fact both pages ask about — `Pealkiri`, `Lühikokkuvõte`, `Saabumise
kuupäev`, `Arvamuse tähtaeg`, `Vastutaja`, `Saatja`, `Valdkonnad`, `Hetkeseis`,
`Õigusakt`, `Menetluse link` — creation defines the label, the component, the
vocabulary, the selection behaviour, the reveal and the refusal, and editing
follows it.

The two remain two *transactions* with two form classes and two services.
Creating a Matter and correcting one have different rules and §11 of the
previous round turns on their staying different. What is shared is how the
question is asked.

### 2. Shared, by sharing the partial

`Valdkonnad`, `Hetkeseis`, `Õigusakt` and `Menetluse link` are four
`{% include %}`s on both pages. Not four pairs of matching markup — a copy that
matches today is a copy that stops matching, and this round exists because four
of them had.

Two consequences worth stating. `MatterEditForm` gains
`policy_area_other_selected`, so `Muu` is a chip on both pages rather than a
chip on one and a permanently open box on the other; `edit_initial` ticks it
from the stored text, so a Matter filed under `Muu` still opens showing what it
holds. And `policy_area_chosen_count` / `stage_summary` move to a shared mixin
that reads `initial` as well as the POST — the create form's version answered
zero whenever unbound, which is right on a blank creation form and wrong on an
edit page, where unbound is exactly the case that holds five ticked areas.

### 3. `Valdkonnad` and `Hetkeseis` are drawn, not hidden behind a menu

This **reverses ADR 0094 §2**. The chips are on the page when the page arrives,
in ordinary flow, in the same interaction language `Õigusakt` has always used.

They stay inside a `<details>`, and it is `open` on the server. The fold was
never the defect — arriving shut was. Open, every choice is visible and
selectable without opening anything; collapsible, a lawyer who has answered
`Valdkonnad` once can have the row back.

ADR 0094's own complaint is answered by never being shut: nothing below moves
when a chip is pressed, because everything below is already drawn where it
stays. It moves once, if and when the *reader* collapses the section — a thing
they asked for rather than a thing that happened to them.

`bindChipMenus` is deleted outright, and that is independent of the markup.
Escape-to-close, click-outside-to-close and single-select-auto-close are all
wrong for an in-flow section, and the last one in particular re-created one row
lower the exact re-layout the overlay was built to prevent.

### 4. `Sildid` and `Nähtavus` leave the ordinary Teema interface

`Sildid` had already left the Teema rail — it printed «Silte ei ole.» on nearly
every Matter (ADR 0052 §14) — and `Muuda teemat` was the last control offering
the vocabulary.

`Nähtavus` is the one that matters, and the owner has now asked for it twice. A
three-word radio group behind a `⋯` glyph is a poor place to decide who may read
a file. Withdrawn from `Muuda teemat`, from the Teema header, and from
`update_field`'s `visibility` branch with them.

**Deleted, never hidden.** A field a form still declares is a field the form
still cleans and the view still hands to a service, so a crafted POST carrying
`visibility=RESTRICTED` would reach a working write path through a control
nobody is offered. Removing the declaration is what makes the view's `.get()`
return `None`; removing the `FIELD_SERVICES` entry is what makes `update_field`
answer 404. Both, or neither.

**Nothing about the mechanism moves.** `Matter.tags`, `Matter.visibility`,
`visibility_override`, `app.core.authorization`, restricted Matters, restricted
children, document visibility and every query filter behave exactly as before.
`Tag` and every imported assignment are untouched and nothing is backfilled.

#### 4.2 What this costs, stated rather than discovered

There is **no ordinary UI left that changes an existing Matter's visibility.**
Existing restricted Matters stay restricted and are still correctly filtered.
`Saabunud` still asks for visibility when a restricted letter is *first* filed
(`IncomingIntakeForm`), which is where the decision is actually made and is
outside this round's scope — so restricting a *new* Matter is still possible and
re-restricting an existing one is a host-shell operation until somebody designs
a deliberate surface for it. That is the trade the owner asked for; it is
recorded here so the next reader does not take the absence for an oversight.

### 5. `Menetlusliik` and the Matter-level `Adressaat` are not ordinary questions

`Uus teema` stopped asking both (ADR 0090 §4, §5), which left each of them on
exactly one of two pages that are supposed to be one job seen twice. Both are
now withdrawn from `Muuda teemat` and from the Teema rail, and `update_field`
no longer names `track` or `addressee_organisation`.

`Matter.track` and `Matter.addressee_organisation` keep every stored value. The
register refresh, the final cutover, the counterparty coverage report and the
`Seotud materjalid` cards all still read and write them.

The Submission recipient is a different fact and is untouched: `Koja arvamus`
proposes its recipients from the Matter's `Saatja` on an unbound form and they
are independent from that moment. Saving them changes no `Saatja`.

### 6. `Menetluse link` is Matter metadata

It says *where the proceeding this file is about is happening*. That is a fact
about the Matter, like its `Saatja` and its `Õigusakt` — not an event — and a
launcher of things-that-happened was the wrong list for it.

Asked on `Uus teema`, corrected on `Muuda teemat`, read on the rail. Two boxes
on both pages, `Link` and `Nimetus`, and **no kind**: every row either page
writes is filed under `ProceduralLinkCreateForm.STORED_KIND`, which is the
enum's own value for a link nobody has classified. Nothing is inferred from the
address — no hostname rule, no fetch, no guess — and no historical row is
rewritten. A correction through `Muuda teemat` keeps the kind the row already
carries, so an address fixed on a link somebody deliberately filed as `EIS` does
not silently reclassify it.

A Matter carrying several links keeps every one; the rail's card corrects each
in place, as it has since ADR 0089 §6. The card now also renders a quiet
`+ Lisa` line when there are none, because the chip that used to be the add
affordance is gone.

### 7. `Menetluse areng` is retired as a word, and its record survives

The lawyer never asked for a concept called `Menetluse areng`. The mental model
is *märge: something happened*. So `+ Menetluse areng` and `+ Märge` become one
control, and its default kind is `Tavaline`.

**The surviving record is the structured one, not the `Entry`.** This is the
decision most likely to look backwards, so the reasoning is here rather than
only in the code. `MatterProceduralDevelopment` exists because an `Entry` could
not hold three things (its own class docstring, ADR 0091 §5):

1. the date had to be allowed to be unknown — `Entry.occurred_at` is `NOT NULL`;
2. the lawyer's own note had to be a second field;
3. **a projection needs a title it did not have to parse.**

This round withdraws the questions behind (1) and (2) from the new-entry flow —
the date box defaults to today and is still clearable, and `Juristi märkus` is
gone. It does nothing to (3). `title` is «what happened», stated; `Entry.body`
is prose, and deriving «what happened» from its first sentence is the guessing
this repository refuses everywhere else.

So the toolbar loses a concept and the database keeps a record. The category
widens: «Rääkisin Justiitsministeeriumiga» is now filed as a
`MatterProceduralDevelopment`, which under the old reading — *one step the
external procedure took* — it is not. Nobody sees the word: it is not on the
panel, not on the chronology row, not in the audit summary.

**What retires with it:** there is no UI path left that creates a bare `Entry`
from the launcher. Entries are still written by `PRAEGUNE TEGEVUS` on every
completed step, still read, still corrected, and still carry their append-only
`EntryRevision` history. Nothing was migrated and no row moved between tables.

#### 7.1 The ordinary `Märge` asks six things and usually needs two

`Kuupäev` (today, clearable), `Mis juhtus?`, `Failid`, and optionally
`Uus hetkeseis`, `Järgmine tegevus` and `Millal?`. The stage and the next action
are written in the **same transaction** as the note, through the canonical
services — «Eelnõu saadeti Riigikokku» and `Hetkeseis → Riigikogus` are one act.

Nothing is inferred, ever. An empty stage moves nothing; no text is read and no
keyword matched; there is no model near this. An empty next action creates and
supersedes nothing.

#### 7.2 `Täpsus` and `Juristi märkus` are deleted from this form

Deleted, not hidden, on ADR 0095's rule: a crafted `areng_precision=QUARTER`
reaches a form that never cleaned it, and the service is called with `EXACT`.

The column still stores all four precisions, every historical row keeps what it
was filed under, and `ProceduralDevelopmentEditForm` still offers the whole
control — it decides per *record*, which is where a statement about how well a
date is known belongs.

### 8. `LISA TEEMALE` is four choices

    + Märge   + Kaasamine   + Arvamus / tagasiside   + Ülevaade / uudis

and the distinctions that remain are asked second, inside the one chosen:

    + Märge                  Tavaline · Oluline tähtaeg · Jõustumine · Töövõit
    + Arvamus / tagasiside    Meile saadetud · Teiste arvamus · Koja arvamus

#### 8.2 Grouped on the screen, distinct in the database

This is a presentation change and deliberately not a data change.
`+ Arvamus / tagasiside` writes three different records through three different
endpoints; `Oluline tähtaeg`, `Jõustumine` and `Töövõit` keep their own models,
services and reporting. One visible family, truthful backend types underneath —
a structured model is never replaced to shorten a row of chips.

A lawyer recording a win should not have to know that it becomes a special
reporting object. They should have to know that they are recording a win.

#### 8.3 `+ Järgmine tegevus` leaves the row

There is at most one open `NextAction` and there is now exactly one ordinary way
to set one: `Muuda` in `PRAEGUNE TEGEVUS` while a task is current, and the
optional `Järgmine tegevus` inside `+ Märge` otherwise — beside the thing that
prompted it. Two controls both offering to set "the next action" is how a lawyer
ends up believing they have two.

#### 8.4 Which sub-choice is checked is computed in Python

`open_choice` is global to the page. A template comparing against it directly
would leave the `+ Märge` group with *no* radio checked whenever a refusal came
from `+ Arvamus / tagasiside` — so opening `+ Märge` afterwards would show four
chips and no form. Nothing errors and nothing logs; the page is simply missing a
control. `_workspace_choices` gives each family one variable guaranteed to name
one of its own ids.

### 9. `TEEMA TOIMINGUD` is a section, not two more chips

`Lõpeta teema` and `Kustuta teema` affect the Matter rather than adding content
to it. `+ Märge` and `+ Lõpeta teema` are not the same kind of thing, and a chip
row that mixes them is a row where the most consequential control looks exactly
like the most routine one. Being *last* in that row was the right instinct and
the wrong fix.

The section renders for any writer; `Lõpeta teema` only while the Matter is
open, `Kustuta teema` on both — a Matter closed by mistake is one of the cases
deletion exists for.

### 10. `Kustuta teema` deletes the content and leaves an audit tombstone

Deletion is genuine: the Matter disappears from the register, `Minu asjad`, the
department surfaces and search, its detail URL answers 404, and its owned
business data is removed.

**The row itself stays, and that is architecture rather than a shortcut.**
`ChangeEvent.matter` is `PROTECT`; `ChangeEvent` is append-only through a
`BEFORE UPDATE OR DELETE` trigger on `audit_changeevent`; every Matter carries
change events from creation. Django's collector refuses the delete, deleting the
events first is refused by the trigger, and nulling the pointer is an `UPDATE`
the same trigger refuses. No order of operations removes the row while leaving
the audit guarantee standing.

Which is the right outcome. «This Matter existed, this person deleted it, on
this day» is precisely what a deleted record must still answer, and
`app/matters/purge.py` has refused to decide this inside a utility command since
it was written. It is decided here: **a minimal tombstone, kept only as audit
proof, which does not behave as a Matter.** `Matter.objects` excludes it, so
every read surface is empty of it by construction rather than by fifty callers
remembering; `Matter.all_objects` is the unfiltered manager and
`Meta.base_manager_name` names it, so audit rows still resolve their Matter.

The tombstone keeps its title. `MATTER_DELETED` carries counts and never
content, and the title is what makes the audit row answerable at all.

**What is never removed:** shared `Organisation`, `Tag` and `PolicyArea` rows —
ownership is followed through *reverse* relations only, so a row the Matter
merely points at is unreachable by construction.

**Four refusals, atomic and by name:** a document under a legal hold; a row
outside the owned set pointing into it; an owned row straddling the boundary; and
an owned row that is append-only and is not one of the two the tombstone keeps.
The fourth is the `EntryRevision` case — a corrected entry's previous wording is
evidence the database will not let anything remove — so **a Matter whose entries
have been corrected cannot be deleted**, and says so in those words rather than
failing halfway with an `IntegrityError`.

The plan is rebuilt under the Matter's row lock inside the transaction, so what
is checked is what is deleted. The audit event is written *before* anything is
removed, so a refusal rolls it back with everything else.

`GET` shows the confirmation and `POST` performs it — one address, two methods,
so following a link can never delete a Matter. The confirmation is a page, not a
panel: it names the Matter in full, says what goes and what stays, and offers
`Loobu` first. No `confirm()` and no `hx-confirm`; both are one keystroke from
dismissed and neither is something a server can require.

## Consequences

Two additive migrations: `matters.0032_matter_deletion_tombstone` (two nullable
columns and their indexes) and `audit.0025_matter_deleted_event` (one enum
value; Python metadata only). No data is read, written or rewritten by either.

Reversed: ADR 0094 §2 on the menu. Superseded in part: ADR 0090 §4 on where
`Menetlusliik` is answered (nowhere, now); ADR 0091 §5 on `Menetluse areng`
being a user-facing action (it is not); ADR 0074 §15 on `+ Menetluse link` being
the add affordance (the rail is). Every earlier ADR stands as written; none is
rewritten as though it had not happened.

Retired capabilities, each flagged rather than discovered later: no UI changes an
existing Matter's visibility (§4.2); no UI creates a bare `Entry` from the
launcher (§7); no UI creates a new `MatterProceduralDevelopment` at an
approximate precision (§7.2).

**Still open, and deliberately not decided here.** The three special `Märge`
kinds — `Oluline tähtaeg`, `Jõustumine` and `Töövõit` — keep their four-way
precision control. `+ Märge · Tavaline` is the only one this round made
exact-only.

The brief asked for no *unnecessary* precision selector in the ordinary entry
path, and the three are not equal on that test. `Oluline tähtaeg` and
`Jõustumine` record a date **somebody else announced** — a commencement given
in a draft as «2027», a milestone known only to a quarter — which is the case
ADR 0079 §1 built the control for and the one place it is not ceremony.
`Töövõit` is the weak one: a win is something this office achieved, and it
knows when. Left as it is because the round had already made the same change
twice and a third judgement call is better made by the owner than inferred;
making it is the same contained change and does not need an ADR.
