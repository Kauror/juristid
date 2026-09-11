# 0075 — The Teema current action and add-to-matter workspace

Status: accepted
Date: 2026-09-11
Supersedes the presentation clauses of [0074](0074-teema-approved-target.md) §3,
§4, §6, §7, §8, §9, §10 and §15 that describe the composer. Everything else in
0074 — the header, the rail, the process strip, the chronology, the canonical
records and their services — stands unchanged.

## 1. Context

The approved target (0074) gave the Teema page one open composer: two text
boxes, a row of quick dates with a file affordance, five progressive panels and
one `Salvesta`. Above it sat the `Järgmiseks` row with `✓ Tehtud` and `Muuda`.

It was built on a claim worth taking seriously — that a routine professional
update is *one act*, so one form and one save is the honest shape for it. In use
the claim does not hold, and it fails in two separate ways.

**The commonest thing anybody does here had no operation of its own.** A lawyer
finishes the task the page is telling them to finish. Under 0074 that was two
saves in two places: `✓ Tehtud` on the row, which wrote no description because
inventing one would put words in their mouth (ADR 0052 §7), and a note in the
composer, which completed nothing. Either order, with nothing tying them
together — so a Matter could carry an account of finishing something beside a
step that was still open, and a step could be marked done with no record of what
was done about it.

**One save could mean seven things.** Note, next step, deadline, commencement,
work victory, consultation, closure — and which of them it meant depended on
which boxes happened to carry a value. Technically permissive; for a reader of
the screen, unclear. The screen mixed three different jobs (*I had a task — what
did I do with it*, *something else happened*, *I want to add a structured fact*)
and asked the user to understand the data model before knowing which box to
fill. An invalid `Töövõit` could refuse a note somebody had also typed, and a
closure could be written by a save meant for something else.

## 2. Decision — two zones, and one intention per save

The upper workspace is two clearly separate zones.

**`PRAEGUNE TEGEVUS`** shows the open `NextAction` — its own text, its own date
at its own stored precision, its own lateness — and asks exactly one question:
`Mida tegid?`, with a file affordance and one `Salvesta`.

**`LISA TEEMALE`** is a choice of seven operations: `+ Märge`,
`+ Järgmine tegevus`, `+ Kaasamine`, `+ Oluline tähtaeg`, `+ Jõustumine`,
`+ Töövõit`, `+ Lõpeta teema`. It is a row of chips and nothing else until one
is chosen; then that one operation's small form opens, with its own fields, its
own validation, its own `Salvesta` and its own endpoint. Opening one closes
another. There is no global save left on this page.

`Ajajoon`, the process strip, the header and the rail are untouched.

## 3. Saving the result completes the current action

**One operation.** `Mida tegid?` → `Salvesta` writes the `Entry`, captures its
files and completes *that* `NextAction`, atomically. There is no `Märgi tehtuks`,
no `✓ Tehtud`, no second confirmation, and no order in which half of it can
happen.

`Mida tegid?` is **required**. A step marked done with nothing said about it
leaves the record saying only that somebody pressed a button, and an uploaded
file is supplementary evidence rather than a description — nothing fabricates a
body from a filename, from the action's own wording, or from the word "Tehtud".
A blank submit is refused beside the field and leaves the action OPEN.

This reverses ADR 0052 §7 only in *where* the description comes from, not in its
rule: the application still never writes a lawyer's record for them. What
changed is that it now asks.

The response re-renders the whole `#teema-vaade` column. ADR 0052 §8 kept
`✓ Tehtud` off that target so a half-typed composer underneath was not thrown
away; with one control there is nothing underneath left to discard, so the
problem is removed rather than worked around.

`matters:complete_action` and `next_action_row.html` are not deleted — see §11.

## 4. The action is named by the form and re-read under a lock

The completion form carries the exact `NextAction` it was rendered against, as a
hidden `action_id`.

A lawyer with one Matter open in two tabs finishes the task in one; the other tab
still shows the step that has since been replaced. Asking the service for
"whatever is open" would complete a *different* task — one the author never saw
— and file their description against it.

So the view fetches the action through `visible_to` (404 for one this reader may
not see, the rule `complete_action` already follows) and the service locks the
Matter row in the same order `set_next_action` and `close_matter` lock it,
re-reads the open action inside that lock, and **refuses outright** if it is not
the one named. No partial write, no redirection to the replacement, and the
refusal comes back with the fresh workspace state.

A double submit is the same case and gets the same answer: the second POST finds
the action COMPLETED rather than OPEN, and exactly one result survives.

## 5. After a completion, and when there is nothing to complete

A successful save leaves the Entry in the chronology, the action COMPLETED, and
**no new step opened**. The person may have finished what they needed to do;
naming the next one is a deliberate act through `+ Järgmine tegevus`.

A Matter with no open action renders one compact line — `Järgmine samm on
määramata`, or the register's own Excel instruction where it carries one — and
**no textarea**. A large empty `Mida tegid?` with no task above it is a form
asking about work nobody has named. *No current action* and *a blank completion
form waiting to be filled* are different states and read differently.

## 6. Files support an exact record

A `Document` has always belonged to a `Matter`, which is not enough once a person
can attach two PDFs to a work victory, a scan to a commencement and a member's
reply to a consultation in one afternoon. "Which of these eleven files is the
evidence for *that* Töövõit" has to be answerable, and a Matter-level document
answers it only with a filename and a timestamp — both guesses.

**`app.documents.links.DocumentLink`** is one additive table: a `document` FK and
five nullable typed FKs — `entry`, `engagement`, `important_date`,
`effective_date`, `work_victory` — with a `CHECK` that exactly one is set, and a
partial `UNIQUE` per target so one document supports one record once.

*Why not a generic target.* A `GenericForeignKey` has no referential integrity
at all: nothing stops a content-type/id pair naming a row that does not exist, a
row on another Matter, or a table that has been dropped, and every read costs a
query per kind. This codebase uses none.

*Why this shape.* It is the one `related_materials.MatterBackgroundMaterial` and
`RelatedSuggestionDismissal` already use for the same problem. Every link is a
real foreign key, the database refuses a row naming two records or none, and one
`select_related` reads every kind. The cost — a sixth linkable kind is a
migration — is the correct cost: what evidence may be attached to is a product
decision, not a shape a caller invents at run time.

*Why the Matter is not on the link row.* Both ends already carry it. A third copy
is a third thing that can disagree, and it would still let the database check
nothing: a `CHECK` sees one row and cannot follow a foreign key. So
`link_document_to_record` refuses a link whose two ends disagree, and
`manage.py check_evidence_integrity` reports any row that ever does
(`cross-matter-link`) — the division of labour the evidence layer already uses
for what PostgreSQL cannot see. The alternative that *would* put it in the
database is a composite foreign key on `(document_id, matter_id)`, needing a
redundant unique constraint on `(id, matter)` across six production tables and
raw SQL the ORM cannot see: disproportionate to one invariant written in one
place.

*Visibility.* A link carries none of its own and is readable exactly when **both**
its ends are — `DocumentLinkQuerySet.visible_to` conjoins the document's clause
with each target's. A restricted document on a normal note contributes no row,
and a normal document on a restricted fact contributes none either.

*Roles are not touched.* Every file captured here is `DocumentRole.OTHER`. The
button a file arrived through is not a business role; the link is what answers
that question.

## 7. Atomicity of fact and file

Every operation that takes files validates the form, validates **every** upload,
writes the canonical fact or Entry, captures the evidence and creates the exact
associations — in one transaction. If file 2 of 3 is rejected, no fact survives
claiming evidence and holding file 1. If the fact is refused, no `Document` is
created.

## 8. Each operation keeps its existing business model

Nothing about `MatterEngagement`, `MatterImportantDate`, `MatterEffectiveDate`,
`MatterWorkVictory`, `Entry`, `NextAction` or closure changed this round. Blank
`response_count` still means *nobody counted* rather than *nobody answered*; an
important date still creates no `NextAction`; a work victory still closes
nothing and completes nothing; closure still asks two questions and fabricates no
sent opinion, no victory and no final evidence, and still ends the open step
through `end_open_action_for_closure`.

The larger Kaasamine redesign («Alustasin arvamuste küsimist» / «Arvamused
saabusid») is a separate product round and is deliberately not begun here.

## 9. `Lõppsõna` no longer borrows the composer's body

The composer stored its own `body` as `disposition_reason` when `Lõppsõna` was
blank, because one save carried both. There is no shared body now, so a closure
with nothing to add stores an empty reason rather than a sentence written for a
different operation.

## 10. One open next action, one control for it

There is at most one open `NextAction`, so there is one control. While a step is
open, `+ Järgmine tegevus` is absent from the launcher and `Muuda` sits beside
the task in `PRAEGUNE TEGEVUS`, prefilled, posting to `matters:set_action` — the
existing service, which supersedes what it replaces. Once the step is finished
the chip appears. `Muuda` means *change what the task is or when it is due*; it
never means *record that I did it*.

The same form partial hosts both, rendered in whichever place the situation calls
for, rather than a button opening a panel elsewhere on the page — which would do
nothing with scripting off.

## 11. The composer is superseded, not deleted

`compose_update()`, `ComposerForm` and `matters:compose` still exist and still
work. They serve their tests and remain a compatibility surface; nothing on the
Teema page posts to them, and the new UI does not depend on one giant composer
transaction. `matters:complete_action`, `matters:defer_action`,
`matters:review_action` and `next_action_row.html` are likewise left available —
they are reachable, tested, and `matters:complete_work_item` on Minu asjad
already offers the same one-click completion from a work list, which is a
different surface with its own decision.

Only presentation was retired. No canonical rule, service or record moved.

## 12. The chronology gains file links and nothing else

Where the existing chronology already renders an `Entry`, a `Kaasamine`, an
`Oluline tähtaeg`, a `Jõustumine` or a `Töövõit`, the files explicitly associated
with that record now read under it in the existing compact document-link
language. A file gets **no timeline dot, no row of its own and no contribution to
the chronology count**.

This needs one suppression to be true: an evidence event belonging to an
operation with no `Entry` — a work victory with two PDFs — would otherwise be
grouped into a row of its own. `_versions_shown_on_their_record` drops those
events from the stream, and `_with_linked_files` renders them on the record's own
row. Entry attachments are untouched: they are already a clause on the note's
row and still are.

No other timeline change. The process strip, its geometry, its milestone policy,
`Hetkeseis`/`Idee`, the chronology ordering and the two-row-kind grammar are all
exactly as 0074 left them.

## 13. Consequences

* Seven endpoints where there was one, each in the business-write matrix.
* One additive migration, `documents/0017`-equivalent (`documents/0008`), one
  `CreateModel`, no backfill. Historic documents keep no association, which is an
  honest historical state; nothing infers one.
* `ComposerForm`'s field-preservation test now reads the union of two renders,
  because the page is deliberately never both "has a current action" and "has
  none" at once.
* A reader gets no write forms and a closed Matter gets no workspace; the
  chronology and the files stay readable according to authorization.
* Search, the index version, the projection, the intake reader and the
  extraction topology are untouched. A file added here is ordinary evidence.

## Amendment, 2026-09-11 — a closed Matter refuses the write, not just the form

**Status:** accepted

§13 above says *«a closed Matter gets no workspace»*, and it is true: a fresh GET
of a closed Matter renders no `PRAEGUNE TEGEVUS` and no `LISA TEEMALE`. Wide QA
showed what it does not say.

Two tabs, one Matter. Tab A closes it. Tab B is still holding the page from
before — every field, every button — and saves. The POST landed: new canonical
content on a file that was already shut, written through a form that was
perfectly legitimate at the moment it was rendered. Confirmed against the
compatibility composer route and against the related-materials writer (R2-02).

**A page is not a boundary.** The server has no memory of which page a POST came
from, so what a form did or did not render decides nothing about what the
application accepts. Not rendering the form on a closed Matter is the right
behaviour and it is a courtesy to the reader; the rule has to hold at the write.

### The rule

For normal business content: **a closed Matter accepts no new business write.**
Stated once, in `app.matters.locks.lock_open_matter_for_business_write`, beside
the lock order this repository already keeps.

It is a lock and then a read, never a read and then a write. Checking
`matter.is_open` on the instance a request arrived with answers a question about
a moment that has already passed: between that read and the write, another
transaction can commit the closure. So the Matter row is locked first and the
state is read *from the locked row*, exactly as `lock_matter_for_evidence_integrity`
requires of its own callers. `close_matter` takes the same row, and the two
strengths conflict — whichever transaction reaches it first wins, and both
orderings are correct. `tests/test_concurrency.py` runs the interleaving against
real PostgreSQL.

`FOR NO KEY UPDATE` rather than `FOR UPDATE`, at the Matter, for the reason
`complete_current_action` already took it that way: these transactions go on to
insert rows that *reference* the Matter, and such an insert takes `FOR KEY
SHARE`, which the stronger mode blocks.

### Where it is taken

| | |
| --- | --- |
| current-action completion | already had it; now through the shared helper |
| `+ Märge`, `+ Kaasamine`, `+ Oluline tähtaeg`, `+ Jõustumine`, `+ Töövõit` | added |
| `+ Järgmine tegevus` / `Muuda` | `set_next_action` already refused, unchanged |
| `matters:compose` — the compatibility composer | added |
| `intelligence:` add-date / add-commencement / add-victory | added, in the services the three routes share |
| overview `Kaasamine` | added, via `record_engagement` |
| related materials: link, unlink, add background, remove background | added |
| `+ Lõpeta teema` | **not** guarded: closing is the act, and `close_matter` answers a second attempt itself |

Two placements differ deliberately, and it is the same reason both times: the
leaf service has a legitimate writer that must *not* be held to this rule.
`add_entry` is how `register_incoming` writes a new Matter's first line, and
`add_engagement` is how `app.legacy_import.register_outreach` files
consultations onto imported Matters — most of which are closed, because the
register is full of finished work. So for those two the rule is stated where a
*person* writes: in the workspace operations, in `compose_update`, and in
`record_engagement`. The others have no such writer and take it in the service.

### What was deliberately not broadened

**Personal notes.** `Märkmed` and its autosave are one person's workspace state,
not canonical Matter content: no `ChangeEvent`, not on the chronology, not in the
record anybody else reads. Nothing in the existing product contract makes them
immutable on a closed Matter, and disabling them because of where they are drawn
would be a product decision this correction has no mandate for.

**Suggestion dismissals.** `Ei ole seotud` writes no `ChangeEvent` and asserts
nothing about the file; it stops a candidate being offered. It stays available.

**Editing and cancelling an existing fact** — `update_important_date`,
`cancel_effective_date`, `update_work_victory`, `confirm_work_victory` and their
siblings — is **not** guarded by this amendment. Those change a fact that is
already on the file rather than append new work to it, and two of them decide
the fate of a machine-proposed candidate, which is a review queue that closing a
file does not empty. Whether a closed Matter should accept a correction is a
real product question and it is left open here rather than answered by the shape
of a bug fix.

### Copy

`Minu asjad` and the work-row menus still offered `Märgi tehtuks`. Direct
completion is gone (§3), so the click navigated to `PRAEGUNE TEGEVUS`, where the
person must still describe the result and press `Salvesta` — a label promising
an act that the click does not perform. It is `Lisa tulemus…` now, with the same
ellipsis `Vaatasin üle…` beside it already uses for the same reason, and only
where the click *navigates*. `Muuda` is unchanged.

No migration. `documents/0008` is untouched, and this amendment adds none.
