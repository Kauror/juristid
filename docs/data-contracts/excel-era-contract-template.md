---
contract: excel-era
version: 1.0
status: superseded by the machine-readable contracts in this directory
owner: department head + lawyers (business meaning), technical owner (parser rules)
---

# Per-era Excel column contract

> **This file is no longer the contract.** The contracts the parser actually
> reads are `excel-era-2011.toml` … `excel-era-2026.toml` in this directory, and
> [`excel-era-overview.md`](excel-era-overview.md) is generated from them by
> `python manage.py check_era_contracts`. This page explains the *scheme* and
> records the era boundaries; it does not describe any individual sheet.
>
> The Stage-0 version of this page carried illustrative column names —
> `PEALKIRI`, `SAABUS`, `TÄHTAEG`, `KÜSITUD`, `VASTAS` — written before the live
> workbook had been inspected. **None of them are real.** The corrected header
> text is below and in the TOML files.

## Why one contract per year

The `Tööd eelnõudega` workbook is not one format. Between 2011 and 2026 the
sheet gained columns, moved its header row, and changed what its counterparty
column *means*. No sheet is parsed without a contract, and a header that does
not match its contract is a review finding rather than a hint to shift columns.

## The real columns

Verified against the supplied 2026-08 snapshot.

| Col | Header | Canonical meaning | Present |
| --- | --- | --- | --- |
| A | `NR` | Human reference `YYYY_N` | every year |
| B | `TEEMA` | Title. Carries the OneNote hyperlink | every year |
| C | `ÕIGUSAKT` | Instrument type. **Not** a Track and not a stage | every year |
| D | `SISSE` | Received date | every year |
| E | `ARVAMUSE TÄHTAEG` | Response deadline | every year |
| F | `VÄLJA` | Date the opinion was sent | every year |
| G | `KELLELT` / `KELLELE` | **Sender** 2011–2019, **addressee** 2020–2026 | every year |
| H | `VASTUTAJA` | Responsible lawyer, as a first name | every year |
| I | `LIIKMETE ARV, KES ANDSID TAGASISIDET` | Members who responded | 2018– |
| J | `LIIKMETE ARV, KELLELT OTSE KÜSISIME TAGASISIDET` | Members asked directly | 2018– |
| K | `HETKESEIS` | Stage or closure, via `LegacyStatusMapping` | 2023– |
| L | `JÄRGMISEKS` | Free-text next step | 2025– |
| K | *(unlabelled)* | Meaning not established | 2022 only |

Note the order of I and J: **the responded count comes before the asked count.**
They are independent observations, and rows where more members responded than
were asked directly occur in most tracked years. No response rate can be derived
from them and none is computed.

## Era boundaries

| Era | What changes |
| --- | --- |
| 2011–2017 | Core register, eight columns. `KELLELT` is the sender. No feedback counts, no status, no next action. |
| 2018–2019 | Member-feedback count columns appear. `KELLELT` still means sender. |
| 2020–2022 | Counterparty becomes `KELLELE`, meaning addressee. 2021 puts a title row above its headers. 2022 carries an unlabelled eleventh column. |
| 2023–2024 | `HETKESEIS` appears; use is sparse and 2024 contains free-text variants. |
| 2025 | `JÄRGMISEKS` appears, partly populated. |
| 2026 | Current standard structure, pre-numbered ahead of use. |

**`KELLELT` and `KELLELE` are never unified.** They are different facts and feed
different columns (`source_organisation`, `addressee_organisation`). The
contract loader refuses a file that claims both, and refuses a column whose
declared direction disagrees with the canonical field it feeds.

## What each contract must record

Sheet and year, expected header row, exact original header text, canonical
meaning, direction, parser rule, authority (`authoritative`, `optional`,
`deferred`, `unknown`), null-versus-zero semantics, known anomalies and a
contract version. All of these are validated on load against closed
vocabularies.

## Status mapping rule

Some `HETKESEIS` values are not procedural stages. `rohkem pole tegevusi
plaanis` describes closure, not where the external process stands, and maps to
a disposition. Each raw label gets a row in `workflow.LegacyStatusMapping` with
the raw text preserved and exactly one interpretation — a stage **or** a
disposition, never both.

**Mappings are era-scoped**, unique per `(raw_label, source_era)`, because the
same text need not mean the same thing in 2019 and 2025. An empty `source_era`
is the generic fallback and an exact era match wins.

The workbook's `Hetkeseisu info` sheet holds **11** authoritative labels, and
inspection of the supplied snapshot confirms the seeded vocabulary matches it
exactly. Five further values appear in rows without being in that list —
including `rohkem tegevusi pole`, one word away from the controlled
`rohkem pole tegevusi plaanis`. **They are not mapped.** Deciding whether those
are the same value is a lawyer's judgement, not a parser's.

## Anomalies that must survive import

Legitimate evidence, not dirt. Preserved, and asserted by tests:

- Excel serial numbers left as strings (`40543`);
- blank versus zero counts, which are different facts;
- more members responding than were asked directly;
- negative intervals, where the deadline was already running on arrival;
- rows with no status because the era had no status column;
- OneNote hyperlinks that may resolve to the wrong page.

Nothing is cleaned automatically. A correction is a reviewed interpretation
recorded beside the raw value, never an edit to it.

## Completeness ledger

Each applied run writes an `ImportRowLedger` row for every non-blank source row,
carrying its outcome and anomalies. Blank padding is counted separately rather
than recorded. The outcomes partition the sheet, so "no source row disappeared"
is arithmetic rather than assertion.
