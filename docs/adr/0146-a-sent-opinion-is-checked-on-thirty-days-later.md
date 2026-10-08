# 0146 — A sent opinion is checked on thirty days later, until somebody says how it ended

**Status:** accepted
**Date:** 2026-10-08

The owner's stage-one monitoring round. When the Chamber records an opinion as
sent, Juristid schedules a check 30 calendar days after the recorded sending
date: *find out whether the addressee has answered.* The responsible lawyer
records what the check found. If nothing has arrived, they choose the next day
to look again, or end the monitoring and say why. A sent opinion no longer
leaves the department's active work the day it goes out.

**Two migrations, both additive.** `workflow/0014` creates `OpinionFollowUp`
and adds two `NextAction` columns: `follow_up` (nullable) and
`follow_up_outcome` (with a database default `''`). It also adds five
`NextAction` constraints that every existing row satisfies. `audit/0036` adds
one event choice, `NEXT_ACTION_RESCHEDULED`, with no SQL. **No data migration
and no backfill.**

## 1. The send schedules the check, and only an interactive send

Three interactive acts record a send:
- `+ Koja arvamus`;
- `Märgi saadetuks` on a draft;
- `Registreeri saatmine` on Dokumendid.

All three arrive through the two open-Matter wrappers,
`mark_submission_sent_on_open_matter` and
`register_sent_opinion_on_open_matter`. Each wrapper calls
`schedule_follow_up_of` after the send is stamped, inside the send's own
transaction and `composer_operation`. That function calls
`app.workflow.follow_ups.schedule_first_check`, which is the one place a check
is created. No view writes one.

Every kind counts: a formal opinion, a supplementary one, a submission to the
Riigikogu, a joint letter, and anything else sent through the canonical
workflow. What triggers the check is the SENT transition. Uploading a file,
saving a draft or editing an opinion triggers nothing.

`mark_submission_sent` and `register_sent_opinion` sit below the wrappers and
schedule nothing. The archive apply, the register importers and the seed
commands write through them, or write `SENT` rows directly. A letter from 2019
does not become a task. `tests/test_opinion_follow_up.py` fails if an importer
ever names the scheduler.

## 2. Thirty calendar days, from the recorded sending date

The first check is due on `sent_on + 30` days:
- `sent_on` is `Submission.sent_at` read as a Europe/Tallinn business day, the
  way every surface reads it (`timezone.localdate`);
- a day-only send is stored as Tallinn midnight, a timestamped one is the day
  it was pressed;
- a weekend or a public holiday does not move it: 8 October 2026 gives
  7 November 2026, a Saturday.

An opinion registered today as sent two months ago gets a check that is already
overdue. This is intentional.

## 3. One follow-up per opinion, and the checks are ordinary actions

`OpinionFollowUp` is one row per sent `Submission`, enforced by a one-to-one.
It holds what no single check can:
- which opinion is being watched;
- the day the first check was computed from (`sent_on`, `first_due_on`);
- how the watching ended (`state`: `MONITORING`, `RESPONSE_RECEIVED`, `ENDED`
  or `CANCELLED`, plus `ended_at`, `ended_by` and `end_reason`).

The work is `NextAction`. Each check is an ordinary dated action with
`follow_up` pointing at its row, so this is not a second task engine. The
relation is explicit and set only by the scheduler. Nothing is matched by
title, date, filename, addressee or chronology.

- **One opinion with several addressees** gets one check, worded in the plural
  («Kontrolli, kas adressaadid …»). Every addressee is shown beside it.
  `FOR_INFORMATION` recipients are not addressees.
- **Several opinions on one Matter** each get their own follow-up and their
  own checks. Finishing one never touches another.
- **The database holds the shape**:
  - one follow-up per opinion;
  - one planned or current check per follow-up
    (`workflow_one_active_check_per_follow_up`);
  - a check is dated;
  - an outcome only on a completed check, and a completed check always has one.

**Idempotent and concurrent-safe.** Every write locks the Matter, then the
submission or the check — the order every service follows. A repeated
`Märgi saadetuks` is refused by the send itself. A second press of a drawn
`Koja arvamus` is refused by its one-time token. A repeated scheduling call
finds the first follow-up and writes nothing. Two simultaneous sends of one
draft leave one send and one check. If scheduling fails, the send is rolled
back with it.

## 4. Current and planned actions are untouched

The check is created `PLANNED`, beside whatever is current. It never supersedes
the current action, and it never cancels, moves or rewrites another planned
row. It becomes current only by the established rule (docs/adr/0143 §A4): when
the current action is completed, the earliest planned action is promoted.

So a `Koja arvamus` saved with `Märgi praegune tegevus tehtuks` completes the
step and then promotes the earliest planned row. That is often the new check,
due 30 days later, and `PRAEGUNE TEGEVUS` then reads it.

**New work never makes a check disappear.** When `set_next_action` writes new
work over a *current* check — a `+ Märge` dated ahead, or an import — the check
goes back to the plan on its own day instead of being superseded. Both moves
are audited.

## 5. Finishing a check needs a typed outcome

`✓ Tehtud` on a check asks «Mis selgus?»:

- **`Vastus saabunud`.** The check is done and the monitoring ends answered.
  `Selgitus` and files are optional and carry the answer. The Matter stays open
  and no new task is created. Nothing infers a reply from uploaded files or
  correspondence: the lawyer decides.
- **`Vastust ei ole — kontrollin uuesti`.** The check is done, and the next
  check is planned on a day the lawyer chooses (required, not in the past).
  It keeps the same opinion and text, the Matter's owner and the same
  restriction. It is never an automatic 30 days and never an endless
  recurrence.
- **`Lõpetan jälgimise`.** The check is done and the monitoring ends. A written
  reason is required and kept on the follow-up row. Nothing is scheduled and
  the Matter stays open.

It is one `composer_operation`: the note, its files, the completion with
`follow_up_outcome`, and the next check. `Teema käik` therefore reads one «✓»
row, e.g. «Vastust ei ole — kontrollin uuesti 7.12.2026. …», with the next
check under it. The note opens with the outcome the person chose, which is a
typed fact, never an invented sentence. It is restricted with the check
(docs/adr/0138).

Finishing a check that is the current action promotes the next planned row, as
completing any current action does. Finishing a planned one promotes nothing
(docs/adr/0144 §1). Nothing is half-done on failure: a check is never left
completed with its next check missing.

**No generic control finishes, rewrites or removes a check.** Each of these
refuses one, with the sentence that sends the person to the check's own form:
- the current action's `Mida tegid?`;
- a planned row's `✓ Tehtud`;
- `Muuda` on either;
- `×`;
- a ticked `Märgi praegune tegevus tehtuks` on `Koja arvamus` or
  `Lõpeta kaasamine`.

The pages do not draw those controls for a check at all. The database refuses a
completed check without an outcome. An unanswered opinion stops being watched
only through `Lõpetan jälgimise`, with a reason.

## 6. The day can always be moved, in place

`Muuda` on a check, planned or current, changes its day and nothing else:
earlier or later, before the day or long after it (`reschedule_check`). It is
**the same check**, with the same id, opinion, responsible person and
restriction. `NEXT_ACTION_RESCHEDULED` records `from` and `to`, and the
automatic day stays on the follow-up row. A day already in the past is refused.

**A corrected sending date** (`correct_sent_opinion`) moves the first check to
the corrected date + 30, but only while all of these hold:
- the monitoring is still running;
- no check has been done yet;
- the check still stands on its automatic day.

A day a lawyer chose is never silently overwritten. Correcting the title, kind,
summary or recipients creates no second check. A completed check's completion
time is history, not a scheduling field.

## 7. Where a check is seen

The check is real work and appears wherever work does: the Matter's
`PRAEGUNE TEGEVUS` (as a planned row, or as the current action), Minu asjad,
Osakond, and the dated-work read model (`work_items`).

Each row shows the day and the sentence, plus one quiet line naming the opinion:
«Koja arvamus 8.10.2026 · Rahandusministeerium», with a link to the exact
letter that went out. A work row links to the check's own row
(`#jarelkontroll-<id>`).

**A planned check whose day has passed is overdue**, in the colour and words
«N p üle» the current action uses (`NextAction.is_overdue`, `overdue()`).
Minu asjad bands it red and Osakond lists it under «Vajab sekkumist». It is
never rescheduled, completed or duplicated by the passing of a day. Every other
planned action keeps docs/adr/0143's reading: not late until it is current. The
register's «hilinenud» filter and Statistika's overdue count still mean the
current action. Nothing about a check is e-mailed.

## 8. Closing, reopening, withdrawing

**Closure is allowed, but it asks first.** When a closure would end a planned
or current check, every door that can close a Matter refuses once with the
owner's sentence:

> Sellel teemal on pooleli Koja arvamuse järelkontroll. Teema sulgemisel
> lõpetatakse ka planeeritud järelkontroll.

The doors are a terminal `Hetkeseis` from `Muuda teemat`, from the header,
from `✓ Tehtud` and from `Koja arvamus`. The refusal shows the box
`Sulge teema ja lõpeta ka järelkontroll`, or a single button in the header.
`close_matter` asks the same question under the Matter's lock against what the
database holds now. A form drawn before a check existed therefore cannot close
past it. Operations that store a file ask before they store it.

Once confirmed, the existing lifecycle cancels the checks: «Teema suleti», kept
in the history, linked to their opinions. Each follow-up ends `CANCELLED`. Sent
opinions and completed checks are untouched. A `Koja arvamus` whose stage ends
the Matter needs the same confirmation: it schedules its check and the closure
cancels it.

The register cutovers and historical closures are not a person closing
anything. They end checks without asking, through `end_live_work_for_closure`.

The warning is asked of the database, not of the reader. That is safe only
because every role that may write — and so close a Matter — also sees
restricted work (`ROLES_WITH_BUSINESS_WRITE ⊆ ROLES_WITH_RESTRICTED_ACCESS`).
Nobody who could be told «a check is pending» is refused the check, and a test
fails if the two role sets ever part.

**Reopening recreates nothing.** A cancelled follow-up stays cancelled. An
explicit continuation can be designed separately.

**A withdrawn or superseded opinion** cancels its planned or current check with
«Arvamus võeti tagasi» / «Arvamus asendati». Its completed checks stay. A check
that was the current action hands the slot to the earliest planned row, as
finishing it would have. A newly
sent replacement is a new opinion with its own check. An interactive send on a
closed Matter is still refused, and no live check is created on one by any path.

## 9. Historical opinions

No opinion sent before this release gets a check, and no process sweeps the
archive. A lawyer who registers an old send now gets a check from its real
date, overdue if it is. That is the one way history enters the queue, by a
person's act.

## 10. Visibility and audit

**A check is never more visible than its opinion or its Matter.**
- It is created with the opinion's restriction (copied, docs/adr/0138).
- `NextAction.objects.visible_to` also reads the opinion's restriction live,
  through `follow_up__submission`.

So an opinion restricted later hides its check, the check's events, the line
naming the opinion and its link too. The pages that scope `NextAction` without
`visible_to` — the audit projection, work activity and the overview feed — ask
the same rule (`child_scope_q`). The opinion's line is read through
`Submission.objects.visible_to`, and the letter's link through
`Document.objects.visible_to`.

**Audit, on the existing events:**

| Event | When | Payload |
| --- | --- | --- |
| `NEXT_ACTION_SET` | the first check is scheduled | `follow_up.automatic: true` — scheduled by the system, attributed to the person whose send it was, never to an invented actor |
| `NEXT_ACTION_RESCHEDULED` | a day is moved or recalculated | `from`, `to`; `recalculated` when a corrected sending date moved it |
| `NEXT_ACTION_COMPLETED` | a check is finished | `follow_up.outcome` |
| `NEXT_ACTION_SET` | the next check is planned | `automatic: false`, `check`, `after` |
| `NEXT_ACTION_CANCELLED` | closure or withdrawal ends a check | the reason, `follow_up` |
| `NEXT_ACTION_SET` | a check goes back to the plan | `returned_to_plan` |

`Teema käik` folds the scheduled check under «Arvamus välja», so one act is one
row. A moved day is audit-only, as a review is. The integrity verifier
(`check_domain_invariants`) reports:
- a check on another Matter than its opinion;
- a live check on ended monitoring;
- monitoring with no live check;
- a live check on an opinion no longer sent.

**Known display limit.** A `Koja arvamus` whose own stage closes the Matter
schedules its check and the closure cancels it in the same save. «Arvamus välja»
still reads «→ Kontrolli, kas adressaat …» under it: the fold shows what the send
set. The closure row below says the file ended.

## 11. What this does not build

Stage one only. Not built:
- recording what an answer said, or whether the Chamber's proposals were
  accepted;
- tracking a draft's progress;
- detecting stalled initiatives or long silences;
- e-mail reminders;
- a recurrence rule;
- a general workflow engine.

The extension points are the stable relation (`NextAction.follow_up` →
`OpinionFollowUp.submission`) and the typed `FollowUpOutcome` / `FollowUpState`.
A later stage can attach typed facts to a follow-up without reading prose, and
no speculative table was added for it.

## Amends

- **docs/adr/0143 §A6.** A planned *check* is overdue on the work surfaces once
  its day has passed. Promotion and closure work as stated there, and apply to
  checks.
- **docs/adr/0144 §1.** A planned check's `✓ Tehtud` asks for a typed outcome,
  and its row has no `×`. Its `Muuda` moves the day only.
- **docs/adr/0126 §2, §4.** `Märgi praegune tegevus tehtuks` is not offered
  beside a check. A send's row folds the check it scheduled.

## Not changed

These keep their behaviour:
- the current action's uniqueness;
- what `COMPLETED`, `CANCELLED` and `SUPERSEDED` mean for any other action;
- the response-deadline link (docs/adr/0135);
- the plan suggestion;
- the evidence rules and the send services' refusals;
- the business-write boundary;
- search (`INDEX_VERSION` unchanged).
