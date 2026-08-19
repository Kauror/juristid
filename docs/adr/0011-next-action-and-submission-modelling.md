# 0011 — NextAction and Submission modelling

Status: Accepted (Stage 1)

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

## Consequences

- Reporting derives a compatibility "sent date" from Submission through a
  documented rule; it is never an independently editable column (ADR 0007).
- Statistics about opinion volume query Submission, not Matter count.
- The three action kinds and three date meanings must be visible in the UI as
  words, not colours, or the distinction is lost to the reader.
- Stage 2's Consultation work inherits the same shape: thin, first-class, and
  never inferring a rate from two independent counts.
