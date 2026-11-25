# 0223 — Minu statistika credits each figure by a stated rule and never guesses history

Date: 2026-11-25 · Status: Proposed — overnight development 25.11.2026, subject to owner review;
amended in the corrections round 25.11.2026 (§6–§9, also Proposed).
Owner decision BLOCK-4 (UX programme preparation report, 24.11.2026): historical workload is
credited to the owner *at the time*; the page is for the signed-in person only.

## Context

Minu asjad has a «Minu statistika» foldout (`my_work.person_stats`, `opinion_work`): this
month's and this year's sent opinions, entries and closed Teemad, plus opinions in
preparation. Every one of those is credited to the Teema's **current** owner
(`Matter.owner`); the sent-opinion count reads the one reader of sent opinions
(`reporting.opinions.opinion_facts`, ADR 0214 §6) narrowed by `owner_id`.

FUNC-003 asks for a coherent personal statistics view with historical figures. Juristid has
no assignment-history table. The only record of who owned a Teema before is the
`MATTER_ASSIGNED` change event, written by `services.assign_matter` (a colleague's hand-over
and the Lovable synchronisation's, the latter with provenance). Its payload carries the two
owners' **display names** (`from_name`, `to_name`), not their ids. A Teema created in
Juristid records `MATTER_CREATED` without an owner and does not raise `MATTER_ASSIGNED` for
its first owner. Imported Teemad (the register since 2011, the Lovable synchronisation since
November 2026) entered Juristid long after most of their history; Lovable's own changes of
the responsible lawyer before then were never applied in Juristid.

## Decision

1. **A page of one's own figures**: `matters:my_statistics`, `/minu-asjad/statistika/`, GET,
   reached by «Kõik minu statistika →» at the foot of the foldout on one's own desk only. The
   route takes no person: it always reads `request.user`, as that person, so it adds no
   cross-user visibility. A department head opening it reads their own figures; a
   colleague's desk (`person_work`) does not offer the link. No figure of another person is
   read or shown; nothing is ranked, rated, coloured or compared. The foldout and every
   existing statistic keep their current-owner attribution unchanged.

2. **Every figure names its credit rule** (`app/matters/my_statistics.py`):

   | Figure | Credit rule | Definition |
   | --- | --- | --- |
   | Avatud teemad | current owner | visible Teemad with `owner = me`, `is_open` (the register list `?vastutaja=me`) |
   | Arvamus koostamisel | current owner | `opinion_work.in_preparation` (Osakond's ARVAMUS KOOSTAMISEL, unchanged) |
   | Arvamuse tähtaeg sel nädalal | current owner | `opinion_work.due_this_week` (unchanged) |
   | Välja saadetud arvamused (per month) | recorded actor; imported: owner at the time | every fact of `opinion_facts`: a sent `Submission` → its `sent_by`; a request ended as answered → `ended_by`; an imported «välja saadetud» (names nobody) or a fact without an actor → the Teema's owner on `sent_on` |
   | Vastutusel teemasid kuu lõpus | owner at the time | visible real Teemad that could have had an owner on the month's last day whose rebuilt owner that day is me |
   | Sissekanded | recorded actor | visible `Entry` rows with `author = me`, by `occurred_at` month |
   | Lõpetatud teemad | recorded actor | `MATTER_CLOSED` events with `actor = me` (a person closing live work; the cut-over and synchronisation closings are other event types) |
   | Avaldatud ülevaated ja uudised | recorded actor | `WEBSITE_OVERVIEW_PUBLISHED` events with `actor = me` |
   | Lisatud kaasamised | recorded actor | `ENGAGEMENT_ADDED` events with `actor = me` |

   `OpinionFact` gains `actor_id` (who the record names as sending it); no existing count
   reads it, and the merging of imported and native records is unchanged.

3. **Ownership at the time is rebuilt only from Juristid's own trail.** For each visible
   real Teema: from the day it was created in Juristid, the owner is the first
   `MATTER_ASSIGNED`'s `from_name` (or the current owner if it was never handed over), then
   each event's `to_name` from its day. A name resolves to a person only when exactly one
   user carries it. The current owner never inherits the time before a hand-over.

4. **The completeness boundary is read from the records, not assumed.** *(Superseded in part
   by §7: «what you did» is no longer bounded by the earliest actor event, and a month that
   is partly known shows what is known with its state instead of «andmed puuduvad».)* A value is
   «andmed puuduvad» — never 0 — when it cannot be credited:
   * a Teema that could have had an owner in the month (its span runs from its arrival,
     else its reference year, to its closing, else — if it closed before reaching Juristid —
     its creation here, else today) but did not yet exist in Juristid that day;
   * an assignment trail whose names do not chain (an event's `from_name` is not the
     previous `to_name`), name nobody who can be told apart, or end on someone other than the
     current owner (a change nobody recorded, at an unknown time);
   * a sent opinion in the month whose credit is unknown by the rules above;
   * «what you did» for months before Juristid first recorded a person's action (the earliest
     change event with an actor).
   The earliest assignment event is **not** taken to prove completeness. The page states the
   first day actions are recorded and the oldest month from which «at the time» figures are
   complete.

5. **Bounds.** Twelve calendar months, newest first, the current one to today. About a dozen
   queries whatever the portfolio (tested), all through `visible_to`.

## Corrections (25.11.2026, coordinator review)

6. **Assignment events carry stable owner ids.** `services.assign_matter` — the one writer of
   `MATTER_ASSIGNED`, used by a colleague's hand-over (Teema page, workspace, inline edit), the
   MCP write operation, the Lovable synchronisation and pilot, and the owner backfill — now
   adds `previous_owner_id` and `new_owner_id` (the users' ids as text; `null` for nobody, so
   an unassignment stores `new_owner_id: null`) next to the unchanged `from_name`/`to_name`.
   Additive: no migration, no reader of the names changes, and **no existing audit row is
   rewritten** — older events keep exactly the names they had. The rebuild prefers the ids
   when the event carries them (a renamed or inactive person stays themselves). For an older
   name-only event a name is a person only when exactly one account carries it **today** and
   that account **already existed when the event was written**; a name a renamed person has
   left, or one a later account took, resolves to nobody known and that stretch is unknown.
   Historic identities are never inferred. Residual limit: if two older accounts swapped names
   after a name-only event, the name rule cannot tell; Juristid keeps no rename history.

7. **Coverage is an explicit state per value** (`my_statistics.Coverage`, `Cell`):
   | State | Meaning | Shown as |
   | --- | --- | --- |
   | `VERIFIED` | the whole month was recorded and every record in it is credited | the count; **0 is a real zero** |
   | `PARTIAL` | recording covers part of the month — it began inside the month, or the month is still running — and every record is credited | the count + «osaline: alates dd.mm.yyyy» / «osaline: kuni dd.mm.yyyy» / both ends; never a whole-month total |
   | `AT_LEAST` | records were found, but the month is not fully recorded or a record in it cannot be credited | «vähemalt N» + «ajalugu poolik» (genuine records are not hidden) |
   | `UNKNOWN` | nothing reliable, and no record found | «andmed puuduvad» |
   **When recording began is evidence, not the earliest event seen.** For each figure the
   page reads the time the migration that created its record was applied to this database
   (`django_migrations.applied`; `RECORDED_BY`): sent opinions — `submissions.0001_initial`
   and `matters.0045_response_deadline_history` (the later of the two); entries —
   `matters.0003_entry`; closed Teemad and the ownership/open-state trail —
   `audit.0001_initial`; overviews — `audit.0018_website_overview_events`; consultations —
   `audit.0012_matter_engagement` (the first migrations naming those event types; a test
   holds the mapping to the migration files). Before that moment Juristid could not write
   the record at all, so an empty month there is `UNKNOWN`, not 0, and records found there
   (imported, back-dated) make it `AT_LEAST`. The same rule applies to sent opinions: an
   imported «välja saadetud» before recording began is a genuine record but not proof of a
   complete month (154 historical register topics were never imported, ADR 0214). The page
   lists each figure's «salvestatud alates» day. A restore keeps `django_migrations`, so the
   dates survive a restore; a database rebuilt by `migrate` starts recording again, and so
   do its records.

8. **Historical workload is reconstructed responsibility, labelled so.** The column is now
   «Vastutusel avatud teemasid kuu lõpus» with the rule «vastutaja tol ajal; taastatud», and
   the page says it is reconstructed responsibility, not confirmed active workload. A Teema
   counts at the **end of the month's last day** (the current month: today) when the trail
   shows both that the person owned it and that it was open then:
   * open/closed state is rebuilt from the Teema's own events — `MATTER_CLOSED` closes it on
     its day; `MATTER_REOPENED`, `MATTER_PROMOTED`, `MATTER_REGISTER_CUTOVER_ACTIVATED` open
     it; the state before the first such event is its opposite. A Teema **closed on the last
     day of a month** is not open at that month's end; a **reopened** one counts from the
     reopening day, and not in between;
   * a cut-over retirement (`MATTER_HISTORICAL_CUTOVER_CLOSED`,
     `MATTER_REGISTER_CUTOVER_RETIRED`) records when the normalisation ran, not when work
     stopped: the stretch before it is unknown;
   * an **imported** Teema is unknown (owner and state) for the time before it was created in
     Juristid; one that arrived closed is closed from its recorded `closed_at` when that is
     earlier, else from its arrival here;
   * several **ownership changes in one day**: the last one decides that day's end;
   * a Teema that might count but whose owner or state is unknown makes the month `AT_LEAST`
     (or `UNKNOWN` if nothing is certain);
   * a month ending before the trail was recorded (`audit.0001_initial` applied) is never
     `VERIFIED`, even when no known Teema existed then: the Teemad Juristid holds are not all
     there were (historical register topics were never imported).

9. **Two rules, said where both are seen.** Statistika and the Minu asjad summary keep the
   current-owner attribution unchanged. The foldout now says under its link «Siinsed arvud:
   teema praegune vastutaja. «Kõik minu statistika» loeb sinu tehtut ja vastutust tol ajal.»,
   and the page opens with a short paragraph saying the same, so two different numbers do not
   read as a contradiction. No ranking, no comparison; the page stays `request.user`'s own —
   no URL parameter (`?kasutaja=`, `?vastutaja=`, `?jurist=`, …) selects anyone else (tested).

10. **Amendment (integration review 25.11.2026): a migration date is the earliest possible
    boundary, not proof of complete history.** §7 treated the day a figure's migration was
    applied as the start of complete coverage. It only says when recording became technically
    possible. Each figure now has two boundaries (`my_statistics.recording_since`,
    `reliable_since`): **earliest possible** — the migration day, as in §7 — and **reliable
    coverage** — the day somebody first recorded that kind of activity in Juristid as it
    happened (`RELIABLE_BY`: the first `SUBMISSION_SENT`, `ENTRY_ADDED`, `MATTER_CLOSED`,
    `WEBSITE_OVERVIEW_PUBLISHED`, `ENGAGEMENT_ADDED` written by a person; for the ownership trail
    the first `MATTER_ASSIGNED` by a person or by the Lovable synchronisation), on a Teema the
    reader may see, never earlier than the migration day. Events with no actor (imports) or
    naming a batch operation or provenance (the owner back-fill) do not count. Where no such
    event exists the boundary is «not established» and no month of that figure is complete.
    Only months inside reliable coverage are `VERIFIED` or `PARTIAL`; before it a positive count
    is «vähemalt N» and none is «andmed puuduvad», never 0. The page lists both days per
    figure and says the figures count activities recorded in Juristid, not all work actually
    done; a `VERIFIED` 0 means no activity was recorded, not that none was done. Access (the
    signed-in person only), stable owner ids and existing audit rows are unchanged.

## Consequences

* On production most past months read «andmed puuduvad» today: almost every Teema arrived in
  Juristid in November 2026 with years of earlier history. The figures fill in as Juristid's
  own trail grows; nothing is back-filled by inference.
* Names, not ids, are what the trail held until §6. A renamed person's older events stop
  resolving and their stretch reads unknown rather than wrong; events written since §6 carry
  the ids and resolve exactly. `lovable_sync._imported_owner_id` still reads only
  `from_name` (unchanged here; a follow-up may prefer the id).
* Ownership changes made in Lovable and never applied in Juristid are invisible to this
  rebuild; Juristid's own record is the one credited.

## Alternatives considered

* **Credit everything to the current owner, labelled** (BLOCK-4 option 2) — simpler, but
  credits a new owner with a predecessor's work; rejected by the owner's decision.
* **A department head may open a colleague's Minu statistika** — new cross-user visibility;
  not decided, not built.
* **An assignment-history table with a back-fill** — would need a migration and would
  manufacture certainty for the imported years the trail does not cover.

## Reversibility

Read-only apart from §6: no migration, no stored value, no change to an existing figure. §6
adds two keys to new `MATTER_ASSIGNED` payloads; removing those two lines stops writing them,
and readers fall back to the names. Removing the route,
the template, the link and `my_statistics.py` restores the previous behaviour; `actor_id` on
`OpinionFact` is unused elsewhere.
