# 0093 — A `Koja arvamus` carries its own `Märksõnad` and its own `Ülevaade / uudis` links

**Status:** accepted
**Date:** 2026-09-19

*Resolves the two questions ADR 0091 §6.5 and §8 left open*, and resolves them the
other way. That record listed five things lawyer feedback 13 asked for, implemented
three, declined one — per-opinion keywords — and deferred one — the association
between a specific opinion and the Chamber's publication about it. Both of those
answers were provisional by construction: the first said «if the department later
wants to search opinions by their own terms, the honest form of it is a decision
about the existing taxonomy», and the second said the relation «remains a
documented hook rather than a half-built column». The department has now decided,
and this record is that decision.

*Nothing else in ADR 0091 is reopened.* `Submission` stays the canonical outbound
advocacy action; `Pealkiri` stays the send's own title; `+ Koja arvamus` still asks
four questions and this record adds none to it; `Dokumendid` stays where a Matter's
opinions are managed (ADR 0061); `MatterWebsiteOverview` stays the one publication
activity with its three states (ADR 0081, ADR 0085, ADR 0089 §8). No `KodaOpinion`
model exists, no second publication model is added, and no second keyword
vocabulary is created.

## Context

ADR 0091 §6.5 declined per-opinion keywords for a reason worth restating, because
the decision below has to answer it: *a Matter already carries the classification
the department searches by, and a second narrower vocabulary on individual
Submissions would create two places a topic can be classified, two answers to «what
is this about», and a reporting question with no correct answer.*

The half of that which was right stays right, and this record keeps it: **there is
still exactly one keyword vocabulary.** What the argument got wrong is the
inference from one vocabulary to one *assignment*. Governed `taxonomy.Tag` is the
vocabulary; `matters.TagAssignment` is a Matter's use of it; and a Matter's use of
it is not the only use it can have. A file about the packaging act is tagged for
what the file is about. The opinion Koda sent on the first draft, the supplementary
letter six months later and the joint submission at second reading are three
letters that argued three things, and the department wants to find the letter, not
the file it hangs off.

§8 deferred the publication association because «relating one to a *specific*
`Submission` requires a relation that does not exist, and creating a publication
record here would collide with the package that owns publication». The collision is
over: `MatterWebsiteOverview` shipped, with its states, its constraints, its
services and its audit vocabulary. What is missing is only the relation, and it can
now be added without inventing anything about publication.

## Decision

### 1 — Per-Submission `Märksõnad` are governed `taxonomy.Tag`, explicitly assigned

A `Submission` may carry zero, one or many keywords, and every one of them is a row
of the **existing** `taxonomy.Tag` vocabulary. There is no free-text keyword
column, no per-Submission tag table with names in it, and no second lifecycle: the
same tags, the same aliases, the same merges, the same `is_active`, the same
governance.

The assignment is its own record — `submissions.SubmissionTagAssignment` — because
a Matter's tags and an opinion's tags are **two different facts about two different
records**. `matters.TagAssignment` points at a Matter and means «this file is
about». It is not reused and it is not widened.

**Nothing is inherited and nothing is inferred.** A Submission does not start with
its Matter's tags, does not acquire one when the Matter acquires one, and loses
none when the Matter loses one. No tag is derived from the opinion's title, from
the bytes of the file that went out, from its recipients, from its channel, from
its `Liik` or from any other record on the file. A person selects them or there are
none.

**No backfill.** Every `Submission` that exists on the day this ships carries zero
keywords, and goes on carrying zero until somebody opens it and chooses. Copying
the Matter's tags onto its opinions would manufacture, on every historical letter
in the register, a classification nobody made — which is the one thing this
product's import rules have refused from the beginning.

**Selection offers active canonical tags, and keeps what is already there.** A new
assignment resolves through `Tag.canonical()`, so choosing a merged spelling files
the assignment against the tag that carries assignments today, and a tag that is
not active after that resolution is refused. A tag *already assigned* to this
Submission stays selectable and stays ticked even after it is deprecated or merged,
because a historical assignment must not disappear from a record because the
vocabulary moved underneath it. That is the same bound-value union
`MatterEditForm` uses to keep a departed colleague a valid owner.

### 2 — `Submission` ↔ `MatterWebsiteOverview` is an explicit, optional many-to-many

One opinion may be linked to zero, one or several `Ülevaade / uudis` records, and
one `Ülevaade / uudis` may be linked to zero, one or several opinions. Both
directions are ordinary: a single write-up frequently covers the initial opinion
and the supplementary letter together, and a long proceeding is written up more
than once.

The relation is its own record — `submissions.SubmissionWebsiteOverviewLink` —
holding two foreign keys, who made the link and when. **It copies nothing.** No
URL, no title, no status and no date is denormalised onto it; the address and the
day the page went up are the overview's own columns and are read from there.

**Both endpoints must belong to the same Matter.** A link whose overview belongs to
another Matter is refused, and the refusal takes the whole operation with it: a
save naming three overviews of which one is foreign writes none of them. The rule
is enforced at the canonical service boundary, under the same transaction as the
write, and restated in `clean()` for the admin and for anything constructing a row
directly. It is deliberately **not** a database trigger and deliberately not a
denormalised `matter_id` on the link row: this repository does not emulate
cross-table CHECK constraints that way, and a copied `matter_id` is one more column
that can go stale.

**Nothing is inferred and nothing is backfilled.** No link is created from a
matching URL, a matching title, a shared date, chronological proximity, a shared
document, a shared tag, a status or anything else. Every `Submission` and every
`MatterWebsiteOverview` that exists on the day this ships is linked to nothing.

**Every state participates.** A `Plaanis` row is a real record of a write-up this
file owes, and linking the opinion it will cover is exactly the moment somebody
knows which opinion that is; `Tühistatud` is a real record of a plan that was
dropped. So no status restriction is invented here. The only rules the relation
answers to are the ones `MatterWebsiteOverview` already has, and linking one
neither publishes it, cancels it, nor gives it an address.

### 3 — Both edits are metadata corrections, and both are audited

Four new `ChangeEventType` values — `SUBMISSION_TAG_ASSIGNED`,
`SUBMISSION_TAG_REMOVED`, `SUBMISSION_OVERVIEW_LINKED`,
`SUBMISSION_OVERVIEW_UNLINKED` — one per fact that actually moved. Adding two
keywords and removing one is three facts and three rows, which is the rule
`set_tags` already keeps for a Matter.

**A no-op writes nothing.** Saving a form without changing anything produces no
`ChangeEvent`, because an audit trail in which «somebody looked at this» is
indistinguishable from «somebody changed this» answers neither question.

**The payloads carry identities and names, never content.** A tag's key and name;
an overview's identity and status. The opinion's `notes`, the lawyer's own
assessment and the contents of the file that went out appear in none of them.

None of the four is in `TIMELINE_EVENT_TYPES`. Classifying a letter is not authored
chronology, exactly as `TAG_ASSIGNED` and `TAG_REMOVED` are not; the professional
narrative stays what happened to the file.

**Removing a link or a keyword is a correction, not a deletion.** It removes the
assignment row and nothing else: the `Submission` keeps its status, its `sent_at`,
its recipients and its immutable final evidence, and the `MatterWebsiteOverview`
keeps its status, its URL and its `published_on`. Neither endpoint is deleted,
cancelled, published or rewritten by any operation in this record.

**The closed-Matter contract is not special-cased.** Both edits are
`@business_write_required` and take no open-Matter lock, which is exactly what
`MatterEditForm` already does when it writes a Matter's own `Sildid` through
`set_tags`, and what `withdraw` already does on a send. Correcting how an existing
record is classified is not new canonical business content.

### 4 — Visibility is asked of each endpoint, independently, before anything is shown

`Submission` and `MatterWebsiteOverview` are both `VisibilityInheritingModel`
children and each may be restricted below its Matter. The relation between them
**grants nothing**:

* the candidate list on the edit surface is scoped with
  `MatterWebsiteOverview.objects.visible_to(viewer)`, so a restricted overview is
  not offered, not counted and not hinted at;
* a POST naming an overview this actor may not see is refused exactly as a POST
  naming one on another Matter is — the write resolves its endpoints through the
  same scoped queryset the form built its choices from, so a guessed identifier
  reaches nothing;
* the reciprocal list on an `Ülevaade / uudis` reads
  `Submission.objects.visible_to(viewer)`, so a restricted opinion contributes no
  row and no count to a reader who may not see it;
* the list of keywords on an opinion is only ever rendered on a `Submission` the
  reader can already see.

One consequence is stated because it is a rule rather than an accident: **an actor
can only unlink what they can see.** Removals are diffed against the links whose
overview is visible to the actor, so a save by a reader who cannot see a restricted
linked overview leaves that link exactly where it is instead of silently destroying
a relation they were never shown.

### 5 — Search is not redesigned, and `INDEX_VERSION` does not move

`Submission` keywords are **not** projected into the search corpus, and this is a
decision rather than an omission.

The evidence is the existing contract. `app/search/child_indexing.py` builds one
row per child record from *that record's own columns*: an `Entry` contributes its
body, its author and its organisation; a `Submission` contributes its title, its
`reference`, its recipients and their aliases, and its `notes`. **No child row
carries taxonomy.** Tags reach the corpus once, through the Matter's own projection
(`TagAssignment` in `_alias_text_for`), which is where the classification of a file
belongs and where a search for a keyword already finds the file — and `Teemad` is
the one discovery surface (ADR 0071).

The precedent is explicit and recent: `MatterExternalPosition` is not indexed
(ADR 0084 §5) and `MatterProceduralDevelopment` is not indexed (ADR 0091 §9), each
for the reason that a short structured child fact's readers are the chronology and
the Matter page. A Submission's keywords are the same kind of fact.

And the arithmetic makes the alternative absurd on the day it would ship. There is
no backfill, so on deployment day **every** Submission carries zero keywords:
bumping `INDEX_VERSION` would rebuild the entire corpus in order to index nothing —
precisely what ADR 0091 §9 refused. `ARCHIVE_INDEX_VERSION` is untouched; no
archive-index contract changed.

No search filter, no facet and no new query parameter is added. If the department
later wants to search opinions by their own keywords, that is a decision about the
search contract, taken then, with a rebuild planned for it.

## Consequences

**Two additive tables, and nothing else moves.** One migration in `app.submissions`
creating `SubmissionTagAssignment` and `SubmissionWebsiteOverviewLink`, each with
its unique constraint. No column is altered, no data is written, no existing row is
touched, and the migration is reversible by dropping two empty tables.

**`Tag` deletion stays protected.** `SubmissionTagAssignment.tag` is `PROTECT`,
like `TagAssignment.tag`: a governed vocabulary row that something is classified by
cannot vanish under it. Both link foreign keys to `Submission` and to
`MatterWebsiteOverview` are `CASCADE`, because both endpoints already cascade from
`Matter` and a `PROTECT` on either would make deleting a Matter fail on a metadata
row.

**One edit surface, reached from the opinion.** `Arvamuse märksõnad ja seosed`
(`/arvamused/<id>/andmed/`) edits exactly these two facts and nothing else. It is
deliberately not a second opinion editor: it cannot change a title, a recipient, a
date, a status or a file, and the send workflow is untouched. The keywords and the
linked write-ups are shown read-only on the opinion's own row, behind the `⋯` that
already carries `Saatmise andmed`.

**Reversibility.** Dropping the two tables removes every assignment and every link
and leaves both endpoints exactly as they were. Nothing else in the product reads
them: no statistic, no search row, no work item, no deadline, no export and no
projection depends on either, so removing them changes no count.
