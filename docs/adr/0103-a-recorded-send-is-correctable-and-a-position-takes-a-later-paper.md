# 0103 — A recorded send is correctable, and a position takes a paper that arrives later

**Status:** accepted
**Date:** 2026-09-21

Two corrections from the 2026-09-21 QA round that touch decisions already on
record, so they are written down rather than merged as polish. The round's other
findings — the register's empty state, the date plausibility hint, the duplicate
filename marks — are presentation inside existing decisions and need no record.

---

## 1. `Arvamus välja` becomes correctable, from its own chronology row

**Amends ADR 0061 and ADR 0093 §3.**

`Teema käik` gives every user-created record a `Muuda`: `Meile saadetud
tagasiside`, `Teiste arvamus`, `Kaasamine`, `Märge` and `Ülevaade / uudis` all
carry one. `Arvamus välja` carried none — the one record where a wrong date or a
wrong recipient matters most, because the send date reaches the outbound
register, the process rail and every report that counts advocacy. A letter
registered with the day mistyped could be repaired only by an administrator
(QA-023).

### Why this is not the editor ADR 0061 retired

0061 retired a *second surface for opinions* — a per-Matter `Arvamused` page
that restated the letter, its evidence mechanics and its reconciliation facts
beside `Dokumendid`. 0093 §3 kept that line when it added
`Arvamuse märksõnad ja seosed`: «a metadata surface that could also re-address a
sent letter would be a second opinion editor».

What is added here is neither. It is one control on the chronology row that
already exists, and it corrects exactly the four facts the chronology itself
prints:

```
Saadetud     the business date and its precision
Liik         Ametlik arvamus, Täiendav arvamus, Pöördumine …
Kokkuvõte    what the letter argued
Adressaadid  who Koda formally wrote to
```

No file, no status, no evidence, no title, no tags, no overview links, no
workflow. The send workflow's own routes are untouched and still land on
`Dokumendid`.

### What a correction may not reach

* **The evidence.** `final_version` is not an argument, no `DocumentVersion` is
  superseded and no bytes are rewritten. A letter whose *text* was wrong is a
  different letter, which is what supersession is for (docs/adr/0084 §8).
* **The status.** Un-sending, withdrawing and superseding are acts with their
  own services, their own events and their own meaning. A spelling fix may not
  become one of them.
* **`sent_by`.** Who sent the letter is not who corrected the record, and the
  audit trail keeps both.
* **`Teadmiseks` recipients.** `set_recipients` replaces the whole set, so a
  form asking only about addressees would silently drop every copied-in
  committee. The service reads the current ones and passes them through — the
  distinction `RecipientRole` exists to keep.

### The act has its own name

`ChangeEventType.SUBMISSION_CORRECTED` — «Arvamuse andmeid parandatud». Not a
second `SUBMISSION_SENT`, which would make the file read as two letters; not a
`SUBMISSION_WITHDRAWN`, which says something about the letter where this says
something about what we wrote down about it. It carries the before and after of
every value that moved, and is not written at all when nothing did.

### Refusals

Only a recorded send: a `DRAFT` has no stated facts to correct and is refused
rather than given an invented send date. Optimistic concurrency on `updated_at`,
compared under the row lock, so a stale second tab writes nothing.

**No open-Matter lock**, which is the existing contract rather than an exception
invented here: `withdraw_submission` corrects a recorded send on a closed file,
and `SubmissionMetadataForm` edits an opinion's metadata on one. Correcting what
a closed file wrote down about a letter it sent is the same kind of act as
correcting an entry's text on one (docs/adr/0075 §12, docs/adr/0093 §3).

### There is no `Kustuta` on this row

It is the one user-created chronology row without one. A sent opinion is a
letter that left this office; taking it off the file would be the record
claiming it never went, and the domain already has the true sentence for «we
took it back» (docs/adr/0102 §4).

---

## 2. A `Väline seisukoht` takes evidence after the fact

**Extends ADR 0084 §8 to the record it had not reached.**

A `Menetluse areng` has had `+ Lisa fail` since docs/adr/0091 §5.4: the ministry
sends the revised draft a fortnight after the step was written up, and the file
learns about another paper supporting a record it already holds correctly.

A `Väline seisukoht` had no such act. Files could arrive only with the capture,
and `Muuda` deliberately does not take bytes — so an association that sent its
position paper a week after somebody wrote down what it said on the telephone
had nowhere on the file to put it (QA-021).

The same act, with the same reasoning and the same refusals:

* **additive only** — the organisation, the date, the `Seisukoht`, the address,
  `Juristi märkus` and `Liige` are untouched;
* **never a replacement and never a removal** — the files the position already
  carries are not read, so nothing can detach one, and no `DocumentVersion` is
  superseded;
* **no revision token** — two people attaching two different papers to one
  position is not a lost update, both links are wanted, and there is no earlier
  value for a later writer to overwrite;
* **refused on a closed Matter**, under the Matter's row lock;
* **all or none** — a second file being refused unwinds the first.

`EXTERNAL_POSITION_DOCUMENT_LINKED` already existed for the capture path and is
what this raises.

### This is the answer to «create and correct are asymmetric»

QA-021 reported the two forms asking different questions. The part that was a
real gap is this one: a fact set at capture could be corrected, and a file could
not be *added* at all. The rest of the asymmetry is docs/adr/0095 §3 and §5
working as decided — capture asks few questions, `Muuda` asks the ones a
correction needs — and the correction form is not widened to match a panel
deliberately kept short.

## Migrations

One, `audit/0029_submission_corrected_event.py`: a new value in
`ChangeEvent.event_type`'s choices, which is a state change with no SQL behind
it. Nothing is backfilled — before this, a recorded send had no correction
surface, so nothing historical says `SUBMISSION_CORRECTED`.
