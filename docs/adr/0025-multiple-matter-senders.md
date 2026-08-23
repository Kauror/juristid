# 0025 — A Matter has zero, one or several senders; the addressee stays singular

- **Status:** Accepted — implemented on the `feat/multiple-senders` branch
- **Date:** 2026-08-23
- **Builds on** ADR 0012 (legacy register import and the era contracts), ADR 0013 (the search projection), ADR 0017 (statistics and the metric catalogue).

## Context

`Matter` held exactly one sender:

```python
source_organisation = models.ForeignKey(
    "organisations.Organisation", on_delete=models.PROTECT, null=True, blank=True
)
```

The department's work does not. A draft law reaches Koda from a ministry and an
industry association together often enough that the single column was being
worked around — one body recorded, the other written into the title or a note,
where no filter and no statistic can reach it.

Two things made this more than a widening. The register's counterparty column
means different things in different decades: `KELLELT` — the sender — until
2019, `KELLELE` — the addressee — from 2020. And the removed field carried
`on_delete=PROTECT`, so an institution that had sent Koda something could not be
deleted out from under the record.

## Decision

### Cardinality

Sender is **0..N**. Addressee remains **0..1**.

The asymmetry is the domain's. Material arrives from several bodies at once; an
answer Koda sends goes to one. `addressee_organisation` is untouched by this
change, and `KELLELT` and `KELLELE` remain two separate stored facts that nothing
merges — the same rule ADR 0012 rests on.

### One store, no compatibility shim

The singular field is **removed**, not kept beside the plural one. There is no
`source_organisation` property returning the first sender, and no notion of a
primary sender.

A `.first()` shim would have been the cheap migration, and that is the argument
against it: every one-sender assumption in views, selectors, search, reporting
and exports would have kept compiling and kept reading as correct while
silently seeing half the data. Removing the attribute made each of those call
sites fail loudly and get a decision.

### An explicit through model, for integrity and nothing else

```python
class MatterSourceOrganisation(BaseModel):
    matter = models.ForeignKey(Matter, on_delete=models.CASCADE, ...)
    organisation = models.ForeignKey(Organisation, on_delete=models.PROTECT, ...)

    class Meta:
        constraints = [UniqueConstraint(fields=["matter", "organisation"], ...)]
```

A plain `ManyToManyField` would have been simpler and was the starting
preference. Its auto-created join table **cascades** when an Organisation is
deleted, which would have traded away the `PROTECT` guarantee the removed
foreign key gave — quietly, with nothing failing until the day somebody tidied
the organisation list and took a decade of provenance with it.

Preserving that guarantee is the only reason the through model exists, and it
holds nothing else: no primary flag, no ordering, no role, no per-relation
provenance, no confidence, no manual/register marker. Raw source provenance
already lives in the immutable `MatterSourceReference`, and a column added
against a future requirement is a column something starts depending on.

The unique constraint means a duplicate POST converges to one association
rather than being deduplicated in application code.

### Reverse relation

`Organisation.matters_as_sources` (plural). The old `matters_as_source` is gone
rather than aliased: a singular reverse accessor over a plural relation is the
same misleading API as a singular forward one.

### The migration preserves, and refuses

`matters/0008_multiple_source_organisations` adds the relation, copies every
non-null `source_organisation_id` into exactly one relation row, and drops the
old column. No `RunSQL`. No re-resolution of the historical register, no
re-reading of a workbook, no splitting of a raw counterparty string that
mentions two names — **this change alters cardinality, not matching rules.** A
row that had no sender ends with an empty set, so the relation count after the
copy is exactly the number of Matters whose old field was set.

The reverse **fails closed**. While every Matter still has at most one sender it
restores the singular column faithfully. Once any Matter has two it raises and
aborts, because every way of choosing which sender to keep — the first, the
alphabetically smallest, the most recent — destroys data while reporting
success. Rollback stays available up to the moment real multi-sender data
exists; after that it is a decision a person makes, deliberately, by reducing
the data first.

### Multi-valued statistics say so

"Teemad saatja järgi" is no longer a partition. A Matter with two senders
appears in a segment for each, so the segment totals can exceed the headline —
which stays a count of *distinct matters that have at least one sender*. The
metric carries a note saying this on the page, rather than leaving a reader to
work out why the bars add up to more than the number above them.

Every aggregate over the relation counts `Count("id", distinct=True)`, the rule
`app.core.authorization.scoped_count` already established for the visibility
join. Filters that traverse the relation add an explicit `.distinct()`, because
`apply` only adds one when the reader is actually scope-restricted — a
department head would otherwise be the one person who saw the duplicate row.

### The control matches the model

Sender selection is checkboxes on `Uus teema` and on the Matter header's inline
editor. Frequently used organisations are offered as chips, ordered by distinct
visible usage; the long tail sits behind a disclosure as a multiple select. The
two controls are **unioned** — nothing is privileged for having come from the
frequent list — and both validate against the full Organisation queryset, so
creating a Matter is never a way to create an institution.

### The header band clips, and says so

The Matter header's facts strip has a hard height budget — a browser test fails
it above two lines — because a band that grew into a wall of always-open
controls above the title is the regression that budget exists to prevent. A
sender *list* does not fit there in general.

So the band clips the sender list exactly as it clips every other long value,
and the complete list is always on the field's tooltip and always one click
away in the editor behind it; the register tables, the `Saabunud materjalid`
note and the CSV export render it unclipped. This is a deliberate departure
from "the detail page renders every sender in the band", and the alternative —
growing the band — was tried and rejected against the existing contract.

Fixing this exposed a latent bug the singular field had hidden: the trigger set
`text-overflow: ellipsis` on an `inline-flex` element, where the property does
nothing. Long values had always clipped mid-word and taken the disclosure caret
with them; one short institution name simply never reached the edge. The cut now
happens on an inner block, so truncation reads as truncation.

## Consequences

- A sender change is a real Matter edit, so the service bumps `updated_at`
  explicitly: `.set()` writes the join table and nothing else, and every
  activity surface reads `updated_at`.
- Sender order is not a business fact. Comparison is by set of primary keys, so
  re-submitting the same senders in a different order writes nothing, raises no
  audit event and does not move `updated_at`.
- `MATTER_ORGANISATION_CHANGED` is reused; its payload now carries whole sets
  (`source_from` / `source_to` as sorted id and name lists) rather than a single
  name. Search text concatenates sender names in sorted order so the projection
  hash cannot change between rebuilds because the join returned a different row
  order.
- Exports render several senders as `A; B` sorted by name — semicolons because
  organisation names contain commas.
- The relation is Matter-owned, so the TEST purge planner reaches the join rows
  and never the shared Organisation rows. Sender cardinality has no bearing on
  `data_class`.

## Alternatives considered

**Keep the FK and add a secondary "additional senders" relation.** Two stores
for one fact; every reader has to remember to consult both, and the first one
that forgets is a silent bug.

**Plain `ManyToManyField`.** Rejected only because of the `PROTECT` regression
above. If Organisation deletion policy ever stops being protective, the through
model can be dropped in favour of the auto-created one.

**A through model with `is_primary` / ordering.** No current requirement asks
which sender is the main one, and inventing one would put a field on the screen
that people would then have to answer.

## Reversibility

The schema reverses cleanly while the data is still 0..1 per Matter, and refuses
once it is not. The through model can later be replaced by an auto-created join
table if `PROTECT` stops being required; nothing outside `app/matters/models.py`
names it.
