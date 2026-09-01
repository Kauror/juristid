# ADR 0053 — The 1 September snapshot, and two spellings the parser could not see

- Status: accepted
- Date: 2026-09-01
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0045 (the repeatable current-register refresh, which this uses
  unchanged), ADR 0021 (the reviewed snapshot scope), ADR 0011 (why a `VÄLJA`
  date is not a Submission)

## Context

The 1 September workbook is the third snapshot the repeatable refresh has been
asked to read, and the first one that needed almost nothing from it. Its headers
match the reviewed 2026 and 2025 contracts exactly; no column was added, renamed
or repurposed; the two sheets carry 201 and 220 titled rows against 199 and 220
derived rows in the database. Two 2026 references are new, none disappeared,
none is duplicated, and across every canonical field the maintained years hold
**one** change: a `HETKESEIS` cell that now reads `17`.

So this is not an architecture question. ADR 0045's machinery — authorship
decides, the sheet's own year settles a year-less date, the plan reads the
portfolio it is about to write — is what reads this workbook, and it read it
correctly on the first run.

What the workbook did expose is two sentences in `JÄRGMISEKS` that state an
instruction and a date perfectly plainly, and that parser 2.0 could not see
because of how they were spelled rather than because of what they said. Both are
the shape ADR 0045 §2 and §3 were written for; neither is a new question.

A third shape appeared as well, on three rows, and it is **not** adopted here.

## Decision

### 1. The digest is registered; the 30.08 workbook is not

`3db743ac…` joins `REVIEWED_SNAPSHOTS` with `current_years = {2025, 2026}` and
`snapshot_date = 2026-09-01`. Same scope as the two before it, and stated again
rather than inherited: the department maintains 2025 and 2026, and nothing in
this workbook speaks for 2024 or earlier.

A 30 August workbook sits beside it on the operator's desk and is deliberately
**absent** from the list. A reviewed snapshot is a statement that somebody looked
at those exact bytes; nobody did, it was superseded before it was ever planned,
and adding it now to make the sequence look continuous would record a review that
never happened.

### 2. `küsi` is a review verb, with the particle

*küsi 05.10 üle* is the register asking a ministry again on a named day. That is
a review of a wait — `MONITOR` + `REVIEW_ON` — and not work Koda owes anybody,
which matters because only a `DO` with a `DEADLINE` can be reported overdue.

It is admitted **only with `üle`**, exactly like `vaata`. Bare *küsi* is "ask",
and the register writes that about the substance of a file constantly; a stemmed
`küsi\w*` would put every one of those on somebody's work list.

### 3. A comma between the verb and the particle is a typo when only a date sits there

*Vaata, 10.11 üle* is one instruction with a stray comma in it, not two clauses.
Punctuation decides clause ownership everywhere else in this module and still
does: the exception admits **a single date and separators and nothing lexical at
all**. A real second clause has words in it, so the rule cannot assemble an
instruction out of two of them — which is the property the tests pin, not the
one row it happened to recover.

### 4. A review date beside an external milestone stays refused

Three 2026 rows write a first-person review instruction with its own date beside
a plainly-stated third-party date — a plenary sitting, a commission's quarter, a
ministry's month. Reading the date inside the review clause as the target would
convert all three, and it is a tempting reading: the date sits between the verb
and the punctuation that ends its clause, which is where Estonian puts the thing
the verb governs.

**It is not adopted.** Parser 2.0 considered this exact sentence shape and
refused it on the record — *"an external milestone is not commentary, and
choosing between it and the review date is not a reading"* — and that refusal is
a reviewed decision with a test standing on it, not a gap somebody left. A
refresh brief is not the place to reverse it: the three rows are reported as
review candidates with both source fragments quoted, and a person decides whether
the reading is sound. If it is adopted later it should be adopted as its own
change, with the ADR 0045 §4 clause-ownership argument extended properly rather
than as a side effect of registering a workbook.

Parser version 2.0 → **2.1**. The version travels inside the plan digest, so the
two snapshots cannot be mistaken for one another even where the sentence is
unchanged.

### 5. `17` is not a stage

One 2026 row's `HETKESEIS` now reads `17`. `resolve_status` returns no stage and
no disposition for it, `_observed_changes` therefore proposes no stage change,
and the raw cell is preserved on the derived row where a person can see it. The
controlled vocabulary does not grow itself from whatever a spreadsheet contained,
and this is what that rule looks like when it fires. The row stays current work
— an unreadable status is not a terminal one — and it is reported for correction
at the source.

## Consequences

No schema change, no migration, no new model, no change to `authorization.py` or
`INDEX_VERSION`. The refresh command, the four digests, the `HUMAN_WINS`
precedence and the outreach split are all untouched.

Two 2026 rows convert that did not convert before, and nothing else in either
maintained sheet reads differently — measured across all 162 non-blank
`JÄRGMISEKS` cells on the 2025 and 2026 sheets, not argued. The 2025 sheet is
unchanged in every respect, which is the expected result: year-less dates do not
resolve there and that is ADR 0045 §2, not an omission.

`CAMPAIGN_WINDOW` still ends on 28 August 2026 and is deliberately left alone.
No campaign export participates in this refresh, so the campaign set is empty and
inside the plan digest as empty; widening a reviewed pilot window is a decision
about outreach, not about reading a newer register.
