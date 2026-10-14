# 0163 — An initiative's send meets its own deadline, a withdrawal says why, and a same-day request on a date-only send is not judged

**Status:** accepted — owner decisions D7 and D10 of 2026-10-14 (QA remediation
round), and the correction of QA-WORKFLOW-04 under the ADR 0151 amendment of
2026-10-13. **No migrations**, no data migration, no backfill.
**Date:** 2026-10-14
**Narrows:** 0151 (amendment of 2026-10-13, §4), 0160 §4, 0120 §6 / 0102.

## 1. Every interactive send on a `Koja algatus` Teema answers its own deadline (QA-WORKFLOW-04)

### Before

The 2026-10-13 amendment of ADR 0151 decided that an initiative may hold its own
optional `Arvamuse tähtaeg`, that it is *not a request* (§3), and that the send
of the proposal answers it (§4). Only the fast path implemented §4:
«Märgi saadetud Koja arvamuseks» on `Uus teema` ended the deadline as
`ANSWERED`. A proposal filed as work to prepare (path A) and sent later through
`+ Koja arvamus` — or Dokumendid «Registreeri saatmine» / «Märgi saadetuks» —
left the deadline current: in `response_obligations`, in the header, and
overdue once its day passed. Every deadline written by a person carries
`response_requested_at` (`intake.file_new_matter`, the header's
`change_response_deadline`), so the implicit discharge of ADR 0059 did not
apply; the only escape was ticking «Vastab arvamuse küsimisele», whose words
describe a request an initiative never has.

### Now

- **`answer_initiative_deadline_of`** (`app/submissions/services.py`, over
  `response_deadlines.answer_initiative_deadline_by_send`) is called by the two
  interactive send wrappers — `register_sent_opinion_on_open_matter` and
  `mark_submission_sent_on_open_matter` — beside `schedule_follow_up_of`, inside
  the send's transaction and under the Matter's lock. On a `Koja algatus` Teema
  with a current deadline it ends that deadline `ANSWERED`, linked to exactly
  that `Submission`, and clears the field. Every door that records the proposal
  going out (`Uus teema` fast path, `+ Koja arvamus`, «Registreeri saatmine»,
  «Märgi saadetuks») therefore converges on one state.
- **Nothing is fabricated.** No request is created; `INITIATIVE_HAS_NO_NEW_REQUEST`
  still refuses both acts that mean Koda was asked. On an ordinary Teema the
  deadline is a request and is still answered only by an opinion a person
  names (`answers_deadline` / «Vastab arvamuse küsimisele»). Importers and the
  archive apply call neither wrapper: a historical letter answers nothing.
- **Several sends.** A second send finds no current deadline and does nothing.
  A deadline the lawyer sets after the first send is the next proposal's due
  day, and the next send answers it — each history row names its own send.
- **The `+ Koja arvamus` panel on an initiative** no longer offers «Vastab
  arvamuse küsimisele»; it says «Saatmine täidab ettepaneku tähtaja: tähtaeg …»
  and posts no field. A form drawn earlier that still posts `vastab_tahtajale`
  is answered once, not twice (the save reads the deadline back after the send).
  The form's field names and URL are unchanged.
- **`intake.py` is not changed.** Recording `response_requested_at` with an
  initiative's deadline keeps it a tracked deadline (never discharged by an
  unrelated send); the send-time rule above is what meets it.

## 2. A withdrawal needs a reason, on an open and a closed Teema (D7)

- Before: `withdraw_submission` took an optional `reason` that no form ever
  posted; a sent opinion on a closed Teema could be withdrawn (no open-Matter
  lock, by design — ADR 0120 §6, `submissions.views.metadata`) with no reason.
- Now: **a reason is required for every withdrawal**, not only on a closed
  Teema — the act and its consequences (out of every count of sent opinions and
  out of reporting) are the same on an open one, and a rule that differed by
  Matter state would be a second rule to remember. `withdraw_submission`
  refuses an empty or blank reason (`WITHDRAWAL_NEEDS_A_REASON`, «Tagasivõtmiseks
  kirjuta põhjus.») before any lock or write; the confirmation
  (`submissions/partials/withdraw_confirm.html`) carries a `required`
  `Põhjus` box (`name="reason"`, max 500, the length the audit payload keeps).
- The model is unchanged: the reason lives in the `SUBMISSION_WITHDRAWN` audit
  row's payload, as it always could, and `Teema käik` reads it back under the
  withdrawal's line («Põhjus»). Withdrawals recorded before carry none and
  draw no line.
- Still true, now tested on a closed Teema: the original send, its final
  evidence and the audit history stay; the Teema is not reopened; its stage,
  disposition, deadline, steps and checks are untouched (the withdrawn
  opinion's own live check, if any, is cancelled per ADR 0146 §9 — a closed
  Teema has none left).

## 3. A request recorded on the sending day of a date-only send is «ei saa kontrollida» (D10)

- Before (ADR 0160 §4): for a date-only send a deadline *change* on the
  sending day was treated as after it, but a *request* recorded that day was
  treated as before it, so the answer was judged on time or late
  (DC-STATS-02).
- Now: such an answer is not verifiable — its own group
  `SAME_DAY_REQUEST` «Küsiti saatmise päeval, järjekord teadmata», counted,
  listed (`/statistika/loend/`), never in the percentage. A request on an
  earlier day, or a timestamped send (whose instants are ordered), is judged
  exactly as before; a change after the send keeps its own group, which takes
  precedence. `OPINIONS_ON_TIME` is version 2 and its exclusions say so. No
  per-Matter surface judges timeliness, so Statistika is the only reader.

## Reversibility

Entirely by code. Deadlines answered by §1 are ordinary `MatterResponseDeadline`
rows; a withdrawal reason is an audit payload value.
