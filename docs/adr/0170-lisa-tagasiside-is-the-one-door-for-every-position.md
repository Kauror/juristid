# 0170 — `Lisa tagasiside` is the one door for every position

**Status:** accepted — owner's incremental UX round, stage 3 (2026-10-15).
**Date:** 2026-10-15

**No migrations**, no data migration, no backfill, no new model and no
existing row rewritten. Search, statistics, the chronology and every
correction surface read the same `MatterExternalPosition` rows they always
read.

Amends docs/adr/0091 §3.5 (two chips, provenance fixed by the chip),
docs/adr/0095 §4–§5 (which panel asks `Liige`; `Seotud kaasamine` not asked on
creation), docs/adr/0142 §B and its amendments (`Lisa tagasiside` is tied to a
round) and docs/adr/0167 §2 (only round answers go through `Lisa tagasiside`).
It **keeps** docs/adr/0084 §3 (the three-source rule), docs/adr/0091 §3.2
(provenance is stated, never inferred) and §3.3 (a published opinion names its
author), docs/adr/0095 §4 (the member flag in three layers), docs/adr/0101
(received feedback may name nobody) and docs/adr/0162 (a reply to a restricted
round is restricted). Those decisions are not rewritten; this record only
changes which form asks them.

## Context

A position on a Teema could be recorded through three doors, all writing the
same record through the same function (`_record_external_position` →
`record_external_position`):

* `+ Arvamus / tagasiside · Meile saadetud tagasiside` — `RECEIVED`;
* `+ Arvamus / tagasiside · Teiste arvamus` — `DISCOVERED`;
* `+ Kaasamine · Lisa tagasiside` — `RECEIVED`, tied to a round.

A lawyer holding a member's e-mail had to know in advance which of three doors
the application filed it under, and the round-only door could not take feedback
that answered no round. The owner asked for one compact form under
`+ Kaasamine`, without losing what the three doors could each record.

## Decision

### 1. Why one form, and why provenance still matters

The three doors were one record with one shape; what differed was how the
position reached Koda. That difference is real and stays on every row:
feedback **sent to Koda** is evidence of engagement with the Chamber, a
position **published elsewhere** is someone else's public view. Statistics, the
chronology heading («Meile saadetud tagasiside» / «Teiste arvamus») and the
member flag all read it. So the doors merge and the question moves into the
form: `Kuidas seisukoht meieni jõudis?` — «Saadeti Kojale» (`RECEIVED`) or
«Avaldatud mujal» (`DISCOVERED`).

The radio is **required and has no default**. It is the person's statement,
never derived from `Liige`, an organisation, a date, a link, a file or the
existence of a round on the Matter (0091 §3.2). The request now carries the
value — the chip no longer does — but the form accepts only those two answers;
`LEGACY` is still refused as an answer (0091 §3.4).

### 2. The form (`FeedbackForm`, route `matters:add_engagement_reply`)

* `Organisatsioon` — the shared «otsi või lisa asutus» picker (docs/adr/0073):
  * **optional for «Saadeti Kojale»**. Received feedback may name nobody: a
    caller who gave no name, an individual, an anonymous survey answer. A
    required box here is what puts invented organisations on a professional
    file (docs/adr/0101, OWNER-01);
  * **required for «Avaldatud mujal»**: an unattributed published opinion is
    an anonymous claim (0091 §3.3).
* `Liige` — selectable only beside «Saadeti Kojale» (hidden otherwise, and
  cleared by the page when the answer changes). Ticked by the person and
  inferred from nothing — not the catalogue, the CRM or other Matter data. The
  form refuses it on «Avaldatud mujal», and so do the service
  (`EXTERNAL_POSITION_MEMBER_IS_RECEIVED_ONLY`) and
  `matters_external_position_member_is_received` (0095 §4).
* `Seisukoht`, `Link`, `Failid` — **at least one of the three, any
  combination** (0084 §3, amended 2026-09-16). A published position may be
  only its address; a position paper may be only its PDF; a member's call may
  be only two sentences. Nothing is invented for the boxes left empty — no
  placeholder summary — and an entirely empty record is refused with the
  existing sentence. The «last live source» safeguard on `Eemalda dokument`
  (0167 §1) is unchanged.
* `Seotud kaasamine` — optional; see §3.
* `Kuupäev` — opens on today, clearable (0078 §2).

No `Märkus` (0142, amendment of 2026-10-12) and no `Allikas` (0095 §4): both
are still asked by `Muuda` on rows that carry them.

### 3. Linking a consultation is a statement, never a guess

`Seotud kaasamine` offers this Matter's rounds as this reader may see them
(`answerable_rounds`, the round's own `visible_to`), open ones first, finished
ones marked «lõpetatud». **It is never preselected**, not even when exactly one
round exists: the form now also records positions published elsewhere about
the same proposal, and a preselected round would silently turn a published
opinion — or unsolicited feedback — into an answer to Koda's consultation and
end its wait. The one exception is `Ootame tagasisidet → Tehtud`
(docs/adr/0168): that control names the round itself, so the form opens with
that round selected and «Saadeti Kojale» checked.

A linked round is Koda's own consultation, so its answer was **sent to Koda**:
the provenance is `RECEIVED` and cannot be changed — «Avaldatud mujal» is
disabled on the page and refused by the form
(`FEEDBACK_LINKED_ROUND_IS_RECEIVED`) rather than rewritten. A save with a
round and no stated provenance (no-script) is that same `RECEIVED`.

The rule is the **service's**, not only the form's: `record_external_position`
refuses `DISCOVERED` with a round, and `correct_external_position` refuses a
`DISCOVERED` row a round it did not already hold
(`EXTERNAL_POSITION_ROUND_ANSWER_IS_RECEIVED`). A link a historical row already
carries is kept, so the row stays correctable without being rewritten; `LEGACY`
rows are not judged. The page never draws a radio both checked and disabled, and
draws `Liige` ticked only beside a shown «Saadeti Kojale», so a refused form
re-rendered and saved again cannot post an answer the person could not see. A
«Saadeti Kojale» checked because a round was chosen (or by `Tehtud`) is cleared
again when the round is cleared, unless the person chose it.

### 4. Recording a reply versus recording a published opinion

Only a position **linked to a round** is a reply to it:

* an identical repeat to the round is refused (`refuse_repeated_reply`) —
  identical now including the **author**: the same organisation (or none) and the
  same `Liige` mark. The round-only form named no author, so equal words were the
  same answer; now two organisations' «Toetame.» are two answers (0167 D3: an
  accidental duplicate is prevented, a deliberate one is not);
* the first answer to an open round finishes its wait in the same transaction
  (`finish_wait_on_reply` → `complete_engagement_feedback`);
* a later answer to a finished round is recorded against it and the closure
  stays; earlier answers are untouched (0167 §2).

A standalone position — received or published elsewhere — opens, finishes or
touches no wait. No save here creates a `NextAction`, a deadline, a plan step
or a stage change.

### 5. The old doors are retired

`Meile saadetud tagasiside` and `Teiste arvamus` leave `+ Arvamus /
tagasiside`, which kept only `Koja arvamus` — since docs/adr/0097 (amendment of
2026-10-17) the family is «+ Koja arvamus / kohtumine» with `Koja arvamus` and
`Kohtumine / arutelu`, and still no feedback choice. Their routes, `matters:add_received_feedback` and
`matters:add_external_position`, are **removed, not redirected**: a stale tab's
POST is a 404 and writes nothing, so an old page can never save a record under
a provenance its new form did not ask (the ENG-050A precedent,
`tests/test_retired_write_doors.py`). `ReceivedFeedbackForm`,
`OtherOpinionForm`, `CompactExternalPositionForm` and
`external_position_panel.html` are deleted. Per docs/adr/0155 these controls
stay retired unless the owner decides otherwise.

### 6. What did not move

* **Historical records and labels.** Stored rows keep their provenance, dates,
  organisations, notes and labels; the chronology still reads «Meile saadetud
  tagasiside» and «Teiste arvamus», which describe provenance, not the form.
  `LEGACY` rows read as they always did.
* **Correction and evidence.** `Muuda` (`update_external_position`, history
  kept), `+ Lisa fail` on the row and `Eemalda dokument` are unchanged.
* **Restrictions.** A position linked to a restricted round is restricted with
  its files even on an unrestricted Matter (0162), through the same capture;
  a reader cannot name a round they cannot see.
* **Boundaries.** Readers, the shared-gate visitor and other non-writers are
  refused by `business_write_required`; a closed Matter is refused under the
  Matter's lock; a round on another Matter is refused by the field and the
  service; a refused save writes nothing — no position, no new organisation,
  no file — and re-renders the form with what was typed.

## Consequences

* One door, one record, the provenance asked in plain words.
* Every capability of the three doors survives: anonymous received feedback,
  link-only and file-only positions, members, round replies and later replies.
* The opinion family holds no feedback choice; stage 4 renamed it
  «+ Koja arvamus / kohtumine» (docs/adr/0097, amendment of 2026-10-17).

## Reversibility

Code-only. Restoring the two routes and panels restores the previous doors; no
stored row depends on which form wrote it.
