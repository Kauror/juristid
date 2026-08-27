# 0042 — Department-wide access for the legal team

Accepted, 2026-08-27. Supersedes the read-policy half of the visibility model
described in the master specification §5.2, and the assumption every
authorization decision before it was written against.

## Context

`RESTRICTED` was defined as "visible only to the Matter owner, explicit
collaborators and configured business roles such as department head", and the
configured set held one role: `DEPARTMENT_HEAD`. The master specification lists
**"Restricted-content business roles and break-glass policy"** among the
decisions it explicitly defers to the department head rather than inventing.
This is that decision.

Two lawyers, both `SPECIALIST`, neither the other's collaborator. Measured on
`6ea0b65` before this change, the second could see nothing of the first's
restricted work: no Matter, Entry, Submission, Document or `Järgmiseks` in any
queryset, a 404 on the detail page, and no trace of it in the register, on
Ülevaade or in search.

That is not how the department works. Its members answer for the Chamber's
position as a body — a lawyer covering for a colleague, picking up a file, or
being asked in a meeting what Koda said about a draft act needs to be able to
look. The record this application replaced was a shared OneNote notebook that
every lawyer could read. Reproducing the old process but with less access than
it had is not a migration anybody asked for, and the workaround — break-glass
for ordinary curiosity — would turn an audited emergency mechanism into a daily
habit, which is how such mechanisms stop being read.

## Decision

**The confidentiality boundary is the application, not the Matter.** Everything
that keeps outsiders out stays exactly as it is: Cloudflare Access, the shared
gate, authentication, the business-write HTTP boundary (ADR 0037), the 404
refusal that will not confirm a record exists.

**Both lawyer roles read department-wide.** `ROLES_WITH_RESTRICTED_ACCESS` now
holds `SPECIALIST` and `DEPARTMENT_HEAD`. One frozenset, in the module every
read already passes through, so every surface follows without a second copy of
the rule anywhere: detail, lists, children, documents and their filenames,
downloads, the timeline, search, Ülevaade and the reporting counts.

**Ownership and collaborators are workflow metadata.** They record who is
answerable for a file and who is working it. They never answered the question
"who may know this exists", and they no longer look as though they did.

**The write model is unchanged, and that is deliberate.**
`may_write_business_content` was always role-based, with an explicit note that
ownership is not the write boundary — the department maintains these records
collaboratively. A specialist may therefore now edit a colleague's `RESTRICTED`
Matter exactly as they could already edit a colleague's `NORMAL` one. This is
the existing rule meeting a wider set of Matters, not a new rule. A
participant-only write scope was considered and rejected: it would have invented
a boundary the product does not have, and left the application enforcing an
ownership rule for restricted files that it enforces nowhere else.

**Reach is not capability.** What a role may *do* is still decided per
operation. A specialist who can now open any restricted file still cannot review
a `Töövõit`, and the department head's management surfaces stay the department
head's.

## What this does not change

- **`ADMINISTRATOR` gains nothing.** Technical administration is not legal work.
  It is now the only business role outside the restricted set, which makes the
  separation more load-bearing than before, not less. An administrator who needs
  to read a restricted file uses break-glass, and break-glass is audited.
- **`DepartmentViewer` gains nothing.** Knowing the shared password proves
  somebody is behind the door, never who they are. The sentinel is not a person,
  cannot be an audit actor, and still sees only `NORMAL`.
- **`READER` is not widened.** The specification describes it as management or
  communications reading "where approved" — a different audience with a
  different question behind it. Deciding what they may see is a separate
  decision, and it is not taken here.
- **Anonymous, inactive and unrecognised roles** are refused as before, by the
  same whitelist that fails closed on a value it does not know.
- **Break-glass stays.** It is no longer the way a lawyer reads a colleague's
  work, which is the point — it goes back to being what it was for: an audited
  grant for an actor whose role does not carry business read access.
- **`Matter.visibility` and `visibility_override` stay.** They still separate the
  department from everyone else, still carry the evidence-integrity invariant
  that DATA-001 and DATA-002 enforce in the database (ADR 0040), and still
  describe historical records truthfully. Nothing is migrated; no value changes.
- **Search is unchanged as a projection.** `INDEX_VERSION` stays `AUTH003.1`
  because the indexed text is identical — only the live authorization join that
  filters it moved. AUTH-003's invariant is untouched: a projection still never
  exposes more than its source authorizes. What changed is that a specialist is
  now authorized to the source.

## Consequences

A lawyer joining the department sees the department's work. The audit trail
still records who did what, because that was never the same mechanism as who
could look. `RESTRICTED` keeps a narrower job than it had — it separates the
legal team from technical administration, from a shared-gate visitor and from
anyone outside — and the tests now say so directly rather than by implying that
one lawyer is a stranger to another.
