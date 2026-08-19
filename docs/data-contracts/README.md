# Data contracts

Versioned agreements about data that crosses a boundary — into the system from a
historical source, or out of it to a consumer.

| Contract | Purpose | Status |
| --- | --- | --- |
| [`dashkoda-export-v1.md`](dashkoda-export-v1.md) | Fields the future export must carry so Koda's management reporting survives the retirement of the Excel register. | Draft, awaiting the DashKoda owner |
| [`excel-era-2011.toml`](excel-era-2011.toml) … [`excel-era-2026.toml`](excel-era-2026.toml) | The per-year contracts the parser actually reads. Machine-readable, validated on load. | Complete, 2011–2026 |
| [`excel-era-overview.md`](excel-era-overview.md) | Human overview of all sixteen contracts. **Generated** — never edit by hand. | Generated from the TOML |
| [`excel-era-contract-template.md`](excel-era-contract-template.md) | The scheme, the era boundaries and the real column names. | Superseded as a contract |
| [`import-mapping-template.toml`](import-mapping-template.toml) | Template for the reviewed owner/organisation/record-mode answers an operator supplies to the importer. | Template, synthetic values only |
| [`source-snapshot-manifest.md`](source-snapshot-manifest.md) | The manifest format and directory convention for immutable source snapshots. | Convention agreed |

## Rules

1. A contract is versioned. Changing the meaning of a field means a new version,
   not an edit.
2. Compatibility fields are **derived** from canonical records by a documented
   rule; they are never independently stored.
3. No contract may express a metric the metric catalogue prohibits.
4. Confidential source material never enters this repository. Contracts describe
   structure; snapshots live outside Git. The repository ignores `*.xlsx`
   outright and `tests/test_repository_data_safety.py` fails if one is ever
   tracked.
5. Where a contract has both a machine-readable and a human form, the machine
   form is the source of truth and the human form is generated from it.
   `python manage.py check_era_contracts --check` fails CI if they diverge.
