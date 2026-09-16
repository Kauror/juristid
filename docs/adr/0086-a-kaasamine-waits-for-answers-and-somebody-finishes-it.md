# 0086 — A `Kaasamine` waits for answers, and somebody finishes it

**Status:** accepted
**Date:** 2026-09-16

*Numbered 0086 rather than 0085.* Both numbers were free on `main` at
`a4393b73`, and two branches off that base each took the next one — PR #226,
which renames `Kodulehe ülevaade` to a neutral publication activity, claimed
0085 first and cites it from its own migrations. The repository's rule is that
the record cited from a reviewed migration keeps the number and the other
renumbers, so this one moved. Nothing in it changed with the digits.

**Narrows ADR 0078 §3** and **ADR 0083 §1** on one point: a
`MatterEngagement.feedback_deadline` that is still open is work, and it draws a
row on the work surfaces. Everything else those two records decided — that the
column is not `Matter.response_deadline`, creates no `NextAction`, enters no
response-deadline statistic, no work-victory metric, no search row and no
archive projection, and that the process strip draws the dated point rather than
the act — stands unchanged.

**Narrows ADR 0082 §1** on one point: the four-way `Täpsus` control leaves the
two `Kaasamine` forms. The column, the stored values, `format_at_precision` and
every surface that renders an approximate engagement are unchanged, and no row
is rewritten.

**Narrows ADR 0074 §4 and the Teema redesign §14** on one point: `Liik` is no
longer asked on either `Kaasamine` surface. The column, the vocabulary and every
stored value stand.

## Context

`Kaasamine` records that Koda asked somebody a question. Since ADR 0078 §3 it
can also record the day the answers were asked for, and ADR 0083 §1 drew that
day on the Teema process strip. What the file could never record is the other
half of a consultation: **what came back**, and **that the round is over**.

Using it on real files produced three complaints, and they are one complaint.

**The round disappears the moment it is filed.** A lawyer opens a consultation
on 8 September asking members to answer by the 22nd, and then nothing on any
surface mentions it again. `PRAEGUNE TEGEVUS` says «Järgmine samm on määramata»,
Minu asjad shows nothing, Ülevaade counts nothing, and the deadline is drawn on
a strip below the fold. On the 22nd there is no reason the file would come back
to anybody's attention, and the department's actual practice — chase, collect,
write up — has no support anywhere in the product. ADR 0078 §3 put that beyond
reach deliberately: «no work item, no overdue badge, not even when the day has
passed».

**The answers have nowhere to go.** «Liikmed toetasid, v.a kaubandus» had to go
into a `Sissekanne`, which then said nothing about which round it belonged to,
or into `Märkus` on the engagement, which is a note *about* the round rather
than a record of what the people who were asked said back.

**The panel asks two questions nobody uses before the ones they do.** The first
control is a row of `Liik` chips — `Küsitlus` / `Koosolek` / `Kirjade voor` —
and the second is a four-way `Täpsus` selector. No surface filters on the kind,
no statistic counts it and no list groups by it; the precision exists for
historical rounds and is chosen on a form where the overwhelming case is «this
happened today».

## Decision

**`Kaasamine` is a consultation workflow with four steps: record who was
engaged, optionally say when answers are expected, carry that expectation as
real current work, and finish it in writing.**

### 1. The two selectors leave both `Kaasamine` surfaces

`Liik` and `Täpsus` are removed from `+ Kaasamine` and from `Muuda` on a filed
round. Neither column is dropped, neither vocabulary is narrowed, and no stored
row is rewritten.

**`Liik`.** Every engagement created through either surface stores
`EngagementKind.OTHER`, which is the column's own default and has always meant
«muu». The chronology prints a channel **only when the row carries one that is
not `OTHER`**, so a historical `Kaasamiskutse veebis` or `Kirjade voor` reads
exactly as it did and a new round states no channel rather than stating «Muu».
This is ADR 0054's reasoning about `NextAction.kind`, arriving at the second
classification nobody reads back: a decision with no consequence is a decision
not worth asking for.

The correction form loses `Liik` with the panel. A form that can write a value
the creating surface cannot is a record correctable into a shape it could never
have been created in — the rule `EngagementForm` already keeps, read the other
way round. The consequence is accepted: a historical kind is no longer editable
through the UI. It is a label on a column nothing reads, and the alternative is
an editor offering a vocabulary the product has stopped having.

**`Täpsus`.** `Kaasamise kuupäev` is an exact day on both forms, because a round
somebody is recording as it happens always has one. ADR 0082's finding — that a
consultation is routinely remembered as «oktoobris» — is not withdrawn, and this
is where the cost is paid: a *new* historical round can no longer be entered as
a period, only as a day or as «kuupäev teadmata».

**Existing approximate rows are preserved, and preserving them needed a rule.**
The stored anchor of *oktoober 2025* is `2025-10-01`, which ADR 0079 §2 forbids
any surface from printing as a day — so the correction form opens such a record
with the day box **empty**. An empty box therefore cannot mean «clear the date»
on those rows, or pressing `Salvesta` after fixing a typo in the audience would
destroy what somebody recorded. The rule is stated on the form:

| the row is | the day box opens | an empty box on save means |
| --- | --- | --- |
| dated to a day | holding that day | clear the date |
| undated | empty | still undated |
| dated to a period | empty, with the period named beside it | **leave the period alone** |

A `Kustuta salvestatud kuupäev` checkbox — rendered only for the third row —
keeps the column clearable for them. It is not a fifth precision chip: the
question it asks is «remove this», which is a different question from «at what
precision is this known».

### 2. The creation form, and what its two dates default to

`+ Kaasamine` asks `Keda kaasati` (required), `Kaasamise kuupäev`,
`Tagasisidet ootame kuni`, `Saadud tagasiside / arvamused`, and keeps
`Vastuseid`, the two provider links and the file control. All but the first are
optional.

**Both dates are pre-filled, visibly, and both clear.** `Kaasamise kuupäev` is
today, unchanged from ADR 0078 §2. `Tagasisidet ootame kuni` is **today + 7**,
and that reverses ADR 0078 §3's «optional and undefaulted». The argument there
was that «today is a plausible engagement date and never a plausible reply-by
date, so a pre-filled one would be answered by pressing `Salvesta`». That is an
argument against defaulting to *today*, not against defaulting: a week out is
what a consultation asks for when nobody says otherwise, and it is not a value
anybody presses past without reading.

Three quick spans sit beside the box — `1 nädal`, `2 nädalat`, `1 kuu` — and the
box itself takes any date. `1 kuu` is a **calendar** month with end-of-month
clamping (31 January + 1 month = 28 February), because that is what somebody
picking it means. Every span is resolved on the server in Europe/Tallinn and
each chip prints the day it landed on, exactly as `Järgmine tegevus`'s quick
dates already do.

**A default is an initial value and nothing else.** Nothing in any service
supplies a date the form did not send: an emptied `Kaasamise kuupäev` stores
`NULL`, an emptied `Tagasisidet ootame kuni` opens no wait, and a validation
error or an htmx swap comes back on a bound form holding exactly what was typed
or cleared. The previous defect — the *view* stamping `timezone.localdate()`
whatever the person had done — is what ADR 0078 §2 removed, and no part of this
record puts it back.

### 3. An open feedback wait is work

A `Kaasamine` on an open `FULL` Matter carrying a `feedback_deadline` and no
completion is an **open waiting activity**, and it draws exactly one
`WorkItem` — a fourth source in `app/matters/work_items.py`, beside the open
`NextAction`, the `Oluline tähtaeg` and the outstanding `Arvamuse tähtaeg`.

This is the narrowing of ADR 0078 §3, and the objection that record raised is
answered rather than dismissed. It said that a column generating a task «would
make every historical consultation somebody types in overdue on the day it is
entered». That is true of a naive reading and is not what this is: the source
starts from `open_matters`, which is open `FULL` records only, so the decade of
imported register consultations — `ARCHIVE` rows — reaches no work surface
however many reply-by dates they carry. What remains is a round somebody opened
on a live file this week, which is exactly the thing that was falling off the
page.

**What the wait is not** is the whole of the rest of ADR 0078 §3, and none of it
moves:

* it creates no `NextAction`, no `MatterImportantDate` and no `Submission`;
* it does not touch, shadow or substitute for `Matter.response_deadline`, and
  discharges no response obligation;
* it is deliberately **absent from `real_deadlines`**, so it enters no
  *Tähtajad* panel, no deadline-window population and no register deadline
  group. Those name what Koda promised, and «we asked our own members by the
  22nd» is not one of them;
* it enters no work-victory metric, no search projection and no archive
  projection;
* it creates no second row anywhere. Ending the wait is one column on the
  consultation, not a task object with a lifecycle of its own.

**It never overwrites an existing plan.** A file carrying an open `Järgmiseks`
*and* a waiting round shows both, on `PRAEGUNE TEGEVUS` and as two rows in a
work list. They are two true facts about one file, and a surface that showed one
of them would be choosing which half of somebody's day to withhold. Nothing
cancels, completes or supersedes anything.

### 4. Who sees it, and when it turns due

**The responsible person is the Matter's owner**, which is the reading an
`Oluline tähtaeg` and an `Arvamuse tähtaeg` already get. A consultation belongs
to whoever carries the file rather than to whoever typed it in, so a
reassignment moves the wait without anybody editing it.

**A Matter with no owner produces a wait with no responsible person.** It
reaches nobody's Minu asjad and appears on the department's *vastutajata*
surfaces — `Vajab sekkumist`, `?too_vastutaja=puudub` — which is where the
product already puts work nobody has been given. No duplicate is created for
every lawyer, and no fallback owner is invented.

**Before the deadline it is waiting; on the day and after it is due.** The
reading is inclusive of the day itself — «vastake 22. septembriks» is a thing to
look at *on* the 22nd — and what is late is **this office's unread post**, never
the people who were asked. Nothing anywhere states or counts whether a ministry
or the membership replied on time.

**The day passing closes nothing.** A wait ends when somebody says it has ended,
and for no other reason.

The Teema page states each open wait on `PRAEGUNE TEGEVUS` —
*Ootame tagasisidet kuni 30.9.2026* — with a link to the round's own chronology
row. It is scoped through the engagement's own `visible_to`, so a restricted
round puts no line on the page for a reader who may not open it (AUTH-003).

### 5. `Saadud tagasiside / arvamused`

One new `TextField`, on the creation panel, on the correction form and on the
completion form. It holds what the people who were asked said back, where there
is no separate file.

Separate from `Märkus`, deliberately: a note is what the person recording the
round wanted to say *about* it, and one column carrying both would be a column
whose meaning depends on who wrote the sentence.

**It is not indexed.** The search projection reads an engagement's title, its
note and its link hosts, and this round does not widen it — what a member wrote
to Koda is not a thing to make findable from the header search box without the
visibility question being asked first. No reindex follows this release.

### 6. `Lõpeta kaasamine`

One server-validated act ends a wait, and it is offered on the round's own
chronology row: a textarea pre-filled with whatever feedback is already
recorded, the ordinary file control, and a `Lõpeta kaasamine` button.

* **Nothing is required.** An empty box records that the round is over and
  nothing came back, which is a real and common outcome. A completion that
  demanded prose would make «keegi ei vastanud» the one result a lawyer could
  not file.
* **Files ride with the decision**, through `capture_supporting_evidence` and a
  `DocumentLink` to the engagement, in one transaction with the completion — so
  a refused upload unwinds it rather than leaving a round recorded as answered
  and the answer nowhere.
* **It is auditable as a decision.** `ENGAGEMENT_FEEDBACK_CLOSED` carries the
  actor, the timestamp, the deadline it was closed against, why it closed, and
  whether anything was written down. Not *what* was written: feedback runs to
  paragraphs and lives on the record where it can be corrected, and an audit
  table holding a second copy is a worse copy nobody maintains.
* **Two refusals, both read from the locked row**: a round with no
  `feedback_deadline` has no wait to finish, and a wait somebody already
  finished is not finished twice. A second press writes no second audit row and
  does not move the timestamp.
* **Closing removes it from every active surface** and leaves it in the
  chronology as completed history — *Tagasiside ootamine lõpetatud 24.9.2026* on
  the round's own row, with the feedback under it.
* **Correcting it afterwards** is `Muuda`, through `correct_engagement`, with
  the Matter's lock, the revision token and its own audit row. The existing
  correction conventions are not special-cased for a completed round.
* **Two columns, and they are one fact.** A closure timestamp requires the
  deadline that opened the wait
  (`matters_engagement_feedback_closure_needs_deadline`), so a correction that
  clears `Tagasisidet ootame kuni` clears the closure with it — the wait stops
  existing rather than becoming a completion of nothing. The inverse is not
  symmetrical: setting a deadline on a round that never had one opens a new
  wait, and cannot reopen a closed one.

### 7. A Matter closing ends its waits

`close_matter` ends every open feedback wait on the file, in the transaction
that shuts it and under its lock, each with its own audit event naming the
closure as the reason. It is the rule `end_open_action_for_closure` and
`cancel_planned_website_overviews_for_closure` already keep, arriving at the
third thing a closed file could otherwise keep owing — and here it is not
optional, because an open wait draws a work item while every route that could
finish one refuses a closed Matter, so the row would sit on somebody's desk
permanently unfinishable.

**Closure is never blocked by a wait**, and no feedback text is written for one:
a file being shut says nothing about what members answered.

**Reopening does not reopen them**, exactly as it does not revive a cancelled
`NextAction` or an abandoned website plan. A reopened file that is genuinely
still waiting gets a new deadline from somebody who has decided that it is,
which is a statement with a name on it.

Every interactive write on a `Kaasamine` — create, correct, complete — takes
`lock_open_matter_for_business_write` and refuses a closed Matter there rather
than by whether a page rendered a button, because the browser that posts may be
holding a page from before the closure.

### 8. The schema

One migration, `matters/0024_engagement_feedback_wait`: `feedback_received`
(`TextField`, blank), `feedback_closed_at` (nullable `DateTimeField`),
`feedback_closed_by` (nullable FK, `PROTECT`), and the `CHECK` above. One
migration, `audit/0020_engagement_feedback_closed_event`, which is an
`AlterField` over a `choices` list.

**No `RunPython`, no `RunSQL`, no backfill, no reindex and no archive rebuild.**
Every existing row reads back «no wait was ever closed», which is true of all of
them: nothing could close one until now. Deriving a closure from
`response_count`, from the deadline having passed or from the note would put a
decision on the file that nobody made.

## Alternatives considered

**Leave the column inert and add a `NextAction` when a deadline is set.**
Rejected, and it is the alternative most worth stating. It would put the wait on
every work surface for free — and it would also put a `Järgmiseks` on the file
that the lawyer did not write, that outranks the `Arvamuse tähtaeg` by ADR 0050,
that has to be cancelled if the deadline is corrected, and that can disagree
with the consultation it was derived from. Two records for one fact is what
ADR 0027 refused when it declined to write an `Entry` beside an engagement.

**Make the wait a `MatterImportantDate`.** Rejected for the same reason plus
one: an `Oluline tähtaeg` is a milestone *somebody else announced*, and a round
this office opened is not that.

**Let the wait close itself when the deadline passes.** Rejected. The day
arriving is when the work starts, not when it finishes, and a round that
disappeared on its own deadline would take the department's actual task — read
what came back, write it down — off the page at the exact moment it became due.

**Let the wait go overdue against the people who were asked.** Rejected, and
ADR 0083 §1 already rejected it. What is late here is Koda's reading of its own
post. Nothing states or counts a member's or a ministry's timeliness, and no
`real_deadlines` reading calls this a promise.

**Count the wait in `real_deadlines`, since it has a date and can be late.**
Rejected. That predicate is what *Tähtajad* and the register's deadline groups
read, and those name obligations the Chamber owes outside the building. A
consultation collection date counted there would make the department's own
question look like a promise to a ministry.

**Keep `Täpsus` on the correction form only, so historical rounds stay
enterable.** Rejected on the rule ADR 0082's own *Alternatives* section states:
an editor must not offer a precision the creating panel cannot write. ADR 0082
resolved that tension by widening both surfaces; this record resolves it by
narrowing both, and pays the cost in §1 rather than reintroducing a form that
can write what its sibling cannot.

**Make `feedback_received` searchable.** Rejected for now. It is plausibly
useful and it is member correspondence, and widening the corpus is a decision
about disclosure rather than about convenience (ADR 0038, the ADR 0056 archive
boundary). It can be added later; it cannot be un-indexed later.

**A separate `EngagementFeedback` child row, so one round can collect several
answers.** Rejected as more machinery than the fact deserves, on ADR 0027's own
reasoning about a five-field record. A round has one written summary and any
number of attached files, which is what a `DocumentLink` already gives it.

## Consequences

A department running consultations sees them. A file waiting on members appears
on the owner's Minu asjad, in `Üle tähtaja` once its day has gone, and on
`PRAEGUNE TEGEVUS` beside whatever else is planned. That is new work on those
surfaces — it is work that was always there and was not being shown.

`Üle tähtaja` and `Vajab sekkumist` can grow for a reason that is not a missed
deadline. The row states its own meaning — `OOTAME TAGASISIDET` — in the cell
that exists for exactly that, beside `PLAANIS`, `ARVAMUSE TÄHTAEG` and
`OLULINE TÄHTAEG`.

`work_items` runs four queries where it ran three, each already narrowed by
`visible_to`, and the new one is an indexed filter on a child table.

A historical `Kaasamine` kind is no longer editable through the UI, and a new
approximate engagement date is no longer enterable. Both are stated in §1 as
costs rather than as omissions.

## Reversibility

High for the product and additive in the schema. Removing the fourth work source
leaves every stored row readable and every column meaningful; restoring the two
selectors is re-attaching controls to columns that never went away. Dropping the
three new columns would lose what people had written down, which is the usual
cost of any additive column and the reason these are the smallest set that
answers the question.
