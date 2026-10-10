# ADR 0152 — Closing a Teema confirms the live work it would end

- **Status:** accepted
- **Date:** 2026-10-10
- **Context:** the product owner's answer to F3 of the 2026-10-09 brief and the
  follow-up question of 2026-10-10 («Confirm all live work»).
- **Extends:** 0146 §8 (the follow-up closure confirmation) to every kind of
  live work; **narrows** 0143's «closure cancels all» and 0131 §10–§11 (a
  terminal `Hetkeseis` closes the Matter) by asking first.

## Context

A `Hetkeseis` that ends a Matter — «Jõustunud», «Rohkem ei tegele» — closes it,
and `end_live_work_for_closure` ends what the Matter owes only while it is
current work: the current step, every planned step and planned
`Arvamuse järelkontroll`, every planned website overview and every open
`Kaasamine` feedback wait. Each is kept in the history (cancelled with «Teema
suleti», or closed with the Matter's closure as its reason); nothing is deleted.
But only a pending check made the person confirm it (0146 §8). Everything else
ended without a word.

The owner, answering F3: an EU regulation is «Jõustunud» even while Estonian
implementing measures are pending, and «Jõustunud» closes the Teema — so any
remaining substantive work must be placed before it closes, for example on a
linked national Teema, and **no outstanding work or follow-up may be discarded
silently**. Asked how, the owner chose: confirm all live work.

## Decision

1. **Any live work stops a closure until the person confirms.**
   `refuse_unconfirmed_closure` — asked early by every save that would store
   files ahead of a closure, and again by `close_matter` under the Matter's
   lock — refuses whenever closure would end anything
   (`app.matters.closure_work.live_work_at_closure`). Closure is never
   forbidden: confirmed, it goes ahead exactly as before.
2. **The refusal says what, in counts.** «Teema sulgemisel lõpetatakse ka
   pooleli töö: praegune tegevus, 2 planeeritud tegevust, 1 planeeritud
   kodulehe ülevaade ja 1 kaasamise tagasiside ootus. Kui midagi neist tuleb
   jätkata, vii see enne sulgemist teisele, seotud teemale.» Kinds and counts,
   never the records' own words: the question is reader-blind (closure ends
   every one of them, whoever may see them), so the sentence must not quote a
   restricted step to somebody who may not read it.
3. **The confirming box says what it does**: «Sulge teema ja lõpeta pooleli
   töö». When a check is all there is, ADR 0146 §8's sentence and box —
   «Sulge teema ja lõpeta ka järelkontroll» — are kept word for word.
4. **A step the same save completes is done, not outstanding.** «✓ Tehtud»
   that also chooses «Jõustunud», and a `Koja arvamus` that was the step, pass
   that step as `finishing`; a send that schedules a check in the same save
   counts it (`will_check`).
5. **One mechanism, every path.** The existing exception
   (`FollowUpClosureUnconfirmed`, now carrying the sentence and the box's
   words), field (`confirm_follow_up_closure`) and partial serve Muuda
   teemat, the header's stage editor, «✓ Tehtud», «+ Märge» and «+ Koja
   arvamus». The register's retirement and the historical cutover, which close
   nothing on a person's behalf, still call `end_live_work_for_closure`
   directly and ask nothing.

## Consequences

- A lawyer closing a file with work still on it sees, before anything is
  written, what will end — and can move it to a linked Teema first.
- Browser and server tests that closed a Matter with live work now confirm, as
  the person would.

## Reversibility

Entirely, by code: no migration and no data change.
