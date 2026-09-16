# 0089 — `Menetluse link` is a reference, and a publication date is optional

**Status:** accepted
**Date:** 2026-09-17

Two decisions from one round of lawyer testing, recorded together because they
are both about the same failure: the application asking somebody to state a fact
they do not have, and getting a plausible answer instead of a true one.

**§2–§7 and §11–§13 record a new record** — `Menetluse link`, where the official
proceeding on a Matter actually lives. It implements, narrowly, the master
specification's §11.2 `ExternalReference`.

**§8–§10 change a decision this product already took.** It
**amends ADR 0081 §2 and ADR 0081 §6's first implication**, and it
**supersedes ADR 0085 §3 entirely** — the date default that arrived with the
published path. Everything else ADR 0081, ADR 0083 and ADR 0085 decided about
`Ülevaade / uudis` stands exactly as written: three states, the same
transitions, no `PUBLISHED → CANCELLED`, both columns empty on a plan, the
widened address rule, the closed-Matter rules, the audit vocabulary, the
optimistic concurrency and every absence ADR 0081 §4 listed.

---

# Part one — `Menetluse link`

## 1 — The question the file could not answer

A lawyer opening a Teema three months after somebody else filed it asks one
thing before any other: *where is this actually happening*.

The answer is an address. The EIS toimik. The ministry's own document register.
The Commission's consultation page, or the EUR-Lex entry for the proposal. The
Riigikogu proceeding. Whichever of those the file is running through, somebody
had it on screen on the day the Matter was created — and the file had nowhere to
keep it. It lived in a browser history, in an e-mail, or pasted into a `Märge`
where nothing could find it and nothing could render it as a link.

So the colleague picking the file up reconstructed it from the title, through a
search engine, and frequently landed on a different proceeding.

## 2 — Five kinds, and the kind is a statement rather than a proof

The vocabulary is the five places an Estonian policy lawyer looks up a
proceeding:

| value | label |
| --- | --- |
| `EIS` | `EIS` |
| `MINISTRY_REGISTER` | `Ministeeriumi dokumendiregister` |
| `EU_PROCEDURE` | `ELi menetlus` |
| `RIIGIKOGU` | `Riigikogu` |
| `OTHER` | `Muu menetluslink` |

**Nothing infers the kind from the hostname, and nothing verifies it against
one.** That is the load-bearing half of this section, and it is the same
conclusion ADR 0084 §3 and ADR 0085 §2 reached about allow-lists, in the shape a
vocabulary takes.

A ministry publishes through more than one document register, and moves between
them. An EU file is read on EUR-Lex one month and on a Commission consultation
page the next. `EIS` is not `eelnoud.valitsus.ee` and nothing else, and a rule
that said it was would silently reclassify every stored row the day a register
moved domain — or refuse to record where a proceeding actually is, which is
worse.

So `kind` says **what the lawyer was pointing at**. It is the fact a colleague
opening the file needs, it is stated by a person, and it is corrected like any
other recorded fact. `OTHER` is a real answer rather than a gap: a proceeding
lives wherever it lives, and refusing the sixth kind of source would mean
refusing to record the truth about the file.

## 3 — The address rule is the shared one

`normalize_procedural_link_url` is the fourth caller of `_normalize_public_link`,
beside the `Kaasamine`, `Väline seisukoht` and `Ülevaade / uudis` doors. It
keeps exactly what those keep:

| rule | why |
| --- | --- |
| `http` or `https` only | `javascript:` and `data:` are script delivery dressed as an address; `file:` and `ftp:` point where the reader's browser cannot usefully follow |
| a **parsed host**, non-empty | `https://user:pw@/path` has an authority and no host at all |
| **no userinfo** | there is no proceeding behind a credential, and a credential on the file is a credential in an audit payload, on a rendered page and in everybody's browser history (F-2) |
| refused, never truncated, past 1000 characters | a link cut off is a link that no longer resolves (F-1) |

It adds one rule the overview's door does not have: **empty is refused**. There
is no state of this record that legitimately has no address, so a row with
nothing to open is not a reference to anything.

**Nothing is rewritten.** No trailing slash added or removed, no scheme
upgraded, no query parameter dropped, no punycode expanded. A document
register's deep link is frequently a query and nothing else, and a canonicaliser
that «tidied» it would quietly point the row at a different page than the one
somebody opened.

The safety half is *shared* rather than copied, for the reason ADR 0085 §2
gives: it is the difference between a clickable control on a page a lawyer
trusts and a script-delivery vector, and a second copy is a second place for
`javascript:` to be forgotten.

## 4 — A reference, not an ingestion, and that is the record

**This application never contacts the address.** It is not fetched, not
crawled, not polled, not resolved, not previewed and not watched. No `Document`
and no `DocumentVersion` is created from it. No date, title or metadata is read
off the other end. Nothing classifies what is behind it, nothing is queued and
no task system is introduced.

That is a deliberate boundary rather than a missing feature, and it is the
master specification's own:

* **§20.2** puts EIS automation *after* the core workflow is proven —
  «adoption risk is more important than feed automation» (§21);
* **§11.2** warns in one sentence against the exact failure this record could
  introduce: «never present a static link as synchronized unless an integration
  actively maintains it»;
* **AGENTS.md** refuses Celery and background job frameworks without measured
  need and explicit approval.

A row here says what a lawyer wrote down on the day they wrote it down, and it
will go on saying that when the page behind it has moved. **Nothing claims the
page is still there**: no link checker runs, no status is stored, and a dead
link years later is a fact about the web rather than an error in this file —
exactly as ADR 0085 §2 decided for a published address.

**The shape is compatible with the automation that is deferred.** A future feed
writes its own observed identifier and retrieval timestamp *beside* these
columns; nothing here has to be rebuilt for that, and nothing here pretends to
it now. What this ADR refuses is implementing it early, not enabling it later.

## 5 — Three kinds of address on one Teema, and none is inferred from another

A Teema page now renders four kinds of link, and the product rule is that they
are **different facts** which happen to share a validator.

| record | what it says |
| --- | --- |
| `Menetluse link` | **where the official proceeding lives** — `EIS: https://…` |
| `Ülevaade / uudis` | **this file, written up where the public can read it** — `Koda kirjutas teemast: https://koda.ee/…` |
| `Väline seisukoht` | **what another organisation said about it**, and where to read that |
| `Kaasamine` | the campaign or consultation tool Koda used to ask members |

**The same address may legitimately be recorded as more than one of them**,
because a lawyer meant more than one. A ministry's opinion hosted in its
document register is both a position that body stated *and* a page in the
register where the procedure runs; which of the two somebody is recording is
their intent, and this application does not have it. So **nothing is inferred**:
recording where the proceeding lives is not a claim that anybody stated a
position, and recording a position is not a claim about where the procedure
runs.

Unifying them into one untyped URL bucket was considered and rejected. It would
have made every consumer branch on a `kind` that meant four different things,
and it would have put a ministry's press release and a ministry's document
register in one list under one heading.

Feedback and opinion *provenance* — which of these a received document came
through — is a separate question and is deliberately not decided here.

## 6 — Several per Matter, one address per Matter, and no deletion

**Zero, one or many**, including several of one kind: a long proceeding runs
through two ministries' registers and a Riigikogu page at once, and a `kind`
usable only once would make the second one unrecordable. There is deliberately
no uniqueness on `(matter, kind)`.

**One address per Matter**, enforced by
`matters_procedural_link_one_row_per_address`. Unlike `MatterExternalPosition`
— where two organisations may genuinely have published at one address — this
record carries no second business fact that could make two identical rows mean
different things. So the duplicate a double-click, a browser retry or a stale
response would produce is refused by the database rather than merely discouraged
by the form.

Above it, `record_procedural_link` is **idempotent where the answers agree**: a
second submit carrying the same kind, address and label returns the row that is
already there and writes no second audit event. The save the person meant
happened, and telling them it failed would be a lie about a record that is in
front of them. A second submit carrying a *different* kind or label is refused
by name instead, because silently ignoring a changed classification would leave
somebody looking at a row saying something they had just corrected and been told
was saved.

**No deletion**, on an open Matter or a closed one. Create and correct — the
kind, the label and the address are all correctable in place — because what the
file recorded and who recorded it is part of the file. The rule
`MatterEngagement` and `MatterExternalPosition` both keep (ADR 0084 §8).

**A correction is allowed on a closed Matter**, and adding one is not. Closure
means no new business content; it has never meant that an address recorded
wrongly must stay wrong. The same split, for the same reason, as
`correct_website_overview_link` (ADR 0075 §12, ADR 0081 §5).

## 7 — Where it appears, and what it is not allowed to become

**A rail card, `Menetluse lingid`**, under `Koja arvamus` and above
`Seotud materjalid`. The rail is «information that is looked up, never read»,
which is exactly what this is: nobody reads a register address, somebody reaches
for it.

**Nothing at all when there are none**, which is the ordinary case today. There
are no permanently visible empty sections on the Teema page, and specifically
**not five empty rows** waiting to be filled in. The one compact add affordance
is the launcher's own chip, which is on every open Matter a writer can see.

**The link is labelled, never printed.** `display_label` is the lawyer's own
name for it, the parsed host otherwise, and the kind's own name where the
address has no host to name. A raw URL as a row's own text is a line a reader
has to parse instead of read, and it is the one shape in which a look-alike
address is believed (ADR 0081 §4).

**`+ Menetluse link` is the tenth launcher chip**, second to last, before
`+ Lõpeta teema`. The bar's stability contract is unchanged — the chip is a
control, the form is a separate element, and the order is fixed (ADR 0078 §1).

**It is not work.** No `NextAction`, no `MatterImportantDate`, no deadline, no
work item, no badge, no effect on `Matter.response_deadline`. A Matter carrying
four references reads exactly as it did with none.

**It is not a chronology row.** The two audit events exist and are readable, and
neither is in `TIMELINE_EVENT_TYPES` — like every other structured fact since
ADR 0074 §14, and here for a second reason: a procedural link is not something
that *happened* on the file, it is where the file is happening. A line saying
«EIS link added» on a Tuesday would be the history of somebody's typing.

**It is not in search.** No `SearchDocument` row, no index-version bump, no
reindex, and nothing about the linked page is indexed. A Matter's links are
found by opening the Matter, which is the ordinary access rule. If a later
round finds a real need to search on the label, that is the smallest existing
projection extension and its own decision.

**Its visibility is its Matter's.** `MatterProceduralLink` is a
`VisibilityInheritingModel` read only through `visible_to`, so a link may be
*more* restrictive than its Matter and never less — AGENTS.md's rule, and
AUTH-003's. There is no independent sharing model for these links.

---

# Part two — the `Ülevaade / uudis` publication date

## 8 — The decision that changes

### What it was

ADR 0081 §2 made an `Ülevaade / uudis` two columns, and §6 wrote «published
means published» as two database implications: a `PUBLISHED` row has an address
**and** a day, and every other row has neither.

ADR 0085 §3 then added a default. The date box filled itself with today the
moment somebody started typing an address, on the reasoning that pasting a link
*is* choosing the published path and retyping today's date after that is pure
friction. It was careful: a *visible* default, in a box the person could read,
change and clear, that never fired on an untouched form and never fired again
once the box had been touched.

### What lawyer testing measured

Both halves failed, in the same direction.

The refusal came first. Somebody pastes an address out of a search result, a
mail or a colleague's message. The page plainly exists. The day it went up is
not on the page, not remembered and not worth a hunt. The save refused, asking
for `avaldamise kuupäeva` — and what people typed to get past it was **today**.

The default then removed even the moment of hesitation. A plausible date was
already in the box, it looked correct, and it was accepted.

So the file filled up with publication dates the application had proposed and
nobody had checked — indistinguishable, afterwards, from dates somebody knew.
That is precisely the failure ADR 0078 §2 names and this product exists to
avoid: manufacturing historical certainty.

### The decision

**A publication is made by its address. The publication date is optional.**

* An `Ülevaade / uudis` may be `Avaldatud` with a public `http(s)` address and
  **no** publication date.
* `NULL` means **unknown**, and unknown is an ordinary answer rather than an
  incomplete record.
* **Nothing invents one.** Not `timezone.localdate()`, not the Matter's creation
  date, not the row's `created_at`, not `published_at`, not
  `status_changed_at`, and not `1900-01-01`, `1970-01-01` or `9999-12-31`. The
  service stores what it was given and derives nothing.
* **No form offers today either.** `data-publication-default`,
  `data-publication-trigger` and `bindPublicationDate` are removed from
  `static/js/ux.js` — ADR 0085 §3 is superseded — and
  `WebsiteOverviewLinkForm`'s `initial=timezone.localdate` is removed with them.
  A default in a box is a default that gets accepted.
* **A date with no address is still refused**, unchanged. A publication date is
  a fact *about a page*, so one filed with nothing to open is a claim about
  nothing.
* **Clearing a date is an ordinary correction.** Somebody who realises the day
  on the file was a guess empties the box; the row stays a publication at an
  address, and nothing puts today back on the next save.

`WEBSITE_OVERVIEW_NEEDS_DATE` is deleted rather than left in place, because a
refusal sentence no code can produce is a rule the next reader will believe.

### What did not change

Stated explicitly, because this is exactly the kind of change under which a
neighbouring rule quietly stops being enforced.

* **Three states and the same transitions.** `PLANNED → PUBLISHED`,
  `PLANNED → CANCELLED`, `PUBLISHED → PUBLISHED` as a correction, and **no
  `PUBLISHED → CANCELLED`**. A blank date does not mean cancelled, does not mean
  planned and does not make the address invalid.
* **A blank date is not a downgrade.** An undated publication is `Avaldatud`.
  Nothing reads it as «plaanis».
* **The second implication.** A `PLANNED` or `CANCELLED` row still carries
  neither an address nor a date, and the database still says so.
* **The address rule, the timestamps, the status vocabulary and the per-Matter
  uniqueness of a published address** — every one of ADR 0081 §6's other
  constraints is untouched.
* **`published_at` and `published_on` stay two facts.** The first records when
  somebody wrote the publication down; the second records the day the page went
  up. They were never the same thing and this makes the difference visible: one
  of them exists on every published row and the other does not.

## 9 — Existing data is not guessed at

**Nothing is cleared, rewritten or inspected.** Rows already stored keep their
dates exactly as they are.

Some of them carry the day they were recorded, because the old panel proposed
it. **There is no stored provenance that distinguishes those from a date
somebody typed deliberately** — a `published_on` equal to `created_at`'s local
day is as likely to be a publication recorded the same morning as it is to be an
accepted default.

So no migration guesses. A heuristic cleanup would silently destroy true dates
in order to remove guessed ones, which is a worse file than the one it started
from. The new rule is **prospective**, and a date somebody knows is wrong is
cleared by that person, through the correction control, as a recorded
correction.

## 10 — Displaying a date nobody knows

**Omit it, or say so, and never print an internal timestamp in its place.**

On the chronology, an undated publication prints **«Kuupäev teadmata»** — the
same three words a `Väline seisukoht` with no `stated_on` already prints,
because it is the same fact about the file and two spellings of it on one page
would read as two different situations.

**The row still appears.** Dropping it would hide a page that exists, which is a
worse answer than placing it approximately. So where the row *sits* and what it
*says* are two different answers: it sits on the day it was recorded, which is
the only day this system knows anything about, and it says «Kuupäev teadmata».
That is exactly the rule `external_position_chronology_day` already states — the
fallback places the row and never describes it.

`Meta.ordering` keeps `published_on DESC NULLS LAST, -created_at, -id`, so an
undated publication sorts into the tail behind every dated one, deterministically
and reproducibly between reads. **That is a technical ordering and nothing
else**: it places the row so a list does not reshuffle, and it states nothing
about when the page went up.

## 11 — Audit

Two new event types, `PROCEDURAL_LINK_RECORDED` and
`PROCEDURAL_LINK_CORRECTED`, following the existing conventions: named after
what happened, carrying the address itself because a history saying only «a link
was added» could not answer «which», and **absent from
`TIMELINE_EVENT_TYPES`** for the reasons §7 gives. No second timeline system is
created, and the forthcoming simplification of the substantive history has one
fewer thing to unpick.

For a publication, the two facts stay apart in the payload as well: the audit
event's own timestamp is when the record was written, and `published_on` is
`None` in the payload when the day is not known. **An audit payload is the last
place a guessed business date should appear**, because it is what a later reader
reconstructs the record from.

## 12 — Migrations

Three, and **none of them carries data**.

| migration | what it does |
| --- | --- |
| `matters/0027_procedural_link` | creates one table, with its constraints and one index |
| `matters/0028_overview_news_optional_publication_date` | one `RemoveConstraint` + one `AddConstraint` |
| `audit/0022_procedural_link_events` | one `AlterField` over a `choices` list — state-only |

**No backfill.** The obvious temptation is to mine the Matters this application
already holds for addresses — a URL in a `Märge`, a `Kaasamine`'s campaign link,
the free text of a historical register row — and file each as a procedural link.
Every one of those would be a guess about *what the address was for*, written
into a column whose whole content is what a lawyer meant by it, with nothing
recording that a system rather than a person had decided. A wrong `EIS` on a file
is worse than no link at all, because it is a wrong answer somebody will act on
without checking.

**No column change on `published_on`.** It has been `null=True, blank=True`
since `0022_matter_website_overview`; the requirement lived in the `CHECK` and
in the service, never in the column.

`0028` is strictly **weakening**, so it applies against a populated database
without inspecting a row. Backwards is the direction that can fail, and it
should: restoring the old constraint refuses while any row recorded under the
new rule has no date. That failure is honest — it says what a rollback would
have to decide first, which is a question only a person can answer.

## 13 — Parallel work, and why the create form is one line

`Uus teema` is being worked on by another package at the same time, and
`MatterCreateForm`, `matter_create.html` and `app/matters/views.py` are all in
its path.

The lawyer feedback asked for a procedural link *during creation* by name, so
the requirement is met rather than deferred — but the footprint is deliberately
minimal:

* `ProceduralLinkCreateForm` is its **own form**, appended to `forms.py`, not a
  set of fields on `MatterCreateForm`. `NextActionForm` already sits beside it
  on that page the same way.
* It carries a **`prefix`** (`menetlus-`) rather than an `auto_id`, so it
  namespaces the POST keys as well as the ids. A field name added to
  `MatterCreateForm` by the other package cannot reach this form's `clean`.
* The template is **one partial** and **one `{% include %}`** at the end of the
  form, clear of every other block.
* It is bound only when somebody typed an address, exactly as `Järgmine tegevus`
  is bound only when somebody asked for a step.

**One row, not a formset.** A Matter being created has one proceeding behind it
in the overwhelming majority of cases; the second and third addresses turn up
later, and the Teema page's own chip records those. A repeating control would
put an empty table on a form whose whole design is that nothing on it is
required.

**And it is folded**, which is not a detail. ADR 0088 — from the *same* round of
lawyer feedback — is about this exact page, and its complaint is that too much
on it asks for attention before a question has been answered: three blocks
expanded at once made the capture screen read as a survey again. A fourth,
permanently expanded, would be that complaint answered and re-created by the
very next package.

So this block takes the shape ADR 0088 gave `Valdkond`: one
`<details class="chipdetails chipdetails--field">`, a summary naming the thing,
and the answer beside the name once there is one — `Valdkond · Energeetika` and
`Menetluse link · Riigikogu: Eelnõu 123 SE` are the same affordance, for ADR
0088 §3's reason. A shut field is quieter than a chip row and two boxes; a shut
field that also hid *the answer* would be quieter and worse.

Three rules keep the fold honest, and each is tested:

* it is **shut on arrival**, and `data-stay-closed` stops the pre-selected `EIS`
  chip unfolding it — a chip nobody clicked is not an answer;
* the **summary says nothing until there is an address**, for the same reason;
* a refusal **this block owns** renders it open, server-side, so the box
  somebody has to correct is reachable with scripting off. A refusal somewhere
  else on the page leaves it shut, because the summary already says what it is
  holding — the position `policy_area_disclosure_open` takes, followed
  deliberately rather than re-argued.

**Atomic.** The Matter and its link land inside one `transaction.atomic()`: a
refusal anywhere takes both, a refused address leaves no Matter behind, and a
refused Matter leaves no orphan link. A refused save re-renders the bound form,
so the typed address survives — losing it is the defect this block is most prone
to, and it is the same one the file-upload path on this page already fixed.

**No draft Matter is created to hold the link**, which is ADR 0087 §4's rule
about the similar-matter finder applied here.

## Alternatives considered

**Five URL columns on `Matter`.** Rejected. It fixes the vocabulary in the
schema, makes a second ministry register unrecordable, puts five empty
placeholders on the page, and needs a migration every time the world adds a
source.

**A generic typed-link framework for the whole product.** Rejected as
over-building: four records already carry addresses for four different reasons,
and a shared *business* abstraction over them would be an abstraction over things
that are not the same. What they share is the URL validator, and that is already
shared.

**Deriving the kind from the hostname.** Rejected in §2.

**A `deleted_at` or a `RETIRED` state on a procedural link.** Rejected: a
lifecycle nobody maintains, on a record whose whole content is one pointer.
Correction reaches every field, including the kind.

**Keeping the publication date required and adding an «unknown» checkbox.**
Rejected. Absence already means unknown, and a boolean beside a nullable column
is two representations of one fact that will disagree.

**Migrating away the dates the old default filled in.** Rejected in §9.

**Leaving ADR 0085 §3's script default in place and only relaxing the
constraint.** Rejected. The refusal and the default were producing the same
wrong answer from opposite directions; removing one and keeping the other would
have left today in the box on every paste, with the refusal no longer there to
make anybody look at it.

## Consequences

* `Lisa teemale` gains a tenth chip, so the visual baselines that photograph the
  launcher move. The Teema page gains a rail card on Matters that carry a link,
  and `Uus teema` gains one block.
* One new table, one relaxed constraint, one `choices` list. No reindex, no
  archive rebuild, no data migration, and no release-note or deployment change
  in this package.
* An `Ülevaade / uudis` recorded from now on may carry no publication date, and
  surfaces that print one must tolerate `NULL`. The chronology, the strip, the
  page, search and the archive all do, and `tests/test_overview_news_unknown_date.py`
  holds each.
* One isolated JavaScript island is **removed** from `static/js/ux.js`, which is
  the direction AGENTS.md prefers.

## Reversibility

**The procedural link: high.** One table nothing else depends on, one launcher
chip, one rail card, one create block. Dropping it loses the recorded addresses,
which is why it would be a product decision rather than a patch — but nothing
else in the application reads the table.

**The publication date: high in the mechanism, and a decision to re-take in
substance.** Restoring the constraint is one migration, and it will refuse while
undated rows exist. Restoring the script default is the function this round
deleted. What is *not* reversible is the rows recorded in the meantime: a
narrowing would have to say what date they are supposed to have, and the honest
shapes are «existing rows are left alone and only new ones are checked» or «the
narrowed rule is a reviewed migration that names every row it would refuse» —
the same choice ADR 0085 §2's reversibility section names, for the same reason.
