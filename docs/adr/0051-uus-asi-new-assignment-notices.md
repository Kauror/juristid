# ADR 0051 — «Uus asi»: a personal receipt for a Matter somebody just put on your desk

- Status: proposed (feature branch `ux/new-assignment-notices`)
- Date: 2026-08-31
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0036 (`/inimesed/<id>/asjad/` and the manager's view of a desk),
  ADR 0042 (department-wide lawyer access — why an owner participates in their
  own Matter), the Stage-2F owner backfill (where `provenance` on
  `assign_matter` came from), `01-EHITUSJUHIS` §3.5 and `03-BACKEND` §2 (the
  scratchpad's privacy rule, which this record extends to a second block)

## Context

Handing work over is invisible. A lawyer changes a Matter's *Vastutaja*, or
files a new Teema and names who is to deal with it, and the person on the
receiving end finds out when they next scan Minu asjad closely enough to notice
a row that was not there yesterday — or when somebody asks them about it.

The register answers *what is this Matter*. Minu asjad answers *what do I do
now*. Neither answers the smaller, more time-sensitive question underneath them:

> Has somebody just put a new Matter on my desk?

## Decision

One rail block on Minu asjad, called **Uus asi**, above **Märkmed**. It lists
the Matters that have been assigned to this person and that they have not yet
opened from it. When there is nothing unread, there is no block, no heading and
no reserved space.

This is deliberately **not** a notification system. There is no channel, no
email, no browser push, no preference screen, no notification centre, no bell,
no badge in the top bar, no counter and no toast. Adding any of those is a
separate decision that this record does not take and does not prepare for.

### The product rule

**A human owner assignment produces a notice for the recipient.** Both halves of
"assignment" count, because ownership is written in two places and only one of
them is `assign_matter`:

* changing the owner of an existing Matter (`assign_matter`);
* creating a Matter with an owner on it (`create_matter`, which writes the
  column directly and never calls `assign_matter`).

**Self-assignment produces one too.** `actor == recipient` is not excluded. A
person who takes a file off the unassigned pile, or files a new Teema under
their own name, has something new on their desk, and the block is the record
that they have not been back to it yet.

**Automated and imported assignments do not.** The owner backfill, the legacy
import, a register refresh, an enrichment pass and the seeding commands all
materialise ownership that no colleague decided. The boundary is the one the
audit trail already used: `assign_matter(provenance=...)` is present exactly
when no colleague made the assignment, and `create_matter` now takes the same
argument for the same reason. It is not a caller-name heuristic, and it is not
"is there an actor" — a management command runs under an operator's account.

**Nothing else is an assignment.** A title, a deadline, a stage, a Järgmiseks, a
sender, an addressee, an added collaborator: none of them produce a notice. Nor
does re-posting the owner a Matter already has — `assign_matter` returns before
it writes anything when the owner did not change, so a repeated request cannot
produce a second notice.

**The notice is personal to its recipient.** Absolutely, and in the same sense
`PersonalScratchpad` is. The manager's view of a colleague's desk
(`/inimesed/<id>/asjad/`) does not query it, so the section is absent from that
response rather than hidden in it. Nobody but the recipient can acknowledge one,
and the acknowledgement route cannot resolve another person's row at all.

**It is acknowledged by opening the Matter *from* Uus asi.** A dedicated POST,
which stamps `viewed_at` and redirects to the Matter.

**Ordinary Matter viewing does not acknowledge it.** This is the half that
carries the design. Saving a new Teema with your own name on it redirects you
straight into that Matter; if rendering `matter_detail` counted as
acknowledgement, a self-assigned notice would clear itself before its recipient
ever reached Minu asjad, and the self-assignment rule above would be quietly
untrue. There is no timing heuristic guessing whether a particular Matter page
"counts" — the notification owns its own read receipt.

**A real owner transition supersedes a stale unread notice.** Sandra is handed a
file at 09:00 and has not looked at it; at 09:05 it goes to Ireen. Sandra's
block must not still offer it. Clearing the owner does the same. Neither deletes
the row — what landed on somebody's desk is a fact about that day.

**A superseded notice is retired, and can no longer be acknowledged.** The block
disappears from the rail the moment the file is handed on, but the page somebody
already has open does not: a browser still sitting on Minu asjad from before the
reassignment carries the form. That stale POST answers **404**, like every other
refusal on this route, and writes nothing.

This is a lifecycle rule and not a tidiness one. `viewed_at` and `superseded_at`
are two different terminal reasons — *the recipient acted on the active notice*
and *the notice ceased to be active because ownership changed* — and letting a
stale click add the first to a row that already carries the second would
conflate them. A notice that is merely **already viewed** is a different case
and still resolves: that is a live receipt submitted twice, and the conditional
UPDATE makes the second POST a redirect that changes nothing. The gate sits in
the route's own lookup and again in the service's UPDATE, so no future caller
can reach the write by a different path.

**No unread rows means no block.** Not an empty section, not a zero, not a
placeholder, not a heading with nothing under it. The last acknowledged row
takes the whole section with it.

**No historical backfill.** The feature starts at deployment. Ownership that
already exists is not new, and a migration that manufactured notices for it
would open the product with thousands of «uus asi» rows about work people have
been carrying for years.

### The shape

One additive model, `MatterAssignmentNotice`
(`app/matters/migrations/0013_matter_assignment_notice.py`), holding foreign
keys and two nullable stamps: `matter`, `recipient`, `assigned_by`,
`created_at`, `viewed_at`, `superseded_at`. It stores **no copy** of the title,
the owner's name or a URL; those are canonical elsewhere, and a snapshot would
go stale the first time a Matter was renamed.

`assigned_by` is provenance, not copy: it records who did the handing, and no
user-visible sentence is built from it. The visible row is the Matter title and
nothing else.

A **partial** unique constraint — one active notice per `(matter, recipient)`,
where active means neither viewed nor superseded — prevents a duplicate without
forbidding a legitimate future reassignment back to the same person. A partial
index on `(recipient, -created_at)` over the same condition is the Minu asjad
read, which is one query with the Matter joined and does not grow with the
number of rows.

Lifecycle and ownership share one transaction. There is no queue, no job and no
asynchronous delivery: this is local database state written beside the ownership
change it is about.

### What this does not touch

The main column of Minu asjad is unchanged. A newly assigned Matter appears in
*Aktiivsed teemad* and in its deadline band according to its ordinary work
state, exactly as before; there is no second "new" row and no change to
`WorkItem`, deadline or `järgmise tegevuseta` semantics.

The existing `MATTER_ASSIGNED` change event is unchanged and is still the
canonical audit record. The notice sits beside it as derived personal workflow
state; no duplicate business event is raised for rendering.

## Consequences

The two writers of `Matter.owner` are now also the two places that decide
whether anybody is told. That is the point — it is why there is one rule and not
five view-level triggers — but it does mean a future caller that writes the
column directly would be silent. `create_matter` and `assign_matter` remain the
only two places in the application that write it.

`create_matter` gains a `provenance` argument. Callers that pass nothing keep
their existing behaviour, which is what makes every human path notify without
being changed; the seeding commands pass it so a synthetic world does not open
with a queue of hand-overs nobody performed, and so the visual baselines keep
describing the page they describe.

The `visible_to` gate in the selector cannot currently exclude a Matter its own
owner holds, because in this authorization model an owner participates in their
own file (ADR 0042). It is still asked, and asked before any Matter information
is rendered, because ownership is not authorization and a narrower rule later
must not have to remember to add it.
