# ADR 0012 — Legacy register import architecture

- Status: accepted
- Date: 2026-08-19
- Stage: 2A
- Supersedes: nothing
- Related: ADR 0002 (identifiers), ADR 0005 (authorization), ADR 0013 (search)

## Context

The department's register is a single workbook, `Tööd eelnõudega.xlsx`, with one
sheet per year from 2011 to 2026 plus a `Hetkeseisu info` vocabulary sheet.
Direct inspection of the supplied 2026-08 snapshot found 3,023 rows below the
headers, of which 2,455 carry a matter, 108 are numbers reserved ahead of use,
451 are blank padding and 9 are neither.

It is not one schema. Between 2011 and 2026 the sheet gained member-feedback
columns, gained a status column, gained a next-action column, moved its header
row once, and — the expensive one — **changed what its counterparty column
means** without changing much else. `KELLELT` (2011–2019) is who sent the
material. `KELLELE` (2020–2026) is who Koda sent its opinion to. The headers
look alike and the meanings are opposite.

The importer must also not run against real data yet: the Secure Pilot Gate has
not been passed, and at the time of writing the repository is public.

## Decision

**Five layers, strictly separated, and only the last one writes.**

    parse → extract → plan → apply
                        ↘ report

`parser.py` reads cells. `extraction.py` applies a year's contract to a row.
`planner.py` decides what would happen, reading the database and writing
nothing. `apply.py` executes a plan in one transaction. `reporting.py` and
`plan_reports.py` write reports from either.

The separation buys three specific things. The offline inspector can run on a
machine with no PostgreSQL, which is the machine an operator actually has when a
new snapshot arrives. The dry run and the apply consume *the same plan object
from the same code*, so what a reviewer approves is literally what executes.
And search can be rebuilt without re-importing, because import and search are
not coupled.

**One reviewed contract per year, in TOML, and no sheet is parsed without one.**
`docs/data-contracts/excel-era-YYYY.toml` states the expected header row, the
exact header text of every column, its canonical meaning, its parser, its
direction, and what a blank cell means. Contracts use closed vocabularies and
fail to load if they use a value outside them. A contract may not describe both
a sender and an addressee column; a column feeding `source_organisation` must be
marked `direction = "source"`. TOML because `tomllib` is in the standard
library, so a reviewer can read the rules without installing anything.

**A header that does not match its contract is a finding, not a hint.** The
parser does not shift, guess or best-fit. The failure mode being prevented is an
imported year whose columns are one to the left, which produces confident,
wrong history that nobody notices for a year.

**openpyxl, not pandas.** The hyperlink on a title cell is the only surviving
pointer to the OneNote page behind a matter, and the difference between a date
cell and a string that reads `43831` is evidence about which era wrote the row.
pandas discards both on the way to a DataFrame. Here they are the payload.

**Exact matching or nothing, for owners, organisations and statuses.**
Conservative normalisation — casefold, strip diacritics, collapse whitespace —
is allowed because it changes spelling, not identity. Nothing beyond that. The
register contains `MKM` and `Majandus- ja Kommunikatsiooniministeerium` for one
ministry, and it contains `Keskkonnaministeerium` and `Kliimaministeerium`,
which look equally similar and are *not* the same body: one pair is a rename and
the other is a change of remit. A similarity score cannot tell them apart. A
reviewed alias can, because it is somebody's recorded decision.

Unresolved values leave the canonical field null, keep the raw text, and appear
in a mapping-gap report. An operator answers them in a mapping file, which is
input to the importer and never something it writes for itself.

**Reserved numbers are a first-class outcome.** The 2026 sheet is numbered to
`2026_300` while only 192 rows carry a matter. Those 108 trailing rows are not
defective matters, and reporting them as "missing title" would bury the rows
that genuinely need a decision. They get their own outcome, create nothing, and
**still push the reference sequence forward** — because the department considers
those numbers spoken for, and a sequence that only knew about imported rows
would hand `2026_193` to the next natively created Matter.

**Every relevant row leaves the planner with exactly one outcome**, so the
outcomes partition the sheet and the totals can be checked against the row
count. Anomalies are a separate, multi-valued thing: a row can have an unmapped
owner *and* still be created, and a reviewer needs to see both.

**Immutable provenance is enforced by the database.** `MatterSourceReference`
already refused raw edits in `save()`. That guard never covered
`QuerySet.update()`, `bulk_update()`, a data migration, a shell session or
`psql`. Migration `0003` adds a trigger. Interpretive and operational columns —
`match_method`, `conflict_state`, `reviewed_by`, `onenote_content_status` —
remain editable on purpose: an immutability rule that froze those would push
people to delete and recreate the row, losing the evidence it exists to protect.

## Consequences

Adding a year means writing a contract, not changing code. A year without one is
reported and skipped rather than guessed at.

The importer will refuse to do things an operator asks for when doing them would
require inventing data. An override forcing `FULL` onto a row whose status says
Koda stopped working on it is rejected out loud, because a closed FULL Matter
must carry a closure timestamp and the register never recorded one.

Some things are deliberately slower than they could be. Every unresolvable
ministry name needs a human decision before that Matter gets its organisation.
That is the cost of not creating four hundred near-duplicate Organisations.

`openpyxl` is loaded with `data_only=True`, so a formula cell yields its cached
value rather than its formula. For a hand-kept register that is what the humans
saw, but it means a workbook saved by a tool that does not cache formula results
would read as blank. The snapshot hash makes that detectable after the fact.

## Alternatives considered

**One tolerant parser with heuristics per column.** Rejected. It is the design
that produces a plausible import nobody can audit, and the register's history is
exactly the kind that punishes it.

**A second `LegacyRegisterRecord` table.** Rejected by the master specification
(11.5, 19.4) and independently here: one canonical Matter model with
`record_mode` means an archive row that becomes relevant is promoted, not
copied.

**Importing `VÄLJA` (the sent date) as a Submission.** Rejected. Submission is
the canonical outbound record and `SENT` requires both a timestamp and an
immutable final evidence document. Creating one from a bare date would
manufacture a sent opinion with no evidence, breaking a Stage-1 invariant. The
value is preserved raw and the modelling gap is recorded as an open decision.
