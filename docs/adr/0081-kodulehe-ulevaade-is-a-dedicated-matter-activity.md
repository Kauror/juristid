# 0081 — `Kodulehe ülevaade` is a dedicated Matter activity, and `koda.ee` is its boundary

*Accepted 2026-09-14.*

A lawyer finishing a round of work frequently decides that the membership should
be told about it on the Chamber's own website. The file has never been able to
hold that decision. The intention lived in somebody's head until the page
appeared; the published address lived in a browser history; and the question a
colleague actually asks — «kas sellest kirjutati kodulehele, ja kus see on» —
had no answer on the Teema at all.

This records a dedicated Matter activity for it: three states, two columns, one
trust boundary, and deliberately nothing else.

## 1 — Why it is its own record

Seven things on this product could have absorbed it, and each would have lost
something the department relies on.

A **`Märge`** is narrative. «Kodulehe ülevaade on plaanis» written as prose is a
sentence nothing can ask a question of: it cannot be listed, it cannot be
published, and the day the page goes up somebody writes a second note and the
file has two, neither of which is the record.

A **`Document`** is bytes in the immutable evidence store. A koda.ee page is not
evidence, nothing is uploaded, and a second place to attach files is a second
place to lose them — the reasoning `Kaasamine` already records (docs/adr/0027).

**`Tulemuse tõend`** and a **`Submission`** are both about what Koda *sent*. A
summary written for the membership is not the Chamber's formal outbound opinion,
and folding it into that vocabulary would corrupt every submission statistic the
department reports on.

A **`Töövõit`** is a reviewed judgement that Koda changed something. Publishing a
page is not a claim that anything was won, and a write-up of a file that went
nowhere is as ordinary as a write-up of one that did.

A **`NextAction`** and a **response deadline** are work with a date. An overview
that is owed has no date — nobody has undertaken to publish it by any day — and a
record that generated a task would make every Matter carrying one read as late
from the moment somebody typed it in. That is the mistake docs/adr/0078 §3
refused for `feedback_deadline`, and it is refused here for the same reason.

And it is not a **`Kaasamine`** either, although the resemblance is the closest
of the seven and `EngagementKind.WEB_CALL` is literally "kaasamiskutse veebis".
An engagement records that Koda **asked** somebody something. An overview records
that Koda **told** the membership what happened. The first has an audience, a
response count and a reply-by date, none of which means anything here; the second
has a published address, which means nothing there. One model carrying both would
need every column to be optional, and a record whose every field is optional is a
record that cannot say what it is.

So: `MatterWebsiteOverview`, beside `MatterEngagement` in `app.matters`, with its
own service functions, its own audit events and its own three-state lifecycle.
**No new framework.** It is a Matter child record written through named use
cases, exactly like every other structured fact on this page; the launcher, the
lock discipline, the visibility inheritance, the audit model and the chronology
projection are all the existing ones.

A Matter may carry **zero, one or many**. A long proceeding is written up more
than once, so nothing is unique on `matter`.

## 2 — `Plaanis`, `Avaldatud`, `Tühistatud`

Three states, and the transitions between them are the whole lifecycle:

```
PLANNED ──► PUBLISHED ──► PUBLISHED   (a correction, §5)
   │
   └──────► CANCELLED                 (terminal)
```

**`Plaanis` carries no address and no date**, because neither exists. The whole
content of the record at that moment is *that the write-up is owed*, and that is
why `+ Kodulehe ülevaade` is a panel with one button and no fields. A title box
here would be asking somebody to invent a headline that the real page contradicts
a week later; a date box would be asking them to commit to a day nobody has
agreed. Both would then sit on the file looking like facts.

**`Avaldatud` requires both**, and the database says so rather than only the
service: a `PUBLISHED` row has a non-empty `url` and a `published_on`, and a row
in any other state has neither. A link on a record that claims nothing was
published is a link a reader would follow.

**`Tühistatud` is what a dropped plan becomes.** Nothing is deleted when a plan
changes: what the department decided at the time is part of the file, and quietly
removing the row is how a reader concludes nobody ever recorded anything — the
rule `FactStatus` was written for.

**`Avaldatud` → `Tühistatud` is refused in v1**, and not for want of a state to
move to. The page is on koda.ee. A record saying it was never published would be
the file disagreeing with the website, and what should happen to an overview that
was *taken down* is a product question nobody has been asked. When somebody asks
it, the answer is a fourth state with its own date, not a reuse of this one.

**`Tühistatud` is terminal.** A plan that comes back is a new plan; resurrecting
the old row would give one record two lives and one `status_changed_at`.

### The publication date is the person's

`published_on` is the day the page went up, as somebody states it, and **nothing
in this application derives it**. The form offers today because today is the
overwhelming case and retyping it is the friction people complain about — but the
default is *visible*, it can be changed, and a box left empty is a refusal naming
the missing date rather than a silent stamp.

That is docs/adr/0078 §2 applied before the mistake is made rather than after:
`+ Kaasamine` used to stamp `timezone.localdate()` on every row it wrote, so a
consultation from March, typed up in September, was filed as a September
consultation with no box on the screen saying so.

`published_at` is a different column and a different fact — the moment somebody
wrote the publication down — and a correction months later moves the first and
never the second.

## 3 — The address: `koda.ee`, `https`, and a parsed host

A published overview may point at **`https://koda.ee/…` or `https://<anything>.koda.ee/…`**
and nothing else.

`http` is not on the list, and that is a deliberate difference from
`normalize_engagement_url`, which accepts both schemes. An engagement link points
at whatever a campaign tool happened to serve, and the historical register is full
of addresses this department did not choose. This is the Chamber's own page on
the Chamber's own site: there is no version of it that is legitimately
unencrypted.

The comparison is against the **parsed host** — `hostname == "koda.ee"` or
`hostname.endswith(".koda.ee")` — and never against the string. Three shapes make
that the difference between a boundary and a decoration:

* `https://koda.ee.example.com/uudised/x` contains `koda.ee` and is somebody
  else's domain;
* `https://koda.ee@example.com/uudised` puts it in the **userinfo**, which every
  browser ignores when resolving and a substring check believes;
* `https://notkoda.ee/x` ends in `koda.ee` without the dot that makes a subdomain
  a subdomain.

Userinfo is refused outright rather than merely ignored: there is no legitimate
koda.ee page behind `user:pw@`, and a credential on the file is a credential in an
audit payload, on a rendered page, and in everybody's browser history. That is
the other half of red-team finding F-2, which `MatterEngagement._hostname` records
for the same reason.

An over-long address is **refused, never truncated** (finding F-1): a link cut off
at a thousand characters is a link that no longer resolves, and a stored pointer
that is quietly wrong is worse than a refusal naming the row.

The rule lives in one function, `normalize_koda_website_url`, which every writer
of the column passes through. The form calls it too — so a person sees the
refusal beside what they typed — but the form is not where it is enforced: a form
is what one browser was shown, and a POST is what arrives.

**This is the whole domain decision, and it is `koda.ee` alone.** Publishing under
a second domain is a business decision this record does not make; the day it is
made, `KODA_WEBSITE_HOST` is the line that moves and the only one.

## 4 — Where it reads, and the eleven places it deliberately does not

**Two surfaces, and that is all.**

`Kodulehe ülevaated` is a strip on the Matter page listing the overviews the file
still **owes**, with `Avalda` and `Tühista` on each. It renders only when
something is planned — there are no permanently visible empty sections on this
page — and it says «Ülevaade on plaanis, aga veel avaldamata» in words rather than
signalling it by colour alone.

The **chronology** records the two completed milestones: an overview published,
and a plan cancelled. Projected from the canonical record through
`projected_milestones`, like every other structured fact since docs/adr/0074 §14,
so the audit events are not rendered beside them and one act takes one line. A
*planned* overview is deliberately not there: the chronology reads newest-first
and means *what has already happened*, and an intention is not that.

The published row renders **`Ava kodulehel`**, never the address. A raw URL as a
row's own text is a line a reader has to parse instead of read, and it is the one
shape in which a look-alike address would be believed. `target="_blank"`,
`rel="noopener noreferrer"`, and a visually hidden «— avaneb uues aknas» so the
new tab is announced rather than merely happening.

**Nothing else touches it in v1**, and each absence is a decision:

* not a fourth task source in `Minu asjad`, `Tähtajad`, `Ülevaade` or the
  department work lists — an overview that is owed is not dated work, and adding
  it would make every Matter carrying one appear in somebody's queue;
* no `NextAction`, no `MatterImportantDate`, no effect on `Matter.response_deadline`;
* no search result and no row in the search projection — the address is a pointer
  on one file, and indexing it would answer queries nobody meant to ask;
* no archive projection and no document listing;
* no Submission metric and no work-victory metric;
* no count, no statistic, no filter, no register sort, no badge.

The `Uus teema` intake-reader flow does not offer the chip either. A Matter is
written up after work has happened, not at the moment it is created.

## 5 — A closed Matter: every plan is dropped, and the addresses stay correctable

Closure means the department has stopped working on the file. Two consequences,
and they point in opposite directions.

**Every planned overview is cancelled by the closure**, in `close_matter`'s own
transaction and under its own lock, each with its own auditable event whose
payload names the closure as the reason. Without that, a closed file would go on
saying that a summary is owed while every route that could publish or cancel one
refuses a closed Matter — an instruction permanently outstanding and impossible
to discharge.

**Closure is never blocked by one.** There is no precondition and no refusal:
closing a Matter carrying ten plans is the same gesture as closing one carrying
none. A file is not held open by a write-up nobody did.

**Nothing reopens the Matter**, then or later.

**A published overview's address and date stay correctable on a closed Matter.**
Closure means no *new* business content — no new plan, no publication of one, no
cancellation, each refused by `lock_open_matter_for_business_write` on its own
route. It has never meant that a fact recorded wrongly must stay wrong, which is
the rule `edit_entry` has kept since docs/adr/0075 §12 and
`matters:edit_entry` proves on a closed file every day.

The correction cannot become a door into the other three. `correct_website_overview_link`
reads the status **from the locked row** and refuses anything that is not already
`Avaldatud`: the transition that *creates* a publication is the guarded one, and
this one can only move an address that already exists.

Corrections observe the project's optimistic concurrency. A rendered form carries
the record's revision — `updated_at`, the token `personal_note_revision` and
`entry_revision_token` already use — and a save whose token is not the stored one
raises `WebsiteOverviewConflict` and **writes nothing**. The token is compared
after the row lock, so the version compared against is the committed one, and
before anything else is decided, so a refusal leaves nothing behind. The page
answers 409 with the form still holding what the person typed, and does not
advance the hidden token: adopting the newer one would be the view deciding that
the next submit may overwrite what the other writer saved (QA-09).

## 6 — The audit trail, and the migration

Four event types, because four different things happen to this record and a
history that could not tell them apart would be a history nobody trusts:

| event | what it means |
| --- | --- |
| `WEBSITE_OVERVIEW_PLANNED` | the file is owed a summary |
| `WEBSITE_OVERVIEW_PUBLISHED` | the page exists, at this address, from this day |
| `WEBSITE_OVERVIEW_CANCELLED` | the plan was dropped — `reason` says by whom: a person, or the closure |
| `WEBSITE_OVERVIEW_LINK_CORRECTED` | the file was wrong about where or when |

Each carries the actor, the timestamp the audit model already stamps, and the
small scalar values that changed: the address and the publication date on a
publication, and both prior and current values on a correction. None of the four
is in `TIMELINE_EVENT_TYPES`, because the chronology renders the two milestones
from the canonical record and reading the event as well would state one act
twice.

The database carries the invariants rather than trusting the services with them:
a `PUBLISHED` row has a link and a date, a row in any other state has neither, a
published row records when it was written down and a cancelled one when it was
dropped, the status vocabulary is closed, and one address is filed at most once
against one Matter. The last is the only uniqueness there is, and it is
deliberately **not** on `matter`: three plans on one file are three legitimate
rows, while one koda.ee page recorded twice on one Matter is the same overview
entered twice.

Two indexes, both of them a read this product actually performs: `(matter, status)`
for the strip's question and the closure's, and `(matter, -published_on)` for the
chronology's, which is the pair `MatterEngagement` keeps on `(matter, -occurred_on)`.

**The migration is schema-only.** `matters/0022_matter_website_overview` is one
`CreateModel`; `audit/0018_website_overview_events` is one `AlterField` over a
`choices` list, which is Python metadata and not a database object. There is no
`RunPython`, no `RunSQL`, no backfill and nothing to backfill from: the record did
not exist, so every Matter has zero of them, and deriving a planned overview from
a note, a tag, a `WEB_CALL` engagement or a koda.ee address somebody once pasted
somewhere would put an intention on the file that nobody stated.

## Alternatives considered

**A `kind` on `MatterEngagement`.** Rejected in §1: the two records answer
opposite questions, and one model would need every column optional.

**A generic «Matter link» activity** with a type and a URL, so the next external
pointer costs no table. Rejected because this product has been here before: a
generic container makes every consumer branch on `kind`, and the validation that
matters — *this* host, *this* scheme, *this* lifecycle — has nowhere to live but
in those branches. Boring and explicit beats clever (AGENTS.md).

**A free `status` with no transition rules**, letting the UI decide. Rejected:
`PUBLISHED → CANCELLED` would then be one crafted POST away, and the invariant
that a published row keeps its address would hold only as long as every caller
remembered it.

**Making the overview a work item so that somebody chases it.** Rejected in §4.
There is no date to be late against, and a queue of undated obligations is a queue
people stop reading.

**Allowing a second domain now** (`kaubanduskoda.ee`, a campaign microsite).
Rejected as a business decision nobody has made; §3 names the single line that
moves when they do.

## Consequences

* `Lisa teemale` offers eight operations rather than seven. The bar's stability
  contract is unchanged — the chip is a control, the form is a separate element,
  and the order is fixed (docs/adr/0078 §1).
* Three visual baselines that photograph the launcher change, and only because
  the row has one more chip in it.
* A Matter page gains one conditional strip and the chronology one milestone kind.
* Nothing that counts, reports, indexes or schedules changes at all.

## Reversibility

High. The table is new and empty, nothing else reads it, and no existing column,
projection or statistic was touched. Removing the feature is one migration and
the deletion of one strip, one panel and one chronology branch. Widening the
domain boundary is one constant. Adding a state for an overview that was taken
down is additive, and §2 says what it would have to carry.
