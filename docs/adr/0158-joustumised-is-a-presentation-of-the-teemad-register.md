# 0158 — Jõustumised is a presentation of the Teemad register, not a destination

**Status:** accepted
**Date:** 2026-10-13
**Amends** ADR 0071 (Teemad is the discovery surface). ADR 0071's decision
stands in full: the department-wide «Jõustuvad aktid» page stays retired,
`/jalgimine/joustumised/` and `/joustuvad-aktid/` keep redirecting to
`/teemad/?joustumine=on&olek=koik`, nothing is added to the bar, and the
filters `?joustumine=`, `?joustub_alates=`, `?joustub_kuni=` keep their
meaning, including the containment rule.
**Related:** ADR 0018 (structured Matter facts and the precision vocabulary),
ADR 0065 (adding a structured fact happens on the Matter), ADR 0122 (the
deadline windows), ADR 0155 (a retired control stays retired).

## Context

ADR 0071 turned `Jõustumine` into a dimension of the register, which made
«avatud teemad, millel on jõustumine» askable. What it could not answer is the
question the department actually asks of commencements: *which* parts of
*which* acts commence *when* — the main body on 1 January, particular
provisions eighteen months later, a register abolished on a third date. The
register lists Matters, one row per file, so a file that commences in three
stages is one row and none of the three dates is visible.

The product owner decided on 2026-10-13:

> Preserve ADR 0071. Build the full Jõustumised calendar/list experience as an
> alternative view within the existing Teemad register, activated by its
> Jõustumine filters. Do not restore the retired standalone page and add no
> top-level navigation destination. Reuse the canonical MatterEffectiveDate
> model, existing register filtering and permission boundaries. Keep the normal
> Teemad view unchanged.

## Decision

### 1. A view switch inside the register, present only under a Jõustumine filter

`?vaade=` takes two values on `/teemad/`:

| Address | Shows |
| --- | --- |
| *(absent)* | the Matter table, exactly as before |
| `?vaade=joustumised` | the same filtered register, one row per commencement, in buckets |
| `?vaade=kalender` (`&kuu=YYYY-MM`) | the same, on a month calendar |

The switch (*Teemad · Jõustumiste loend · Jõustumiste kalender*) is drawn under
the filter chips **only while a Jõustumine filter is narrowing the register** —
`?joustumine=on`, or a `joustub_alates`/`joustub_kuni` window (which needs no
`on` beside it, ADR 0071). `?joustumine=puudub` selects files with no
commencement, so it offers no switch. A `?vaade=` without a commencement filter
is ignored and the ordinary table renders: the presentation cannot be reached
as a page of its own, which is the distinction from the retired destination.

`vaade` because it is the word every other page's view switch uses (Osakond,
Minu asjad, Ülevaade). The list orders by its own `?joustumiste_jarjestus=`
(`kuupaev`, `kuupaev_desc`, `teema`), never by `?jarjestus=`, whose values order
*Matters* and mean something else.

### 2. The population is the register's, and nothing is re-decided

The view hands `app/intelligence/commencements.py` the register's own queryset
— searched (`?q=`), authorized (`visible_to`), status-segmented and narrowed by
every dimension — as a subquery of primary keys. *Vastutaja* and *Valdkond* are
therefore the register's own `?vastutaja=` and `?valdkond=`, chosen in Täpsem
otsing or from the Vastutaja heading, and shown as chips; no new filter
parameter exists. The status segment applies too: the register opens on
*Avatud*, so the view says «Näidatakse avatud teemasid — kuva kõik olekud»
while it does (a commencement often lands after the department closed its
file; the retired-page redirect already carries `olek=koik`).

Every presentation parameter (`vaade`, `kuu`, `joustumiste_jarjestus`) is in
the URL, carried by the live search box, the status segments and the
Täpsem otsing form (`vaade` only — a changed window re-anchors the calendar),
cleared by `Tühjenda kõik`, and dropped by a chip's `×` when that chip was the
last Jõustumine filter.

### 3. Which records, and how they are read

* **Reader-scoped first.** `MatterEffectiveDate.objects.visible_to(user)` before
  anything is bucketed, placed on a day or counted. A restricted commencement on
  a readable Matter does not appear, and does not change a count.
* **Active only.** `FactStatus.ACTIVE` — the same rule `?joustumine=on` reads.
  `CANCELLED` and `SUPERSEDED` rows stay history on the Teema page and are in no
  bucket, no calendar cell and no count.
* **The window narrows rows, by the same condition.** A Matter is in the
  register because *one* of its commencements is inside the window; the listing
  shows only the ones that are. `register_filters.commencement_window_q` is the
  one statement of containment, read by the filter and by the listing.
* **Several per Matter are several rows.** The register still counts the file
  once; the count line reads «N teemat · M jõustumist».

### 4. Precision is shown honestly; no day is invented

Every date is written by `display_when` / `format_at_precision`: «III kvartal
2027», never «1.7.2027». An approximate row also says «kvartali täpsusega»
(the `DatePrecision` label) and, while its period has begun and not ended,
«periood kestab».

**The list's buckets**, on the Tallinn date (`timezone.localdate()`):

| Bucket | Rule |
| --- | --- |
| Järgmise 30 päeva jooksul | containment: `date_value ≥ today` and `period_end ≤ today + 30` |
| Hiljem | dated, not over, not wholly inside the next 30 days — including a running quarter |
| Kuupäev teadmata | `GENERAL_ORDER` and `UNKNOWN`, which carry no date by constraint |
| Möödunud | `period_end < today` — the period's *end*, as `_direction_q`/`has_passed` decide it |

Past rows appear whenever the population has them; a window starting today or
later has none.

**The calendar** (Monday first) places a day — `EXACT` or `INFERRED` — on its
cell. A `MONTH` row recorded for the shown month is listed above the grid as
«Kuu jooksul, päev teadmata». A quarter, half-year or year that overlaps the
month is listed separately as «Pikem periood, mis hõlmab seda kuud — võib
jõustuda ka sel kuul»: never on a cell and never counted as the month's, which
is the containment rule applied to a month. Undated rows are counted under the
grid with a link to the list, where they have a bucket.

**Clickable dates.** A day with commencements links to the list narrowed to
`joustub_alates = joustub_kuni = that day`; the month's title links to the list
narrowed to the whole month. Both write the existing window parameters, so the
result is an ordinary register narrowing with ordinary chips.

### 5. Coverage is stated, not filled

Under the view, always: «Jõustumised on näha ainult siis, kui need on teemale
struktureeritult lisatud. Kuupäevi ei tuletata pealkirjast, Hetkeseisust ega
Järgmiseks-tekstist.», followed by «N teemal selles valikus jõustumist kirjas ei
ole» linked to the same narrowing with `?joustumine=puudub`. Counted from the
same searched, reader-scoped population with the register's own pipeline. No
date is parsed out of a title, `Hetkeseis` or `Järgmiseks`, and an absent
record is never shown as «does not commence».

### 6. A commencement is nobody's deadline

Unchanged from ADR 0071: nothing here creates a `NextAction`, reaches Minu
asjad or is «üle aja». The view only reads.

## Consequences

* The ordinary register is byte-for-byte what it was unless a Jõustumine filter
  is active, and then only the switch is added above the unchanged table. No
  new query runs on the ordinary page (`_commencement_context` returns before
  reading anything).
* Under the view, the page reads the commencement rows once (bounded by the
  department's commencement records — hundreds, not the register) plus one
  coverage `COUNT`. The Matter page of the register is still computed for the
  count line.
* No migration, no new model, no index, no search-index change, no URL route,
  no navigation item. `app/intelligence/commencements.py` is the read-only
  selector module; `app/matters/views.py` wires it into `matter_list`.
* `tests/test_retired_ui_contract.py` pins that the retired page still redirects
  and that no commencement destination is on the bar (ADR 0155 §3).

## What was considered and refused

**Restoring `/jalgimine/joustumised/`.** Refused by the owner and by ADR 0071:
it would be a second list of Matters selected by one property, unreachable
from the register's other dimensions.

**Overlap for the calendar month.** A quarter overlapping October drawn as
«October's» would be the false precision ADR 0071 refused for the window. It is
shown beside the month, labelled for what it is.

**Status and precision filters.** Not added. Status would expose `CANCELLED`
and `SUPERSEDED` as a population the register's `?joustumine=` does not mean;
precision is already visible on every row.

**Paginating the commencement list.** The register pages Matters; paging
commencements by a Matter-sized page would split one file's stages across
pages. The population is small enough to render whole.
