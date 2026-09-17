# 0091 — One lawyer workflow, from `Teema` to the next `Koja arvamus`

**Status:** accepted
**Date:** 2026-09-17

*Numbered 0091 rather than 0088.* `0088` was free on `main` when this work began
and four branches off that base each reached for the next number. PR #230 —
`Uus teema` is manual-first — merged its 0088 first; lawyer-package B took 0089 and
has since merged; and lawyer-package A (#235) claimed 0090 on the same day this
record did. Both of those cite their own number from their own migrations, so the
repository's usual tiebreak — the record cited from a reviewed migration keeps the
number — separates nothing, and merge order would have decided it by failing
whichever PR merged second. This record steps aside rather than leaving that to
be discovered during an integration. ADR 0086 moved for the same reason a round
earlier. Nothing in this record changed with the digits.

*Narrows ADR 0086 §2* on one point: `+ Kaasamine` no longer asks for
`Tagasisidet ootame kuni` at all, and opening a wait becomes one explicit act on
the round's own chronology row, `Ootan tagasisidet`. Everything else that record
decided — that a set deadline opens a wait, that the wait is one `WorkItem` on an
open `FULL` Matter, that `Lõpeta kaasamine` ends it, that it enters no
`real_deadlines`, no statistic and no search row, and that a Matter closing ends
its waits — stands unchanged, and every round already waiting goes on waiting.
`Muuda` and `EngagementForm` keep the box, because that is where a stored deadline
is read and repaired.

*Extends ADR 0084* with a provenance column, an optional author for aggregate
feedback, and the lawyer's own note beside the source. It reverses nothing in
that record: the source rule, the date semantics, the `Kaasamine` relation, the
visibility boundary, the audit families and the no-delete rule are all exactly as
0084 wrote them.

*Extends ADR 0061* with a second door onto `Submission` on the Teema page. That
record's decision — that a Matter's opinions are documents and `Dokumendid` is
where they are managed — is unchanged, and no `KodaOpinion` model exists.

*Extends ADR 0052 and ADR 0078 §2* rather than narrowing them. No date on any
new surface is supplied by the server: `Koostan arvamuse` has **no default at
all**, and `Kaasamise kuupäev`, `Menetluse areng`'s `Kuupäev` and
`Saatmise kuupäev` each open *visibly* holding today, in the box, readable,
changeable and clearable — which is the one shape ADR 0078 §2 allows a date
default to take. Proposing a likely day a person can overrule is not the same act
as inserting one behind them, and only the second is forbidden.

## Context

Lawyers used the product on real files for the first time. Five of the things
they reported are one thing: **the product held every fact the work produces and
did not hold the work.**

The five, in their own terms:

**9 — «creating a Matter should naturally establish `Koostan arvamuse`».** Every
incoming consultation begins identically: the lawyer files the Teema and will
write Koda's opinion by a day they already know. `Uus teema` had a
`Järgmine tegevus` panel, so this was *possible* — by typing the sentence
«Koostan arvamuse» into a free-text box. What they were describing is that they
were entering the same fact twice, in the product built to stop them entering
facts twice.

**11 — «simplify `Kaasamine`».** ADR 0086, three weeks old, had just made a
consultation round into a managed waiting workflow with a reply-by date defaulted
to a week out. The reasoning was sound and the *consequence* was not: recording
«19.09 — kaasati 234 tööstusettevõtet» is a completed act, and every such act now
opened a wait, drew a work item, appeared on somebody's Minu asjad and needed a
second deliberate act to get off the page. That is work the application was
assigning rather than work a lawyer had taken on.

**12 — «distinguish feedback sent to Koda from opinions found elsewhere».**
`Väline seisukoht` held both under one heading. «Metallitööstuse Liit answered our
consultation» and «the ministry published its position» are different
professional facts, and a colleague scanning six months could not tell them
apart.

**13 — «make Koda's own opinion an obvious first-class step».** It *is* first-class
in the schema — `Submission` has been canonical since the foundational round — and
it was invisible where the work happens. Recording one meant leaving the Teema for
the `Dokumendid` tab, opening a collapsed block, uploading a file, then finding it
again in a select and registering the send.

**14 — «after Koda sends an opinion, the Matter must continue naturally».** This
is the serious one. A file whose opinion had gone out and whose step was finished
read «Järgmine samm on määramata» and offered nothing that looked like a way on.
The procedure does not stop when Koda answers: the ministry revises the draft, the
government approves it, the file reaches the Riigikogu, the Chamber writes again.
Lawyers were opening **new Matters** for the same proceeding.

## 0 — The architectural decision, stated first

**A new model is justified where the business fact genuinely does not exist in the
current architecture, and nowhere else.** This round has exactly one such fact.

The temptation it presents is a workflow subsystem: a `WorkflowStep` table, a
state machine, an `EngagementFeedback` beside an `ExternalPosition` beside an
`AnotherOpinion` beside a `SurveyFeedback`. Every one of those would have been a
second opinion about facts the domain already holds, and the day two of them
disagreed there would be no way to tell which was meant. None of them is built.

What *is* built is one record — `MatterProceduralDevelopment` — and it is built
because the Package D discovery established that the fact it holds cannot be
projected truthfully from anything that existed. §5.1 is that argument in full,
including the round in which this record was an `Entry` and the three things that
reading could not hold.

**One new model is added by this round, and exactly one.** Everything else is
the domain objects that already existed:

| the lawyer's concept | what it is |
| --- | --- |
| `Koostan arvamuse` | a `NextAction`, from a date the person typed |
| `Kaasan` | `MatterEngagement`, with one question removed from its panel |
| `Meile saadetud tagasiside` | `MatterExternalPosition` + `provenance=RECEIVED` |
| `Teiste arvamus` | `MatterExternalPosition` + `provenance=DISCOVERED` |
| `Koja arvamus` | `Submission`, through the service `Dokumendid` posts to |
| `Menetluse areng` | **a new `MatterProceduralDevelopment`**, plus stage and step |

The additions are: three columns and an index on
`matters_matterexternalposition`, one widened `NOT NULL` there, one new table
(`matters_matterproceduraldevelopment`), a seventh typed column on
`documents_documentlink`, and two `choices` edits. Four migrations, all additive.
No `RunPython`, no backfill, no reindex.

**`Menetluse areng` is the one place a new record was warranted**, and §5.1 is the
argument: the Package D discovery established that an incoming procedural
development cannot be projected truthfully from any existing record, because its
date may honestly be unknown, its title must be readable without parsing prose,
and the lawyer's note must not become the ministry's own sentence. Every other
concept in the table below is a domain object this product already had.

No BPM engine, no state-machine framework, no Celery, no background job, no AI, no
automated communication, no CRM. Nothing in this round fetches, sends, generates,
summarises or monitors anything.

## 1 — `Koostan arvamuse` is created from a date, and from nothing else

### 1.1 What the page asks

One box on `Uus teema`, labelled `Koostan arvamuse`, holding a date. Saving the
Matter creates the `NextAction` whose text is
`app.workflow.services.OPINION_PREPARATION_TEXT` — «Koostan arvamuse», the
department's own words — with that date, `DO` / `DEADLINE` / `EXACT`, owned by the
`Vastutaja` chosen on the same form.

It is not a new kind of task and gets no special case anywhere. It appears on
`Minu asjad`, on `PRAEGUNE TEGEVUS`, in `Tähtajad`, in the register's
`JÄRGMISEKS` column and in the work queues exactly as any other open step, and it
is completed, replaced, deferred and superseded through the ordinary services.
`NextAction` already represents this business fact; a second «opinion task» model
would have represented it twice.

### 1.2 The date is the lawyer's, and the application invents none

**«Automatically creates the next action» is not «guess when it is due».** The box
carries no `initial` and the service refuses `None`. Not today, not seven days
out, not the official consultation deadline, not the end of the month, not the
Matter's creation date, and not `Matter.response_deadline` — which is a
*different* commitment, owed to whoever asked, and using it here would make a
lawyer's own working plan a copy of somebody else's timetable.

`Arvamuse tähtaeg` three rows up on the same form carries no default for exactly
this reason, and the reason has teeth since that field became work: a Matter
created and left alone would be due on its creation day and overdue the next
morning, on every deadline surface in the product (ADR 0052 §5, ADR 0078 §2).

**A blank box creates nothing at all** — not an undated step either. `ActionKind`
permits a dateless `WAIT` or `MONITOR`, and this is neither: the sentence is «I
will write the opinion», which is `DO`, and `workflow_deadline_requires_a_date`
refuses a dated-kind step with no date. Turning an unanswered field into an
open-ended commitment would be the application making a promise nobody made.

### 1.3 Atomicity, idempotency, and the one refusal

**One transaction.** The Matter, its files, its private note and this step are one
write. A Teema that saved while its first step did not would be a file the lawyer
believes has a plan and every work surface says has none; a step that saved while
the Teema did not would be an instruction attached to nothing. Either way nobody
is told, so neither is possible: a refusal anywhere rolls the whole thing back and
the entered date comes back in the box on the re-rendered form.

**Idempotent, twice over.** A double-pressed button, a resubmitted POST or a proxy
replaying a request must not leave two identical instructions on one file.
`workflow_one_open_action_per_matter` makes a second open step impossible in the
database whatever any caller does; and `establish_opinion_preparation_action`
checks for an **equivalent** open step under the Matter's lock before writing, so
a retry does not even supersede-and-replace the first one. Equivalent means the
same text, the same day and the same precision — a lawyer who deliberately moves
the date is changing the plan and gets a real replacement with its own audit row,
which is a different act.

**Both first-step boxes cannot be answered at once.** `Järgmiseks` and
`Koostan arvamuse` both want the Matter's single open step. Silently dropping
either would leave somebody who answered both with one of their two facts missing
and nothing said about it, so the save is refused, in Estonian, naming the choice,
with everything typed still on the page. It is a rare collision — a person who has
a preparation date does not usually also write a free-text first step — which is
why it is a refusal rather than a redesign of the page.

### 1.4 Why it is a separate form and a separate partial

`MatterCreateForm` and `matter_create.html` are being rewritten in parallel by the
classification work. A field declared on that class, and a label, a hint, an error
block and a date control interleaved into that template, would be a merge conflict
in the two files most likely to move, over a question neither of them is about.

So: `InitialOpinionActionForm` with its own `arvamus` prefix, its own partial, and
**one `{% include %}`** in the creation page. The integration hunk is one line.
`_create_context` takes the form as an optional third positional argument and
defaults it, so nothing else that calls that helper had to change.

## 2 — A new `Kaasamine` does not ask about a wait at all

**`+ Kaasamine` loses `Tagasisidet ootame kuni` — the field, its label, its three
quick spans and its error line. Opening a wait becomes one named act of its own,
`Ootan tagasisidet`, on the round's own chronology row.**

ADR 0086 §2's argument was about *defaults*: today is never a plausible reply-by
date, a week out is what a round asks for when nobody says otherwise, and today + 7
is not a value anybody presses `Salvesta` past without reading. Every clause of
that is still true. What it did not weigh is that pressing past it is not
symmetrical with pressing past an engagement date: an accepted `Kaasamise kuupäev`
is a fact that is probably right, and an accepted `Tagasisidet ootame kuni` is a
managed activity with a work item, a responsible person, an overdue state and a
second required act to end it.

**Emptying the default was this round's first answer, and it was not enough.** It
stopped the application assigning work nobody had asked for, which was the sharp
end of the complaint. It did not stop the *question* — an empty box is still a box
that has to be read, understood and skipped, every time somebody files a
consultation — and it left the capture panel as the thing that opens a managed
wait, so the two acts were still one press. The department's complaint was about
the panel being complicated, and a panel with one fewer default is still the same
number of questions.

So the question moved rather than being softened. Recording «19.09 — kaasati 234
tööstusettevõtet» is a *completed act*: what happened, on what day, to whom.
Deciding that this file is now **waiting** on an answer is a second decision, taken
by a person, and it is the only one that puts a row on somebody's desk. Two
decisions, two acts, and the second one is where the question belongs.

**`Ootan tagasisidet` is the explicit opt-in.** It is a closed disclosure on the
round's chronology row, beside `Lõpeta kaasamine`, and it asks exactly one
question. The date is **required** there, unlike the field it replaces: the form
exists only to open a wait, so an empty day would be a press that does nothing, and
somebody who means «no wait» simply leaves the disclosure shut. It is offered only
on a round that is not already waiting and is not finished — moving a deadline
somebody else set is a *correction*, and corrections live on `Muuda`.

**Nothing about the wait itself moves.** The column stays. The three quick spans
travel with the question they answer, so `1 nädal` is still one click and asking for
a wait still costs almost nothing. A set deadline still opens a wait, still draws
exactly one `WorkItem` on an open `FULL` Matter, still reads on `PRAEGUNE TEGEVUS`,
still goes due on its own day, and is still ended by `Lõpeta kaasamine` with its own
audit event. `feedback_received`, `feedback_closed_at`, `feedback_closed_by` and
`matters_engagement_feedback_closure_needs_deadline` are untouched. `close_matter`
still ends open waits. The date-order rule — a reply-by day may not fall before the
engagement date — is unchanged and is now kept in two places for two kinds of
caller: `refuse_deadline_before_engagement` for the bound forms that still write the
column, and the same one string inside `open_engagement_feedback_wait` for the act,
which has no form to report on.

**No generic mechanism was introduced.** `open_engagement_feedback_wait` writes one
nullable column on one record, takes the Matter's row lock through
`lock_open_matter_for_business_write`, refuses a stale revision and a round that is
already waiting, and records `ENGAGEMENT_CHANGED` naming the column that moved. It
is one service function for one business fact — not a state machine, not a wait
registry, and not a step anything else can be plugged into.

**Historical #227 rounds are untouched and this is not negotiable.** There is no
migration, no `RunPython` and no backfill: a round recorded with a deadline keeps
it, a wait that is open stays open and stays completable, an overdue one stays
overdue, and the visibility scoping is unchanged. An unrelated correction to such a
round — fixing a typo in `Keda kaasati` — does not touch the deadline, because
`update_engagement`'s `_UNSET` sentinel already means «not mentioned» and the
**correction form still renders the box holding what is stored**. `Muuda` keeps
`Tagasisidet ootame kuni` deliberately: it is the surface where a historical row is
read and repaired, and taking the box off it would make the stored value
uneditable. `EngagementForm` — the older non-panel route — keeps it for the same
reason. What lost the field is the one surface whose job is capture.

### 2.1 Is the engagement lifecycle still needed?

For a **new** simple round, no, and that is the point: recording that Koda asked
somebody something is complete once saved, the answers are added afterwards as
separate substantive records (§3), and there is no artificial «close the
Kaasamine» step in the ordinary flow because there is nothing open to close.

For a round somebody **deliberately** opens a wait on — and for every historical
one — yes, unchanged. The closure functionality is preserved in full. Retiring it
because new entries stop depending on it would destroy the #227 compatibility this
round is required to keep, and it would take away a capability lawyers do use when
they genuinely are waiting on members.

## 3 — Two feedback sources, one record, explicit provenance

### 3.1 Why one model and not four

`MatterExternalPosition` already holds: an author from the shared catalogue, a
source minimum of three kinds, an optional date at four precisions with an
anchor-and-precision pair, an optional `Kaasamine` relation, evidence through
`DocumentLink` with a real `DocumentRole`, visibility inheritance, four audit
event types, optimistic concurrency, no-delete, and a chronology projection.

`Meile saadetud tagasiside` needs every one of those and nothing else. Four
competing models — `EngagementFeedback`, `ExternalPosition`, `AnotherOpinion`,
`SurveyFeedback` — would have been four implementations of one set of rules, and
the validation that matters is precisely the part that would have been copied.

So the distinction is a column and the panels are two doors onto one operation.
Where it *would* have made validation ambiguous — the authorship rule, which
genuinely differs — the difference is stated once, in
`_external_position_authorship`, and enforced as two `CHECK` constraints because
all three columns it reads are on one row.

### 3.2 The vocabulary

`ExternalPositionProvenance`: `RECEIVED` («Meile saadetud tagasiside»),
`DISCOVERED` («Teiste arvamus»), `LEGACY` («Täpsustamata»). Stable machine keys,
Estonian labels, the repository's ordinary `TextChoices` shape.

**It is structured data and never an inference.** Not the presence of a `Kaasamine`
link, not whether an organisation was named, not whether a URL was supplied, not
whether a file was uploaded. Every one of those combinations occurs under both
values: a ministry *does* answer consultations Koda ran, and a member company's
position paper *does* get found on its own website. Each inference is therefore
wrong for some real record.

### 3.3 Aggregate feedback, and the one place the authorship rule bends

A survey of 234 industrial companies producing 58 answers has **no single
author**. The two things the old `NOT NULL` forced were an invented organisation
called «234 ettevõtet» and one arbitrary respondent standing for the rest, and both
put a fact on a professional file that nobody stated.

So:

* `organisation` becomes nullable;
* `source_label` («Allikas») is a short name for a collection of answers —
  «Tööstusettevõtete küsitlus»;
* `matters_external_position_author_or_label` refuses a row with neither;
* `matters_external_position_label_is_received` keeps the label to `RECEIVED` rows.

**Organisation provenance is not weakened for ordinary named positions.** A
`DISCOVERED` row still requires the catalogue's own organisation and may not name
one through a free text box, because that would be a ninth way of naming an
institution beside the one ADR 0073 built. The `Allikas` box is not rendered on
that panel and the field is not even present on that form, so a POST carrying it
is a value that did not come off a page — and the service refuses it again.

### 3.4 `LEGACY`, and the backfill that was not written

Every row written before this round reads `LEGACY`, which is true of all of them:
they were recorded through one panel that never asked. **Nothing is backfilled.**
The two readings a migration could have made — a linked `Kaasamine` means
received, or a member organisation means received — are exactly the inferences
§3.2 refuses.

A `LEGACY` row **reads as it always did**: the chronology prints
«Väline seisukoht: …», not «Täpsustamata: …», because nothing about those records
changed and a file that started announcing a gap in itself would be asking somebody
to close one that cannot honestly be closed.

`LEGACY` is refused as an *answer*. Neither panel offers it and
`_external_position_authorship` raises on it, so no new record can be filed as
unspecified. A correction to a `LEGACY` row passes `provenance=None`, which means
«this form did not ask», and the row keeps what it has.

### 3.5 Two chips, one panel, one operation

`+ Meile saadetud tagasiside` and `+ Teiste arvamus` are two `ReceivedFeedbackForm`
/ `OtherOpinionForm` subclasses of one form, rendered through one parameterised
partial, posting to two routes that both call `_record_external_position`, which
calls one `workspace.add_matter_external_position`.

**The provenance is a class attribute, not a field.** Neither form declares one,
so there is nothing for a browser to post and nothing a crafted POST can move: `+
Teiste arvamus` cannot be made to file received feedback by adding a parameter. It
is ADR 0052 §1's reasoning about `NextAction.kind`, applied to a distinction the
page genuinely does decide.

**The correction form does not move `provenance`.** How the file learned something
is what the panel it was recorded through said, and a correction that could change
it would let one press turn feedback a member sent us into an opinion we found
somewhere. A mis-filing is corrected by recording it again under the right chip;
there is no delete, so both rows stay, which is the honest history of one. This is
`EngagementForm`'s rule read the other way round: an editor must not write a shape
its creating surface cannot.

`add_external_position` keeps its route name. The browser lane and the visual
baselines reach it by that name, and renaming a working endpoint because a chip's
label changed would be churn with a migration attached.

### 3.6 The `Kaasamine` relation stays optional and is never inferred

Unchanged from ADR 0084 §4, and restated because this round makes it more
tempting: received feedback frequently *does* follow a round Koda ran, and
`engagement` says so where a person says so. It is never derived from dates or
organisations happening to match, an aggregate summary may cover several outreach
actions, `Teiste arvamus` usually has no round behind it, and older e-mail feedback
has none recorded. Both ends must belong to one Matter, refused in the service and
narrowed in the field's queryset by the reader's own visibility.

## 4 — The lawyer's note is not the source's words

`lawyer_note` («Juristi märkus»), a separate optional bounded column.

The failure it fixes: «MKM toetab varianti B» and «nende põhjendus ei arvesta
liikmete kulumõjuga» had two homes before it — appended to `summary`, where the
file recorded the *ministry* as having said the second sentence, or a separate
`Märge` that then said nothing about which position it was about. The first is
serious: a professional record that attributes this office's criticism to the body
being criticised is a record that lies.

The separation holds at every layer:

* **stored** as its own column, never concatenated;
* **rendered** on its own line, under its own label, with its own left rule and
  indent, from `ChronologyMilestone.own_note` — a field of its own rather than
  another clause appended to `sub`. The label travels *on the milestone* rather
  than being looked up by each template, because two surfaces render this row and a
  third would otherwise render the paragraph unlabelled, which is the attribution
  defect with better line spacing;
* **audited** as `has_lawyer_note: true` and never as text. A payload carrying this
  office's comment beside the organisation's identifier is the one place a reader
  could take them for one statement;
* **not indexed** (§9), so no search result can show Koda's words as the source's;
* **not a source.** `_external_position_source` does not count it and may not: a
  record whose only content is this office's opinion of something nobody can read
  is a record of nothing.

## 5 — `Menetluse areng` is a canonical fact of its own

### 5.1 The model this round first refused, and why it was wrong to

**This record was an `Entry` of a new `EntryKind` for one round.** The reasoning
was that a procedural development is a dated, attributable sentence about
something that happened, which is exactly what the authored chronology is:
`occurred_at` already means «when the work happened, not when it was typed up»,
`body` already holds the account, `DocumentLink` already carries the files, and
`EntryKind` already distinguishes kinds of chronology. What was missing, on that
reading, was not a table — it was a *panel* that asked for the date and could set
the stage and the step in the same breath.

That reasoning is right about the **shape** and wrong about the **fact**, and the
Package D discovery is what proved it: an incoming development such as
«Ministeerium saatis eelnõu uue versiooni» **cannot be projected truthfully** from
the existing records. Three things the fact needs, an `Entry` cannot hold:

* **The date has to be allowed to be unknown.** `Entry.occurred_at` is `NOT NULL`
  and has been since the foundational schema; every chronology reader, the
  `-occurred_at` ordering and the timeline's pagination depend on it. A
  development learned about from a third party months later frequently has no day
  anybody could defend, and the two answers an `Entry` left were an invented day
  or no record at all. §5.2 of the previous draft of this record called that a
  stated cost. It is not a cost that may be paid: inventing a date for somebody
  else's proceeding is precisely what docs/adr/0078 §2 and docs/adr/0079 exist to
  refuse.
* **The lawyer's own note is a second field.** An `Entry` has one `body`, so
  «Ministeerium saatis uue versiooni» and «see ei arvesta meie ettepanekut» had to
  become one sentence — the same conflation §4 refuses for a `Väline seisukoht`,
  arriving on the other record and misattributing this office's judgement to the
  ministry.
* **A projection needs a title it did not have to parse.** Package D reads these
  into one substantive history, and an `Entry` offers prose. Deriving «what
  happened» from the first sentence of a `body` is the guessing this repository
  refuses everywhere else.

So `MatterProceduralDevelopment`, beside `MatterEngagement`,
`MatterWebsiteOverview` and `MatterExternalPosition` in `app.matters`, with its
own service functions and its own audit events. **No new framework.** It is a
Matter child record written through named use cases exactly like every other
structured fact on this page; the launcher, the lock discipline, the visibility
inheritance, the evidence pipeline, the audit model and the chronology projection
are all the existing ones.

### 5.2 What it holds, and what it deliberately does not

Four columns and no more:

| column | |
| --- | --- |
| `title` | **required** — the step, in one line a projection can read |
| `occurred_on` + `occurred_on_precision` | **optional**, at the precision it is known to |
| `note` | **optional** — `Juristi märkus`, never the event itself |

`title` is bounded at 500, like `MatterEngagement.title`, because it is the line
a reader scans a year of a proceeding by. Detail goes in the note and the paper
goes in the attachments; a box that invited paragraphs would make this record a
worse copy of the document beside it.

`occurred_on` carries the four precisions of docs/adr/0079 through the same
composer every other period on this product goes through, an emptied box stores
`NULL` and reads «Kuupäev teadmata», and the database refuses a precision on a row
with no date. Nothing derives it: not `created_at`, not the day somebody typed it
in, not the stage change saved beside it.

**Not a `MatterImportantDate`**, which is a milestone somebody *announced* for a
date still ahead — the opposite tense. **Not a `Submission`**, which is what Koda
sent. **Not a `NextAction`**: a development is something that has already
happened, and a record that generated work would make every Matter carrying one
read as owing something (docs/adr/0078 §3, docs/adr/0084 §1).

**The stage it moved the file to is not copied onto it.** `Matter.stage` is where
the file stands and `MATTER_STAGE_CHANGED` is the history of it moving; a copy
here would be a second place for the same fact and therefore a second thing that
can disagree. What ties the two together is the operation identifier both writes
share (`app.audit.operations`) — which is also what lets Package D render «the
ministry sent a new draft, and the file moved to Kooskõlastusringil» as one act
without either record holding the other.

`DocumentLink` gains a **seventh** typed target column. That is the documented
cost of typed columns over a generic target, paid once more and for the same
reason docs/adr/0084 §3 paid it the sixth time: a development routinely arrives
*with* the paper, and the file has to be able to say which bytes are the evidence
for which step.

### 5.3 The stage and the step are offered, never derived

**Nothing reads the title.** No stage is inferred from «Eelnõu jõudis
Riigikokku», no next action is generated, no vocabulary is matched. A person
chooses, or nobody does, and a save naming neither changes neither.

The stage goes through `change_stage`, the canonical service, over
`active_stages()` — so a revision of the stage vocabulary arrives here without
this form knowing about it, and **no stage key is hard-coded in this round**. The
step goes through `set_next_action_for_new_work`, the native boundary, so the
departed-owner rule applies exactly as on `+ Järgmine tegevus`, and it supersedes
whatever was open, which is `NextAction`'s own invariant.

`Järgmiseks` and `Millal?` are both-or-neither, refused on the empty half, in
ADR 0052 §5's own words.

### 5.4 Atomicity

Up to four canonical writes in one transaction: the record, its evidence, the
stage, the step. A validation failure anywhere leaves the Matter exactly as it was
— no stage changed with the development absent (a file claiming to be in the
Riigikogu with nothing saying how it got there), no duplicated next action, no
orphan evidence. Ordered as the composer orders its own: record, evidence, stage,
step.

### 5.5 The continuation after a `Submission`

On a Matter that has a sent opinion this reader may see, and no open step,
`PRAEGUNE TEGEVUS` prints one sentence:

> Koja arvamus on saadetud. Menetlus võib jätkuda — *lisa menetluse areng* või
> *järgmine tegevus*.

Two anchors to controls that are already on the page. **Not** a wizard, not a
dashboard, not a suggested step, and not a created one: the file says the work may
continue and *what* continues it is the lawyer's to say.

It invents no work. A sent opinion with no open step is a perfectly ordinary state
— Koda answered and the file is waiting on somebody else — so the sentence says
«võib jätkuda» rather than naming anything outstanding, enters no work queue, and
adds nothing to any count, badge or deadline surface.

`opinion_sent` is read off the process strip the page has already built, which is
`visible_to`-scoped there, so a `Submission` restricted below the Matter draws no
column and puts no sentence on the page either.

### 5.6 The Package B seam, and the inference it forbids

Package B introduces `MatterProceduralLink` — **where a proceeding lives**: the
EIS page, the Riigikogu file, the ministry's own register entry.

**A link is a reference and a development is an event, and neither is derived from
the other.** Nothing in this round reads, writes or infers a link, and nothing may
later infer a development from one: that a proceeding has a Riigikogu page says
nothing about *when* it reached the Riigikogu, who noticed, or what this office
made of it. A file that manufactured the second fact from the first would be
inventing a dated event nobody recorded — the same class of invention §3.4 refuses
for provenance and §1.2 refuses for a date.

**The association is a pointer somebody would set, and this round deliberately
does not add it.** Package B merged as #233 while this branch was in flight, so
`MatterProceduralLink` is on `main` now and this record *could* point at it. It
does not, and that is a decision rather than a sequencing accident: **this branch
is the second of the two, and being second is not a reason to add a column.**

Nothing in this round needs the relation. No form asks for it, no projection reads
it, no refusal depends on it, and no lawyer has asked to cite the proceeding a
development happened in. A nullable foreign key that every write leaves `NULL` is
not a seam — it is a column with a constraint, a migration, a queryset join
everybody has to reason about, and a name that invites exactly the inference the
paragraph above forbids. The schema is cheaper to extend later than to explain now.

If the department does later want a development to name the proceeding it happened
in, the seam is **one nullable foreign key on `MatterProceduralDevelopment`**, set
by a person and never inferred. Nothing in either schema has to change to accept
it, and nothing in either package has to be revisited to add it.

### 5.7 Correction, and no deletion

Create and correct, like `MatterEngagement` and `MatterExternalPosition`. There is
no delete route, no service and no soft-delete state: a mistaken row is corrected,
because what the file recorded and who recorded it is part of the file.

Corrections are refused on a closed Matter, take the Matter's row lock, observe
the project's optimistic concurrency, and write nothing at all on a stale token —
not half the record and not the audit row. The files a development carries are not
re-posted by a correction and cannot be detached by one: adding evidence is a
different act with a different audit trail (docs/adr/0084 §8).

## 6 — `Koja arvamus` reuses `Submission`

### 6.1 A second door, not a second record

`+ Koja arvamus` posts to `workspace.add_matter_koda_opinion`, which composes
`register_sent_opinion_on_open_matter` — the service the `Dokumendid` panel already
posts to. `create_submission` still validates the kind and writes the creation
event, `select_final_evidence` still takes both locks and runs
`check_evidence_is_usable`, `mark_submission_sent` still re-runs the evidence check
and writes the send event.

There is no `KodaOpinion` model, no second statistic, no second withdrawal path and
no second definition of «sent». `Dokumendid` keeps everything it has — drafts,
`+ Uus arvamus`, `Võta tagasi`, channel, reference, the archive links — and remains
where an opinion is *managed*. ADR 0061 is extended, not reversed.

**Four questions:** the file, the day, the addressees, and optionally a title. The
title falls back to the filename, because «Koja arvamus pakendiseaduse eelnõule» is
worth typing and «arvamus_final_v3.docx» is not worth retyping. Nothing is read out
of the file's contents.

**No `Liik` and no `Kanal` on this panel.** `SubmissionKind` keeps every value and
`Dokumendid` keeps offering them; what this writes is `FORMAL_OPINION`, which is
what «Koja arvamus» means. Two optional bookkeeping boxes in front of four required
answers is the friction this panel exists to remove.

**Keywords are not asked.** `taxonomy.Tag` and `TagAssignment` classify a *Matter*,
not a submission; there is no per-opinion tag mechanism, and inventing one here
would be a taxonomy decision disguised as a form field. The short summary the brief
mentions is `title`, which the record has.

### 6.2 A file is not a send

`DocumentRole.KODA_SUBMISSION_FINAL` says Koda holds these bytes as an opinion. The
`Submission` says it was sent, to whom, and when. They remain two records. This
operation writes both because a person pressed one button meaning both, which is
what an atomic operation is for and is not the same as inferring one from the other:
uploading a file through `Dokumendid` still asserts nothing.

**The date is required, is proposed rather than assumed, and is never supplied
by the server.** Said precisely, because the three are easy to run together:

* the box **opens holding today**, visibly, and that initial value is real — it
  is in the field where it can be read, typed over and deleted, which is the one
  shape docs/adr/0078 §2 allows a date default to take. An opinion is written up
  on the day it goes out far more often than not, so proposing today is proposing
  the likely answer, not asserting it;
* the lawyer may **change it to any past day, or clear it entirely**;
* a **cleared box is refused**, on the field, naming the missing day. It is not
  quietly filled in;
* a **future day is refused**, in `clean_sent_on`, in
  `RegisterSentOpinionForm`'s own words — a send is something that happened;
* the **server substitutes nothing at any layer**. `register_sent_opinion`
  refuses `None` and refuses any precision but `DATE`, which is the rule R2-01
  put there after a blank box became `timezone.now()` and the outbound register
  reported `Arvamus välja <today>` about letters nobody had dated.

The distinction that matters is between a *proposal a person can see and
overrule* and a *value the application inserts behind them*. This form makes the
first and the stack refuses the second. `Märgi saadetuks` keeps its «now»,
because pressing send *is* a moment.

Every existing evidence and visibility invariant is preserved untouched: final
evidence may not be less restricted than its `Submission`, evidence is immutable, a
correction is a new version, and the concurrency guard of ADR 0040 still runs.

### 6.3 The recipient is not the sender

`Adressaadid` is required, at least one, and **never defaulted from the Matter's
`Saatja` or `Adressaat`**. An opinion on the first draft goes to the ministry; one at
second reading goes to a Riigikogu committee; one on a revised text may go to a
third body; one may go to an EU institution. Assuming the original sender would put
a false recipient on the canonical outbound record of a professional letter.

This is why the classification work removes `Adressaat` from *initial Matter
creation* and not from the domain model: the column answers a question about the
incoming file, and `SubmissionRecipient` answers a different question about each
outgoing letter. Sender and recipient are not collapsed here.

The control is the plain chip row, **not** the `organisation_picker`. That control's
`+` creates an institution inside the save's transaction, which is right where the
record is about who an unfamiliar body is — and wrong here: registering a letter Koda
sent is not the moment to invent the institution it was sent to, and a picker that
offered to would put a typo into the catalogue under a professional outbound record.

### 6.4 Several per Matter

Nothing is unique on `(matter, …)`, nothing supersedes an earlier opinion, and no
earlier `Submission`, `Document` or `DocumentVersion` is touched. An opinion on the
VTK, one on the draft, one during Riigikogu proceedings and one on the revised text
are four sends, four Submissions and four immutable files. There is no
`Matter.final_opinion` and this round does not invent one (ADR 0061).

### 6.5 Lawyer feedback 13, item by item

The request named five things. Three are implemented, one is deliberately not
introduced, and one is deferred with a reason. Stated separately so nobody has to
infer which is which from the code.

| asked for | state | where |
| --- | --- | --- |
| the send date | **implemented** | `Saatmise kuupäev`, §6.2 |
| the file itself | **implemented** | one upload, `KODA_SUBMISSION_FINAL`, §6.1 |
| the recipients | **implemented** | `Adressaadid`, `SubmissionRecipient`, §6.3 |
| a title for the opinion | **implemented**, through `Submission.title` | §6.1 |
| keywords on the opinion | **deliberately not introduced** | below |
| the association to the Koda publication | **deferred** | below |

**The title is the `Submission`'s own.** `Pealkiri` on the panel writes
`Submission.title`; no second title column was added, and none was needed. An
opinion's title is a property of the send, which is the record that already has
one.

**Per-opinion keywords are deliberately not introduced.** A Matter already carries
the classification the department searches by — policy areas, the legal
instrument, the stage — and an opinion is a send *of* that Matter. Adding a second,
narrower keyword vocabulary attached to individual Submissions would create two
places a topic can be classified, two answers to «what is this about», and a
reporting question with no correct answer; it would also be a new vocabulary,
which is Package A's subject and not this branch's. If the department later wants
to search opinions by their own terms, the honest form of it is a decision about
the existing taxonomy, not a private keyword field on a send.

**Association to the Koda publication is deferred, and §8 says why.**
`MatterWebsiteOverview` (ADR 0085) is the existing publication activity; relating
one to a *specific* `Submission` requires a relation that does not exist, and
creating a publication record here would collide with the package that owns
publication. **No publication model is added by this branch.** A lawyer records
the publication through `+ Ülevaade / uudis` exactly as they do today, and the
association remains a documented hook rather than a half-built column.

## 7 — Versioning: a revised draft is new bytes

A revised ministry draft arriving at `+ Menetluse areng` is captured as a **new
`Document` with its own immutable `DocumentVersion`**, through
`capture_supporting_evidence` and linked to the development that brought it. It is
not a new version of the earlier draft's document.

That is the honest reading: the two files arrived on different days, through
different procedural acts, and what the file has to preserve is *what Koda reviewed
at each stage*. A new version of one logical document would make the earlier draft
reachable only through version history, on a record whose own date is the first
arrival — and the chronology would show one act where two happened.

`DocumentVersion` remains append-only and immutable. Nothing in this round mutates
an evidence file in place, and nothing overwrites a prior draft. `Koja arvamus`
follows the same rule: a supplementary or revised opinion is a new letter and new
bytes.

## 8 — The two sibling packages, now merged

**This section was written about work in flight. Both siblings landed first and
were merged into this branch, so what follows is what happened rather than what was
expected.** The predictions are kept nowhere; the facts replace them.

**Package B — procedural links and undated publications — merged as #233** and
`main` was merged in at `4f263ce`. Twelve files conflicted and all were resolved
keeping both sides whole. `MatterProceduralLink` is therefore an upstream fact
this branch builds on top of, and §5.6 states why this branch still adds no
relation to it.

Two things about that merge are worth recording, because they are invisible
afterwards. Resolving `models.py` and `enums.py` by splicing git's interleaved
hunks would have dropped a constraint from one of the two classes, so both classes
were taken whole — this branch's in place, Package B's re-inserted verbatim from
`main`. And trimming a stale fragment from Package B's side of `views.py` silently
deleted `add_procedural_link` and `correct_procedural_link_view`; the only symptom
was `ProceduralLinkConflict imported but unused`. Both were restored verbatim.

**Package A — classification and the simpler Matter form — merged as #235** at
`47e3bdb`, and `main` was merged in at `955bc56`. It rewrote `matter_create.html`,
reworked `static/js/app.js` and took 467 net lines out of `app/matters/forms.py`.
Five files conflicted and **`forms.py` and `views.py` were not among them** — they
auto-merged, which is exactly what the one-line `{% include %}` and the
keyword-only `_create_context` were for.

**No dependency on either package's vocabulary.** `+ Menetluse areng` reads
`active_stages()` through the canonical selector and hard-codes no stage key, so
Package A's revised stage list arrives here without this branch being edited.
`Adressaat` is untouched in the domain model: `+ Koja arvamus` uses
`SubmissionRecipient` and never defaults it from the Matter's `Saatja`, which is
why Package A can remove `Adressaat` from initial creation without reaching this
record.

**No duplication of Package B's subject.** This round adds no publication model
and no link model. `+ Koja arvamus` does **not** ask for a Koda publication
reference: `MatterWebsiteOverview` (ADR 0085) is the existing publication activity,
associating one with a specific `Submission` needs a relation that does not exist,
and creating one here would collide with the package that owns publication. The
integration hook is documented and nothing else — a lawyer records the publication
through `+ Ülevaade / uudis` today, as they do now.

**ADR numbering.** Both this branch and Package A claimed 0090 on the same day and
both cited their own number from their own migrations, so the repository's usual
tiebreak separated nothing. This branch moved to 0091 **before** either merged,
which is why the Package A merge did not fail
`test_no_two_decision_records_claim_the_same_number` mid-integration.
`docs/adr/README.md` carries both rows.

**The current release.** No release notes and no deployment changes in this branch.

## 9 — Search, reporting and the work surfaces

**Search is not redesigned and `INDEX_VERSION` does not move.**

The three new columns are nullable metadata that no projection reads.
`MatterExternalPosition` was never indexed (ADR 0084 §5) and is not indexed now; a
linked `Document` keeps its own unchanged document-search behaviour, which is the
search people actually perform. A `Submission` written through `+ Koja arvamus` is
indexed exactly as one written through `Dokumendid`, because it is the same record
through the same service — no recipe changed, so no reindex follows this release.
**`MatterProceduralDevelopment` is not indexed**, for the reason ADR 0084 §5
gives for `MatterExternalPosition`: it is a short structured fact whose readers
are the chronology and the Matter page, and the searchable thing a person
actually looks for is the document attached to it, which keeps its own
unchanged document-search behaviour.

Bumping the index version because a nullable metadata column exists would rebuild
the whole corpus to change nothing.

**`lawyer_note` is deliberately not projected.** It is this office's professional
assessment of a third party, and widening the corpus is a decision about disclosure
rather than convenience (ADR 0038, ADR 0056). It can be added later; it cannot be
un-indexed later. And if it ever is, provenance must be preserved in the rendered
result — a hit that showed Koda's words under a ministry's name would be the
attribution defect arriving through search.

**Reporting.** No new metric. Existing external-position counts are unchanged and
remain understandable: `provenance` partitions a population that was previously
reported as one, so any future report that wants the split can have it, and nothing
silently redefines what an existing count means. Submission reporting is untouched,
because `+ Koja arvamus` writes the same `Submission` the outbound register already
counts.

**Work surfaces.** One business commitment appears once. `Koostan arvamuse` is a
`NextAction` and appears where open steps appear. A new `Kaasamine` creates no wait
unless somebody asks for one, and a wait is one `WorkItem` and not also a
`NextAction`. `+ Menetluse areng` creates at most the one step somebody typed. A
`Väline seisukoht` of either provenance creates no work at all, exactly as ADR 0084
§5 decided — so a Matter carrying ten of them never reads as late.

## 10 — Permissions

Every new and extended record follows the existing central rules and adds no new
authorization path.

`MatterExternalPosition` is a `VisibilityInheritingModel` read only through
`visible_to`; the new columns are on that row and inherit its scoping, so a
restricted position's `Allikas` and `Juristi märkus` are as unreachable as its
organisation. `DocumentLink.visible_to` still requires **both** ends to be readable.
Final evidence still may not be less restricted than its `Submission`. The
engagement queryset on both panels is narrowed by the reader's own visibility, so a
crafted POST naming a round on another file — or one restricted below the Matter —
is refused by the field as well as by the service. `opinion_sent` is derived from a
`visible_to`-scoped read, so a restricted `Submission` puts no sentence on the page.
Every write route is behind `business_write_required` and takes
`lock_open_matter_for_business_write`, because a page is not a boundary.

## 11 — Migrations

**Four, all additive**, no `RunPython`, no `RunSQL`, no backfill, no reindex, no
archive rebuild. The numbers below are the final ones, assigned after Package B
(#233) and Package A (#235) merged and were merged in here, so no app has two
leaves in any merge order.

**`matters/0029_external_position_provenance`** — the three columns §3 and §4 add
to `MatterExternalPosition`:

* `provenance` (`CharField`, default `LEGACY`, indexed) — true of every existing row;
* `source_label`, `lawyer_note` — blank on every existing row, also true of all of them;
* `organisation` widened to nullable — every stored row keeps its organisation;
* three `CHECK`s (`..._provenance_vocabulary`, `..._author_or_label`,
  `..._label_is_received`), all satisfied by every existing row as they are added;
* one composite index, `matters_extpos_matter_prov`.

**`matters/0030_procedural_development`** — `MatterProceduralDevelopment` itself
(§5). A new table, so nothing existing is touched: `title`, nullable `occurred_on`
with its `occurred_on_precision`, `note`, `created_by`, the visibility columns of a
`VisibilityInheritingModel`, four `CHECK`s
(`matters_development_title_required`, `..._precision_vocabulary`,
`..._undated_is_exact`, `..._visibility_vocabulary`) and the index
`matters_devel_matter_date`.

**`documents/0011_procedural_development`** — the seventh typed target column on
`DocumentLink`, and its exactly-one `CHECK` re-stated over seven columns. Nullable
and blank on every existing link (ADR 0075 §5).

**`audit/0023_procedural_development`** — an `AlterField` over `ChangeEvent.event_type`'s
`choices` for the three `PROCEDURAL_DEVELOPMENT_*` events. Python metadata; no
database object changes.

**Nothing migrates for §2.** The `Kaasamine` correction is a form and a route: the
`feedback_deadline` column, its data and its constraints are exactly as #227 left
them, which is what makes the historical-compatibility promise cheap to keep.

Migration-from-zero compatible and upgrade compatible. Reversible: the adds and the
constraints reverse, the new table drops, and re-narrowing `organisation` succeeds
on any database whose rows these migrations did not change. A reverse would lose
what people wrote into the new columns and the new table, which is the ordinary
cost of any additive schema change.

No merged migration is edited.

## Alternatives considered

**A `WorkflowStep` model and a state machine.** Rejected in §0. The architecture
constraint forbids a generic workflow engine, and the constraint is right here: the
lawyer flow is seven named acts on six existing records, and a generic engine would
make every consumer branch on a `kind` while the validation that matters had nowhere
to live but in those branches.

**Four feedback models.** Rejected in §3.1. One set of rules implemented four times,
with the authorship difference — the only real difference — copied into each.

**Infer provenance for historical rows.** Rejected in §3.4. Every candidate rule is
wrong for some real record, and a wrong provenance is worse than an unspecified one.

**Let `Allikas` answer «whose position is this» on `Teiste arvamus` too.** Rejected
in §3.3: a free text box beside the one shared catalogue is a ninth way of naming an
institution.

**Put `Juristi märkus` in `Seisukoht` with a separator.** Rejected in §4. A separator
is not attribution, and the whole defect is a file recording this office's criticism
as the ministry's words.

**Make `Entry.occurred_at` nullable so a development could stay an `Entry`.**
Rejected in §5.1, and it is the alternative most worth stating: it is a large change
to the most-read table in the product — every chronology reader, the `-occurred_at`
ordering and the timeline's pagination depend on that column being present — made to
avoid a table that costs one `CreateModel`. And it would still have left the other
two problems: one `body` for two authors, and a title a projection has to parse out
of prose.

**Create a `NextAction` automatically after a `Submission`.** Rejected in §5.5. The
application does not know what happens next, and an invented step is
indistinguishable from a deliberate one a week later — ADR 0086's own reasoning about
deriving a step from a deadline.

**Default `Koostan arvamuse` to the consultation deadline.** Rejected in §1.2. That
is `Matter.response_deadline`, which is somebody else's timetable; copying it would
make a lawyer's own plan a restatement of an obligation.

**Let both first-step boxes save, with `Koostan arvamuse` winning.** Rejected in
§1.3. Silently dropping a sentence somebody wrote is worse than a refusal they can
read and fix.

**Ask for a Koda publication reference on `+ Koja arvamus`.** Deferred in §8. The
relation does not exist, and creating a publication model here would duplicate the
package that owns publication.

**Derive a `Menetluse areng` from a `MatterProceduralLink`.** Rejected in §5.6, and
forbidden rather than merely declined: a link says *where a proceeding lives* and an
event says *what happened and when*. Manufacturing the second from the first would
put a dated event on the file that nobody recorded — the same invention §1.2 refuses
for a date and §3.4 refuses for a provenance.

**Keep the `Tagasisidet ootame kuni` default and make the wait quieter.** Rejected.
The wait's loudness is the point of ADR 0086 §3 and is right for a round somebody
opened deliberately. What was wrong was opening one on every round by default.

**Empty the default and leave the box on the panel.** This is what the round did
first, and it was rejected after the fact rather than before it. It fixes the
consequence and leaves the cause: an empty box is still a question every lawyer
reads, understands and skips on every consultation they file, and the capture panel
is still the thing that opens a managed wait. The department's complaint was that
the panel was complicated, and a panel with one fewer default has the same number of
questions in it.

**Put the wait behind a checkbox on the panel — «ootan tagasisidet» plus a date.**
Rejected. It is the same question, asked in two controls instead of one, and it
leaves opening a wait as a side effect of pressing `Salvesta` on a form about
something else. The act has a name, so it gets a control with that name on it.

**Build a general «this record is waiting on somebody» mechanism.** Rejected, and
forbidden by the architecture constraint. `open_engagement_feedback_wait` writes one
nullable column on one record and records one audit event. There is no wait
registry, no state machine and no step anything else can be plugged into: three
other records in this ADR could conceivably «wait», and none of them does, because
no lawyer has asked them to.

## Consequences

The ordinary journey is one page and then one launcher. A lawyer files the Teema with
a preparation date and the file has a plan; runs a consultation without acquiring a
managed wait; records what came back and what others said, told apart and attributed;
registers Koda's opinion where the work is; and records what the procedure did next,
with the stage and the next step, in one save.

`LISA TEEMALE` grows from nine chips to twelve. That is a real cost to a bar whose
stability is tested to the pixel, and it is paid rather than avoided: the chips are
the product's inventory of what can be recorded, and four of them were missing.
`Väline seisukoht` splits into two chips, so the bar's canonical order and the
launcher stability test both change.

A `Väline seisukoht` chronology row can now be three lines rather than two.

`Uus teema` gains one control. Its visual baselines change, as do the Teema page's.

Package D still owns the unified OneNote-like substantive history. This round
deliberately creates clean semantic data and uses current UI patterns; it builds no
second timeline.

**What Package D asked for and has.** Its discovery established that an incoming
procedural development could not be projected truthfully from any existing record,
and `MatterProceduralDevelopment` is the answer: a required title it can read
without parsing prose, an optional date at the precision it is known to, an optional
lawyer note that is never the event, optional evidence through the seventh typed
`DocumentLink` column, and an optional stage change and next action tied to it by a
shared operation identifier rather than by a copied column.

**What remains for Package D:** how a `Menetluse areng`, a
`Meile saadetud tagasiside`, a `Teiste arvamus` and a `Koja arvamus` read together
as one narrative, whether the chronology should group by provenance, and whether the
process strip should draw procedural developments. None of those needs a schema
change after this round.

## Reversibility

High for the product, and different for the two halves.

Additive and cleanly reversible: removing `provenance` leaves every row readable
under `Väline seisukoht`'s original heading; re-narrowing `organisation` succeeds
unless an aggregate record was written; deleting the two feedback panels leaves the
one that existed; removing `+ Koja arvamus` leaves every `Submission` it wrote
canonical and managed on `Dokumendid`; and putting `Tagasisidet ootame kuni` back on
the capture panel is one field, one template block and one keyword argument — the
column, the service and the wait were never withdrawn.

`MatterProceduralDevelopment` is the one that is not free. Dropping the table would
lose what people wrote into it, and there is nowhere else the fact could go — which
is the whole reason the record exists. What *is* reversible is the surface: the
panel, the chronology row and the seventh `DocumentLink` column can each be withdrawn
leaving the rows stored and readable through the admin and the shell. Nothing depends
on the record that could not be rebuilt from it.

What a reverse of the additive half would lose is what people wrote into the three
new columns on `MatterExternalPosition`, which is the usual cost of additive columns
and the reason these are the smallest set that answers the question.
