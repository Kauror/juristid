# 0084 — `Väline seisukoht` is a dedicated Matter activity, and its source is the boundary

*Accepted 2026-09-15.*

A lawyer working a file learns, constantly, what other people think about it.
The ministry publishes a press release. An association sends its position paper.
A member organisation answers a consultation in writing. None of that is Koda's
opinion, and all of it is the material the Chamber's own position is argued
against.

The file has never been able to hold any of it. The address lived in a browser
history, the PDF lived in a mail folder, and the question a colleague actually
asks — «kes on selle kohta midagi öelnud, ja kus see on» — had no answer on the
Teema at all.

This records a dedicated Matter activity for it: one organisation, one source
minimum, an optional date at the precision it is known to, and deliberately
nothing else.

## 1 — Why it is its own record

Six things on this product could have absorbed it, and each would have lost
something the department relies on.

A **`Märge`** is narrative. «Rahandusministeerium avaldas oma seisukoha» written
as prose is a sentence nothing can ask a question of: it cannot be listed, the
link inside it cannot be rendered safely, and the PDF that came with it has
nowhere to go but the Matter at large.

A **`Submission`** is what Koda *sent*. Recording what a ministry published as
an outbound opinion is the opposite claim, and it would corrupt every submission
statistic the department reports on. The same is true of `Tulemuse tõend`.

A **`Töövõit`** is a reviewed judgement that Koda changed something. Somebody
else's position is not a claim that anything was won — and a position *against*
the Chamber is exactly as ordinary a record as one for it, which a victory
model has no way to express.

A **`NextAction`** and a **response deadline** are work with a date. An external
position is something that has already happened; a record that generated a task
would make every Matter carrying one read as owing something. That is the
mistake docs/adr/0078 §3 refused for `feedback_deadline` and docs/adr/0081 §4
refused for a planned overview, and it is refused here for the same reason.

A **`Document` alone** is bytes with a role. `DocumentRole.EXTERNAL_POSITION`
has existed since the foundational schema and nothing has ever written it —
because a document cannot say *whose* position it is, *when* it was stated, or
that the position exists at all when it was only published on a web page. The
role is part of the answer and it was never the whole of it.

And it is not a **`Kaasamine`**, although that is the closest of the six. An
engagement records that Koda **asked** somebody something; this records that
somebody else **said** something. The first has an audience, a response count
and a reply-by date, none of which means anything here; the second has an
author outside this organisation and a source, neither of which means anything
there. One model carrying both would need every column to be optional, and a
record whose every field is optional is a record that cannot say what it is.

The two are *related*, though, and that is §4: a position frequently arrives
because of a round Koda ran, and `engagement` says so without merging them.

So: `MatterExternalPosition`, beside `MatterEngagement` and
`MatterWebsiteOverview` in `app.matters`, with its own service functions and its
own audit events. **No new framework.** It is a Matter child record written
through named use cases, exactly like every other structured fact on this page;
the launcher, the lock discipline, the visibility inheritance, the evidence
pipeline, the audit model and the chronology projection are all the existing
ones.

A Matter may carry **zero, one or many**, including several from one
organisation — a ministry that states a position at consultation and again at
second reading has stated two, and nothing is unique on `(matter, organisation)`.

## 2 — What it asks, and what it deliberately does not

Two required answers and four optional ones.

**`Organisatsioon` is required**, from the one shared catalogue that already
answers `Saatja` and `Adressaat`. A position with no author is an anonymous
claim on a professional file. The control is the one docs/adr/0073 built — a
search box and a `+` — and not a ninth way of naming an institution: typing is
a query, `+` is a proposal, and `resolve_organisation_name` inside the save's
own transaction is the only thing that creates. There is deliberately **no
«Määramata» chip**: that is a real answer to *who was this addressed to* and is
not one to *whose position is this*.

**A source is required**, and §3 is the whole of that rule.

**`Seisukoha kuupäev` is optional and carries its precision.** A position found
months later frequently has no date anybody could defend, and one remembered as
«kevadel 2019» had two answers before this — an invented day or an empty field —
and both are worse than the one the person has. So the four precisions of
docs/adr/0079 §1 are offered through the same composer every other period on
this product goes through, an emptied box stores `NULL`, and the database
refuses a precision on a row with no date, because absence has no precision.

**The date box carries no default**, unlike `+ Kaasamine`'s. A consultation is
usually written up the day it happens, so today is the useful default there; a
position is usually found and filed some time after it was stated, so a
pre-filled today would be a date nobody chose sitting one `Salvesta` away from
being saved as another organisation's timetable (docs/adr/0078 §2).

**`Selgitus` is optional and short.** It is the line that lets a colleague
scanning the chronology decide whether to open the source. It is not a summary
of the document — the document is attached — and nothing extracts, generates or
indexes it.

**No title, no kind, no stance and no weight.** A headline would be somebody
writing a second name for a page that already has one. A `toetab` / `vastu`
field would be this office classifying another organisation's position from one
reading of it, on a record whose whole point is that the source speaks for
itself; when the department asks for that, it is a new column with its own
vocabulary and its own ADR, not a reuse of `Selgitus`.

## 3 — The source: a public address, an attached document, or both

**One of the two is required and either alone is enough.**

The URL is `http` or `https` and nothing else — the allow-list
`normalize_engagement_url` already keeps, shared rather than copied, because
`javascript:` and `data:` are script delivery dressed as an address and `file:`
and `ftp:` point somewhere the reader's browser cannot usefully follow. It is
checked against the **parsed host**, so `https://user:pw@/uudised` — a non-empty
authority with no host at all — is refused rather than stored, and a credential
in a link never reaches an audit payload, a rendered page or a browser history
(red-team finding F-2). An over-long address is **refused, never truncated**: a
link cut off at a thousand characters is a link that no longer resolves
(finding F-1).

**There is no host allow-list, and that is the difference from
`normalize_koda_website_url`.** That one guards the Chamber's own site and can
name it. This records where another organisation published, and the set of
those is every institution in Estonia and the EU; a list would be a list
somebody has to maintain, and the day a ministry moves domain the file would
refuse to record what actually happened.

**The document is an ordinary attached `Document`.** The existing upload,
`Document`, `DocumentVersion`, visibility and immutability pipeline, linked
through the existing `DocumentLink` architecture — no copied files, no
separately stored extracted text, no second evidence store. `DocumentLink` gains
a sixth typed target column, which is the documented cost of typed columns over
a generic target and is paid here rather than avoided (docs/adr/0075 §6).

**The files carry `DocumentRole.EXTERNAL_POSITION`**, and this is the one
workspace operation whose uploads are not `OTHER`. Every other panel captures
*supporting evidence for something Koda did*, where the button a file arrived
through is not a business role and inventing one to record where it came from is
what the link exists to avoid. Here the document **is** the position: a
ministry's paper filed against a Matter has a role the product already named and
has never written.

**The rule cannot be a database constraint** — the address is a column on this
row and the document is a row in another table, and a `CHECK` sees neither of
the other. So it lives in `record_external_position`, decided *before* the
insert from the count of files the caller is about to capture, and the capture
runs in the same transaction: a position that promised a file and whose file was
refused unwinds with it. A correction asks the same question again, against what
the save would result in, and reads the link table rather than assuming —
emptying the address of a position that carries a document is an ordinary
correction, and emptying the address of one that carries nothing else is refused.

**The visibility boundary is the existing one, twice over.** A position inherits
the Matter's visibility and may be more restrictive, never less; a document link
is readable exactly when *both* of its ends are, which is what
`DocumentLink.visible_to` computes. Neither is new and neither is relaxed.

## 4 — The optional `Kaasamine` relation

`engagement` is nullable and must stay so. The commonest external position is
**unsolicited** — published because the ministry chose to publish it, with no
round of ours behind it — and a required relation would make the commonest kind
unrecordable.

Where it is set it says something no other column can: that this is a reply to a
consultation Koda ran. Both ends must belong to one Matter, refused in the
service because a `CHECK` cannot follow a foreign key — the same division of
labour, and the same refusal rather than a repair, as `link_document_to_record`.
The form narrows its own queryset to this Matter's engagements *as this reader
sees them*, so a crafted POST naming a round on another file, or one restricted
below the Matter, is refused by the field as well.

`on_delete=SET_NULL`: the position is a fact in its own right and survives a
round it happened to answer. Nothing in this product deletes an engagement, so
that is the honest answer to a case that does not arise rather than a cascade
that would take factual records with it.

**It is not a response obligation and not a count.** Nothing asks «how many of
the people we asked have answered», nothing compares this to
`MatterEngagement.response_count`, and a round with ten positions filed against
it is not thereby «answered». That is a product question nobody has been asked.

## 5 — Where it reads, and the places it deliberately does not

**One surface: the chronology.** A position renders as

```
Väline seisukoht: Rahandusministeerium            14.3.2026
Toetab eelnõu, kuid soovib pikemat üleminekuaega. · Vastus kaasamisele: liikmed
rahandusministeerium.ee                            seisukoht.pdf
```

projected from the canonical record through `projected_milestones`, like every
other structured fact since docs/adr/0074 §14, so the audit events are not
rendered beside it and one act takes one line. The date reads at the precision
it was recorded to, or as «Kuupäev teadmata» — never as the day somebody typed
it in, and never as the anchor of a period (docs/adr/0079 §2, §3). A position
dated in the future is not there: the chronology reads newest-first and means
*past*.

**The link is named by its host**, `rahandusministeerium.ee`, or by
`Ava seisukoht` where the address has no host to name. A raw URL as a row's own
text is a line a reader has to parse instead of read, and it is the one shape in
which a look-alike address would be believed — the rule a published
`Kodulehe ülevaade` already follows. `target="_blank"`, `rel="noopener
noreferrer"`, and a visually hidden «— avaneb uues aknas» so the new tab is
announced rather than merely happening.

**No standing strip.** Unlike `Kodulehe ülevaated`, there is nothing here that
the file *owes*: a position is a completed fact, not outstanding work, so there
is no list of them to act on and no permanently visible empty section
(TEEMA_TARGET_SPEC §F).

**Nothing else touches it in v1**, and each absence is a decision:

* no `NextAction`, no `MatterImportantDate`, no effect on
  `Matter.response_deadline`, no lateness and no work item — so a Matter
  carrying ten of these never appears in anybody's queue and never reads as
  late;
* no row in `Minu asjad`, `Tähtajad`, `Ülevaade` or the department work lists;
* no work-victory metric, no submission metric, no count, no statistic, no
  filter, no register sort, no badge;
* **no new metadata source for search and no archive projection.** A linked
  document keeps its existing document-search behaviour unchanged — it is a
  `Document` like any other — and the position record itself is not indexed. The
  organisation, the address and the `Selgitus` answer questions about one file,
  and indexing them would answer queries nobody meant to ask;
* the `Uus teema` intake reader does not offer it. A Matter is written up after
  work has happened, not at the moment it is created.

## 6 — `Lisa teemale` gains a ninth chip

`+ Väline seisukoht`, between `+ Kodulehe ülevaade` and `+ Lõpeta teema` —
last of the capture operations, because closing a file is not one of them. The
bar's stability contract is unchanged: the chip is a control, the form is a
separate element, the order is fixed, and the browser enforces one open panel
through one radio `name` (docs/adr/0078 §1). Nothing about the shared launcher
is redesigned; it has one more chip in it.

A recorded position is corrected in place on its chronology row, through the
same `Muuda` interaction a `Kaasamine` already has and the same partial shape —
the row is the swap target, so a correction cannot move the line, add a second
one, or turn a milestone into a work entry.

## 7 — The audit trail

Four event types, because four different things happen to this record:

| event | what it means |
| --- | --- |
| `EXTERNAL_POSITION_RECORDED` | this organisation's position is on the file |
| `EXTERNAL_POSITION_CORRECTED` | the file was wrong about who, when, what or which round |
| `EXTERNAL_POSITION_SOURCE_CHANGED` | where it points, and where it pointed before |
| `EXTERNAL_POSITION_DOCUMENT_LINKED` | these bytes are the evidence for this position |

Each carries the actor, the timestamp the audit model already stamps, and the
small scalar values that moved — both the old and the new value for the
organisation, the date, its precision and the linked round, and both addresses
in full on a source change. The source has its own event rather than a field
name inside the correction because it is the one change a reader is most likely
to be auditing: an address that quietly became a different page is the one way
this record can lie. It is written on creation as well, so a history whose only
«where does this point» rows were corrections is not the only history a record
that was never corrected has.

`EXTERNAL_POSITION_DOCUMENT_LINKED` is beside `DOCUMENT_CREATED` and
`EVIDENCE_VERSION_ADDED` rather than instead of them: those two say bytes
arrived on the Matter, and neither can say what they are the evidence *for*.

None of the four is in `TIMELINE_EVENT_TYPES`, because the chronology renders
the position from the canonical record and reading the event as well would state
one act twice. All four are in `app.audit.visibility`'s child families, so an
event about a restricted position is scoped by that position and not only by its
Matter.

## 8 — A closed Matter, and corrections

**No new position on a closed Matter**, refused under
`lock_open_matter_for_business_write` on the route rather than by the page not
rendering the panel: a browser that had the Teema open before somebody else
closed it still has every field and every button, and its POST reaches a server
with no memory of which page it came from (R2-02).

**No deletion at all**, on an open Matter or a closed one. There is no route, no
service and no soft-delete state. A mistaken position is corrected, because what
the file recorded and who recorded it is part of the file — the rule
`MatterEngagement` has kept since it was written.

**Corrections are refused on a closed Matter too**, and this is the point on
which the brief left a choice. The existing architecture supports a correction
on a closed file in exactly one narrow shape — `correct_website_overview_link`,
which may run there *because the transition it performs cannot create anything*:
it moves an address on a row that is already published and refuses every other
state (docs/adr/0081 §5). No such guarded half exists here. Every field on this
record is substantive — the organisation, the date, the source, the round it
answered — and correcting any of them is what `correct_engagement` calls normal
interactive business work, which a finished file refuses (docs/adr/0076 §2). So
this record follows `Kaasamine` and not `Kodulehe ülevaade`: **reopening is
required**, and it leaves somebody's name on both decisions. Narrowing that
later — a rule under which some field is correctable on a closed file — is
additive and needs a product decision this round did not have.

**Closure does nothing to the positions a Matter already carries.** Unlike a
planned overview, there is no outstanding obligation to cancel: a position is a
completed fact, and a closure that rewrote one would be the file changing what
another organisation said.

**Corrections observe the project's optimistic concurrency.** A rendered form
carries the record's revision — `updated_at`, the token `personal_note_revision`,
`entry_revision_token` and `website_overview_revision` already use — and a save
whose token is not the stored one raises `ExternalPositionConflict` and **writes
nothing**: not the metadata, not the source link, not half of either. The token
is compared after the row lock, so the version compared against is the committed
one, and before anything else is decided, so a refusal leaves nothing behind.
The page answers 409 with the form still holding what the person typed, and does
not advance the hidden token: adopting the newer one would be the view deciding
that the next submit may overwrite what the other writer saved (QA-09).

## 9 — The migrations

Three, all additive, none of them carrying data.

`matters/0023_matter_external_position` is one `CreateModel`.
`documents/0010_document_link_external_position` adds one nullable foreign key
and recreates the one `CHECK` that enumerates the link's target columns by hand.
`audit/0019_external_position_events` is one `AlterField` over a `choices` list,
which is Python metadata and not a database object.

**No `RunPython`, no `RunSQL`, no backfill and nothing to backfill from.** The
record did not exist, so every Matter has zero of them; every existing
`DocumentLink` reads back `NULL` in the new column, which is the truthful answer
because none of them is about a position. Deriving one from a `Märge` naming a
ministry, from a document already filed under the `EXTERNAL_POSITION` role, or
from a URL somebody once pasted into a note would put another organisation's
stated position on the file when nobody stated that it was one.

## Alternatives considered

**A `kind` on `MatterEngagement`.** Rejected in §1: the two records answer
opposite questions, and one model would need every column optional.

**A generic «Matter link» activity** with a type and a URL, so the next external
pointer costs no table. Rejected for the reason docs/adr/0081 rejected it: a
generic container makes every consumer branch on `kind`, and the validation that
matters — *this* source rule, *this* organisation requirement, *this* rendering
— has nowhere to live but in those branches.

**The document alone, with a role and a title.** Rejected in §1. It cannot say
whose position it is or when it was stated, and it cannot exist at all for a
position that was only published on a web page.

**Requiring both a link and a document.** Rejected: half the real cases have
one and not the other, and a rule that refused them would be a rule people work
around by putting the address in a note.

**A stance field — `toetab` / `vastu` / `osaliselt`.** Rejected in §2 as this
office classifying another organisation's position, on a record whose whole
point is that the source speaks for itself.

**Indexing the position for search.** Rejected for v1 in §5. The linked document
is already searchable as a document, which is the search people actually
perform; indexing the record would add a second kind of hit for the same
material.

**Allowing the correction on a closed Matter**, as `Kodulehe ülevaade` allows
one. Rejected in §8, with the narrow reason the overview's exception exists at
all.

## Consequences

* `Lisa teemale` offers nine operations rather than eight. The bar's stability
  contract is unchanged and the chip order is fixed (docs/adr/0078 §1).
* The visual baselines that photograph the launcher change, and only because
  the row has one more chip in it.
* `DocumentLink` has six target columns rather than five, and the next kind is
  still a migration.
* The chronology gains one milestone kind and one correctable row.
* Nothing that counts, reports, indexes or schedules changes at all.

## Reversibility

High. The table is new and empty, nothing else reads it, and no existing column,
projection or statistic was touched. Removing the feature is three migrations
and the deletion of one panel, one chronology branch and one row partial. Adding
search, a stance vocabulary, a response-obligation reading of the `Kaasamine`
relation, or a closed-Matter correction rule is additive in each case, and §2,
§4, §5 and §8 say what each would have to decide.
