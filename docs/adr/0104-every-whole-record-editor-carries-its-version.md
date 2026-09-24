# 0104 — Every whole-record editor carries the version it was filled from

**Status:** accepted
**Date:** 2026-09-21

**Generalises a pattern that already existed on five records to the two that
did not have it, and to every editor added since.**

A `Märge`, a `Kaasamine`, a `Väline seisukoht`, an entry and a private note have
carried a revision token for some time: the form holds the version it was filled
from, the service compares it under the row lock, and a stale save writes
nothing. `Muuda teemat` and the `Menetluse kulg` editor did not, and the 2026-09-21
QA round found exactly what that costs (QA-002, QA-004):

> Tab A changes `Vastutaja` to Martin and saves. Tab B, holding a form drawn
> before that, changes only `Lühikokkuvõte` and saves. `Vastutaja` is Sandra
> again. No warning in either tab.

It does not need two people. One lawyer with the file open twice is enough, and
it is the normal way this tool is used. The audit log recorded the revert as an
ordinary business event — `Teema määratud → Martin`, `Teema määratud → Sandra`,
one second apart — with nothing marking the second as an accident.

---

## 1. The contract

> **Extended by ADR 0109 §3 (2026-09-24).** The inline Valdkonnad, Saatja and
> Lühikokkuvõte editors and the three structured-fact editors post a whole value
> and now carry a revision too — for the inline three, a digest of their own
> value rather than the Matter's `updated_at`, and a missing token is a conflict
> (ENG-028).

**Every editor that posts a whole record carries `revision`.** A hidden field on
the form, read back by the service, compared against the value of the *locked*
row:

```python
locked = Model.objects.select_for_update(no_key=True).get(pk=record.pk)
if expected_revision and revision_of(locked) != expected_revision:
    raise Conflict(locked)
```

Three properties, and all three are load-bearing:

* **Compared under the lock**, so what the token is measured against is the
  committed version rather than whatever the caller's instance remembers;
* **compared before any value is decided**, so a refusal cannot have
  half-applied the record;
* **the conflict carries the committed row**, so the answer can show what beat
  this save beside what the person typed. Neither is chosen for them.

**The token is `updated_at.isoformat()`**, which is what every seam here already
used. `auto_now` sets it on every write, PostgreSQL stores it to the microsecond
so two saves cannot share one, and it costs no migration. An edit counter would
not do: a correction reverted and re-applied returns it to a value a stale form
is still holding, and that form would then be accepted as current when it is two
writes behind.

**A record with no single row gets a token over its rows.**
`timeline_steps_revision_token` is a SHA-256 over the sorted
`MatterTimelineStep` set, because the thing being edited is the whole rail and
there is no row whose `updated_at` covers it.

## 2. What a refusal does

**Nothing is written, and nothing typed is lost.** The form comes back bound,
holding this person's values, with the sentence above it and — where the editor
has room — the committed version rendered beside it to compare.

**409, and the token is not advanced.** Adopting the newer token in the answer
would be the view deciding that the next submit may overwrite what the other
writer saved; the person re-reads, decides, and submits again with a token they
have actually seen.

**The refusal goes back where the person is looking.** A saved whole-record
editor re-renders the page around it; a refused one re-renders the *panel*, and
swapping a panel into the page's target replaces the whole document with a bare
form. `HX-Retarget` says so per response rather than per form, which is the only
place the distinction exists.

## 3. What does not get a token

**Evidence capture.** `+ Lisa fail` on a `Märge` or a `Väline seisukoht`
carries none, deliberately. Two people attaching two different papers to one
record is not a lost update: both links are wanted, the link write is idempotent
on the pair, and there is no earlier value for a later writer to overwrite.
A token here would refuse the second lawyer's file to protect a sentence nobody
touched.

**Removal.** `Kustuta` carries one — it is a whole-record act — but removing an
already-removed record is not an error: a double submit, a second tab or a
retried request finds the state the person asked for and returns it (docs/adr/0102 §5).

## 4. Why not a database column

A `version` integer bumped in a trigger, or `SELECT … FOR UPDATE` alone, would
both work. Neither is better here:

* a column is a migration on eight tables to store what `updated_at` already
  stores to the microsecond;
* a lock alone serialises the two writes and still lets the second overwrite the
  first, which is the defect. What is needed is not ordering — it is the second
  writer *knowing* that the first happened.

## Migrations

**None.** Every token is computed from data that already exists.
