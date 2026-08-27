# 0011 — NextAction and Submission modelling

Status: Accepted (Stage 1)

- **Amended 2026-08-27 (DATA-001).** A third trigger, on `matters_matter`. The
  final-evidence rule below named two triggers and two records; both sides of
  the comparison are *derived* from the Matter's visibility, which was the third
  input and the one nothing guarded. See *The Matter is the third input* below.
  It states an input this ADR relied on without recording; it changes no
  decision here.

## Context

Two Stage-1 models carry the weight of the product's central claims: that a
work queue can be trusted, and that Koda can say exactly what it sent and when.
Both replace a single spreadsheet column that could not express what the work
actually is.

## Decision

**`Järgmiseks` is a first-class record with a kind and a date meaning.**

`NextAction` stores `kind` (DO / WAIT / MONITOR) and `date_semantics`
(DEADLINE / REVIEW_ON / EXPECTED_AROUND) as separate facts. Only
**DO + DEADLINE** can be overdue. A WAIT whose review date has passed is due for
a look; an EXPECTED_AROUND date is an estimate of someone else's timing and can
never be "missed".

This is the difference between a work list people believe and one they learn to
ignore. Waiting on a ministry is the ordinary state of much of this work, and a
system that files it under "overdue" makes every genuine deadline look like
noise.

**At most one open action per Matter**, enforced by a partial unique index.
Replacing an action supersedes the previous one and links them, so the record of
what Koda intended and when survives. Closing a Matter ends its open action, so
a closed file cannot sit in someone's queue forever.

`date_precision` exists from the start because historical rows and external
expectations are often known only to the month or the quarter, and recording a
guess as an exact date would manufacture certainty the source never had.

This is deliberately **not** a task manager: no sub-tasks, no assignment queue,
no recurrence, no notifications.

**`Submission` is the canonical outbound record, and there is no
`Matter.opinion_sent_date`.**

One Matter routinely produces several submissions — an opinion during the
consultation round, a supplementary letter after the ministry replies, a
parliamentary submission later. A single date column on the Matter could only
ever record one of them, which is why opinion counts derived from the register
were never reliable.

**A submission becomes SENT only together with the exact binary that was sent.**
The service checks it under a row lock, and a CHECK constraint refuses a SENT
row without both `sent_at` and `final_version`. Evidence already captured is
never re-pointed at a different file: replacing what was relied upon would
rewrite history, so the path is withdraw and supersede.

## Where the invariants are enforced

Business validation is authoritative in the service layer. Forms validate early
for the user's sake, and the database is the backstop for states that would be
dangerous to have persisted at all. Three rules earned all three layers:

**A DO action with a DEADLINE must carry a date.** The form said so first, but
the rule belongs where an importer or an integration also has to obey it, and a
CHECK constraint refuses the row outright. WAIT and MONITOR may be dateless —
"waiting on the ministry, no idea when" is an honest state, not an incomplete
one.

**Closure and `Järgmiseks` serialise on the Matter row.** Both depend on the
Matter's lifecycle state, so both take `select_for_update()` on the Matter and
re-read `is_open` while holding it. Whichever transaction arrives first wins: a
closure that lands first makes the later call refuse, and an action that lands
first is cancelled by the closure. Neither ordering can end with a closed Matter
still carrying an open instruction. The partial unique index is not the
mechanism here — catching its error afterwards would mean the decision had
already been made on a stale read.

**Final evidence must belong to the submission's Matter, and must not be less
restricted than the submission.** The first makes "what exactly did Koda send"
answerable; the second stops the restriction being cosmetic, because otherwise
the exact text of a restricted submission would be listed and downloadable by
people who cannot see the submission. Two triggers back this up: one on
`submissions_submission` for the moment evidence is attached or sent, and one on
`documents_document` for the moment an already-referenced document is relaxed.
Without the second, one `UPDATE` would undo the rule after the fact.

**The Matter is the third input** (amended 2026-08-27, DATA-001). Effective
visibility is derived rather than stored (ADR 0005), so both sides of "not less
restricted than the submission" are computed from the Matter's visibility and
the record's own override. Relaxing the Matter therefore drops the evidence to
whatever its own override says, while a submission carrying its own `RESTRICTED`
override stays where it is — reaching the state the other two triggers refuse to
create, through an ordinary audited `set_matter_visibility` call, without either
record being written. `set_matter_visibility` now refuses that change with a
sentence, and a third trigger on `matters_matter` backs it up. Only relaxation
can break it: tightening raises both sides together, and repairs the state if it
was already broken.

The rule is now enforced on every input that can change it, so a row that fails
it can only predate the third trigger. `check_evidence_integrity` reports both
halves of the rule — `evidence-less-restricted` and `foreign-final-evidence` —
so that historical corruption is findable rather than silent. Detection is not
the enforcement, and neither substitutes for the other.

`EntryRevision` is append-only in the database for the same reason the audit
tables are: a record of superseded wording that can itself be rewritten proves
nothing. `edit_entry` locks the Entry row and re-reads it before numbering a
revision, so two simultaneous edits queue rather than colliding on a revision
number or losing one wording.

## Consequences

- Reporting derives a compatibility "sent date" from Submission through a
  documented rule; it is never an independently editable column (ADR 0007).
- Statistics about opinion volume query Submission, not Matter count.
- The three action kinds and three date meanings must be visible in the UI as
  words, not colours, or the distinction is lost to the reader.
- Stage 2's Consultation work inherits the same shape: thin, first-class, and
  never inferring a rate from two independent counts.
