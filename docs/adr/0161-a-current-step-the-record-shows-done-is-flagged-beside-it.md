# 0161 — A current step the record already shows done is flagged beside it

**Status:** accepted — owner's decision of 2026-10-13 (release preparation): the single deterministic, read-only workflow-consistency warning is accepted as specified here. No further alerts are authorised by this record.
**Date:** 2026-10-13

**No migrations**, no data migration, no backfill, nothing written by the
feature. One read-only alert on the Teema page, under `PRAEGUNE TEGEVUS`.

## Context

The brief asked for workflow-consistency alerts — and asked first for an audit
of what Juristid already checks, so that only genuine gaps are closed. Three
candidates were named: (A) the stage changed while the current action is
clearly obsolete; (B) a sent opinion lacks the subsequent work the canonical
workflow requires; (C) an active Matter has no current action where one is
needed. The audit below found that B and C are already covered, that A has no
structured relationship to read without inventing one, and that one adjacent
gap is real: **a current step started from the standard sequence stays current
after the record proves it done.**

Since docs/adr/0141 §2 no record fulfils a step. A lawyer who presses `Alusta`
on «Kaasa liikmeid / küsi tagasisidet» and then files the `Kaasamine` through
`LISA TEEMALE` (or publishes the overview, or sends the opinion without ticking
`Märgi praegune tegevus tehtuks`) keeps the old step as the current one, and
the page says nothing. `Järgmisena?` already knows the step is done — it skips
it (docs/adr/0144 §2) — but it is asked only while nothing is current.

## Decision

1. **One alert, `step-already-recorded`** (`app/workflow/consistency.py`). It
   appears when every clause holds, each a structured fact:
   - the Matter is open and a full record (`RecordMode.FULL`);
   - the reader's current step (`selectors.current_action_of`, reader-scoped)
     is `OPEN`, `DO`, not an `Arvamuse järelkontroll`, and carries
     `NextAction.plan_step` (only `Alusta` and `Muuda` of such a step write
     it; imported, legacy and hand-written steps never carry one);
   - `app.matters.plan_view.RecordedWork.proves(step)` — the same table
     docs/adr/0144 §2 decides `Järgmisena?` by, keyed on `template_step_key` /
     `operation`, never on words — **counting only records entered after the
     step was set** (`RecordedWork.of(..., since=created_at)`).
2. **`since` is the only change to the shared rule.** `RecordedWork.of` gains
   an optional `since`; without it every answer is what it was. With it a
   published write-up counts if filed after, or dated after the day of,
   `since`; a `Kaasamine` if filed after; feedback if closed after; an opinion
   if filed or sent after. Whether a request for an opinion is still
   outstanding is a fact about now and is not narrowed, so a repeat
   `Arvamuse tähtaeg` still keeps «Saada Koja arvamus» open.
3. **It says why and what can be done, with the controls already there.**
   «Praegune tegevus võib olla juba tehtud. Pärast selle tegevuse määramist on
   teemale lisatud kaasamine.» and, for a writer, «Kui see on tehtud, vajuta
   «✓ Tehtud». Kui töö jätkub, täpsusta tegevust nupuga «Muuda» — siis see
   teade kaob.» `Muuda` supersedes the step with a new row (docs/adr/0133 §4),
   set after the record, so the same records stop proving it; `✓ Tehtud`
   completes it. Nothing is completed, moved, created or dismissed by the
   alert, and no new dismissal state exists.
4. **One place.** Only the Teema page, directly under the current step, in the
   existing `.notice .notice--warning` style. No count, no work-list row, no
   Osakond or Minu asjad figure, no Andmekvaliteet queue.
5. **Never more than the reader may see.** The step is the reader's own
   current step, and every proof is read through its record's `visible_to`.
   A reader who may not see a restricted `Kaasamine` is not told by the alert
   that one exists; a reader who may not see a restricted step gets no alert
   about it. A reader without write access sees the reason, not the remedy.

## Gap matrix

| # | Candidate | What exists today | Reliable? | Predicate a new alert would use | False-positive risks | Where | Verdict |
|---|---|---|---|---|---|---|---|
| A | Stage changed while the current action is clearly obsolete | `stage_transition` / `change_stage` (`app/matters/services.py:964`, `:1001`) move the stage and touch no action except by closure; a terminal stage closes and asks about all live work (`closure_work.live_work_at_closure`, `app/matters/closure_work.py:257`, docs/adr/0152); `stage_flow.py` is ordering only and `stage_guidance.py` dims chips on `Uus teema` only — neither relates a stage to an action. | Terminal stages: yes. Non-terminal: nothing to read. | None deterministic: no encoded relation exists between a `Hetkeseis` key and an action's kind, step or text. «Action set before the last stage change» alone is not obsolescence (a ministry wait survives «Kooskõlastusringil» → «Valitsuses»). | Every WAIT/MONITOR across a stage move; every DO that legitimately spans stages; would need text reading to be sharper. | — | **REJECT** (terminal case ALREADY COVERED by 0152) |
| B | A sent opinion lacks expected subsequent work | Every interactive send schedules the 30-day check in the send's transaction (`follow_ups.schedule_first_check`, `app/workflow/follow_ups.py:187`, docs/adr/0146 §1); one live check per follow-up is a UNIQUE index; `check_domain_invariants` reports monitoring with no live check, a live check after monitoring ended, on an unsent opinion, or across Matters (`app/core/invariants.py:524`); a late check is red on Minu asjad and in «Vajab sekkumist» (0146 §7). | Yes, by construction plus the verifier. Historical sends get no check by decision (0146 §9). | Residual: `OpinionFollowUp.state = CANCELLED`, `end_reason = «Teema suleti»`, Matter open again, opinion still `SENT`. | 0146 §8 says reopening recreates nothing and defers «an explicit continuation» to a separate design; no control exists to resume a cancelled follow-up, so the alert would have no real corrective action. | — | **ALREADY COVERED**; the reopened-Matter residual is an **owner decision** (0146 §8), not built |
| C | Active Matter with no current action where one is necessary | `next_step.without_next_step` (`app/matters/next_step.py:142`) — reader-scoped, counts a current or planned action, an upcoming `Oluline tähtaeg` and an open feedback wait as a next step (0120, 0144 §3); drives «järgmine samm määramata» in «Vajab sekkumist» (`app/matters/overview.py:357`, via `work_items.matters_without_action`, `app/matters/work_items.py:1791`), Minu asjad / register `?tegevus=puudub`, the Andmekvaliteet queue «Aktiivne teema ilma järgmise tegevuseta» (`app/reporting/selectors/quality.py:321`) and the Teema page's «Järgmine samm on määramata» (`templates/matters/partials/current_action.html`). | Yes, on four surfaces with one definition. | Same as `without_next_step`. | Already tuned: archive rows, closed Matters, scheduled waits and milestones excluded. | — | **ALREADY COVERED** |
| D | A current step the record already shows done | `RecordedWork.proves` (`app/matters/plan_view.py:60`, `:117`) answers it, but `recommendation_for` asks only when nothing is current; 0141 §2 means no record completes a step; ADR 0126's tick covers only `Koja arvamus` / `Lõpeta kaasamine` and only when ticked. | **No** — a gap. | §1 above. | Valid repeat work (guarded by `since` and cleared by `Muuda`); a still-outstanding opinion request (`opinion_owed` not narrowed); WAIT/MONITOR (excluded); planned rows (not current, excluded); checks (excluded); imported/historical (no `plan_step`; archive rows excluded); closed Matters (excluded); restricted step or proof (reader-scoped). | Teema page, under `PRAEGUNE TEGEVUS` | **IMPLEMENT** |
| E | Live step assigned to someone no longer in the department | New work refuses a departed owner (`responsible_for_new_work`, `app/workflow/services.py:70`); Osakond's team table aggregates work owned outside the department into «Väljaspool osakonda» (`department_dashboard.team_rows`, `app/matters/department_dashboard.py:525`), head only. | Partly: by Matter owner, not by the step's own `responsible`. | `NextAction.status ∈ {OPEN, PLANNED}` and not `is_assignable_business_user(responsible)`. | Historical steps legitimately name former colleagues; the register keeps them on purpose (`assignable_including`). | — | **REJECT this round** (ownership, not workflow consistency; owner may want a reassignment queue) |
| F | Planned (non-check) action whose day passed while another step is current | Deliberately not late until current (docs/adr/0143 §A6, 0146 §7); only a planned check is overdue. | By decision. | `status = PLANNED`, `follow_up IS NULL`, `target_date < today`. | Contradicts an accepted ADR. | — | **REJECT** (conflicts 0143/0146) |
| G | Late step, ripe review, late feedback wait, late `Oluline tähtaeg` | `work_items.work_items` → `intervention_rows` (`app/matters/overview.py:295`) — «N p üle», «üle vaadata»; Minu asjad bands. | Yes. | — | — | — | **ALREADY COVERED** |
| H | Live work on a closed Matter; a closure with no event; stage periods out of step | `check_domain_invariants` (`app/core/invariants.py` `closed-matter-*`, `stage-episode-*`, `terminal-stage-on-open-matter`). | Yes (operator report; services prevent new cases). | — | — | — | **ALREADY COVERED** |

## Consequences

- A lawyer who did the standard step through `LISA TEEMALE` sees, under the
  step, that the record already shows it — and the reason — instead of a
  current step silently lagging the file.
- The suggestion rule and the alert cannot disagree: they are one function.
- Steps written by hand are never judged; the alert's reach is exactly the
  steps started from the standard sequence.

## Not built

- No alert for A, B's reopened residual, E or F (matrix above).
- No count, no list, no dismissal, no e-mail; nothing written.
- No change to what completes a step: a record still fulfils nothing
  (0141 §2 unchanged).

## Reversibility

By code. Remove the view's `consistency_alerts` entry and the template loop;
`RecordedWork.of`'s `since` defaults to the old behaviour.

## Amendment of 2026-10-14 — structured steps open their canonical form

Owner decision D4 (docs/adr/0168). `Alusta` on «Kaasa liikmeid / küsi tagasisidet», «Saada Koja arvamus» or «Koosta kodulehe ülevaade» opens that step's `LISA TEEMALE` form and writes nothing; the form's save finishes the step that opened it (`samm`, checked under the lock). `STANDARD_PLAN` is version 2 without «Koonda tagasiside ja kujunda Koja seisukoht» (`form-position`), which is never suggested on a version-1 plan either; existing rows are not rewritten.

## Amendment of 2026-10-18 — the overview step reads the kind the page shows

Before this amendment, `RecordedWork.published_overview` and the `Ülevaade /
uudis` save's own step check (`views._overview_does_the_step`) read the
*stored* `kind`. They counted a row with an empty kind as an overview. The page
reads `effective_kind`: the stored kind, or, for a row from before the column
existed, the kind its koda.ee address states. So a legacy row on a
`koda.ee/…/uudised/…` address was shown as «Uudis» and was still counted as the
published overview. The alert said a «Koosta kodulehe ülevaade» step was done
because of a news item (JARCH-002, 2026-10-17 audit).

Both callers now ask one question,
`app.matters.publication_kind.overview_fulfils_step`. It is true for a
published row whose `effective_kind` is `OVERVIEW` or still unknown. A legacy
row whose address does not say which kind it is still counts as an overview,
because that is what every one of them was filed as. The only change is that
a legacy row on a koda.ee *news* address no longer proves the step. Stored
kinds are unaffected. Pinned by `tests/test_one_rule_one_place.py`.
