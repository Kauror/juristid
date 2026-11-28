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
   attributed to `ended_by`. COMPLETED → *Tehtud*; CANCELLED → *Tühistatud*; SUPERSEDED by a row with the
   same text and kind and a *later* day → *Edasi lükatud*; any other SUPERSEDED → *Asendatud*. A
   `NEXT_ACTION_REVIEWED` audit event by a person (no provenance/operation marker) → *Üle vaadatud*,
   one per action. A row ended by the system or the synchronisation has no `ended_by` and is nobody's work.
   A moved day, a new action, a follow-up scheduled automatically and a Lovable change are never *tehtud*.
   *Tegemata* is open DO deadlines that are late or fall due by Sunday; a WAIT/MONITOR review date is never
   one, however long past.
3. **The week is Monday–Sunday in Europe/Tallinn**, selected by `local_day_range` on the timestamp.
4. **Every figure links to its rows.** `/minu-asjad/nadal/?naitaja=…&nadal=…` (and `saadetud-arvamused`,
   `uued-teemad` with `alates`) lists exactly the counted records for the signed-in reader, through
   `visible_to`. The preset is the query string only — refresh, Back and a shared link work; nothing is
   saved, no saved view, filter or default column is read or written; «Eemalda eelseadistus» is a link
   to the ordinary Minu asjad. The route takes no person.
5. **Monitoring is not work.** Incoming-monitoring figures sit in their own block, built from Minu
   statistika's `seire` cards, and are never added to any work figure.
6. **A colleague's desk gets no personal statistics.** The statistics-backed blocks (sent, previous
   week, new Teemad, monitoring) exist only on one's own desk; a head reading a colleague's desk sees
   only what the work model already showed. No cross-person statistical visibility is widened.
7. **The Seis strip's first four figures** are Tähtaja ületanud (rows of the band it opens), Vajavad
   tähelepanu, Arvamused koostamisel, Menetluses teemad (all Teemad); the three older figures follow
   unchanged. The strip's existing rule that a zero is not drawn is unchanged.

## Not decided here (open for the owner / a later round)

* Osakond's operational overview and shared components for the department.
* *Otsustatud mitte saata* for the week: the declined fact carries the request's deadline or arrival
  day, not the day of the decision, so a weekly count would be a guess.
* The opinion's formal/informal kind: not reliably recorded, so it is not shown (never defaulted to *Ametlik*).
* Chart day/week/month selection in Minu statistika.
