# 0121 — A date, an address and an upload mean what the person meant

**Status:** accepted
**Date:** 2026-09-30

**The owner's second user-side correction batch**, one release. It confirms the
parts of docs/adr/0120 that production already carries and adds nine decisions.
Each narrows or reverses an earlier record, named where it applies. **No
migrations**: `Disposition.OTHER` («Muu») has been in the vocabulary since the
first migration and `ChangeEvent.operation_id` since the composer, so nothing
here changes a table, a constraint or the search index.

1. **One next step, confirmed** — docs/adr/0120 §1–§2 stand.
2. **The Minu asjad portfolio reads the Matter's own step, whoever carries it.**
3. **A date may be in the past, today or the future**; what it means is read
   where it is used. ENG-004's refusal is withdrawn; the sent-opinion rule stays.
4. **`Kaasamine`: `Muuda` edits what `+ Kaasamine` asked**, and a legacy
   `Link` / `Märkus` is kept.
5. **A web address needs no `https://`, through one rule; a link reads as its
   name.**
6. **One upload of several files is one `Teema käik` action.**
7. **Minu asjad: a broad period is never this week's.**
8. **`Lõpeta teema` offers `Muu`.**
9. **`Lõpeta teema → Märgi töövõiduks` records the ordinary `Töövõit` in the
   same act.**

---

## 1. The next step, confirmed

docs/adr/0120 §1 (no «Koja arvamus on saadetud» sentence; the empty state is
«Järgmine samm on määramata») and §2 (an open `Järgmiseks` is authoritative;
otherwise the earliest active, visible `Oluline tähtaeg` whose period has not
ended; surfaced, never converted) are what production runs and are not
changed — with one correction to how several candidates are ranked: **the one
due first wins** (period end, then anchor, then key) instead of the one whose
stored anchor is earliest. Ranked by anchor, a whole-year «2026» — stored as
1 January — outranked a concrete deadline due tomorrow, which is §7's defect
in another place: the first day of a period standing in for a day nobody named.
An informational `Märge` never answers the question, whatever its date (§3). docs/adr/0120 §3 (`Tagasisidet ootame kuni` on
`+ Kaasamine`, optional, no default), §5 (document corrections and their refusal
table), §6 (`Võta tagasi` on the opinion row) and §7 (`Kustuta` only where the
deletion plan has no blocker) likewise stand and were re-verified.

## 2. The portfolio row states the Matter's step

`Minu asjad`'s portfolio listed a person's *Matters* but read only the
`Järgmiseks` rows that *person* carries. A file owned by Marko whose step Ireen
carries showed its step on its own page, was not counted by the strip's
«järgmise tegevuseta» (`?tegevus=puudub`) — and read «Järgmise tegevuseta»
under the portfolio chip of the same name, on the same page. A Matter has at
most one open step (`workflow_one_open_action_per_matter`), so `build_portfolio`
now reads each Matter's open step, reader-scoped, the way the Matter page and
`without_next_step` do. The bands above — *my* work — are unchanged.

## 3. Dates: valid input versus what a date means

The application records what happened, what is happening and what is planned,
and a person typing a date is not told that tomorrow is not allowed. **ENG-004's
refusal of a date after today is withdrawn** for the four records it covered:
`Kaasamise kuupäev`, a `Väline seisukoht`'s date (`Teiste arvamus`, `Meile
saadetud tagasiside`), a `Märge` (`MatterProceduralDevelopment`) and a
published `Ülevaade / uudis`. Every writer and every form accepts a past, today's
or a future date at every precision the record supports, and a correction may
move a date forward. `Järgmine tegevus`, `Oluline tähtaeg`, `Tagasisidet ootame
kuni`, `Jõustumine`, a `Töövõit` and the timeline's steps already accepted any
day. Relational rules are not date-policy and stay: a reply-by date may not
precede its round (ENG-043), and years stay within 1990–2100.

What a date *means* is decided where it is read:

* **Teema käik draws the row at once and marks it `Eesolev`** beside its date
  (`ChronologyMilestone.ahead`, decided by the record's own period through
  `period_starts_after`: «september 2026» read in September is not ahead). It
  used to leave such a row off the page until its day — with its `Muuda` — which
  is why ENG-004 refused the date in the first place. `Eesolev` is the word an
  upcoming `Oluline tähtaeg` already carries.
* **A round dated ahead is not the file's last activity** (`annotate_last_activity`
  counts a `Kaasamine` whose period has begun).
* **A `Märge` dated ahead is not evidence that a phase was reached, nor its
  date** (`recorded_phase_keys`, `legal_process_rail`) until its day comes.
* **Nothing dated ahead becomes the next step** except through §1's rule.
* `check_domain_invariants` no longer reports `engagement-in-future`,
  `external-position-in-future` or `website-overview-published-in-future`: they
  are valid data now.

**Kept, deliberately: a `Koja arvamus` recorded as sent may not be dated after
today** (`SENT_DATE_IN_THE_FUTURE`, ENG-043). `SENT` is a state that asserts the
letter went out: it locks the evidence (docs/adr/0120 §5), is counted in the
sent-opinion statistics and is what withdrawal reverses. A send that has not
happened is a planned step — `Järgmine tegevus` or `Oluline tähtaeg` — not a
sent opinion, so this is a domain invariant rather than a date policy.
**Supersedes** docs/adr/0120's closing «Dates» paragraph («the existing ENG-004
rule … is unchanged») for the four records above.

## 4. `Kaasamine`: create and edit ask the same things

`Muuda` on a `Kaasamine` offered a generic `Link` and `Märkus` that `+ Kaasamine`
never asks — leftovers of the five-field form (docs/adr/0027), which nothing on
the chronology printed. The editor now offers exactly the panel's fields:
`Keda kaasati`, `Vastuseid`, `Kaasamise kuupäev`, `Tagasisidet ootame kuni`,
`Saadud tagasiside / arvamused`, `Smaily link`, `Alchemer link`. **No second
link concept.** A value an older round stored in `url` or `note` is kept: the
form does not carry the fields and the view does not name them, so
`update_engagement`'s `_UNSET` leaves them exactly as they are on every save —
the shape `Liik` already has (docs/adr/0086 §1). They stay in search and in the
audit history. The compatibility create door stops writing them too.

## 5. Web addresses and how a link reads

**One rule** — `app/core/web_addresses.py`, `normalize_web_address` — for every
ordinary web-address box: `Menetluse link`, `Smaily` / `Alchemer`, `Teiste
arvamus`, `Meile saadetud tagasiside`, `Ülevaade / uudis`, a working document's
SharePoint address, and a `Jõustumine`'s `Ametlik allikas` / a `Töövõit`'s
`Viide`. It was `_normalize_public_link` in `app.matters.services` (which now
delegates) plus a looser copy in `link_working_document` and Django's own
`URLField` in the two fact forms. `with_web_scheme` adds `https://` to an
address that **looks like a host** — the part before the first `/`, `?` or `#`
is a dotted name ending in a letter-only top-level label, optionally with a
port, and nothing is whitespace — and leaves everything else as typed, so
`www.delfi.ee` and `delfi.ee/uudised?id=123` are stored with `https://`,
`https://…` and `http://…` are never changed and never doubled, and `kampaania`,
`ei ole aadress`, `mailto:` and `javascript:` are still refused. The two fact
forms' `<input type="url">`, which made the *browser* refuse `delfi.ee`, are
text boxes with the URL keyboard. **Narrows** docs/adr/0089's «nothing is
rewritten … no scheme upgraded»: a scheme is still never upgraded or replaced;
a missing one is supplied.

**A link reads as its name.** A stylesheet `::after` meant to draw ↗ after every
`Teema käik` link had been saved as a control byte and «97», so a `Kaasamine`
read «Smaily 97 Alchemer 97» beside its count of replies. The rule is removed:
a link is its label, and «avaneb uues aknas» stays visually hidden. An
`Ülevaade / uudis` link reads «Ülevaade / uudis» (`MatterWebsiteOverview.
link_label`) instead of its address cut at 72 characters; the address is the
link's `title`, the rest of its accessible name — so three write-ups on one
file are still told apart, which was docs/adr/0105 §3's reason — and the box
`Muuda` opens with. **Narrows** docs/adr/0105 §3. `Väline seisukoht` links keep
their host label (docs/adr/0084 §3).

## 6. One upload, one action

Creating a Matter with seven files wrote «Marko Udras lisas dokumendi» seven
times. `Teema käik` already folds every event sharing a `ChangeEvent.
operation_id` into one row, and only an explicit operation groups anything —
never a minute, never an author (docs/adr/0092 §6). The files of one `Uus teema`
press (staged and posted) and of one `Saabunud` press are now written inside
one `composer_operation()`, and the row's clause counts them:
«lisas dokumendi», «lisas 2 dokumenti», «lisas 7 dokumenti» — any number. Only
the files: `Teema loodud`, the scratch note, the handover note and the first
step are their own rows. Each document keeps its own `DOCUMENT_CREATED` and
`EVIDENCE_VERSION_ADDED`; the grouping is the chronology's reading, never a
merged record. A later upload is a new request and a new identifier, so it is
its own row even in the same second. The count is taken from the row's own
events, which are already scoped to documents the reader may see and from which
a removed document's upload has gone (AUTH-003, docs/adr/0120 §5). The e-mail
worker's attachment rows are left as they are.

## 7. Minu asjad: a broad period is never this week's

docs/adr/0120 §4 banded a period by its **last day**, and a review by its
start. Both still made a weekly deadline out of a day nobody named: «september
2026» sat under *Sel nädalal* in the week September ended, and a review for
«oktoober 2026» the day October began. **A month, a quarter, a half-year or a
year that has not ended is never *Sel nädalal*** (nor *Järgmised 30 päeva*); it
is *Hiljem* and prints as the period it is («oktoober 2026», «IV kvartal 2026»,
«2026»). Only once the whole period is behind us is it placed with the other
past items — overdue, or come round for a look — because then no representative
day is chosen. Exact dates band as before. Every section's count is the length
of the row set it heads (unchanged: `WorkBand.total` before the render cap, the
strip's figures the bands' totals). **Supersedes** docs/adr/0120 §4's
«its last day falls this week, or it is a review whose period has begun».

## 8. `Muu`

`Lõpeta teema` offers four outcomes: `Jõustus`, `Menetlus lõppes`, `Loobuti` and
**`Muu`**, which is `Disposition.OTHER` — stored as itself, read back as «Muu» on
the closed banner and the rail, carried in `MATTER_CLOSED`'s payload, and
cleared by reopening like any other. It is never mapped onto one of the other
three. **Amends** docs/adr/0074 §10's three chips.

## 9. `Märgi töövõiduks`

An unticked checkbox on `Lõpeta teema`; unticked, the closure is exactly what it
was. Ticked, it opens `Töövõidu märkus` and the save records **the ordinary
`Töövõit`**: `CompactClosureForm` refuses an empty description with `+ Märge →
Töövõit`'s own sentence (`WORK_VICTORY_NEEDS_TEXT`), states the closing day as
that panel states a day (`work_victory_kwargs`), and
`close_matter_from_workspace` hands the result to `add_matter_work_victory` — the
same use case, service (`add_confirmed_work_victory`), record, audit event
(`WORK_VICTORY_CONFIRMED`), authorization and reporting — before `close_matter`,
in one transaction and one operation. The order is the only one that works and
the one `_apply_closure` already uses: a win is recorded on an *open* file.
Either refusal unwinds both. **Amends** docs/adr/0074 §10's «no work-victory
question» for this optional box; a win is still never inferred from a closure.

---

## Not changed

`Document` / `DocumentVersion` immutability and the evidence rules; the
withdrawal service; the deletion guard; the audit history, which stays
append-only (grouping is a reading); the horizontal process timeline's phase
logic; the e-mail worker; `Väline seisukoht` link labels; the register's other
populations; the search index.
