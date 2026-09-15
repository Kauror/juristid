# ADR 0080 — Õigusakt is read from one document's head, not from the envelope

- Status: accepted
- Date: 2026-09-14
- Stage: pre-QA (shared-gate development phase)
- Extends: ADR 0060 (the deterministic reader, its confidence contract and its
  conflict rule — all unchanged), ADR 0064 (assisted intake happens while the
  Teema is being created; the manual-override mechanism is unchanged), ADR 0070
  (`Õigusakt` is a canonical Matter field; the vocabulary, the control and the
  `Muu` rule are unchanged)
- Related: ADR 0072 (`.msg` attachments stay opaque; nothing is stored),
  ADR 0026 (closed word lists, refusal over guessing), ADR 0029 (exact
  matching, never resemblance)

## Context

ADR 0070 made `Õigusakt` a canonical Matter field with a reviewed vocabulary of
seventeen types and put its control on `Uus teema` and on `Muuda teemat`. It
records, deliberately, that no historical data is populated by that change and
that the register's own column is not written to the canonical field by
anything.

What it left is a person retyping, on `Uus teema`, a classification that is
written on the front page of the file they have just dropped onto the form. The
deterministic reader (ADR 0060, ADR 0064) already reads that page for the
title, the sender, the deadline, the Menetlusliik and the Valdkonnad. The
question this record answers is not *whether* it should also read the
instrument — it obviously should — but **what shape the evidence has**, because
the answer is not the shape the two existing classification fields use.

### Menetlusliik and Valdkond pool. An instrument cannot.

`_pool_signals` scores a rule over every document in the envelope, counting
each signal once at its strongest. That is right for the two fields it serves:
a covering letter, a draft, a memorandum and three annexes are all about the
same subject and all part of the same procedure, so a term found in any of them
is evidence about the Teema.

An instrument is not a property of an envelope. It is what **one document is**.
A comparison table that names a directive in every row is telling the truth
about its own contents and saying nothing whatever about what was submitted;
the memorandum beside it names the draft it explains; the covering letter names
the two things stapled behind it. Pooled scoring would hand the answer to
whichever document is longest, which on an ordinary legislative bundle is the
annex — the same failure `ANNEX_ONLY_HIGH_MARGIN` was written for, except that
here capping the confidence would not be enough, because the wrong answer would
still be the one on the panel.

## Decision

### 1. Evidence is one document's head, and the envelope picks a speaker

`textscan.head_lines` reads a document's **title block**: a message's
`Subject:`, and the run of lines at the top of a file before the document
starts talking. The block ends at the first salutation, sentence, verb of
sending or asking, list entry or numbered section; lines that cannot be a
heading but do not end the block — an address, a reference line that is mostly
digits, a label ending in a colon — are stepped over, because a ministry puts
two of them between its letterhead and its heading.

Blank lines are not load-bearing, and that is a finding rather than a
preference: `parse_source` returns a PDF's text as one line per visual line
with the empty ones gone, so a rule asking for blank space above and below a
heading is a rule that never fires on a real document. The block is bounded by
`HEAD_MAX_LINES` and `TITLE_OPENING_WINDOW` instead.

Each readable document is then classified on its own head, and the envelope
chooses **one speaker**: the best-scoring document of the best available kind,
where a document's own head outranks a message's subject, which outranks an
annex. Only the speaker's own kind may reach HIGH. Every other document may
corroborate at MEDIUM and may never outvote it.

Two consequences are worth stating because they are the point:

- **A covering letter is not the thing it encloses.** «Lisad: 1. eelnõu, 2.
  seletuskiri» never reaches the reader, because a list entry ends the title
  block. A covering letter that carries the draft's name *as its own heading* —
  which is the ordinary shape — still evidences it, and should.
- **A cited EU act is background.** Where a domestic instrument speaks for the
  envelope and a Directive or an EU Regulation is named by nothing but an
  annex, it is not offered at all rather than offered weakly: «Direktiiv»
  beside «Seadus» on the panel is the reader proposing the wrong answer and
  inviting one click to accept it. ADR 0070's ELi õiguse ülevõtmine worked
  example is what this implements. The same veto applies inside one head line:
  «Direktiivi (EL) 2024/825 ülevõtmise seaduse eelnõu» names a directive and
  submits an Act.

### 2. A specific kind beats the generic `Eelnõu`, and they are never both

`Eelnou` is the vocabulary's own answer for «a draft whose kind the source did
not name» (ADR 0070's table says so in as many words). So it is offered only
when nothing more specific was evidenced **anywhere in the envelope** — never
beside `Seadus`, never beside `Määrus`. Adding it to a known kind would be the
reader answering a question the document has already answered.

### 3. `Muu` is never inferred

`Muu` is a real vocabulary row and a person may tick it. Ticking it makes
`Õigusakti liik` required (`clean_legal_instrument_answer`), and a machine has
nothing honest to write there — so a `Muu` suggestion would put the form into a
state it cannot be saved from. There is no rule keyed on `Muu` and a guard
(`INSTRUMENT_NEVER_INFERRED`) says so as data rather than as a comment.

### 4. An annex may offer and may never fill

`SourceDocument.is_annex` and `ANNEX_ONLY_HIGH_MARGIN` are the existing
safeguards and they are reused rather than re-derived: a document the annex
markers recognise is held one point below HIGH, whatever it scored. So a
readable annexed `määruse kavand` offers `Määrus` with «Kasuta» — including
beside a parent law draft that fills `Seadus`, because both are evidenced and
the field is plural. An annex alone never pre-fills anything.

One thing the annex markers do differently here, and only here: on a document
`is_annex` has already identified, a leading «Lisa 2.» is stripped off its
first line before that line is read, because on the annex those words are its
title page. On every other document a line that looks like a list entry stays
refused outright. The two cases look identical and are opposites, and what
separates them is which document the line is written on.

### 5. A message may offer and may never fill

A staged `.msg` or `.eml` is read for its subject, its body and its headers,
and **its attachments are still never unpacked** (ADR 0072 §1, unchanged — no
attachment is opened, no filename is read, nothing is staged that was not
uploaded). A subject naming an instrument is therefore a person's shorthand for
something the reader has not seen, and `attachment_count` says how many files
it has not seen rather than what any of them is. That is real evidence and it
is offered with «Kasuta»; `MESSAGE_ONLY_HIGH_MARGIN` holds it one point below
HIGH, exactly as an annex is held.

### 6. The existing confidence and override contract, unchanged

There is no second confidence model. HIGH may pre-fill an empty, untouched
control; MEDIUM is offered with «Kasuta»; two documents of the same standing
naming different kinds is a **conflict** and fills nothing, which is the answer
two disagreeing formal headings already get (ADR 0060). A value the record
already holds is never overwritten, and on `Uus teema` the browser still
declines to write into any control somebody has typed in or pressed «Kasuta»
on (ADR 0064, amended 2026-09-12).

A bundle really can be an Act *and* a Regulation — the field is plural and
ADR 0070 §2 says so. What the conflict rule refuses is not plurality but
*picking*: choosing the higher-scoring of two equally-placed answers and
filling that one is not «both», and both are two clicks away on the panel.

### 7. No schema, no vocabulary change, no data

No migration. `Matter.legal_instruments`, `Matter.legal_instrument_other`,
`LegalInstrumentType` and its seventeen rows are exactly as ADR 0070 left them,
and `canonical_legal_instrument_keys` — the register's reading of the
historical column — is untouched and unrelated: this reads documents, that
reads a spreadsheet cell. Nothing is backfilled, no historical Matter gains a
classification, and the recurring register refresh still does not carry this
field.

Nothing is stored. A staged parse remains ephemeral (ADR 0072): the suggestion
is computed from text that is thrown away with the session, and the value
reaches the record only because a person left it on the form and pressed
`Loo teema`.

## Consequences

- One more row on the suggestion panel, between Menetlusliik and Valdkonnad,
  which is where the control already sits on both forms. No new template, no
  new CSS, no new route, no new confidence word.
- `RULES_VERSION` moves 1.0 → 1.1: the same text now produces one more kind of
  candidate, and an evaluation run should be able to say which rules it
  measured.
- `analyse()` takes the offered instrument vocabulary as a keyword argument and
  defaults it to *not reading Õigusakt at all*, so a caller with no vocabulary
  to resolve against gets the behaviour it had. An **empty** mapping is a
  different state and is reported as a diagnostic: that is a database whose
  vocabulary has not been seeded.
- The browser island's `FILLABLE` list gains `legal_instruments`, which is what
  makes the manual-override protection apply to the new control. A field
  missing from that list would be unreachable rather than unprotected, so it is
  asserted by test.
- `evaluate_intake_suggestions` is unchanged and does not score this field. It
  measures against what people filed, and nothing has filed `legal_instruments`
  yet — ADR 0070 §10 is why. A scorecard whose denominator is zero is worse
  than no scorecard.

## Alternatives considered

- **Pool the signals, like Valdkond and Menetlusliik.** One function fewer, and
  the wrong answer on every legislative bundle: the comparison table outscores
  the draft, because naming every act it touches is what a comparison table is
  for. Rejected — this is the whole of §1.
- **Read the filename.** `eelnou.pdf`, `maarus.docx` and `VV_maaruse_kavand.pdf`
  are real and would be cheap. They are also what somebody's mail client called
  a file, they are absent as often as they are present, and ADR 0060 already
  refuses to read meaning out of a name beyond the words literally in it.
  `is_annex` reads a filename and is the deliberate exception, because what it
  decides is a *ceiling* rather than an answer.
- **Infer `Muu` where a document is clearly an instrument of some unlisted
  kind.** It would need `Õigusakti liik` filled in, and the only thing a rule
  could write there is a quotation from the document. Rejected in §3.
- **Suggest the EU act beside the domestic one in a transposition.** Defensible
  — the directive is genuinely part of the story — and it is the failure mode
  ADR 0070 wrote a worked example about. Menetlusliik already carries «ELi
  õiguse ülevõtmine»; the instrument is the Act.
- **A numeric confidence for this field only.** Rejected for the reason
  ADR 0060 rejected it everywhere: a second confidence model is a second
  contract, and the two would drift.
- **Unpacking a staged `.msg` to classify its attachments.** It would be the
  most informative evidence available and it is exactly what ADR 0072 §1 says
  the staging path does not do. Not attempted; the subject is read instead, at
  MEDIUM.

## Reversibility

Complete and cheap, like ADR 0060's. Removing `INSTRUMENT_RULES`,
`head_lines`, `_instruments`, the one `prefill_initial` block, the
`SuggestedField` member and the one entry in the browser's `FILLABLE` list
leaves every other suggestion byte-for-byte what it was and every record
exactly as it is — because none of them ever wrote one.
