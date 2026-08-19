# Data contracts

Versioned agreements about data that crosses a boundary — into the system from a
historical source, or out of it to a consumer.

| Contract | Purpose | Status |
| --- | --- | --- |
| [`dashkoda-export-v1.md`](dashkoda-export-v1.md) | Fields the future export must carry so Koda's management reporting survives the retirement of the Excel register. | Draft, awaiting the DashKoda owner |
| [`excel-era-contract-template.md`](excel-era-contract-template.md) | Per-era mapping from workbook columns to canonical meaning. One completed file per sheet/year. | Template only |
| [`source-snapshot-manifest.md`](source-snapshot-manifest.md) | The manifest format and directory convention for immutable source snapshots. | Convention agreed |

## Rules

1. A contract is versioned. Changing the meaning of a field means a new version,
   not an edit.
2. Compatibility fields are **derived** from canonical records by a documented
   rule; they are never independently stored.
3. No contract may express a metric the metric catalogue prohibits.
4. Confidential source material never enters this repository. Contracts describe
   structure; snapshots live outside Git.
