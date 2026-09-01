# ADR 0055 — The opinion archive is department work product, not a migration tool

- Status: proposed
- Date: 2026-09-01
- Stage: opinion corpus reconciliation
- Related: ADR 0028 (the development archive workspace, whose access matrix
  this replaces), ADR 0042 (department-wide lawyer access, whose set this
  finally reaches), ADR 0047 (Arvamused as a section of Teemad), ADR 0037 (the
  business-write HTTP boundary), ADR 0034 (persona candidates)
- Number: 0055, stacked on 0054 from the same round.

## Context

*Teemad → Arvamused* shows `Saadetud 1`. That figure is correct — production
holds exactly one canonical Submission — and it is not what the department
needs to see, because the application also holds 767 real outgoing Koda
letters. They are one tab away, and for most of the department that tab does
not exist.

`may_read_archive` admitted `DEPARTMENT_HEAD` and `ADMINISTRATOR` behind the
shared gate, and `ADMINISTRATOR` alone outside it. That was written when the
archive was a migration artefact nobody had filed yet, and two things have
happened since.

**ADR 0042 moved the confidentiality boundary to the application.**
`SPECIALIST` and `DEPARTMENT_HEAD` became one set that reads department-wide,
including every `RESTRICTED` Matter these letters are filed onto. This module
predates that decision and was left behind by it, and the gap is not
theoretical: a specialist could open the Matter, its timeline, its documents
and their filenames, and could not open the Chamber's own outgoing letter about
it. 767 letters, unreadable by the people whose work they are.

**The narrowing outside the shared gate points the wrong way.** As written, the
whole department loses the archive on the day Cloudflare Access replaces the
shared password. That is backwards. Access authenticates the *individual*
better than a shared password does; it does not make a lawyer less entitled to
their own department's correspondence.

## Decision

**One reader set, asked in every authentication mode.** `ARCHIVE_READERS` is
`ROLES_WITH_RESTRICTED_ACCESS` plus `ADMINISTRATOR` — the two lawyer roles
because the letters are their work, and the administrator because operating the
reconciliation means reading what is being reconciled. `is_shared_gate` no
longer branches the read decision, which is also one fewer thing that can
disagree with itself.

**Writing did not move, and the asymmetry is the point.**
`may_manage_archive_links` and `may_use_opinion_queue` are untouched. A
specialist may now open every historical letter and still may not say which
Matter one belongs to; the administrator may read the corpus and still may not
make that claim either. **Reading the department's own correspondence is not a
privilege; asserting what it concerns is** — the same separation
`ROLES_WITH_BUSINESS_WRITE` makes everywhere else.

**Nothing about Matter visibility changed, and this is the load-bearing
refusal.** Archive access answers a question about the **corpus** and never
about a Matter. `ADMINISTRATOR` remains outside `ROLES_WITH_RESTRICTED_ACCESS`,
so an administrator who opens an archive row learns nothing about which
register entries they may read: everything the archive renders about a Matter
still goes through `Matter.objects.visible_to`. Asserted directly — an
administrator opening a letter filed onto a `RESTRICTED` Matter does not see
that Matter's title.

**`READER` is not widened**, for ADR 0042's reason exactly: a different
audience with a different question behind it, and deciding what they may see is
a separate decision that is not taken here. `DepartmentViewer` — the shared
password with no persona chosen — has an empty role, is in no set, and is
unchanged.

**Downloads follow the row.** Unchanged, and now worth restating because the
population widened: the file route asks `may_read_archive` exactly as the list
and the detail do, so the four surfaces cannot disagree. Every archive download
still records `authenticated_via` beside the persona, which is what keeps the
shared gate's limited claim about *who* read a letter visible on the audit row
rather than compensated for by withholding the corpus.

## Consequences

- A specialist opening *Teemad → Arvamused* now sees the `Arhiiv` tab with the
  corpus behind it, and `Saadetud 1` stops being the whole answer.
- **The embedded section costs one more query for a specialist** — exactly the
  one ADR 0047 already wrote down as "a sixth appears for a reader who may read
  the archive". The budget test moves 8 → 9 with that arithmetic named rather
  than the number simply raised. Nothing that scales with rows moved.
- **Behind the shared gate there is no longer a selectable persona that the
  archive refuses.** `READER` and `ADMINISTRATOR` are not persona candidates
  (ADR 0034) and both lawyer roles now read. The refusal tests therefore sign
  in individually, which is where that rule can still be proven; asserting it
  through `act_as` would have proven the *persona* rule a second time and this
  rule not at all.
- No migration, no index version move, no projection rewrite. This changes who
  may ask, not what is stored.

## Alternatives rejected

**Widening only under the shared gate.** It would fix today's complaint and
leave the Cloudflare cliff in place, which is the half of the current rule that
is actually wrong.

**Making the archive readable by anybody authenticated.** The brief refuses it
and so does the shape of the product: `READER` exists precisely as the role
that reads where approved, and a boundary that admits everybody is not one.

**Inheriting archive access from the linked Matter.** Attractive, and it cannot
answer the question. 118 letters are linked to no Matter at all and 350 are in
the review queue; there is nothing to inherit from, which is why ADR 0028 put
this decision here in the first place.

**Letting a specialist file links too.** That is a business claim the
department signs, and the round that widened reading is the wrong round to
widen authorship. It is a real question and it is left open on purpose.
