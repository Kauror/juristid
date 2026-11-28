# 0235 — The week overview counts recorded outcomes; a postponement is not a completion

Date: 2026-11-28 · Status: Implemented in the r97 candidate (**no migrations**, read side only; Minu asjad
only — Osakond's variant is not built, see «Not decided here»).

## Context

The owner asked that Minu asjad become the daily overview, learning from the Lovable Command Center's
compact KPI cards, weekly summaries and drill-downs. Lovable counts a next-action deadline moved as
completed work. Juristid records far more (several opinions per Teema, a ledger of how each action
ended, coverage of historical recording) and must not flatten it.

## Decision

1. **One definition per figure, taken from where it already lives.** Sent opinions, new and closed
   Teemad and incoming monitoring for the week and the previous week are Minu statistika's own cards
   (`my_statistics`), so the overview and the page it links to cannot disagree and coverage («≥ N», «—»,
   a genuine 0) survives. Open opinion work is `drafting_matters` for the person's Teemad, counted as
   Teemad. Sent opinions and open requests are shown as separate counts; there is **no completion
   percentage**, because they have no honest common denominator.
2. **Actions are counted from the `NextAction` rows by what ended them** (`week_overview.classify_ended`),
   attributed to `ended_by`. COMPLETED → *Tehtud*; CANCELLED → *Tühistatud*. A SUPERSEDED row replaced by the
   *same step* (same text, kind, date meaning, date precision and person) is a date change: a later day is
   *Edasi lükatud*, any other change of day *Tähtaega muudetud*. Any other replacement, and a re-save that
   changed nothing, is *Asendatud*. No explicit «date moved» event exists for ordinary steps (only follow-up
   checks have `NEXT_ACTION_RESCHEDULED`), so the row pair is the recorded evidence. *Üle vaadatud* counts
   every person-made `NEXT_ACTION_REVIEWED` event — reviews are events, a step reviewed twice is two
   reviews, each kept with its own identity in the detail list. A row ended by the system or the
   synchronisation has no `ended_by` and is nobody's work. *Tegemata* is open DO deadlines that are late or
   fall due by Sunday; a WAIT/MONITOR review date is never one.
3. **The week is Monday–Sunday in Europe/Tallinn**, selected by `local_day_range` on the timestamp.
4. **Every figure links to its rows.** `/minu-asjad/nadal/?naitaja=…&nadal=…` (and `saadetud-arvamused`,
   `uued-teemad` with `alates`) lists exactly the counted records for the signed-in reader, through
   `visible_to`. The preset is the query string only — refresh, Back and a shared link work; nothing is
   saved, no saved view, filter or default column is read or written; «Eemalda eelseadistus» is a link
   to the ordinary Minu asjad. The route takes no person.
5. **Monitoring is not work.** Incoming-monitoring figures sit in their own block, built from Minu
   statistika's `seire` cards, and are never added to any work figure.
6. **Three scopes, enforced below the template.** `SELF` (own desk), `DEPARTMENT` (the head only) and
   `COLLEAGUE` (a head reading a colleague's desk: the seven-day work list and nothing historical). The
   records route has no person parameter; `?ulatus=osakond` answers 403 unless
   `may_view_department_management`; every row is read through `visible_to`. Department figures are
   distinct records of everything the head may read — never a sum of personal figures — and carry no
   ranking.
7. **History is attributed like Minu statistika's.** Sent opinions use `credit_opinion` (recorded sender,
   else the owner on the sent day, else unknown) over one `load_series` read that also serves the previous
   week; actions use `ended_by`. History figures carry coverage (`cell`): a week before recorded history
   is «—» or «≥ N», never a 0; a covered empty week is a genuine 0. A new key `actions` (first
   person-made `NEXT_ACTION_COMPLETED`) was added to `RECORDED_BY`/`RELIABLE_BY`.
8. **Requests are counted as requests.** A Teema holds at most one open `Arvamuse tähtaeg`; earlier ones
   are ended `MatterResponseDeadline` rows. «Täitmata nõuded» counts open requests (identity: Teema +
   deadline); a draft with no recorded request is Teema-level workload (the strip's «koostamisel»).
9. **One resolver.** Every figure and the records page call `week_overview.records`, so the number and
   its list are one population; tests compare record identities.
10. **The Seis strip's first four figures** are Tähtaja ületanud (rows of the band it opens), Vajavad
   tähelepanu, Arvamused koostamisel, Menetluses teemad (all Teemad); the three older figures follow
   unchanged. The strip's existing rule that a zero is not drawn is unchanged.

## Not decided here (open for the owner / a later round)

* *Otsustatud mitte saata* for the week: no reliable decision date exists; none is inferred.
* The opinion's formal/informal kind: not reliably recorded, so it is not shown (never defaulted to *Ametlik*).
* A derived drafting step and its opinion request can both appear in the seven-day list: no explicit
  relation exists, and none is inferred from text.
* Department-level incoming monitoring counts are not computed (Minu statistika's are per person); the
  department block links to Monitooring.
* Chart day/week/month selection in Minu statistika.
