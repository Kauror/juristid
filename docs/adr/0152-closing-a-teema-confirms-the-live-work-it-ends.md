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

## Amendment, 2026-10-14 — removed records are not live work, and a confirmation names its work

Independent QA of 2026-10-14 found two gaps (QA-WORKFLOW-01, QA-WORKFLOW-02);
the owner's decision D9 of the same day is recorded in docs/adr/0146 §8.

6. **A record removed with «Kustuta» is not live work.** A `Kaasamine` or a
   planned `Kodulehe ülevaade` taken off the file keeps its row — the wait
   open, the plan `PLANNED` — but is on no surface. `live_work_at_closure`
   no longer counts it, `close_open_feedback_waits_for_closure` and
   `cancel_planned_website_overviews_for_closure` no longer end it (so no
   event is written about it), and the integrity checks
   `closed-matter-open-feedback-wait` / `closed-matter-planned-website-overview`
   no longer report it.
7. **The confirmation is bound to the work the warning counted.** The refusal
   carries a digest — 16 hex characters of a SHA-256 over the Matter's id and
   the ids of every live step, planned overview and open wait it counted (and
   «a check this save schedules», when the save is a send). The confirming box
   posts that digest as its value (`confirm_follow_up_closure`, now a
   `CharField`); the header's one button posts it as a hidden field; a refused
   response also names it in the `X-Closure-Digest` header for a caller whose
   panel is not on the page. Under the Matter's lock the digest is computed
   again: if a colleague added, finished, removed or replaced work in between,
   it differs and the closure is refused once more, the renewed warning
   prefixed «Pooleli töö on pärast hoiatust muutunud — vaata see üle ja kinnita
   uuesti.» and drawn with an unticked box and the new digest. A bare `on` (a
   tab drawn before this rule) confirms nothing. The digest is a hash of row
   ids, so it says nothing about a restricted record. `True` remains for code
   that closes on nobody's interactive behalf (seeds, tests) and for a save
   that verified the person's digest earlier in the same locked transaction —
   so the check a closing `Koja arvamus` schedules, or the step `✓ Tehtud`
   finishes, is not read as changed work. Every door asks the same way: `Muuda
   teemat`, the header stage editor, `✓ Tehtud`, `+ Märge`, `+ Koja arvamus`.
8. **`MATTER_CLOSED` names what it ended.** Its payload gains `ended`:
   `{"actions": [...], "overviews": [...], "waits": [...]}` — the ids of the
   rows the closure is about to cancel or close, read under the lock. Each row
   still writes its own `NEXT_ACTION_CANCELLED` / `WEBSITE_OVERVIEW_CANCELLED` /
   `ENGAGEMENT_FEEDBACK_CLOSED`; the list is what lets a reader of the history
   (Muudatused, D1) show one closure as one act. Additive; no event type and no
   existing key changed.

Reversible by code; no migration, no data change.
