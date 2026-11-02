# ADR 0200 — Teemad: one selection pipeline, multi-value filters, chosen columns and three Excel exports

- Status: Proposed (owner's brief of 2026-11-02 and decision A of the same day; **no migrations**, no stored state)
- Related, unchanged: 0021 (a `VÄLJA` date is the register's, not a send), 0026 (last activity is not `updated_at`), 0071 (one filter state, in the address), 0079 (dates at the precision they are known to), 0122 §2 (a bounded window never holds a period), 0158 (Jõustumised as a presentation of the register), 0181 (one internal read policy)

## Context

The owner asked for richer filtering in Teemad, a choice of columns, and three
Excel downloads: the table as shown, every supported field, and the old register
layout. Three traps in the code as it stood made each of those wrong by
construction: the register read every parameter with `QueryDict.get` (the last
value only) and serialized with `.items()`; free text, the status default,
filters and sort were decided inside the view, so an export would have been a
second copy of that sequence; and the shared table partial is also Saabunud's.

## Decision

1. **One selection pipeline** (`app/matters/register_selection.py`). Search →
   status default → structured filters → deterministic order (`-created_at`,
   then `-pk`) is read once into a `RegisterSelection`. The table pages it; the
   table export writes all of it. An unknown `?jarjestus=` is the default sort —
   nothing from the address reaches `order_by`.
2. **Multi-value dimensions** (`register_filters.MULTI_VALUE_PARAMS`: vastutaja,
   hetkeseis, menetlusliik, valdkond, silt, liik, arvamus, oigusakt). Repeated
   parameters (`?hetkeseis=a&hetkeseis=b`) are alternatives (OR); different
   dimensions narrow each other (AND); `puudub` is one of the values, never
   «all». A single-value link written before means what it meant. An unreadable
   value empties the list rather than being skipped. Many-to-many dimensions are
   a subquery, so a Teema in two chosen values is one row. `as_text`, chips (one
   per value, its `×` removes that value), the search box's carried inputs,
   named-view comparison and `register_url` keep every value. Täpsem otsing
   draws these as checkbox groups; nothing applies until «Filtreeri».
3. **New windows**, each over a child record read as the reader may see it:
   `saadetud_*` (an actually sent Submission, including one later withdrawn —
   never a draft and never a `VÄLJA` mark), `samm_*` (the open step's date; by
   0122 §2 only a day-precise date can fall in a bounded window), `viimane_*`
   (last substantive activity, 0026) and `muudetud_*` (the record's own
   modification stamp — deliberately separate). An inverted range is refused
   with a sentence, not answered with an empty guess.

   **`viimane_*` reads the last activity at its precision** (amended after
   review, `activity.last_activity_within`). It asks about the very fact
   «Viimane tegevus» names — `activity_of`'s latest date, the basis the
   precedence picks on a tie, and that basis's precision — in the database,
   one `CASE` per row over the correlated maxima the register already reads:
   - a **day** (every basis but the three below, and an `EXACT` / `INFERRED`
     one of them) matches when it lies inside the window;
   - a **period** — a `Kaasamine`, `Märge` or `Väline seisukoht` known to a
     month, quarter, half-year or year — matches **only when its whole period
     lies inside the window**: «juuni 2024» is in 01.06–30.06.2024, in «alates
     01.06.2024» and in «2024», and in no window that holds only part of June.
     Its anchor (the 1st) is a place in the ordering, never the day it
     happened (0079 §2, 0134 §5);
   - the **native-record `updated_at` fallback** is not work (0026, 0134 §2):
     a Matter whose only date is that fallback is in no `viimane_*` window,
     though the column still prints it. A technical edit after old work moves
     nothing; that is `muudetud_*`;
   - the **latest** activity decides: a Matter worked on in the window and
     again after it is not in it. «Any work in a period» stays
     `work_activity_between` (Osakond), which this filter does not borrow.

   Containment differs from 0122 §2 on purpose. There, a bounded deadline
   window never holds a period, because the windows are a consecutive set
   beginning today and containment would place one period in different
   windows on different surfaces. A `viimane_*` range is one interval a reader
   typed about the past, and a period wholly inside it certainly falls inside
   it; refusing it would hide a Matter whose last work is known to be «2024»
   from every 2024 range. The sort and the column's display are unchanged.
4. **A column catalogue** (`app/matters/register_columns.py`) defines each
   column once: key, heading, default or not, sort (only where the register
   already sorts correctly), and typed spreadsheet cells. `?veerud=` carries the
   choice (absent = today's six columns). Added columns follow the six base ones
   in catalogue order, so the table and its Excel have one order. A column never
   changes which rows are shown; a filter never changes the columns. A heading
   sort whose column is hidden falls back to the default order and says so — in
   the table and in the export alike. Saabunud passes no columns and is
   unchanged. Values for a page or an export are read in a fixed number of
   statements (`load_facts`), each `visible_to`-scoped.
5. **Three exports**, all through `app/core/xlsx_safety.py` (explicit cell
   types, formula-looking text stored as text with a quote prefix, real dates
   only for exact days, private no-store responses):
   - «Laadi Excel tabelis kuvatud infoga» — the applied address, every page,
     the chosen columns, the table's order. Unsubmitted chooser or panel ticks
     are not in the address and so not in the file.
   - «Laadi Excel kõikide andmetega» — every Teema the reader may see (open,
     closed, archive; real data only), every catalogue column plus identity
     fields, one sheet per canonical reporting year and «Teadmata»; the table's
     filters do not apply, and the info sheet says so. Creation year is never a
     reporting year.
   - «Laadi Excel vana Exceli formaadis» — the unified 2026 layout A–L
     (decision A; `docs/data-contracts/legacy-register-export.md`): G is always
     KELLELE and never filled from a pre-2020 KELLELT value; VÄLJA is the
     earliest historically sent Submission, else the register's own text,
     never converted into a date or a send; member counts only as recorded;
     HETKESEIS and JÄRGMISEKS are current values. It is not an era-faithful
     reproduction and does not claim round-trip compatibility.

## Consequences

- No schema change. URL compatibility: every single-value address keeps its
  meaning; the comma form of `paritolu` is unchanged.
- The register's filter panel posts repeated parameters, so a hand-made address
  that repeats a single-value dimension (e.g. `?aasta=`) still reads the last
  value, as before.
- «Original responsibility» is not offered as a filter or column: there is no
  reliable first-class source (only audit events for changes made here and the
  imported first-name owner). Reported as a gap rather than synthesized.
- Measured on synthetic data (see the PR): export cost grows with rows, not with
  rows × columns.
