# 0106 — A next action may have no date yet

**Status:** accepted
**Date:** 2026-09-22

**Supersedes ADR 0052 §5's fourth row** — *«any / set / — → Refused on
`next_date`»* — and the database constraint written for it,
`workflow_deadline_requires_a_date` (migration `workflow/0005`). Everything else
in ADR 0052 stands: a native step is still `DO` / `DEADLINE`, there is still no
mode chip, and the classification is still not a user-facing concept.

---

## 1. The two facts

A lawyer routinely knows **what** happens next before knowing **when**:

> Järgmiseks: *Vaatan ministeeriumi vastuse üle*

The product refused that. Text and date had to arrive together or not at all, so
recording the sentence meant typing a day nobody had chosen — and an invented day
is not a harmless placeholder. It is a false statement that the work queue then
reports on: it lands in `Tähtajad`, it goes overdue, it is counted late, and the
person who invented it is the one who then has to explain the number.

The two are separate facts and they are now stored separately.

## 2. Canonical storage

An ordinary lawyer-created step with no date stores:

```
kind           = DO
date_semantics = DEADLINE
target_date    = NULL
date_precision = EXACT     (the field default; nothing renders it)
```

**`DO` and `DEADLINE`, deliberately.** The obvious alternative was to reach for
`WAIT`, `MONITOR` or `EXPECTED_AROUND` to satisfy the old invariant, and every
one of them would have been a lie about a different thing: the lawyer is not
waiting on anybody, is not monitoring anything, and has not estimated a date.
They intend to *do* this. `DEADLINE` says what the date means **when there is
one** — the day the work is due — and says nothing at all when there is not.

**`NULL` means «no deadline has been recorded yet».** It is never read as today,
as the end of the month, as approximate, as waiting, as monitoring or as overdue.
Nothing anywhere fills it in.

`date_precision` keeps its `EXACT` default beside a `NULL` date. That is an
implementation detail and no surface implies an exact day exists:
`display_date` answers `""` on a `None` and `date_label` answers `""`, so
precision has nothing to describe and nothing to print.

## 3. The database

`workflow_deadline_requires_a_date` is dropped
(`workflow/0008_a_next_action_may_have_no_date`). One `RemoveConstraint`, no
column change, no data migration, no backfill, and not one existing row read or
written.

`target_date` has been nullable since the table was created, because `WAIT` and
`MONITOR` have always been able to be dateless — so this changes which rows are
*allowed*, not what the column is.

**Rolling-safe, which is what lets it be applied before the new image is up.**
Dropping a `CHECK` cannot fail on existing data, and the old code cannot write a
row the dropped constraint would have caught: the same refusal lives in
`set_next_action` and in `NextActionForm`, and the old image is still running
both. Every reader of `target_date` in the old image already guards `None`,
because `WAIT` and `MONITOR` rows reach the same code paths. Reversing the
migration re-adds the constraint and **will fail** on a database that has since
taken an undated `DO` — which is the honest behaviour.

`workflow_next_action_text_required` and `workflow_one_open_action_per_matter`
are untouched. An action with no text is still not a record of anything.

## 4. The service and the forms

`set_next_action` now has three valid shapes and one refusal:

| `text` | `target_date` | outcome |
| --- | --- | --- |
| set | set | the ordinary dated step |
| set | `NULL` | a step whose day nobody knows yet |
| blank | set | refused, **on the text** |
| blank | `NULL` | refused, on the text |

A date with no sentence stays refused and stays pinned to the sentence:
somebody who pressed `Homme` and then wrote nothing did ask for a step, and gets
told which half is missing rather than getting a save with no step in it.

Three forms carry the rule and all three now agree: `NextActionForm`
(`PRAEGUNE TEGEVUS` → `Muuda`, and `Uus teema`), `MatterProgressForm`'s
`Järgmine tegevus` block inside `+ Märge`, and `ComposerForm._clean_next_action`.
A *malformed* period is still refused — `Kuu` chosen with no month picked — and
that error is still on the control it belongs to. What is no longer refused is
the date control being **left alone**.

**Adding and clearing are the same ordinary act.** A step recorded without a day
takes one later through `Muuda`, and a step with a day has it cleared by emptying
the box; both go through `set_next_action_for_new_work`, which supersedes the
previous row and leaves the replacement chain and the audit event exactly as they
are. There is no separate «remove the date» control and no cancel-and-recreate
workaround.

This also completes what docs/adr/0105 §4 set out to do and stopped short of.
That round made `+ Märge` save with whatever the lawyer had — a comment, a file,
a stage change, a step — and kept the step's date required, on the reasoning that
a dateless step appears in nobody's `Tähtajad` and in nobody's `Minu asjad`. The
first half is true and correct; **the second half was wrong**, and §5 is why.

## 5. Where undated work appears, and where it does not

**It appears in `Minu asjad`.** `my_work.undated_items` has rendered a
`Kuupäevata` block since the page was built — it simply had only `WAIT` and
`MONITOR` rows to put in it. An undated `DO` lands there with no new code, with
its count and its «näita kõiki» link. `PortfolioRow.has_action` already counts an
undated action, so a Matter carrying one is correctly **not** in *järgmine
tegevus puudub*.

**It appears in `PRAEGUNE TEGEVUS`**, reading `Kuupäev määramata` where a dated
step reads its day. Muted, never the overdue colour: a step with no deadline
recorded cannot be late, and a warning there would be the page inventing a
problem out of a blank. The same words appear on the portfolio row, lower-cased
to match its line.

`Kuupäev määramata` rather than `Tähtaeg määramata`, and rather than a dash or an
empty cell. It is the construction `Etapp määramata`, `Vastutaja määramata` and
`Hetkeseis määramata` already use; a dash reads as missing data, and *tähtaeg* is
a word this product reserves for the two obligations owed to somebody outside
(docs/adr/0054 §Amendment).

**It does not appear in any dated surface**, and none of them needed changing —
every one excludes a `NULL` in SQL rather than by a Python guard that could drift:

* `NextActionQuerySet.overdue()` and `overdue_date_q` — `target_date__lt`;
* `due_for_review` and `reviews_due` — `target_date__isnull=False`;
* the register's `?tegevus=hilinenud` and `?tegevus=ulevaatus`;
* `Osakond`'s upcoming window and `dashboard.upcoming_rows` — `target_date__gte`;
* the `OVERDUE_DO_DEADLINE` and `REVIEW_DUE` reporting metrics;
* `my_work_timeline`'s four dated bands, which put it in `Kuupäevata` instead.

`NextAction.is_overdue`, `is_due_for_review`, `days_late`, `display_date` and
`date_label` each return early on a `None`, so `days_late` is `0` and nothing
prints. The moment a date is added, every one of those surfaces picks the step up
with no further action.

## 6. What does not change

* **Historical and imported semantics.** `WAIT`, `MONITOR`, `REVIEW_ON`,
  `EXPECTED_AROUND` and the approximate precisions keep every meaning they have.
  The register's parser goes on recording the pairs its sources name. No data
  migration runs over old actions and none is wanted.
* **The `Uus teema` prepare-by flow.** A supplied preparation date still creates
  the initial `Koostan arvamuse` step and a blank one still creates nothing. It
  does not now manufacture an undated action; the lawyer may create one by hand
  afterwards, which is a different act.

  ADR 0091 §1.2 states that decision and cites the dropped constraint as what
  enforced it. The **decision is unchanged and so is the behaviour** — what
  enforces it is `establish_opinion_preparation_action`'s own
  `prepare_by is None` refusal, which was always there beside the constraint and
  is now the whole of it. The reasoning in that ADR's paragraph is the part that
  no longer reads true; the rule it protects is asserted here.
* **Search.** Next-action text participates in the index exactly as before. A
  `NULL` date changes no indexed document, so `INDEX_VERSION` is not bumped.
* **Audit.** The `NEXT_ACTION_SET` event is written as always, and its payload
  carries `target_date: null`, which is truthful.

---

## Alternatives considered

* *Keep the constraint and let the form write today.* Rejected — it is the
  defect, not a workaround for it.
* *Store an undated step as `WAIT` or `EXPECTED_AROUND`.* Rejected. Both are
  statements about the world that the lawyer did not make, and both would move
  the step out of `TEEN` in every report that still reads the kind.
* *A separate «kuupäev teadmata» flag beside the date.* Rejected: a nullable date
  already carries exactly that, and a second column is a second thing that can
  disagree with the first.
* *Leave the date cell empty on the two display surfaces.* Rejected. Every dated
  row on those surfaces ends in a day, so one ending at the sentence reads as a
  row whose date failed to render.

---

## 7. Also in this round — `opinion_manage` prints the address

docs/adr/0105 §3 made the `Ülevaade / uudis` chronology row show its address
instead of the label `Ava ülevaade või uudis`, and deliberately left
`opinion_manage.html` alone as out of that round's scope. The result was two
presentations of one link on one file, so this finishes it: the same
`MatterWebsiteOverview.link_display` — no scheme, no userinfo, no trailing slash,
cut at 72 characters and marked when it is cut. The stored `url` is untouched and
is still the whole `href`, with the same `target`, `rel` and visually hidden
«avaneb uues aknas». No second URL formatter was written.
