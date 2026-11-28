# 0235 — The week overview counts recorded outcomes; a postponement is not a completion

Date: 2026-11-28 · Status: Implemented in the r97 candidate (**no migrations**, read side only): Minu asjad,
the department head's Osakond, and a URL-only records page. Nothing here changes a Lovable-owned field or a
permission.

## Context

The owner asked that Minu asjad and Osakond become the daily operational overview, learning from the Lovable
Command Center's compact KPI cards, weekly summaries and drill-downs. Lovable counts a next-action deadline
moved as completed work. Juristid records far more (several opinions per Teema, a ledger of how each action
ended, coverage of historical recording) and must not flatten it.

## Decision

1. **One resolver per figure and its list.** `week_overview.records(ctx, kind, span)` produces the records a
   figure counts and the records page lists, so they are one population, compared in tests by record
   identity. A figure with no exact list is not a link; a broad link is labelled as one («Kõik tähtajad»).
2. **Three scopes, enforced below the template.** `SELF` (own desk), `DEPARTMENT` (the head only) and
   `COLLEAGUE` (a head reading a colleague's desk: the seven-day work list and nothing historical). The
   records route has no person parameter; `?ulatus=osakond` answers 403 unless
   `may_view_department_management`; every row is read through `visible_to`. Department figures are
   distinct records of everything the head may read — never a sum of personal figures — and carry no ranking.
3. **Actions come from the `NextAction` rows by what ended them** (`classify_ended`), attributed to
   `ended_by`: COMPLETED → *Tehtud*, CANCELLED → *Tühistatud*; a SUPERSEDED row replaced by the same step
   (same text, kind, date meaning, precision and person) is a date change — later *Edasi lükatud*, any other
   *Tähtaega muudetud*; any other replacement or a re-save that changed nothing is *Asendatud*. No explicit
   «date moved» event exists for ordinary steps, so the row pair is the recorded evidence. *Üle vaadatud*
   counts every person-made `NEXT_ACTION_REVIEWED` event (a step reviewed twice is two reviews, each kept
   with its identity). A row ended by the system or the synchronisation is nobody's work. *Tegemata* is open
   DO deadlines late or due by Sunday; a WAIT/MONITOR/SOURCE review date is never one (`REVIEW_KINDS`).
4. **Sent opinions keep their own identity** (`OpinionFact.record_id`): `submission:<id>`, `answered:<id>`
   or `outcome:<id>`. Two native opinions on one Teema on one day are two facts. The native/imported
   de-duplication of `opinion_facts` is unchanged. On a person's desk they are credited as Minu statistika
   credits them (`credit_opinion`: recorded sender, else the owner on the sent day, else unknown).
5. **Open opinion requests are the canonical obligation** (`work_items.response_obligations`): one record
   per open `Arvamuse tähtaeg`, identified by Teema and deadline. A draft with no recorded request is
   Teema-level workload (the strip's «koostamisel»), never a request. No completion percentage: sent
   opinions and open requests have no common denominator.
6. **Arrivals are events.** *Uued teemad* on a desk are `arrivals_series` records — a Teema and a day, so a
   Teema that returns on a later day is a second arrival, and a closed Teema stays one. The recent-Teema
   preview and its list read the same selector (`created_since` for creations, the complete ownership trail
   for hand-overs) over the same window, whatever the loaded series cover. In the department, *Uued teemad*
   are Teemad received in the window.
7. **Commencements are dates, not outcomes.** An exact recorded date that has passed is *Kuupäev saabunud*
   (never «jõustunud», `my_dates.COMMENCEMENT_PASSED`); an exact date ahead is *Eesolevad jõustumised*;
   approximate ones are *Ligikaudsed jõustumised* whether past or future. General-order and unknown records
   have no date, so they are in no week bucket and stay distinguishable in the full list.
8. **Coverage everywhere.** History figures carry `cell()` coverage (new `actions` key in
   `RECORDED_BY`/`RELIABLE_BY`; department sent/new/closed use `sent`/`trail`/`closed`): a week before
   recorded history is «—» or «≥ N», a covered empty week is a genuine 0. Present-state figures (open
   requests, outstanding work, commencements) are exact readings of today's records.
9. **Monitoring is not work.** Personal: Minu statistika's `seire` series for the person's Teemad.
   Department (head only): the same five series over every Teema the head may read
   (`incoming_series(everyone=True)`) — distinct findings, items and calls, not a sum of personal figures;
   every count opens its findings (`seire-<key>`). No new relevance engine, no polling. Head-only draft
   discovery (Monitooring → Eelnõud) is not summarised here.
10. **A colleague's desk gets no personal statistics**, and no cross-person visibility is widened.
11. **Osakond is consolidated, not stacked.** The head sees one compact band under the strip (week outcomes,
    previous week, incoming monitoring, nearest commencement dates). The seven-day deadlines are a count line
    in *Eesolev*'s heading (real deadlines, ripe reviews and late work counted separately; the longer-term
    windows and «Kõik tähtajad» are untouched), the latest Teemad sit in the rail's *Uued teemad*, and the
    sent opinions stay in *Tehtud*. A non-head sees the page as before.
12. **Presets are URLs, never state.** `/minu-asjad/nadal/?naitaja=…` (and `ulatus=osakond`) is not a saved
    view and writes nothing; «Eemalda eelseadistus» is a link to the page it came from. Minu statistika's
    chart columns are links (`?valik=<bucket start>`, validated against the period's own buckets): a
    measured column — a genuine 0 included — opens its days, an unmeasured or future one is not a link.
13. **The Seis strip's first four figures** are Tähtaja ületanud, Vajavad tähelepanu, Arvamused koostamisel,
    Menetluses teemad; the strip's rule that a zero is not drawn is unchanged.
14. **Cost is bounded.** One `load_series` read serves both weeks, one sent-opinion read is shared with
    Osakond, reads are memoised per request and per reader. Measured on 1,501 Teemad / 1,493 actions, r96
    baseline → r97: Minu asjad 55 → 86 queries (989 → 1,441 ms median), head's Osakond 51 → 70 (613 → 792
    ms), non-head Osakond 40 → 40 (553 → 558 ms); constant at 36 and 72 Teemad in the scale tests.

## Limitations (accepted, documented)

* *Otsustatud mitte saata* for the week: no reliable decision date exists; none is inferred.
* The opinion's formal/informal kind is not reliably recorded, so it is not shown (never defaulted to *Ametlik*).
* A derived drafting step (`Koostan arvamuse`, identified only by its text) and its opinion request have no
  explicit relation; both can appear in the seven-day list as different record types. None is inferred.
* Department monitoring has no «new draft» (Eelnõud) count: that is head-only discovery with its own review.
* Chart zoom between day, week and month grain is not provided.
