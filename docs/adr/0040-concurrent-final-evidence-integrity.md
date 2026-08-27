# ADR 0040 — Concurrent final-evidence integrity: one Matter lock, one lock order

- Status: accepted
- Date: 2026-08-27
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0005 (visibility is derived, never stored), ADR 0011
  (final evidence and what a Submission claims), ADR 0003 (document lifecycle),
  DATA-001 (`submissions/migrations/0005`, the serial case and the detector)

## Context

A Submission's final version is the system's answer to "what exactly did Koda
send". Two rules make that answer trustworthy, and DATA-001 restated both:

1. the final version belongs to a Document in the Submission's **own Matter**;
2. the final version is **never less restricted** than the Submission.

Both are checked in `app.submissions.services.check_evidence_is_usable`, and
`submissions/migrations/0002` and `0005` back them with `BEFORE UPDATE`
triggers on `submissions_submission`, `documents_document` and `matters_matter`.

DATA-001 closed the ordinary case: one writer, one check, one refusal. It left
one open, deliberately, and the DATA-002 investigation confirmed a second
alongside it.

### The residual case: write skew

Both sides of rule 2 are *derived* from the Matter's visibility (ADR 0005). So
the invariant has three inputs living in three rows, and the two operations that
can falsify it write **different** ones:

- binding final evidence writes `submissions_submission`;
- relaxing a Matter writes `matters_matter`.

Before this change they took no lock in common. Under READ COMMITTED each could
take a snapshot in which the other had not committed, pass its own check against
that snapshot, and commit. Reproduced deterministically on the DATA-001 branch:
a transaction binding evidence to a RESTRICTED submission held its pointer
uncommitted while `set_matter_visibility` relaxed the Matter to NORMAL. Neither
raised. Both committed. The result was a RESTRICTED submission whose exact text
was listed and downloadable by the whole department — and
`check_evidence_integrity` was the only thing left that could see it, after the
fact.

**A trigger cannot close this**, and it is worth being explicit about why. A
`BEFORE UPDATE` trigger runs inside the writing transaction and its queries see
that transaction's snapshot. A Matter-side trigger scanning
`submissions_submission` therefore scans a table that does not yet contain the
row making the relaxation illegal. Adding a fourth snapshot-reading trigger
would have looked like a fix and changed nothing.

### The second parent hole: Document reparenting

Rule 1 was structural for the Submission — the trigger on
`submissions_submission` refuses a `final_version_id` whose document sits under
another Matter — but it watches the *pointer*, and the invariant has two ends.
A single `UPDATE documents_document SET matter_id = ...` carried an
already-relied-upon document into a different Matter, making

    submission.final_version.document.matter_id != submission.matter_id

true without either the Submission or its pointer being written at all. No
product flow does this; a shell, a data migration and `QuerySet.update` all can,
and one of those is how the historical corpus was loaded.

## Decision

### 1. Both sides serialise on the Matter row

`app/matters/locks.py` holds one small, domain-specific helper —
`lock_matter_for_evidence_integrity(matter_id)` — and every operation that can
establish or falsify the invariant calls it before reading anything the decision
rests on:

| path | what it does |
| --- | --- |
| `submissions.services.attach_final_evidence` | captures and binds |
| `submissions.services.select_final_evidence` | binds an existing version |
| `submissions.services.mark_submission_sent` | re-checks at the moment of sending |
| `legacy_import.opinion_apply._write_one_submission` | binds during an archive apply |
| `matters.services.set_matter_visibility` | relaxes or tightens the Matter |

The Matter is the synchronisation point because it is the one record both
operations already have in hand: the evidence being bound belongs to it, and the
visibility being relaxed is its own column.

### 2. One global lock order

    Matter → Submission → Document

Take a prefix of that order or the whole of it, never a suffix before a prefix.
`documents.services.add_evidence_version` locks a Document on its own, which is a
suffix taken alone and therefore safe. What would not be safe is any path that
locked a Document and then a Matter.

### 3. Lock strength is part of the order — at every level of it

Every row this protocol takes is locked `FOR NO KEY UPDATE`, not `FOR UPDATE` —
exactly what a plain `UPDATE` of that row takes by itself. The two conflict with
each other, so the writers this exists to serialise still take turns. Neither
conflicts with the `FOR KEY SHARE` that every insert of a row *referencing* the
locked one acquires.

That is not a micro-optimisation, and it is load-bearing twice.

**On the Matter.** Documents, Submissions and audit events all carry a
`matter_id`, so `FOR UPDATE` here would put a Matter wait in the path of a
transaction that already holds a Document lock and is merely inserting a child
row — reintroducing the `Document → Matter` edge the ordering rule exists to
keep out of the graph. That path is reachable in ordinary use:
`documents.views.add_version` locks an existing Document and then records a
`ChangeEvent` against its Matter. A test asserts the weaker lock behaviourally:
holding it must not block a `create_document` on the same Matter.

**On the Submission.** The same rule applies one level down, and there it is the
difference between a wait and a cycle. A targeted search refresh runs from
`post_save` *inside* the binding transaction and takes the rebuild gate's shared
side (`app.search.indexing`); a rebuild holds that gate exclusively and
re-inserts `SearchDocument` rows carrying `submission_id`, which takes
`FOR KEY SHARE` on the row the binder is holding. Under `FOR UPDATE` those two
deadlock — the rebuild waiting for the row, the binder waiting for the gate —
and PostgreSQL aborts one of them. `FOR NO KEY UPDATE` still serialises two
binders against each other and against a send, which is everything the check
needs, and lets the projection row through.

`lock_submission_for_evidence_integrity` is the second helper for that reason.
`test_binding_evidence_does_not_deadlock_against_a_search_rebuild` fails if it is
strengthened to `FOR UPDATE`, and
`test_the_matter_lock_does_not_block_writers_that_only_reference_the_matter`
fails if the Matter helper is.

`mark_submission_sent` still takes the older, stronger `FOR UPDATE` on the
Submission; that predates this work and is left alone here, but it means the
cycle above is still reachable from *that* service and from
`documents.services.add_evidence_version`. Both are pre-existing and belong to a
separate change rather than to DATA-002.

### 4. A waiter re-reads everything

A transaction that waited on the Matter lock was, by definition, waiting for
someone else's write to that row, so anything it read beforehand may now be
false. Every caller uses the row the lock returned rather than the instance it
arrived with, re-reads the Submission under its own lock, and re-reads the
evidence version with its Document locked. `set_matter_visibility` re-derives
`previous` from the locked row and re-runs
`_submissions_left_above_their_evidence` after acquiring it. A lock that only
delays a stale check closes nothing.

### 5. A trigger for the reparent, as a backstop

`submissions/migrations/0006` adds
`documents_relied_upon_evidence_stays_in_matter`, a `BEFORE UPDATE OF matter_id`
trigger on `documents_document` that refuses to move a Document out from under a
Submission relying on one of its versions. It is a structural bypass guard in
the same class as the three before it — **not** the concurrency fix. What makes
the *concurrent* reparent safe is the Document row lock in the binding path: the
reparent waits, and PostgreSQL re-fires the trigger once it wakes, against a
database that now contains the pointer.

### 6. Refusal, never repair

Which end of a broken pair is wrong is not something code can know. The document
may belong where it is being sent and the submission may be the mistake, or the
reverse. Every path here refuses the invalid mutation; nothing reassigns
`Submission.matter`, detaches final evidence, or moves evidence automatically.

### 7. The detector stays DATA-001's

`check_evidence_integrity` already reports `foreign-final-evidence` and
`evidence-less-restricted`. DATA-002 adds no second detector — one question
should have one answer, and the runbook names that one. Before deploying this,
an operator runs `check_evidence_integrity` and expects zero relevant findings.
Findings mean a human decision (restrict the document, relax the submission,
select different evidence, supersede the opinion), not an automatic repair.

## Consequences

- **No business-data migration**, and no data repair. The migration adds one
  trigger function and one trigger, both reversible.
- **Serialisation is one row wide.** Two people editing unrelated Matters never
  wait for each other; the lock is a primary-key read. Read pages take no row
  locks at all, and both facts are tested.
- **Writers on the same Matter now queue.** A Matter holds a handful of
  submissions and the critical sections are short, so the cost is a wait
  measured in the length of one transaction. That is the price of the invariant
  being true rather than usually true.
- **`Submission.matter` stays CASCADE.** Deleting a Matter is still refused, by
  `Document.matter` and `ChangeEvent.matter` being PROTECT, exactly as before —
  the new trigger fires on `UPDATE OF matter_id` and does not touch deletion.
  The incidental protection is unchanged, and a test now pins it so a later
  change has to notice.
- **Authorization is untouched.** Read populations, write authorization,
  break-glass, department-head reads, ADMINISTRATOR behaviour, 404/403 semantics
  and child visibility are all as ADR 0037 and 0038 left them.
- **Search is untouched, but not unrelated.** No new searchable mutation path,
  no new hook, no change in `app/search`, `INDEX_VERSION` unchanged: visibility
  is joined live (ADR 0013, 0014) and `set_matter_visibility` already
  participated in existing refresh behaviour. What *is* new is that these
  transactions now hold row locks while the `post_save` refresh asks for the
  rebuild gate, which is why the lock strength in decision 3 is stated for the
  Submission as well as the Matter.

## Alternatives rejected

**A fourth trigger on `matters_matter` that also scans submissions.** This is
what the shape of DATA-001 suggests, and it cannot work: the scan reads the
writing transaction's snapshot, which is precisely the snapshot that lacks the
other transaction's row.

**SERIALIZABLE isolation for these paths.** It would detect the skew, but as a
retryable error surfaced to whoever lost — and the repository has no retry
protocol, so the failure would reach a person as a database error rather than as
a sentence. It would also change the isolation contract for everything sharing
the connection.

**Locking the Submission, or the Document, instead.** Neither serialises against
a Matter visibility mutation, which writes neither of them.

**An advisory lock keyed on the Matter id.** Equivalent in effect, but it lives
outside the row it protects: nothing about a `pg_advisory_xact_lock` call site
tells a reader which row is guarded, and a path that forgot it would be
invisible. Locking the canonical row makes the protocol legible.

**A generic locking abstraction** — a repository layer, a lock manager, a
decorator. One explicit named helper that reviewers can follow is the point;
anything that hides the acquisition makes the ordering rule unauditable.

**Deciding whether final evidence may be *more* restricted than its Submission.**
Out of scope and left open. DATA-002 enforces only the existing asymmetric rule:
evidence must never be *less* restricted.

**Absorbing the `OpinionArchiveBinary` findings** (bytes not covered by
`check_evidence`, mutable identity columns). Real, and separate; they are P3
follow-ups against a different canonical evidence holder.
