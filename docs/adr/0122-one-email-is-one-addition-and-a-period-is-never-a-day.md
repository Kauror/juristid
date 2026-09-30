# 0122 — One e-mail is one addition, and a period is never a day

**Status:** accepted
**Date:** 2026-09-30

**A consistency batch after docs/adr/0121**, one release. Three decisions, each
extending a rule production already runs to the places that still disagreed
with it. **No migrations**: `ChangeEvent.operation_id` has existed since the
composer, and every date already carries its `DatePrecision`.

1. **One received e-mail is one `Teema käik` addition**, however many
   attachments it carried — the rule an upload of several files follows since
   docs/adr/0121 §6.
2. **A deadline recorded as a month, a quarter or a year is classified as the
   period it is on every deadline surface**, by one rule — the rule Minu asjad
   follows since docs/adr/0121 §7 — and is overdue only once the period has
   ended.
3. **Production source carries no raw control byte**: the `\b` of
   `opinion_sources.ADDRESSEE_SEPARATOR`, saved as two backspace bytes, is
   restored.

---

## 1. One e-mail, one addition

A message filed on a Matter is opened by the extraction pass, and
`register_email_attachments` makes each real attachment a `Document` of its own
(Stage-2B brief 25). Each wrote its own `DOCUMENT_CREATED` and
`EVIDENCE_VERSION_ADDED` with no operation, so an e-mail carrying eight annexes
read «lisas dokumendi» eight times — the noise docs/adr/0121 §6 removed for
`Uus teema` and `Saabunud` and deliberately left here.

**The attachments of one e-mail are now one operation**, and `Teema käik` folds
them by the mechanism it already has (docs/adr/0092 §6): «lisas dokumendi»,
«lisas 2 dokumenti», «lisas 8 dokumenti» — any number, under the name of the
person who filed the message, with one file link per attachment. There is no
second grouping concept and no new vocabulary.

**The identifier is the message's own.** `email_intake_operation_id` derives it
(UUID v5, a fixed namespace) from the message's `DocumentVersion` — the row
every `EmailAttachmentLink.parent_version` already names and the stable
identity of one received e-mail. So:

* **two e-mails are two rows**, whatever their timing: two identifiers can only
  coincide for one message. Nothing groups by time, author or filename;
* a message **inside** a message is an e-mail of its own, read by its own pass,
  under its own identifier;
* a later pass over the **same** message — a forced re-read that finds an
  attachment the first pass had to skip — adds to that e-mail's row rather than
  opening a second one for one e-mail. A pass that adds nothing writes nothing;
* the attachments are written inside `separate_operation`, not
  `composer_operation`: an operation running around the read is **never
  joined**, because two messages opened inside one request would otherwise fold
  into one row. The outer operation is restored afterwards.

**Only what was added is counted.** An empty part, a type the evidence store
refuses, an oversized part, an inline resource and a message past the nesting
limit are skipped before anything is written (ENG-033), so they write no event
and the row's count is the attachments that were actually added. An e-mail with
nothing usable adds no row at all. The count is read from the row's own events,
already scoped to what the reader may see (AUTH-003), exactly as for an upload.

**Nothing is merged.** Each attachment keeps its `Document`, its immutable
`DocumentVersion`, its `EmailAttachmentLink` (parent, ordinal, declared name)
and both audit events, each carrying the message's operation. The grouping is
the chronology's reading.

**Where the three sources stand.** `Uus teema` and `Saabunud` group one press
with `composer_operation()` (a new identifier per request); an e-mail groups
with the identifier of its message. One field, one fold, one clause
(`documents_added_clause`).

## 2. A period is never a day

### The defect

docs/adr/0121 §7 settled Minu asjad: a month, a quarter or a year that has not
ended is never *Sel nädalal* nor *Järgmised 30 päeva*; it is *Hiljem*. Every
other deadline window still compared the stored **anchor** — the first day of
the period (docs/adr/0079 §2) — in `work_items._deadlines_between`, the one
selector behind:

* Osakond's *Eesolev* windows (*Täna*, *Homme*, *Järgmine nädal*, *Ülejäänud
  kuu*, *Kaugemal*) and its «Kõik tähtajad» link;
* Osakond's «tähtaeg sel nädalal» figure and the Meeskond table's week column;
* the register's `?too=tahtaeg-nadalal / -jargmisel / -30 / -kaugemal /
  -vahemik`;
* the Kiirvaade's «Tähtaeg 30 p jooksul» on a colleague's Minu asjad;
* the Ülevaade / Statistika strip's «tähtaeg 30 p jooksul».

On 30 September «oktoober 2026» was *Hiljem* in Minu asjad, *Homme* on Osakond
and *Tähtaeg sel nädalal* in the register. And a period already running — its
anchor behind today, its last day ahead — was in **no** window until it became
overdue, which broke the partition `upcoming_windows` promises.

### The rule

`app.workflow.dates.period_in_window` — beside `period_starts_after`, and read
through `WorkItem.in_window` by every window above **and** by Minu asjad's
`band_of`:

* **A day** (`EXACT`, `INFERRED`) is in a window when it falls inside it.
* **A window with a last day never holds a period.** *Täna*, *Homme*, *Sel
  nädalal*, *Järgmine nädal*, *30 päeva jooksul*, *Ülejäänud kuu* are claims
  about days, and neither end of «oktoober 2026» is a day anybody named.
* **A window with no last day** — *Hiljem*, *Tähtaeg kaugemal*, *Kaugemal*,
  «Tähtaeg ees» — is the later category every set of windows ends in and the
  one that never implied a day. It holds every period that has not ended (and,
  when it opens in the past, every period that reaches it). A period is
  therefore in exactly one of a set of consecutive windows beginning today, as
  a day is.
* **A period is past only once its last day is** (docs/adr/0079 §4, unchanged):
  «oktoober 2026» is not overdue on 2 October nor on 31 October, and is overdue
  on 1 November; «2026» is not overdue in February 2026. An ended period is in
  no window ahead.

**Containment was considered and refused** — "a period is in a window that holds
every day of it". Whether a month fits depends on where a window happens to
begin, so «november 2026» would be *30 p jooksul* on the Ülevaade strip on
1 November while Minu asjad calls it *Hiljem* and the register *Tähtaeg
kaugemal*: the representative-day reading in another place. **Classifying by
the last day was refused** too, for docs/adr/0121 §7's reason: it makes a
weekly deadline out of the last day of a month nobody named.

**Display is unchanged except where it named a day.** A period prints as
itself — «oktoober 2026», «IV kvartal 2026», «2027» (`format_at_precision`),
never `10.26`. `WorkItem.is_today` is now false for a period, so an Osakond row
no longer reads «täna», and a Minu asjad row is no longer styled as today's, on
the first day of a month.

### The call sites (the audit)

Every comparison of a precision-carrying date with today or a window was listed
and decided:

| Surface | Reads | Decision |
|---|---|---|
| Minu asjad bands (`band_of`) | period (0121 §7) | **now through the rule**; unchanged in effect |
| Osakond *Eesolev*, «Kõik tähtajad», «tähtaeg sel nädalal», Meeskond week column | anchor | **B → the rule** |
| Register `?too=tahtaeg-*`, `?too_alates` / `?too_kuni` | anchor | **B → the rule** |
| Kiirvaade «Tähtaeg 30 p jooksul», Ülevaade strip «tähtaeg 30 p jooksul» | anchor | **B → the rule** |
| `WorkItem.is_today`, the work row's «today» styling | anchor | **B → a day only** |
| `week_items` (no live caller) | anchor | **B → the rule**, so no anchor window is left in the module |
| Overdue: `NextAction.is_overdue` / `days_late`, `overdue_date_q` (register `?tegevus=hilinenud`, Statistika), `MatterImportantDate.period_end`, `WorkItem.days_late` | period end | **correct, unchanged** (0079 §4–§5) |
| Review ripeness: `is_due_for_review`, `review_due_q`, `?too=ulevaatamiseks` | period start | **kept on purpose** (0079 §6: a review is a reminder that comes round when its period opens; a review is not a deadline and is in no deadline window) |
| Next step: `next_step.milestone_is_upcoming`, `upcoming_milestones`; `intelligence` upcoming/past; `distance_label` | period end | correct, unchanged |
| `Teema käik` «Eesolev» for a `Kaasamine`, `Väline seisukoht`, `Märge` | period start (`period_starts_after`) | correct, unchanged (0121 §3: an event, not a deadline) |
| `response_deadline`, `feedback_deadline`, `published_on` | exact by construction | **A**: exact logic is right |

**Audited and deliberately left out of this batch** — none is a deadline
window, and each is listed so the next reader does not rediscover it:

* **A `Jõustumine` (`MatterEffectiveDate`) is placed by its anchor** on `Teema
  käik` («Jõustus» from the day after the anchor), on the *Menetluse kulg*
  strip and on the rail, while `MatterEffectiveDate.has_passed` and the
  Statistika «jõustunud» figure read its period's end. In mid-October an
  «oktoober 2026» commencement reads *Jõustus* / REACHED beside an «oktoober
  2026» milestone reading AHEAD. The question is "has it taken effect", not
  "is it due", and changing it moves the strip and rail model (docs/adr/0119).
  **Resolved on 2026-09-30 by docs/adr/0123** — a period `Jõustumine` reads
  «Jõustub» / AHEAD until its period has ended and sits at the period's end
  on the strip and the rail, as `has_passed` and Statistika already read it.
* **An added `MatterTimelineStep` dated as a period is marked TODAY** on the
  rail on its anchor day (`legal_process._added_step`).
  **Resolved on 2026-09-30 by docs/adr/0123** — `_added_step` takes the
  step's state from the same period reading, so a period step is never TODAY
  and reads AHEAD until its period has ended.
* **`defer_action`** (`Lükka edasi`) is hidden for a period step, but the POST
  endpoint does not refuse one and computes from the anchor.
* Dead or test-only readers still compare the anchor:
  `selectors.active_deadline.days_remaining` / `is_today` (in the context, read
  by no template), `selectors.my_work_timeline`, `dashboard._upcoming_sources`,
  `OperationalSnapshot.was_overdue`.
* **Two figures captioned «30 p jooksul» count different windows**, a caption
  question rather than a precision one: the Kiirvaade's is the day after next
  week through today + 30 (`WORK_DEADLINE_30_DAYS`), the Ülevaade strip's is
  today through today + 30.

### Date entry is not touched

This is classification, not input. docs/adr/0121 §3 stands: every operational
date may be in the past, today or the future, at every precision the record
supports; only a sent `Koja arvamus` may not be dated after today (ENG-043).

## 3. The control byte in `opinion_sources.py`

`ADDRESSEE_SEPARATOR` (line 247) read `r"[,;/]|<BS>ning<BS>"`: two **0x08**
bytes where `\b` was meant. It entered with 1dfb5f27 (2026-09-01, «Match an
archived letter on what both sources actually wrote»), the commit that made a
recipient string a set of bodies. `\b` typed through a shell or a writer that
interprets escapes becomes a backspace, and inside a raw string the regex then
matches a literal backspace — which no recipient contains — so «A ning B» was
never split.

It was accidental, not intentional: the offline measurement that commit reports
split on `\s+ning\s+`, and `register_semantics._ADDRESSEE_SEPARATOR` — the
register's own reading of the same column — is `r"[,;/]|\bning\b"`. The bytes
are replaced with that escape.

**No imported value changes.** `addressee_bodies` is additive (the unsplit
string stays a key), production's `legacy_import` tables are empty since the
2026-09-21 reset, and in the 1 September reconciliation export no register
addressee (9 839) and no archive recipient (767) contains the word «ning» — the
restored split changes the body set of none of them.

`tests/test_consistency_batch_three.py` now reads the production trees (`app`,
`config`, `templates`, `static`, `scripts`, `deploy`) for any raw control byte,
the way `app.css` was guarded after docs/adr/0121 §5's «97».

**The same defect, left out of scope:** four test files carry `\b` saved as
0x08 in a regex, each making part of an assertion vacuous —
`e2e/test_process_strip.py:56`, `e2e/test_ui_shell.py:557`,
`tests/test_deployment_unraid.py:504`, `tests/test_templates.py:107`. Restoring
them changes what those tests check, so it is its own change.

---

## Not changed

`Document` / `DocumentVersion` immutability, the evidence rules and the
e-mail parser; `EmailAttachmentLink`; the audit history, which stays
append-only (grouping is a reading); the `Uus teema` / `Saabunud` grouping; the
lateness rule (0079 §4) and the review rule (0079 §6); what dates may be entered
(0121 §3); the windows' boundaries; the register's other populations; the
search index.
