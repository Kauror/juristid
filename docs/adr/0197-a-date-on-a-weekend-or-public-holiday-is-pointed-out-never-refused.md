# 0197 — A date on a weekend or a public holiday is pointed out, never refused

- Status: Proposed (owner's date-warning brief, 2026-10-30; **no migrations**;
  not merged)
- Relates to: 0146 (a sent opinion is checked on thirty days later — its day is
  not moved for weekends or holidays, and that is unchanged), 0171 (one
  delegated script, each feature failing on its own), QA-015 (an implausibly
  old date says so and saves anyway — the precedent this follows)

## Context

A day chosen in a date box is usually a deadline or the day something
happened, and a finger one row off in the calendar lands on a Saturday or on
jaanipäev without anybody noticing. Nothing in the application said so. A
deadline that genuinely falls on a Sunday is also real — the department records
what the other side wrote — so a refusal would be wrong.

## Decision

1. **A sentence under the box, and nothing more.** When somebody types, picks
   in the calendar or presses a quick-date chip and the box then holds a
   Saturday, a Sunday or a riigipüha, one line appears under it:
   `24.06.2027 valitud kuupäev on riigipüha (jaanipäev).`,
   `26.06.2027 valitud kuupäev on laupäev.`,
   `27.06.2027 valitud kuupäev on pühapäev.` A holiday on a weekend says only
   the holiday. No advice is added. It is a `role="status"` hint with the age
   check's style (`.field__hint--check`), named by the box's
   `aria-describedby` beside any id already there; it never sets
   `aria-invalid`, never blocks a save and is not a validation error.
2. **After a change only.** A box drawn already holding such a day says
   nothing on page load or after a swap: every edit form re-showing a recorded
   date would otherwise carry a sentence nobody asked about.
3. **Only a day somebody chose.** A box in a `Täpsus` control not set to
   «Täpne päev», a hidden `periodfields` group, a box not shown, and the
   register's filter boxes (`GET` forms — a search boundary, not a recorded
   day) say nothing. The first day a month, quarter or year is stored as is an
   anchor, not a choice. Changing the precision re-reads the boxes already
   touched.
4. **The holidays are defined once, on the server.** `app/core/holidays.py`
   holds the current Pühade ja tähtpäevade seadus list — uusaasta,
   iseseisvuspäev (the statute's «Eesti Vabariigi aastapäev,
   iseseisvuspäev», shortened to its second half so the line stays short),
   suur reede, ülestõusmispühade 1. püha, kevadpüha, nelipühade 1. püha,
   võidupüha, jaanipäev, taasiseseisvumispäev, jõululaupäev, esimene and teine
   jõulupüha — with Easter by the anonymous Gregorian (Meeus/Butcher)
   algorithm, and `date_notice` writes the sentence. The browser asks
   `GET /kuupaevad/riigipuhad/<year>/` (`core:public_holidays`,
   `login_required`, every signed-in reader including READER, GET/HEAD only,
   `Cache-Control: private, max-age=86400`, 1990–2100 else 404) once per year
   and reuses the answer; it computes no holiday itself, only the weekday. A
   failed request leaves the weekend sentences and is never an error.
5. **Current law for every supported year.** The list is applied to 1990–2100
   as the law stands now; the earlier versions of the law (days added or
   removed since 1990) are not modelled. Shortened working days (lühendatud
   tööpäevad) are working days and say nothing.
6. **ADR 0146 is unchanged.** A follow-up check is still due thirty calendar
   days after the send, not moved off a weekend or a holiday; the sentence only
   describes a day a person is choosing.

## Alternatives considered

- **Refuse or ask to confirm.** Rejected: a weekend deadline is a fact the
  department must be able to record without friction.
- **The holiday rules in JavaScript too.** Rejected: two copies of Easter
  disagree one day. The JSON route is tiny and cached.
- **The holidays embedded in every page.** Rejected: a dozen dates per year in
  every response for a hint most pages never show.
- **Showing it on load.** Rejected (decision 2).

## Consequences

`static/js/app.js` gains one delegated `input`/`change` listener; the age check
now adds and removes its own id in `aria-describedby` instead of replacing the
attribute, so the two hints and a refusal's message can describe one box
together. A change in the law is one edit to `app/core/holidays.py` and its
tests.

## Reversibility

Complete: no stored data, no migration. Removing the listener and the route
removes the feature.
