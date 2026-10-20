# ADR 0174 — An official «no comments» answer is a send, not an opinion

- Status: Proposed (2026-10-20), owner decision of 20.10.2026
- Deciders: the product owner; implemented in the evidence-pilot round

## Context

The Chamber answers many consultation requests with a short official e-mail or
letter: «Oleme eelnõuga tutvunud ning täiendavaid ettepanekuid ei ole.» The
owner's decision of 20.10.2026 states that this is an **official written
response**. Whether it is a signed PDF or an e-mail is a question of form and
evidence, not of whether Koda answered.

The vocabulary had no truthful place for it:
- `FORMAL_OPINION` counts it as an «arvamus välja» and schedules a thirty-day
  `Arvamuse järelkontroll` (ADR 0146), although it states no position anybody
  could act on;
- `INFORMAL_WRITTEN_RESPONSE` («Mitteametlik kirjalik vastus») calls an official
  answer informal merely because it was an e-mail.

A third case is not a send at all: the department reviews a proposal and decides
not to answer. That is an internal disposition, recorded as it is today (a
closure or a note). No `Submission` is ever fabricated for it, and nothing here
changes it.

## Decision

1. `SubmissionKind.NO_OBJECTION_RESPONSE`, «Vastus: märkusi ja ettepanekuid ei
   ole», is the kind of an official no-comments answer.
   `NOT_AN_OPINION_KINDS = {NO_OBJECTION_RESPONSE}`.
2. **It is a real send.** It is registered through the same services as any
   other send: `register_sent_opinion` and the interactive wrappers. It has:
   - a real sending day and recipients;
   - its original e-mail (`.msg`, `.eml`) or letter as final evidence;
   - the same visibility and search behaviour.

   No signed PDF is required, and none ever was for any kind. The evidence rules
   for a signed formal opinion are untouched.
3. **It answers the request.** Like every SENT submission, it discharges an
   untracked `Arvamuse tähtaeg`, and it is a valid `submission` for
   `resolve_response_deadline(..., outcome=ANSWERED)` on a tracked one.
4. **It is not counted as an opinion.** «Arvamusi välja» excludes it in
   Statistika (`reporting.selectors.submissions.sent_submissions` unless the
   kind is asked for by name), on Osakond and on Minu asjad
   (`department_dashboard.sent_submissions`). Statistika's «Aruandlus» rail adds
   «vastust märkusteta», this year's count. That figure is drawn only when it is
   non-zero and opens the same list filtered by the kind.
5. **It schedules no follow-up.** A «no comments» answer asks nothing of anybody,
   so `schedule_follow_up_of` returns without creating an `Arvamuse
   järelkontroll` for it. Every other kind is followed up exactly as before.
6. **It reads as an answer.** The chronology row is «Vastus välja», with the
   kind's label in its sub-line. The process rail step is «Koja vastus».

## Consequences

- One choices-only migration, `submissions/0011`. There is no database
  constraint on the vocabulary, no row changes, and every existing send keeps
  its kind.
- No existing figure changes for existing data, because nothing has this kind
  yet. A send recorded with it is counted on its own from then on.
- The kind can be chosen wherever a send's kind is chosen today (the Dokumendid
  registration form and the sent-opinion edit form). «+ Koja arvamus» still
  writes `FORMAL_OPINION`.
- Importers use it for reviewed historical answers. The 12-topic evidence pilot
  v2.3 records 2026_218's e-mail of 19.10.2026 to Kliimaministeerium with it.

## Alternatives considered

- **Reuse `INFORMAL_WRITTEN_RESPONSE`.** Rejected: the owner's decision is that
  the answer is official, and the label says the opposite.
- **A flag on `FORMAL_OPINION`.** Rejected: every count, follow-up and label
  would need to read two fields to say one thing.
