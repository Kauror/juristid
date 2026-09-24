# 0111 — A write that points one Matter at another locks both

**Status:** accepted
**Date:** 2026-09-24

**Amends ADR 0096 §4.6.** §4.6 says a writer that already held the lock
finishes first and a writer that arrives afterwards is refused. That holds for
writers of the Matter being deleted. It did not hold for writers of *another*
Matter that create a pointer at it, because they held a different lock
(ENG-073, reproduced on `7a626158` with a deletion paused after its plan).

---

## 1. The write skew

`delete_matter` decides two things under the lock of the Matter being deleted:
whether anything points at it that must refuse the deletion (a `Järglane`, a
background citation of its opinion — ADR 0096 §4.4, ENG-042), and which pair
rows go with it (`MatterRelation`, `RelatedSuggestionDismissal`). The writers
that create those rows locked only the Matter the row was written on:

* `close_matter(..., successor=A)` locked B;
* `link_related_matters(matter=B, other=A)` locked B;
* `dismiss_related_suggestion(matter=B, candidate_matter=A)` locked nothing;
* `add_background_submission(matter=B, submission=<A's opinion>)` locked B.

A deletion of A that had built its plan, and one of these writers holding B,
each passed their own check and both committed. The results were a closed
Matter whose continuation linked to a 404, and a relation row left under a
tombstone.

## 2. Decision

**Every write that creates a live pointer from one Matter to another locks both
Matters, at `FOR NO KEY UPDATE`, in ascending primary-key order**, and re-reads
them before deciding. `lock_matters_in_order` (`app/matters/locks.py`) does
this. It locks one row at a time in the order it states, because the order in
which a single `IN (...)` statement locks rows belongs to its plan, not to us.
It returns tombstones rather than hiding them, so each caller decides what a
deleted counterpart means:

* closure: a deleted successor refuses the closure, with its own sentence;
* relation, dismissal: a deleted other Matter refuses the write;
* background citation: a deleted source Matter, or a vanished opinion, refuses.

`delete_matter` is unchanged. It already locks the Matter being deleted before
it plans, and now every competing writer needs that same row. Whichever takes
it first wins: a deletion that commits first makes the writer refuse, and a
writer that commits first is seen by the deletion's plan. That means a
successor or a citation refuses the deletion, and a relation or a dismissal is
removed with the Matter as a pair row.

**The order is global.** Two writers needing the same two Matters take them in
the same order, so linking A→B and B→A at once cannot deadlock. The deletion
takes only its own Matter, and nothing it writes waits on the other Matter's
row lock: it inserts no row referencing the other Matter that `FOR NO KEY UPDATE`
would block.

A relation or a dismissal on a closed Matter keeps the rules it had. A relation
still requires its own Matter to be open (the rule
`lock_open_matter_for_business_write` applied). A dismissal never required that,
and still does not.

## Migrations

**None.**
