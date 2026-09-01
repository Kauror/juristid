# ADR 0056 — A baseline holds the product, not the clock and not the seed

- Status: proposed
- Date: 2026-09-01
- Related: ADR 0049 (the department page, whose nav change made five baselines
  stale), the #113 clock-stabilisation work this layers on
- Number: 0056 rather than 0055. `data/opinion-corpus-reconciliation` (#112) is
  in flight and holds 0054 and 0055, both of which collide with the 0054 #111
  merged; it renumbers to 0055 and 0056 or later when it rebases, and leaving a
  gap here is cheaper than two branches claiming one number twice.

## Context

#113 gave the visual suite a way to hold clock-derived values still, and said
in its own docstrings that the thing it could not fix was the class of value
that was *never* masked: "a mask that stops matching fails loudly the next
morning, but a value that was never masked just makes the baseline quietly
stale. The cost lands on somebody else."

Three such debts remained, and they are three different defects that look like
one — "the visual job is close to its threshold".

**Five persona baselines were stale.** Measured fresh against the newest
successful main run (`33512492085`, `c277fc8`): `persona-pill-suletud`,
`persona-popover-aktiivne` and `persona-popover-avatud` at **0.1989%** against a
0.2000% limit — 99.5% of the budget — with `persona-leht-valitud` at 0.0928%
and `persona-leht-ilma` at 0.0566%. Every one of them differs in a **single
y-band, 17–31**, and nowhere else. Cropped and read, that band is the top bar:
`Ülevaade | Minu asjad | Teemad` in the baselines against
`Minu asjad | Osakond | Teemad` on the page. It is ADR 0049, already approved,
already shipped, and never adopted into these five images.

**`/otsing/` drifted on every run rather than on every day.** 0.0593%, in one
8px band. Cropped, it reads
`kaasamine-01a053b2-2ac3-76d5-82a2-233933f82c73` against
`kaasamine-01a05d22-111a-7151-b07d-31e9aebc3d52`. Not a clock value at all: a
primary key, reseeded per run, printed under a search result.

**The Teema captures drifted daily.** 0.0158% and 0.0153%, and 304 of those 308
pixels are one element — the folded system-run summary's date span, `31.08`
becoming `01.09`. (The other four are the right edge of an already-masked
element, a sub-pixel mask-box effect at 0.0002%.)

## Decision

### The five persona baselines are retaken, and nothing else is

Adopted from the candidates of a real run against this branch, with
`E2E_UPDATE_BASELINES` left at `"0"` throughout — the workflow #113 documented.
Attribution came first: five scenarios, one y-band each, read as an image and
identified as ADR 0049's bar before any file was replaced. `persona-ilma-popover`
was measured too and is **0.0000%**; it is not touched, which is the check that
this was a retake of five known-stale images rather than a sweep.

### A search result's locator is a place, never a primary key

`source_locator` is documented as *where the result opens from, when the source
is not the Teema itself*. Two of its five producers honour that — a document
fragment writes «lk 4», a historical page writes its OneNote location. The
other three wrote `kaasamine-<uuid>`, `sissekanne-<uuid>` and `arvamus-<uuid>`.

That is not a place, and it was not useful. The prefix repeats the badge already
beside it; the identifier appears in no register entry and no document; it is
not what the row links to, because `_target_url` builds every anchor from the
typed `entry_id` / `submission_id` / `document_id` columns; and the provenance it
carried is already structural, in `source_kind` and `source_object_id`, where
code can use it. **So it is removed from the product rather than masked.**
Masking it would have made the test green while leaving a lawyer looking at a
UUID.

Fixed at both ends, and the asymmetry is deliberate:

- **the producers** stop writing a primary key into that field, so nothing new
  carries one;
- **the read model** suppresses it for those three source kinds, so every row
  already stored stops printing one on deploy.

The second is there because `INDEX_VERSION` is the wrong instrument. That
version is a **fail-closed authorization gate**: bumping it makes every existing
row ineligible until a rebuild has run, which is right when a stored vector may
hold text a reader may not see, and far too heavy for a field that is in no
`SearchVector` and decides no access. Both were checked rather than assumed.

The suppression is **by source kind, not by string shape**. A rule that hid
anything UUID-shaped would be guessing at content, and would start hiding a real
locator the day one contained a hyphenated hex run.

### The system-run date span is masked, and the mask is required

`.uxtl__sysrow > span:nth-child(2)` — positional, because the element has no
class and adding one for a test would put a Playwright concern in product
markup. It is stable rather than incidental: `.uxtl__sysrow` is one `<summary>`
with exactly three unconditional `<span>` children in a fixed order — the count
and kinds sentence, this span, and `.uxtl__sysshow`. None is inside an `{% if %}`,
so `:nth-child(2)` can only be `row.span`.

**Masked rather than normalised, and measured rather than assumed.**
`short_day_month` zero-pads, and the differing columns are x506–574 in both
scenarios with «näita ▸» standing still: the glyphs move, the box does not.
`short_range` can also return an eleven-character `27.08–31.08`, and that is the
second reason not to normalise — a normalisation would have to rewrite a real
range to a single date and say something untrue about how long the run lasted.
A mask covers whatever box is there.

It is in `REQUIRED_MASKS` for `teema-ulevaade` and `teema-1024`, because what
decides its presence is the seed's own event ordering rather than the day, and
both captures rendered it on the run this was measured from. It is not required
elsewhere: the selector is in `CLOCK_DEPENDENT`, so any other scenario that
folds a run has it painted anyway, and requiring it there would assert a
presence nothing measured.

## Consequences

- **Eight baselines move, and two of them were not expected to.** The five
  persona images and `otsing.png` were planned. `teema-ulevaade.png` and
  `teema-1024.png` move too, and the reason is arithmetic rather than a
  surprise: those baselines hold the `31.08` **glyphs**, so painting a mask
  where the glyphs were changes the pixels once, by construction. There is no
  version of "mask this element" that leaves an image holding its unmasked text
  unchanged. The alternative — normalising to the string the baselines already
  hold, which is how #113 stabilised `HORIZON_LABEL` without moving an image —
  was rejected above on its own merits, not to avoid the retake.
- `minu-too*`, `teemad-*` and `teema-suletud.png` are untouched. They are
  canonical after #113 and #111.
- **No product markup changed for a test.** The only product change is the
  search locator, and it is a product change on its own merits.
- The three drifts that remain in these scenarios total four pixels
  (`teema-ulevaade`, mask-box edge) — 0.0002% against a 0.2% limit.
- **`E2E_UPDATE_BASELINES` stays `"0"`.** No workflow enables it, and no
  baseline in this change was produced by it.

## Alternatives rejected

**Masking the search locator.** It would make the visual job green and leave the
UUID on the page. The brief for a screenshot suite is to notice things like
this, not to paint over them.

**Replacing the locator with a different string** — an entry date, a submission
reference. It invents a new label to fill a slot whose answer is "there is no
place to name here", and the badge already says what kind of thing matched.

**Bumping `INDEX_VERSION`.** Correct for a change to indexed text or to what a
row may reveal; this is neither, and it would take search partially dark until a
rebuild ran, for a cosmetic field.

**A UUID-shaped regex in the template or the read model.** Guessing at content,
and wrong the first time a real locator looks like one.

**Adding a class to `timeline_items.html` for the mask.** The DOM already
identifies the element unambiguously, and product markup should not carry test
affordances.
