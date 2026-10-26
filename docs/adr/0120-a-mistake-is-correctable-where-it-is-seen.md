# 0120 — A mistake is correctable where it is seen, and a plan the lawyer wrote is the next step

**Status:** accepted
**Date:** 2026-09-29

**One correction batch, from the owner's user-side QA of 29 September**
(`UQ-09`–`UQ-13` and two items the report put under UQ-01). Each part narrows
or reverses an earlier record, named where it applies. Two migrations, both
additive: `documents/0012_removable_documents` (the two `RemovableRecord`
columns on `Document`) and `audit/0030_document_correction_event_types` (two
`choices`, no SQL). No backfill, no `RunPython`, no search-index version change.

Seven decisions:

1. **`PRAEGUNE TEGEVUS` says nothing about a sent opinion.**
2. **An upcoming `Oluline tähtaeg` is the next step when no `Järgmiseks` is set.**
3. **`+ Kaasamine` asks `Tagasisidet ootame kuni` again — optional and empty.**
4. **A period is banded by when it is due, printed in words, and each figure
   counts its own section.**
5. **A document can be re-versioned, reclassified and removed — never its bytes.**
6. **`Võta tagasi` is on the opinion, behind a confirmation.**
7. **`Kustuta` is offered only where the deletion plan has no blocker.**

---

## 1. The continuation sentence is retired

«Koja arvamus on saadetud. Menetlus võib jätkuda — lisa märge.» was written by
docs/adr/0091 §5.5 so a finished file with a sent opinion would not read as a
dead end. The owner found it unnecessary and asked for it gone, with nothing in
its place: the empty state is the one line «Järgmine samm on määramata». Its
template branch, its `opinion_sent` context value and its two CSS rules are
removed; the rest of the panel is unchanged. **Supersedes docs/adr/0091 §5.5.**

## 2. The next step, answered in one place

Recording «Ootan ministeeriumi tagasisidet, 1.10.2026» as an `Oluline tähtaeg`
left the Matter saying «Järgmine samm on määramata» above it and `Minu asjad`
listing the file under «Järgmise tegevuseta». From the lawyer's side both were
false.

The rule lives in `app/matters/next_step.py` and nowhere else:

* an **open `NextAction`** the reader may see — dated or not
  (docs/adr/0106) — is the next step, always;
* otherwise the **earliest `MatterImportantDate`** that is `ACTIVE`, not removed,
  visible to the reader, and whose **period has not ended** (`period_end >=
  today`, the same reading `has_passed` and `matter_intelligence` give it — a
  milestone dated today is still today's, «oktoober 2026» is ahead of us until
  31 October);
* ties broken by the model's own order: anchor, then the period's end, then the
  primary key — so the Matter page, a `Minu asjad` row and the register cannot
  pick different records.

  **Narrowed on 2026-09-30 by docs/adr/0121 §1:** ranked by the period's end
  first (the one due first), then the anchor, then the key.

The milestone is **surfaced, not converted.** No `NextAction` is written, it
keeps its own label («Oluline tähtaeg»), and `PRAEGUNE TEGEVUS` draws no
`Mida tegid?` under it — a milestone has no completion of its own; it stops
being the next step when it is edited to another date, cancelled, removed or its
period ends, and the page recalculates on the next render. A past milestone was
valid to record and stays in `Teema käik`; it is simply never *next*.
`MatterImportantDate`'s docstring — «not `NextAction.target_date`» — still holds:
the two are different facts, and this is a reading of one in the absence of the
other.

**The milestone's `Teema käik` row gains `Muuda` and `Tühista`.** They are
links to the pages that already corrected and cancelled an `Oluline tähtaeg`
(`intelligence:edit_important_date`, `intelligence:cancel_important_date`) and
that nothing on the Teema page reached: the row offered only `Kustuta`. Once the
milestone *is* the file's next step, moving its day or calling it off has to be
possible where it is seen — the one dependency this part has on a surface
outside the brief, and no new rule or service.

The SQL form, `without_next_step`, replaces four restatements of «no open
action»: the register's `?tegevus=puudub` (`filter_by_next_action`),
`dashboard.without_next_action` (Osakond), `selectors.matters_without_next_action`
(the attention list) and Statistika's `active_without_next_action`. Every one of
them links to or is counted against `?tegevus=puudub`, so they change together
or a figure and its list disagree. `Minu asjad`'s portfolio row reads the same
rule over the work items it already holds (`milestone_is_upcoming`,
`milestone_order`). **Narrows** the `PortfolioRow` comment that called an
`Oluline tähtaeg` «deliberately not eligible».

## 3. The feedback deadline on create

docs/adr/0091 §2 took `Tagasisidet ootame kuni` off `+ Kaasamine` because a
*pre-filled* box opened waits nobody had decided on. The consequence was UQ-10:
the one fact that makes a round a wait could only be given by saving it and then
opening `Muuda`. The box is back on the panel as **the same field object**
`EngagementForm` uses (`engagement_feedback_deadline_field`), **with no default**,
checked by the same date-order rule (`refuse_deadline_before_engagement`) and
passed to the same service parameter. Empty, it stores nothing and opens no
wait; filled, it is exactly the wait `Ootan tagasisidet` opens: one `OOTAME
TAGASISIDET` work item, «Tagasiside tähtaeg» on the rail, `Lõpeta kaasamine`.
**Supersedes docs/adr/0091 §2's «not asked at all»**; its objection to a default
stands.

## 4. Minu asjad: when a period is due, how it reads, what a figure counts

* **Banding.** A period — month, quarter, half-year, year — is *Sel nädalal* only
  when its **last day** falls this week, or when it is a review whose period has
  begun (the moment `review_has_come_round` already names). Otherwise it is
  **Hiljem**, never *Järgmised 30 päeva*. Banding on the anchor put «oktoober
  2026» into *Sel nädalal* on 29 September because 1 October is a Thursday.
  Exact dates band exactly as before.
  **Superseded on 2026-09-30 by docs/adr/0121 §7:** a period that has not
  ended is never *Sel nädalal*, whichever of its days falls this week — not its
  last day, and not a review's first. It is *Hiljem* until it has ended.

* **Labels.** `WorkItem.short_date` and `meaning_line` print a month as
  `format_at_precision` does everywhere else — «oktoober 2026», not `10.26` —
  and a quarter and a year as they already did («IV kvartal 2026», «2026»).
  `WorkItem.compact_month` is removed; `MatterActivityFact.compact_display` (the
  rail's «Viimati muudetud») loses its `MM.YY` month the same way. **Supersedes
  design handoff 01 §3.2's two-number month.**
* **Figures.** The strip's «üle tähtaja» and «sel nädalal» are the **totals of
  the two bands** below them and open those bands in place (`#ule_tahtaja`,
  `#sel_nadalal`). They were distinct Matters from `work_population_ids`, and
  «2 tähtaeg sel nädalal» sat over a «Sel nädalal 3» whose third row was an
  `OOTAME TAGASISIDET` wait. The caption is «sel nädalal» because the band holds
  more than deadlines. The register populations are unchanged and still answer
  the manager's Kiirvaade, Osakond and Ülevaade.

## 5. Correcting a document

`Dokumendid → ⋯` opened a page that said «parandus on uus versioon» and offered
nothing (UQ-12). The document's own page now carries **Paranda dokumenti**, for
a writer on an open Matter:

* **`Lisa uus versioon`** posts to the existing `documents:add_version` route and
  `add_version_on_open_matter`: a new immutable `DocumentVersion`, made current,
  every earlier version kept in `Versioonid`; one document, not a second row.
* **`Muuda liiki`** — `change_document_role`, over the upload's own vocabulary
  (`offered_document_roles`: `Arvamus` stays reachable only through `Koja
  arvamus`; `Tulemuse tõend` is not offered but kept if already stored). Metadata
  only; audited as `DOCUMENT_ROLE_CHANGED` with the old and new value.
* **`Eemalda dokument`** — `remove_document`: ADR 0102's removal for the file
  itself. `Document` gains `removed_at`/`removed_by`; `Document.objects.visible_to`
  drops removed rows, so Dokumendid, its tab count, the files under `Teema käik`
  rows, product downloads and every count stop offering it; `DocumentLink` and
  the e-mail provenance lines drop it; the search rows are withdrawn by the
  save's signal and never rebuilt (`refresh_documents`, `indexable_fragments`,
  `check_search_integrity` all count live documents); the upload line leaves
  `Teema käik`. Every version, every byte, every `ChangeEvent` stays;
  `DOCUMENT_REMOVED` joins them in `Kõik muudatused`.

**What refuses, stated once for the page and the service**
(`new_version_refusal`, `role_change_refusal`, `removal_refusal`, all over
`opinion_evidence_statuses`):

| the file is the `final_version` of an opinion that is… | new version | liik | remove |
| --- | --- | --- | --- |
| `SENT` | refused — withdraw first | refused | **refused — withdraw first** |
| `SUPERSEDED` / `DRAFT` | refused | refused | refused |
| `WITHDRAWN` | refused | refused | allowed |
| none | allowed | allowed | allowed unless under legal hold |

A withdrawn opinion's letter may leave the list because the withdrawal was the
decision that it does not stand; the withdrawn `Submission` keeps its
`final_version` (`PROTECT`) and the bytes stay. A standing opinion's letter
never does. `check_evidence_is_usable` gains a third rule — a removed document
is not evidence — read from the row after the Matter's lock, which removal also
takes. **DATA-001 is untouched**: removal changes neither a document's Matter
nor its restriction. **Narrows docs/adr/0102 §4** («evidence stays») to the files
an opinion stands on.

## 6. Withdrawal where the opinion is

`Võta tagasi` is on the opinion's own row in `Teema käik`, for a `SENT`
submission, beside `Muuda`; the Dokumendid menu keeps it. Both include one
partial (`submissions/partials/withdraw_confirm.html`) — a native disclosure
whose sentence says what happens (out of the sent-opinion counts and reporting;
the record, date and file stay; nothing is deleted) — and both post to
`submissions:withdraw`, i.e. `withdraw_submission`: one transition, one
`SUBMISSION_WITHDRAWN`. `tagasi=teema`, a fixed word, returns to the row.

## 7. `Kustuta` only where deletion can succeed

The header asks `plan_matter_deletion(matter).is_blocked` — the plan
`matter_delete` renders and `delete_matter` rebuilds under the lock — and draws
`Kustuta` only when nothing blocks. `Muuda teemat`'s danger zone prints the
plan's refusals and points to `+ Lõpeta teema` instead of a button that could
only fail. The route is unchanged and still refuses. The plan costs tens of
queries, so only a writer's header asks it; the Matter page's query budget test
now measures the page against its ceiling plus the plan's own count.
**Supersedes docs/adr/0096 §4's unconditional offer**; its safety rules stand.

**Superseded on 2026-10-10 for how the header asks — see the amendment at the
end of this document.** The header asks a few `EXISTS` instead of the plan; the
plan is still built where the deletion is confirmed and carried out.

---

## Dates: valid input versus what a date means

No part of this adds a rule that a date must be in the future. A next step, an
`Oluline tähtaeg`, a feedback deadline and a timeline step accept past, today and
future days at every precision; what the date *means* is decided on reading —
a past milestone is history and never the next step, a past feedback deadline
reads «Tagasiside tähtaeg möödus». The existing ENG-004 rule that a record of
something that **happened** — a `Märge`, `Kaasamise kuupäev`, a send — is not
dated after today is unchanged.

**Superseded on 2026-09-30 by docs/adr/0121 §3** for a `Märge`, a
`Kaasamise kuupäev`, a `Väline seisukoht` and a published `Ülevaade / uudis`:
each accepts a future date and reads `Eesolev` in Teema käik. The sent-opinion
rule (ENG-043) is kept.

## Not changed

The timeline's phase logic, anchors and staleness (UQ-02–UQ-08, UQ-14), the
Osakond and register populations beyond `?tegevus=puudub`, `DocumentVersion`
immutability, the evidence triggers, the deletion guard, and the withdrawal
domain behaviour.

---

## Amendment, 2026-10-10 — the offer asks a few `EXISTS`, not the whole plan

- Status: accepted, amending §7's «The header asks `plan_matter_deletion(matter).is_blocked`»
- Scope: the header's `Kustuta` offer only; the confirmation page, `Muuda
  teemat`'s danger zone and `delete_matter` are unchanged

### What was decided before

Every writer's Teema page, documents tab and edit page built the whole
deletion plan to decide whether to draw one link.

### Why it is superseded

Audit AUD-002 measured the plan at 105–109 queries and 127–205 ms of every
writer's page view. Most pages are viewed many times and deleted never.

### What is decided now

The header asks `deletion_may_be_offered(matter)`. It runs one `EXISTS` for
each blocker that is a fact about this Matter's own rows:

- a legal hold;
- a successor Matter;
- a corrected entry;
- another Matter's opinion standing on a version filed here;
- a background citation of this Matter's opinion;
- an e-mail attachment link that crosses Matters.

The plan is built where it is acted on: the confirmation page renders it, and
`delete_matter` rebuilds it under the lock.

### What this amendment does not change

The rule itself stands: an action that can only fail is not offered, for every
blocker the check names. Every refusal is the route's, as before. A structural
blocker the check does not yet name is told on the confirmation page rather
than by withholding the link, so the offer can be wrong only towards offering.
It never hides a deletion that could succeed. Permissions, the evidence
safety rules and the deletion semantics are untouched.

## Amendment, 2026-10-19 — an informational milestone is never the next step

### What was decided before

§2: when no `Järgmiseks` is set, the nearest upcoming `Oluline tähtaeg` —
*any* active, visible one whose period has not ended — is the file's next step,
on the Teema, on `Minu asjad`, in the register and in every
«järgmine tegevus puudub» population (`without_next_step`).

### Why it is narrowed

The rule was written for a milestone that says what the file is waiting for:
«Ootan ministeeriumi tagasisidet, 1.10.2026». It also caught a milestone that
says nothing about the lawyer's work: a commencement a *draft* proposes. A
Teema with an open `Arvamuse tähtaeg`, a draft's «Eelnõus kavandatud jõustumine
20.05.2027» and no step read that milestone in `PRAEGUNE TEGEVUS`, left
`järgmise tegevuseta` and `Vajab sekkumist`, and hid the one thing those
populations exist to show — that nobody has said what happens next. No
`NextAction` was written; the reading made it look as if one had been. (Found on
the twelve-topic evidence pilot's isolated rehearsal, 2026-10-19.)

### What is decided now

`ImportantDateKind` gains `PLANNED_COMMENCEMENT` — «Eelnõus kavandatud
jõustumine» — and the kinds are split explicitly by
`INFORMATIONAL_IMPORTANT_DATE_KINDS` (`app/intelligence/enums.py`). A milestone
of an informational kind:

- is **never the next step** — `next_step.milestone_is_upcoming`,
  `upcoming_milestone`, `upcoming_milestones` and so `without_next_step` skip it;
  `Minu asjad`'s portfolio row skips it through the work item's
  `is_informational`;
- is **never late** — its period ending is not Koda's lateness, so the work item
  and the header's nearest deadline do not call it overdue;
- **stays a dated fact** on the Teema — `Menetluse kulg`, `Tegevused`, the
  header's nearest date — and in the work lists as a date, exactly like any
  other milestone.

Decided by the stored `kind`, never by the title. **Safe default:** `OTHER` —
every row written so far — and `TRANSPOSITION_DEADLINE` keep §2 unchanged;
moving a kind into the informational set is a product decision. One additive
migration, `intelligence/0004`, re-creates the kind vocabulary's check with the
new value; no data changes and nothing is backfilled. The `Oluline tähtaeg`
form does not ask the kind (unchanged); importers and later surfaces set it.

### What this amendment does not change

An open `Järgmiseks` (DO, WAIT or MONITOR, dated or not), a `PLANNED` action
and a round waiting for feedback are the next step exactly as before; a
cancelled, past, removed or restricted milestone is never one; every probe is
still `visible_to` the reader. No `NextAction` is created by any reading.

## Amendment, 2026-10-20 — a source's estimate is informational, and is not the bill's date

The owner selected Lovable's 27.05.2027 as 2026_225's prospective commencement
in place of the bill's own 20.05.2027 (evidence pilot v2.3). Recording that
estimate as `PLANNED_COMMENCEMENT`, «Eelnõus kavandatud jõustumine», would
attribute a tracker's estimate to the text of bill 947 SE. So
`ImportantDateKind` gains **`ESTIMATED_COMMENCEMENT`**, «Eeldatav jõustumine
(allika hinnang)»: a commencement somebody estimated, as opposed to one a draft
states. It is the second member of `INFORMATIONAL_IMPORTANT_DATE_KINDS`, so it
is never the next step and never late, exactly like the first. The value joins
the same, still unreleased migration (now `intelligence/0004_informational_commencement_kinds`);
no data changes, nothing is backfilled, and the kind is still never read from a
title.

## Amendment of 2026-10-26 — the document page opens on the file

**Owner's «Five UX Corrections» brief, fix 5 (presentation only).** The
document's own page (`templates/documents/document_detail.html`) is reordered:

1. **Identity** — the title (or filename) as a plain heading that wraps, and one
   line of role, `Piiratud`, file type, size and «Versioon N / M»; the filename
   under it when it differs from the title.
2. **The way to the file** — «Ava fail» as the primary action only where
   `app/documents/inline.py` lets the browser render the format, in a new tab;
   otherwise «Laadi alla» is primary, so the page never promises an opening
   that turns into a download. A copy icon copies this exact version's
   authorized address (no public link). The heading is no longer the download.
3. **«Muuda dokumenti»** — what this ADR introduced as **Paranda dokumenti**,
   now a closed disclosure holding the same four forms (`Lisa uus versioon`,
   `Pealkiri`, `Liik`, `Eemalda dokument` with its confirmation), each with its
   own route, refusal and audit. A refused correction redirects to
   `?muuda=<versioon|pealkiri|liik|eemalda>#muuda-dokumenti`, which opens it
   (and the removal confirmation for `eemalda`). Absent for a reader and on a
   closed Matter, as before.
4. **Originaal** — filename, received date and processing state visible; type,
   exact size, OneNote day, version number, SHA-256, provenance and the version
   history (open and download per version) under «Dokumendi andmed». Cells wrap
   inside themselves, so a long filename no longer runs under the next label.

On Dokumendid the row's «Vaata sisu» is gone (it led to the same page as the
title), the upload button reads «↑ Lae üles», and `Ava`, the download icon and
`⋯` sit together in their own column. No route, model, migration, evidence
byte, version, extraction or search behaviour changed.
