---
contract: excel-era
version: template
status: template — one completed file per workbook sheet/year
owner: department head + lawyers (reviewers), technical owner (parser rules)
---

# Per-era Excel column contract — template

The `Tööd eelnõudega` workbook is not one format. Direct inspection of the
supplied copy shows material era differences, and the same column header can
mean different things in different years. Every sheet therefore gets its own
reviewed contract file named `excel-era-<year>.md`, and no importer runs against
a sheet that does not have one.

Complete **2026 and 2025 first**; they carry the active set.

## Known era boundaries

| Era | What changes |
| --- | --- |
| 2011–2017 | Core register. Counterparty column is `KELLELT`. No status or next-action model. |
| 2018–2019 | Two member-feedback count columns appear. Counterparty remains `KELLELT`. |
| 2020–2022 | Counterparty column becomes `KELLELE`. Count columns remain. |
| 2023–2024 | `HETKESEIS` appears; usage is sparse and inconsistent early on. |
| 2025 | `JÄRGMISEKS` exists and is partly populated. |
| 2026 | Current standardised operating structure. |

**`KELLELT` means source/sender. `KELLELE` means recipient/addressee.** They are
different facts and map to different columns (`source_organisation` and
`addressee_organisation`). They must never be unified because the header text
looks similar.

## Per-column rows

Fill one row per column in the sheet.

| Original header | Canonical field | Meaning and direction | Type / parser rule | Null vs zero semantics | Raw preservation | Confidence | Reviewer |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `NR` | `Matter.reference_year` + `reference_number` | Human register reference | split on `_`, both integers | blank means no reference was ever assigned | verbatim in `source_row_raw` | | |
| `PEALKIRI` | `Matter.title` | | trim only | blank is a data-quality flag, not an empty title | verbatim | | |
| `KELLELT` | `Matter.source_organisation` | **sender** (2011–2019) | resolve against `Organisation`, never by name similarity alone | blank means unknown | verbatim | | |
| `KELLELE` | `Matter.addressee_organisation` | **addressee** (2020+) | as above | blank means unknown | verbatim | | |
| `SAABUS` | `Matter.received_date` | | date parser vN; Excel serials stay as raw strings | blank ≠ epoch | verbatim in `source_date_raw` | | |
| `TÄHTAEG` | `Matter.response_deadline` | | date parser vN | blank ≠ no deadline; record which | verbatim | | |
| `HETKESEIS` | `Matter.stage` **or** `Matter.disposition` | see the status-mapping rule below | `workflow.LegacyStatusMapping` | blank means the era had no status model | verbatim | | |
| `JÄRGMISEKS` | Stage-1 `NextAction` | free text, later parsed for a review date | no automatic parse in the importer | | verbatim | | |
| `KÜSITUD` | `Consultation.contacted_count` | independent observation | integer | **blank ≠ 0** | verbatim | | |
| `VASTAS` | `Consultation.response_count` | independent observation | integer | **blank ≠ 0** | verbatim | | |

## Status mapping rule

Some `HETKESEIS` values are not procedural stages. The workbook value
`rohkem pole tegevusi plaanis` describes closure, not where the external process
stands, and maps to a disposition. Each raw label gets a row in
`workflow.LegacyStatusMapping` with the raw text preserved and exactly one
interpretation — a stage **or** a disposition, never both.

The current workbook contains **11** authoritative `HETKESEIS` labels, including
`ootan ELi õiguse ülevõtmist`. The full list is transcribed from the live
workbook during the Stage-0 vocabulary workshop. It is not reconstructed from
memory or from any secondary report.

## Anomalies that must survive import

These are legitimate evidence, not dirt. The importer preserves them and the
test fixtures assert they still exist:

- Excel serial numbers left as strings (`43831`);
- blank versus zero counts, which are different facts;
- `VASTAS` greater than `KÜSITUD`;
- negative intervals;
- rows with no status at all because the era had no status column;
- OneNote hyperlinks that resolve to the wrong page.

Nothing is "cleaned" automatically. A correction is a reviewed interpretation
recorded beside the raw value, never an edit to it.

## Completeness ledger

Each import run produces a bidirectional ledger: every workbook row maps to a
Matter, an archive Matter, or an explicit unmatched entry with a reason. No
source row disappears silently.
