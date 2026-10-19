# 0172 — The owner's acceptance fixes of 2026-10-19

**Status:** accepted — the product owner's corrective brief after the 19.10 release (2026-10-19).
**Date:** 2026-10-19

**No migrations**, no data migration, no backfill, no new model and no
existing row rewritten. Every fix below is a reading of records that already
exist, a word on a page, or a form's starting value.

Amends docs/adr/0144 §2 (what `Järgmisena?` suggests after a `Kaasamine`),
docs/adr/0097 (amendment of 2026-10-15: `Millal?` of `+ Lisa · Muu` had no
default) and docs/adr/0131 §13 (`Menetluse kulg` drew no watched deadline and
no `Töövõit`). It **keeps** docs/adr/0140 §7 (`Tegevused` is the section's
name) and makes the rest of the interface agree with it.

## Context

The owner's manual acceptance walk on production 5d549ab8 found six things:

1. With `Ootame tagasisidet` showing, the same block suggested
   `JÄRGMISENA: Koosta kodulehe ülevaade · Alusta`.
2. `Kõik muudatused` sent the reader to «Teema käik», a section the Teema page
   has called `Tegevused` since docs/adr/0140 §7.
3. The 19.10 release note sent a planned meeting to «Järgmine tegevus»; the
   button is `+ Lisa tegevus`.
4. `+ Lisa · Muu` opened with an empty `Millal?`, which was filled in by hand
   almost every time.
5. A saved `Muu` «did not appear» in `Tegevused`.
6. An `Oluline tähtaeg` («isiklik põhjus», 21.10.2026) and a `Töövõit`
   (19.10.2026) read in `Tegevused` and on no timeline.

## Decision

### 1. A filed `Kaasamine` puts the overview behind the file

`Järgmisena?` reads the record (docs/adr/0144 §2): the first open step of the
standard sequence that the record does not prove done. A `Kaasamine` proves
`read-material` and `consult-members`, but only a published `Ülevaade` proved
`website-overview` — so on a file with a round out, the first step still open
was the overview, both while the wait was open and after it closed.

Now a filed `Kaasamine` — waiting or finished, visible to the reader — also
means the record has **moved past** the overview: by then it has been written
or will not be (`RecordedWork.supersedes`). The overview step is not
suggested; the next valid step is. The wait closing does not bring it back,
because the round stays on the file.

* Nothing is written. The plan row stays `SUGGESTED`, no `SKIPPED` is stored, no
  audit event is invented — a manual task, a dismissal and every historical
  row are untouched.
* `RecordedWork.proves` is unchanged: it says what *was done*, and the
  docs/adr/0161 alert reads it. A filed round is not proof that an overview was
  written, so an overview step somebody made current keeps the alert's answer.
* A current action still shadows every suggestion (docs/adr/0141 §3).
* A round removed with `Kustuta` stops superseding, as every record-read rule
  here does.

### 2. `Tegevused`, everywhere a reader meets it

Every user-facing sentence that named «Teema käik» names `Tegevused`:
`Kõik muudatused`, the removal and withdrawal confirmations, the document
page's removal note, the two document refusals, `Kokkuvõte`'s help, the empty
list, the 15.10 release note, and the bounded list's «Näita kogu käik», now
«Näita kõik tegevused». Code comments, identifiers, audit history and
the release notes written before the rename (5.10) keep the old word, because
there it describes what the page said at the time. The `removed_at` model
`help_text` keeps it too: it is read only by the admin, and changing it would be
a migration for a sentence no lawyer sees.

### 3. `+ Lisa tegevus` is the button

The 19.10 note now says «plaanitu jaoks on «+ Lisa tegevus» või «Oluline
tähtaeg»». «Järgmine tegevus» stays where it is the concept or a field of the
completion form, which is a different thing from the button.

### 4. `Millal?` says today

A fresh `+ Lisa · Muu` form is drawn with today's Tallinn day
(`timezone.localdate()`; `TIME_ZONE = "Europe/Tallinn"`), and the page renews
it to today's Tallinn day — read with `Intl.DateTimeFormat` in
`Europe/Tallinn`, whatever the computer's own zone — when the panel is opened
or the box takes focus, as long as the box still holds what it was drawn with.
A page left open overnight therefore opens the form on the new day. A day the
person typed or picked is never touched; a refused save redisplays what was
posted, without the marker; the future-day refusal is unchanged; `Muuda` of a
saved entry never touches its day.

### 5. A `Muu` reads at the head of its day, and says what it is

The entry was never missing. On the owner's Teema (read-only probe on
production) it was stored, visible, in the open current period — and it was
the **last** row, under «Teema loodud», reading only «Kaur · MUU · 19.10.2026».
Two causes:

* `Millal?` is stored as the start of the Tallinn day, and the chronology sorted
  on that midnight, so the row read below everything else done that day —
  even the creation of the Teema it was added to.
* A closed row is one line, and for an authored entry that line is author and
  day. A `Muu` is not a note, and «Kaur · MUU» said nothing about what was
  added.

So a day-dated entry — `Muu` or `Kohtumine`, stored at local midnight — sorts
where every row dated by day already does: at the end of its day, the save
moment breaking ties (`entry_chronology_moment`). The bounded read keeps such
an entry whenever its day is read, as it does for records. And a `Muu`'s closed
line carries the first words of its `Kirjeldus` (`entry_gist`, 90 characters,
plain text), renewed out of band when `Muuda` corrects it. Nothing stored
changes; links and files keep their established presentation and permission
rules.

### 6. Watched deadlines and `Töövõit` on `Menetluse kulg`, as events

docs/adr/0131 §13 narrowed the rail to the procedure's own points. The owner
wants a file's dated events on it too, **as events, not stages**. They are
drawn as `KIND_EVENT` columns:

* an `ACTIVE` `Oluline tähtaeg` (not the transposition deadline, which keeps its
  own column), passed or ahead — «Eesolev tähtaeg» above its title while ahead,
  «Tähtaeg» once passed — at its last day;
* a `Töövõit` with the visible status and a period — «Töövõit» above its title —
  at the start of its period, read as reached once that has begun.

They are placed by date exactly as every unanchored dated point is (beside the
current phase, against any dated phase), so the stages keep their meaning,
order and state; no workflow state and no stage is created. A diamond dot,
the kind in words and the record's own title at the body weight tell them
apart without colour. Columns are capped at 11rem and wrap between words; the
strip scrolls itself, never the page. Cancelled, replaced, removed and
restricted records draw nothing, and the rail is rebuilt on every save, edit
and removal. `+ Lisa samm` does not offer an event as the place a step goes.

## Consequences

* Tests that pinned the overview suggestion after a round, the old wording and
  the rail's absence of watched deadlines are rewritten to the new rules.
* Visual baselines that include the rail or the chronology of a seeded file with
  watched dates or a `Töövõit` change.

## Reversibility

Each section is a reading or a template change and reverts with its code.
Nothing is stored that depends on it.
