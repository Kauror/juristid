# 0101 — Received feedback may name nobody

**Status:** accepted
**Date:** 2026-09-21

**Relaxes ADR 0091 §3.3 for `RECEIVED` rows only.** 0091 made `organisation`
nullable and gave received feedback a `source_label` to name a collection of
answers instead — «Tööstusettevõtete küsitlus» for a survey of 234 companies
with no single author. The database then refused a row carrying neither, through
`matters_external_position_author_or_label`.

That check was right about the problem and one answer too strict about the
solution. The owner met it in ordinary use (OWNER-01):

> a lawyer writes down what a member said on the telephone, and the panel will
> not save until they invent a name for who it was.

`Allikas` was built for a *collection*. A single anonymous caller is not a
collection, so there is nothing truthful to type, and the form's refusal is
answered the only way a refusal can be answered: by making something up. The
rule intended to keep invented authorship off professional files was producing
it.

---

## The decision

**For `RECEIVED`, a position may name nobody.** No organisation, no
`source_label`, and the absence is the record rather than a gap in it.

**For `DISCOVERED`, nothing changes.** A published opinion with no author is an
anonymous claim on a professional file, and
`EXTERNAL_POSITION_NEEDS_ORGANISATION` still refuses it. That half of 0091 §3.3
was never the problem — a ministry's opinion always has a ministry.

**The record still cannot be empty.** `EXTERNAL_POSITION_NEEDS_SOURCE` is
untouched: a position, a link or a file is always present. What became optional
is *whose* it was, which is a different question from *whether there is
anything here*.

**Naming is not weakened where it happens.** A named row still carries the
catalogue's own organisation, `PROTECT` still refuses to lose an institution a
Matter cites, `matters_external_position_label_is_received` still keeps
`Allikas` to `RECEIVED`, and both correction doors ask the same question again
against what the save would result in.

## The constraint, as relaxed

```sql
CHECK (organisation_id IS NOT NULL OR provenance = 'received')
```

`matters/0035_received_feedback_may_name_nobody.py`. Additive in effect: every
row the old check accepted the new one accepts. Nothing is backfilled, because
there is nothing to backfill — an existing row already names somebody.

**The old constant is retired, not deleted.**
`EXTERNAL_POSITION_NEEDS_AUTHOR_OR_LABEL` stays in `app/matters/services.py`
with a note saying what happened to it, because release notes and tests cite it
and a reader who meets the name should find out that the rule went rather than
only that the name is gone. Nothing raises it.

## What the panel shows now

The author box is still there and still asked first — most feedback does have a
sender, and the ordinary record is the named one. It reads `valikuline`, and
`Liige` is beside it rather than inside it, because *a member said this* and
*this named member said this* are two facts and only the second needs a name
(QA-014).

An unattributed row reads `Meile saadetud tagasiside` in `Teema käik`, with no
trailing punctuation where the author would have been, and `· Liige` when the
mark is set.

## Alternatives rejected

**Keep the refusal and improve its wording.** The sentence was not unclear. It
asked for something the lawyer did not have.

**Default `source_label` to «Nimetu» or «Liige».** A manufactured label is the
invented authorship this record exists to stop, written by the application
instead of by a person under duress — worse, because it looks like somebody
stated it.

**Infer the member from the Matter's `Kaasamine` rows.** Prose parsing and
inference are out by the product's own constraints, and a file's engagement
list is not evidence of who telephoned.

**Relax the check for both provenances.** Rejected above: it would let an
anonymous published opinion onto a file, which is the half of 0091 §3.3 that
works.
