# 0077 — A personal note's autosave cannot overwrite a version it never saw

*Accepted 2026-09-12.*

## The two writes with no history behind them

`MatterPersonalNote` — `Märkmed` on the Teema rail — and `PersonalScratchpad` —
`Märkmed` on Minu asjad — are the only writes in this product that file no
`ChangeEvent`. ADR 0074 §18 and the Teema redesign §22.4 argued that deliberately
and the argument still holds: an autosave of somebody's scratch paper is not a
business change, nothing downstream reads it, no statistic counts it, and
recording every keystroke pause as authoritative history would bury the history it
sits beside.

That decision has a consequence nobody wrote down, and adversarial QA on
12 September found it (QA-09):

> Tab A saves a note. Tab B, open since before that save and still holding the
> older text, autosaves 900 ms after its next keystroke. B's copy replaces A's.
> No warning. No copy kept. And because this write files no history, **nothing
> anywhere** holds the version that was overwritten.

Every other overwrite in this application is recoverable, because every other
write leaves a row saying what changed. This one is not. The absence of history
is what turns an ordinary last-write-wins into persisted data loss, and two tabs
on one Teema is not an exotic state — it is how a lawyer reads a file while
writing about it.

## Decision

**A rendered editable personal note carries the version it was filled from, and
a save whose version is not the stored one writes nothing.**

### The revision token is `updated_at`

No new column, and no new migration. `BaseModel.updated_at` is
`DateTimeField(auto_now=True)`, so it moves on every write without anything
remembering to move it; PostgreSQL stores it to the microsecond, so two saves
cannot share a token; and it is already what the rail prints as
`Salvestatud HH:mm`, so the thing the reader sees and the thing the server
compares are the same fact. The token is its ISO 8601 rendering.

A note that has never been saved has **no** version, and the empty string is
that, not a missing value. It is compared like any other token, so a second tab
whose box was empty when it loaded cannot silently replace a note the first tab
has since created.

### A stale save is refused, and nothing is decided for the person

`save_personal_note` and `save_scratchpad` take `expected_revision`, read the row
back under `select_for_update(no_key=True)` — `no_key` for the reason every lock
in this codebase takes it — and compare. Equal: save, and answer with the new
token. Not equal: raise, and write nothing.

The refusal answers **409**, and `app.js`'s `htmx:beforeSwap` lets it swap, for
the same reason it already lets a 400 swap: a response that is dropped leaves the
box looking exactly as though the save had worked.

What comes back is **not the textarea**. The person's own words stay where they
are, caret and selection included, which is the same rule the successful save has
always followed. What arrives is the hint slot carrying one sentence — «Märget on
muudetud teises aknas. Sinu muudatust ei salvestatud.» — and, out of band, the
newer version underneath, read-only, in a `<pre>` they can copy from.

Three things this deliberately does not do:

* **It does not merge.** Nobody can tell from two versions of a private note
  which sentences were meant to survive, and a three-way merge of somebody's
  scratch paper would produce a paragraph neither person wrote.
* **It does not choose.** Their text and the other tab's text are both on the
  screen; which one wins is theirs.
* **It does not adopt the newer token.** Handing it back would mean the next
  keystroke overwrites what the other tab saved — the defect with one more step
  in it. The way forward is an explicit reload, and the token only moves when
  they have actually seen what they are saving over.

### `expected_revision=None` means "no opinion"

Not an unchecked door. It is for the caller creating a Matter and its first note
in one act (`Uus teema`), where there is no earlier version for a second tab to
be holding, and where the note's row does not exist until the Matter does.

## Scope

**These two pads, and nothing else.** Canonical business editing is not given
optimistic locking here. That is a much larger contract — every inline editor on
the Teema page, every add panel, the register's own columns — with a much larger
surface and a real design question about what a conflict even means for a record
that files history and can be corrected. Nothing about it is settled by this
decision.

The reason these two come first is not that they are the most important writes.
It is that they are the only ones where an overwrite leaves no trace, so they are
the only ones where "you can see what happened and put it back" is not already
true.

## Consequences

* Two new fragments, `note_conflict.html` and `scratchpad_conflict.html`, and a
  hidden `revision` field in each form. No schema change and no migration.
* A successful save now also replaces the hidden token and clears any standing
  conflict block, both out of band. Without the first the *next* autosave would
  arrive holding the version it just replaced and conflict with itself.
* The conflict block must exist, empty, in the rendered page: an out-of-band swap
  at an id that is not on the page is silently dropped, which would make a
  refused autosave look like a successful one.
* `save_personal_note` no longer uses `update_or_create`. The read and the write
  are separated by a lock, which is what makes the comparison mean anything.
