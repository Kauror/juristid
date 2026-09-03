# ADR 0059 — What a `VÄLJA` mark says about an `Arvamuse tähtaeg`

- Status: accepted
- Date: 2026-09-03
- Stage: pre-QA (shared-gate development phase)
- Related: ADR 0011 (why a `VÄLJA` date is not a `Submission`, and what evidence
  a SENT one requires — unchanged by this record), ADR 0012 and ADR 0021 (the
  register import and the reviewed snapshot scope), ADR 0031 §5 (why an empty
  `Arvamuse tähtaeg` means *no commitment recorded*), ADR 0045 and ADR 0053 (the
  repeatable refresh that keeps the derived state current), ADR 0050 (the
  `Järgmiseks` precedence this extends rather than replaces)

## Context

`Arvamuse tähtaeg` was the department's principal missing-opinion alarm and
almost none of it was true.

Measured on production at `e827d355`, against the 1 September workbook the
deployment holds: **95** open `FULL` Matters carried an outstanding response
deadline and **87** of those were overdue. **77** of the 87 were files whose
register row already recorded that the opinion work was finished. The same page
that called them late also counted **15** files as *Arvamusi koostamisel* — the
population with no `VÄLJA` mark — so the product was stating, forty pixels
apart, that fifteen opinions were in preparation and that eighty-seven were
late. Both numbers were read from the same 204 register rows.

The gap is not an arithmetic error. It is that the work model had no idea what
`VÄLJA` says, while `Arvamusi koostamisel` and the `?arvamus=` register filter
had known since ADR 0021.

### What the register actually records

`VÄLJA` — column F, unchanged in definition across all sixteen era contracts
2011–2026 — is where the department writes that the opinion step on a file is
over. The product owner has confirmed the convention that the Chamber's own
working register is kept to:

```
VÄLJA = kuupäev        arvamus saadeti; arvamusetöö on lõpetatud
VÄLJA = "ei saatnud"   arvamust ei saadetud; arvamusetöö on lõpetatud
VÄLJA = tühi           teemaga tegeletakse veel
```

Two of those three end the work. The third does not, and that asymmetry is the
whole decision: *ei saatnud* is a recorded decision not to send, which finishes
the obligation exactly as sending it does, while a blank cell is the live
drafting queue.

The workbook agrees with itself on this. Of the 2026 sheet's 15 blank rows,
**all 15** carry a deadline and a named owner and **none** carries a terminal
`HETKESEIS`; they are the newest references on the sheet. Of the 16 `ei saatnud`
rows, **not one** has an opinion link, a match candidate or a Submission
anywhere in the archive — the evidence agrees with the register that nothing
went out.

### What the register does not record

It does not record *what Koda sent*. This is where an earlier reading of the
same data was wrong in an instructive way: every VÄLJA date on a current Matter
that also has a canonical Submission matches that Submission's `sent_at`
exactly, 27 of 27 — and the agreement is worthless, because all 312
opinion-derived Submissions in production carry `sent_date_basis =
EXCEL_OUT_DATE`. Their date *was copied from* `VÄLJA`. Comparing the two
measures nothing.

The honest corroboration is elsewhere and it is good: across 612 pairs where an
archived letter carries its own date, the register's `VÄLJA` falls on the same
day 360 times and one day away 248 times — **608 of 612 within a day**. That is
strong evidence that a `VÄLJA` date names the day something went out. It is no
evidence at all about *which document*, and no amount of it ever will be.

## Decision

### 1. Two register states discharge the operational deadline

For **current operational work**, an `Arvamuse tähtaeg` is outstanding when all
of these hold:

- the Matter is open and `FULL`;
- `response_deadline` is not null;
- no `SENT` `Submission` exists on the Matter;
- no open `NextAction` exists on the Matter;
- the Matter's **current** register state does not record the opinion work as
  finished — that is, its `opinion_sent_state` is neither `DATE` nor
  `NOT_SENT`.

The fourth clause is ADR 0050 and is unchanged. The fifth is this record.

### 2. `RECORDED_OTHER` is not completion

`VÄLJA` has a fourth reading: something is written that the parser could not
read as a date and that is not one of the `ei saatnud` wordings. It **does not**
discharge the deadline.

This is deliberate and it is the one place the analysis behind this record was
overruled. Testing `opinion_sent_recorded` — *is anything written* — would have
been one indexed boolean that already exists, and it would discharge an
obligation on the strength of a sentence nobody has read. The cell may say the
opinion went out, or that it did not, or something about the file entirely.
Leaving the deadline outstanding is the conservative direction, and it is the
same direction `is_terminal_status` already chose for an unknown `HETKESEIS`:
dropping live work is the harm, an extra row on a dashboard is not.

The state is therefore surfaced on the Matter as a data-quality observation
asking for the source to be corrected, and never as completion. Production holds
zero such rows today, so this clause changes no count — it decides what happens
the first time somebody types prose into column F.

### 3. Only a `CURRENT` register row may speak

`CurrentRegisterState` holds 2258 `RETIRED` and 2 `SUPERSEDED` rows beside its
204 `CURRENT` ones. A retired row's `VÄLJA` describes a finished file, not live
work, and must not discharge a deadline on a Matter the application still
considers open. A Matter with **no** register row — one created natively in the
application — is likewise never discharged here: it has no `VÄLJA` to speak for
it, and its deadline stands until a Submission or an open `NextAction` says
otherwise.

### 4. Nothing is written, and no evidence is claimed

A `VÄLJA` value of any state:

- never creates or modifies a `Submission`;
- never creates or modifies a `Document` or any evidence record;
- never enters canonical opinion statistics, which continue to count
  `Submission` rows and only those;
- never becomes `Matter.opinion_sent_date`, which does not exist and is not
  added;
- never clears, moves or rewrites `response_deadline`, which stays canonical
  Matter data and keeps stating itself in the header (ADR 0050).

ADR 0011 is untouched. A SENT Submission still requires `sent_at` and exact
final evidence, and remains the only record that can answer what Koda sent. This
record is about **whether a lawyer should still be told to act**, which is a
different question with a different standard of proof.

### 5. The reason must be visible, in the register's own words

A passed deadline that is no longer called late has to explain itself, or the
product has traded a false alarm for a silence nobody can account for. The
Matter's facts panel therefore states the register observation:

```
DATE            registris märgitud väljasaadetuks 14.08.2026
NOT_SENT        registrisse märgitud: arvamust ei saadetud
RECORDED_OTHER  a data-quality note asking for the source to be checked
```

**Not «Koja arvamus saadetud».** The register containing a send date is a source
fact; that Koda can produce the document is a claim about evidence, and only a
`Submission` may make it. Where one exists the page already says so in *Koja
arvamus* with the file attached, so the register's weaker sentence about the
same event is suppressed rather than printed beside it.

### 6. One definition, several renderers

The rule lives in `outstanding_response_deadlines` and every dated surface
inherits it, as they already inherit ADR 0050. Two surfaces needed bringing in:

- **Ülevaade's area rail.** `AreaMatterLine` computed `response_deadline <
  today` plainly — the one live bypass of the shared model — and showed 192
  overdue against a canonical 87. The caret's rows are now flagged from the same
  work-model list the row's count is taken from, so the number and the list
  behind it cannot disagree.
- **The Matter header** (UX-010). It now prints `· täna` on the day, `· N p` for
  a deadline within sixty days, `· N p üle` for one that is genuinely
  outstanding, and the date alone for one the register has discharged. The
  question is asked of the canonical selector rather than recomputed in the
  template.

An `Oluline tähtaeg` keeps its own semantics throughout. Nothing discharges a
milestone, its last day passing is genuine lateness, and this record does not
touch it.

## Alternatives considered

**Test `opinion_sent_recorded`.** One boolean, already indexed by
`legacy_register_drafting`, and exactly equivalent on today's data because
production holds no `RECORDED_OTHER` row. Rejected: it encodes *something is
written* when the rule is *the work is finished*, and the first unreadable cell
somebody types would silently discharge a live obligation. The set is the honest
predicate even where the boolean is the faster one.

**Require a parseable date.** It would strand the 16 `ei saatnud` files as
permanently overdue with no action that could ever clear them, and it repeats
the exact defect ADR 0021 fixed when it stopped reading a null parse as *not
sent*.

**Create Submissions from `VÄLJA`.** It would need an evidence-free `Submission`
variant, an invented recipient, an explicit carve-out for the 16 `ei saatnud`
rows, and would inflate opinion statistics by up to 146 records nobody can open.
It reverses ADR 0011's central invariant to fix a work-queue problem that does
not require it.

**Ask a lawyer to confirm each one.** 77 reviews today and a queue that refills
on every refresh, producing a human opinion *about a spreadsheet cell* — weaker
evidence than the cell, and a second thing to keep in step.

**Clear `response_deadline` when the register records completion.** It destroys
a canonical fact to express a reading, and the header would lose the date a
lawyer still needs to see. The read model exists so this is unnecessary.

## Consequences

- Outstanding response deadlines fall from **95** to **18** (10 overdue, 8
  upcoming) and *Üle tähtaja* stops being ~89 % noise. The remaining rows are
  the blank-`VÄLJA` drafting queue plus the Matters with no register row, which
  is what a believable work list looks like.
- Ülevaade's area rail and its count column state the same thing for the first
  time.
- **No schema migration, no data migration, no backfill, no search rebuild, no
  register refresh, no production write.** The rule reads derived state that the
  repeatable refresh already rebuilds from each approved snapshot.
- It reverses itself with the source. If a later approved workbook blanks a
  `VÄLJA` cell, the deadline becomes outstanding again on the next read — no
  write, no backfill, no operator step. That property is what makes discharging
  on a spreadsheet cell safe.
- `Arvamusi koostamisel`, `?arvamus=`, the `?tegevus=` populations,
  `Järgmise tegevuseta` and every opinion statistic are deliberately unchanged.

## Reversibility

Total, and in one place. The rule is one `Exists` clause in one queryset;
removing it restores the previous behaviour exactly, because nothing was written
to express it.
