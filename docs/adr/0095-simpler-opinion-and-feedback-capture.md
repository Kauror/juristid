# 0095 — Simpler opinion and feedback capture on the Teema page

**Status:** accepted
**Date:** 2026-09-19

Five simplifications to four `Lisa teemale` panels, from one round of owner
feedback on the page lawyers use most. Two additive columns, no destructive
change, no data rewritten, nothing inferred.

1. `+ Koja arvamus` asks for a searchable `Adressaadid` control that opens on the
   Teema's `Saatja`, and for a `Kokkuvõte` instead of a `Pealkiri`.
2. `+ Teiste arvamus` and `+ Meile saadetud tagasiside` ask for one exact date
   instead of four precision chips.
3. Both of those panels lose `Juristi märkus` and `Seotud kaasamine` from
   creation, and the received-feedback one loses `Allikas`.
4. `+ Meile saadetud tagasiside` gains `Liige`.
5. `+ Ülevaade / uudis` becomes a day and an address, and a valid save is a
   publication.

## Context

Every one of these panels was designed correctly for a case that is real and
rare, and used daily for a case that is common. The pattern repeats in all four:
a decision recorded in an earlier ADR bought the ability to record an unusual
fact, and paid for it with a question in front of every ordinary save.

That is not an argument for deleting the capability. It is an argument for
deciding *which surface* asks for it. Every fact named below is still storable,
still rendered, and still correctable through `Muuda` — the surface a person
reaches when a record is wrong, which is exactly when the unusual case comes up.

## Decision

### §1 — `Adressaadid` is the shared picker, and it opens on `Saatja`

ADR 0091 §6.3 drew the whole catalogue as a chip row and refused a default, on
two arguments.

The first was that registering a letter Koda sent is not the moment to invent the
institution it went to. That is an argument about a careless `+`, and the picker's
is not careless: typing is a query, `+` is a proposal, and only the save creates —
through `resolve_organisation_name`, inside the save's own transaction, reusing an
exact or alias match and refusing a spelling that names two (ADR 0073). What the
chip row produced instead was a body genuinely missing having to be added on
another page, with the half-filled panel abandoned to get there.

The second was that an opinion at second reading goes to a Riigikogu committee
rather than to the ministry that sent the draft. That is true, and it is an
argument against a default the *save* applies rather than against one somebody can
see. The senders are now ticked in the control before anything is saved, where
they can be read, removed, replaced and added to — the one shape ADR 0078 §2
allows a date or a value default to take, applied to a recipient list.

Three properties are load-bearing:

- **it is an initial value, not a write.** `Matter.source_organisations` is not
  touched by this save and is not read back from it. `SubmissionRecipient` and
  `Saatja` remain two facts about two different acts;
- **it applies to an unbound form only.** Re-rendering a refused save shows what
  was posted. Reapplying the default there would resurrect a recipient somebody
  had just removed;
- **several is still ordinary.** One letter goes to two bodies, which is what
  `SubmissionRecipient` has always held, and a checkbox group already behaves as
  «several» without the control having to be told.

### §2 — `Kokkuvõte` replaces `Pealkiri`, in its own column

`Submission.title` is that record's *identity*: `NOT NULL`, printed in the
outbound register's cell, carried by the document the bytes live under, and what
a colleague scans a list of sends by. A lawyer who has just sent an opinion knows
what the Chamber argued and does not know what to call it, so the box asking for
a name got the file's name, nothing, or a sentence trimmed to fit a heading.

So the question changed and the answer has somewhere truthful to go:

- **a new `Submission.summary`, additive and blank on every existing row.** Not a
  renamed `title`, because a paragraph is not an identity. Not `notes`, because
  «märkused» is bookkeeping beside the record and is already what the search
  projection reads as the body — a reader looking for what Koda argued must not
  have to guess which of two boxes the last person used;
- **`title` on this path is the uploaded file's own name.** That is not new:
  `add_matter_koda_opinion` has taken it from there whenever the title box was
  left blank since the panel was written. The panel simply no longer offers the
  box. Nothing is read out of the file's *contents*, nothing is generated, and no
  headline is cut from the first characters of the summary;
- **nothing is backfilled.** Every Submission recorded before this column existed
  has an empty one, which is what is true of it.

`Kokkuvõte` renders where a reader asks what the opinion said — first in the
chronology's sub-line, as `Seisukoht` is for an external position, and for the
same reason. The technical identity stays where a technical identity is needed.

**`INDEX_VERSION` moves because of this and only this.** Until now the
descriptive sentence about a sent opinion went into `title`, which the search
projection indexes in the identity tier — so «pakendiseaduse üleminekuaeg» found
the opinion that argued it. Moving that content to `summary` without indexing it
would quietly retire a search that works today, so `summary` joins `notes` in the
projection body and the version bump makes pre-release rows ineligible until the
one-time rebuild runs. `ARCHIVE_INDEX_VERSION` is untouched: the archive's
contract did not change.

### §3 — One date on the two creation panels

ADR 0079 §11 and ADR 0084 §2 gave external positions the four-chip period
composer, because a ministry's paper remembered as «kevadel 2019» had an invented
day or an empty field before it. That case is real and is overwhelmingly a
*correction*: the position being filed through these panels is feedback that
arrived this week, on a day the person knows.

So the creation panels ask one exact date, opening on today and clearing to
`NULL`; `Muuda` keeps all four chips and every stored `MONTH`, `QUARTER` and
`YEAR` row keeps its precision and its rendering. `DatePrecision` is untouched.

The same reasoning retires `Juristi märkus` and `Seotud kaasamine` from creation.
Two substantive boxes invited a decision about which half of a thought goes where,
and a `Kaasamine` select stood in front of somebody recording two sentences from a
member association. `Seisukoht` is now the whole of what a new record says;
`lawyer_note` is blank and `engagement` is `NULL` on everything these panels
write. **Stored notes and stored links are untouched, still render under their own
labels, and are still never concatenated into the position** (ADR 0091 §4).

### §4 — `Allikas` off creation, `Liige` onto it

ADR 0091 §3.3 made the organisation optional on received feedback so that an
aggregate answer — a survey of 234 companies producing 58 replies — could be filed
under an `Allikas` naming the collection rather than under an invented
organisation. The case is real. What it cost was a question with two right answers
at the top of the panel a department fills in several times a week.

So the creation panel names an institution. **The authorship rule is satisfied the
way it has always been satisfied and is not relaxed**: `source_label` stays on the
model, every row carrying one keeps it and renders it, both `CHECK`s stand, and
`ExternalPositionEditForm` still offers the box — per record, because it decides
from the record rather than from its class.

In its place, `Liige`: this feedback came from a Chamber member.

- **explicitly ticked, derived from nothing.** Not the membership registry, not
  the CRM, not the organisation's name, not an address domain, not other feedback
  on the file;
- **not recomputed.** An organisation leaving the Chamber next year does not make
  last year's feedback stop having come from a member. There is no signal, no
  trigger and no job attached to the column;
- **received feedback only.** A position Koda found published somewhere was not
  written to Koda at all, so there is no question for the box to answer. The box
  is absent from the other panel, the service refuses a `True` on any other
  provenance, and a `CHECK` refuses it under the row lock — three defences, not
  one;
- **not backfilled.** Every existing row gets `False`, which here means «nobody
  said» as much as it means «not a member».

No search facet is added for it in this release.

### §5 — `+ Ülevaade / uudis` records a page that exists

ADR 0081 §1 gave the panel one button and no fields; ADR 0083 added two optional
boxes so a lawyer recording an already-published page did not have to file a plan
and then publish it. What that left was three answers, one of which — *neither
box* — was reached by pressing `Salvesta` on a form that looked untouched.

A save whose meaning depends on what somebody did not type is the one shape a
composer may not have. The address is required, a valid save is `PUBLISHED`, and
the day opens on today.

That last point narrows ADR 0089 §8, which refused a default here. What it
refused specifically was the withdrawn `data-publication-default` island: a date
appearing in the box the instant somebody pasted a link, which is a date they
accept without reading. This one is in the box before anything is typed, and an
emptied box still stores `NULL` and still reads «kuupäev teadmata».

**`PLANNED` and `CANCELLED` are untouched.** Every stored plan reads on the file,
carries `Avalda` and `Tühista`, and `plan_website_overview` still writes one. What
is gone is a silent way to create one from this panel.

## Form default versus canonical inference

The three date defaults in this record are **form defaults**. They mean: this box
opens holding today, visibly, before anything is saved, and the person may change
or clear it.

They are not inferences. Specifically, and deliberately:

- no historical row gains a date it did not have;
- no import gains one;
- an unknown date is still `NULL`, still `EXACT`, and still reads «kuupäev
  teadmata»;
- an approximate historical date keeps its precision.

The same distinction governs §1's recipient default: the control opens holding a
suggestion, and nothing about the Matter is written because of it.

## Consequences

Two additive migrations, both carrying no data: `submissions.0008_opinion_summary`
and `matters.0031_external_position_member`. One `CHECK` added, validated against
the existing table, which every stored row already satisfies. One `INDEX_VERSION`
bump requiring the established one-time search rebuild after deployment.

Nothing is removed from any model. `source_label`, `lawyer_note`, `engagement`,
`stated_on_precision`, `Submission.title`, `PLANNED` and `CANCELLED` all remain,
and every surface that reads them still does.

The panels reverse or narrow parts of ADR 0078 §2 (in its favour), ADR 0081 §2,
ADR 0083, ADR 0084 §2, ADR 0089 §8, and ADR 0091 §3.3, §4, §6.3. None of those
records is rewritten: each was right about the case it described, and this one
records which surface now asks the question.
