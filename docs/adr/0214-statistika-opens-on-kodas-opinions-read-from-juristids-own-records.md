# 0214 — Statistika opens on Koda's opinions, read from Juristid's own records

Status: Accepted (owner, 2026-11-16)

## Context

After the Lovable register replaced the pilot data (r43/r44) and the nightly
synchronisation went to APPLY (14.11.2026), Statistika's first page counted
almost nothing a lawyer recognises. For 2026 it showed «Uued teemad kuude
kaupa: 0», every Teema «Klassifitseerimata» by Valdkond and 248 of 264 with
«Menetlusliik määramata». Its metrics were written for Teemad created inside
Juristid and for `Submission` rows with a file; production holds 2 369 Teemad
from the register and no `Submission` at all.

The department's working statistics are the ones on the Lovable register's
«Statistika» page: opinions sent in a period and its comparison, month by
month and by lawyer, and the long view from 2011 (sent per year, days given to
answer, share sent by the deadline, share of EU topics, Koda's own
initiatives) with records.

The owner's conditions:

* the same statistics, built on Juristid's own structure of data;
* **nothing may depend on Lovable** — the register is being phased out;
* Valdkond and «who asked» are not in the register's data and wait.

## Decision

1. **`/statistika/` is a new page about Koda's opinions**
   (`reporting:dashboard`, `templates/reporting/dashboard.html`). The page it
   replaced there — counts of Teemad, the rail, «Inimesed» — is unchanged at
   `/statistika/seis/` under its old route name `reporting:overview` and the
   tab «Seis».

2. **One reader of sent opinions** — `app.reporting.opinions.opinion_facts`.
   It is the only place that knows where a sent opinion is recorded, and it
   reads three records, all of them tables of this application:
   a sent `Submission`; an `Arvamuse tähtaeg` a person ended as answered
   without a document; and the imported statement «välja saadetud»
   (`LovableOpinionOutcome`, current row, SENT). The synchronisation's own
   endings are left out so one act is one fact. Nothing queries Lovable; when
   it is switched off the imported rows remain as history and new opinions
   arrive as `Submission`s through the same reader.

   The owner chose this over converting the 2 330 imported statements into a
   new native record now: no data conversion and no change to the live
   synchronisation. The conversion stays possible later behind the same
   function.

3. **This amends two earlier rules, for this page only.**
   * `ResponseDeadlineOutcome.ANSWERED` and the imported outcome «count in no
     statistic» (ADR 0213 §10): they are now counted by `opinion_facts`. The
     catalogued metric `SUBMISSIONS_SENT` is unchanged and still counts
     `Submission`s only.
   * «No chart, no target, no comparison with a colleague» (02-EKRAANID §E,
     master specification 18.8): the page draws column charts and lists sent
     opinions by lawyer, because that is the statistic the department reads
     today. It ranks nobody and sets no target: a lawyer's row is a count, in
     the order every breakdown uses.

4. **Definitions** (`app.reporting.opinion_stats`), the register's restated on
   Juristid's fields: an opinion counts on the day it went out; periods are
   rolling 3/6/12 months (default 12), a calendar year or two dates, compared
   with the span before (3, 6, custom) or the same dates a year earlier (12,
   year); the long-term trends group by the Teema's reference year — days
   given = `Arvamuse tähtaeg` − `Matter.received_date`, EU = the Teema's
   legal instrument is one of the four EU types, own initiative =
   `Track.KODA_INITIATIVE`; the lawyer is the Teema's `owner`, else its
   `former_owner_name`.

5. **Charts are server-rendered markup**, like every chart before them
   (`app/reporting/charts.py`): a column is an element with a height and each
   month's column links to the opinions it counted
   (`reporting:opinion_rows`). No chart library and no script.

## Alternatives considered

* **Convert the imported statements to a native record now** — cleaner end
  state, but a production data conversion plus a change to the synchronisation
  two days after APPLY began. Deferred, not rejected.
* **Read Lovable's own statistics** — refused by the owner's condition.
* **Extend the metric catalogue** — its `ReportingContext` (period by
  reference year, record mode, provenance) does not express rolling months or
  a comparison period; forcing these figures into it would have changed every
  catalogued metric's filter contract.

## Consequences

* Figures match the register's for the Teemad Juristid holds; 154 historical
  register topics that were never imported are not counted.
* `opinion_facts` reads every visible Teema's facts on each request (a handful
  of queries, ~2 400 rows). Acceptable now; a cache or a snapshot is the next
  step if the page slows.
* Valdkond, «who asked» and semantic analysis are not on the page: the data
  does not exist yet.

## Reversibility

The old first page is intact at `/statistika/seis/`. Pointing the navigation
back at `reporting:overview` restores the previous behaviour; nothing was
migrated and no data was written.
