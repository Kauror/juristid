# ADR 0045 — The repeatable current-register refresh: authorship decides, and the sheet's own year settles a date

- Status: accepted
- Date: 2026-08-28
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0011 (why `JÄRGMISEKS` was not converted, and why a `VÄLJA` date
  is not a Submission), ADR 0021 (the final register cutover and the reviewed
  snapshot scope this extends), ADR 0027 (`MatterEngagement` as a pointer, not a
  system), ADR 0038 (child visibility in projections), ADR 0041 (search index
  freshness)

## Context

The register is a living file. Juristid has been able to read it exactly once:
the final cutover reconciled the portfolio against the 21.08 workbook, the
enrichment converted that workbook's readable instructions, and everything after
that froze. A review date typed in August stayed August into October. An owner
who handed a file over kept it. A `HETKESEIS` that moved to *riigikogus* did
not.

The 28.08 workbook is the same file a week later, and reading it exposed four
things the one-shot architecture could not do:

1. Every Matter the enrichment had spoken about now carried an action — **its
   own** — and the precedence rule skipped any Matter with any action. The
   operation had made itself unrepeatable.
2. The maintained 2026 sheet writes dates without years constantly. Sixty-five
   of its 131 instructions carry one, and the parser refused all of them.
3. `VÄLJA` on sixteen 2026 rows reads **ei saatnud** — a decision somebody
   recorded — and the derived state knew only that *something* was written,
   which renders as a send date whose value was lost.
4. The product owner asked for the two member-feedback columns on the file, and
   those columns hold 124 written zeros against 19 blanks in one sheet. A
   representation that could not tell those apart would be worse than none.

## Decision

### 1. Authorship, not existence, decides whether the register may speak

`SKIP_EXISTING_ACTION_HISTORY` is replaced by `HUMAN_WINS`, and the test behind
it moved from *is there an action* to *did a person do anything here*. The audit
log already answers that exactly: `set_next_action` records `CURRENT_REGISTER`
under `payload.provenance.source` and leaves `actor` null, and every surface a
person uses passes an `actor`. `action_ownership` reads it in two queries.

An action is the register's own only when its `NEXT_ACTION_SET` event named
`CURRENT_REGISTER` and carried no actor, and it stays the register's own only
while every later event about it also carried no actor. **One signed-in person
anywhere in a Matter's action history and the whole Matter is theirs,
permanently** — creating, completing, cancelling or superseding.

Four outcomes follow, and each names what provenance proved: `AUTO` (nothing has
ever existed here), `REFRESH_IMPORTED` (ours, untouched, and the newer workbook
reads differently — superseded, so the chain survives), `IMPORTED_UP_TO_DATE`
(ours, and the newer workbook reads identically — nothing written, which is what
makes a re-run free), and `REMOVE_STALE_IMPORTED` (ours, and the newer workbook
no longer states a readable instruction — cancelled, with provenance).

`cancel_next_action` gained the `provenance` keyword `set_next_action` already
had, and the pairing matters more here: a null actor is precisely what tells the
next run that nobody has touched this, so the reason it is null has to be
recorded rather than inferred from its absence.

**The rejected alternative** was to key ownership on `NextAction.status` —
treating a cancelled or completed action as human work. That reads the
operation's *own* withdrawal as somebody's decision and freezes the file
permanently, which is the same self-blocking defect one level down.

### 2. A year-less date means the sheet's year, and only where the snapshot agrees

`ParseContext` carries two facts and no more: the sheet the row sits on, and the
day the workbook was taken. A year-less date resolves **only when those two
years are the same**.

On the 2026 sheet of a 28 August 2026 workbook, *vaata üle 15.09* was written
this year about this year. On the 2025 sheet of the same workbook nothing
resolves — not because 2025 is old, but because a 2025 row is still maintained:
somebody may have typed that cell in November 2025 or in August 2026, those are
two days a year apart, and the sheet cannot say which. The register's own
writers resolve it by remembering, which is not evidence.

The inference is **year = sheet year**, never *the next such date after today*.
A 2026 row reading *vaata 15.07 üle* means 15 July 2026 — a date that has
passed, which the planner reports as `STALE_SOURCE`. It must never become 2027,
which the source did not say, and it must never depend on the day the command
happened to run.

Omitting the context reproduces 1.2 exactly, so a caller that has not thought
about provenance cannot accidentally acquire a year.

### 3. A wait and a review of it is one instruction

`ootan valitsusele saatmist, vaata üle 15.09` names one date and it says when
Koda looks at the wait. It becomes `WAIT` + `REVIEW_ON`: `WAIT` because Koda is
not the one who has to act, `REVIEW_ON` because that is what the date means.
Only a `DO` with a `DEADLINE` can be reported overdue, so nothing here can put a
ministry's timetable on this department's late list.

The pair is read as one instruction **only** in that exact shape — a wait and a
review, nothing else beside them, and exactly one actionable date. Anything
wider and `WAIT_AND_REVIEW` still refuses it.

### 4. Clause ownership removes a date rather than being defeated by it

An entry-into-force clause and a past-tense clause each own their date. 1.2 only
noticed when such a date was the *only* one, so a sentence carrying a live
review date beside a commentary date was refused as ambiguous and **both** were
discarded. 2.0 filters governed dates first, which lets the review date win
*because the other demonstrably belongs to another clause* — a reading, not a
preference for first, last or nearest.

The protection is unchanged where it was load-bearing: when filtering removes
every date there was, the named refusal stands rather than degrading into a
dateless action.

Parser version 1.2 → **2.0**.

### 5. Blank is not zero, and the two counts live on the derived table

`CurrentRegisterState` gains `member_feedback_responded` and
`member_feedback_requested` as **nullable** integers. `NULL` means the register
did not record the number; `0` means somebody measured and the answer was none.
The 2026 sheet holds 124 written zeros against 19 blanks in one of these
columns, so collapsing them would report 124 measurements as gaps.

They are **not** on `Matter`. They are a statement the latest reviewed workbook
makes *about* the Matter, rebuilt whenever a newer snapshot is approved, never
edited in the application and attributed to no particular outreach.

Nothing divides one by the other. The register's own contract says the columns
are not subsets of one another and the real data holds rows where more members
answered than were asked directly.

The 2025 and 2026 era contracts move these columns from `deferred` to a new
authority level, **`derived`**. `deferred` means nobody has decided where the
value belongs; `derived` means somebody has, and the answer is the derived
table. 2018–2024 stay `deferred`: this refresh speaks for the maintained years.

### 6. `VÄLJA` is four answers, and `KELLELE` may name several bodies

`opinion_sent_state` records `DATE` / `NOT_SENT` / `RECORDED_OTHER` / `BLANK`.
`opinion_sent_recorded` is unchanged and still answers the portfolio's question
from presence alone; this answers the reader's. **ei saatnud** is a recorded
decision and reads as one.

None of the four can become a `Submission`. A sent opinion's canonical record
needs immutable final evidence and a spreadsheet cell is not evidence (ADR 0011,
DATA-001).

`addressee_raw` and `addressee_cardinality` keep the complete `KELLELE` cell.
The canonical singular `Matter.addressee_organisation` is written **only** from
a cell naming exactly one organisation; thirteen cells in the 28.08 workbook
name two or three, and taking the first would record — with no trace of the
choice — that Koda wrote to one ministry when it wrote to three. Matter
addressee cardinality is **not** redesigned here.

The separator set is comma, semicolon, slash and standalone `ning` — and
deliberately **not** `ja`. The ministries Koda writes to are called *Majandus-
ja Kommunikatsiooniministeerium*; splitting on `ja` reads 193 single addressees
as pairs and invents an organisation for each one.

### 7. Outreach is proposed by a matcher and written only by a reviewed mapping

The candidate matcher uses owner (a hard filter), the consultation window (a
hard filter) and subject overlap (which raises confidence and never rejects). It
writes nothing, ever.

That split is not ceremony. The register titles a Matter by its instrument and
the campaign titles it by its subject; on the pilot data those two strings share
no content word for one of the seven consultations the operator identified, and
they are the same consultation. A matcher confident enough to link them would be
confident enough to link things that are not. On the pilot the rules produce
eleven high-confidence pairs and sixty-four further candidates, and that one
consultation appears only as a candidate — which is the honest result.

Only a reviewed mapping file, prepared by a person and named by its digest at
apply time, creates a `MatterEngagement`.

Import identity lives in a new `RegisterEngagementImport` row rather than a
column on `MatterEngagement`, following `OpinionSubmissionImport`. Its unique
constraint is `(matter, channel, source_key)`, where the key is the vendor's
template URL or the public page URL — never a title, because correcting a title
is the only editing `Kaasamine` supports and identity that moved when somebody
fixed a typo would duplicate the record they were tidying. Hand-made rows stay
distinguishable from imported ones for free.

Only recipient counts are imported. Opens, open rate, views, clicks, click rate,
bounces, unsubscribes and complaints are all in the export and none of them is
imported: they are engagement analytics about identifiable members and a legal
file is not where they belong.

**Sendsmaily enqueues are not the register's feedback count.** One 2026 file
records 273 members asked directly against 234 addresses enqueued. Both are
true, the mailing is one channel of several, and each stays on its own record.
Neither is ever substituted for the other.

### 8. Plan, review, apply — four digests

The workbook digest says which bytes were reviewed and is checked against
`REVIEWED_SNAPSHOTS`. The plan digest says nothing in the database moved between
deciding and writing. The campaign digest names the export the candidates came
from. The mapping digest names the links a person approved.

The apply re-derives the plan inside its transaction and demands the same digest
before writing anything. A difference aborts everything: a partial apply against
an approved digest would leave a state neither the plan nor the database
describes.

The report is complete **before** anything is written, which needed one piece of
machinery: the enrichment reads the derived state table, and at plan time that
table still describes the previous workbook. So the plan projects the rows the
reconciliation would write, in memory, through the same function that writes
them. One derivation, so the report and the apply cannot drift.

## Consequences

The scope is unchanged and that is a reviewed decision, not an inherited
default: `current_years` stays `{2025, 2026}` and this operation cannot move
2024 and earlier in either direction. The 21.08 snapshot's identity stays in
`REVIEWED_SNAPSHOTS` beside the 28.08 one — a state row naming a snapshot the
reviewed list had forgotten would be evidence with no provenance.

`INDEX_VERSION` is untouched at `AUTH003.1`; search is refreshed through the
existing mechanism after the writes. `authorization.py` is untouched. The only
schema changes are six derived columns on `CurrentRegisterState` and the new
`RegisterEngagementImport` table. No business-data migration runs: the one
backfill derives the new `VÄLJA` reading from two columns each row already
carries, because a check constraint cannot install over rows that contradict it,
and `RECORDED_OTHER` is deliberately not refined into `NOT_SENT` there — telling
those apart needs the raw cell, which lives on the source reference.

Five assertions in `test_register_next_action_parser_12.py` moved. Each says so
where it stands; 1.2's module keeps its name because most of what it decided is
still exactly what the parser does.
