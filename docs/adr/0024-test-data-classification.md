# 0024 — Test data is a stored class on the Matter, and purging it is a later decision

- **Status:** Accepted — implemented on the `feat/test-data-classification` branch, pending integration
- **Date:** 2026-08-23
- **Builds on** ADR 0003 (immutable evidence), ADR 0005 (authorization and visibility inheritance), ADR 0014 (storage classes and derivatives), ADR 0017 (statistics and the metric catalogue).

## Context

Juristid is being built against real production data. Development and testing
therefore happen in the same database as the historical register, the opinion
archive and the department's live work: somebody testing the create form, an
upload, a submission or a next action leaves records behind that are
indistinguishable from business data.

Indistinguishable is the operative word. Once a development record exists there
is no reliable way to find it again, no way to keep it out of a statistic, and
no way to remove it without a person reading titles and guessing. Every
convention that has been reached for elsewhere — a `TEST` prefix on the title, a
reserved reference number, a tag, a policy area, an owner, a date window, an
environment flag — is a convention rather than a fact, and each fails the same
way: it is a property of how the record was *written*, not of what the record
*is*.

There are already four fields nearby that mean something else and must keep
meaning it:

- `record_mode` — current work or register archive. An archive row is real
  history.
- `origin` — how the record entered the system. A natively created Matter is
  normally real work.
- `data_quality_tier` — how much of an imported row has been verified. An
  unverified register row is real data nobody has checked, which is the opposite
  of a record that was never about anything.
- `visibility` — who may read it. Unrelated.

## Decision

### `Matter.data_class` — REAL or TEST, defaulting to REAL

One indexed column on the canonical Matter, with a database `CheckConstraint`
on the vocabulary. Every existing row becomes REAL through the column default,
so the migration carries no `RunPython` and no importer changes: the historical
register, the OneNote corpus and everything a lawyer has filed stay real work
without anything having to decide that on their behalf.

The vocabulary constraint is not decoration. A value outside `{REAL, TEST}`
would be absent from `real_data()` — and therefore from every statistic — *and*
absent from `test_data()`, and therefore invisible to the maintenance planner
whose job is to find it. Django choices do not stop a bulk `update()`, a data
migration or a shell session.

### TEST implies `origin = NATIVE`, enforced twice

A second `CheckConstraint` refuses TEST on any Matter the system did not create
itself. TEST means "made while developing Juristid"; a historical register row
is somebody's real work from 2017, carrying provenance that cannot be
reconstructed. Marking one disposable because a control sat beside the wrong row
is the most expensive mistake this feature could enable, so the service refuses
it and the database refuses it again.

### One classification owner

No child record gets a flag of its own — not `Entry`, `NextAction`,
`Submission`, `Document`, `DocumentVersion`, the structured facts, or the search
projection. A child is test data when its Matter is. This is the same reasoning
as ADR 0005's derived visibility: a stored copy on every child is a copy that
goes stale, and the state it goes stale into here is a REAL Matter holding a
TEST submission, which is not a thing that should be representable.

The opinions archive is outside this entirely. `OpinionArchiveBinary` and
`OpinionArchiveItem` belong to the archive, not to any Matter. If a TEST Matter
carries an `OpinionArchiveMatterLink`, only the *link* is test-contextual; the
evidence it points at is real and is never test-owned.

### Classification is not authorization, and TEST is not hidden

`visible_to()` is untouched, and no operational queryset filters TEST out. A
developer must be able to open the record they created ten seconds ago; a
register that silently hid it would teach them the save had failed. What exists
instead is vocabulary — `Matter.objects.real_data()` and `.test_data()` — plus
an explicit `?andmed=` filter on the register whose default is *all*.

**Reporting must opt in.** Business and statistical populations start from
`.real_data()`. That is a contract this ADR states and the Statistics work
consumes; the single integration point is `app/reporting/selectors/base.py`
`visible_matters()`, deliberately not changed here because a parallel branch
owns it.

### Purging is planned, not implemented

`python manage.py purge_test_data --plan` walks the deletion graph from Django's
own metadata, following reverse relations only, and reports what a purge would
have to account for: every owned row by model, the canonical evidence objects
behind them, the append-only audit rows, and every blocker.

**There is no `--apply`, and that is the decision.** The graph runs through
things a delete cannot simply cascade past:

- `ChangeEvent` is append-only *in the database*, by trigger, and holds
  `matter` under `PROTECT`;
- `Document`, `DocumentVersion`, `MatterSourceReference` and `ImportRowLedger`
  are all `PROTECT`;
- evidence bytes live outside PostgreSQL;
- a real Matter's submission can legitimately point at a version under a test
  Matter.

Removing a test Matter's audit history means either physically deleting
append-only rows under a dedicated maintenance protocol, retaining them under a
detached tombstone identity, or replacing the whole idea with a disposable
development-database reset. Those are three different answers with three
different consequences for an architecture guarantee, and none of them belongs
inside a utility command written to make cleanup convenient. This branch
produces the exact deletion graph that decision needs and stops there.

## Alternatives considered

**A `TEST` tag or policy area.** Rejected: both are business vocabulary a lawyer
curates, and an entry called TEST would appear in the chooser, in the tag cloud
and in every taxonomy statistic.

**A title prefix or a reserved reference block.** Rejected: a convention, not a
fact. Nothing enforces it, a rename destroys it, and the reference sequence is
already load-bearing for a number people carry in their heads.

**A separate development environment only.** Not rejected as a good practice,
but it does not solve this problem: the reason development happens against real
data is that the historical corpus is what the features are about, and a record
created during that work still needs a name.

**Excluding TEST from `visible_to()`.** Rejected: it would make authorization
depend on a field that has nothing to do with authorization, and would hide the
record the developer is looking at.

**Implementing `--apply` behind a confirmation flag.** Rejected: see above. A
flag would settle the audit-retention question by accident.

## Consequences

- Every reporting population must say `.real_data()` explicitly. Anything that
  forgets counts development records, and nothing on the screen looks wrong.
- Test data accumulates until a purge exists. That is the accepted cost of not
  guessing at the audit decision; the plan command makes the accumulation
  visible.
- A Matter's `origin` cannot move away from `NATIVE` while it is TEST. Nothing
  does that today — promotion applies to archive rows, which cannot be TEST.

## Reversibility

High. Dropping the column and its two constraints removes the feature with no
data loss, because REAL is the default and nothing else reads the column. No
existing constraint, trigger or relation was weakened to add it.
