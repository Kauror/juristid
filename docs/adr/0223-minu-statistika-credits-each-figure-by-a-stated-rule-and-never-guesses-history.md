# 0223 — Minu statistika credits each figure by a stated rule and never guesses history

Date: 2026-11-25 · Status: Proposed — overnight development 25.11.2026, subject to owner review.
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

4. **The completeness boundary is read from the records, not assumed.** A value is
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

## Consequences

* On production most past months read «andmed puuduvad» today: almost every Teema arrived in
  Juristid in November 2026 with years of earlier history. The figures fill in as Juristid's
  own trail grows; nothing is back-filled by inference.
* Names, not ids, are what the trail holds. A renamed person's older events stop resolving
  and their stretch reads unknown rather than wrong. Storing the owner ids on
  `MATTER_ASSIGNED` (an additive payload change, no migration) would make the rebuild exact
  going forward; not done here.
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

Read-only: no migration, no stored value, no change to an existing figure. Removing the route,
the template, the link and `my_statistics.py` restores the previous behaviour; `actor_id` on
`OpinionFact` is unused elsewhere.
